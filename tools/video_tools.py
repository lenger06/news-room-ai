from langchain.tools import tool
from typing import Optional
import logging
import requests
import json
import re
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime
from config.settings import settings

logger = logging.getLogger(__name__)

_PROMO_PATH = Path("./assets/promo_with_audio.mp4")
# Target resolution — match HeyGen's output so the promo scales down to fit
_OUT_W, _OUT_H, _OUT_FPS = 1280, 720, 30


def _get_ffmpeg() -> str | None:
    """Return the FFmpeg executable path (system PATH or imageio-ffmpeg bundle)."""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return "ffmpeg"
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    try:
        import imageio_ffmpeg
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if exe:
            return exe
    except Exception:
        pass
    return None


def prepend_promo(broadcast_path: Path) -> Path | None:
    """
    Concatenate promo_with_audio.mp4 + broadcast MP4 into a single final video.
    Both clips are scaled/padded to _OUT_W × _OUT_H at _OUT_FPS before concat.
    Returns the path to the final file, or None if the promo is missing / FFmpeg fails.
    """
    if not _PROMO_PATH.exists():
        logger.info(f"[video_editor] No promo found at {_PROMO_PATH} — skipping intro prepend")
        return None

    ffmpeg = _get_ffmpeg()
    if not ffmpeg:
        logger.warning("[video_editor] FFmpeg not found — skipping intro prepend")
        return None

    out_path = broadcast_path.parent / f"final_{broadcast_path.stem}.mp4"

    # Scale both clips to the same resolution (no padding — both are 16:9),
    # normalise frame rate and audio sample rate, then hard-concat.
    scale = f"scale={_OUT_W}:{_OUT_H},fps={_OUT_FPS},setsar=1"
    filter_complex = (
        f"[0:v]{scale}[v0];"
        f"[1:v]{scale}[v1];"
        f"[0:a]aresample=44100,aformat=sample_fmts=fltp[a0];"
        f"[1:a]aresample=44100,aformat=sample_fmts=fltp[a1];"
        f"[v0][a0][v1][a1]concat=n=2:v=1:a=1[vout][aout]"
    )
    cmd = [
        ffmpeg, "-y",
        "-i", str(_PROMO_PATH),
        "-i", str(broadcast_path),
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]

    logger.info(f"[video_editor] Prepending promo → {out_path.name}")
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            logger.warning(
                f"[video_editor] FFmpeg concat failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace')[-800:]}"
            )
            return None
        size = out_path.stat().st_size
        logger.info(f"[video_editor] Final video with intro: {out_path.name} ({size:,} bytes)")
        return out_path
    except Exception as e:
        logger.warning(f"[video_editor] prepend_promo error: {e}")
        return None


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
