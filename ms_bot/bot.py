from __future__ import annotations

import argparse
import asyncio
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from playwright.async_api import Page

from .board import BoardSnapshot, Cell, parse_square_class
from .solver import best_guess, choose_first_click, iter_in_order, solve_step


DEFAULT_URL = "https://minesweeperonline.com/#beginner"

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
                await page.wait_for_timeout(think_ms)
                continue

            res = solve_step(board, total_mines=total_mines)
            if res.to_flag or res.to_click:
                for c in iter_in_order(res.to_flag):
                    if board.get(c) == "covered":
                        await click_cell(page, c, button="right")
                        await page.wait_for_timeout(think_ms)
                for c in iter_in_order(res.to_click):
                    if board.get(c) == "covered":
                        await click_cell(page, c, button="left")
                        await page.wait_for_timeout(think_ms)
                continue

            if res.guess:
                await click_cell(page, res.guess, button="left")
                await page.wait_for_timeout(think_ms)
                continue

            # If we observe no board changes for several iterations, force a guess.
            if stuck_threshold > 0 and same_state_steps >= stuck_threshold:
                guess, _prob = best_guess(board, total_mines=total_mines)
                if guess:
                    await click_cell(page, guess, button="left")
                    await page.wait_for_timeout(think_ms)
                    same_state_steps = 0
                    continue

            # No moves found; click a random covered cell (should be rare).
            covered = [c for c in board.iter_cells() if board.get(c) == "covered"]
            if not covered:
                break
            await click_cell(page, covered[0], button="left")
            await page.wait_for_timeout(think_ms)

        await page.wait_for_timeout(500)
        state = await game_state(page)
        print(f"Finished after {steps} steps: {state}")
        if headful:
            await page.wait_for_timeout(10_000)
        await context.close()
        if not profile_dir:
            await browser.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Play minesweeperonline.com using heuristics.")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--width", type=int, default=9)
    ap.add_argument("--height", type=int, default=9)
    ap.add_argument("--mines", type=int, default=10)
    ap.add_argument("--headful", action="store_true")
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
    profile_dir = args.profile_dir if str(args.profile_dir).strip() else None
    asyncio.run(
        run_bot(
            url=args.url,
            width=args.width,
            height=args.height,
            total_mines=args.mines,
            headful=args.headful,
            slowmo_ms=args.slowmo_ms,
            think_ms=args.think_ms,
            max_steps=args.max_steps,
            profile_dir=profile_dir,
            stuck_threshold=args.stuck_threshold,
        )
    )


if __name__ == "__main__":
    main()
