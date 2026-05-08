from langchain.tools import tool
from typing import Optional
import logging
import requests
import json
from config.settings import settings

logger = logging.getLogger(__name__)


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
    if not settings.PEXELS_API_KEY:
        return json.dumps({"error": "PEXELS_API_KEY not configured", "videos": []})

    try:
        response = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": settings.PEXELS_API_KEY},
            params={
                "query": query,
                "per_page": min(num_results or 3, 5),
                "orientation": "landscape",
                "size": "medium",
            },
            timeout=15,
        )
        if not response.ok:
            logger.warning(f"[video_search_tool] Pexels HTTP {response.status_code}: {response.text[:200]}")
            return json.dumps({"error": f"HTTP {response.status_code}", "videos": []})

        data = response.json()
        videos = []
        for v in data.get("videos", []):
            files = v.get("video_files", [])
            # Prefer HD (720p) landscape files; avoid ultra-high-res to keep download sizes sane
            landscape = [f for f in files if f.get("width", 0) >= f.get("height", 1)]
            hd = next(
                (f for f in sorted(landscape, key=lambda f: f.get("width", 0))
                 if f.get("height", 0) <= 720),
                landscape[0] if landscape else (files[0] if files else None),
            )
            if hd and hd.get("link"):
                # Build a readable description from the Pexels page URL slug
                slug = v.get("url", "").rstrip("/").split("/")[-1]
                description = slug.replace("-", " ").strip() or query
                videos.append({
                    "url": hd["link"],
                    "description": description,
                    "duration_seconds": v.get("duration", 0),
                    "width": hd.get("width", 0),
                    "height": hd.get("height", 0),
                })

        logger.info(f"[video_search_tool] {len(videos)} clips for: {query!r}")
        return json.dumps({"videos": videos, "query": query})

    except Exception as e:
        logger.error(f"[video_search_tool] Error: {e}", exc_info=True)
        return json.dumps({"error": str(e), "videos": []})
