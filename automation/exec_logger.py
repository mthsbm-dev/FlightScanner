#!/usr/bin/env python3
"""Parse OpenClaw session files and log exec/code calls to MongoDB."""
import json
import os
from pathlib import Path
from datetime import datetime, timezone
from pymongo import MongoClient
import sys

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "openclaw"
COLLECTION_NAME = "exec_calls"

SESSIONS_DIR = Path.home() / ".openclaw" / "agents" / "main" / "sessions"
STATE_FILE = Path.home() / ".openclaw" / "workspace" / "memory" / "exec_parser_state.json"

def get_mongo():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    col = db[COLLECTION_NAME]
    col.create_index("timestamp")
    col.create_index("tool_name")
    col.create_index("session_file")
    col.create_index("success")
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

def parse_exec_call(msg, session_filename):
    """Extract exec/code call info from a message entry."""
    if msg.get("type") != "message":
        return None
    
    message_data = msg.get("message", {})
    if message_data.get("role") != "assistant":
        return None
    
    # Look for tool calls
    content = message_data.get("content", [])
    if not isinstance(content, list):
        return None
    
    for item in content:
        if item.get("type") != "toolCall":
            continue
        
        tool_name = item.get("name")
        tool_id = item.get("id")
        arguments = item.get("arguments", {})
        
        # Skip if not an exec-like call
        if tool_name not in ["exec", "process"]:
            continue
        
        # Try to extract command from arguments
        cmd = arguments.get("command", "") if isinstance(arguments, dict) else ""
        
        return {
            "_id": f"{session_filename}:{tool_id}" if tool_id else None,
            "timestamp": datetime.fromtimestamp(message_data.get("timestamp", 0) / 1000, tz=timezone.utc),
            "tool_name": tool_name,
            "tool_id": tool_id,
            "command": cmd[:500] if cmd else None,  # Truncate long commands
            "session_file": session_filename,
        }
    
    return None

def process_file(filepath, state, col):
    """Process a single session file."""
    file_state = state["files"].get(filepath.name, {"last_line": 0})
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
                exec_data = parse_exec_call(msg, filepath.name)
                if exec_data:
                    entries.append(exec_data)
            except json.JSONDecodeError:
                continue
    
    # Save progress
    if current_line > last_line:
        file_state["last_line"] = current_line
        state["files"][filepath.name] = file_state
        save_state(state)
    
    # Insert to MongoDB (ignore duplicates)
    if entries:
        try:
            result = col.insert_many(entries, ordered=False)
            print(f"Logged {len(result.inserted_ids)} exec calls from {filepath.name}")
        except Exception as e:
            if "duplicate" in str(e).lower():
                print(f"No new exec calls in {filepath.name}")
            else:
                print(f"Error in {filepath.name}: {e}")
    else:
        print(f"No new exec calls in {filepath.name}")
    
    return len(entries)

def main():
    state = load_state()
    col = get_mongo()
    
    total_new = 0
    
    # Process all session files
    for filepath in get_session_files():
        try:
            new_count = process_file(filepath, state, col)
            total_new += new_count
        except Exception as e:
            print(f"Error processing {filepath}: {e}")
    
    print(f"Done. Total new exec calls: {total_new}")

if __name__ == "__main__":
    main()
