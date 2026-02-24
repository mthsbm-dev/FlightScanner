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


# Search progress state (for incremental/rresumable searches)
SEARCH_STATE_FILE = Path(".search_state.json")


def load_search_state() -> dict:
    """Load search progress state."""
    if not SEARCH_STATE_FILE.exists():
        return {"completed_origins": [], "results": []}
    try:
        return json.loads(SEARCH_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"completed_origins": [], "results": []}


def save_search_state(state: dict) -> None:
    """Save search progress state."""
    SEARCH_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def clear_search_state() -> None:
    """Clear search progress state."""
    if SEARCH_STATE_FILE.exists():
        SEARCH_STATE_FILE.unlink()
