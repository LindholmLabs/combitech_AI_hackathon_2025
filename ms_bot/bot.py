from __future__ import annotations

import argparse
import asyncio
import re
import time
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

async def detect_board_origin(page: "Page") -> tuple[int, int]:
    data: dict[str, Any] = await page.evaluate(
        """
        () => {
          if (document.getElementById('1_1')) return { ox: 1, oy: 1 };
          if (document.getElementById('0_0')) return { ox: 0, oy: 0 };
          const any = document.querySelector('div.square[id]');
          if (!any || !any.id) return { ox: 0, oy: 0 };
          const m = /^(\\d+)_(\\d+)$/.exec(any.id);
          if (!m) return { ox: 0, oy: 0 };
          return { ox: parseInt(m[1], 10), oy: parseInt(m[2], 10) };
        }
        """
    )
    return int(data.get("ox", 0)), int(data.get("oy", 0))


async def read_board(page: "Page", origin_x: int, origin_y: int, width: int, height: int) -> BoardSnapshot:
    raw: list[list[str]] = await page.evaluate(
        """
        ({ox, oy, w, h}) => {
          const out = [];
          for (let y = oy; y < oy + h; y++) {
            for (let x = ox; x < ox + w; x++) {
              const id = `${x}_${y}`;
              const e = document.getElementById(id);
              out.push([id, e ? (e.className || '') : '']);
            }
          }
          return out;
        }
        """,
        {"ox": origin_x, "oy": origin_y, "w": width, "h": height},
    )
    data: list[dict[str, Any]] = [{"id": pair[0], "className": pair[1]} for pair in raw]
    cells: dict[Cell, object] = {}
    coords: list[Cell] = []
    for item in data:
        c = _parse_cell_id(item.get("id", ""))
        if not c:
            continue
        coords.append(c)
        cells[c] = parse_square_class(item.get("className", ""))

    for y in range(origin_y, origin_y + height):
        for x in range(origin_x, origin_x + width):
            cells.setdefault(Cell(x, y), "covered")

    return BoardSnapshot(origin_x=origin_x, origin_y=origin_y, width=width, height=height, cells=cells)


async def game_state(page: "Page") -> str:
    # Avoid Playwright auto-wait (which can add multi-second pauses); read directly.
    face = await page.evaluate("() => document.getElementById('face')?.className || ''")
    face = face or ""
    if "facewin" in face:
        return "win"
    if "facedead" in face:
        return "dead"
    return "playing"

async def _dispatch_actions(page: "Page", actions: list[dict[str, Any]]) -> int:
    # Faster than N separate Playwright clicks; also bypasses strict actionability checks.
    return await page.evaluate(
        """
        (actions) => {
          let done = 0;
          for (const a of actions) {
            const id = a.id;
            const buttonName = a.button;
            const e = document.getElementById(id);
            if (!e) continue;
            const isRight = buttonName === 'right';
            const button = isRight ? 2 : 0;
            const buttons = isRight ? 2 : 1;
            const common = {
              bubbles: true,
              cancelable: true,
              composed: true,
              view: window,
              button,
              buttons
            };
            e.dispatchEvent(new MouseEvent('mousedown', common));
            e.dispatchEvent(new MouseEvent('mouseup', common));
            e.dispatchEvent(new MouseEvent(isRight ? 'contextmenu' : 'click', common));
            done += 1;
          }
          return done;
        }
        """,
        actions,
    )

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
    cell_id = f"{cell.x}_{cell.y}"
    try:
        done = await _dispatch_actions(page, [{"id": cell_id, "button": button}])
        if done:
            return
    except Exception:
        pass

    locator = page.locator(f'div.square[id="{cell_id}"]').first
    try:
        await locator.click(button=button, timeout=3000)
    except Exception:
        # Often a cookie banner blocks the first action; dismiss and retry once.
        await _dismiss_common_popups(page)
        await locator.click(button=button, timeout=3000, force=True)

async def reset_game(page: "Page") -> None:
    # Start a fresh game on the current difficulty. MinesweeperOnline resets via the face button.
    try:
        await page.evaluate(
            """
            () => {
              const face = document.getElementById('face');
              if (face) face.click();
            }
            """
        )
    except Exception:
        try:
            await page.locator("#face").click(timeout=1000)
        except Exception:
            pass

    # Wait until face is not in a terminal state and the board looks reset (blank squares exist).
    try:
        await page.wait_for_function(
            """
            () => {
              const face = document.getElementById('face');
              const cls = face ? (face.className || '') : '';
              if (cls.includes('facewin') || cls.includes('facedead')) return false;
              const anyBlank = !!document.querySelector('div.square.blank');
              return anyBlank;
            }
            """,
            timeout=3000,
        )
    except Exception:
        pass

def _parse_timer_digit(text: str, class_name: str) -> int | None:
    text = (text or "").strip()
    if len(text) == 1 and text.isdigit():
        return int(text)
    m = re.search(r"\btime(\d)\b", class_name or "")
    if m:
        return int(m.group(1))
    return None


async def read_website_seconds(page: "Page") -> int | None:
    data: dict[str, str] = await page.evaluate(
        """
        () => {
          const ids = ['seconds_hundreds', 'seconds_tens', 'seconds_ones'];
          const out = {};
          for (const id of ids) {
            const el = document.getElementById(id);
            out[id + '_text'] = el ? (el.textContent || '').trim() : '';
            out[id + '_class'] = el ? (el.className || '') : '';
          }
          return out;
        }
        """
    )
    h = _parse_timer_digit(data.get("seconds_hundreds_text", ""), data.get("seconds_hundreds_class", ""))
    t = _parse_timer_digit(data.get("seconds_tens_text", ""), data.get("seconds_tens_class", ""))
    o = _parse_timer_digit(data.get("seconds_ones_text", ""), data.get("seconds_ones_class", ""))
    if h is None or t is None or o is None:
        return None
    return h * 100 + t * 10 + o


async def play_one_game(
    page: "Page",
    url: str,
    width: int,
    height: int,
    total_mines: int,
    think_ms: int,
    max_steps: int,
    stuck_threshold: int,
    dump_history: bool,
    dump_board_on_dead: bool,
) -> tuple[str, int, float, int | None]:
    start = time.perf_counter()

    await page.goto(url, wait_until="domcontentloaded")
    await page.wait_for_timeout(200)
    await _dismiss_common_popups(page)
    await reset_game(page)
    origin_x, origin_y = await detect_board_origin(page)

    steps = 0
    same_state_steps = 0
    last_sig: tuple[tuple[tuple[int, int, int], ...], tuple[tuple[int, int], ...]] | None = None
    history: list[str] = []

    def fmt_cell(c: Cell) -> str:
        return f"{c.x}_{c.y}"

    async def record_and_wait(note: str) -> None:
        history.append(f"{steps:04d} {note}")
        if think_ms > 0:
            await page.wait_for_timeout(think_ms)

    while steps < max_steps:
        steps += 1
        state = await game_state(page)
        if state != "playing":
            break

        board = await read_board(page, origin_x=origin_x, origin_y=origin_y, width=width, height=height)
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
            flags = [c for c in iter_in_order(res.to_flag) if board.get(c) == "covered"]
            clicks = [c for c in iter_in_order(res.to_click) if board.get(c) == "covered"]

            if flags:
                await _dispatch_actions(
                    page,
                    [{"id": f"{c.x}_{c.y}", "button": "right"} for c in flags],
                )
                for c in flags:
                    history.append(f"{steps:04d} flag {fmt_cell(c)}")

            if clicks:
                await _dispatch_actions(
                    page,
                    [{"id": f"{c.x}_{c.y}", "button": "left"} for c in clicks],
                )
                for c in clicks:
                    history.append(f"{steps:04d} click {fmt_cell(c)}")

            if think_ms > 0:
                await page.wait_for_timeout(think_ms)
            continue

        if res.guess:
            await click_cell(page, res.guess, button="left")
            p = "?" if res.guess_prob is None else f"{res.guess_prob:.3f}"
            await record_and_wait(f"guess {fmt_cell(res.guess)} p={p}")
            continue

        if stuck_threshold > 0 and same_state_steps >= stuck_threshold:
            guess, prob = best_guess(board, total_mines=total_mines)
            if guess:
                await click_cell(page, guess, button="left")
                p = "?" if prob is None else f"{prob:.3f}"
                await record_and_wait(f"stuck_guess {fmt_cell(guess)} p={p}")
                same_state_steps = 0
                continue

        covered = [c for c in board.iter_cells() if board.get(c) == "covered"]
        if not covered:
            break
        await click_cell(page, covered[0], button="left")
        await record_and_wait(f"fallback_click {fmt_cell(covered[0])}")

    if think_ms > 0:
        await page.wait_for_timeout(200)
    state = await game_state(page)
    website_seconds = None
    try:
        website_seconds = await read_website_seconds(page)
    except Exception:
        website_seconds = None
    elapsed = time.perf_counter() - start

    timer_note = "" if website_seconds is None else f" (site: {website_seconds}s)"
    print(f"Finished after {steps} steps: {state}{timer_note}")
    if dump_history and state in ("dead", "win"):
        print("---- move history ----")
        for line in history:
            print(line)
        print("---- end history ----")
    if dump_board_on_dead and state == "dead":
        try:
            final_board = await read_board(
                page, origin_x=origin_x, origin_y=origin_y, width=width, height=height
            )
            print("---- final board ----")
            print(render_board_for_llm(final_board))
            print("---- end final board ----")
        except Exception as e:
            print(f"Failed to dump final board: {e}")

    return state, steps, elapsed, website_seconds


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
    dump_history: bool,
    dump_board_on_dead: bool,
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
        await play_one_game(
            page=page,
            url=url,
            width=width,
            height=height,
            total_mines=total_mines,
            think_ms=think_ms,
            max_steps=max_steps,
            stuck_threshold=stuck_threshold,
            dump_history=dump_history,
            dump_board_on_dead=dump_board_on_dead,
        )
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

async def run_n_times(
    runs: int,
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
    dump_history: bool,
    dump_board_on_dead: bool,
) -> None:
    from playwright.async_api import async_playwright

    durations: list[float] = []
    site_seconds: list[int | None] = []
    losses = 0
    wins = 0

    async with async_playwright() as p:
        for i in range(1, runs + 1):
            # Important: start a fresh browser/context each run so we never carry
            # over a win/dead state or stale DOM.
            browser = None
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

            print(f"=== Run {i}/{runs} ===")
            state, _steps, elapsed, website_seconds = await play_one_game(
                page=page,
                url=url,
                width=width,
                height=height,
                total_mines=total_mines,
                think_ms=think_ms,
                max_steps=max_steps,
                stuck_threshold=stuck_threshold,
                dump_history=dump_history,
                dump_board_on_dead=dump_board_on_dead,
            )
            durations.append(elapsed)
            site_seconds.append(website_seconds)
            if state == "dead":
                losses += 1
            elif state == "win":
                wins += 1

            await context.close()
            if browser is not None:
                await browser.close()

        print("=== Summary ===")
        for i, d in enumerate(durations, start=1):
            ws = site_seconds[i - 1]
            ws_note = "" if ws is None else f" site={ws}s"
            print(f"Run {i}: {d:.3f}s{ws_note}")
        print(f"Wins: {wins}  Losses: {losses}  Total: {runs}")
        win_pct = (wins / runs * 100.0) if runs else 0.0
        win_times = [s for s in site_seconds if s is not None]
        avg_site = (sum(win_times) / len(win_times)) if win_times else None
        if avg_site is None:
            print(f"Win%: {win_pct:.1f}%  Avg site time: n/a")
        else:
            print(f"Win%: {win_pct:.1f}%  Avg site time: {avg_site:.2f}s")


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
    ap.add_argument("--runs", type=int, default=1, help="Play N games back-to-back.")
    ap.add_argument(
        "--dump-history",
        action="store_true",
        help="Print move history when a game ends (useful with --runs).",
    )
    ap.add_argument(
        "--dump-board",
        action="store_true",
        help="Print final board when dead (useful with --runs).",
    )
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
    # For multi-run mode, always close between runs and exit after printing stats.
    leave_open = (args.headful and not args.close) and (not args.runs or int(args.runs) <= 1)
    try:
        if args.runs and args.runs > 1:
            asyncio.run(
                run_n_times(
                    runs=int(args.runs),
                    url=url,
                    width=width,
                    height=height,
                    total_mines=mines,
                    headful=args.headful,
                    slowmo_ms=args.slowmo_ms,
                    think_ms=args.think_ms,
                    max_steps=args.max_steps,
                    profile_dir=profile_dir,
                    stuck_threshold=args.stuck_threshold,
                    leave_open=False,
                    dump_history=bool(args.dump_history),
                    dump_board_on_dead=bool(args.dump_board),
                )
            )
        else:
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
                    dump_history=True,
                    dump_board_on_dead=True,
                )
            )
    except KeyboardInterrupt:
        # Exit quietly on Ctrl+C (do not print a full traceback).
        return


if __name__ == "__main__":
    main()
