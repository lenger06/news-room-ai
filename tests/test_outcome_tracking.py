"""
Unit tests for Phase 7.1 (SELF_IMPROVEMENT_ROADMAP.md) — structured production
outcome logging (tools/agent_outcomes.py), the read-only aggregation report
built on top of it (tools/outcome_report.py), and the EP wiring that records
one outcome per process_message() call.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import tools.agent_outcomes as agent_outcomes
import tools.outcome_report as outcome_report
import agents.executive_producer.agent as ep_module


def _patch_log(tmp_path, monkeypatch):
    monkeypatch.setattr(agent_outcomes, "_LOG_PATH", tmp_path / "agent_outcomes.json")


# ── tools/agent_outcomes.py ─────────────────────────────────────────────────

def test_record_and_load_recent_round_trip(tmp_path, monkeypatch):
    _patch_log(tmp_path, monkeypatch)

    agent_outcomes.record(
        workflow="BROADCAST_VIDEO", show_slug="evening-news", desk="foreign",
        anchor_name="Erik Sinclair", topic="Test topic", keywords=["test"],
        fact_check_verdict="CLEAR TO PUBLISH", fact_check_attempts=1,
        compliance_verdict="CLEAR TO PUBLISH", published=True, succeeded=True,
        duration_seconds=123.4, run_id="run1",
    )

    recent = agent_outcomes.load_recent(days=7.0)
    assert len(recent) == 1
    assert recent[0]["workflow"] == "BROADCAST_VIDEO"
    assert recent[0]["anchor_name"] == "Erik Sinclair"
    assert recent[0]["published"] is True
    assert recent[0]["duration_seconds"] == 123.4


def test_load_recent_excludes_entries_outside_window(tmp_path, monkeypatch):
    _patch_log(tmp_path, monkeypatch)
    from datetime import datetime, timezone

    old_entry = {
        "workflow": "ARTICLE", "show_slug": "", "desk": "national", "anchor_name": "",
        "topic": "Old story", "keywords": [], "fact_check_verdict": "", "fact_check_attempts": 0,
        "compliance_verdict": "", "published": False, "succeeded": True, "duration_seconds": 1.0,
        "run_id": "", "ts": "", "ts_unix": datetime.now(timezone.utc).timestamp() - (10 * 86400),
    }
    agent_outcomes._save([old_entry])

    agent_outcomes.record(
        workflow="ARTICLE", show_slug="", desk="national", anchor_name="",
        topic="Recent story", keywords=[], fact_check_verdict="", fact_check_attempts=0,
        compliance_verdict="", published=False, succeeded=True, duration_seconds=1.0,
    )

    recent = agent_outcomes.load_recent(days=7.0)
    assert len(recent) == 1
    assert recent[0]["topic"] == "Recent story"


# ── tools/outcome_report.py ─────────────────────────────────────────────────

def test_generate_report_empty_window(tmp_path, monkeypatch):
    _patch_log(tmp_path, monkeypatch)
    report = outcome_report.generate_report(days=7.0)
    assert "No production outcomes recorded" in report


def test_generate_report_computes_hold_rates_and_grouping(tmp_path, monkeypatch):
    _patch_log(tmp_path, monkeypatch)

    # 3 health_science productions, 2 with a fact-check hold — 66.7% hold rate for that desk.
    for i in range(2):
        agent_outcomes.record(
            workflow="BROADCAST_VIDEO", show_slug="morning-report", desk="health_science",
            anchor_name="Darlene Smith", topic=f"Health story {i}", keywords=["health"],
            fact_check_verdict="HOLD FOR CORRECTIONS", fact_check_attempts=2,
            compliance_verdict="CLEAR TO PUBLISH", published=False, succeeded=False,
            duration_seconds=100.0,
        )
    agent_outcomes.record(
        workflow="BROADCAST_VIDEO", show_slug="morning-report", desk="health_science",
        anchor_name="Darlene Smith", topic="Health story clean", keywords=["health"],
        fact_check_verdict="CLEAR TO PUBLISH", fact_check_attempts=1,
        compliance_verdict="CLEAR TO PUBLISH", published=True, succeeded=True,
        duration_seconds=200.0,
    )
    # 1 business production, no holds — 0% hold rate.
    agent_outcomes.record(
        workflow="BROADCAST_VIDEO", show_slug="morning-report", desk="business",
        anchor_name="Lars Whitfield", topic="Business story", keywords=["business"],
        fact_check_verdict="CLEAR TO PUBLISH", fact_check_attempts=1,
        compliance_verdict="CLEAR TO PUBLISH", published=True, succeeded=True,
        duration_seconds=150.0,
    )

    report = outcome_report.generate_report(days=7.0)

    assert "4 production(s)" in report
    assert "Published: 2/4 (50.0%)" in report
    assert "Fact-check hold rate: 2/4 (50.0%)" in report
    # health_science: 2/3 holds = 66.7%
    assert "health_science: 3 production(s)" in report
    assert "fact-check hold rate 66.7%" in report
    # business: 0/1 holds = 0.0%
    assert "business: 1 production(s)" in report
    assert "fact-check hold rate 0.0%" in report
    # grouped by anchor too
    assert "Darlene Smith: 3 production(s)" in report
    assert "Lars Whitfield: 1 production(s)" in report


# ── EP wiring: process_message records one outcome per run ─────────────────

async def test_process_message_records_outcome(tmp_path, monkeypatch):
    _patch_log(tmp_path, monkeypatch)

    ep = ep_module.Agent()

    fake_final_state = {
        "workflow": "BROADCAST_VIDEO",
        "show_slug": "evening-news",
        "desk": "foreign",
        "anchor_name": "Erik Sinclair",
        "topic": "Test topic",
        "keywords": ["test", "topic"],
        "fact_check_verdict": "CLEAR TO PUBLISH",
        "fact_check_attempts": 1,
        "compliance_verdict": "CLEAR TO PUBLISH",
        "outputs": {"publisher": "Published to YouTube successfully.\nURL: https://youtube.com/watch?v=abc123"},
        "researcher_failed": False,
        "anchor_failed": False,
        "needs_human_review": False,
        "final_summary": "**Production Complete**",
        "run_id": "run123",
    }

    async def fake_ainvoke(initial_state):
        return fake_final_state

    monkeypatch.setattr(ep.workflow, "ainvoke", fake_ainvoke)

    captured = {}

    def fake_record(**kwargs):
        captured.update(kwargs)

    import tools.agent_outcomes as ao_module
    monkeypatch.setattr(ao_module, "record", fake_record)

    result = await ep.process_message("Produce a broadcast video on a test topic")

    assert result["success"] is True
    assert captured["workflow"] == "BROADCAST_VIDEO"
    assert captured["anchor_name"] == "Erik Sinclair"
    assert captured["published"] is True
    assert captured["succeeded"] is True
    assert captured["fact_check_attempts"] == 1
    assert captured["duration_seconds"] >= 0


async def test_process_message_records_unpublished_outcome_on_halt(tmp_path, monkeypatch):
    _patch_log(tmp_path, monkeypatch)

    ep = ep_module.Agent()

    fake_final_state = {
        "workflow": "BROADCAST_VIDEO",
        "show_slug": "evening-news",
        "desk": "foreign",
        "anchor_name": "Erik Sinclair",
        "topic": "Test topic",
        "keywords": ["test"],
        "fact_check_verdict": "HOLD FOR CORRECTIONS",
        "fact_check_attempts": 2,
        "compliance_verdict": "",
        "outputs": {},
        "researcher_failed": False,
        "anchor_failed": False,
        "needs_human_review": True,
        "final_summary": "**Halted**",
        "run_id": "run124",
    }

    async def fake_ainvoke(initial_state):
        return fake_final_state

    monkeypatch.setattr(ep.workflow, "ainvoke", fake_ainvoke)

    captured = {}
    import tools.agent_outcomes as ao_module
    monkeypatch.setattr(ao_module, "record", lambda **kwargs: captured.update(kwargs))

    result = await ep.process_message("Produce a broadcast video on a test topic")

    assert result["success"] is False
    assert captured["published"] is False
    assert captured["succeeded"] is False


async def test_outcome_recording_failure_does_not_break_process_message(tmp_path, monkeypatch):
    """A broken outcome-log write must never take down the actual production response."""
    _patch_log(tmp_path, monkeypatch)

    ep = ep_module.Agent()

    fake_final_state = {
        "workflow": "ARTICLE", "show_slug": "", "desk": "national", "anchor_name": "",
        "topic": "Test topic", "keywords": [], "fact_check_verdict": "CLEAR TO PUBLISH",
        "fact_check_attempts": 1, "compliance_verdict": "", "outputs": {},
        "researcher_failed": False, "anchor_failed": False, "needs_human_review": False,
        "final_summary": "**Production Complete**", "run_id": "run125",
    }

    async def fake_ainvoke(initial_state):
        return fake_final_state

    monkeypatch.setattr(ep.workflow, "ainvoke", fake_ainvoke)

    import tools.agent_outcomes as ao_module

    def _raise(**kwargs):
        raise RuntimeError("simulated disk failure")

    monkeypatch.setattr(ao_module, "record", _raise)

    result = await ep.process_message("Write an article about a test topic")
    assert result["success"] is True
