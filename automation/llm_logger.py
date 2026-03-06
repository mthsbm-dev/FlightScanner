#!/usr/bin/env python3
"""Parse OpenClaw session files and log LLM calls to MongoDB."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from pymongo import MongoClient
import sys

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "openclaw"
COLLECTION_NAME = "llm_calls"

SESSIONS_DIR = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
STATE_FILE = Path.home() / ".openclaw" / "workspace" / "memory" / "llm_parser_state.json"

def get_mongo():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    col = db[COLLECTION_NAME]
    col.create_index("timestamp")
    col.create_index("model")
    col.create_index("session_file")
    return col

def load_state():
    """Load last processed state."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"files": {}}

def save_state(state):
    """Save processing state."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def get_session_files():
    """Get all .jsonl session files sorted by modification time."""
    return sorted(SESSIONS_DIR.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)

def parse_message(msg, session_filename):
    """Extract LLM call info from a message entry."""
    if msg.get("type") != "message":
        return None
    
    message_data = msg.get("message", {})
    if message_data.get("role") != "assistant":
        return None
    
    api = message_data.get("api")
    provider = message_data.get("provider")
    model = message_data.get("model")
    usage = message_data.get("usage", {})
    
    if not model:
        return None
    
    # Use message ID + session file as unique _id
    msg_id = msg.get("id")
    
    return {
        "_id": f"{session_filename}:{msg_id}" if msg_id else None,
        "timestamp": datetime.fromtimestamp(message_data.get("timestamp", 0) / 1000, tz=timezone.utc),
        "model": model,
        "provider": provider,
        "api": api,
        "tokens_in": usage.get("input"),
        "tokens_out": usage.get("output"),
        "cache_read": usage.get("cacheRead"),
        "cache_write": usage.get("cacheWrite"),
        "total_tokens": usage.get("totalTokens"),
        "cost": usage.get("cost", {}).get("total") if isinstance(usage.get("cost"), dict) else None,
    }

def process_file(filepath, state, col):
    """Process a single session file."""
    file_state = state["files"].get(filepath.name, {"last_line": 0, "last_ts": None})
    last_line = file_state.get("last_line", 0)
    
    entries = []
    current_line = 0
    
    with open(filepath) as f:
        for line in f:
            current_line += 1
            if current_line <= last_line:
                continue
            
            try:
                msg = json.loads(line)
                llm_data = parse_message(msg, filepath.name)
                if llm_data:
                    entries.append(llm_data)
            except json.JSONDecodeError:
                continue
    
    # Save progress
    if current_line > last_line:
        file_state["last_line"] = current_line
        state["files"][filepath.name] = file_state
        save_state(state)
    
    # Insert to MongoDB
    if entries:
        col.insert_many(entries)
        print(f"Logged {len(entries)} LLM calls from {filepath.name}")
    else:
        print(f"No new LLM calls in {filepath.name}")
    
    return len(entries)

def main():
    state = load_state()
    col = get_mongo()
    
    # Track all seen message IDs to avoid duplicates
    existing_ids = set()
    for doc in col.find({}, {"_id": 1}):
        existing_ids.add(str(doc["_id"]))
    
    total_new = 0
    
    # Process all session files (newest first to catch recent first)
    for filepath in get_session_files():
        try:
            new_count = process_file(filepath, state, col)
            total_new += new_count
        except Exception as e:
            print(f"Error processing {filepath}: {e}")
    
    print(f"Done. Total new LLM calls: {total_new}")

if __name__ == "__main__":
    main()
