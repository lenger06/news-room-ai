"""
Unit tests for the POST /webhook/ingest route in main.py. The breaking_news_checker
agent is mocked out entirely — these tests only verify the HTTP layer (request
validation, delegation, error handling), not the checker's own logic (covered in
tests/test_breaking_news_fire_logic.py).
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient

import main as main_module
import agents.registry as registry_module


class _FakeChecker:
    def __init__(self, response):
        self._response = response
        self.calls = []

    async def process_webhook_event(self, source, headline, detail="", url="", keywords=None):
        self.calls.append({"source": source, "headline": headline, "detail": detail, "url": url, "keywords": keywords})
        return self._response


def _client():
    # Deliberately not used as a context manager — that would trigger FastAPI's
    # lifespan (loads every agent, creates output dirs), which this test doesn't need.
    return TestClient(main_module.app)


def test_webhook_ingest_delegates_and_returns_result(monkeypatch):
    fake_checker = _FakeChecker({
        "success": True,
        "response": "Breaking news detected — production started.",
        "agent": "breaking_news_checker",
        "breaking_news_found": True,
    })

    async def fake_get_agent(name):
        assert name == "breaking_news_checker"
        return fake_checker

    monkeypatch.setattr(registry_module.agent_registry, "get_agent", fake_get_agent)

    resp = _client().post("/webhook/ingest", json={
        "source": "usgs_earthquake",
        "headline": "M7.2 earthquake strikes Region X",
        "detail": "Depth 12km",
        "url": "https://earthquake.usgs.gov/example",
        "keywords": ["earthquake", "region-x"],
    })

    assert resp.status_code == 200
    body = resp.json()
    assert body["breaking_news_found"] is True
    assert body["response"] == "Breaking news detected — production started."

    assert len(fake_checker.calls) == 1
    call = fake_checker.calls[0]
    assert call["source"] == "usgs_earthquake"
    assert call["headline"] == "M7.2 earthquake strikes Region X"
    assert call["keywords"] == ["earthquake", "region-x"]


def test_webhook_ingest_defaults_optional_fields(monkeypatch):
    fake_checker = _FakeChecker({"success": True, "response": "No breaking news.", "breaking_news_found": False})

    async def fake_get_agent(name):
        return fake_checker

    monkeypatch.setattr(registry_module.agent_registry, "get_agent", fake_get_agent)

    resp = _client().post("/webhook/ingest", json={
        "source": "nws_alert",
        "headline": "Severe thunderstorm warning issued",
    })

    assert resp.status_code == 200
    call = fake_checker.calls[0]
    assert call["detail"] == ""
    assert call["url"] == ""
    assert call["keywords"] is None


def test_webhook_ingest_503_when_checker_unavailable(monkeypatch):
    async def fake_get_agent(name):
        return None

    monkeypatch.setattr(registry_module.agent_registry, "get_agent", fake_get_agent)

    resp = _client().post("/webhook/ingest", json={
        "source": "market_data",
        "headline": "Circuit breaker triggered on S&P 500",
    })
    assert resp.status_code == 503


def test_webhook_ingest_requires_source_and_headline():
    resp = _client().post("/webhook/ingest", json={"detail": "missing required fields"})
    assert resp.status_code == 422
