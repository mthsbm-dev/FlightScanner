import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Set
import hashlib
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Match ID generation (backend-agnostic)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Abstract storage backend
# ---------------------------------------------------------------------------

class StorageBackend(ABC):
    @abstractmethod
    def load_sent(self) -> Set[str]:
        ...

    @abstractmethod
    def save_sent(self, ids: Set[str]) -> None:
        ...

    @abstractmethod
    def load_search_state(self) -> dict:
        ...

    @abstractmethod
    def save_search_state(self, state: dict) -> None:
        ...

    @abstractmethod
    def clear_search_state(self) -> None:
        ...

    @abstractmethod
    def save_flight_results(self, matches: list) -> None:
        ...


# ---------------------------------------------------------------------------
# JSON file backend (default, backward-compatible)
# ---------------------------------------------------------------------------

STORAGE_FILE = Path(".sent_matches.json")
SEARCH_STATE_FILE = Path(".search_state.json")


class JsonFileStorage(StorageBackend):
    def __init__(self, sent_file: Path = STORAGE_FILE, state_file: Path = SEARCH_STATE_FILE):
        self._sent_file = sent_file
        self._state_file = state_file

    def load_sent(self) -> Set[str]:
        if not self._sent_file.exists():
            return set()
        try:
            data = json.loads(self._sent_file.read_text(encoding="utf-8"))
            return set(data)
        except Exception:
            return set()

    def save_sent(self, ids: Set[str]) -> None:
        self._sent_file.write_text(json.dumps(list(ids), ensure_ascii=False), encoding="utf-8")

    def load_search_state(self) -> dict:
        if not self._state_file.exists():
            return {"completed_origins": [], "results": []}
        try:
            return json.loads(self._state_file.read_text(encoding="utf-8"))
        except Exception:
            return {"completed_origins": [], "results": []}

    def save_search_state(self, state: dict) -> None:
        self._state_file.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def clear_search_state(self) -> None:
        if self._state_file.exists():
            self._state_file.unlink()

    def save_flight_results(self, matches: list) -> None:
        # JSON backend: no-op (results are logged to flights.log by run.py)
        pass


# ---------------------------------------------------------------------------
# MongoDB backend
# ---------------------------------------------------------------------------

class MongoStorage(StorageBackend):
    def __init__(self, host: str = "localhost", port: int = 27017,
                 db_name: str = "flightscanner", username: str = "",
                 password: str = "", auth_source: str = "admin"):
        try:
            from pymongo import MongoClient
        except ImportError:
            raise ImportError(
                "pymongo is required for MongoDB storage. "
                "Install it with: pip install pymongo"
            )

        kwargs = {"host": host, "port": port}
        if username and password:
            kwargs["username"] = username
            kwargs["password"] = password
            kwargs["authSource"] = auth_source

        self._client = MongoClient(**kwargs)
        self._db = self._client[db_name]
        self._sent = self._db["sent_matches"]
        self._state = self._db["search_state"]
        self._results = self._db["flight_results"]

    # -- sent matches -------------------------------------------------------

    def load_sent(self) -> Set[str]:
        try:
            return {doc["_id"] for doc in self._sent.find({}, {"_id": 1})}
        except Exception:
            return set()

    def save_sent(self, ids: Set[str]) -> None:
        # Sync the collection to match the given set.
        existing = self.load_sent()
        to_add = ids - existing
        to_remove = existing - ids
        if to_add:
            self._sent.insert_many([{"_id": mid} for mid in to_add])
        if to_remove:
            self._sent.delete_many({"_id": {"$in": list(to_remove)}})

    # -- search state -------------------------------------------------------

    _STATE_DOC_ID = "current"

    def load_search_state(self) -> dict:
        try:
            doc = self._state.find_one({"_id": self._STATE_DOC_ID})
            if doc is None:
                return {"completed_origins": [], "results": []}
            doc.pop("_id", None)
            return doc
        except Exception:
            return {"completed_origins": [], "results": []}

    def save_search_state(self, state: dict) -> None:
        doc = {**state, "_id": self._STATE_DOC_ID}
        self._state.replace_one({"_id": self._STATE_DOC_ID}, doc, upsert=True)

    def clear_search_state(self) -> None:
        self._state.delete_one({"_id": self._STATE_DOC_ID})

    # -- flight results -----------------------------------------------------

    def save_flight_results(self, matches: list) -> None:
        if not matches:
            return
        timestamp = datetime.now(timezone.utc).isoformat()
        docs = []
        for m in matches:
            doc = {**m, "stored_at": timestamp}
            # Use match ID as _id for natural dedup
            doc["_id"] = get_match_id(m)
            # Flatten nested dicts that might cause issues with dots in keys
            docs.append(doc)
        from pymongo import UpdateOne
        ops = [
            UpdateOne({"_id": d["_id"]}, {"$set": d}, upsert=True)
            for d in docs
        ]
        self._results.bulk_write(ops)


# ---------------------------------------------------------------------------
# Global backend instance + convenience functions
# ---------------------------------------------------------------------------

_backend: StorageBackend = JsonFileStorage()


def init_storage(cfg) -> StorageBackend:
    """Initialise the storage backend from a ConfigParser config.

    Call this once at startup.  If the [storage] section is missing or
    ``backend`` is ``json`` (or absent), the default JSON-file backend is
    used.  Set ``backend = mongodb`` to use MongoDB.
    """
    global _backend

    backend_type = cfg.get("storage", "backend", fallback="json").strip().lower()

    if backend_type == "mongodb":
        _backend = MongoStorage(
            host=cfg.get("storage", "mongo_host", fallback="localhost"),
            port=cfg.getint("storage", "mongo_port", fallback=27017),
            db_name=cfg.get("storage", "mongo_db", fallback="flightscanner"),
            username=cfg.get("storage", "mongo_username", fallback=""),
            password=cfg.get("storage", "mongo_password", fallback=""),
            auth_source=cfg.get("storage", "mongo_auth_source", fallback="admin"),
        )
    else:
        _backend = JsonFileStorage()

    return _backend


# Module-level convenience functions (delegate to the active backend).
# These keep the existing call-sites working without changes.

def load_sent() -> Set[str]:
    return _backend.load_sent()


def save_sent(ids: Set[str]) -> None:
    _backend.save_sent(ids)


def load_search_state() -> dict:
    return _backend.load_search_state()


def save_search_state(state: dict) -> None:
    _backend.save_search_state(state)


def clear_search_state() -> None:
    _backend.clear_search_state()


def save_flight_results(matches: list) -> None:
    _backend.save_flight_results(matches)
