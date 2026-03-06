#!/usr/bin/env python3
import re
with open('/home/mathias/.openclaw/workspace/scripts/check_landlord_mail_imap.py', 'r') as f:
    c = f.read()

old = '''            msg = BytesParser(policy=policy.default).parsebytes(raw)
            body = (extract_text(msg) or '').strip()'''

new = '''            msg = BytesParser(policy=policy.default).parsebytes(raw)
            
            # Skip emails from ignored senders
            msg_from = msg.get('from', '') or ''
            skip = False
            for ignored in IGNORED_SENDERS:
                if ignored.lower() in msg_from.lower():
                    skip = True
                    break
            if skip:
                if uid > max_seen:
                    max_seen = uid
                continue
            
            body = (extract_text(msg) or '').strip()'''

if old in c:
    c = c.replace(old, new)
    open('/home/mathias/.openclaw/workspace/scripts/check_landlord_mail_imap.py', 'w').write(c)
    print('OK - filter added')
else:
    print('NOT FOUND')
    # Debug: show what's in that area
    import re
    m = re.search(r'msg = BytesParser.*?body = ', c, re.DOTALL)
    if m:
        print(repr(m.group(0)))