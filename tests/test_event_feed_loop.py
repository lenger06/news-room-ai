"""
Unit test for main.py's _event_feed_loop — the background poller that, when
enabled, feeds tools.event_feeds candidates through
breaking_news_checker.process_webhook_event(). asyncio.sleep and fetch_all are
mocked so this runs instantly and never makes a real network call.
"""
import asyncio
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest

import main as main_module
import agents.registry as registry_module
import tools.event_feeds as event_feeds_module


class _FakeChecker:
    def __init__(self):
        self.calls = []

    async def process_webhook_event(self, source, headline, detail="", url="", keywords=None):
        self.calls.append({"source": source, "headline": headline, "detail": detail, "url": url, "keywords": keywords})
        return {"success": True, "response": "No breaking news.", "breaking_news_found": False}


async def test_event_feed_loop_evaluates_candidates_then_stops_on_cancel(monkeypatch):
    call_state = {"n": 0}
    candidate = {
        "source": "usgs_earthquake", "headline": "M7.0 earthquake — Testville",
        "detail": "detail text", "url": "https://example.com", "keywords": ["earthquake", "testville"],
    }

    async def fake_sleep(seconds):
        call_state["n"] += 1
        if call_state["n"] > 1:
            raise asyncio.CancelledError()

    def fake_fetch_all():
        return [candidate] if call_state["n"] == 1 else []

    fake_checker = _FakeChecker()

    async def fake_get_agent(name):
        assert name == "breaking_news_checker"
        return fake_checker

    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(event_feeds_module, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(registry_module.agent_registry, "get_agent", fake_get_agent)

    with pytest.raises(asyncio.CancelledError):
        await main_module._event_feed_loop()

    assert len(fake_checker.calls) == 1
    assert fake_checker.calls[0]["source"] == "usgs_earthquake"
    assert fake_checker.calls[0]["keywords"] == ["earthquake", "testville"]


async def test_event_feed_loop_survives_checker_exception_and_keeps_polling(monkeypatch):
    """A single candidate blowing up must not kill the whole poll loop — it should log
    and continue to the next sleep/fetch cycle."""
    call_state = {"n": 0}
    candidate = {"source": "nws_alert", "headline": "Severe weather", "keywords": ["storm"]}

    async def fake_sleep(seconds):
        call_state["n"] += 1
        if call_state["n"] > 2:
            raise asyncio.CancelledError()

    def fake_fetch_all():
        return [candidate] if call_state["n"] <= 2 else []

    class _RaisingChecker:
        async def process_webhook_event(self, **kwargs):
            raise RuntimeError("simulated LLM failure")

    async def fake_get_agent(name):
        return _RaisingChecker()

    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(event_feeds_module, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(registry_module.agent_registry, "get_agent", fake_get_agent)

    with pytest.raises(asyncio.CancelledError):
        await main_module._event_feed_loop()

    # Reached the cancellation on the 3rd sleep call — proves the loop survived
    # two exception-raising iterations instead of crashing out on the first.
    assert call_state["n"] == 3


async def test_event_feed_loop_logs_heartbeat_even_with_no_new_candidates(monkeypatch, caplog):
    """
    Regression guard: an empty poll cycle (the common case — most 30-minute
    windows won't have a new earthquake/alert/RSS item) must still leave a
    visible log line, otherwise there's no way to tell "polling fine, nothing
    new" apart from "silently stopped working."
    """
    call_state = {"n": 0}

    async def fake_sleep(seconds):
        call_state["n"] += 1
        if call_state["n"] > 1:
            raise asyncio.CancelledError()

    def fake_fetch_all():
        return []

    monkeypatch.setattr(main_module.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(event_feeds_module, "fetch_all", fake_fetch_all)

    with caplog.at_level("INFO"):
        with pytest.raises(asyncio.CancelledError):
            await main_module._event_feed_loop()

    assert any("Poll cycle complete: 0 new candidate(s)" in r.message for r in caplog.records)
