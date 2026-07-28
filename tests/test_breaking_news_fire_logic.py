"""
Unit tests for agents/breaking_news_checker/agent.py's shared suppression/fire
logic (_evaluate_and_maybe_fire) and the new process_webhook_event entry point
added for the /webhook/ingest route.

CRITICAL: requests.post and requests.get are always mocked here. The real
targets (http://localhost:8091/produce/async, /job/{id}) would kick off a
real, billed production run / hit a real local server if actually let through
— these tests must never let either call through. asyncio.sleep is also
mocked to instant, which means the fire-and-forget _check_job_outcome
background task (normally delayed ~45 min) runs essentially immediately —
tests that care about its outcome grab it from _background_checks and await
it directly rather than relying on scheduling luck.
"""
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
from unittest.mock import AsyncMock

import pytest
import requests

from agents.breaking_news_checker.agent import Agent as BNAgent
import agents.breaking_news_checker.agent as bn_agent_module
import tools.breaking_news_log as bn_log
import tools.story_history as story_history
import tools.review_queue as review_queue


class _FakePostResponse:
    def __init__(self, ok=True, job_id="test-job-123"):
        self.ok = ok
        self._job_id = job_id

    def json(self):
        return {"job_id": self._job_id}


class _FakeGetResponse:
    def __init__(self, ok=True, status="complete", error=None):
        self.ok = ok
        self._status = status
        self._error = error

    def json(self):
        return {"status": self._status, "error": self._error}


@pytest.fixture
def bn_agent(tmp_path, monkeypatch):
    # _background_checks is a module-level set (so real fire-and-forget tasks survive
    # across the whole process) — clear it so a task left behind by a previous test
    # can't throw off this test's "exactly one task" assertions.
    bn_agent_module._background_checks.clear()
    monkeypatch.setattr(bn_log, "_LOG_PATH", tmp_path / "breaking_news_log.json")
    monkeypatch.setattr(story_history, "_LOG_PATH", tmp_path / "story_history.json")
    monkeypatch.setattr(review_queue, "_LOG_PATH", tmp_path / "needs_review.json")
    # Point at a nonexistent path so _determine_show_slug always falls back to the
    # dedicated "breaking-news" team, independent of this machine's real production history.
    monkeypatch.setattr(bn_agent_module, "_LAST_BROADCAST_PATH", tmp_path / "last_broadcast.json")
    # Retries/the post-fire safety net both sleep for real amounts of time — collapse
    # that to instant so tests don't take minutes.
    monkeypatch.setattr(bn_agent_module.asyncio, "sleep", AsyncMock())

    calls = {"post": [], "get": []}

    def fake_post(url, json=None, timeout=None):
        calls["post"].append((url, json))
        return _FakePostResponse()

    def fake_get(url, timeout=None):
        calls["get"].append(url)
        # Default: the safety-net check finds the job already "complete" — a no-op
        # for tests that don't care about it. Override with monkeypatch in tests that do.
        return _FakeGetResponse(status="complete")

    monkeypatch.setattr(requests, "post", fake_post)
    monkeypatch.setattr(requests, "get", fake_get)

    agent = BNAgent()
    agent._test_calls = calls
    return agent


async def _await_background_check():
    """Grab and await the single fire-and-forget _check_job_outcome task a fire just
    created, so its effects (review_queue/unrecord) are deterministic in tests."""
    assert len(bn_agent_module._background_checks) == 1
    task = next(iter(bn_agent_module._background_checks))
    await task


async def test_not_breaking_news_no_side_effects(bn_agent):
    result = await bn_agent._evaluate_and_maybe_fire({
        "breaking_news_found": False,
        "reason": "Nothing meets the threshold right now.",
    })
    assert result["breaking_news_found"] is False
    assert result["success"] is True
    assert bn_agent._test_calls["post"] == []


async def test_low_confidence_suppressed(bn_agent):
    result = await bn_agent._evaluate_and_maybe_fire({
        "breaking_news_found": True,
        "confidence": "low",
        "reason": "Borderline, not clearly qualifying.",
    })
    assert result["breaking_news_found"] is False
    assert result["confidence"] == "low"
    assert bn_agent._test_calls["post"] == []


async def test_qualifying_event_fires_production(bn_agent):
    parsed = {
        "breaking_news_found": True,
        "confidence": "high",
        "topic": "Major earthquake strikes Region X",
        "headline": "M7.2 earthquake strikes Region X, buildings collapse",
        "reason": "Meets major natural disaster criterion",
        "keywords": ["earthquake", "region-x"],
        "production_message": "BREAKING: Major earthquake strikes Region X.",
    }
    result = await bn_agent._evaluate_and_maybe_fire(parsed)

    assert result["breaking_news_found"] is True
    assert result["show_slug"] == "breaking-news"
    assert len(bn_agent._test_calls["post"]) == 1
    url, payload = bn_agent._test_calls["post"][0]
    assert url == "http://localhost:8091/produce/async"
    assert payload["request"] == parsed["production_message"]
    assert payload["show_slug"] == "breaking-news"

    # record() must have logged this fire so a repeat check within the gap window
    # is suppressed (tested below).
    logged = bn_log.get_recent_for_dedup()
    assert len(logged) == 1
    assert logged[0]["topic"] == parsed["topic"]


async def test_same_story_suppression_blocks_repeat_fire(bn_agent):
    # Seed two prior fires sharing keywords with the new event — total_coverage=2
    # requires a 6h gap, and these were "just" logged, so the repeat must suppress.
    bn_log.record(topic="Earthquake update 1", headline="h1", keywords=["earthquake", "region-x"], show_slug="breaking-news")
    bn_log.record(topic="Earthquake update 2", headline="h2", keywords=["earthquake", "region-x"], show_slug="breaking-news")

    parsed = {
        "breaking_news_found": True,
        "confidence": "high",
        "topic": "Earthquake update 3",
        "headline": "h3",
        "reason": "Still developing",
        "keywords": ["earthquake", "region-x"],
        "production_message": "BREAKING: update 3",
    }
    result = await bn_agent._evaluate_and_maybe_fire(parsed)

    assert result["breaking_news_found"] is False
    assert "suppression" in result["response"].lower()
    # No new production should have fired.
    assert bn_agent._test_calls["post"] == []


async def test_webhook_event_within_cooldown_skips(bn_agent, monkeypatch):
    monkeypatch.setattr(bn_agent_module, "within_cooldown", lambda: True)
    result = await bn_agent.process_webhook_event(
        source="usgs_earthquake", headline="M4.1 earthquake near Testville",
    )
    assert result["breaking_news_found"] is False
    assert "cooldown" in result["response"].lower()
    assert bn_agent._test_calls["post"] == []


async def test_webhook_event_uses_caller_keywords_when_llm_omits_them(bn_agent, monkeypatch):
    class _FakeLLMResponse:
        content = json.dumps({
            "breaking_news_found": True,
            "confidence": "high",
            "topic": "M7.5 earthquake near Testville",
            "headline": "Major earthquake strikes Testville",
            "reason": "Major natural disaster",
            "keywords": [],  # LLM omitted keywords — adapter-supplied ones should be used
            "production_message": "BREAKING: earthquake in Testville.",
        })

    async def fake_ainvoke(self, messages, *args, **kwargs):
        return _FakeLLMResponse()

    # ChatOpenAI is a pydantic model — instance-level attribute patching is rejected
    # by its __setattr__, so patch the class method instead.
    monkeypatch.setattr(type(bn_agent.llm), "ainvoke", fake_ainvoke)

    result = await bn_agent.process_webhook_event(
        source="usgs_earthquake",
        headline="M7.5 earthquake near Testville",
        detail="Depth 10km",
        url="https://earthquake.usgs.gov/example",
        keywords=["earthquake", "testville"],
    )

    assert result["breaking_news_found"] is True
    assert len(bn_agent._test_calls["post"]) == 1
    logged = bn_log.get_recent_for_dedup()
    assert logged[0]["keywords"] == ["earthquake", "testville"]


# ── Submission robustness: retry, unrecord + review-queue on failure ──────────

def _qualifying_parsed(topic="Major earthquake strikes Region X", headline="M7.2 earthquake strikes Region X"):
    return {
        "breaking_news_found": True,
        "confidence": "high",
        "topic": topic,
        "headline": headline,
        "reason": "Meets major natural disaster criterion",
        "keywords": ["earthquake", "region-x"],
        "production_message": "BREAKING: Major earthquake strikes Region X.",
    }


async def test_submission_retries_once_then_succeeds(bn_agent, monkeypatch):
    attempts = {"n": 0}

    def flaky_post(url, json=None, timeout=None):
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise requests.exceptions.ReadTimeout("simulated timeout")
        return _FakePostResponse()

    monkeypatch.setattr(requests, "post", flaky_post)

    result = await bn_agent._evaluate_and_maybe_fire(_qualifying_parsed())

    assert attempts["n"] == 2
    assert result["production_started"] is True
    assert "PRODUCTION_JOB_ID" in result["response"]
    # The story is still validly recorded — nothing to undo, it actually fired.
    assert len(bn_log.get_recent_for_dedup()) == 1
    assert review_queue.list_pending() == []


async def test_submission_fails_after_retry_unrecords_and_flags_review(bn_agent, monkeypatch):
    def always_fails(url, json=None, timeout=None):
        raise requests.exceptions.ConnectionError("simulated connection failure")

    monkeypatch.setattr(requests, "post", always_fails)

    parsed = _qualifying_parsed()
    result = await bn_agent._evaluate_and_maybe_fire(parsed)

    assert result["production_started"] is False
    assert "FAILED to start" in result["response"]
    # Must NOT be left falsely marked "covered" — nothing was ever produced.
    assert bn_log.get_recent_for_dedup() == []
    pending = review_queue.list_pending()
    assert len(pending) == 1
    assert pending[0]["stage"] == "breaking_news_submission"
    assert pending[0]["topic"] == parsed["topic"]
    # No background outcome-check should be scheduled — there's no job to check.
    assert len(bn_agent_module._background_checks) == 0


async def test_submission_http_error_response_also_unrecords_and_flags(bn_agent, monkeypatch):
    monkeypatch.setattr(requests, "post", lambda url, json=None, timeout=None: _FakePostResponse(ok=False))

    result = await bn_agent._evaluate_and_maybe_fire(_qualifying_parsed())

    assert result["production_started"] is False
    assert bn_log.get_recent_for_dedup() == []
    assert len(review_queue.list_pending()) == 1


# ── Post-fire safety net: _check_job_outcome ───────────────────────────────────

async def test_check_job_outcome_noop_when_job_completed(bn_agent, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _FakeGetResponse(status="complete"))

    result = await bn_agent._evaluate_and_maybe_fire(_qualifying_parsed())
    assert result["production_started"] is True

    await _await_background_check()

    assert review_queue.list_pending() == []
    # Still validly recorded — the job succeeded.
    assert len(bn_log.get_recent_for_dedup()) == 1


async def test_check_job_outcome_flags_error_and_unrecords(bn_agent, monkeypatch):
    monkeypatch.setattr(
        requests, "get",
        lambda url, timeout=None: _FakeGetResponse(status="error", error="Executive Producer not available"),
    )

    parsed = _qualifying_parsed()
    result = await bn_agent._evaluate_and_maybe_fire(parsed)
    assert result["production_started"] is True
    assert len(bn_log.get_recent_for_dedup()) == 1  # recorded optimistically at fire time

    await _await_background_check()

    pending = review_queue.list_pending()
    assert len(pending) == 1
    assert pending[0]["stage"] == "breaking_news_job_error"
    assert "Executive Producer not available" in pending[0]["reason"]
    # A job that actually failed must not leave the story falsely marked covered.
    assert bn_log.get_recent_for_dedup() == []


async def test_check_job_outcome_flags_stalled_without_unrecording(bn_agent, monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _FakeGetResponse(status="running"))

    result = await bn_agent._evaluate_and_maybe_fire(_qualifying_parsed())
    assert result["production_started"] is True

    await _await_background_check()

    pending = review_queue.list_pending()
    assert len(pending) == 1
    assert pending[0]["stage"] == "breaking_news_job_stalled"
    # Unlike a confirmed error, a job that's just still running might finish normally —
    # don't unrecord it, that would risk a duplicate production for the same story.
    assert len(bn_log.get_recent_for_dedup()) == 1
