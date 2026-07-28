from langchain.tools import tool
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage
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
_OUTRO_PATH = Path("./assets/outro.mp4")
# Target resolution — match HeyGen's native output; promo/outro scale down to fit
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


def assemble_final_video(broadcast_path: Path) -> Path | None:
    """
    Assemble the final broadcast video:  [promo] + broadcast + [outro]
    Promo and outro are optional — each is included only if its file exists in ./assets/.
    All clips are scaled to _OUT_W × _OUT_H at _OUT_FPS (straight scale, no letterbox —
    all assets should be 16:9).
    Returns the path to the assembled file, or None if nothing to assemble / FFmpeg fails.
    """
    has_promo = _PROMO_PATH.exists()
    has_outro = _OUTRO_PATH.exists()

    if not has_promo and not has_outro:
        logger.info("[video_editor] No promo or outro found — skipping assembly")
        return None

    ffmpeg = _get_ffmpeg()
    if not ffmpeg:
        logger.warning("[video_editor] FFmpeg not found — skipping assembly")
        return None

    # Build ordered clip list: promo (opt) → broadcast → outro (opt)
    clips: list[Path] = []
    if has_promo:
        clips.append(_PROMO_PATH)
    clips.append(broadcast_path)
    if has_outro:
        clips.append(_OUTRO_PATH)

    n = len(clips)
    scale = f"scale={_OUT_W}:{_OUT_H},fps={_OUT_FPS},setsar=1"

    # Build filter_complex dynamically for n inputs
    parts = []
    for i in range(n):
        parts.append(f"[{i}:v]{scale}[v{i}]")
        parts.append(f"[{i}:a]aresample=44100,aformat=sample_fmts=fltp[a{i}]")
    stream_pairs = "".join(f"[v{i}][a{i}]" for i in range(n))
    parts.append(f"{stream_pairs}concat=n={n}:v=1:a=1[vout][aout]")
    filter_complex = ";".join(parts)

    out_path = broadcast_path.parent / f"final_{broadcast_path.stem}.mp4"
    cmd = [ffmpeg, "-y"]
    for clip in clips:
        cmd += ["-i", str(clip)]
    cmd += [
        "-filter_complex", filter_complex,
        "-map", "[vout]", "-map", "[aout]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]

    parts_desc = " + ".join(
        ("promo" if c == _PROMO_PATH else "outro" if c == _OUTRO_PATH else "broadcast")
        for c in clips
    )
    logger.info(f"[video_editor] Assembling: {parts_desc} → {out_path.name}")
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            logger.warning(
                f"[video_editor] FFmpeg assembly failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace')[-800:]}"
            )
            return None
        size = out_path.stat().st_size
        logger.info(f"[video_editor] Assembled final video ({size:,} bytes): {out_path.name}")
        return out_path
    except Exception as e:
        logger.warning(f"[video_editor] assemble_final_video error: {e}")
        return None


# Keep old name as alias so nothing else breaks
prepend_promo = assemble_final_video


def _get_video_duration_seconds(path: Path) -> float | None:
    """Read the container duration via ffmpeg's stderr banner (imageio-ffmpeg bundles
    ffmpeg but not ffprobe, so this avoids depending on a separate ffprobe binary)."""
    ffmpeg = _get_ffmpeg()
    if not ffmpeg:
        return None
    try:
        result = subprocess.run([ffmpeg, "-i", str(path)], capture_output=True, timeout=30)
        stderr = result.stderr.decode(errors="replace")
        m = re.search(r"Duration:\s*(\d+):(\d+):(\d+\.\d+)", stderr)
        if not m:
            return None
        h, mn, s = m.groups()
        return int(h) * 3600 + int(mn) * 60 + float(s)
    except Exception as e:
        logger.warning(f"[video_editor] Could not read video duration: {e}")
        return None


def check_visual_qa(video_path: Path, num_frames: int = 3) -> dict:
    """
    Phase 7.3 (SELF_IMPROVEMENT_ROADMAP.md) — sample frames from the final composited
    video and ask a vision-capable LLM whether any show a watermark, logo, or visual
    artifact that shouldn't be there. Deliberately scoped to the defect class already
    known to occur (the avatar_iii "Veo" watermark — see HEYGEN_V3_MIGRATION_PLAN.md
    sec 4a/10) rather than open-ended anomaly detection.

    Returns {"flagged": bool, "notes": str}. Never raises — a QA check that crashes the
    pipeline is worse than one that silently skips, so any failure here returns
    flagged=False with an explanatory note; callers should treat that as "couldn't
    check", not "confirmed clean". This never blocks publish on its own — see the
    caller in agents/video_editor/agent.py for how a flag is surfaced instead.
    """
    ffmpeg = _get_ffmpeg()
    if not ffmpeg:
        return {"flagged": False, "notes": "FFmpeg not available — visual QA skipped"}

    duration = _get_video_duration_seconds(video_path)
    if not duration or duration <= 0:
        return {"flagged": False, "notes": "Could not determine video duration — visual QA skipped"}

    # Evenly spaced samples, avoiding the very first/last instant (fade-in/out).
    timestamps = [duration * (i + 1) / (num_frames + 1) for i in range(num_frames)]

    import base64
    images_b64 = []
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            for i, ts in enumerate(timestamps):
                frame_path = Path(tmpdir) / f"frame_{i}.jpg"
                subprocess.run(
                    [ffmpeg, "-y", "-ss", str(ts), "-i", str(video_path),
                     "-frames:v", "1", "-q:v", "3", str(frame_path)],
                    capture_output=True, timeout=30,
                )
                if frame_path.exists():
                    images_b64.append(base64.b64encode(frame_path.read_bytes()).decode("ascii"))
    except Exception as e:
        logger.warning(f"[video_qa] Frame extraction failed: {e}")
        return {"flagged": False, "notes": f"Frame extraction failed: {e}"}

    if not images_b64:
        return {"flagged": False, "notes": "No frames extracted — visual QA skipped"}

    try:
        llm = ChatOpenAI(
            model=settings.model_for("video_editor"), temperature=0.0,
            openai_api_key=settings.OPENAI_API_KEY,
        )
        content: list = [{
            "type": "text",
            "text": (
                "These are frames sampled from a finished broadcast news video, ready to "
                "publish. Look carefully at each frame, especially the corners and edges. "
                "Does any frame show a watermark, logo, or visual artifact that should not "
                "be in a professional news broadcast — e.g. a third-party AI-tool watermark, "
                "a visible chromakey fringe/halo around the anchor, or an obviously wrong or "
                "mismatched background? Respond with exactly one line starting with "
                "'FLAGGED: yes' or 'FLAGGED: no', followed by a one-sentence explanation."
            ),
        }]
        for b64 in images_b64:
            content.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})

        response = llm.invoke([HumanMessage(content=content)])
        text = (response.content or "").strip()
        upper = text.upper()
        if "FLAGGED: YES" in upper:
            flagged = True
        elif "FLAGGED: NO" in upper:
            flagged = False
        else:
            # Model didn't follow the expected format — err toward a human glancing at
            # it rather than silently assuming clean.
            flagged = True
        return {"flagged": flagged, "notes": text}
    except Exception as e:
        logger.warning(f"[video_qa] Vision check failed: {e}")
        return {"flagged": False, "notes": f"Vision check failed: {e}"}


_LOWER_THIRD_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


def _load_lower_third_font(size: int):
    from PIL import ImageFont
    for path in _LOWER_THIRD_FONT_CANDIDATES:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def _render_lower_third_png(text: str, width: int = _OUT_W, height: int = _OUT_H) -> Path:
    """Render a single lower-third graphic (dark bar + accent stripe + white text) as a
    transparent PNG the same size as the broadcast frame, for FFmpeg to overlay in place."""
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    bar_height = int(height * 0.14)
    bar_bottom_margin = int(height * 0.08)
    bar_top = height - bar_height - bar_bottom_margin
    draw.rectangle([0, bar_top, width, bar_top + bar_height], fill=(12, 12, 14, 210))
    draw.rectangle([0, bar_top, 8, bar_top + bar_height], fill=(198, 30, 30, 255))

    font = _load_lower_third_font(size=int(bar_height * 0.42))
    draw.text((28, bar_top + bar_height // 2), text, font=font, fill=(255, 255, 255, 255), anchor="lm")

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name)
    tmp.close()
    return Path(tmp.name)


_GRAPHIC_DISPLAY_SECONDS = 4.5


def render_graphic_overlays(video_path: Path, cues: list[tuple[str, float]]) -> Path | None:
    """
    Burn [GRAPHIC: ...] cues into the video as lower-third overlays. `cues` is a list of
    (text, fractional_position) tuples from extract_graphic_cues_with_position — timing is
    a deliberate approximation (see that function's docstring), not exact speech alignment.
    Returns the output path, or None if there's nothing to render or FFmpeg is unavailable.
    """
    if not cues:
        return None

    ffmpeg = _get_ffmpeg()
    if not ffmpeg:
        logger.warning("[video_editor] FFmpeg not found — skipping graphic overlays")
        return None

    duration = _get_video_duration_seconds(video_path)
    if not duration:
        logger.warning("[video_editor] Could not determine video duration — skipping graphic overlays")
        return None

    png_paths: list[Path] = []
    timings: list[tuple[float, float]] = []
    for text, frac in cues:
        start = max(0.0, min(frac, 1.0)) * duration
        end = min(start + _GRAPHIC_DISPLAY_SECONDS, duration)
        if end - start < 0.5:
            continue
        try:
            png_paths.append(_render_lower_third_png(text))
            timings.append((start, end))
        except Exception as e:
            logger.warning(f"[video_editor] Could not render graphic '{text[:40]}': {e}")

    if not png_paths:
        return None

    out_path = video_path.parent / f"gfx_{video_path.stem}.mp4"
    cmd = [ffmpeg, "-y", "-i", str(video_path)]
    for p in png_paths:
        cmd += ["-i", str(p)]

    filter_parts = []
    prev = "0:v"
    for i, (start, end) in enumerate(timings, 1):
        label = f"g{i}"
        filter_parts.append(f"[{prev}][{i}:v]overlay=0:0:enable='between(t,{start:.2f},{end:.2f})'[{label}]")
        prev = label

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{prev}]",
        "-map", "0:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]

    logger.info(f"[video_editor] Rendering {len(png_paths)} graphic overlay(s)")
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            logger.warning(
                f"[video_editor] Graphic overlay FFmpeg failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace')[-800:]}"
            )
            return None
        size = out_path.stat().st_size
        logger.info(f"[video_editor] Graphic overlays rendered ({size:,} bytes): {out_path.name}")
        return out_path
    except Exception as e:
        logger.warning(f"[video_editor] render_graphic_overlays error: {e}")
        return None
    finally:
        for p in png_paths:
            try:
                p.unlink(missing_ok=True)
            except Exception:
                pass


def compose_foreground_layers(video_path: Path, layers: list) -> Path | None:
    """
    FFmpeg: composite n foreground overlay images/videos on top of the broadcast video.
    Preserves the original video duration and audio. Outputs to fg_{stem}.mp4 in the
    same directory. Returns the output path, or None if layers is empty or FFmpeg fails.
    """
    if not layers:
        return None

    ffmpeg = _get_ffmpeg()
    if not ffmpeg:
        logger.warning("[video_editor] FFmpeg not found — skipping foreground layers")
        return None

    _project_root = Path(__file__).resolve().parent.parent
    resolved: list[tuple] = []
    for l in layers:
        src = Path(l.source)
        if not src.is_absolute():
            src = _project_root / l.source
        if src.exists():
            resolved.append((l, src))
        else:
            logger.warning(f"[video_editor] Foreground layer file not found: {src}")

    if not resolved:
        logger.warning("[video_editor] No foreground layer files found — skipping")
        return None

    out_path = video_path.parent / f"fg_{video_path.stem}.mp4"

    cmd = [ffmpeg, "-y", "-i", str(video_path)]
    for layer, src in resolved:
        if src.suffix.lower() in (".mp4", ".mov", ".webm"):
            cmd += ["-stream_loop", "-1", "-i", str(src)]
        else:
            cmd += ["-loop", "1", "-i", str(src)]

    def _scale(layer) -> str:
        if layer.width and layer.height:
            return f"scale={layer.width}:{layer.height}"
        if layer.width:
            return f"scale={layer.width}:-2"
        if layer.height:
            return f"scale=-2:{layer.height}"
        return "scale=iw:ih"

    filter_parts = []
    prev = "0:v"
    for i, (layer, _) in enumerate(resolved, 1):
        s_label = f"s{i}"
        o_label = f"o{i}"
        filter_parts.append(f"[{i}:v]{_scale(layer)}[{s_label}]")
        filter_parts.append(f"[{prev}][{s_label}]overlay={layer.x}:{layer.y}[{o_label}]")
        prev = o_label

    cmd += [
        "-filter_complex", ";".join(filter_parts),
        "-map", f"[{prev}]",
        "-map", "0:a",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart",
        str(out_path),
    ]

    layers_desc = ", ".join(src.name for _, src in resolved)
    logger.info(f"[video_editor] Compositing foreground layers: {layers_desc}")
    try:
        result = subprocess.run(cmd, capture_output=True, timeout=600)
        if result.returncode != 0:
            logger.warning(
                f"[video_editor] Foreground layers FFmpeg failed (rc={result.returncode}): "
                f"{result.stderr.decode(errors='replace')[-800:]}"
            )
            return None
        size = out_path.stat().st_size
        logger.info(f"[video_editor] Foreground composite done ({size:,} bytes): {out_path.name}")
        return out_path
    except Exception as e:
        logger.warning(f"[video_editor] compose_foreground_layers error: {e}")
        return None


def _download_video_impl(
    url: str,
    filename: Optional[str] = None,
    directory: Optional[str] = None,
) -> str:
    """Plain-Python core of download_video (not an LLM tool) — called directly by
    agents/video_editor/agent.py's deterministic extraction path as well as by the
    @tool-wrapped version below, so a fresh video always gets downloaded whether or
    not the LLM tool-calling agent successfully invokes the tool itself."""
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
    return _download_video_impl(url, filename, directory)


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


def extract_graphic_cues_with_position(script: str) -> list[tuple[str, float]]:
    """
    Plain-Python variant of extract_graphic_cues (not an LLM tool) that also returns
    each cue's fractional position (0.0-1.0) in the script text — used to approximate
    when the cue should appear on screen once the video is rendered. HeyGen does not
    return word-level caption timing, so exact speech alignment isn't available; this
    proportional estimate (cue's character offset / total script length, mapped onto
    the video's actual duration) is a deliberate approximation, not a precise sync.
    """
    script_len = len(script) or 1
    return [
        (m.group(1).strip(), m.start() / script_len)
        for m in re.finditer(r'\[GRAPHIC:\s*([^\]]+)\]', script, re.IGNORECASE)
    ]


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
