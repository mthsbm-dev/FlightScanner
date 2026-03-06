#!/usr/bin/env python3
"""Fetch new emails directly from IMAP (Hosteurope) and output digestable JSON.

Why:
- More stable than relying on local Maildir + mbsync timestamps.
- Uses per-mailbox UID tracking in memory/imap-monitor-state.json.

Mailboxes:
- INBOX
- INBOX.webde

Credentials:
- Host/user/password sourced from existing mbsync config files.

Outputs JSON:
{
  generated_at, message_count, messages: [ {folder, uid, from, to, subject, date_header, body, attachments, has_real_attachments} ]
}
"""

from __future__ import annotations

import json
import os
import re
import ssl
from dataclasses import dataclass
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
import imaplib

WORKSPACE = Path(__file__).resolve().parents[1]
STATE_PATH = WORKSPACE / 'memory' / 'imap-monitor-state.json'
PASS_PATH = Path.home() / '.config' / 'mbsync' / 'aner_mbohm.pass'
HOST = 'wp010.webpack.hosteurope.de'
PORT = 993
USER = 'wp1055757-bohm'
MAILBOXES = {
    'INBOX': 'INBOX',
    'webde': 'INBOX.webde',
}

# Ignore emails from these senders (own sent emails that would otherwise be picked up as "new")
IGNORED_SENDERS = ['athena@aner.de']

# Ignore emails from these senders (own sent emails that would otherwise be picked up as "new")
IGNORED_SENDERS = ['athena@aner.de']

# If no new UIDs but unread exists, emit a warning.
WARN_IF_UNSEEN_BUT_EMPTY = True

# limits for safety
MAX_UID_FETCH = int(os.environ.get('IMAP_MAX_UID_FETCH', '200'))
BODY_MAX_CHARS = int(os.environ.get('IMAP_BODY_MAX_CHARS', '20000'))


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {
        'last_check': None,
        'mailboxes': {k: {'last_uid': 0} for k in MAILBOXES},
    }


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def extract_text(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            ctype = (part.get_content_type() or '').lower()
            disp = (part.get_content_disposition() or '').lower()
            if ctype == 'text/plain' and disp != 'attachment':
                try:
                    return part.get_content()
                except Exception:
                    continue
        for part in msg.walk():
            disp = (part.get_content_disposition() or '').lower()
            if disp == 'attachment':
                continue
            if (part.get_content_type() or '').lower().startswith('text/'):
                try:
                    return part.get_content()
                except Exception:
                    continue
        return ''
    try:
        return msg.get_content()
    except Exception:
        return ''


def list_attachments(msg):
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


def imap_connect():
    pw = PASS_PATH.read_text().strip()
    ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL(HOST, PORT, ssl_context=ctx)
    M.login(USER, pw)
    return M


def fetch_rfc822(M, uid: int) -> bytes | None:
    code, data = M.uid('fetch', str(uid), '(RFC822)')
    if code != 'OK' or not data:
        return None
    # data can contain tuples; find bytes payload
    for item in data:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def main():
    state = load_state()
    new_messages = []

    M = imap_connect()
    try:
        for key, mailbox in MAILBOXES.items():
            last_uid = int(state.get('mailboxes', {}).get(key, {}).get('last_uid', 0) or 0)
            code, _ = M.select(mailbox, readonly=True)
            if code != 'OK':
                continue

            # Initialize: if no state yet, set last_uid to current max and don't backfill.
            if last_uid == 0:
                code0, data0 = M.uid('search', None, 'ALL')
                if code0 == 'OK' and data0 and data0[0]:
                    all_uids = [int(x) for x in data0[0].split() if x.isdigit()]
                    if all_uids:
                        state.setdefault('mailboxes', {}).setdefault(key, {})['last_uid'] = max(all_uids)
                continue

            # search for UIDs greater than last_uid
            criteria = f'UID {last_uid + 1}:*'
            code, data = M.uid('search', None, criteria)
            if code != 'OK' or not data or not data[0]:
                continue
            uids = [int(x) for x in data[0].split() if x.isdigit()]
            if not uids:
                continue

            uids = sorted(uids)
            # cap fetch to newest
            if len(uids) > MAX_UID_FETCH:
                uids = uids[-MAX_UID_FETCH:]

            max_seen = last_uid
            for uid in uids:
                raw = fetch_rfc822(M, uid)
                if not raw:
                    continue
                msg = BytesParser(policy=policy.default).parsebytes(raw)
                body = (extract_text(msg) or '').strip()
                if len(body) > BODY_MAX_CHARS:
                    body = body[:BODY_MAX_CHARS] + '…'
                attachments = list_attachments(msg)
                new_messages.append({
                    'folder': key,
                    'mailbox': mailbox,
                    'uid': uid,
                    'timestamp': datetime.now(timezone.utc).isoformat(),
                    'from': msg.get('from', ''),
                    'to': msg.get('to', ''),
                    'subject': msg.get('subject', ''),
                    'date_header': msg.get('date', ''),
                    'body': body,
                    'attachments': attachments,
                    'has_real_attachments': bool(attachments),
                })
                if uid > max_seen:
                    max_seen = uid

            # update per mailbox
            state.setdefault('mailboxes', {}).setdefault(key, {})['last_uid'] = max_seen

        now_iso = datetime.now(timezone.utc).isoformat()
        state['last_check'] = now_iso
        state['last_digest'] = now_iso
        save_state(state)

    finally:
        try:
            M.logout()
        except Exception:
            pass

    # Safety: check unseen counts
    unseen = {}
    try:
        for key, mailbox in MAILBOXES.items():
            code, _ = M.select(mailbox, readonly=True)
            if code != 'OK':
                continue
            c2, d2 = M.search(None, 'UNSEEN')
            if c2 == 'OK' and d2 and d2[0]:
                unseen[key] = len(d2[0].split())
            else:
                unseen[key] = 0
    except Exception:
        pass

    warnings = []
    if WARN_IF_UNSEEN_BUT_EMPTY and len(new_messages) == 0:
        if any(v > 0 for v in unseen.values()):
            warnings.append({
                'type': 'unseen_but_no_new_uids',
                'unseen': unseen,
                'hint': 'UNSEEN messages exist but no new UIDs were fetched. Possible state drift or message moves.'
            })

    output = {
        'generated_at': state.get('last_check'),
        'message_count': len(new_messages),
        'messages': new_messages,
        'unseen': unseen,
        'warnings': warnings,
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
