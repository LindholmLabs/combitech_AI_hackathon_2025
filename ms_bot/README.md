MinesweeperOnline bot (Playwright + Python)

Run:
1. Install deps: `pip install -r ms_bot/requirements.txt`
2. Install browsers: `python -m playwright install`
3. Start (defaults to beginner): `python -m ms_bot.bot --headful`
4. Other sizes: `python -m ms_bot.bot --intermediate --headful` or `python -m ms_bot.bot --expert --headful`
5. Multiple runs: `python -m ms_bot.bot --expert --runs 50` (add `--dump-history` / `--dump-board` for per-run dumps)
6. Run timing: the bot also reads the in-page timer from `seconds_hundreds`, `seconds_tens`, `seconds_ones` and reports per-run and average site time.

Notes:
- Difficulty presets: `--beginner` (9x9, 10 mines), `--intermediate` (16x16, 40 mines), `--expert` (16x30, 99 mines).
- You can override with `--url`, `--width`, `--height`, `--mines` if needed.
- The bot reads DOM square ids like `2_3` and automatically detects the board's coordinate origin (0-based vs 1-based).
- Squares have ids that begin with digits (e.g. `5_5`), so the bot uses an attribute selector (`[id="5_5"]`) rather than `#5_5`.
- The bot also attempts to auto-dismiss common cookie/consent popups (and injects CSS to hide known consent containers).
- Cookie persistence: by default the bot uses a persistent Chromium profile in `.ms_bot_profile` so cookies/localStorage are retained across runs. Disable with `--profile-dir ""`.
- Stuck guessing: if the board state doesn't change for a few loops, the bot forces a guess (`--stuck-threshold 5`, disable with `--stuck-threshold 0`).
- Board detection: the bot only uses *visible* `.square` elements to avoid hidden template/ad elements with `square` classes and out-of-range ids.
- Browser lifecycle: in `--headful` mode, the bot leaves the browser open after the game ends (close it manually). Use `--close` to force it to exit.
- Flag safety: before placing multiple flags, the bot filters them to avoid over-flagging around any revealed number (e.g. never places 2 flags adjacent to a `1`).
- Performance: within a step, clicks/flags are batched and dispatched in-page (reduces Playwright round-trips); `read_board()` also reads the full board in a single `page.evaluate()` by iterating expected `x_y` ids (no repeated DOM scanning).
- Guessing: frontier enumeration now weights solutions using the global remaining mine count, so it can find additional forced safe/mine cells and pick better low-risk guesses.
- Chording: when a number is satisfied by adjacent flags, the bot can middle-click the number to open all remaining neighbors quickly (disable with `--no-chord`).
