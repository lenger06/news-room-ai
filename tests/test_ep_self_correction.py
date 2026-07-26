"""
Unit tests for the Phase 1 self-correction loop and Phase 2 compliance gate in
agents/executive_producer/agent.py. These exercise _route_after_step (pure/sync)
and _execute_step_node (async, with agent_registry mocked) directly — no live
LLM or API calls, so they're safe and free to run repeatedly.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest

from agents.executive_producer.agent import Agent as EPAgent


def _base_state(**overrides) -> dict:
    state = {
        "steps": ["researcher", "writer", "fact_checker", "editor", "producer"],
        "current_step_index": 4,   # points past "editor" (index 3) at "producer"
        "last_step_name": "",
        "researcher_failed": False,
        "anchor_failed": False,
        "fact_check_verdict": "",
        "fact_check_attempts": 0,
        "compliance_verdict": "",
        "needs_human_review": False,
        "human_review_reason": "",
        "review_stage": "",
    }
    state.update(overrides)
    return state


@pytest.fixture
def ep():
    return EPAgent()


def test_route_next_step_normal(ep):
    state = _base_state(current_step_index=1)
    assert ep._route_after_step(state) == "next_step"


def test_route_done_at_end(ep):
    state = _base_state(current_step_index=5)  # == len(steps)
    assert ep._route_after_step(state) == "done"


def test_route_done_on_researcher_failed(ep):
    state = _base_state(current_step_index=1, researcher_failed=True)
    assert ep._route_after_step(state) == "done"


def test_route_done_on_anchor_failed(ep):
    state = _base_state(current_step_index=1, anchor_failed=True)
    assert ep._route_after_step(state) == "done"


def test_fact_check_retry_splices_reverification(ep):
    """After editor patches a HOLD_FOR_CORRECTIONS article, with retries remaining,
    the EP should splice ['fact_checker', 'editor'] back into the step list rather
    than proceeding straight to the next step."""
    state = _base_state(
        last_step_name="editor",
        fact_check_verdict="HOLD FOR CORRECTIONS",
        fact_check_attempts=1,
    )
    original_len = len(state["steps"])
    result = ep._route_after_step(state)

    assert result == "next_step"
    assert state["steps"][4:6] == ["fact_checker", "editor"]
    assert len(state["steps"]) == original_len + 2
    assert state["needs_human_review"] is False


def test_fact_check_aborts_after_max_attempts(ep):
    """Once MAX_FACT_CHECK_ATTEMPTS is reached and the verdict is still failing,
    the EP must halt before script/video/publish steps and flag for human review —
    not publish an uncorrected story."""
    state = _base_state(
        last_step_name="editor",
        fact_check_verdict="HOLD FOR CORRECTIONS",
        fact_check_attempts=EPAgent.MAX_FACT_CHECK_ATTEMPTS,
    )
    result = ep._route_after_step(state)

    assert result == "done"
    assert state["needs_human_review"] is True
    assert state["review_stage"] == "fact_check"
    assert "HOLD FOR CORRECTIONS" in state["human_review_reason"]
    # Step list must NOT have been extended further — no more retries once exhausted.
    assert state["steps"] == _base_state()["steps"]


def test_fact_check_clear_verdict_does_not_retry(ep):
    """A CLEAR TO PUBLISH (or PUBLISH WITH NOTES) verdict must never trigger the
    retry/abort branch — editor running after a clean fact-check is normal, not
    a correction cycle."""
    state = _base_state(last_step_name="editor", fact_check_verdict="CLEAR TO PUBLISH")
    original_steps = list(state["steps"])
    result = ep._route_after_step(state)

    assert result == "next_step"
    assert state["steps"] == original_steps
    assert state["needs_human_review"] is False


@pytest.mark.parametrize("verdict", ["ERROR", "UNKNOWN"])
def test_fact_check_error_or_unknown_treated_as_failing(ep, verdict):
    """Fail closed: an errored or unparseable fact-check verdict must be treated
    the same as an explicit HOLD, not silently waved through to publish."""
    state = _base_state(
        last_step_name="editor",
        fact_check_verdict=verdict,
        fact_check_attempts=1,
    )
    result = ep._route_after_step(state)
    assert result == "next_step"
    assert state["steps"][4:6] == ["fact_checker", "editor"]


def test_compliance_hold_aborts_immediately_no_retry(ep):
    """Compliance failures get no retry loop (no agent can auto-fix a policy
    violation) — a single HOLD verdict must halt immediately before publish."""
    state = _base_state(
        steps=["anchor", "video_editor", "compliance_checker", "producer", "publisher"],
        current_step_index=3,
        last_step_name="compliance_checker",
        compliance_verdict="HOLD FOR REVIEW",
    )
    result = ep._route_after_step(state)

    assert result == "done"
    assert state["needs_human_review"] is True
    assert state["review_stage"] == "compliance"
    assert "HOLD FOR REVIEW" in state["human_review_reason"]


def test_compliance_clear_verdict_proceeds(ep):
    state = _base_state(
        steps=["anchor", "video_editor", "compliance_checker", "producer", "publisher"],
        current_step_index=3,
        last_step_name="compliance_checker",
        compliance_verdict="CLEAR TO PUBLISH",
    )
    result = ep._route_after_step(state)
    assert result == "next_step"
    assert state["needs_human_review"] is False


class _FakeAgent:
    """Stand-in for a registry-loaded agent — returns a canned response without
    making any LLM/API calls."""
    def __init__(self, response_dict):
        self._response = response_dict

    async def process_message(self, message, context=None):
        return self._response


async def test_execute_step_captures_fact_check_verdict(ep, monkeypatch):
    import agents.executive_producer.agent as ep_module

    async def fake_get_agent(name):
        assert name == "fact_checker"
        return _FakeAgent({
            "success": True,
            "response": "## VERDICT\nHOLD FOR CORRECTIONS",
            "verdict": "HOLD FOR CORRECTIONS",
            "agent": "fact_checker",
        })

    monkeypatch.setattr(ep_module.agent_registry, "get_agent", fake_get_agent)

    state = _base_state(
        steps=["fact_checker"],
        current_step_index=0,
        request="test request",
        topic="test topic",
        outputs={},
    )
    result = await ep._execute_step_node(state)

    assert result["fact_check_verdict"] == "HOLD FOR CORRECTIONS"
    assert result["fact_check_attempts"] == 1
    assert result["last_step_name"] == "fact_checker"
    assert result["current_step_index"] == 1


async def test_execute_step_captures_compliance_verdict(ep, monkeypatch):
    import agents.executive_producer.agent as ep_module

    async def fake_get_agent(name):
        assert name == "compliance_checker"
        return _FakeAgent({
            "success": True,
            "response": "## RECOMMENDATION\nHOLD FOR REVIEW",
            "verdict": "HOLD FOR REVIEW",
            "agent": "compliance_checker",
        })

    monkeypatch.setattr(ep_module.agent_registry, "get_agent", fake_get_agent)

    state = _base_state(
        steps=["compliance_checker"],
        current_step_index=0,
        request="test request",
        topic="test topic",
        outputs={},
    )
    result = await ep._execute_step_node(state)

    assert result["compliance_verdict"] == "HOLD FOR REVIEW"
    assert result["last_step_name"] == "compliance_checker"


async def _run_process_message_with_fake_final_state(ep, state_overrides, monkeypatch):
    async def fake_ainvoke(initial_state):
        state = dict(initial_state)
        state.update(state_overrides)
        return state

    monkeypatch.setattr(ep.workflow, "ainvoke", fake_ainvoke)
    return await ep.process_message("test request")


async def test_process_message_reports_failure_on_needs_human_review(ep, monkeypatch):
    """A halted production must come back as success=False so callers (Jarvis's
    /produce/async job runner) can tell it apart from a normal completion —
    otherwise a review-gate halt is indistinguishable from a published video."""
    result = await _run_process_message_with_fake_final_state(ep, {
        "final_summary": "halted for review",
        "needs_human_review": True,
        "researcher_failed": False,
        "anchor_failed": False,
    }, monkeypatch)
    assert result["success"] is False


@pytest.mark.parametrize("failure_field", ["researcher_failed", "anchor_failed"])
async def test_process_message_reports_failure_on_hard_abort(ep, monkeypatch, failure_field):
    result = await _run_process_message_with_fake_final_state(ep, {
        "final_summary": "aborted",
        "needs_human_review": False,
        "researcher_failed": False,
        "anchor_failed": False,
        failure_field: True,
    }, monkeypatch)
    assert result["success"] is False


async def test_process_message_success_true_on_normal_completion(ep, monkeypatch):
    result = await _run_process_message_with_fake_final_state(ep, {
        "final_summary": "done",
        "needs_human_review": False,
        "researcher_failed": False,
        "anchor_failed": False,
    }, monkeypatch)
    assert result["success"] is True


async def test_process_message_success_true_on_dedup_suppressed(ep, monkeypatch):
    """dedup_suppressed is an intentional no-op (story already covered), not a
    failure — it must not be reported as success=False."""
    result = await _run_process_message_with_fake_final_state(ep, {
        "final_summary": "suppressed — duplicate story",
        "dedup_suppressed": True,
        "needs_human_review": False,
        "researcher_failed": False,
        "anchor_failed": False,
    }, monkeypatch)
    assert result["success"] is True
