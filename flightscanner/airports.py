"""Utilities for resolving origin airports (Europe defaults and helpers)."""
from typing import List

# Opinionated list of European hubs (IATA codes). Add/remove as desired.
DEFAULT_EUROPEAN_HUBS = [
    "FRA", "MUC", "CDG", "AMS", "MAD", "LIS", "BER"
]


def resolve_origins(origins_value: str | None) -> List[str]:
    """Resolve origins config value into a list of IATA codes.

    If the value contains the token 'EUROPE' (case-insensitive), returns
    the opinionated DEFAULT_EUROPEAN_HUBS. Otherwise splits on commas.
    """
    if not origins_value:
        return DEFAULT_EUROPEAN_HUBS.copy()

    val = origins_value.strip()
    if not val:
        return DEFAULT_EUROPEAN_HUBS.copy()

    tokens = [t.strip().upper() for t in val.split(",") if t.strip()]
    if any(t in ("EUROPE", "ALL_EU", "DEFAULT") for t in tokens):
        return DEFAULT_EUROPEAN_HUBS.copy()

    return tokens
