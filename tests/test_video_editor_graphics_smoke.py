"""
Real (non-mocked) smoke test for the Phase 6 graphic overlay pipeline. Generates a
tiny synthetic test clip with the actual bundled FFmpeg binary (no external assets
needed), then runs render_graphic_overlays and _get_video_duration_seconds against
it for real. This is the one place that proves the overlay filter chain and the
duration-parsing regex actually work against this system's real FFmpeg output —
the rest of tests/test_video_editor_graphics.py only exercises the Python control
flow with subprocess mocked out.

Slower than the rest of the suite (a couple of real encodes) but still seconds,
not minutes — not worth gating out of the default run.
"""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import tools.video_tools as video_tools
from tools.video_tools import render_graphic_overlays, _get_video_duration_seconds, _get_ffmpeg

_ffmpeg_available = _get_ffmpeg() is not None


def _make_synthetic_clip(path: Path, duration: float = 6.0) -> None:
    ffmpeg = _get_ffmpeg()
    import subprocess
    cmd = [
        ffmpeg, "-y",
        "-f", "lavfi", "-i", f"color=c=blue:s=1280x720:d={duration}:r=30",
        "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
        "-t", str(duration),
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
        "-c:a", "aac",
        "-shortest",
        str(path),
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=60)
    if result.returncode != 0:
        pytest.fail(f"Could not generate synthetic test clip: {result.stderr.decode(errors='replace')[-500:]}")


@pytest.mark.skipif(not _ffmpeg_available, reason="FFmpeg not available in this environment")
def test_duration_lookup_matches_real_ffmpeg_output(tmp_path):
    clip = tmp_path / "synthetic.mp4"
    _make_synthetic_clip(clip, duration=6.0)

    duration = _get_video_duration_seconds(clip)
    assert duration is not None
    assert 5.5 <= duration <= 6.5


@pytest.mark.skipif(not _ffmpeg_available, reason="FFmpeg not available in this environment")
def test_render_graphic_overlays_end_to_end_with_real_ffmpeg(tmp_path):
    clip = tmp_path / "synthetic.mp4"
    _make_synthetic_clip(clip, duration=6.0)

    result = render_graphic_overlays(clip, [("Breaking News Test", 0.1), ("Second Cue Test", 0.6)])

    assert result is not None
    assert result.exists()
    assert result.stat().st_size > 1000  # not an empty/truncated file

    # Duration should be preserved — overlays composite in place, they don't trim/extend.
    out_duration = _get_video_duration_seconds(result)
    assert out_duration is not None
    assert 5.5 <= out_duration <= 6.5


@pytest.mark.skipif(not _ffmpeg_available, reason="FFmpeg not available in this environment")
def test_lower_third_png_renders_expected_size(tmp_path):
    png_path = video_tools._render_lower_third_png("Test Headline Text")
    try:
        from PIL import Image
        with Image.open(png_path) as img:
            img.load()  # force the pixel data to load now, so the file handle can close
            assert img.size == (video_tools._OUT_W, video_tools._OUT_H)
            assert img.mode == "RGBA"
    finally:
        png_path.unlink(missing_ok=True)
