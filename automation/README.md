# automation/

Workspace automation scripts for mail monitoring, LLM logging, and misc utilities.

## Mail monitoring & digest

| Script | Description |
|---|---|
| `check_landlord_mail_imap.py` | IMAP monitor for landlord mailbox (INBOX + webde), outputs new emails as JSON |
| `check_athena_mail_imap.py` | IMAP monitor for athena@ mailbox, outputs new emails as JSON |
| `check_landlord_mail.py` | Maildir-based mail scanner with classification and Telegram notifications |
| `imap_backfill_batch.py` | Batch backfill of historical emails from IMAP with UID paging |
| `send_landlord_digest.py` | Full digest pipeline: fetch, classify (rules + phi3 + Claude), render HTML/text, send via email + Telegram |
| `fix_filter.py` | One-off patch to add sender-ignore filter to the IMAP mail checker |

## LLM wrappers

| Script | Description |
|---|---|
| `anthropic.sh` | Shell wrapper for the Anthropic Messages API |
| `anthropic_judge.py` | Python wrapper around `anthropic.sh` for batch JSON classification |
| `local_llm.sh` | Shell wrapper for local Ollama LLM (phi3) with timeout |

## Logging (MongoDB)

| Script | Description |
|---|---|
| `llm_logger.py` | Parses OpenClaw session files, logs LLM calls to MongoDB |
| `log_llm.py` | Library/CLI to log and query LLM calls (`log`, `recent`, `query`, `stats`) |
| `exec_logger.py` | Parses OpenClaw session files, logs exec/code calls to MongoDB |

## Other

| Script | Description |
|---|---|
| `moltbook_heartbeat.py` | Monitors Moltbook notifications, DMs, and activity |
| `tts_elevenlabs.sh` | Text-to-speech via ElevenLabs API |
