import json
from pathlib import Path
from typing import Set
import hashlib

STORAGE_FILE = Path(".sent_matches.json")


def load_sent() -> Set[str]:
    if not STORAGE_FILE.exists():
        return set()
    try:
        data = json.loads(STORAGE_FILE.read_text(encoding="utf-8"))
        return set(data)
    except Exception:
        return set()


def save_sent(ids: Set[str]) -> None:
    STORAGE_FILE.write_text(json.dumps(list(ids), ensure_ascii=False), encoding="utf-8")


def get_match_id(match: dict) -> str:
    mid = match.get("id")
    if mid:
        return str(mid)
    # fallback: generate hash from key fields
    key = {
        "price": match.get("price"),
        "cityTo": match.get("cityTo"),
        "dTimeUTC": match.get("dTimeUTC"),
    }
    raw = json.dumps(key, sort_keys=True)
    return hashlib.sha1(raw.encode()).hexdigest()
