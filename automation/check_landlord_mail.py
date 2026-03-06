#!/usr/bin/env python3
"""Scan Maildir folders for new messages relevant to landlord duties.

Currently reads \"INBOX\" and \"webde\" under ~/Mail/aner_mbohm/INBOX.
Outputs JSON with new message metadata + body text (plain text best effort).
Maintains incremental state in memory/mail-monitor-state.json keyed per folder.
"""
import json
import os
from pathlib import Path
from email import policy
from email.parser import BytesParser
from datetime import datetime, timezone

STATE_PATH = Path(__file__).parent.parent / 'memory/mail-monitor-state.json'
MAILDIR_BASE = Path.home() / 'Mail' / 'aner_mbohm' / 'INBOX'
CLASSIFIER_PATH = Path(__file__).parent.parent / 'mail-classifier/src/classifier.py'
FOLDERS = {
    'INBOX': MAILDIR_BASE,
    'webde': MAILDIR_BASE / 'webde',
}
FORCE_LOOKBACK_HOURS = float(os.environ.get('FORCE_LOOKBACK_HOURS', '0') or 0)

STATE_TEMPLATE = {
    'last_check': None,
    'folders': {folder: 0 for folder in FOLDERS},
}


def load_state():
    if STATE_PATH.exists():
        with STATE_PATH.open() as f:
            data = json.load(f)
    else:
        data = STATE_TEMPLATE.copy()
        data['folders'] = STATE_TEMPLATE['folders'].copy()
    # ensure new folders present
    if 'folders' not in data:
        data['folders'] = {}
    for folder in FOLDERS:
        data['folders'].setdefault(folder, 0)
    return data


def save_state(state):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with STATE_PATH.open('w') as f:
        json.dump(state, f, indent=2)


def extract_text(msg):
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disp = (part.get_content_disposition() or '').lower()
            # Prefer human text; skip attachments.
            if content_type == 'text/plain' and disp != 'attachment':
                try:
                    return part.get_content()
                except Exception:
                    continue
        # fallback: first text/* that isn't an attachment
        for part in msg.walk():
            disp = (part.get_content_disposition() or '').lower()
            if disp == 'attachment':
                continue
            if part.get_content_type().startswith('text/'):
                try:
                    return part.get_content()
                except Exception:
                    continue
        return ''
    else:
        try:
            return msg.get_content()
        except Exception:
            return ''


def list_attachments(msg):
    """Return attachment metadata.

    We treat as 'real attachment' if content_disposition=attachment OR has a filename,
    and it's not an inline image.
    """
    atts = []
    if not msg.is_multipart():
        return atts

    for part in msg.walk():
        if part.is_multipart():
            continue
        ctype = (part.get_content_type() or '').lower()
        disp = (part.get_content_disposition() or '').lower()
        filename = part.get_filename() or ''
        if isinstance(filename, bytes):
            try:
                filename = filename.decode('utf-8', errors='replace')
            except Exception:
                filename = str(filename)

        is_inline_image = (disp == 'inline') and ctype.startswith('image/')
        is_attachment = (disp == 'attachment') or bool(filename)
        if not is_attachment or is_inline_image:
            continue

        try:
            payload = part.get_payload(decode=True) or b''
            size = len(payload)
        except Exception:
            size = None

        atts.append({
            'filename': str(filename),
            'content_type': str(ctype),
            'disposition': str(disp),
            'size_bytes': int(size) if size is not None else None,
        })

    return atts


def collect_messages():
    state = load_state()
    new_messages = []
    force_cutoff_ts = None
    if FORCE_LOOKBACK_HOURS > 0:
        force_cutoff_ts = datetime.now(timezone.utc).timestamp() - FORCE_LOOKBACK_HOURS * 3600
        print(f"DEBUG: force_cutoff_ts={force_cutoff_ts} -- going back {FORCE_LOOKBACK_HOURS}h from now", file=__import__('sys').stderr)

    for folder_name, folder_path in FOLDERS.items():
        last_ts = state['folders'].get(folder_name, 0)
        effective_last_ts = last_ts
        if force_cutoff_ts is not None:
            effective_last_ts = min(last_ts, force_cutoff_ts)
        print(f"DEBUG: folder={folder_name}, last_ts={last_ts}, effective={effective_last_ts}, force_cutoff={force_cutoff_ts}", file=__import__('sys').stderr)
        newest_ts = last_ts
        if not folder_path.exists():
            continue
        for sub in ('new', 'cur'):
            target = folder_path / sub
            if not target.exists():
                continue
            for msg_file in sorted(target.iterdir()):
                try:
                    mtime = msg_file.stat().st_mtime
                except FileNotFoundError:
                    continue
                if mtime <= effective_last_ts:
                    continue
                try:
                    with msg_file.open('rb') as f:
                        msg = BytesParser(policy=policy.default).parse(f)
                except Exception:
                    continue
                body = extract_text(msg)
                attachments = list_attachments(msg)
                new_messages.append({
                    'folder': folder_name,
                    'path': str(msg_file),
                    'timestamp': datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                    'from': msg.get('from', ''),
                    'to': msg.get('to', ''),
                    'subject': msg.get('subject', ''),
                    'date_header': msg.get('date', ''),
                    'body': body.strip(),
                    'attachments': attachments,
                    'has_real_attachments': bool(attachments),
                })
                if mtime > newest_ts:
                    newest_ts = mtime
        state['folders'][folder_name] = newest_ts
    now_iso = datetime.now(timezone.utc).isoformat()
    state['last_check'] = now_iso
    state['last_digest'] = now_iso
    save_state(state)
    return new_messages, state


def main():
    messages, state = collect_messages()
    output = {
        'generated_at': state['last_check'],
        'message_count': len(messages),
        'messages': messages,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


def classify_and_notify(messages: list) -> list:
    """Classify messages and send Telegram notifications."""
    if not messages:
        return []
    
    # Limit to most recent 50 messages for classification (performance)
    recent_messages = messages[-50:]
    
    # Prepare messages for classifier
    msg_for_classifier = {
        "messages": [
            {
                "subject": m.get("subject", ""),
                "body": m.get("body", "")[:1000],  # Limit body size
                "from": m.get("from", ""),
                "date": m.get("date_header", "")
            }
            for m in recent_messages
        ]
    }
    
    # Call classifier
    import subprocess
    json_output = None
    try:
        result = subprocess.run(
            ["python3", str(CLASSIFIER_PATH), "--json"],
            input=json.dumps(msg_for_classifier),
            capture_output=True,
            text=True,
            timeout=60
        )
        
        if result.returncode == 0:
            # Parse classifier output - find JSON array
            output = result.stdout.strip()
            try:
                # Find JSON array in output
                start = output.find('[')
                end = output.rfind(']') + 1
                if start >= 0 and end > start:
                    json_output = json.loads(output[start:end])
            except json.JSONDecodeError as e:
                print(f"JSON parse error: {e}, output: {output[:200]}", file=__import__('sys').stderr)
                
    except Exception as e:
        print(f"Classification error: {e}", file=__import__('sys').stderr)
    
    # Send Telegram digest with ALL messages
    if json_output:
        send_telegram_digest(messages, json_output)
        
        # Merge classification with original messages
        for i, classified in enumerate(json_output):
            if i < len(recent_messages):
                idx = len(messages) - len(recent_messages) + i
                if idx >= 0:
                    messages[idx]["category"] = classified.get("category", "unbekannt")
                    messages[idx]["confidence"] = classified.get("confidence", 0)
    
    return messages


def send_telegram_digest(all_messages: list, classified_messages: list = None):
    """Send digest to Telegram."""
    import requests
    
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    
    if not token or not chat_id:
        # Try reading from FlightScanner config
        config_path = Path(__file__).parent.parent / 'FlightScanner/config.ini'
        if config_path.exists():
            import configparser
            config = configparser.ConfigParser()
            config.read(config_path)
            try:
                token = config.get('notifications', 'telegram_bot_token')
                chat_id = config.get('notifications', 'telegram_chat_id')
            except:
                pass
    
    if not token or not chat_id:
        print("Telegram not configured", file=__import__('sys').stderr)
        return
    
    # Use classified messages if provided, otherwise all_messages
    msgs_to_show = classified_messages if classified_messages else all_messages
    
    # Generate digest
    by_category = {}
    for msg in msgs_to_show:
        cat = msg.get("category", "unbekannt")
        by_category.setdefault(cat, []).append(msg)
    
    emoji = {"vermieterrelevant": "🏠", "finanzen": "💰", "privat/werbung": "📬", "unbekannt": "❓"}
    
    lines = ["📧 *Mail-Digest*", ""]
    
    for cat in ["vermieterrelevant", "finanzen", "privat/werbung", "unbekannt"]:
        if cat in by_category:
            items = by_category[cat]
            lines.append(f"{emoji.get(cat, '📧')} *{cat}* ({len(items)})")
            for item in items[:3]:
                subj = item.get("subject", "")[:40]
                if len(item.get("subject", "")) > 40:
                    subj += "..."
                lines.append(f"  • {subj}")
            if len(items) > 3:
                lines.append(f"  ... +{len(items)-3} mehr")
            lines.append("")
    
    text = "\n".join(lines)
    
    try:
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10
        )
    except Exception as e:
        print(f"Telegram send error: {e}", file=__import__('sys').stderr)


def main():
    state = load_state()
    messages, state = collect_messages()
    messages = classify_and_notify(messages)  # NEW: classify and notify
    output = {
        'generated_at': state['last_check'],
        'message_count': len(messages),
        'messages': messages,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
