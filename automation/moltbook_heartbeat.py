#!/usr/bin/env python3
"""Moltbook heartbeat - check notifications, DMs, and activity."""
import os
import json
import urllib.request
from datetime import datetime, timezone
from pymongo import MongoClient
from bson import ObjectId

MONGO_URI = "mongodb://localhost:27017"
DB_NAME = "openclaw"
COLLECTION_NAME = "moltbook_checks"

API_KEY = os.environ.get("MOLTBOOK_API_KEY", "moltbook_sk_GRKyWHOLRtBirfKGOcEhJmgPmQ1meYup")
API_BASE = "https://www.moltbook.com/api/v1"

def get_mongo():
    client = MongoClient(MONGO_URI)
    db = client[DB_NAME]
    col = db[COLLECTION_NAME]
    col.create_index("timestamp")
    return col

def call_api(endpoint):
    """Make API call to Moltbook."""
    url = f"{API_BASE}/{endpoint}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {API_KEY}")
    
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return json.loads(response.read().decode())
    except Exception as e:
        print(f"API error: {e}")
        return None

def main():
    col = get_mongo()
    
    # Get /home - gives everything at once
    home_data = call_api("home")
    
    if not home_data:
        print("Failed to fetch Moltbook data")
        return
    
    account = home_data.get("your_account", {})
    dms = home_data.get("your_direct_messages", {})
    activity = home_data.get("activity_on_your_posts", [])
    following = home_data.get("posts_from_accounts_you_follow", {})
    
    entry = {
        "_id": str(ObjectId()),
        "timestamp": datetime.now(timezone.utc),
        "account_name": account.get("name"),
        "karma": account.get("karma"),
        "unread_notifications": account.get("unread_notification_count"),
        "dm_pending": dms.get("pending_request_count"),
        "dm_unread": dms.get("unread_message_count"),
        "activity_count": len(activity),
        "following_count": following.get("total_following"),
        "feed_posts": len(following.get("posts", [])),
    }
    
    col.insert_one(entry)
    
    # Print summary
    print(f"Moltbook: {entry['account_name']} | Karma: {entry['karma']} | Notifs: {entry['unread_notifications']} | DMs: {entry['dm_unread']} | Activity: {entry['activity_count']}")
    
    # Check if there's anything that needs attention
    alerts = []
    if entry['unread_notifications'] != "0":
        alerts.append(f"{entry['unread_notifications']} notifications")
    if entry['dm_unread'] not in ["0", "00", ""]:
        alerts.append(f"{entry['dm_unread']} DMs")
    if entry['activity_count'] > 0:
        alerts.append(f"{entry['activity_count']} post reactions")
    
    if alerts:
        print(f"ALERT: {', '.join(alerts)}")
    else:
        print("All quiet")

if __name__ == "__main__":
    main()
