#!/usr/bin/env python3
"""Log LLM calls to MongoDB."""
from pymongo import MongoClient
from datetime import datetime, timezone
import sys

# MongoDB connection
MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "openclaw"
COLLECTION_NAME = "llm_calls"

def get_collection():
    """Get MongoDB collection."""
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    col = db[COLLECTION_NAME]
    # Create index
    col.create_index("timestamp")
    col.create_index("model")
    return col

def log_llm_call(model: str, prompt: str = None, response: str = None, tokens_in: int = None, tokens_out: int = None):
    """Log an LLM call to MongoDB."""
    col = get_collection()
    entry = {
        "timestamp": datetime.now(timezone.utc),
        "model": model,
        "prompt": prompt[:1000] if prompt else None,  # Truncate
        "prompt_length": len(prompt) if prompt else 0,
        "response_length": len(response) if response else 0,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
    }
    result = col.insert_one(entry)
    return str(result.inserted_id)

def get_recent_calls(limit: int = 10):
    """Get the most recent LLM calls."""
    col = get_collection()
    return list(col.find().sort("timestamp", -1).limit(limit))

def query_logs(model: str = None, since: str = None, limit: int = 100):
    """Query logs by model or since ISO timestamp."""
    col = get_collection()
    query = {}
    if model:
        query["model"] = model
    if since:
        query["timestamp"] = {"$gte": datetime.fromisoformat(since)}
    
    return list(col.find(query).sort("timestamp", -1).limit(limit))

def get_stats():
    """Get summary statistics."""
    col = get_collection()
    pipeline = [
        {"$group": {
            "_id": "$model",
            "total_calls": {"$sum": 1},
            "total_tokens_in": {"$sum": "$tokens_in"},
            "total_tokens_out": {"$sum": "$tokens_out"}
        }}
    ]
    return list(col.aggregate(pipeline))

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  log_llm.py log <model> ['prompt'] [response] [tokens_in] [tokens_out]")
        print("  log_llm.py recent [limit]")
        print("  log_llm.py query [--model <model>] [--since <iso-date>]")
        print("  log_llm.py stats")
        sys.exit(1)
    
    cmd = sys.argv[1]
    
    if cmd == "log":
        model = sys.argv[2]
        prompt = sys.argv[3] if len(sys.argv) > 3 else None
        response = sys.argv[4] if len(sys.argv) > 4 else None
        tokens_in = int(sys.argv[5]) if len(sys.argv) > 5 and sys.argv[5].isdigit() else None
        tokens_out = int(sys.argv[6]) if len(sys.argv) > 6 and sys.argv[6].isdigit() else None
        
        result = log_llm_call(model, prompt, response, tokens_in, tokens_out)
        print(f"Logged: {result}")
    
    elif cmd == "recent":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        for doc in get_recent_calls(limit):
            print(f"{doc['timestamp'].isoformat()} | {doc['model']} | {doc['prompt_length']} chars in | {doc['tokens_in']}/{doc['tokens_out']} tokens")
    
    elif cmd == "query":
        model = None
        since = None
        i = 2
        while i < len(sys.argv):
            if sys.argv[i] == "--model":
                model = sys.argv[i+1]
                i += 2
            elif sys.argv[i] == "--since":
                since = sys.argv[i+1]
                i += 2
            else:
                i += 1
        
        for doc in query_logs(model, since):
            prompt_preview = doc.get('prompt', '')[:80] if doc.get('prompt') else ''
            print(f"{doc['timestamp'].isoformat()} | {doc['model']} | {prompt_preview}...")
    
    elif cmd == "stats":
        print("Calls per model:")
        for doc in get_stats():
            print(f"  {doc['_id']}: {doc['total_calls']} calls, {doc['total_tokens_in']}/{doc['total_tokens_out']} tokens")
    
    else:
        print(f"Unknown command: {cmd}")
        sys.exit(1)
