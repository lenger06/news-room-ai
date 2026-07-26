"""
Unit tests for agents/breaking_news_checker/agent.py's shared suppression/fire
logic (_evaluate_and_maybe_fire) and the new process_webhook_event entry point
added for the /webhook/ingest route.

CRITICAL: requests.post is always mocked here. The real target
(http://localhost:8091/produce/async) would kick off a real, billed production
run if actually hit — these tests must never let that call through.
"""
import json
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest
import requests

from agents.breaking_news_checker.agent import Agent as BNAgent
import agents.breaking_news_checker.agent as bn_agent_module
import tools.breaking_news_log as bn_log
import tools.story_history as story_history


class _FakePostResponse:
    def __init__(self, ok=True, job_id="test-job-123"):
        self.ok = ok
        self._job_id = job_id

    def json(self):
        return {"job_id": self._job_id}


@pytest.fixture
def bn_agent(tmp_path, monkeypatch):
    monkeypatch.setattr(bn_log, "_LOG_PATH", tmp_path / "breaking_news_log.json")
    monkeypatch.setattr(story_history, "_LOG_PATH", tmp_path / "story_history.json")
    # Point at a nonexistent path so _determine_show_slug always falls back to the
    # dedicated "breaking-news" team, independent of this machine's real production history.
    monkeypatch.setattr(bn_agent_module, "_LAST_BROADCAST_PATH", tmp_path / "last_broadcast.json")

    calls = {"post": []}

    def fake_post(url, json=None, timeout=None):
        calls["post"].append((url, json))
        return _FakePostResponse()

    monkeypatch.setattr(requests, "post", fake_post)

    agent = BNAgent()
    agent._test_calls = calls
    return agent


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
