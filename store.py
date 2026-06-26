"""In-memory store of submissions, keyed by content_id.

Backs status lookups/updates for the appeals workflow. The audit log
(auditor.py) remains the canonical append-only record; this store is a
convenience index over it for O(1) lookups during the process lifetime.
"""

_submissions = {}


def save(content_id: str, record: dict) -> None:
    _submissions[content_id] = record


def get(content_id: str) -> dict:
    return _submissions.get(content_id)


def set_status(content_id: str, status: str) -> bool:
    if content_id not in _submissions:
        return False
    _submissions[content_id]["status"] = status
    return True


def all_records() -> list:
    return list(_submissions.values())
