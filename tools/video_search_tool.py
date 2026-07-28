from langchain.tools import tool
from typing import Optional
import logging
import requests
import json
from config.settings import settings

logger = logging.getLogger(__name__)

# Pixabay offers a simple, free video API with direct CDN URLs that download
# without any authorization headers — unlike Pexels whose CDN returns 403.
_PIXABAY_VIDEO_URL = "https://pixabay.com/api/videos/"


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
        videos = []
        for hit in data.get("hits", []):
            chosen = _pick_resolution(hit)
            if not chosen:
                continue
            raw_url = chosen.get("url", "")
            logger.debug(f"[video_search_tool] Pixabay raw URL: {raw_url}")
            tags = hit.get("tags", "").strip() or query
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
