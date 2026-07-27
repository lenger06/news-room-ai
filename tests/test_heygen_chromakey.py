"""
Unit tests for the Option D v3-chromakey migration path (see
HEYGEN_V3_MIGRATION_PLAN.md sec 4a/8): per-avatar V3 capability data in
config/anchors.py, the render+composite pipeline in tools/heygen_tool.py, and
the video_style dispatch in agents/anchor/agent.py. Everything here is mocked —
no real HeyGen/FFmpeg calls, no HeyGen credits spent.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest

import config.anchors as anchors
import tools.heygen_tool as heygen_tool
from config.anchors import get_look_by_avatar_id
from tools.heygen_tool import (
    _concatenate_segment_scripts,
    _chromakey_composite,
    generate_video_multiscene_v3_chromakey,
)


# ── config/anchors.py: per-look V3 capability data ─────────────────────────────

def test_get_look_by_avatar_id_returns_avatar_iii_only_capability():
    look = get_look_by_avatar_id("Shawn_Suit_Front_public")
    assert look is not None
    assert look.v3_engine == "avatar_iii"
    assert look.v3_supports_motion_prompt is False
    assert look.v3_fit == "contain"
    assert look.v3_unsupported is False


def test_get_look_by_avatar_id_returns_none_for_unknown_id():
    assert get_look_by_avatar_id("does-not-exist-in-roster") is None


def test_daniel_mercer_is_flagged_v3_unsupported():
    look = get_look_by_avatar_id("cbc2c423747542eda390ffaeb269202c")
    assert look is not None
    assert look.v3_unsupported is True


def test_full_access_look_defaults_to_avatar_v_with_motion_prompt():
    look = get_look_by_avatar_id("5c71aeacd9fc4b4f91c50312180f189b")  # Zayne Carter
    assert look is not None
    assert look.v3_engine == "avatar_v"
    assert look.v3_supports_motion_prompt is True
    assert look.v3_fit is None


def test_green_wardrobe_look_uses_blue_key_color_instead_of_green():
    look = get_look_by_avatar_id("Saskia_public_4")  # "Green Blazer"
    assert look is not None
    assert look.v3_key_color == "#0000FF"


# ── tools/heygen_tool.py: _concatenate_segment_scripts ─────────────────────────

def test_concatenate_segment_scripts_joins_and_collapses_whitespace():
    segments = [
        {"script": "First sentence.\n\n"},
        {"script": "  Second sentence.  "},
        {"script": ""},  # empty segments are skipped
    ]
    result = _concatenate_segment_scripts(segments)
    assert result == "First sentence. Second sentence."


def test_concatenate_segment_scripts_truncates_to_5000_chars():
    segments = [{"script": "x" * 6000}]
    result = _concatenate_segment_scripts(segments)
    assert len(result) == 5000


# ── tools/heygen_tool.py: _chromakey_composite ─────────────────────────────────

class _FakeCompletedProcess:
    returncode = 0
    stderr = b""


def test_chromakey_composite_uses_green_despill_for_green_key(monkeypatch, tmp_path):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: "ffmpeg")
    captured = {}

    def fake_run(cmd, capture_output=None, timeout=None):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"fake-mp4")
        return _FakeCompletedProcess()

    monkeypatch.setattr(heygen_tool.subprocess, "run", fake_run)
    result = _chromakey_composite(b"avatar-bytes", b"bg-bytes", "#00FF00", "center")

    assert result == b"fake-mp4"
    filter_complex = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert "chromakey=0x00ff00" in filter_complex
    assert "despill=type=green" in filter_complex
    assert "overlay=0:0" in filter_complex  # center -> no x offset


def test_chromakey_composite_uses_blue_despill_for_blue_key(monkeypatch, tmp_path):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: "ffmpeg")

    def fake_run(cmd, capture_output=None, timeout=None):
        Path(cmd[-1]).write_bytes(b"fake-mp4")
        return _FakeCompletedProcess()

    monkeypatch.setattr(heygen_tool.subprocess, "run", fake_run)
    result = _chromakey_composite(b"avatar-bytes", b"bg-bytes", "#0000FF", "left")
    assert result == b"fake-mp4"


def test_chromakey_composite_applies_position_offset(monkeypatch, tmp_path):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: "ffmpeg")
    captured = {}

    def fake_run(cmd, capture_output=None, timeout=None):
        captured["cmd"] = cmd
        Path(cmd[-1]).write_bytes(b"fake-mp4")
        return _FakeCompletedProcess()

    monkeypatch.setattr(heygen_tool.subprocess, "run", fake_run)
    _chromakey_composite(b"avatar-bytes", b"bg-bytes", "#00FF00", "right")
    filter_complex = captured["cmd"][captured["cmd"].index("-filter_complex") + 1]
    assert "overlay=0.35*W:0" in filter_complex


def test_chromakey_composite_returns_none_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: None)
    assert _chromakey_composite(b"a", b"b", "#00FF00", "center") is None


def test_chromakey_composite_returns_none_on_ffmpeg_failure(monkeypatch):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: "ffmpeg")

    class _FakeFail:
        returncode = 1
        stderr = b"boom"

    monkeypatch.setattr(heygen_tool.subprocess, "run", lambda *a, **k: _FakeFail())
    assert _chromakey_composite(b"a", b"b", "#00FF00", "center") is None


# ── tools/heygen_tool.py: generate_video_multiscene_v3_chromakey ──────────────

SEGMENTS = [{"script": "This is a test broadcast segment."}]


def test_generate_v3_chromakey_requires_api_key(monkeypatch):
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "")
    result = generate_video_multiscene_v3_chromakey(
        SEGMENTS, "some-avatar-id", "some-voice-id", "some-bg-id",
    )
    assert result["video_id"] is None
    assert "error" in result


def test_generate_v3_chromakey_refuses_v3_unsupported_avatar_without_rendering(monkeypatch):
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")

    def _should_not_be_called(*a, **k):
        raise AssertionError("Render must not be attempted for a v3_unsupported avatar")

    monkeypatch.setattr(heygen_tool, "_render_avatar_clip_v3_greenscreen", _should_not_be_called)

    result = generate_video_multiscene_v3_chromakey(
        SEGMENTS,
        "cbc2c423747542eda390ffaeb269202c",  # Daniel Mercer — flagged v3_unsupported
        "voice-id", "bg-id",
    )
    assert result["video_id"] is None
    assert "v3_unsupported" in result["error"]


def test_generate_v3_chromakey_happy_path_full_access_avatar(monkeypatch):
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")

    captured_render_args = {}

    def fake_render(full_script, avatar_id, voice_id, engine, key_color,
                     supports_motion_prompt, fit, motion_prompt, title):
        captured_render_args.update(locals())
        return b"greenscreen-bytes", None

    monkeypatch.setattr(heygen_tool, "_render_avatar_clip_v3_greenscreen", fake_render)
    monkeypatch.setattr(heygen_tool, "_build_v3_chromakey_background", lambda *a, **k: b"bg-bytes")
    monkeypatch.setattr(heygen_tool, "_chromakey_composite", lambda *a, **k: b"final-bytes")
    monkeypatch.setattr(heygen_tool, "_upload_video_asset", lambda data: "final-asset-id")
    monkeypatch.setattr(heygen_tool, "_fetch_asset_download_url", lambda asset_id: "https://cdn.example.com/final.mp4")

    result = generate_video_multiscene_v3_chromakey(
        SEGMENTS,
        "5c71aeacd9fc4b4f91c50312180f189b",  # Zayne Carter — full access, avatar_v
        "voice-id", "bg-id", title="Test Title",
    )

    assert result["status"] == "completed"
    assert result["video_url"] == "https://cdn.example.com/final.mp4"
    assert result["video_id"] == "final-asset-id"
    assert result["uploaded_composites"] == []
    # Full-access avatar: engine=avatar_v, motion_prompt included, no fit override
    assert captured_render_args["engine"] == "avatar_v"
    assert captured_render_args["supports_motion_prompt"] is True
    assert captured_render_args["fit"] is None
    assert captured_render_args["key_color"] == "#00FF00"


def test_generate_v3_chromakey_avatar_iii_look_omits_motion_prompt_and_sets_fit(monkeypatch):
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")

    captured_render_args = {}

    def fake_render(full_script, avatar_id, voice_id, engine, key_color,
                     supports_motion_prompt, fit, motion_prompt, title):
        captured_render_args.update(locals())
        return b"greenscreen-bytes", None

    monkeypatch.setattr(heygen_tool, "_render_avatar_clip_v3_greenscreen", fake_render)
    monkeypatch.setattr(heygen_tool, "_build_v3_chromakey_background", lambda *a, **k: b"bg-bytes")
    monkeypatch.setattr(heygen_tool, "_chromakey_composite", lambda *a, **k: b"final-bytes")
    monkeypatch.setattr(heygen_tool, "_upload_video_asset", lambda data: "final-asset-id")
    monkeypatch.setattr(heygen_tool, "_fetch_asset_download_url", lambda asset_id: "https://cdn.example.com/final.mp4")

    result = generate_video_multiscene_v3_chromakey(
        SEGMENTS,
        "Shawn_Suit_Front_public",  # avatar_iii only, studio_avatar
        "voice-id", "bg-id",
    )

    assert result["status"] == "completed"
    assert captured_render_args["engine"] == "avatar_iii"
    assert captured_render_args["supports_motion_prompt"] is False
    assert captured_render_args["fit"] == "contain"


def test_generate_v3_chromakey_propagates_render_error(monkeypatch):
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")
    monkeypatch.setattr(
        heygen_tool, "_render_avatar_clip_v3_greenscreen",
        lambda *a, **k: (None, "HeyGen v3 HTTP 400: bad request"),
    )
    result = generate_video_multiscene_v3_chromakey(
        SEGMENTS, "5c71aeacd9fc4b4f91c50312180f189b", "voice-id", "bg-id",
    )
    assert result["video_id"] is None
    assert "bad request" in result["error"]


def test_generate_v3_chromakey_returns_error_when_background_build_fails(monkeypatch):
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")
    monkeypatch.setattr(heygen_tool, "_render_avatar_clip_v3_greenscreen", lambda *a, **k: (b"clip", None))
    monkeypatch.setattr(heygen_tool, "_build_v3_chromakey_background", lambda *a, **k: None)
    result = generate_video_multiscene_v3_chromakey(
        SEGMENTS, "5c71aeacd9fc4b4f91c50312180f189b", "voice-id", "bg-id",
    )
    assert result["video_id"] is None
    assert "background" in result["error"].lower()


# ── tools/heygen_tool.py: _render_avatar_clip_v3_greenscreen payload shape ─────

class _FakeResp:
    def __init__(self, ok=True, json_data=None, status_code=200, text=""):
        self.ok = ok
        self._json = json_data or {}
        self.status_code = status_code
        self.text = text
        self.content = b"downloaded-clip-bytes"

    def json(self):
        return self._json


def test_render_avatar_clip_v3_greenscreen_omits_motion_prompt_when_unsupported(monkeypatch):
    captured_payload = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured_payload.update(json)
        return _FakeResp(json_data={"data": {"video_id": "vid123"}})

    def fake_get(url, headers=None, timeout=None):
        if "v3/videos/" in url:
            return _FakeResp(json_data={"data": {"status": "completed", "video_url": "https://cdn/x.mp4"}})
        return _FakeResp()  # the final clip download

    monkeypatch.setattr(heygen_tool.requests, "post", fake_post)
    monkeypatch.setattr(heygen_tool.requests, "get", fake_get)
    monkeypatch.setattr(heygen_tool.time, "sleep", lambda s: None)
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")

    clip_bytes, err = heygen_tool._render_avatar_clip_v3_greenscreen(
        "script text", "avatar-id", "voice-id", "avatar_iii", "#00FF00",
        supports_motion_prompt=False, fit="contain",
        motion_prompt="unused", title="Title",
    )

    assert err is None
    assert clip_bytes == b"downloaded-clip-bytes"
    assert "motion_prompt" not in captured_payload
    assert captured_payload["fit"] == "contain"
    assert captured_payload["engine"] == {"type": "avatar_iii"}


def test_render_avatar_clip_v3_greenscreen_includes_motion_prompt_when_supported(monkeypatch):
    captured_payload = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured_payload.update(json)
        return _FakeResp(json_data={"data": {"video_id": "vid123"}})

    def fake_get(url, headers=None, timeout=None):
        if "v3/videos/" in url:
            return _FakeResp(json_data={"data": {"status": "completed", "video_url": "https://cdn/x.mp4"}})
        return _FakeResp()

    monkeypatch.setattr(heygen_tool.requests, "post", fake_post)
    monkeypatch.setattr(heygen_tool.requests, "get", fake_get)
    monkeypatch.setattr(heygen_tool.time, "sleep", lambda s: None)
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")

    clip_bytes, err = heygen_tool._render_avatar_clip_v3_greenscreen(
        "script text", "avatar-id", "voice-id", "avatar_v", "#00FF00",
        supports_motion_prompt=True, fit=None,
        motion_prompt="Professional broadcast news anchor.", title="Title",
    )

    assert err is None
    assert captured_payload["motion_prompt"] == "Professional broadcast news anchor."
    assert "fit" not in captured_payload


def test_render_avatar_clip_v3_greenscreen_returns_error_on_failed_status(monkeypatch):
    def fake_post(url, headers=None, json=None, timeout=None):
        return _FakeResp(json_data={"data": {"video_id": "vid123"}})

    def fake_get(url, headers=None, timeout=None):
        return _FakeResp(json_data={"data": {"status": "failed", "error": "render blew up"}})

    monkeypatch.setattr(heygen_tool.requests, "post", fake_post)
    monkeypatch.setattr(heygen_tool.requests, "get", fake_get)
    monkeypatch.setattr(heygen_tool.time, "sleep", lambda s: None)
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")

    clip_bytes, err = heygen_tool._render_avatar_clip_v3_greenscreen(
        "script", "avatar-id", "voice-id", "avatar_v", "#00FF00",
        True, None, "motion", "Title",
    )
    assert clip_bytes is None
    assert "render blew up" in err


# ── agents/anchor/agent.py: video_style dispatch ───────────────────────────────

@pytest.mark.asyncio
async def test_anchor_agent_dispatches_pip_v3_chromakey_and_skips_polling(monkeypatch):
    import agents.anchor.agent as agent_module

    agent = agent_module.Agent()

    monkeypatch.setattr(agent_module, "get_heygen_credits", lambda: 10_000)
    monkeypatch.setattr(agent_module, "prepare_enhanced_background", lambda bg_id, layers: (bg_id, None, False, None))
    monkeypatch.setattr(agent_module, "delete_heygen_asset", lambda asset_id: True)

    def fake_chromakey_gen(segments, avatar_id, voice_id, bg_id, title,
                           voice_emotion, talking_style, expression, avatar_position,
                           bg_bytes_override):
        return {
            "video_id": "final-asset-id",
            "status": "completed",
            "video_url": "https://cdn.example.com/final.mp4",
            "thumbnail_url": "",
            "scene_count": 1,
            "uploaded_composites": [],
        }

    monkeypatch.setattr(agent_module, "generate_video_multiscene_v3_chromakey", fake_chromakey_gen)

    async def _poll_should_not_be_called(self, video_id):
        raise AssertionError("Polling must be skipped when the generator already returned status=completed")

    monkeypatch.setattr(agent_module.Agent, "_poll_until_complete", _poll_should_not_be_called)

    message = (
        "AVATAR ID: 5c71aeacd9fc4b4f91c50312180f189b\n"
        "VOICE ID: voice-123\n"
        "BACKGROUND ASSET ID: bg-123\n"
        "AVATAR POSITION: center\n"
        "DESK_SLUG: entertainment_test_desk_not_configured\n"
        "VIDEO STYLE: pip_v3_chromakey\n\n"
        "=== SCRIPT ===\n"
        "Good evening, this is a test broadcast.\n\n"
        "Now perform your role.\n"
    )

    result = await agent.process_message(message)

    assert result["success"] is True
    assert result["video_url"] == "https://cdn.example.com/final.mp4"
    assert result["video_id"] == "final-asset-id"


@pytest.mark.asyncio
async def test_anchor_agent_defaults_to_pip_v2_when_no_style_tag(monkeypatch):
    import agents.anchor.agent as agent_module

    agent = agent_module.Agent()

    monkeypatch.setattr(agent_module, "get_heygen_credits", lambda: 10_000)
    monkeypatch.setattr(agent_module, "prepare_enhanced_background", lambda bg_id, layers: (bg_id, None, False, None))

    captured = {}

    def fake_v2_gen(segments, avatar_id, voice_id, bg_id, title,
                    voice_emotion, talking_style, expression, avatar_position,
                    bg_bytes_override):
        captured["called"] = True
        return {"video_id": "v2-id", "status": "processing", "scene_count": 1, "uploaded_composites": []}

    async def fake_poll(self, video_id):
        return {"video_url": "https://cdn.example.com/v2.mp4", "thumbnail_url": ""}

    monkeypatch.setattr(agent_module, "generate_video_multiscene", fake_v2_gen)
    monkeypatch.setattr(agent_module.Agent, "_poll_until_complete", fake_poll)

    message = (
        "AVATAR ID: some-avatar\n"
        "VOICE ID: voice-123\n"
        "BACKGROUND ASSET ID: bg-123\n"
        "DESK_SLUG: entertainment_test_desk_not_configured\n\n"
        "=== SCRIPT ===\n"
        "Good evening, this is a test broadcast.\n\n"
        "Now perform your role.\n"
    )

    result = await agent.process_message(message)

    assert result["success"] is True
    assert captured.get("called") is True
    assert result["video_url"] == "https://cdn.example.com/v2.mp4"
