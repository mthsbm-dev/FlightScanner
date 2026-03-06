#!/usr/bin/env python3
"""Backfill emails from a chosen IMAP mailbox in batches (UID paging).

Default use-case: analyze historical mail (e.g., INBOX.Abos) in chunks.

State is stored in memory/imap-backfill-state.json as a cursor per mailbox.
We page from newest → older by default.

Output JSON compatible with send_landlord_digest.py:
{
  generated_at,
  mailbox,
  batch: {size, uid_from, uid_to, remaining_hint},
  message_count,
  messages: [ {folder, mailbox, uid, from,to,subject,date_header,body,attachments,has_real_attachments,path:null,timestamp} ]
}
"""

from __future__ import annotations

import argparse
import json
import ssl
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from pathlib import Path
import imaplib

WORKSPACE = Path(__file__).resolve().parents[1]
STATE_PATH = WORKSPACE / 'memory' / 'imap-backfill-state.json'
PASS_PATH = Path.home() / '.config' / 'mbsync' / 'aner_mbohm.pass'
HOST = 'wp010.webpack.hosteurope.de'
PORT = 993
USER = 'wp1055757-bohm'


def load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding='utf-8'))
        except Exception:
            pass
    return {'mailboxes': {}}


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


def fetch_rfc822(M, uid: int) -> bytes | None:
    code, data = M.uid('fetch', str(uid), '(RFC822)')
    if code != 'OK' or not data:
        return None
    for item in data:
        if isinstance(item, tuple) and len(item) == 2 and isinstance(item[1], (bytes, bytearray)):
            return bytes(item[1])
    return None


def imap_connect():
    pw = PASS_PATH.read_text().strip()
    ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL(HOST, PORT, ssl_context=ctx)
    M.login(USER, pw)
    return M


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mailbox', required=True, help='IMAP mailbox, e.g. INBOX.Abos')
    ap.add_argument('--key', default=None, help='Logical folder key name, default derived from mailbox (after INBOX.)')
    ap.add_argument('--batch-size', type=int, default=100)
    ap.add_argument('--body-max-chars', type=int, default=20000)
    ap.add_argument('--reset', action='store_true')
    args = ap.parse_args()

    mailbox = args.mailbox
    key = args.key or (mailbox.split('INBOX.', 1)[1] if mailbox.startswith('INBOX.') else mailbox)

    state = load_state()
    mb_state = state.setdefault('mailboxes', {}).setdefault(mailbox, {})
    if args.reset:
        mb_state.pop('cursor_uid', None)

    M = imap_connect()
    new_messages = []
    uid_from = uid_to = None
    try:
        code, _ = M.select(mailbox, readonly=True)
        if code != 'OK':
            # Try quoted mailbox (handles spaces/special chars on some servers)
            code, _ = M.select(f'"{mailbox}"', readonly=True)
        if code != 'OK':
            raise SystemExit(f'Cannot select {mailbox}')

        code0, data0 = M.uid('search', None, 'ALL')
        if code0 != 'OK' or not data0 or not data0[0]:
            all_uids = []
        else:
            all_uids = [int(x) for x in data0[0].split() if x.isdigit()]

        if not all_uids:
            cursor = 0
        else:
            max_uid = max(all_uids)
            cursor = int(mb_state.get('cursor_uid') or max_uid)

        if cursor <= 0:
            # done
            out = {
                'generated_at': datetime.now(timezone.utc).isoformat(),
                'mailbox': mailbox,
                'batch': {'size': 0, 'uid_from': None, 'uid_to': None, 'remaining_hint': 0},
                'message_count': 0,
                'messages': [],
            }
            print(json.dumps(out, ensure_ascii=False, indent=2))
            return

        uid_to = cursor
        uid_from = max(1, uid_to - args.batch_size + 1)

        # fetch this UID range
        criteria = f'UID {uid_from}:{uid_to}'
        code1, data1 = M.uid('search', None, criteria)
        uids = [int(x) for x in (data1[0].split() if (code1 == 'OK' and data1 and data1[0]) else []) if x.isdigit()]
        uids = sorted(uids)

        for uid in uids:
            raw = fetch_rfc822(M, uid)
            if not raw:
                continue
            msg = BytesParser(policy=policy.default).parsebytes(raw)
            body = (extract_text(msg) or '').strip()
            if len(body) > args.body_max_chars:
                body = body[:args.body_max_chars] + '…'
            attachments = list_attachments(msg)

            # headerregistry parsing can throw on malformed headers; use raw header bytes as fallback.
            def safe_hdr(name: str) -> str:
                try:
                    v = msg.get(name, failobj='')
                    return str(v or '')
                except Exception:
                    try:
                        rawh = msg.get_raw(name)
                        if rawh is None:
                            return ''
                        if isinstance(rawh, (bytes, bytearray)):
                            return rawh.decode('utf-8', errors='replace')
                        return str(rawh)
                    except Exception:
                        return ''

            new_messages.append({
                'folder': str(key),
                'mailbox': str(mailbox),
                'uid': int(uid),
                'timestamp': datetime.now(timezone.utc).isoformat(),
                'from': safe_hdr('from'),
                'to': safe_hdr('to'),
                'subject': safe_hdr('subject'),
                'date_header': safe_hdr('date'),
                'body': str(body),
                'attachments': attachments,
                'has_real_attachments': bool(attachments),
            })

        # advance cursor to older
        mb_state['cursor_uid'] = uid_from - 1
        mb_state['updated_at'] = datetime.now(timezone.utc).isoformat()
        save_state(state)

        remaining_hint = max(0, uid_from - 1)
        out = {
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'mailbox': mailbox,
            'batch': {'size': len(uids), 'uid_from': uid_from, 'uid_to': uid_to, 'remaining_hint': remaining_hint},
            'message_count': len(new_messages),
            'messages': new_messages,
        }
        print(json.dumps(out, ensure_ascii=False, indent=2))

    finally:
        try:
            M.logout()
        except Exception:
            pass


if __name__ == '__main__':
    main()
