"""Unit tests for tools/review_queue.py — the shared human-review log used by both
the Phase 1 fact-check retry exhaustion path and the Phase 2 compliance gate."""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import tools.review_queue as review_queue


def test_record_and_list_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(review_queue, "_LOG_PATH", tmp_path / "needs_review.json")

    review_queue.record(
        topic="Iran shipping dispute",
        reason="Fact-checker still returned HOLD FOR CORRECTIONS after 2 attempts",
        stage="fact_check",
        output_dir="./output/evening-news/20260725_120000",
        workflow="BROADCAST_VIDEO",
    )

    pending = review_queue.list_pending()
    assert len(pending) == 1
    entry = pending[0]
    assert entry["topic"] == "Iran shipping dispute"
    assert entry["stage"] == "fact_check"
    assert entry["resolved"] is False


def test_mark_resolved_removes_from_pending(tmp_path, monkeypatch):
    monkeypatch.setattr(review_queue, "_LOG_PATH", tmp_path / "needs_review.json")

    review_queue.record(
        topic="Some story", reason="policy concern", stage="compliance",
        output_dir="./output/x", workflow="BROADCAST_VIDEO",
    )
    assert len(review_queue.list_pending()) == 1

    found = review_queue.mark_resolved("Some story")
    assert found is True
    assert review_queue.list_pending() == []


def test_mark_resolved_missing_topic_returns_false(tmp_path, monkeypatch):
    monkeypatch.setattr(review_queue, "_LOG_PATH", tmp_path / "needs_review.json")
    assert review_queue.mark_resolved("Nonexistent topic") is False


def test_list_pending_empty_when_no_log(tmp_path, monkeypatch):
    monkeypatch.setattr(review_queue, "_LOG_PATH", tmp_path / "does_not_exist.json")
    assert review_queue.list_pending() == []
