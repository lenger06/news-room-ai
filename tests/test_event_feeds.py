"""
Unit tests for tools/event_feeds.py — the Phase 5 free/no-API-key event adapters
(USGS earthquakes, NWS weather alerts) that feed candidates through the same
gates as /webhook/ingest. All HTTP calls are mocked.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import requests

import tools.event_feeds as event_feeds


class _FakeResponse:
    def __init__(self, json_data, ok=True, status_code=200):
        self._json = json_data
        self.ok = ok
        self.status_code = status_code

    def json(self):
        return self._json


def _usgs_geojson(features):
    return {"type": "FeatureCollection", "features": features}


def _usgs_feature(event_id, mag, place):
    return {"id": event_id, "properties": {"mag": mag, "place": place, "url": f"https://example.com/{event_id}"}}


def _patch_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(event_feeds, "_SEEN_PATH", tmp_path / "event_feed_seen.json")


def test_usgs_filters_by_minimum_magnitude(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    features = [
        _usgs_feature("ev1", 5.5, "10km SW of Smallville, CA"),
        _usgs_feature("ev2", 7.2, "20km N of Bigtown, Japan"),
    ]
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(_usgs_geojson(features)))

    results = event_feeds.fetch_usgs_earthquakes(min_magnitude=6.0)
    assert len(results) == 1
    assert results[0]["event_id"] == "usgs:ev2"
    assert "earthquake" in results[0]["keywords"]


def test_usgs_second_poll_does_not_resubmit_same_event(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    features = [_usgs_feature("ev1", 7.0, "5km E of Somewhere, CA")]
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse(_usgs_geojson(features)))

    first = event_feeds.fetch_usgs_earthquakes(min_magnitude=6.0)
    second = event_feeds.fetch_usgs_earthquakes(min_magnitude=6.0)
    assert len(first) == 1
    assert len(second) == 0


def test_usgs_http_error_returns_empty_list(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({}, ok=False, status_code=500))
    assert event_feeds.fetch_usgs_earthquakes(min_magnitude=6.0) == []


def test_usgs_network_error_returns_empty_list(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)

    def _raise(*a, **k):
        raise ConnectionError("network down")

    monkeypatch.setattr(requests, "get", _raise)
    assert event_feeds.fetch_usgs_earthquakes(min_magnitude=6.0) == []


def _nws_feature(alert_id, severity, event_type, area, headline=None):
    return {
        "id": alert_id,
        "properties": {
            "id": alert_id,
            "severity": severity,
            "event": event_type,
            "areaDesc": area,
            "headline": headline or f"{event_type} issued for {area}",
            "description": "Detailed alert description text.",
        },
    }


def test_nws_filters_by_severity(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    features = [
        _nws_feature("a1", "Minor", "Wind Advisory", "Some County"),
        _nws_feature("a2", "Severe", "Tornado Warning", "Other County"),
        _nws_feature("a3", "Extreme", "Hurricane Warning", "Coastal County"),
    ]
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"features": features}))

    results = event_feeds.fetch_nws_alerts(severities=("Extreme", "Severe"))
    ids = {r["event_id"] for r in results}
    assert ids == {"nws:a2", "nws:a3"}


def test_nws_second_poll_does_not_resubmit_same_alert(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    features = [_nws_feature("a1", "Extreme", "Hurricane Warning", "Coastal County")]
    monkeypatch.setattr(requests, "get", lambda *a, **k: _FakeResponse({"features": features}))

    first = event_feeds.fetch_nws_alerts(severities=("Extreme",))
    second = event_feeds.fetch_nws_alerts(severities=("Extreme",))
    assert len(first) == 1
    assert len(second) == 0


def test_nws_sends_required_user_agent_header(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    captured = {}

    def fake_get(url, headers=None, timeout=None):
        captured["headers"] = headers
        return _FakeResponse({"features": []})

    monkeypatch.setattr(requests, "get", fake_get)
    event_feeds.fetch_nws_alerts(severities=("Extreme",))
    assert "User-Agent" in captured["headers"]
    assert captured["headers"]["User-Agent"]


def test_fetch_all_merges_both_sources(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)

    def fake_get(url, headers=None, timeout=None):
        if "earthquake" in url:
            return _FakeResponse(_usgs_geojson([_usgs_feature("ev1", 7.0, "Nowhere")]))
        return _FakeResponse({"features": [_nws_feature("a1", "Extreme", "Tornado Warning", "Elsewhere")]})

    monkeypatch.setattr(requests, "get", fake_get)
    results = event_feeds.fetch_all()
    sources = {r["source"] for r in results}
    assert sources == {"usgs_earthquake", "nws_alert"}
