from typing import List
from .tequila_client import TequilaClient
from .airports import resolve_origins
from datetime import timedelta
import json
from pathlib import Path

LIE_FLAT_AIRCRAFT = {"A330", "A350", "A380", "B787", "B777", "B747"}


def is_lie_flat(match: dict) -> bool:
    # Heuristic: check if any segment aircraft matches common widebodies
    for route in match.get("route", []):
        ac = route.get("aircraft") or ""
        for prefix in LIE_FLAT_AIRCRAFT:
            if ac.upper().startswith(prefix):
                return True
    return False


def total_duration_hours(match: dict) -> float:
    dur = match.get("duration", {}).get("total")
    if not dur:
        return 0.0
    return dur / 3600.0


def filter_matches(matches: List[dict], min_duration_hours: int = 8, require_lie_flat: bool | None = None, max_stopovers: int | None = None) -> List[dict]:
    out = []
    for m in matches:
        if total_duration_hours(m) < min_duration_hours:
            continue
        if require_lie_flat is True and not is_lie_flat(m):
            continue
        if max_stopovers is not None:
            # compute stops per direction using 'return' flag in route entries when available
            route = m.get("route", [])
            outbound = [r for r in route if r.get("return") in (0, None)]
            inbound = [r for r in route if r.get("return") == 1]
            stops_out = max(0, len(outbound) - 1)
            stops_in = max(0, len(inbound) - 1)
            stops = max(stops_out, stops_in)
            if stops > max_stopovers:
                continue
        out.append(m)
    return out


def run_search(cfg, once: bool = True):
    api_key = cfg.get("tequila", "api_key", fallback="").strip()
    if not api_key:
        # fallback: support alternative backends (localfile) so user can test without an API key
        backend = cfg.get("search", "backend", fallback="localfile").strip().lower()
        if backend == "localfile":
            path = cfg.get("search", "localfile_path", fallback="data/sample_matches.json")
            p = Path(path)
            if not p.exists():
                raise RuntimeError(f"Localfile backend enabled but {p} not found")
            data = json.loads(p.read_text(encoding="utf-8"))
            return data
        raise RuntimeError("TEQUILA API key not set in config.ini and no fallback backend available")

    raw_origins = cfg.get("search", "origins", fallback=None)
    fly_from = resolve_origins(raw_origins)
    date_from = cfg.get("search", "date_from")
    date_to = cfg.get("search", "date_to")
    currency = cfg.get("search", "currency", fallback="EUR")
    price_to = cfg.getint("search", "max_price", fallback=1800)
    limit = cfg.getint("search", "limit", fallback=20)
    min_dur = cfg.getint("search", "min_duration_hours", fallback=8)
    cabin = cfg.get("search", "cabin_class", fallback=None)
    max_stopovers = cfg.getint("search", "max_stopovers", fallback=None)

    client = TequilaClient(api_key)
    matches = client.search(fly_from=fly_from, date_from=date_from, date_to=date_to, currency=currency, price_to=price_to, limit=limit, cabin=cabin, max_stopovers=max_stopovers)
    filtered = filter_matches(matches, min_duration_hours=min_dur, max_stopovers=max_stopovers)
    return filtered
