MinesweeperOnline bot (Playwright + Python)

Run:
1. Install deps: `pip install -r ms_bot/requirements.txt`
2. Install browsers: `python -m playwright install`
3. Start: `python -m ms_bot.bot --headful`

Notes:
- Defaults to beginner (9x9, 10 mines). Use `--width/--height/--mines` for other sizes.
- The bot reads DOM square ids like `2_3` and automatically detects the board's coordinate origin (0-based vs 1-based).
- Squares have ids that begin with digits (e.g. `5_5`), so the bot uses an attribute selector (`[id="5_5"]`) rather than `#5_5`.
- The bot also attempts to auto-dismiss common cookie/consent popups (and injects CSS to hide known consent containers).
- Cookie persistence: by default the bot uses a persistent Chromium profile in `.ms_bot_profile` so cookies/localStorage are retained across runs. Disable with `--profile-dir ""`.
- Stuck guessing: if the board state doesn't change for a few loops, the bot forces a guess (`--stuck-threshold 5`, disable with `--stuck-threshold 0`).
- Board detection: the bot only uses *visible* `.square` elements to avoid hidden template/ad elements with `square` classes and out-of-range ids.
