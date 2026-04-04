from langchain.tools import tool
from typing import Optional
import logging
import requests
import json
from config.settings import settings

logger = logging.getLogger(__name__)


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
    if not settings.TAVILY_API_KEY:
        return json.dumps({"error": "TAVILY_API_KEY not configured", "images": []})

    try:
        payload = {
            "api_key": settings.TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "include_images": True,
            "include_image_descriptions": True,
            "max_results": 3,
        }
        response = requests.post("https://api.tavily.com/search", json=payload, timeout=15)

        if not response.ok:
            return json.dumps({"error": f"HTTP {response.status_code}", "images": []})

        data = response.json()
        raw_images = data.get("images", [])

        images = []
        for item in raw_images[:min(num_results or 3, 5)]:
            if isinstance(item, dict):
                images.append({
                    "url": item.get("url", ""),
                    "caption": item.get("description") or query,
                    "thumbnail": item.get("url", ""),
                })
            elif isinstance(item, str):
                images.append({"url": item, "caption": query, "thumbnail": item})

        images = [img for img in images if img["url"]]
        logger.info(f"[image_search_tool] {len(images)} images for: {query}")
        return json.dumps({"images": images, "query": query})

    except Exception as e:
        logger.error(f"[image_search_tool] Error: {e}", exc_info=True)
        return json.dumps({"error": str(e), "images": []})
