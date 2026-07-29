from langchain.tools import tool
from typing import Optional
import logging
import re
import requests
import json
from config.settings import settings

logger = logging.getLogger(__name__)

# Pixabay offers a simple, free video API with direct CDN URLs that download
# without any authorization headers — unlike Pexels whose CDN returns 403.
_PIXABAY_VIDEO_URL = "https://pixabay.com/api/videos/"

# Pixabay's video search matches on uploader-submitted tags, not actual video content
# — and stock-footage tags are routinely stuffed with broad trending buzzwords to
# maximize discoverability. Confirmed live 2026-07-28: a generic "space" stock clip
# tagged "scientist, mars, planet, space, galaxy, universe, cosmos, astronomer,
# astronomy, telescope, science, alien, nasa, astronautics, research, spacex, globe"
# ranked in the top 2 results for the query "SpaceX Starship V3 launch and program
# history" — sharing only the single word "spacex" — and the actual footage was an
# unrelated aerial desert shot. Requiring 2+ overlapping significant words rejects
# this kind of single-buzzword coincidence while still allowing genuinely on-topic
# matches through.
_STOPWORDS = {
    "a", "an", "and", "the", "of", "in", "on", "for", "to", "with", "at", "by",
    "is", "are", "was", "were", "from", "as", "its", "it", "this", "that",
}
_MIN_TAG_OVERLAP = 2


def _significant_words(text: str) -> set[str]:
    words = re.findall(r"[a-zA-Z]+", text.lower())
    return {w for w in words if len(w) > 2 and w not in _STOPWORDS}


def _tag_overlap_count(tags: str, query_words: set[str]) -> int:
    return len(_significant_words(tags) & query_words)


def _pick_resolution(video_hit: dict) -> dict | None:
    """Pick the best resolution <= 720p from a Pixabay video hit's 'videos' dict."""
    vids = video_hit.get("videos", {})
    # Prefer large (1920×1080 downscaled) → medium (1280×720) → small → tiny
    for key in ("large", "medium", "small", "tiny"):
        v = vids.get(key)
        if v and v.get("url") and v.get("height", 9999) <= 720:
            return v
    # Fall back to any available tier
    for key in ("large", "medium", "small", "tiny"):
        v = vids.get(key)
        if v and v.get("url"):
            return v
    return None


def _video_search_impl(query: str, num_results: Optional[int] = 3) -> dict:
    """Plain-Python core of video_search_tool (not an LLM tool) — called directly by
    agents/researcher/agent.py's deterministic b-roll backstop, as well as by the
    @tool-wrapped version below."""
    if not settings.PIXABAY_API_KEY:
        return {"error": "PIXABAY_API_KEY not configured", "videos": []}

    try:
        response = requests.get(
            _PIXABAY_VIDEO_URL,
            params={
                "key": settings.PIXABAY_API_KEY,
                "q": query,
                "per_page": max(3, min(num_results or 3, 200)),
                "video_type": "film",
                "orientation": "horizontal",
            },
            timeout=15,
        )
        if not response.ok:
            logger.warning(f"[video_search_tool] Pixabay HTTP {response.status_code}: {response.text[:200]}")
            return {"error": f"HTTP {response.status_code}", "videos": []}

        data = response.json()
        query_words = _significant_words(query)
        min_overlap = min(_MIN_TAG_OVERLAP, len(query_words)) if query_words else 0

        videos = []
        for hit in data.get("hits", []):
            chosen = _pick_resolution(hit)
            if not chosen:
                continue
            tags = hit.get("tags", "").strip() or query
            if query_words and _tag_overlap_count(tags, query_words) < min_overlap:
                logger.debug(
                    f"[video_search_tool] Skipping low-relevance hit for {query!r} "
                    f"(tags: {tags!r})"
                )
                continue
            raw_url = chosen.get("url", "")
            logger.debug(f"[video_search_tool] Pixabay raw URL: {raw_url}")
            videos.append({
                "url": raw_url,
                "description": tags,
                "duration_seconds": hit.get("duration", 0),
                "width": chosen.get("width", 0),
                "height": chosen.get("height", 0),
            })
            if len(videos) >= (num_results or 3):
                break

        logger.info(f"[video_search_tool] {len(videos)} clips for: {query!r}")
        return {"videos": videos, "query": query}

    except Exception as e:
        logger.error(f"[video_search_tool] Error: {e}", exc_info=True)
        return {"error": str(e), "videos": []}


@tool
def video_search_tool(
    query: str,
    num_results: Optional[int] = 3,
) -> str:
    """
    Search for short royalty-free video clips relevant to a news topic (for use as b-roll footage).

    Args:
        query: What to search for (e.g. "cargo ships strait of hormuz", "senate chamber vote")
        num_results: Number of video clips to return (default 3)

    Returns:
        JSON string with direct video file URLs, descriptions, and durations
    """
    return json.dumps(_video_search_impl(query, num_results))
