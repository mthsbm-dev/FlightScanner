
# FlightScanner — Rules Instead of Destinations

Lightweight Python tool to watch for cheap Business-Class flights using rules (origins, price, duration, cabin).

Features
- Expandable origin token `EUROPE` (opinionated hub list)
- Price limit, minimum flight duration, cabin class and stopover filters
- Heuristic lie-flat seat detection by aircraft family
- Telegram and SMTP notifications
- Dedupe of already-sent matches

Requirements
- Python 3.10+
- Optional: Kiwi/Tequila API key for searches

Quick start

1) Copy example config and edit values

```bash
cp config.ini.example config.ini
# Edit config.ini: add TEQUILA API key and/or notification settings
```

2) Setup virtualenv and install deps

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3) Test Telegram/SMTP notifications (no Tequila key required)

```bash
python run.py --test-telegram
```

4) Run a single search (needs `TEQUILA API KEY`)

```bash
python run.py --once
```

Useful flags
- `--test-telegram`: send a test notification using values in `config.ini`.
- `--reset-sent`: clear internal deduplication store (`.sent_matches.json`).

Configuration (high level)
- `config.ini.example` contains sections:
	- `[tequila]` — `api_key`
	- `[search]` — `origins`, `currency`, `max_price`, `min_duration_hours`, `date_from`, `date_to`, `limit`, `cabin_class` (C for Business), `max_stopovers`
	- `[notifications]` — `telegram_bot_token`, `telegram_chat_id`
	- `[smtp]` — `host`, `port`, `username`, `password`, `sender`, `recipient` (optional)

Telegram notes
- Create a bot with BotFather to get a token.
- Send the bot a message (or add it to a group) and use `getUpdates` to read `chat.id`, or use utility bots like `@userinfobot`.

Tequila/Kiwi API
- Sign up at Kiwi/Tequila and put your API key into `config.ini` under `[tequila]`.
- With the key present `python run.py --once` will perform searches and notify on matches.

No-API (localfile) mode
- If you don't have a Tequila API key yet, set `backend = localfile` in `config.ini` (this is the default in `config.ini.example`).
- Drop or edit `data/sample_matches.json` with real-like entries (an array of match objects). The tool will read that file and apply filters/notifications the same way.
- Example quick run (no API key needed):

```bash
cp config.ini.example config.ini
# ensure config.ini has search.backend = localfile and localfile_path = data/sample_matches.json
python run.py --once
```

When you later obtain a Tequila key, set `tequila.api_key` and `search.backend = tequila` to switch back.

Scheduling
- Use `cron` / `systemd` / Docker to run `python run.py --once` on a schedule. Example cron entry:

```cron
# run every 6 hours
0 */6 * * * cd /path/to/FlightScanner && /path/to/.venv/bin/python run.py --once
```

macOS (launchd) example

1. Make the included wrapper script executable:

```bash
chmod +x scripts/run_flightscanner.sh
```

2. Copy the plist to your LaunchAgents and load it (this runs every 6 hours):

```bash
cp scripts/com.flightscanner.runner.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.flightscanner.runner.plist
```

3. Check logs:

```bash
tail -f /tmp/flightscanner.log
tail -f /tmp/flightscanner.err.log
```

4. To stop/remove the job:

```bash
launchctl unload ~/Library/LaunchAgents/com.flightscanner.runner.plist
rm ~/Library/LaunchAgents/com.flightscanner.runner.plist
```

Notes
- Edit `scripts/com.flightscanner.runner.plist` if you want a different interval or paths.
- If you prefer `cron`, use the `crontab -e` entry shown above.

Security
- `config.ini` is in `.gitignore` by default. Never commit API keys or tokens.

Contributing
- Open an issue or PR on the repository for feature requests or fixes.

