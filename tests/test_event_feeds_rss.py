"""
Unit tests for the RSS/Atom adapter in tools/event_feeds.py — the practical
alternative to a real wire-service webhook (AP/Reuters/Dataminr are all
enterprise-priced with no self-serve push access). feedparser.parse is mocked;
no real network calls.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import feedparser

import tools.event_feeds as event_feeds


class _FakeParsed:
    def __init__(self, feed=None, entries=None, bozo=False):
        self.feed = feed or {}
        self.entries = entries or []
        self.bozo = bozo


def _patch_seen(tmp_path, monkeypatch):
    monkeypatch.setattr(event_feeds, "_SEEN_PATH", tmp_path / "event_feed_seen.json")


def test_no_urls_configured_returns_empty(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    assert event_feeds.fetch_rss_feeds(urls=[]) == []


def test_fetch_rss_feeds_normalizes_entries(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    parsed = _FakeParsed(
        feed={"title": "Example Wire Service — Breaking News"},
        entries=[{
            "id": "story-1",
            "title": "Major Earthquake Strikes Region X",
            "link": "https://example.com/story-1",
            "summary": "<p>Officials report <b>significant damage</b> in the region.</p>",
        }],
    )
    monkeypatch.setattr(feedparser, "parse", lambda url: parsed)

    results = event_feeds.fetch_rss_feeds(urls=["https://example.com/breaking.rss"])
    assert len(results) == 1
    r = results[0]
    assert r["source"] == "rss:Example Wire Service — Breaking News"
    assert r["headline"] == "Major Earthquake Strikes Region X"
    assert "<" not in r["detail"]  # HTML tags stripped
    assert "significant damage" in r["detail"]
    assert r["url"] == "https://example.com/story-1"
    assert r["event_id"] == "rss:story-1"
    assert r["keywords"] == ["major", "earthquake", "strikes", "region"]


def test_fetch_rss_feeds_dedups_across_polls(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    parsed = _FakeParsed(entries=[{"id": "story-1", "title": "Some Story", "link": "https://example.com/1"}])
    monkeypatch.setattr(feedparser, "parse", lambda url: parsed)

    first = event_feeds.fetch_rss_feeds(urls=["https://example.com/feed.rss"])
    second = event_feeds.fetch_rss_feeds(urls=["https://example.com/feed.rss"])
    assert len(first) == 1
    assert len(second) == 0


def test_fetch_rss_feeds_respects_max_items_per_feed(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    entries = [{"id": f"story-{i}", "title": f"Story {i}", "link": f"https://example.com/{i}"} for i in range(20)]
    parsed = _FakeParsed(entries=entries)
    monkeypatch.setattr(feedparser, "parse", lambda url: parsed)

    results = event_feeds.fetch_rss_feeds(urls=["https://example.com/feed.rss"], max_items_per_feed=5)
    assert len(results) == 5


def test_fetch_rss_feeds_falls_back_to_title_when_no_id_or_link(tmp_path, monkeypatch):
    """id/link/title are tried in that order for a stable identifier — a feed entry
    with only a title (no id, no link) should still produce a candidate."""
    _patch_seen(tmp_path, monkeypatch)
    parsed = _FakeParsed(entries=[{"title": "No id or link here"}])
    monkeypatch.setattr(feedparser, "parse", lambda url: parsed)

    results = event_feeds.fetch_rss_feeds(urls=["https://example.com/feed.rss"])
    assert len(results) == 1
    assert results[0]["event_id"] == "rss:No id or link here"


def test_fetch_rss_feeds_skips_entries_with_nothing_usable(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    parsed = _FakeParsed(entries=[{}])
    monkeypatch.setattr(feedparser, "parse", lambda url: parsed)

    assert event_feeds.fetch_rss_feeds(urls=["https://example.com/feed.rss"]) == []


def test_fetch_rss_feeds_one_bad_feed_does_not_break_others(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    good = _FakeParsed(entries=[{"id": "ok-1", "title": "Fine story", "link": "https://good.example.com/1"}])

    def fake_parse(url):
        if "bad" in url:
            raise ConnectionError("feed host unreachable")
        return good

    monkeypatch.setattr(feedparser, "parse", fake_parse)

    results = event_feeds.fetch_rss_feeds(urls=["https://bad.example.com/feed.rss", "https://good.example.com/feed.rss"])
    assert len(results) == 1
    assert results[0]["event_id"] == "rss:ok-1"


def test_fetch_rss_feeds_empty_feed_no_bozo_warning_spam(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    parsed = _FakeParsed(entries=[], bozo=False)
    monkeypatch.setattr(feedparser, "parse", lambda url: parsed)
    assert event_feeds.fetch_rss_feeds(urls=["https://example.com/empty.rss"]) == []


def test_rss_keywords_prefers_capitalized_tokens():
    kws = event_feeds._rss_keywords("Major Earthquake Strikes Coastal Region of Japan")
    assert "major" in kws
    assert "japan" in kws
    assert "of" not in kws  # lowercase filler word must not be picked up


def test_rss_keywords_falls_back_to_long_words_when_no_capitals():
    kws = event_feeds._rss_keywords("breaking news about flooding overnight")
    assert kws  # some keywords extracted even with an all-lowercase headline
    assert all(len(w) > 4 for w in kws)


def test_fetch_all_includes_rss_when_configured(tmp_path, monkeypatch):
    _patch_seen(tmp_path, monkeypatch)
    import requests

    def fake_get(url, headers=None, timeout=None):
        class _Resp:
            ok = True
            def json(self):
                return {"features": []}
        return _Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    monkeypatch.setattr(event_feeds.settings, "EVENT_FEED_RSS_URLS", "https://example.com/feed.rss")

    parsed = _FakeParsed(entries=[{"id": "s1", "title": "Story", "link": "https://example.com/s1"}])
    monkeypatch.setattr(feedparser, "parse", lambda url: parsed)

    results = event_feeds.fetch_all()
    assert any(r["source"].startswith("rss:") for r in results)
