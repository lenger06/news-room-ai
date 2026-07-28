from langchain.tools import tool
from typing import Optional
import logging
import requests
import json
from config.settings import settings

logger = logging.getLogger(__name__)


def _image_search_impl(query: str, num_results: Optional[int] = 3) -> dict:
    """Plain-Python core of image_search_tool (not an LLM tool) — called directly by
    agents/researcher/agent.py's deterministic b-roll backstop, as well as by the
    @tool-wrapped version below."""
    if not settings.TAVILY_API_KEY:
        return {"error": "TAVILY_API_KEY not configured", "images": []}

    try:
        payload = {
            "api_key": settings.TAVILY_API_KEY,
            "query": query,
            "topic": "news",
            "search_depth": "basic",
            "include_images": True,
            "include_image_descriptions": True,
            "max_results": 5,
        }
        response = requests.post("https://api.tavily.com/search", json=payload, timeout=15)

        if not response.ok:
            return {"error": f"HTTP {response.status_code}", "images": []}

        data = response.json()
        raw_images = data.get("images", [])

        _BLOCKED = (
            "lookaside.instagram.com", "lookaside.fbsbx.com",
            "facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com",
        )

        images = []
        for item in raw_images:
            if len(images) >= min(num_results or 3, 5):
                break
            if isinstance(item, dict):
                url = item.get("url", "")
            elif isinstance(item, str):
                url = item
            else:
                continue
            if not url or any(d in url.lower() for d in _BLOCKED):
                continue
            if isinstance(item, dict):
                images.append({
                    "url": url,
                    "caption": item.get("description") or query,
                    "thumbnail": url,
                })
            else:
                images.append({"url": url, "caption": query, "thumbnail": url})
        logger.info(f"[image_search_tool] {len(images)} images for: {query}")
        return {"images": images, "query": query}

    except Exception as e:
        logger.error(f"[image_search_tool] Error: {e}", exc_info=True)
        return {"error": str(e), "images": []}


@tool
def image_search_tool(
    query: str,
    num_results: Optional[int] = 3,
) -> str:
    """
    Search for images relevant to a news topic.

    Args:
        query: What to search for (e.g. "hurricane Milton satellite image")
        num_results: Number of images to return (default 3)

    Returns:
        JSON string with image URLs, captions, and thumbnails
    """
    return json.dumps(_image_search_impl(query, num_results))
