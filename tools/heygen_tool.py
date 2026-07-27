from langchain.tools import tool
from typing import Optional
import logging
import re as _re
import requests
import time
import hashlib
import subprocess
import tempfile
from pathlib import Path
from config.settings import settings

logger = logging.getLogger(__name__)

# ── TTS text normalisation ─────────────────────────────────────────────────────
# Applied to all script text before submission to HeyGen.
# Add entries here whenever the anchor mispronounces something.

_TTS_REPLACEMENTS: list[tuple] = [
    # Dotted abbreviations — remove dots so TTS reads letters, not "dot"
    (_re.compile(r'\bU\.S\.A\.'), "U S A"),
    (_re.compile(r'\bU\.S\.'),    "U S"),
    (_re.compile(r'\bD\.C\.'),    "D C"),
    (_re.compile(r'\bN\.Y\.'),    "N Y"),
    (_re.compile(r'\bL\.A\.'),    "L A"),
    (_re.compile(r'\bD\.O\.J\.'), "D O J"),
    (_re.compile(r'\bF\.B\.I\.'), "F B I"),
    (_re.compile(r'\bC\.I\.A\.'), "C I A"),
    # Common written abbreviations that TTS stumbles on
    (_re.compile(r'\bvs\.'),      "versus"),
    (_re.compile(r'\betc\.'),     "etcetera"),
    (_re.compile(r'\bSt\.\s'),    "Saint "),
    (_re.compile(r'\bDr\.'),      "Doctor"),
    (_re.compile(r'\bMr\.'),      "Mister"),
    (_re.compile(r'\bMrs\.'),     "Missus"),
    (_re.compile(r'\bMs\.'),      "Miz"),
    (_re.compile(r'\bProf\.'),    "Professor"),
    (_re.compile(r'\bSen\.'),     "Senator"),
    (_re.compile(r'\bRep\.'),     "Representative"),
    (_re.compile(r'\bGov\.'),     "Governor"),
    (_re.compile(r'\bGen\.'),     "General"),
    # Em-dash — replace with comma-pause so TTS doesn't vocalise it
    (_re.compile(r'\s*—\s*'),     ", "),
]


def _normalize_tts(text: str) -> str:
    """Apply pronunciation fixes to script text before sending to HeyGen."""
    for pattern, replacement in _TTS_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text

HEYGEN_BASE_URL = "https://api.heygen.com"


def get_heygen_credits() -> int:
    """
    Return remaining HeyGen credits.
    Raises RuntimeError if the API call fails or the key is not configured.
    """
    if not settings.HEYGEN_API_KEY:
        raise RuntimeError("HEYGEN_API_KEY not configured")
    response = requests.get(
        f"{HEYGEN_BASE_URL}/v2/user/remaining_quota",
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
_PIP_W, _PIP_H     = 639, 360   # ~1/2 of frame width, 16:9 — visually ~1/4 of screen
_PIP_PADDING       = 24          # pixels from top-left edges

# FFmpeg composite video cache
_CACHE_DIR                  = Path("./cache")
_BG_VIDEO_CACHE             = _CACHE_DIR / "bg_videos"
_BROLL_COMPOSITE_CACHE      = _CACHE_DIR / "broll_composites"
_BROLL_VIDEO_DOWNLOAD_CACHE = _CACHE_DIR / "broll_video_downloads"
_ENHANCED_BG_CACHE          = _CACHE_DIR / "enhanced_backgrounds"
_COMPOSITE_DURATION_S       = 15     # seconds; HeyGen loops via play_style:loop


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


def _scale_filter(layer) -> str:
    """Return an FFmpeg scale expression for a VideoLayer."""
    if layer.width and layer.height:
        return f"scale={layer.width}:{layer.height}"
    if layer.width:
        return f"scale={layer.width}:-2"
    if layer.height:
        return f"scale=-2:{layer.height}"
    return "scale=iw:ih"


def _apply_background_layers(bg_video_bytes: bytes, layers: list) -> bytes | None:
    """
    FFmpeg: composite n overlay images/videos on top of the studio background video.
    Produces a {_COMPOSITE_DURATION_S}s MP4 (HeyGen will loop it). Audio is stripped.
    Returns MP4 bytes, or None on failure.
    """
    ffmpeg = _get_ffmpeg_exe()
    if not ffmpeg or not layers:
        return None

    # Resolve all layer sources to absolute paths (relative to project root = parent of tools/)
    _project_root = Path(__file__).resolve().parent.parent
    resolved: list[tuple] = []  # (layer, absolute_path)
    for l in layers:
        src = Path(l.source)
        if not src.is_absolute():
            src = _project_root / l.source
        if src.exists():
            resolved.append((l, src))
        else:
            logger.warning(f"[heygen] Background layer file not found: {src}")

    if not resolved:
        logger.warning("[heygen] No background layer files found — skipping")
        return None

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg_path  = tmp / "bg.mp4"
            out_path = tmp / "enhanced_bg.mp4"
            bg_path.write_bytes(bg_video_bytes)

            cmd = [ffmpeg, "-y", "-stream_loop", "-1", "-i", str(bg_path)]
            for layer, src in resolved:
                if src.suffix.lower() in (".mp4", ".mov", ".webm"):
                    cmd += ["-stream_loop", "-1", "-i", str(src)]
                else:
                    cmd += ["-loop", "1", "-i", str(src)]

            filter_parts = []
            prev = "0:v"
            for i, (layer, _) in enumerate(resolved, 1):
                s_label = f"s{i}"
                o_label = f"o{i}"
                filter_parts.append(f"[{i}:v]{_scale_filter(layer)}[{s_label}]")
                filter_parts.append(f"[{prev}][{s_label}]overlay={layer.x}:{layer.y}[{o_label}]")
                prev = o_label

            cmd += [
                "-filter_complex", ";".join(filter_parts),
                "-map", f"[{prev}]",
                "-t", str(_COMPOSITE_DURATION_S),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-an",
                str(out_path),
            ]

            logger.info(f"[heygen] Background layer FFmpeg command: {' '.join(cmd)}")
            result = subprocess.run(cmd, capture_output=True, timeout=180)
            if result.returncode != 0:
                logger.warning(
                    f"[heygen] Background layers FFmpeg failed (rc={result.returncode}): "
                    f"{result.stderr.decode(errors='replace')[-800:]}"
                )
                return None

            data = out_path.read_bytes()
            logger.info(f"[heygen] Background layers applied ({len(resolved)} layer(s), {len(data):,} bytes)")
            return data
    except Exception as e:
        logger.warning(f"[heygen] _apply_background_layers error: {e}", exc_info=True)
        return None


def prepare_enhanced_background(
    background_asset_id: str, layers: list
) -> tuple[str, bytes | None, bool, Path | None]:
    """
    Composite background layers onto the studio background video, upload the result
    to HeyGen, and return (effective_asset_id, enhanced_bytes, is_fresh_upload, asset_cache_path).

    enhanced_bytes is cached locally (not re-composited on subsequent runs).
    If is_fresh_upload=True, caller should delete the HeyGen asset after render and
    unlink asset_cache_path — same pattern as create_broll_video_asset.
    The bytes cache (.mp4) is kept across renders so FFmpeg only runs once.

    If layers is empty or anything fails, returns (background_asset_id, None, False, None).
    """
    if not layers:
        return background_asset_id, None, False, None

    layer_key = ":".join(
        f"{l.source}@{l.x},{l.y},{l.width},{l.height}" for l in layers
    )
    key         = hashlib.md5(f"{background_asset_id}:{layer_key}".encode()).hexdigest()[:12]
    bytes_cache = _ENHANCED_BG_CACHE / f"{key}.mp4"
    asset_cache = _ENHANCED_BG_CACHE / f"{key}.asset_id"

    enhanced_bytes: bytes | None = None

    # Use locally cached bytes if available (avoids re-compositing)
    if bytes_cache.exists():
        enhanced_bytes = bytes_cache.read_bytes()
        logger.info(f"[heygen] Reusing cached enhanced background bytes ({len(enhanced_bytes):,} bytes)")

    # Use cached HeyGen asset_id if available
    if enhanced_bytes and asset_cache.exists():
        cached_id = asset_cache.read_text().strip()
        if cached_id:
            logger.info(f"[heygen] Reusing cached enhanced background asset: {cached_id}")
            return cached_id, enhanced_bytes, False, asset_cache

    # Composite if we don't have cached bytes yet
    if not enhanced_bytes:
        bg_bytes = _get_background_video_bytes(background_asset_id)
        if not bg_bytes:
            return background_asset_id, None, False, None
        enhanced_bytes = _apply_background_layers(bg_bytes, layers)
        if not enhanced_bytes:
            logger.warning("[heygen] Background layer compositing failed — using original background")
            return background_asset_id, None, False, None
        _ENHANCED_BG_CACHE.mkdir(parents=True, exist_ok=True)
        bytes_cache.write_bytes(enhanced_bytes)

    # Upload to HeyGen
    asset_id = _upload_video_asset(enhanced_bytes)
    if not asset_id:
        return background_asset_id, None, False, None

    _ENHANCED_BG_CACHE.mkdir(parents=True, exist_ok=True)
    asset_cache.write_text(asset_id)
    logger.info(f"[heygen] Enhanced background uploaded → {asset_id}")
    return asset_id, enhanced_bytes, True, asset_cache


def _pip_x(pip_w: int, pip_position: str) -> int:
    """Return the x coordinate for the PiP inset based on position."""
    if pip_position == "right":
        return _FRAME_W - pip_w - _PIP_PADDING
    return _PIP_PADDING


def _create_pip_composite(
    image_bytes: bytes, bg_bytes: bytes | None = None, pip_position: str = "left"
) -> bytes | None:
    """
    Build a 1280×720 composite for use as a HeyGen background:
      - full frame: studio background (bg_bytes) if provided, otherwise
        a blurred + darkened version of the b-roll image
      - upper-corner inset: sharp b-roll at _PIP_W × _PIP_H
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

        # Sharp PiP inset — preserve original aspect ratio
        orig_w, orig_h = src.size
        if orig_w * _PIP_H > orig_h * _PIP_W:
            pip_w, pip_h = _PIP_W, max(1, round(_PIP_W * orig_h / orig_w))
        else:
            pip_w, pip_h = max(1, round(_PIP_H * orig_w / orig_h)), _PIP_H
        pip = src.resize((pip_w, pip_h), Image.LANCZOS)
        x = _pip_x(pip_w, pip_position)
        bg.paste(pip, (x, _PIP_PADDING))

        out = _io.BytesIO()
        bg.save(out, format="JPEG", quality=88)
        logger.info(
            f"[heygen] PiP composite created ({pip_w}×{pip_h} inset, {pip_position}) "
            f"on {_FRAME_W}×{_FRAME_H} frame"
        )
        return out.getvalue()
    except Exception as e:
        logger.warning(f"[heygen] PiP composite failed: {e}")
        return None


def upload_image_to_heygen(
    image_url: str,
    pip_composite: bool = False,
    bg_bytes: bytes | None = None,
    pip_position: str = "left",
) -> str | None:
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
            composite = _create_pip_composite(image_bytes, bg_bytes=bg_bytes, pip_position=pip_position)
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

    # Normalise pronunciation then truncate to HeyGen's 5000 char limit
    script = _normalize_tts(script)[:5000]

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
        "motion_prompt": _NEWS_ANCHOR_MOTION_PROMPT,
        "caption": True,
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


def _get_background_video_bytes(background_asset_id: str) -> bytes | None:
    """
    Return bytes for the studio background video.
    Priority:
      1. ./assets/{asset_id}.mp4  — local file named after the HeyGen asset ID
      2. disk cache               — previously downloaded via HeyGen API
      3. HeyGen asset API         — download and cache for next time
    """
    # 1. Local file named after the asset ID
    named_path = Path("./assets") / f"{background_asset_id}.mp4"
    if named_path.exists():
        logger.info(f"[heygen] Using local background video: {named_path}")
        return named_path.read_bytes()

    # 2. Disk cache (from a previous API download)
    cache_path = _BG_VIDEO_CACHE / f"{background_asset_id}.mp4"
    if cache_path.exists():
        logger.info(f"[heygen] Using cached background video: {cache_path}")
        return cache_path.read_bytes()

    # 3. Try HeyGen asset API
    if settings.HEYGEN_API_KEY:
        try:
            resp = requests.get(
                f"{HEYGEN_BASE_URL}/v1/asset/{background_asset_id}",
                headers={"x-api-key": settings.HEYGEN_API_KEY},
                timeout=15,
            )
            if resp.ok:
                url = resp.json().get("data", {}).get("url") or resp.json().get("data", {}).get("download_url")
                if url:
                    dl = requests.get(url, timeout=120, headers={"User-Agent": "Mozilla/5.0"})
                    if dl.ok and dl.content:
                        _BG_VIDEO_CACHE.mkdir(parents=True, exist_ok=True)
                        cache_path.write_bytes(dl.content)
                        logger.info(f"[heygen] Downloaded background video from HeyGen ({len(dl.content):,} bytes) → {cache_path}")
                        return dl.content
        except Exception as e:
            logger.warning(f"[heygen] HeyGen asset download failed: {e}")

    logger.warning(f"[heygen] Background video not found for asset_id={background_asset_id} — video composite skipped")
    return None


def _upload_video_asset(video_bytes: bytes) -> str | None:
    """Upload an MP4 to HeyGen and return the asset_id."""
    try:
        resp = requests.post(
            f"{HEYGEN_UPLOAD_URL}/v1/asset",
            headers={
                "X-Api-Key": settings.HEYGEN_API_KEY,
                "Content-Type": "video/mp4",
            },
            data=video_bytes,
            timeout=120,
        )
        if not resp.ok:
            logger.warning(f"[heygen] Video asset upload failed: HTTP {resp.status_code} {resp.text[:200]}")
            return None
        asset_id = resp.json().get("data", {}).get("id")
        logger.info(f"[heygen] Uploaded video asset → {asset_id}")
        return asset_id
    except Exception as e:
        logger.warning(f"[heygen] _upload_video_asset error: {e}")
        return None


def delete_heygen_asset(asset_id: str) -> bool:
    """
    Delete a HeyGen asset by ID. Returns True on success.
    Also removes the local composite cache file so the asset won't be reused.
    """
    if not settings.HEYGEN_API_KEY or not asset_id:
        return False
    try:
        resp = requests.delete(
            f"{HEYGEN_BASE_URL}/v1/asset/{asset_id}",
            headers={"x-api-key": settings.HEYGEN_API_KEY},
            timeout=15,
        )
        if resp.ok:
            logger.info(f"[heygen] Deleted asset {asset_id}")
            return True
        logger.warning(f"[heygen] Delete asset {asset_id} failed: HTTP {resp.status_code} {resp.text[:200]}")
        return False
    except Exception as e:
        logger.warning(f"[heygen] delete_heygen_asset error: {e}")
        return False


def _get_ffmpeg_exe() -> str | None:
    """Return path to ffmpeg executable, trying system PATH then imageio-ffmpeg."""
    # System PATH
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return "ffmpeg"
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    # imageio-ffmpeg bundled binary
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            logger.info(f"[heygen] Using imageio-ffmpeg binary: {exe}")
            return exe
    except Exception:
        pass
    logger.warning("[heygen] FFmpeg not found (system PATH or imageio-ffmpeg) — skipping video composite")
    return None


def _create_broll_video_composite(
    broll_image_bytes: bytes, bg_video_bytes: bytes, pip_position: str = "left"
) -> bytes | None:
    """
    FFmpeg: overlay the b-roll image as a PiP in the upper corner of the background video.
    Produces a {_COMPOSITE_DURATION_S}s MP4 (HeyGen will loop it).
    Returns MP4 bytes, or None on failure.
    """
    ffmpeg = _get_ffmpeg_exe()
    if not ffmpeg:
        return None

    overlay_x = f"W-w-{_PIP_PADDING}" if pip_position == "right" else str(_PIP_PADDING)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp      = Path(tmpdir)
            bg_path  = tmp / "bg.mp4"
            img_path = tmp / "broll.jpg"
            out_path = tmp / "composite.mp4"

            bg_path.write_bytes(bg_video_bytes)
            img_path.write_bytes(broll_image_bytes)

            cmd = [
                ffmpeg, "-y",
                "-stream_loop", "-1", "-i", str(bg_path),
                "-loop", "1",        "-i", str(img_path),
                "-filter_complex",
                f"[1:v]scale={_PIP_W}:{_PIP_H}:force_original_aspect_ratio=decrease[pip];"
                f"[0:v][pip]overlay={overlay_x}:{_PIP_PADDING}",
                "-t", str(_COMPOSITE_DURATION_S),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-an",
                str(out_path),
            ]

            result = subprocess.run(cmd, capture_output=True, timeout=180)
            if result.returncode != 0:
                logger.warning(f"[heygen] FFmpeg failed (rc={result.returncode}): "
                               f"{result.stderr.decode(errors='replace')[-600:]}")
                return None

            video_bytes = out_path.read_bytes()
            logger.info(
                f"[heygen] FFmpeg PiP composite ({pip_position}): "
                f"{_COMPOSITE_DURATION_S}s MP4 ({len(video_bytes):,} bytes)"
            )
            return video_bytes
    except Exception as e:
        logger.warning(f"[heygen] _create_broll_video_composite error: {e}")
        return None


def _extract_frame_from_bytes(video_bytes: bytes, time_sec: float = 1.0) -> bytes | None:
    """
    Extract a single JPEG frame from in-memory video bytes using ffmpeg.
    Returns JPEG bytes, or None on failure.
    """
    ffmpeg = _get_ffmpeg_exe()
    if not ffmpeg or not video_bytes:
        return None
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            vid_path   = tmp / "input.mp4"
            frame_path = tmp / "frame.jpg"
            vid_path.write_bytes(video_bytes)
            cmd = [
                ffmpeg, "-y",
                "-ss", str(time_sec),
                "-i", str(vid_path),
                "-frames:v", "1",
                "-q:v", "2",
                str(frame_path),
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode == 0 and frame_path.exists():
                data = frame_path.read_bytes()
                logger.info(f"[heygen] Extracted frame from video bytes ({len(data):,} bytes)")
                return data
            logger.warning(f"[heygen] Frame extraction failed (rc={result.returncode})")
    except Exception as e:
        logger.warning(f"[heygen] _extract_frame_from_bytes error: {e}")
    return None


def _extract_video_frame(video_url: str, time_sec: float = 1.0) -> bytes | None:
    """
    Download a video clip (cached) and extract a single JPEG frame using ffmpeg.
    Returns JPEG bytes, or None on failure.
    """
    video_bytes = _download_broll_video(video_url)
    return _extract_frame_from_bytes(video_bytes, time_sec) if video_bytes else None


def _download_broll_video(video_url: str) -> bytes | None:
    """
    Download a b-roll video clip, caching it to disk to avoid re-downloading.
    Returns raw MP4 bytes, or None on failure.
    """
    url_hash = hashlib.md5(video_url.encode()).hexdigest()[:16]
    cache_path = _BROLL_VIDEO_DOWNLOAD_CACHE / f"{url_hash}.mp4"
    if cache_path.exists():
        logger.info(f"[heygen] Reusing cached b-roll video: {cache_path.name}")
        return cache_path.read_bytes()
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        if "pixabay.com" in video_url:
            headers["Referer"] = "https://pixabay.com/"
        resp = requests.get(video_url, timeout=60, headers=headers, stream=True)
        if not resp.ok:
            logger.warning(f"[heygen] Could not download b-roll video (HTTP {resp.status_code}): {video_url[:80]}")
            return None
        _BROLL_VIDEO_DOWNLOAD_CACHE.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                f.write(chunk)
        video_bytes = cache_path.read_bytes()
        logger.info(f"[heygen] Downloaded b-roll video ({len(video_bytes):,} bytes) → {cache_path.name}")
        return video_bytes
    except Exception as e:
        logger.warning(f"[heygen] Failed to download b-roll video: {e}")
        cache_path.unlink(missing_ok=True)
        return None


def _create_broll_video_composite_from_video(
    broll_video_bytes: bytes, bg_video_bytes: bytes, pip_position: str = "left"
) -> bytes | None:
    """
    FFmpeg: overlay a looping b-roll video clip as a PiP in the upper corner of the background video.
    Produces a {_COMPOSITE_DURATION_S}s MP4 (HeyGen will loop it). Audio is stripped.
    Returns MP4 bytes, or None on failure.
    """
    ffmpeg = _get_ffmpeg_exe()
    if not ffmpeg:
        return None

    overlay_x = f"W-w-{_PIP_PADDING}" if pip_position == "right" else str(_PIP_PADDING)

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            bg_path    = tmp / "bg.mp4"
            broll_path = tmp / "broll.mp4"
            out_path   = tmp / "composite.mp4"

            bg_path.write_bytes(bg_video_bytes)
            broll_path.write_bytes(broll_video_bytes)

            cmd = [
                ffmpeg, "-y",
                "-stream_loop", "-1", "-i", str(bg_path),
                "-stream_loop", "-1", "-i", str(broll_path),
                "-filter_complex",
                f"[1:v]scale={_PIP_W}:{_PIP_H}:force_original_aspect_ratio=decrease[pip];"
                f"[0:v][pip]overlay={overlay_x}:{_PIP_PADDING}",
                "-t", str(_COMPOSITE_DURATION_S),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-pix_fmt", "yuv420p",
                "-movflags", "+faststart",
                "-an",
                str(out_path),
            ]

            result = subprocess.run(cmd, capture_output=True, timeout=180)
            if result.returncode != 0:
                logger.warning(f"[heygen] FFmpeg (video pip) failed (rc={result.returncode}): "
                               f"{result.stderr.decode(errors='replace')[-600:]}")
                return None

            video_bytes = out_path.read_bytes()
            logger.info(
                f"[heygen] FFmpeg video PiP composite ({pip_position}): "
                f"{_COMPOSITE_DURATION_S}s MP4 ({len(video_bytes):,} bytes)"
            )
            return video_bytes
    except Exception as e:
        logger.warning(f"[heygen] _create_broll_video_composite_from_video error: {e}")
        return None


def create_broll_video_asset(
    media_url: str,
    background_asset_id: str,
    media_type: str = "image",
    pip_position: str = "left",
    bg_bytes_override: bytes | None = None,
) -> tuple[str | None, Path | None, bool]:
    """
    Full pipeline: download b-roll media + background video → FFmpeg PiP composite →
    upload to HeyGen → return (asset_id, cache_path, is_fresh_upload).

    media_type:        "image" (default) or "video"
    bg_bytes_override: pre-loaded background bytes (e.g. from prepare_enhanced_background)
                       — skips the _get_background_video_bytes call when provided.
    is_fresh_upload:   True when a new asset was uploaded this call; False when
                       the asset_id came from the local cache.
    Returns (None, None, False) on any failure.
    """
    # Cache key includes media_type and pip_position to avoid collisions
    url_hash   = hashlib.md5(f"{media_type}:{pip_position}:{media_url}".encode()).hexdigest()[:12]
    bg_prefix  = background_asset_id[:12] if background_asset_id else "default"
    cache_path = _BROLL_COMPOSITE_CACHE / f"{bg_prefix}_{url_hash}.asset_id"
    if cache_path.exists():
        cached_id = cache_path.read_text().strip()
        if cached_id:
            logger.info(f"[heygen] Reusing cached composite asset_id={cached_id}")
            return cached_id, cache_path, False

    # Get background video (use caller-supplied bytes if available)
    bg_bytes = bg_bytes_override or _get_background_video_bytes(background_asset_id)
    if not bg_bytes:
        return None, None, False

    if media_type == "video":
        broll_bytes = _download_broll_video(media_url)
        if not broll_bytes:
            return None, None, False
        composite_bytes = _create_broll_video_composite_from_video(broll_bytes, bg_bytes, pip_position=pip_position)
    else:
        # Download b-roll image
        try:
            img_resp = requests.get(media_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            if not img_resp.ok or not img_resp.content:
                logger.warning(f"[heygen] Could not download b-roll image: {media_url[:80]}")
                return None, None, False
            ct = img_resp.headers.get("Content-Type", "").split(";")[0].strip()
            if not ct.startswith("image/"):
                logger.warning(f"[heygen] B-roll URL is not an image (Content-Type: {ct!r})")
                return None, None, False
            composite_bytes = _create_broll_video_composite(img_resp.content, bg_bytes, pip_position=pip_position)
        except Exception as e:
            logger.warning(f"[heygen] Failed to download b-roll image: {e}")
            return None, None, False

    if not composite_bytes:
        return None, None, False

    asset_id = _upload_video_asset(composite_bytes)
    if asset_id:
        _BROLL_COMPOSITE_CACHE.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(asset_id)
        return asset_id, cache_path, True
    return None, None, False


_AVATAR_OFFSET_X: dict[str, float] = {"left": -0.35, "center": 0.0, "right": 0.35}
_PIP_FROM_AVATAR:  dict[str, str]   = {"left": "right", "center": "left", "right": "left"}


_NEWS_ANCHOR_MOTION_PROMPT = (
    "Professional broadcast news anchor. Composed, confident posture. "
    "Subtle, deliberate hand gestures to emphasize key points. "
    "Natural head movements when transitioning between topics."
)

_CAPTION_STYLE = "classic"   # burned-in closed-caption style; "classic" = clean subtitle bar
_STUDIO_BG_COLOR = "#0d1b2a"       # deep navy — professional broadcast look for v3 full-screen style
_V3_COLOR_BG = {"type": "color", "value": _STUDIO_BG_COLOR}  # v3 uses "value" not "color"


def generate_video_multiscene(
    segments: list,
    avatar_id: str,
    voice_id: str,
    background_asset_id: str,
    title: str = "News Segment",
    voice_emotion: str = "",
    talking_style: str = "",
    expression: str = "",
    avatar_position: str = "center",
    bg_bytes_override: bytes | None = None,
    motion_prompt: str = _NEWS_ANCHOR_MOTION_PROMPT,
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

    pip_position = _PIP_FROM_AVATAR.get(avatar_position, "left")
    offset_x     = _AVATAR_OFFSET_X.get(avatar_position, 0.0)
    logger.info(f"[heygen] Avatar position={avatar_position!r}  offset_x={offset_x}  pip={pip_position!r}")

    # Load studio background frame once (image composite fallback)
    bg_frame = _load_bg_frame()

    video_inputs = []
    # Track freshly uploaded composite assets so the caller can delete them after render.
    # Each entry: {"asset_id": str, "cache_path": Path}
    uploaded_composites: list[dict] = []

    for seg in segments:
        script = (seg.get("script") or "").strip()
        if not script:
            continue

        video_url = (seg.get("video_url") or "").strip()
        image_url = (seg.get("image_url") or "").strip()

        if video_url:
            # B-roll is a video clip — FFmpeg composite only (no PIL fallback for video)
            asset_id, cache_path, is_fresh = create_broll_video_asset(
                video_url, background_asset_id, media_type="video",
                pip_position=pip_position, bg_bytes_override=bg_bytes_override,
            )
            if asset_id:
                if is_fresh:
                    uploaded_composites.append({"asset_id": asset_id, "cache_path": cache_path})
                background = {"type": "video", "video_asset_id": asset_id, "play_style": "loop"}
                logger.info(f"[heygen] Using FFmpeg video PiP composite (clip, {pip_position}): {asset_id}")
            else:
                logger.warning("[heygen] Video b-roll composite failed — using studio background")
                background = {"type": "video", "video_asset_id": background_asset_id, "play_style": "loop"}
        elif image_url:
            # B-roll is a still image — try FFmpeg composite, fall back to PIL
            asset_id, cache_path, is_fresh = create_broll_video_asset(
                image_url, background_asset_id, media_type="image",
                pip_position=pip_position, bg_bytes_override=bg_bytes_override,
            )
            if asset_id:
                if is_fresh:
                    uploaded_composites.append({"asset_id": asset_id, "cache_path": cache_path})
                background = {"type": "video", "video_asset_id": asset_id, "play_style": "loop"}
                logger.info(f"[heygen] Using FFmpeg video PiP composite (image, {pip_position}): {asset_id}")
            else:
                logger.info("[heygen] FFmpeg composite unavailable — falling back to image composite")
                asset_id = upload_image_to_heygen(
                    image_url, pip_composite=True, bg_bytes=bg_frame, pip_position=pip_position
                )
                if asset_id:
                    uploaded_composites.append({"asset_id": asset_id, "cache_path": None})
                    background = {"type": "image", "image_asset_id": asset_id}
                else:
                    logger.warning("[heygen] All b-roll compositing failed — using studio background")
                    background = {"type": "video", "video_asset_id": background_asset_id, "play_style": "loop"}
        else:
            background = {"type": "video", "video_asset_id": background_asset_id, "play_style": "loop"}

        character = {
            "type": "avatar",
            "avatar_id": avatar_id,
            "avatar_style": "normal",
            "matting": True,
            "offset": {"x": offset_x, "y": 0.0},
        }
        if talking_style:
            character["talking_style"] = talking_style
        if expression:
            character["expression"] = expression

        voice_obj = {
            "type": "text",
            "input_text": _normalize_tts(script)[:5000],
            "voice_id": voice_id,
        }
        if voice_emotion:
            voice_obj["emotion"] = voice_emotion

        video_inputs.append({
            "character": character,
            "voice": voice_obj,
            "background": background,
        })

    if not video_inputs:
        return {"error": "No valid segments to render", "video_id": None}

    import json as _json

    payload = {
        "video_inputs": video_inputs,
        "dimension": {"width": 1280, "height": 720},
        "title": title,
        "motion_prompt": motion_prompt,
        "caption": True,
    }

    logger.info(f"[heygen] Submitting {len(video_inputs)}-scene payload:\n{_json.dumps(payload, indent=2)}")

    try:
        response = requests.post(
            f"{HEYGEN_BASE_URL}/v2/video/generate",
            headers={"x-api-key": settings.HEYGEN_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if not response.ok:
            return {"error": f"HeyGen HTTP {response.status_code}: {response.text[:200]}", "video_id": None}

        data = response.json()
        video_id = data.get("data", {}).get("video_id") or data.get("video_id")
        logger.info(f"[heygen] Multi-scene video submitted: {video_id} ({len(video_inputs)} scenes)")
        return {
            "video_id": video_id,
            "status": "processing",
            "scene_count": len(video_inputs),
            "uploaded_composites": uploaded_composites,
        }

    except Exception as e:
        logger.error(f"[heygen] multiscene generate error: {e}", exc_info=True)
        return {"error": str(e), "video_id": None}


def _concatenate_segment_scripts(segments: list) -> str:
    """
    Concatenate all segment scripts into one continuous take (v3 avatar-type is
    single-scene only, so every v3 path needs this). Collapses whitespace so TTS
    doesn't stumble on embedded newlines, and truncates to HeyGen's 5000 char limit.
    """
    import re as _re2
    full_script = " ".join(
        _normalize_tts((seg.get("script") or "").strip())
        for seg in segments
        if (seg.get("script") or "").strip()
    )
    full_script = _re2.sub(r'\s+', ' ', full_script).strip()
    if len(full_script) > 5000:
        logger.warning(f"[heygen-v3] Script truncated from {len(full_script)} to 5000 chars")
        full_script = full_script[:5000]
    return full_script


def _make_anchor_scene_v3(
    script: str,
    avatar_id: str,
    voice_id: str,
    motion_prompt: str,
    voice_emotion: str = "",
) -> dict:
    """Build a v3 studio avatar_video scene (solid dark background)."""
    inp = {
        "type": "avatar",
        "avatar_id": avatar_id,
        "script": _normalize_tts(script)[:5000],
        "voice_id": voice_id,
        "background": _V3_COLOR_BG,
    }
    if voice_emotion:
        inp["voice_settings"] = {"emotion": voice_emotion}
    return {"type": "avatar_video", "input": inp}


def _make_image_scene_v3(
    script: str,
    image_url: str,
    voice_id: str,
    voice_emotion: str = "",
) -> dict:
    """Build a v3 studio image scene — full-screen b-roll with anchor voiceover."""
    scene: dict = {
        "type": "image",
        "source": {"type": "url", "url": image_url},
        "script": _normalize_tts(script)[:5000],
        "voice_id": voice_id,
    }
    if voice_emotion:
        scene["voice_settings"] = {"emotion": voice_emotion}
    return scene


def generate_video_multiscene_v3(
    segments: list,
    avatar_id: str,
    voice_id: str,
    background_asset_id: str,
    title: str = "News Segment",
    voice_emotion: str = "",
    talking_style: str = "",
    expression: str = "",
    avatar_position: str = "center",
    bg_bytes_override: bytes | None = None,
    motion_prompt: str = _NEWS_ANCHOR_MOTION_PROMPT,
) -> dict:
    """
    Submit a single-scene HeyGen video using the v3 Avatar API.

    Uses v3 type="avatar" (type="studio" requires a workspace feature flag not available on all plans).
    All segment scripts are concatenated into one continuous anchor take. The first available b-roll
    image is uploaded and used as the static background; falls back to solid dark color if none found.

    Gains over v2: Avatar V engine (higher fidelity rendering) + motion_prompt (natural body language).

    Returns {"video_id": "...", "status": "processing", "scene_count": 1, "uploaded_composites": [...]}
         or {"error": "...", "video_id": None}
    """
    import json as _json

    if not settings.HEYGEN_API_KEY:
        return {"error": "HEYGEN_API_KEY not configured", "video_id": None}

    full_script = _concatenate_segment_scripts(segments)
    if not full_script:
        return {"error": "No valid segments to render", "video_id": None}

    # v3 type:"avatar" only accepts background type "color" or "image" — video is rejected.
    # Strategy:
    #   1. Load the studio background PNG from disk (bg_bytes_override is MP4 — do not use it here)
    #   2. Find the first b-roll: prefer image URL; for video URL, extract a still frame
    #   3. PIL-composite the b-roll as a PiP onto the studio PNG
    #   4. Upload the resulting image → use as {"type": "image", "image_asset_id": ...}
    pip_position = _PIP_FROM_AVATAR.get(avatar_position, "left")
    uploaded_composites: list[dict] = []
    background: dict = _V3_COLOR_BG  # final fallback

    # Studio background: prefer PNG from disk; fall back to a frame extracted from
    # the show's background video (already cached locally from v2 renders).
    bg_frame: bytes | None = _load_bg_frame()
    if not bg_frame and background_asset_id:
        _bg_vid = _get_background_video_bytes(background_asset_id)
        if _bg_vid:
            bg_frame = _extract_frame_from_bytes(_bg_vid)
            logger.info(f"[heygen-v3] Extracted studio background frame from video asset ({background_asset_id[:12]}...)")

    # Find first b-roll — image preferred; extract frame from video if needed
    broll_image_bytes: bytes | None = None
    for _seg in segments:
        _iurl = (_seg.get("image_url") or "").strip()
        _vurl = (_seg.get("video_url") or "").strip()
        if _iurl:
            try:
                _r = requests.get(_iurl, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                if _r.ok and _r.content:
                    broll_image_bytes = _r.content
                    logger.info(f"[heygen-v3] B-roll image for PiP: {_iurl[:60]}")
            except Exception as _e:
                logger.warning(f"[heygen-v3] Failed to download b-roll image: {_e}")
            break
        if _vurl:
            broll_image_bytes = _extract_video_frame(_vurl)
            if broll_image_bytes:
                logger.info(f"[heygen-v3] Extracted frame from b-roll video: {_vurl[:60]}")
            break

    # Build the background image: PiP composite if b-roll available, else plain studio frame
    if bg_frame and broll_image_bytes:
        bg_image = _create_pip_composite(broll_image_bytes, bg_bytes=bg_frame, pip_position=pip_position) or bg_frame
    else:
        bg_image = bg_frame

    if bg_image:
        try:
            content_type = "image/png" if bg_image[:4] == b'\x89PNG' else "image/jpeg"
            _up = requests.post(
                f"{HEYGEN_UPLOAD_URL}/v1/asset",
                headers={"X-Api-Key": settings.HEYGEN_API_KEY, "Content-Type": content_type},
                data=bg_image,
                timeout=60,
            )
            if _up.ok:
                _aid = _up.json().get("data", {}).get("id")
                if _aid:
                    background = {"type": "image", "asset_id": _aid}
                    uploaded_composites.append({"asset_id": _aid, "cache_path": None})
                    logger.info(f"[heygen-v3] Background image uploaded → {_aid}")
                else:
                    logger.warning("[heygen-v3] Background upload returned no id — using dark color")
            else:
                logger.warning(f"[heygen-v3] Background upload failed (HTTP {_up.status_code}) — using dark color")
        except Exception as _e:
            logger.warning(f"[heygen-v3] Background upload error: {_e} — using dark color")
    else:
        logger.info("[heygen-v3] BROLL_BG_FRAME_PATH not configured — using dark color fallback")

    payload: dict = {
        "type": "avatar",
        "avatar_id": avatar_id,
        "script": full_script,
        "voice_id": voice_id,
        "background": background,
        "engine": {"type": "avatar_v"},
        "motion_prompt": motion_prompt,
        "resolution": "1080p",
        "aspect_ratio": "16:9",
        "title": title,
    }
    # v3 voice_settings has no "emotion" field — drop it to avoid render failure

    logger.info(f"[heygen-v3] Submitting v3 avatar payload ({len(full_script)} chars):\n{_json.dumps(payload, indent=2)}")

    try:
        response = requests.post(
            f"{HEYGEN_BASE_URL}/v3/videos",
            headers={"x-api-key": settings.HEYGEN_API_KEY, "Content-Type": "application/json"},
            json=payload,
            timeout=120,
        )
        if not response.ok:
            return {"error": f"HeyGen v3 HTTP {response.status_code}: {response.text[:200]}", "video_id": None}

        data = response.json()
        video_id = (
            data.get("data", {}).get("video_id")
            or data.get("data", {}).get("id")
            or data.get("video_id")
        )
        logger.info(f"[heygen-v3] Avatar video submitted: {video_id}")
        return {
            "video_id": video_id,
            "status": "processing",
            "scene_count": 1,
            "uploaded_composites": uploaded_composites,
        }

    except Exception as e:
        logger.error(f"[heygen-v3] avatar generate error: {e}", exc_info=True)
        return {"error": str(e), "video_id": None}


# ── Option D: v3 greenscreen avatar + local FFmpeg chromakey composite ─────────
# See HEYGEN_V3_MIGRATION_PLAN.md sec 4a/8. Keeps 100% of the pip_v2 background/PiP
# pipeline; only the avatar clip's origin changes (v3 greenscreen render instead of
# HeyGen's own v2 server-side matting).

_V3_CHROMAKEY_POLL_INTERVAL_S = 30
_V3_CHROMAKEY_MAX_POLL_ATTEMPTS = 60  # 30 minutes


def _poll_v3_video_sync(video_id: str) -> dict:
    """Blocking poll of a v3 video job until completed/failed/timeout. Returns the final `data` dict, or {"error": ...}."""
    time.sleep(15)
    for attempt in range(1, _V3_CHROMAKEY_MAX_POLL_ATTEMPTS + 1):
        try:
            resp = requests.get(
                f"{HEYGEN_BASE_URL}/v3/videos/{video_id}",
                headers={"x-api-key": settings.HEYGEN_API_KEY},
                timeout=30,
            )
            if not resp.ok:
                return {"error": f"HTTP {resp.status_code}: {resp.text[:200]}"}
            data = resp.json().get("data", {})
            status = data.get("status", "unknown")
            logger.info(f"[heygen-v3-chromakey] Poll {attempt}/{_V3_CHROMAKEY_MAX_POLL_ATTEMPTS} video {video_id}: {status}")
            if status == "completed":
                return data
            if status == "failed":
                detail = data.get("error") or data.get("msg") or "unknown"
                return {"error": f"HeyGen render failed: {detail}"}
        except Exception as e:
            logger.warning(f"[heygen-v3-chromakey] Poll error: {e}")
        if attempt < _V3_CHROMAKEY_MAX_POLL_ATTEMPTS:
            time.sleep(_V3_CHROMAKEY_POLL_INTERVAL_S)
    return {"error": f"Timed out waiting for video {video_id}"}


def _render_avatar_clip_v3_greenscreen(
    full_script: str,
    avatar_id: str,
    voice_id: str,
    engine: str,
    key_color: str,
    supports_motion_prompt: bool,
    supports_remove_background: bool,
    fit: str | None,
    motion_prompt: str,
    title: str,
) -> tuple[bytes | None, str | None]:
    """
    Submit + poll + download a single-scene v3 avatar clip against a solid
    chromakey-color background. Returns (clip_bytes, error) — clip_bytes is
    None on any failure, with error set to a human-readable message.
    """
    payload: dict = {
        "type": "avatar",
        "avatar_id": avatar_id,
        "script": full_script,
        "voice_id": voice_id,
        "background": {"type": "color", "value": key_color},
        "engine": {"type": engine},
        "resolution": "1080p",
        "aspect_ratio": "16:9",
        "title": title,
    }
    if supports_remove_background:
        # Without this, HeyGen has been observed to silently ignore the
        # requested background color and fall back to some default backdrop
        # instead (confirmed live 2026-07-27, Zayne Carter/avatar_iv) — the
        # explicit color alone is not sufficient. But it's rejected outright
        # (HTTP 400 "not trained for matting") for avatar_iii/studio_avatar
        # looks, which is why this is conditional, not a hardcoded True
        # (also confirmed live 2026-07-27, Shawn Green/Brandon Jones).
        # See HEYGEN_V3_MIGRATION_PLAN.md sec 4a.
        payload["remove_background"] = True
    if supports_motion_prompt:
        payload["motion_prompt"] = motion_prompt
    if fit:
        payload["fit"] = fit

    logger.info(
        f"[heygen-v3-chromakey] Submitting greenscreen avatar payload "
        f"({len(full_script)} chars, engine={engine}, fit={fit!r})"
    )

    try:
        resp = requests.post(
            f"{HEYGEN_BASE_URL}/v3/videos",
            headers={"x-api-key": settings.HEYGEN_API_KEY, "Content-Type": "application/json"},
            json=payload, timeout=60,
        )
        if not resp.ok:
            return None, f"HeyGen v3 HTTP {resp.status_code}: {resp.text[:200]}"
        data = resp.json().get("data", {})
        video_id = data.get("video_id") or data.get("id")
        if not video_id:
            return None, f"No video_id in HeyGen response: {resp.text[:200]}"
    except Exception as e:
        return None, f"Submit error: {e}"

    result = _poll_v3_video_sync(video_id)
    if "error" in result:
        return None, result["error"]

    video_url = result.get("video_url")
    if not video_url:
        return None, "Render completed but no video_url returned"

    try:
        dl = requests.get(video_url, timeout=180)
        if not dl.ok or not dl.content:
            return None, f"Failed to download rendered clip: HTTP {dl.status_code}"
        logger.info(f"[heygen-v3-chromakey] Downloaded greenscreen clip ({len(dl.content):,} bytes)")
        return dl.content, None
    except Exception as e:
        return None, f"Download error: {e}"


def _build_v3_chromakey_background(
    segments: list,
    background_asset_id: str,
    bg_bytes_override: bytes | None,
    pip_position: str,
) -> bytes | None:
    """
    Build the background these greenscreen clips composite onto — the same studio
    backdrop pip_v2 uses, with the first b-roll item found (if any) as a looping
    PiP inset. Unlike pip_v2's per-scene backgrounds, this is a single continuous
    background for the whole take, since v3 avatar-type is single-scene only — a
    known, deliberate Phase 1 limitation (see HEYGEN_V3_MIGRATION_PLAN.md sec 8).
    """
    bg_bytes = bg_bytes_override or _get_background_video_bytes(background_asset_id)
    if not bg_bytes:
        return None

    for seg in segments:
        video_url = (seg.get("video_url") or "").strip()
        image_url = (seg.get("image_url") or "").strip()
        if video_url:
            broll_bytes = _download_broll_video(video_url)
            if broll_bytes:
                composite = _create_broll_video_composite_from_video(broll_bytes, bg_bytes, pip_position=pip_position)
                if composite:
                    return composite
            break
        if image_url:
            try:
                r = requests.get(image_url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                if r.ok and r.content:
                    composite = _create_broll_video_composite(r.content, bg_bytes, pip_position=pip_position)
                    if composite:
                        return composite
            except Exception as e:
                logger.warning(f"[heygen-v3-chromakey] B-roll image download failed: {e}")
            break

    return bg_bytes


def _chromakey_composite(
    avatar_clip_bytes: bytes,
    background_bytes: bytes,
    key_color: str,
    avatar_position: str,
) -> bytes | None:
    """
    FFmpeg: chromakey the greenscreen avatar clip and overlay it onto the
    (looping) background, shifted per avatar_position using the same
    _AVATAR_OFFSET_X fractions pip_v2 already uses. Returns final MP4 bytes,
    or None on failure.
    """
    ffmpeg = _get_ffmpeg_exe()
    if not ffmpeg:
        return None

    key_hex = key_color.lstrip("#").lower()
    try:
        r, g, b = int(key_hex[0:2], 16), int(key_hex[2:4], 16), int(key_hex[4:6], 16)
    except ValueError:
        r, g, b = 0, 255, 0
    despill_type = "green" if g >= b else "blue"
    offset_x = _AVATAR_OFFSET_X.get(avatar_position, 0.0)
    overlay_x = f"{offset_x}*W" if offset_x else "0"

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            avatar_path = tmp / "avatar.mp4"
            bg_path = tmp / "bg.mp4"
            out_path = tmp / "composite.mp4"
            avatar_path.write_bytes(avatar_clip_bytes)
            bg_path.write_bytes(background_bytes)

            cmd = [
                ffmpeg, "-y",
                "-stream_loop", "-1", "-i", str(bg_path),
                "-i", str(avatar_path),
                "-filter_complex",
                f"[0:v]scale={_FRAME_W}:{_FRAME_H}[bg];"
                f"[1:v]scale={_FRAME_W}:{_FRAME_H},chromakey=0x{key_hex}:0.10:0.15,despill=type={despill_type}[keyed];"
                f"[bg][keyed]overlay={overlay_x}:0:shortest=1[out]",
                "-map", "[out]", "-map", "1:a",
                "-c:v", "libx264", "-preset", "fast", "-crf", "20",
                "-c:a", "aac",
                str(out_path),
            ]
            result = subprocess.run(cmd, capture_output=True, timeout=600)
            if result.returncode != 0:
                logger.warning(
                    f"[heygen-v3-chromakey] FFmpeg composite failed (rc={result.returncode}): "
                    f"{result.stderr.decode(errors='replace')[-800:]}"
                )
                return None
            data = out_path.read_bytes()
            logger.info(f"[heygen-v3-chromakey] Composite created ({len(data):,} bytes)")
            return data
    except Exception as e:
        logger.warning(f"[heygen-v3-chromakey] _chromakey_composite error: {e}")
        return None


def _upload_final_composite(video_bytes: bytes) -> tuple[str | None, str | None]:
    """
    Upload the final chromakey composite to HeyGen and return (asset_id, url).

    Unlike _upload_video_asset() (used for background/b-roll composites, which
    only ever need the asset_id back), this needs a public URL too — and the
    upload response already contains one directly. A follow-up
    GET /v1/asset/{id} on api.heygen.com 404s for assets uploaded this way
    (confirmed live 2026-07-27) — the upload host (upload.heygen.com) and the
    read host (api.heygen.com) don't share that lookup, so don't round-trip
    through it.
    """
    try:
        resp = requests.post(
            f"{HEYGEN_UPLOAD_URL}/v1/asset",
            headers={"X-Api-Key": settings.HEYGEN_API_KEY, "Content-Type": "video/mp4"},
            data=video_bytes,
            timeout=120,
        )
        if not resp.ok:
            logger.warning(
                f"[heygen-v3-chromakey] Final composite upload failed: "
                f"HTTP {resp.status_code} {resp.text[:200]}"
            )
            return None, None
        data = resp.json().get("data", {})
        asset_id = data.get("id")
        url = data.get("url")
        logger.info(f"[heygen-v3-chromakey] Final composite uploaded → {asset_id} ({url})")
        return asset_id, url
    except Exception as e:
        logger.warning(f"[heygen-v3-chromakey] _upload_final_composite error: {e}")
        return None, None


def generate_video_multiscene_v3_chromakey(
    segments: list,
    avatar_id: str,
    voice_id: str,
    background_asset_id: str,
    title: str = "News Segment",
    voice_emotion: str = "",
    talking_style: str = "",
    expression: str = "",
    avatar_position: str = "center",
    bg_bytes_override: bytes | None = None,
    motion_prompt: str = _NEWS_ANCHOR_MOTION_PROMPT,
) -> dict:
    """
    Option D: render the avatar via v3 against an explicit chromakey background,
    then composite it locally with FFmpeg onto the same studio background/PiP
    pipeline pip_v2 already uses. See HEYGEN_V3_MIGRATION_PLAN.md sec 4a/8.

    Unlike the other generate_* functions, this does the full render + download +
    composite synchronously and returns status="completed" directly — there is no
    separate HeyGen render job left to poll for the final output. Callers should
    skip their usual poll-until-complete step when they see this status.

    voice_emotion/talking_style/expression are accepted for call-signature parity
    with generate_video_multiscene/generate_video_multiscene_v3 but unused here —
    v3's avatar endpoint has no equivalent fields (voice_settings.emotion is
    rejected; talking_style/expression are talking_photo-only v2 concepts).

    Returns {"video_id", "status": "completed", "video_url", "thumbnail_url": "",
             "scene_count", "uploaded_composites": []} or {"error": ..., "video_id": None}.
    """
    from config.anchors import get_look_by_avatar_id

    if not settings.HEYGEN_API_KEY:
        return {"error": "HEYGEN_API_KEY not configured", "video_id": None}

    look = get_look_by_avatar_id(avatar_id)
    if look and look.v3_unsupported:
        return {
            "error": (
                f"Avatar {avatar_id} is flagged v3_unsupported (see "
                f"HEYGEN_V3_MIGRATION_PLAN.md sec 4a) — use pip_v2 for this anchor "
                f"until resolved"
            ),
            "video_id": None,
        }

    engine = look.v3_engine if look else "avatar_iv"
    supports_motion_prompt = look.v3_supports_motion_prompt if look else True
    supports_remove_background = look.v3_supports_remove_background if look else True
    fit = look.v3_fit if look else None
    key_color = look.v3_key_color if look else "#00FF00"

    full_script = _concatenate_segment_scripts(segments)
    if not full_script:
        return {"error": "No valid segments to render", "video_id": None}

    pip_position = _PIP_FROM_AVATAR.get(avatar_position, "left")

    logger.info(
        f"[heygen-v3-chromakey] avatar={avatar_id} engine={engine} "
        f"motion_prompt={supports_motion_prompt} remove_background={supports_remove_background} "
        f"fit={fit!r} key={key_color}"
    )

    clip_bytes, err = _render_avatar_clip_v3_greenscreen(
        full_script, avatar_id, voice_id, engine, key_color,
        supports_motion_prompt, supports_remove_background, fit, motion_prompt, title,
    )
    if err:
        return {"error": f"Greenscreen render failed: {err}", "video_id": None}

    background_bytes = _build_v3_chromakey_background(
        segments, background_asset_id, bg_bytes_override, pip_position
    )
    if not background_bytes:
        return {"error": "Could not build background video for chromakey composite", "video_id": None}

    composite_bytes = _chromakey_composite(clip_bytes, background_bytes, key_color, avatar_position)
    if not composite_bytes:
        return {"error": "FFmpeg chromakey composite failed", "video_id": None}

    asset_id, video_url = _upload_final_composite(composite_bytes)
    if not asset_id or not video_url:
        return {"error": "Failed to upload final composite to HeyGen", "video_id": None}

    logger.info(f"[heygen-v3-chromakey] Final composite ready: {video_url}")
    return {
        "video_id": asset_id,
        "status": "completed",
        "video_url": video_url,
        "thumbnail_url": "",
        "scene_count": len(segments),
        "uploaded_composites": [],
    }


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
            f"{HEYGEN_BASE_URL}/v3/videos/{video_id}",
            headers={"x-api-key": settings.HEYGEN_API_KEY},
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

        raw_data = response.json().get("data", [])
        avatars = raw_data if isinstance(raw_data, list) else raw_data.get("avatars", [])
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

        raw_data = response.json().get("data", {})
        voices = raw_data if isinstance(raw_data, list) else raw_data.get("voices", [])
        # Filter to English voices only for brevity
        english = [
            {"voice_id": v.get("voice_id"), "name": v.get("name"), "language": v.get("language"), "gender": v.get("gender")}
            for v in voices if "en" in (v.get("language") or "").lower()
        ]
        return json.dumps({"voices": english, "count": len(english)})
    except Exception as e:
        return json.dumps({"error": str(e)})
