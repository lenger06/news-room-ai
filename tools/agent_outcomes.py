"""
Per-run production outcome log — the foundation for Phase 7 of
SELF_IMPROVEMENT_ROADMAP.md ("Tier 2" self-improvement). Structured,
queryable records of how each production actually went, instead of only
being readable by opening individual production_logs/*.md files by hand.

Same flat-JSON-log convention as story_history.py, breaking_news_log.py, and
review_queue.py — record() appends, load_recent() reads a rolling window.
tools/outcome_report.py is the read-only aggregation layer on top of this.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_LOG_PATH = Path("./output/agent_outcomes.json")
_MAX_ENTRIES = 500


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
        logger.warning(f"[agent_outcomes] Could not save: {e}")


def record(
    workflow: str,
    show_slug: str,
    desk: str,
    anchor_name: str,
    topic: str,
    keywords: list[str],
    fact_check_verdict: str,
    fact_check_attempts: int,
    compliance_verdict: str,
    published: bool,
    succeeded: bool,
    duration_seconds: float,
    run_id: str = "",
) -> None:
    """Log one production's outcome — the raw material tools/outcome_report.py aggregates."""
    entries = _load()
    now = datetime.now(timezone.utc)
    entries.append({
        "workflow": workflow,
        "show_slug": show_slug,
        "desk": desk,
        "anchor_name": anchor_name,
        "topic": topic,
        "keywords": keywords or [],
        "fact_check_verdict": fact_check_verdict,
        "fact_check_attempts": fact_check_attempts,
        "compliance_verdict": compliance_verdict,
        "published": bool(published),
        "succeeded": bool(succeeded),
        "duration_seconds": round(duration_seconds, 1),
        "run_id": run_id,
        "ts": now.isoformat(),
        "ts_unix": now.timestamp(),
    })
    _save(entries)
    logger.info(
        f"[agent_outcomes] Recorded: {workflow} | {topic[:60]!r} | "
        f"succeeded={succeeded} published={published}"
    )


def load_recent(days: float = 7.0) -> list[dict]:
    """Return outcome records from the last `days` days, oldest first."""
    cutoff = datetime.now(timezone.utc).timestamp() - (days * 86400)
    return [e for e in _load() if e.get("ts_unix", 0) >= cutoff]
