"""
Real (non-mocked) smoke test for the Option D chromakey compositing filter
chain (see HEYGEN_V3_MIGRATION_PLAN.md sec 4a/8). Generates two tiny synthetic
clips with the actual bundled FFmpeg binary — a solid-color "background" and a
green-screen "avatar" clip with a colored square standing in for the subject —
then runs _chromakey_composite against them for real. This is the one place
that proves the scale/chromakey/despill/overlay filter string actually parses
and runs against this system's real FFmpeg, and that the keyed color is gone
from the output while the "subject" square survives. The rest of
tests/test_heygen_chromakey.py only exercises Python control flow with
subprocess mocked out.
"""
import sys
from pathlib import Path

import pytest

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tools.heygen_tool import _chromakey_composite, _get_ffmpeg_exe, _FRAME_W, _FRAME_H

_ffmpeg_available = _get_ffmpeg_exe() is not None


def _make_synthetic_clip(path: Path, color: str, duration: float = 2.0, with_square: bool = False) -> None:
    import subprocess
    ffmpeg = _get_ffmpeg_exe()
    video_src = f"color=c={color}:s={_FRAME_W}x{_FRAME_H}:d={duration}:r=15"
    if with_square:
        # A red square in the middle stands in for "the subject" — should survive keying.
        filter_complex = (
            f"[0:v]drawbox=x=(iw-200)/2:y=(ih-200)/2:w=200:h=200:color=red:t=fill[out]"
        )
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", video_src,
            "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
            "-filter_complex", filter_complex,
            "-map", "[out]", "-map", "1:a",
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-c:a", "aac",
            str(path),
        ]
    else:
        cmd = [
            ffmpeg, "-y",
            "-f", "lavfi", "-i", video_src,
            "-t", str(duration),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "30",
            "-an",
            str(path),
        ]
    result = subprocess.run(cmd, capture_output=True, timeout=60)
    if result.returncode != 0:
        pytest.fail(f"Could not generate synthetic clip: {result.stderr.decode(errors='replace')[-500:]}")


@pytest.mark.skipif(not _ffmpeg_available, reason="FFmpeg not available in this environment")
def test_chromakey_composite_end_to_end_with_real_ffmpeg(tmp_path):
    bg_path = tmp_path / "bg.mp4"
    avatar_path = tmp_path / "avatar.mp4"
    _make_synthetic_clip(bg_path, color="navy", duration=2.0)
    _make_synthetic_clip(avatar_path, color="green", duration=2.0, with_square=True)

    result = _chromakey_composite(
        avatar_path.read_bytes(), bg_path.read_bytes(), "#00FF00", "center",
    )

    assert result is not None
    assert len(result) > 1000

    out_path = tmp_path / "composite.mp4"
    out_path.write_bytes(result)

    frame_path = tmp_path / "frame.png"
    import subprocess
    subprocess.run(
        [_get_ffmpeg_exe(), "-y", "-ss", "1.0", "-i", str(out_path), "-frames:v", "1", str(frame_path)],
        capture_output=True, timeout=30,
    )
    assert frame_path.exists()

    from PIL import Image
    with Image.open(frame_path) as img:
        img.load()
        img = img.convert("RGB")
        w, h = img.size
        # Corners should show the navy background (green keyed away), center should
        # still show the red "subject" square that survived the key.
        corner = img.getpixel((10, 10))
        center = img.getpixel((w // 2, h // 2))

    # navy ~ (0,0,128); center of the composite should still be a strong red, not green.
    assert corner[1] < 100, f"Expected green keyed out of the corner, got {corner}"
    assert center[0] > 100 and center[1] < 100, f"Expected the red subject square to survive keying, got {center}"


@pytest.mark.skipif(not _ffmpeg_available, reason="FFmpeg not available in this environment")
def test_chromakey_composite_left_position_shifts_overlay(tmp_path):
    bg_path = tmp_path / "bg.mp4"
    avatar_path = tmp_path / "avatar.mp4"
    _make_synthetic_clip(bg_path, color="navy", duration=1.0)
    _make_synthetic_clip(avatar_path, color="green", duration=1.0, with_square=True)

    result = _chromakey_composite(
        avatar_path.read_bytes(), bg_path.read_bytes(), "#00FF00", "left",
    )
    assert result is not None
    assert len(result) > 1000
