"""
Needs-human-review queue — records productions halted before publish because
fact-checking or compliance screening failed even after correction retries.

Separate from story_history.py (successful productions) and breaking_news_log.py
(breaking-news dedup) — this is specifically for productions that require a human
look before anything is republished, retried, or manually corrected.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_PATH = Path("./output/needs_review.json")
_MAX_ENTRIES = 200


def _load() -> list[dict]:
    if _LOG_PATH.exists():
        try:
            return json.loads(_LOG_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return []


def _save(entries: list[dict]) -> None:
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        _LOG_PATH.write_text(json.dumps(entries[-_MAX_ENTRIES:], indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[review_queue] Could not save: {e}")


def record(topic: str, reason: str, stage: str, output_dir: str, workflow: str) -> None:
    """Log a production halted for human review before publish."""
    entries = _load()
    now = datetime.now(timezone.utc)
    entries.append({
        "topic": topic,
        "reason": reason,
        "stage": stage,
        "output_dir": output_dir,
        "workflow": workflow,
        "ts": now.isoformat(),
        "ts_unix": now.timestamp(),
        "resolved": False,
    })
    _save(entries)
    logger.warning(f"[review_queue] Needs human review: {topic[:80]} — {reason[:100]}")


def list_pending() -> list[dict]:
    """Return all entries not yet marked resolved."""
    return [e for e in _load() if not e.get("resolved", False)]


def mark_resolved(topic: str) -> bool:
    """Mark the most recent matching entry as resolved. Returns True if one was found."""
    entries = _load()
    for e in reversed(entries):
        if e.get("topic") == topic and not e.get("resolved", False):
            e["resolved"] = True
            _save(entries)
            return True
    return False
