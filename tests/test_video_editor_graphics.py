"""
Unit tests for the Phase 6 graphic-overlay rendering: cue extraction with position,
video duration lookup, and render_graphic_overlays' control flow — all with
subprocess/ffmpeg mocked. A separate real-ffmpeg smoke test lives in
tests/test_video_editor_graphics_smoke.py (marked slow, run deliberately).
"""
import re
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import tools.video_tools as video_tools
from tools.video_tools import extract_graphic_cues_with_position, render_graphic_overlays
from agents.video_editor.agent import Agent as VideoEditorAgent


def test_extract_graphic_cues_with_position_orders_by_script_offset():
    script = "Good evening. [GRAPHIC: headline] The situation worsens. [GRAPHIC: map of region] Reporting live."
    cues = extract_graphic_cues_with_position(script)
    assert len(cues) == 2
    assert cues[0][0] == "headline"
    assert cues[1][0] == "map of region"
    assert 0.0 <= cues[0][1] < cues[1][1] <= 1.0


def test_extract_graphic_cues_with_position_empty_script():
    assert extract_graphic_cues_with_position("No cues here at all.") == []


def test_render_graphic_overlays_returns_none_with_no_cues():
    assert render_graphic_overlays(Path("irrelevant.mp4"), []) is None


def test_render_graphic_overlays_returns_none_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(video_tools, "_get_ffmpeg", lambda: None)
    result = render_graphic_overlays(Path("irrelevant.mp4"), [("text", 0.5)])
    assert result is None


def test_render_graphic_overlays_returns_none_without_duration(monkeypatch):
    monkeypatch.setattr(video_tools, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_tools, "_get_video_duration_seconds", lambda p: None)
    result = render_graphic_overlays(Path("irrelevant.mp4"), [("text", 0.5)])
    assert result is None


def test_render_graphic_overlays_skips_cues_too_close_to_video_end(monkeypatch, tmp_path):
    monkeypatch.setattr(video_tools, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_tools, "_get_video_duration_seconds", lambda p: 10.0)
    # frac=0.99 of a 10s video -> start=9.9s, leaving <0.5s before the end -> must be skipped
    result = render_graphic_overlays(tmp_path / "video.mp4", [("late cue", 0.99)])
    assert result is None


def test_render_graphic_overlays_builds_expected_ffmpeg_command(monkeypatch, tmp_path):
    monkeypatch.setattr(video_tools, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_tools, "_get_video_duration_seconds", lambda p: 20.0)

    rendered_pngs = []

    def fake_render_png(text, width=video_tools._OUT_W, height=video_tools._OUT_H):
        p = tmp_path / f"gfx_{len(rendered_pngs)}.png"
        p.write_bytes(b"fake-png")
        rendered_pngs.append(p)
        return p

    monkeypatch.setattr(video_tools, "_render_lower_third_png", fake_render_png)

    captured_cmd = {}

    class _FakeResult:
        returncode = 0
        stderr = b""

    def fake_run(cmd, capture_output=None, timeout=None):
        captured_cmd["cmd"] = cmd
        out_path = Path(cmd[-1])
        out_path.write_bytes(b"fake-mp4-bytes")
        return _FakeResult()

    monkeypatch.setattr(video_tools.subprocess, "run", fake_run)

    video_path = tmp_path / "broadcast.mp4"
    video_path.write_bytes(b"fake-source-video")
    result = render_graphic_overlays(video_path, [("first cue", 0.1), ("second cue", 0.6)])

    assert result == video_path.parent / f"gfx_{video_path.stem}.mp4"
    assert result.exists()
    cmd = captured_cmd["cmd"]
    assert cmd[0] == "ffmpeg"
    assert str(video_path) in cmd
    assert all(str(p) in cmd for p in rendered_pngs)
    filter_complex = cmd[cmd.index("-filter_complex") + 1]
    assert "overlay=0:0:enable='between(t," in filter_complex
    assert filter_complex.count("overlay=") == 2
    # Temp PNGs must be cleaned up after rendering.
    assert not any(p.exists() for p in rendered_pngs)


def test_render_graphic_overlays_returns_none_on_ffmpeg_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(video_tools, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_tools, "_get_video_duration_seconds", lambda p: 20.0)
    monkeypatch.setattr(video_tools, "_render_lower_third_png", lambda text, **kw: tmp_path / "x.png")
    (tmp_path / "x.png").write_bytes(b"fake")

    class _FakeFailResult:
        returncode = 1
        stderr = b"ffmpeg exploded"

    monkeypatch.setattr(video_tools.subprocess, "run", lambda *a, **k: _FakeFailResult())

    video_path = tmp_path / "broadcast.mp4"
    video_path.write_bytes(b"fake")
    assert render_graphic_overlays(video_path, [("cue", 0.5)]) is None


def test_video_editor_script_extraction_regex_stops_at_next_section():
    """Regression guard: the boundary must stop at the next '===' marker (e.g.
    '=== ANCHOR OUTPUT ===') regardless of surrounding whitespace, not swallow
    everything after the script through the rest of the message."""
    message = (
        "=== SCRIPT_WRITER OUTPUT ===\n"
        "Some LLM summary text.\n\n"
        "=== SCRIPT ===\n"
        "Good evening. [GRAPHIC: headline] More news follows. I'm the anchor.\n\n"
        "=== ANCHOR OUTPUT ===\n"
        "Anchor video generation complete.\n"
        "video_id: abc123\n"
        "video_url: https://example.com/video.mp4\n\n"
        "DESK_SLUG: national\n"
        "MEDIA_DIR: ./output/media\n"
    )
    script_match = re.search(
        r'=== SCRIPT ===\s*(.*?)(?=\n\nDESK_SLUG:|\n\nMEDIA_DIR:|===|\Z)',
        message, re.DOTALL | re.IGNORECASE,
    )
    assert script_match is not None
    extracted = script_match.group(1)
    assert "ANCHOR OUTPUT" not in extracted
    assert "video_id" not in extracted
    assert "[GRAPHIC: headline]" in extracted


def test_video_editor_agent_registers_new_tools_without_error():
    # Just confirms the agent still constructs cleanly with the updated imports —
    # a real API call isn't made by instantiating the agent.
    agent = VideoEditorAgent()
    assert agent is not None
