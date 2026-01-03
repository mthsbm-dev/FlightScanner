import requests
import smtplib
import ssl
from email.message import EmailMessage
from typing import Tuple


def send_telegram(bot_token: str, chat_id: str, message: str, parse_mode: str = "HTML") -> bool:
    if not bot_token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": parse_mode,
        "disable_web_page_preview": True,
    }
    resp = requests.post(url, json=payload, timeout=10)
    return resp.status_code == 200


def send_email(smtp_host: str, smtp_port: int, username: str, password: str, sender: str, recipient: str, subject: str, body: str) -> bool:
    if not smtp_host or not recipient:
        return False
    msg = EmailMessage()
    msg["From"] = sender or username
    msg["To"] = recipient
    msg["Subject"] = subject
    msg.set_content(body)

    try:
        if smtp_port == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(smtp_host, smtp_port, context=context, timeout=20) as server:
                if username and password:
                    server.login(username, password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as server:
                server.starttls()
                if username and password:
                    server.login(username, password)
                server.send_message(msg)
        return True
    except Exception:
        return False


def notify(cfg, subject: str, body: str) -> Tuple[bool, bool]:
    """Send notifications according to config. Returns (telegram_ok, email_ok)."""
    telegram_ok = False
    email_ok = False

    bot = cfg.get("notifications", "telegram_bot_token", fallback="").strip()
    chat = cfg.get("notifications", "telegram_chat_id", fallback="").strip()
    if bot and chat:
        telegram_ok = send_telegram(bot, chat, body)

    smtp_host = cfg.get("smtp", "host", fallback="").strip()
    if smtp_host:
        smtp_port = cfg.getint("smtp", "port", fallback=587)
        username = cfg.get("smtp", "username", fallback="").strip()
        password = cfg.get("smtp", "password", fallback="").strip()
        sender = cfg.get("smtp", "sender", fallback=username).strip()
        recipient = cfg.get("smtp", "recipient", fallback="").strip()
        email_ok = send_email(smtp_host, smtp_port, username, password, sender, recipient, subject, body)

    return telegram_ok, email_ok
