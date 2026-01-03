FlightScanner — Regeln statt Ziele

Lightweight Python tool to watch for cheap Business-Class flights using a rules-based search.

Features
- Flexible origin list (Europe hubs by default)
- Price limit, minimum total flight duration, cabin class filter
- Heuristic check for lie-flat seats via aircraft types
- Telegram notification support

Requirements
- Python 3.10+
- A Kiwi/Tequila API key (optional but recommended)

Quick start
1. Copy `config.ini.example` to `config.ini` and fill in `TEQUILA_API_KEY` and `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` if you want notifications.
2. Create a virtualenv and install dependencies:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

3. Run a single search:

```bash
python run.py --once
```

Notes
- The tool uses Kiwi/Tequila API if `TEQUILA_API_KEY` is provided. Without a key it will exit with a hint.
- Lie-flat detection is heuristic (aircraft family). For exact seat maps, provider-specific data is required.

If you want, I can add scheduled runs (systemd timer / Docker) or SMTP notifications next.
