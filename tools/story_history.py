"""
Story history — universal log of all news productions (all shows, all workflows).
Used by the Executive Producer to prevent duplicate coverage before committing
to a full research/write/video pipeline.

Separate from breaking_news_log.py (which only covers breaking-news auto-fires).
This log covers everything the EP produces, with a 7-day window.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_PATH = Path("./output/story_history.json")
_MAX_ENTRIES = 500
_DEFAULT_WINDOW_HOURS = 168.0   # 7 days


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
        _LOG_PATH.write_text(
            json.dumps(entries[-_MAX_ENTRIES:], indent=2), encoding="utf-8"
        )
    except Exception as e:
        logger.warning(f"[story_history] Could not save: {e}")


def record(topic: str, keywords: list[str], show_slug: str, workflow: str) -> None:
    """Log a completed production to the story history."""
    entries = _load()
    now = datetime.now(timezone.utc)
    entries.append({
        "topic": topic,
        "keywords": keywords,
        "show_slug": show_slug,
        "workflow": workflow,
        "ts": now.isoformat(),
        "ts_unix": now.timestamp(),
    })
    _save(entries)
    logger.info(f"[story_history] Recorded: {topic[:80]}")


def get_recent(hours: float = _DEFAULT_WINDOW_HOURS) -> list[dict]:
    """Return all entries produced within the last N hours."""
    cutoff = datetime.now(timezone.utc).timestamp() - (hours * 3600)
    return [e for e in _load() if e.get("ts_unix", 0) >= cutoff]


def find_similar(
    keywords: list[str],
    hours: float = _DEFAULT_WINDOW_HOURS,
    min_overlap: int = 2,
) -> list[dict]:
    """Return recent entries sharing at least min_overlap keywords with the given list."""
    kw_set = {k.lower() for k in keywords}
    results = []
    for entry in get_recent(hours):
        e_kw = {k.lower() for k in entry.get("keywords", [])}
        if len(kw_set & e_kw) >= min_overlap:
            results.append(entry)
    return sorted(results, key=lambda e: e.get("ts_unix", 0), reverse=True)


def format_for_llm(entries: list[dict]) -> str:
    """Format story history entries as a readable block for LLM context."""
    if not entries:
        return "None — no similar stories found in the recent production history."
    lines = []
    for e in entries:
        ts = e.get("ts", "")[:16].replace("T", " ")
        kw = ", ".join(e.get("keywords", []))
        lines.append(f"[{ts} UTC] {e.get('topic', '')}  (keywords: {kw})")
    return "\n".join(lines)
