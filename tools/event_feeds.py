"""
Event feed adapters — free, no-API-key sources that produce breaking-news candidate
events for /webhook/ingest-style evaluation, without requiring an external poller or
push infrastructure. See SELF_IMPROVEMENT_ROADMAP.md Phase 5.

Each adapter returns a list of normalized candidate dicts:
  {"source", "headline", "detail", "url", "keywords", "event_id"}
`event_id` is a stable identifier used by the seen-event cache so the same live
event (an earthquake, an active weather alert) isn't re-submitted every poll cycle.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

_SEEN_PATH = Path("./output/event_feed_seen.json")
_MAX_SEEN = 500
_SEEN_TTL_HOURS = 72.0

_USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/significant_month.geojson"
_NWS_URL = "https://api.weather.gov/alerts/active"


def _load_seen() -> dict:
    if _SEEN_PATH.exists():
        try:
            return json.loads(_SEEN_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_seen(seen: dict) -> None:
    _SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    cutoff = datetime.now(timezone.utc).timestamp() - (_SEEN_TTL_HOURS * 3600)
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    if len(pruned) > _MAX_SEEN:
        pruned = dict(sorted(pruned.items(), key=lambda kv: kv[1], reverse=True)[:_MAX_SEEN])
    try:
        _SEEN_PATH.write_text(json.dumps(pruned), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[event_feeds] Could not save seen-event cache: {e}")


def _filter_unseen(candidates: list[dict]) -> list[dict]:
    """Return only candidates whose event_id hasn't been recorded before, and record them."""
    seen = _load_seen()
    now = datetime.now(timezone.utc).timestamp()
    unseen = [c for c in candidates if c["event_id"] not in seen]
    if unseen:
        for c in unseen:
            seen[c["event_id"]] = now
        _save_seen(seen)
    return unseen


def _place_keywords(place: str) -> list[str]:
    """Pull discriminating location tokens out of a string like '10km SW of Anytown, CA' —
    same convention used elsewhere (proper nouns/place names, not generic filler words)."""
    tokens = re.split(r'[,\s]+', place)
    return [t.lower() for t in tokens if len(t) > 2 and t.isalpha()][:4]


def fetch_usgs_earthquakes(min_magnitude: float | None = None) -> list[dict]:
    """Poll the USGS significant-earthquakes feed (updated every 5 min, no API key
    required) and return unseen events at or above min_magnitude."""
    min_magnitude = min_magnitude if min_magnitude is not None else settings.EVENT_FEED_MIN_MAGNITUDE
    try:
        resp = requests.get(_USGS_URL, timeout=15)
        if not resp.ok:
            logger.warning(f"[event_feeds] USGS feed HTTP {resp.status_code}")
            return []
        data = resp.json()
    except Exception as e:
        logger.warning(f"[event_feeds] USGS feed fetch failed: {e}")
        return []

    candidates = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        mag = props.get("mag")
        event_id = feature.get("id", "")
        if mag is None or mag < min_magnitude or not event_id:
            continue
        place = props.get("place", "unknown location")
        candidates.append({
            "source": "usgs_earthquake",
            "headline": f"M{mag} earthquake — {place}",
            "detail": f"Magnitude {mag} earthquake reported {place}.",
            "url": props.get("url", ""),
            "keywords": ["earthquake"] + _place_keywords(place),
            "event_id": f"usgs:{event_id}",
        })
    return _filter_unseen(candidates)


def fetch_nws_alerts(severities: tuple | None = None) -> list[dict]:
    """Poll active NWS/weather.gov CAP alerts (no API key required, but a descriptive
    User-Agent header is mandated by their usage policy — see settings.EVENT_FEED_USER_AGENT)
    and return unseen alerts matching the given severities."""
    severities = severities or tuple(s.strip() for s in settings.EVENT_FEED_NWS_SEVERITIES.split(","))
    headers = {"User-Agent": settings.EVENT_FEED_USER_AGENT, "Accept": "application/geo+json"}
    try:
        resp = requests.get(_NWS_URL, headers=headers, timeout=15)
        if not resp.ok:
            logger.warning(f"[event_feeds] NWS feed HTTP {resp.status_code}")
            return []
        data = resp.json()
    except Exception as e:
        logger.warning(f"[event_feeds] NWS feed fetch failed: {e}")
        return []

    candidates = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        severity = props.get("severity", "")
        event_id = props.get("id", "") or feature.get("id", "")
        if severity not in severities or not event_id:
            continue
        area = props.get("areaDesc", "")
        event_type = props.get("event", "Weather alert")
        candidates.append({
            "source": "nws_alert",
            "headline": props.get("headline") or event_type,
            "detail": f"{event_type} — {area}. {(props.get('description') or '')[:300]}",
            "url": event_id,
            "keywords": _place_keywords(area) or [event_type.lower()],
            "event_id": f"nws:{event_id}",
        })
    return _filter_unseen(candidates)


def _rss_keywords(title: str) -> list[str]:
    """Pull discriminating keywords out of a headline — prefers capitalized (likely
    proper-noun) tokens, same convention used elsewhere; falls back to longer words
    if the headline has no capitalized tokens at all."""
    words = re.findall(r"[A-Za-z][A-Za-z'-]{2,}", title)
    capitalized = [w.lower() for w in words if w[0].isupper()]
    pool = capitalized if capitalized else [w.lower() for w in words if len(w) > 4]
    return list(dict.fromkeys(pool))[:6]


def fetch_rss_feeds(urls: list[str] | None = None, max_items_per_feed: int | None = None) -> list[dict]:
    """
    Poll a configurable list of RSS/Atom feeds (settings.EVENT_FEED_RSS_URLS) and return
    unseen items as candidates. This is the practical alternative to a real wire-service
    webhook (AP/Reuters/Dataminr are all enterprise-priced with no self-serve push access) —
    point it at outlets' public "breaking news" or "top stories" category feeds, many of
    which syndicate AP wire content, rather than a full general-news firehose.
    """
    urls = urls if urls is not None else [u.strip() for u in settings.EVENT_FEED_RSS_URLS.split(",") if u.strip()]
    if not urls:
        return []

    max_items_per_feed = max_items_per_feed if max_items_per_feed is not None else settings.EVENT_FEED_RSS_MAX_ITEMS_PER_FEED

    import feedparser

    candidates = []
    for url in urls:
        try:
            parsed = feedparser.parse(url)
        except Exception as e:
            logger.warning(f"[event_feeds] RSS fetch failed for {url}: {e}")
            continue
        if not parsed.entries:
            if getattr(parsed, "bozo", False):
                logger.warning(f"[event_feeds] RSS feed unparseable or empty: {url}")
            continue

        source_name = (parsed.feed.get("title") or url).strip()
        for entry in parsed.entries[:max_items_per_feed]:
            event_id = entry.get("id") or entry.get("link") or entry.get("title")
            if not event_id:
                continue
            title = entry.get("title", "Untitled").strip()
            summary = re.sub(r'<[^>]+>', '', entry.get("summary", ""))[:400].strip()
            candidates.append({
                "source": f"rss:{source_name}",
                "headline": title,
                "detail": summary,
                "url": entry.get("link", ""),
                "keywords": _rss_keywords(title),
                "event_id": f"rss:{event_id}",
            })
    return _filter_unseen(candidates)


def fetch_all() -> list[dict]:
    """Fetch and merge unseen candidates from every configured feed."""
    return fetch_usgs_earthquakes() + fetch_nws_alerts() + fetch_rss_feeds()
