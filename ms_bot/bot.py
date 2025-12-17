from __future__ import annotations

import argparse
import asyncio
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

from .board import BoardSnapshot, Cell, parse_square_class, render_board_for_llm
from .solver import best_guess, choose_first_click, iter_in_order, solve_step


DEFAULT_URL = "https://minesweeperonline.com/#beginner"
BASE_URL = "https://minesweeperonline.com/"

DIFFICULTY_PRESETS: dict[str, dict[str, object]] = {
    "beginner": {"fragment": "beginner", "width": 9, "height": 9, "mines": 10},
    "intermediate": {"fragment": "intermediate", "width": 16, "height": 16, "mines": 40},
    "expert": {"fragment": "expert", "width": 16, "height": 30, "mines": 99},
}

_COOKIE_BUTTON_RE = re.compile(
    r"^(accept|accept all|agree|i agree|ok|okay|got it|consent|allow all)$",
    re.IGNORECASE,
)


def _parse_cell_id(cell_id: str) -> Cell | None:
    if not cell_id:
        return None
    m = re.match(r"^(\d+)_(\d+)$", cell_id)
    if not m:
        return None
    return Cell(int(m.group(1)), int(m.group(2)))


async def read_board(page: "Page", width: int, height: int) -> BoardSnapshot:
    data: list[dict[str, Any]] = await page.evaluate(
        """
        () => Array.from(document.querySelectorAll('div.square[id]'))
          .map(e => {
            const r = e.getBoundingClientRect();
            const s = window.getComputedStyle(e);
            const visible =
              r.width > 0 && r.height > 0 &&
              r.bottom > 0 && r.right > 0 &&
              r.top < window.innerHeight && r.left < window.innerWidth &&
              s && s.display !== 'none' && s.visibility !== 'hidden' && s.opacity !== '0';
            return { id: e.id, className: e.className || '', visible };
          })
          .filter(x => x.visible)
        """
    )
    cells: dict[Cell, object] = {}
    coords: list[Cell] = []
    for item in data:
        c = _parse_cell_id(item.get("id", ""))
        if not c:
            continue
        coords.append(c)
        cells[c] = parse_square_class(item.get("className", ""))

    if coords:
        origin_x = min(c.x for c in coords)
        origin_y = min(c.y for c in coords)
        inferred_w = max(c.x for c in coords) - origin_x + 1
        inferred_h = max(c.y for c in coords) - origin_y + 1
    else:
        origin_x = 0
        origin_y = 0
        inferred_w = 0
        inferred_h = 0

    # Prefer the size inferred from *visible* squares. Passing --width/--height is
    # still useful for mine counts / difficulty presets, but DOM may contain
    # hidden "square" nodes (ads/templates) with out-of-range ids.
    board_w = inferred_w if inferred_w > 0 else width
    board_h = inferred_h if inferred_h > 0 else height
    if board_w <= 0 or board_h <= 0:
        board_w, board_h = (width or 9), (height or 9)

    # Fill missing entries as covered. DOM can omit elements briefly during transitions.
    for y in range(origin_y, origin_y + board_h):
        for x in range(origin_x, origin_x + board_w):
            cells.setdefault(Cell(x, y), "covered")

    return BoardSnapshot(origin_x=origin_x, origin_y=origin_y, width=board_w, height=board_h, cells=cells)


async def game_state(page: "Page") -> str:
    # minesweeperonline uses #face with classes like facesmile/facewin/facedead.
    face = await page.locator("#face").get_attribute("class")
    face = face or ""
    if "facewin" in face:
        return "win"
    if "facedead" in face:
        return "dead"
    return "playing"

async def _dismiss_common_popups(page: "Page") -> None:
    # Cookie/consent banners vary by region and provider (Quantcast/OneTrust/etc).
    # We try (1) clicking common accept/close buttons (also inside iframes) and
    # (2) injecting CSS to hide known containers.
    try:
        await page.add_style_tag(
            content="""
            #qc-cmp2-container, .qc-cmp2-container, .qc-cmp2-ui,
            #onetrust-consent-sdk, .onetrust-pc-dark-filter, .ot-sdk-container,
            [id*="cookie"][style*="position: fixed"], [class*="cookie"][style*="position: fixed"],
            [id*="consent"][style*="position: fixed"], [class*="consent"][style*="position: fixed"] {
              display: none !important;
              visibility: hidden !important;
              pointer-events: none !important;
            }
            """
        )
    except Exception:
        pass

    async def try_click_accept(root: Any) -> None:
        candidates = [
            root.locator('button:has-text("Accept")'),
            root.locator('button:has-text("I agree")'),
            root.locator('button:has-text("Agree")'),
            root.locator('button:has-text("OK")'),
            root.locator('button:has-text("Got it")'),
            root.locator('[role="button"]:has-text("Accept")'),
            root.locator('[aria-label*="close" i]'),
            root.locator('button[aria-label*="close" i]'),
        ]
        for loc in candidates:
            try:
                if await loc.first.is_visible(timeout=150):
                    await loc.first.click(timeout=500)
                    return
            except Exception:
                continue

        # If roles are wired up, this is often the most reliable.
        try:
            btn = root.get_by_role("button").filter(has_text=_COOKIE_BUTTON_RE)
            if await btn.first.is_visible(timeout=150):
                await btn.first.click(timeout=500)
        except Exception:
            pass

    try:
        await try_click_accept(page)
        for frame in page.frames:
            try:
                await try_click_accept(frame)
            except Exception:
                continue
    except Exception:
        pass


async def click_cell(page: "Page", cell: Cell, button: str = "left") -> None:
    # IDs like "5_5" start with a digit which is not a valid CSS id selector
    # without escaping; use an attribute selector instead.
    locator = page.locator(f'div.square[id="{cell.x}_{cell.y}"]')

    async def pick_visible(loc: Any) -> Any:
        try:
            count = await loc.count()
        except Exception:
            return loc.first
        for i in range(count):
            item = loc.nth(i)
            try:
                if await item.is_visible(timeout=100):
                    return item
            except Exception:
                continue
        return loc.first

    locator = await pick_visible(locator)
    try:
        await locator.scroll_into_view_if_needed(timeout=3000)
        await locator.click(button=button, timeout=3000)
    except Exception:
        # Often a cookie banner blocks the first action; dismiss and retry once.
        await _dismiss_common_popups(page)
        try:
            await locator.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        try:
            await locator.click(button=button, timeout=3000)
        except Exception:
            # Last resort: bypass actionability checks (helpful for overlays/animations).
            await locator.click(button=button, timeout=3000, force=True)


async def run_bot(
    url: str,
    width: int,
    height: int,
    total_mines: int,
    headful: bool,
    slowmo_ms: int,
    think_ms: int,
    max_steps: int,
    profile_dir: str | None,
    stuck_threshold: int,
    leave_open: bool,
) -> None:
    from playwright.async_api import async_playwright

    async with async_playwright() as p:
        if profile_dir:
            context = await p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=not headful,
                slow_mo=slowmo_ms,
                viewport={"width": 1100, "height": 900},
            )
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            browser = await p.chromium.launch(headless=not headful, slow_mo=slowmo_ms)
            context = await browser.new_context(viewport={"width": 1100, "height": 900})
            page = await context.new_page()
        page.set_default_timeout(3000)
        page.set_default_navigation_timeout(3000)
        await page.add_init_script(
            "document.addEventListener('contextmenu', e => e.preventDefault());"
        )
        await page.goto(url, wait_until="domcontentloaded")
        await page.wait_for_timeout(500)
        await _dismiss_common_popups(page)

        steps = 0
        same_state_steps = 0
        last_sig: tuple[tuple[tuple[int, int, int], ...], tuple[tuple[int, int], ...]] | None = None
        history: list[str] = []

        def fmt_cell(c: Cell) -> str:
            return f"{c.x}_{c.y}"

        async def record_and_wait(note: str) -> None:
            history.append(f"{steps:04d} {note}")
            await page.wait_for_timeout(think_ms)

        while steps < max_steps:
            steps += 1
            state = await game_state(page)
            if state != "playing":
                break

            board = await read_board(page, width=width, height=height)
            opened_sig = tuple(
                sorted(
                    (c.x, c.y, v)
                    for c, v in board.cells.items()
                    if isinstance(v, int) and 0 <= v <= 8
                )
            )
            flagged_sig = tuple(sorted((c.x, c.y) for c, v in board.cells.items() if v == "flagged"))
            sig = (opened_sig, flagged_sig)
            if last_sig is not None and sig == last_sig:
                same_state_steps += 1
            else:
                same_state_steps = 0
            last_sig = sig

            opened = any(isinstance(v, int) for v in board.cells.values())

            if not opened:
                first = choose_first_click(board)
                await click_cell(page, first, button="left")
                await record_and_wait(f"first_click {fmt_cell(first)}")
                continue

            res = solve_step(board, total_mines=total_mines)
            if res.to_flag or res.to_click:
                for c in iter_in_order(res.to_flag):
                    if board.get(c) == "covered":
                        await click_cell(page, c, button="right")
                        await record_and_wait(f"flag {fmt_cell(c)}")
                for c in iter_in_order(res.to_click):
                    if board.get(c) == "covered":
                        await click_cell(page, c, button="left")
                        await record_and_wait(f"click {fmt_cell(c)}")
                continue

            if res.guess:
                await click_cell(page, res.guess, button="left")
                p = "?" if res.guess_prob is None else f"{res.guess_prob:.3f}"
                await record_and_wait(f"guess {fmt_cell(res.guess)} p={p}")
                continue

            # If we observe no board changes for several iterations, force a guess.
            if stuck_threshold > 0 and same_state_steps >= stuck_threshold:
                guess, _prob = best_guess(board, total_mines=total_mines)
                if guess:
                    await click_cell(page, guess, button="left")
                    p = "?" if _prob is None else f"{_prob:.3f}"
                    await record_and_wait(f"stuck_guess {fmt_cell(guess)} p={p}")
                    same_state_steps = 0
                    continue

            # No moves found; click a random covered cell (should be rare).
            covered = [c for c in board.iter_cells() if board.get(c) == "covered"]
            if not covered:
                break
            await click_cell(page, covered[0], button="left")
            await record_and_wait(f"fallback_click {fmt_cell(covered[0])}")

        await page.wait_for_timeout(500)
        state = await game_state(page)
        print(f"Finished after {steps} steps: {state}")
        if state in ("dead", "win"):
            print("---- move history ----")
            for line in history:
                print(line)
            print("---- end history ----")
        if state == "dead":
            try:
                final_board = await read_board(page, width=width, height=height)
                print("---- final board ----")
                print(render_board_for_llm(final_board))
                print("---- end final board ----")
            except Exception as e:
                print(f"Failed to dump final board: {e}")
        if leave_open and headful:
            print("Leaving browser open. Close the browser window to exit.")
            # Wait until the user closes the window; Ctrl+C should exit cleanly.
            try:
                await page.wait_for_event("close")
            except asyncio.CancelledError:
                return
            except Exception:
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    return

        await context.close()
        if not profile_dir:
            await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Play minesweeperonline.com using heuristics.")
    diff = ap.add_mutually_exclusive_group()
    diff.add_argument("--beginner", action="store_true", help="Play beginner (default).")
    diff.add_argument("--intermediate", action="store_true", help="Play intermediate.")
    diff.add_argument("--expert", action="store_true", help="Play expert.")
    ap.add_argument("--url", default=None, help="Override game URL (defaults to chosen difficulty).")
    ap.add_argument("--width", type=int, default=None, help="Override inferred/preset width.")
    ap.add_argument("--height", type=int, default=None, help="Override inferred/preset height.")
    ap.add_argument("--mines", type=int, default=None, help="Override preset mine count.")
    ap.add_argument("--headful", action="store_true")
    ap.add_argument(
        "--leave-open",
        action="store_true",
        help="After finishing, leave the browser open (headful only).",
    )
    ap.add_argument(
        "--close",
        action="store_true",
        help="Force closing the browser after finishing (overrides --leave-open).",
    )
    ap.add_argument("--slowmo-ms", type=int, default=0)
    ap.add_argument("--think-ms", type=int, default=30)
    ap.add_argument("--max-steps", type=int, default=5000)
    ap.add_argument(
        "--stuck-threshold",
        type=int,
        default=5,
        help="If the board state doesn't change for N loops, force a guess (0 disables).",
    )
    ap.add_argument(
        "--profile-dir",
        default=".ms_bot_profile",
        help="Chromium user data dir for persistent cookies/storage; set to empty to disable.",
    )
    args = ap.parse_args()

    difficulty = "beginner"
    if args.intermediate:
        difficulty = "intermediate"
    elif args.expert:
        difficulty = "expert"

    preset = DIFFICULTY_PRESETS[difficulty]
    url = args.url or f"{BASE_URL}#{preset['fragment']}"
    width = args.width if args.width is not None else int(preset["width"])
    height = args.height if args.height is not None else int(preset["height"])
    mines = args.mines if args.mines is not None else int(preset["mines"])

    profile_dir = args.profile_dir if str(args.profile_dir).strip() else None
    # Default behavior: in headful mode, keep the browser open after finishing.
    # Use --close to force shutdown (useful for automation).
    leave_open = args.headful and not args.close
    try:
        asyncio.run(
            run_bot(
                url=url,
                width=width,
                height=height,
                total_mines=mines,
                headful=args.headful,
                leave_open=leave_open,
                slowmo_ms=args.slowmo_ms,
                think_ms=args.think_ms,
                max_steps=args.max_steps,
                profile_dir=profile_dir,
                stuck_threshold=args.stuck_threshold,
            )
        )
    except KeyboardInterrupt:
        # Exit quietly on Ctrl+C (do not print a full traceback).
        return


if __name__ == "__main__":
    main()
