from langchain.tools import tool
from typing import Optional
import logging
import requests
import json
import re
from pathlib import Path
from datetime import datetime
from config.settings import settings

logger = logging.getLogger(__name__)


@tool
def download_video(
    url: str,
    filename: Optional[str] = None,
    directory: Optional[str] = None,
) -> str:
    """
    Download a video file from a URL and save it to disk.

    Args:
        url: The video URL to download
        filename: Output filename (auto-generated if not provided)
        directory: Save directory (defaults to ./output/media)

    Returns:
        Path to the saved file, or error message
    """
    save_dir = directory or settings.MEDIA_DIR
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    if not filename:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"anchor_video_{timestamp}.mp4"

    filepath = Path(save_dir) / filename

    try:
        logger.info(f"[download_video] Downloading from {url}")
        response = requests.get(url, stream=True, timeout=120)
        if not response.ok:
            return f"Error: HTTP {response.status_code} downloading video"

        with open(filepath, "wb") as f:
            for chunk in response.iter_content(chunk_size=8192):
                f.write(chunk)

        size = filepath.stat().st_size
        logger.info(f"[download_video] Saved {filepath} ({size:,} bytes)")
        return str(filepath)

    except Exception as e:
        logger.error(f"[download_video] Error: {e}", exc_info=True)
        return f"Error downloading video: {str(e)}"


@tool
def extract_graphic_cues(script: str) -> str:
    """
    Parse a broadcast script and extract all [GRAPHIC: ...] cues.

    Args:
        script: The full broadcast script text

    Returns:
        JSON string with list of graphic cues in order
    """
    cues = re.findall(r'\[GRAPHIC:\s*([^\]]+)\]', script, re.IGNORECASE)
    logger.info(f"[extract_graphic_cues] Found {len(cues)} graphic cues")
    return json.dumps({"graphic_cues": cues, "count": len(cues)})


@tool
def save_video_package(
    package_data: str,
    directory: Optional[str] = None,
) -> str:
    """
    Save the video package metadata JSON to disk.

    Args:
        package_data: JSON string containing the video package metadata
        directory: Save directory (defaults to ./output/media)

    Returns:
        Path to saved package file, or error message
    """
    save_dir = directory or settings.MEDIA_DIR
    Path(save_dir).mkdir(parents=True, exist_ok=True)

    filepath = Path(save_dir) / "video_package.json"

    try:
        # Validate it's valid JSON
        data = json.loads(package_data)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info(f"[save_video_package] Saved {filepath}")
        return str(filepath)
    except json.JSONDecodeError as e:
        return f"Error: Invalid JSON — {str(e)}"
    except Exception as e:
        logger.error(f"[save_video_package] Error: {e}", exc_info=True)
        return f"Error saving package: {str(e)}"
