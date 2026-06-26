import json
import os

import config


def append_log(entry: dict) -> None:
    os.makedirs(os.path.dirname(config.LOG_FILE), exist_ok=True)
    with open(config.LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def get_log(limit: int = 100) -> list:
    if not os.path.exists(config.LOG_FILE):
        return []
    with open(config.LOG_FILE) as f:
        lines = [json.loads(line) for line in f if line.strip()]
    return lines[-limit:]


def find_submission(content_id: str) -> dict:
    """Finds the most recent 'classified' entry for a content_id."""
    for entry in reversed(get_log(limit=10_000)):
        if entry.get("content_id") == content_id and entry.get("type", "submission") == "submission":
            return entry
    return None
