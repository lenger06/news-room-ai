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

# PiP layout constants (for b-roll upper-left inset)
_FRAME_W, _FRAME_H = 1280, 720
_PIP_W, _PIP_H     = 426, 240   # ~1/3 of frame width, 16:9 — visually ~1/6 of screen
_PIP_PADDING       = 24          # pixels from top-left edges


def _load_bg_frame() -> bytes | None:
    """
    Load the studio background frame from BROLL_BG_FRAME_PATH if configured.
    Returns raw file bytes, or None if the setting is unset or the file is missing.
    """
    path_str = settings.BROLL_BG_FRAME_PATH
    if not path_str:
        return None
    try:
        from pathlib import Path as _Path
        p = _Path(path_str)
        if p.exists():
            return p.read_bytes()
        logger.warning(f"[heygen] BROLL_BG_FRAME_PATH set but file not found: {p}")
    except Exception as e:
        logger.warning(f"[heygen] Could not load background frame: {e}")
    return None


def _create_pip_composite(image_bytes: bytes, bg_bytes: bytes | None = None) -> bytes | None:
    """
    Build a 1280×720 composite for use as a HeyGen background:
      - full frame: studio background (bg_bytes) if provided, otherwise
        a blurred + darkened version of the b-roll image
      - upper-left inset: sharp b-roll at _PIP_W × _PIP_H
    Returns JPEG bytes, or None on failure (caller falls back to raw upload).
    """
    try:
        from PIL import Image, ImageFilter
        import io as _io

        src = Image.open(_io.BytesIO(image_bytes)).convert("RGB")

        if bg_bytes:
            bg = Image.open(_io.BytesIO(bg_bytes)).convert("RGB").resize(
                (_FRAME_W, _FRAME_H), Image.LANCZOS
            )
            logger.info("[heygen] Using studio background frame for PiP composite")
        else:
            # Fall back: blurred + darkened b-roll fills the frame
            bg = src.resize((_FRAME_W, _FRAME_H), Image.LANCZOS)
            bg = bg.filter(ImageFilter.GaussianBlur(radius=18))
            black = Image.new("RGB", (_FRAME_W, _FRAME_H), (0, 0, 0))
            bg = Image.blend(bg, black, alpha=0.45)
            logger.info("[heygen] No studio background frame configured — using blurred b-roll")

        # Sharp PiP inset in upper-left
        pip = src.resize((_PIP_W, _PIP_H), Image.LANCZOS)
        bg.paste(pip, (_PIP_PADDING, _PIP_PADDING))

        out = _io.BytesIO()
        bg.save(out, format="JPEG", quality=88)
        logger.info(f"[heygen] PiP composite created ({_PIP_W}×{_PIP_H} inset on {_FRAME_W}×{_FRAME_H} frame)")
        return out.getvalue()
    except Exception as e:
        logger.warning(f"[heygen] PiP composite failed: {e}")
        return None


def upload_image_to_heygen(image_url: str, pip_composite: bool = False, bg_bytes: bytes | None = None) -> str | None:
    """
    Download an image from image_url and upload it to HeyGen as an asset.
    If pip_composite=True, converts the image to a PiP composite before uploading.
    Returns the HeyGen asset_id, or None on failure.
    """
    if not settings.HEYGEN_API_KEY:
        logger.warning("[heygen] Cannot upload image: HEYGEN_API_KEY not configured")
        return None
    try:
        img_resp = requests.get(
            image_url,
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if not img_resp.ok:
            logger.warning(f"[heygen] Failed to download image {image_url}: HTTP {img_resp.status_code}")
            return None

        content_type = img_resp.headers.get("Content-Type", "").split(";")[0].strip()
        if not content_type.startswith("image/"):
            logger.warning(f"[heygen] URL is not an image (Content-Type: {content_type!r}): {image_url[:80]}")
            return None

        image_bytes = img_resp.content
        if not image_bytes:
            logger.warning(f"[heygen] Empty image body from {image_url}")
            return None

        if pip_composite:
            composite = _create_pip_composite(image_bytes, bg_bytes=bg_bytes)
            if composite:
                image_bytes = composite
                content_type = "image/jpeg"
            # else fall through with the raw image

        # Normalise to a HeyGen-supported MIME type
        if content_type not in ("image/jpeg", "image/png", "image/gif", "image/webp"):
            content_type = "image/jpeg"

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
        logger.info(f"[heygen] Uploaded image → asset_id={asset_id}")
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

    segments: list of dicts with keys:
      type:              "anchor"
      script:            spoken text
      broll_description: optional search query (None → use studio video bg)
      image_url:         resolved image URL (None → use studio video bg)

    All scenes have the avatar speaking continuously. Scenes with an image_url
    use that image as the background (avatar matted in front); others use the
    studio video background.

    Returns {"video_id": "...", "status": "processing", "scene_count": N}
         or {"error": "...", "video_id": None}
    """
    import json as _json

    if not settings.HEYGEN_API_KEY:
        return {"error": "HEYGEN_API_KEY not configured", "video_id": None}

    # Load studio background frame once for all PiP composites in this run
    bg_frame = _load_bg_frame()

    video_inputs = []
    for seg in segments:
        script = (seg.get("script") or "").strip()
        if not script:
            continue

        image_url = (seg.get("image_url") or "").strip()
        if image_url:
            asset_id = upload_image_to_heygen(image_url, pip_composite=True, bg_bytes=bg_frame)
            if asset_id:
                background = {"type": "image", "image_asset_id": asset_id}
            else:
                logger.warning("[heygen] B-roll upload failed — falling back to studio background")
                background = {"type": "video", "video_asset_id": background_asset_id, "play_style": "loop"}
        else:
            background = {"type": "video", "video_asset_id": background_asset_id, "play_style": "loop"}

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
            "background": background,
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
