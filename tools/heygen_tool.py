from langchain.tools import tool
from typing import Optional
import logging
import requests
import time
from config.settings import settings

logger = logging.getLogger(__name__)

HEYGEN_BASE_URL = "https://api.heygen.com"


def get_heygen_credits() -> int:
    """
    Return remaining HeyGen credits.
    Raises RuntimeError if the API call fails or the key is not configured.
    """
    if not settings.HEYGEN_API_KEY:
        raise RuntimeError("HEYGEN_API_KEY not configured")
    response = requests.get(
        f"{HEYGEN_BASE_URL}/v1/user/remaining.get",
        headers={"x-api-key": settings.HEYGEN_API_KEY},
        timeout=15,
    )
    if not response.ok:
        raise RuntimeError(f"HeyGen credits check failed: HTTP {response.status_code}")
    data = response.json().get("data", {})
    remaining = data.get("remaining_quota")
    if remaining is None:
        raise RuntimeError(f"Unexpected HeyGen credits response: {response.text[:200]}")
    return int(remaining)


HEYGEN_UPLOAD_URL = "https://upload.heygen.com"


def upload_image_to_heygen(image_url: str) -> str | None:
    """
    Download an image from image_url and upload it to HeyGen as an asset.
    Returns the HeyGen asset_id, or None on failure.

    HeyGen asset upload uses upload.heygen.com (not api.heygen.com),
    raw binary body, and Content-Type set to the image MIME type.
    The asset_id is returned in data.id.
    """
    if not settings.HEYGEN_API_KEY:
        logger.warning("[heygen] Cannot upload image: HEYGEN_API_KEY not configured")
        return None
    try:
        # Download image bytes
        img_resp = requests.get(
            image_url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},  # some CDNs block bare requests
        )
        if not img_resp.ok:
            logger.warning(f"[heygen] Failed to download image {image_url}: HTTP {img_resp.status_code}")
            return None

        content_type = img_resp.headers.get("Content-Type", "image/jpeg").split(";")[0].strip()
        # Normalise to a supported MIME type
        if content_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            content_type = "image/jpeg"

        image_bytes = img_resp.content
        if not image_bytes:
            logger.warning(f"[heygen] Empty image body from {image_url}")
            return None

        # Upload raw binary to upload.heygen.com/v1/asset
        upload_resp = requests.post(
            f"{HEYGEN_UPLOAD_URL}/v1/asset",
            headers={
                "X-Api-Key": settings.HEYGEN_API_KEY,
                "Content-Type": content_type,
            },
            data=image_bytes,
            timeout=60,
        )
        if not upload_resp.ok:
            logger.warning(f"[heygen] Asset upload failed: HTTP {upload_resp.status_code} {upload_resp.text[:200]}")
            return None

        asset_id = upload_resp.json().get("data", {}).get("id")
        logger.info(f"[heygen] Uploaded b-roll image → asset_id={asset_id}")
        return asset_id

    except Exception as e:
        logger.warning(f"[heygen] upload_image_to_heygen error: {e}")
        return None


@tool
def generate_anchor_video(
    script: str,
    avatar_id: Optional[str] = None,
    voice_id: Optional[str] = None,
    background_asset_id: Optional[str] = None,
    title: Optional[str] = None,
) -> str:
    """
    Submit a news anchor script to HeyGen to generate an AI presenter video.

    Args:
        script: The spoken broadcast script (plain text, no markdown)
        avatar_id: HeyGen avatar ID to use (uses default from settings if not provided)
        voice_id: HeyGen voice ID to use (uses default from settings if not provided)
        background_asset_id: HeyGen video background asset ID (uses default if not provided)
        title: Optional title for the video in HeyGen

    Returns:
        JSON string with video_id and status, or error message
    """
    import json

    if not settings.HEYGEN_API_KEY:
        return json.dumps({"error": "HEYGEN_API_KEY not configured", "video_id": None})

    # Truncate to HeyGen's 5000 char limit
    script = script[:5000]

    avatar = avatar_id or settings.HEYGEN_AVATAR_ID
    voice = voice_id or settings.HEYGEN_VOICE_ID

    if not avatar or not voice:
        return json.dumps({
            "error": "HEYGEN_AVATAR_ID and HEYGEN_VOICE_ID must be configured in .env",
            "video_id": None,
        })

    payload = {
        "video_inputs": [
            {
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar,
                    "avatar_style": "normal",
                    "matting": True,
                },
                "voice": {
                    "type": "text",
                    "input_text": script,
                    "voice_id": voice,
                },
                "background": {
                    "type": "video",
                    "video_asset_id": background_asset_id or "f6fa4085043140deaba8258a96233036",
                    "play_style": "loop",
                },
            }
        ],
        "dimension": {"width": 1280, "height": 720},
        "title": title or "News Segment",
    }

    try:
        response = requests.post(
            f"{HEYGEN_BASE_URL}/v2/video/generate",
            headers={"x-api-key": settings.HEYGEN_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )

        if not response.ok:
            return json.dumps({"error": f"HeyGen HTTP {response.status_code}: {response.text[:200]}", "video_id": None})

        data = response.json()
        video_id = data.get("data", {}).get("video_id") or data.get("video_id")
        logger.info(f"[heygen] Video generation started: {video_id}")
        return json.dumps({"video_id": video_id, "status": "processing"})

    except Exception as e:
        logger.error(f"[heygen] generate error: {e}", exc_info=True)
        return json.dumps({"error": str(e), "video_id": None})


def generate_video_multiscene(
    segments: list,
    avatar_id: str,
    voice_id: str,
    background_asset_id: str,
    title: str = "News Segment",
) -> dict:
    """
    Build and submit a multi-scene HeyGen video (Studio API v2).

    segments: list of dicts, each either:
      {"type": "anchor", "script": "spoken text..."}
      {"type": "broll",  "image_url": "https://...", "description": "caption"}

    Anchor scenes use the studio background video; b-roll scenes use the image URL
    as a full-screen background with the anchor silently present in front of it.

    Returns {"video_id": "...", "status": "processing", "scene_count": N}
         or {"error": "...", "video_id": None}
    """
    import json as _json

    if not settings.HEYGEN_API_KEY:
        return {"error": "HEYGEN_API_KEY not configured", "video_id": None}

    video_inputs = []
    for seg in segments:
        seg_type = seg.get("type")
        if seg_type == "anchor":
            script = (seg.get("script") or "").strip()
            if not script:
                continue
            video_inputs.append({
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": "normal",
                    "matting": True,
                },
                "voice": {
                    "type": "text",
                    "input_text": script[:5000],
                    "voice_id": voice_id,
                },
                "background": {
                    "type": "video",
                    "video_asset_id": background_asset_id,
                    "play_style": "loop",
                },
            })
        elif seg_type == "broll":
            image_url = (seg.get("image_url") or "").strip()
            if not image_url:
                continue
            # Studio API v2 requires an asset_id for image backgrounds — upload first
            asset_id = upload_image_to_heygen(image_url)
            if not asset_id:
                logger.warning(f"[heygen] Skipping b-roll scene — could not upload image: {image_url}")
                continue
            video_inputs.append({
                "character": {
                    "type": "avatar",
                    "avatar_id": avatar_id,
                    "avatar_style": "normal",
                    "matting": True,
                    "scale": 0.3,
                    "offset": {"x": -0.6, "y": 0.6},  # upper-left corner
                },
                "voice": {
                    "type": "silence",
                    "duration": 8,
                },
                "background": {
                    "type": "image",
                    "image_asset_id": asset_id,
                    "fit": "contain",
                },
            })

    if not video_inputs:
        return {"error": "No valid segments to render", "video_id": None}

    import json as _json

    payload = {
        "video_inputs": video_inputs,
        "dimension": {"width": 1280, "height": 720},
        "title": title,
    }

    logger.info(f"[heygen] Submitting {len(video_inputs)}-scene payload:\n{_json.dumps(payload, indent=2)}")

    try:
        response = requests.post(
            f"{HEYGEN_BASE_URL}/v2/video/generate",
            headers={"x-api-key": settings.HEYGEN_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=60,
        )
        if not response.ok:
            return {"error": f"HeyGen HTTP {response.status_code}: {response.text[:200]}", "video_id": None}

        data = response.json()
        video_id = data.get("data", {}).get("video_id") or data.get("video_id")
        logger.info(f"[heygen] Multi-scene video submitted: {video_id} ({len(video_inputs)} scenes)")
        return {"video_id": video_id, "status": "processing", "scene_count": len(video_inputs)}

    except Exception as e:
        logger.error(f"[heygen] multiscene generate error: {e}", exc_info=True)
        return {"error": str(e), "video_id": None}


@tool
def check_video_status(video_id: str) -> str:
    """
    Check the status of a HeyGen video generation job.
    Poll this every 30 seconds until status is 'completed' or 'failed'.

    Args:
        video_id: The video ID returned by generate_anchor_video

    Returns:
        JSON string with status and video_url when complete
    """
    import json

    if not settings.HEYGEN_API_KEY:
        return json.dumps({"error": "HEYGEN_API_KEY not configured"})

    try:
        response = requests.get(
            f"{HEYGEN_BASE_URL}/v1/video_status.get",
            headers={"x-api-key": settings.HEYGEN_API_KEY},
            params={"video_id": video_id},
            timeout=15,
        )

        if not response.ok:
            return json.dumps({"error": f"HTTP {response.status_code}: {response.text[:200]}"})

        data = response.json().get("data", {})
        status = data.get("status", "unknown")
        video_url = data.get("video_url")
        thumbnail_url = data.get("thumbnail_url")

        logger.info(f"[heygen] Video {video_id} status: {status}")
        return json.dumps({
            "video_id": video_id,
            "status": status,
            "video_url": video_url,
            "thumbnail_url": thumbnail_url,
        })

    except Exception as e:
        logger.error(f"[heygen] status check error: {e}", exc_info=True)
        return json.dumps({"error": str(e)})


@tool
def list_heygen_avatars() -> str:
    """
    List all available HeyGen avatars so you can pick an appropriate news anchor appearance.

    Returns:
        JSON string with list of avatars (id, name, gender)
    """
    import json

    if not settings.HEYGEN_API_KEY:
        return json.dumps({"error": "HEYGEN_API_KEY not configured"})

    try:
        response = requests.get(
            f"{HEYGEN_BASE_URL}/v2/avatars",
            headers={"x-api-key": settings.HEYGEN_API_KEY},
            timeout=15,
        )
        if not response.ok:
            return json.dumps({"error": f"HTTP {response.status_code}"})

        avatars = response.json().get("data", {}).get("avatars", [])
        simplified = [
            {"avatar_id": a.get("avatar_id"), "avatar_name": a.get("avatar_name"), "gender": a.get("gender")}
            for a in avatars
        ]
        return json.dumps({"avatars": simplified, "count": len(simplified)})
    except Exception as e:
        return json.dumps({"error": str(e)})


@tool
def list_heygen_voices() -> str:
    """
    List available HeyGen voices to pick an appropriate news anchor voice.

    Returns:
        JSON string with list of voices (voice_id, name, language, gender)
    """
    import json

    if not settings.HEYGEN_API_KEY:
        return json.dumps({"error": "HEYGEN_API_KEY not configured"})

    try:
        response = requests.get(
            f"{HEYGEN_BASE_URL}/v2/voices",
            headers={"x-api-key": settings.HEYGEN_API_KEY},
            timeout=15,
        )
        if not response.ok:
            return json.dumps({"error": f"HTTP {response.status_code}"})

        voices = response.json().get("data", {}).get("voices", [])
        # Filter to English voices only for brevity
        english = [
            {"voice_id": v.get("voice_id"), "name": v.get("name"), "language": v.get("language"), "gender": v.get("gender")}
            for v in voices if "en" in (v.get("language") or "").lower()
        ]
        return json.dumps({"voices": english, "count": len(english)})
    except Exception as e:
        return json.dumps({"error": str(e)})
