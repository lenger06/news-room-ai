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
                     supports_motion_prompt, supports_remove_background, fit, motion_prompt, title):
        captured_render_args.update(locals())
        return b"greenscreen-bytes", None

    monkeypatch.setattr(heygen_tool, "_render_avatar_clip_v3_greenscreen", fake_render)
    monkeypatch.setattr(heygen_tool, "_build_v3_chromakey_background", lambda *a, **k: b"bg-bytes")
    monkeypatch.setattr(heygen_tool, "_chromakey_composite", lambda *a, **k: b"final-bytes")
    monkeypatch.setattr(heygen_tool, "_upload_final_composite", lambda data: ("final-asset-id", "https://cdn.example.com/final.mp4"))

    result = generate_video_multiscene_v3_chromakey(
        SEGMENTS,
        "5c71aeacd9fc4b4f91c50312180f189b",  # Zayne Carter — full access, avatar_v
        "voice-id", "bg-id", title="Test Title",
    )

    assert result["status"] == "completed"
    assert result["video_url"] == "https://cdn.example.com/final.mp4"
    assert result["video_id"] == "final-asset-id"
    assert result["uploaded_composites"] == []
    # Full-access avatar: engine=avatar_v, motion_prompt + remove_background included, no fit override
    assert captured_render_args["engine"] == "avatar_v"
    assert captured_render_args["supports_motion_prompt"] is True
    assert captured_render_args["supports_remove_background"] is True
    assert captured_render_args["fit"] is None
    assert captured_render_args["key_color"] == "#00FF00"


def test_generate_v3_chromakey_avatar_iii_look_omits_motion_prompt_and_sets_fit(monkeypatch):
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")

    captured_render_args = {}

    def fake_render(full_script, avatar_id, voice_id, engine, key_color,
                     supports_motion_prompt, supports_remove_background, fit, motion_prompt, title):
        captured_render_args.update(locals())
        return b"greenscreen-bytes", None

    monkeypatch.setattr(heygen_tool, "_render_avatar_clip_v3_greenscreen", fake_render)
    monkeypatch.setattr(heygen_tool, "_build_v3_chromakey_background", lambda *a, **k: b"bg-bytes")
    monkeypatch.setattr(heygen_tool, "_chromakey_composite", lambda *a, **k: b"final-bytes")
    monkeypatch.setattr(heygen_tool, "_upload_final_composite", lambda data: ("final-asset-id", "https://cdn.example.com/final.mp4"))

    result = generate_video_multiscene_v3_chromakey(
        SEGMENTS,
        "Shawn_Suit_Front_public",  # avatar_iii only, studio_avatar
        "voice-id", "bg-id",
    )

    assert result["status"] == "completed"
    assert captured_render_args["engine"] == "avatar_iii"
    assert captured_render_args["supports_motion_prompt"] is False
    assert captured_render_args["supports_remove_background"] is False
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


def test_generate_v3_chromakey_returns_error_when_final_upload_fails(monkeypatch):
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")
    monkeypatch.setattr(heygen_tool, "_render_avatar_clip_v3_greenscreen", lambda *a, **k: (b"clip", None))
    monkeypatch.setattr(heygen_tool, "_build_v3_chromakey_background", lambda *a, **k: b"bg")
    monkeypatch.setattr(heygen_tool, "_chromakey_composite", lambda *a, **k: b"final")
    monkeypatch.setattr(heygen_tool, "_upload_final_composite", lambda data: (None, None))
    result = generate_video_multiscene_v3_chromakey(
        SEGMENTS, "5c71aeacd9fc4b4f91c50312180f189b", "voice-id", "bg-id",
    )
    assert result["video_id"] is None
    assert "upload" in result["error"].lower()


# ── tools/heygen_tool.py: _upload_final_composite response parsing ────────────

def test_upload_final_composite_reads_url_from_upload_response_not_a_second_call(monkeypatch):
    """
    Regression guard: HeyGen's asset upload response already contains a public
    `url` — GET /v1/asset/{id} 404s for assets uploaded this way (confirmed
    live 2026-07-27), so this must not make a second HTTP call to resolve it.
    """
    def fake_post(url, headers=None, data=None, timeout=None):
        return _FakeResp(json_data={"data": {"id": "asset-123", "url": "https://resource2.heygen.ai/video/asset-123/original.mp4"}})

    def fake_get(*a, **k):
        raise AssertionError("Must not make a second request to resolve the asset URL")

    monkeypatch.setattr(heygen_tool.requests, "post", fake_post)
    monkeypatch.setattr(heygen_tool.requests, "get", fake_get)
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")

    asset_id, url = heygen_tool._upload_final_composite(b"fake-mp4-bytes")
    assert asset_id == "asset-123"
    assert url == "https://resource2.heygen.ai/video/asset-123/original.mp4"


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
        supports_motion_prompt=False, supports_remove_background=False, fit="contain",
        motion_prompt="unused", title="Title",
    )

    assert err is None
    assert clip_bytes == b"downloaded-clip-bytes"
    assert "motion_prompt" not in captured_payload
    assert captured_payload["fit"] == "contain"
    assert captured_payload["engine"] == {"type": "avatar_iii"}
    # avatar_iii/studio_avatar: HeyGen rejects remove_background outright (HTTP
    # 400 "not trained for matting", confirmed live 2026-07-27) — must be omitted.
    assert "remove_background" not in captured_payload


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
        supports_motion_prompt=True, supports_remove_background=True, fit=None,
        motion_prompt="Professional broadcast news anchor.", title="Title",
    )

    assert err is None
    assert captured_payload["motion_prompt"] == "Professional broadcast news anchor."
    assert "fit" not in captured_payload
    assert captured_payload["remove_background"] is True


def test_render_avatar_clip_v3_greenscreen_sets_remove_background_when_supported(monkeypatch):
    """
    Regression guard: HeyGen has been observed to silently ignore an explicit
    `background` color and fall back to a default backdrop unless
    `remove_background: true` is also set (confirmed live 2026-07-27, Zayne
    Carter/avatar_iv — the color-only payload rendered an unrelated stock
    outdoor scene instead of green). Must be present for matting-capable avatars.
    """
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

    heygen_tool._render_avatar_clip_v3_greenscreen(
        "script text", "avatar-id", "voice-id", "avatar_iv", "#00FF00",
        supports_motion_prompt=True, supports_remove_background=True, fit=None,
        motion_prompt="motion", title="Title",
    )

    assert captured_payload["remove_background"] is True
    assert captured_payload["background"] == {"type": "color", "value": "#00FF00"}


def test_render_avatar_clip_v3_greenscreen_omits_remove_background_when_unsupported(monkeypatch):
    """
    Regression guard for the opposite case: avatar_iii/studio_avatar avatars
    reject remove_background outright with HTTP 400 ("This video avatar does
    not support background removal. The avatar must be trained for matting")
    — confirmed live 2026-07-27, Shawn Green and Brandon Jones. Must be
    omitted entirely for these, not sent as False.
    """
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

    heygen_tool._render_avatar_clip_v3_greenscreen(
        "script text", "avatar-id", "voice-id", "avatar_iii", "#00FF00",
        supports_motion_prompt=False, supports_remove_background=False, fit="contain",
        motion_prompt="motion", title="Title",
    )

    assert "remove_background" not in captured_payload
    assert captured_payload["background"] == {"type": "color", "value": "#00FF00"}


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
        True, True, None, "motion", "Title",
    )
    assert clip_bytes is None
    assert "render blew up" in err


# ── tools/heygen_tool.py: _extract_heygen_failure_detail (2026-07-28) ───────────
# Regression guard: every v3 render failure was logged as a useless "unknown" because
# the old code only checked data.get("error")/data.get("msg"), but HeyGen's real
# failure fields are failure_code/failure_message — confirmed live when a render
# failed with failure_code="MOVIO_PAYMENT_INSUFFICIENT_CREDIT" and neither "error"
# nor "msg" was present in the response at all.

def test_extract_failure_detail_prefers_code_and_message_combined():
    data = {
        "status": "failed",
        "failure_code": "MOVIO_PAYMENT_INSUFFICIENT_CREDIT",
        "failure_message": "Insufficient credit. This operation requires 'plan_credit' or 'api' credits.",
    }
    detail = heygen_tool._extract_heygen_failure_detail(data)
    assert detail == (
        "MOVIO_PAYMENT_INSUFFICIENT_CREDIT: Insufficient credit. "
        "This operation requires 'plan_credit' or 'api' credits."
    )


def test_extract_failure_detail_message_only():
    assert heygen_tool._extract_heygen_failure_detail({"failure_message": "Something broke"}) == "Something broke"


def test_extract_failure_detail_code_only():
    assert heygen_tool._extract_heygen_failure_detail({"failure_code": "SOME_CODE"}) == "SOME_CODE"


def test_extract_failure_detail_falls_back_to_error_and_msg():
    assert heygen_tool._extract_heygen_failure_detail({"error": "boom"}) == "boom"
    assert heygen_tool._extract_heygen_failure_detail({"msg": "also boom"}) == "also boom"


def test_extract_failure_detail_falls_back_to_unknown_when_nothing_present():
    assert heygen_tool._extract_heygen_failure_detail({}) == "unknown"


def test_poll_v3_video_sync_surfaces_failure_code_and_message(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        return _FakeResp(json_data={"data": {
            "status": "failed",
            "failure_code": "MOVIO_PAYMENT_INSUFFICIENT_CREDIT",
            "failure_message": "Insufficient credit. This operation requires 'plan_credit' or 'api' credits.",
        }})

    monkeypatch.setattr(heygen_tool.requests, "get", fake_get)
    monkeypatch.setattr(heygen_tool.time, "sleep", lambda s: None)
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")

    result = heygen_tool._poll_v3_video_sync("vid123")
    assert "MOVIO_PAYMENT_INSUFFICIENT_CREDIT" in result["error"]
    assert "plan_credit" in result["error"]


# ── agents/anchor/agent.py: _check_status_sync / _poll_until_complete ───────────
# Regression guard: _check_status_sync computed error_detail and logged it, but
# _poll_until_complete's failed-status branch discarded it entirely and returned a
# hardcoded generic string with zero diagnostic value — worse than "unknown", it
# never even tried. Fixed alongside the same-cause heygen_tool.py gap above.

@pytest.mark.asyncio
async def test_poll_until_complete_surfaces_real_failure_detail(monkeypatch):
    import agents.anchor.agent as agent_module

    agent = agent_module.Agent()
    monkeypatch.setattr(agent_module.settings, "HEYGEN_API_KEY", "fake-key")

    async def _fast_sleep(s):
        return None
    monkeypatch.setattr(agent_module.asyncio, "sleep", _fast_sleep)

    def fake_get(url, headers=None, timeout=None):
        return _FakeResp(json_data={"data": {
            "status": "failed",
            "failure_code": "MOVIO_PAYMENT_INSUFFICIENT_CREDIT",
            "failure_message": "Insufficient credit. This operation requires 'plan_credit' or 'api' credits.",
        }})

    monkeypatch.setattr(agent_module.requests, "get", fake_get)

    result = await agent._poll_until_complete("vid123")
    assert "MOVIO_PAYMENT_INSUFFICIENT_CREDIT" in result["error"]
    assert "plan_credit" in result["error"]


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
async def test_anchor_agent_falls_back_to_pip_v2_for_v3_unsupported_avatar(monkeypatch):
    """
    Regression guard: a show flipped to pip_v3_chromakey must not hard-fail
    every Daniel Mercer story just because his avatar is flagged
    v3_unsupported (background never honored under v3 — see
    HEYGEN_V3_MIGRATION_PLAN.md sec 4a). Must transparently fall back to
    pip_v2 instead of calling the chromakey generator at all.
    """
    import agents.anchor.agent as agent_module

    agent = agent_module.Agent()

    monkeypatch.setattr(agent_module, "get_heygen_credits", lambda: 10_000)
    monkeypatch.setattr(agent_module, "prepare_enhanced_background", lambda bg_id, layers: (bg_id, None, False, None))

    def _chromakey_should_not_be_called(*a, **k):
        raise AssertionError("Must not call the chromakey generator for a v3_unsupported avatar")

    def fake_v2_gen(segments, avatar_id, voice_id, bg_id, title,
                    voice_emotion, talking_style, expression, avatar_position,
                    bg_bytes_override):
        return {"video_id": "v2-id", "status": "processing", "scene_count": 1, "uploaded_composites": []}

    async def fake_poll(self, video_id):
        return {"video_url": "https://cdn.example.com/v2-fallback.mp4", "thumbnail_url": ""}

    monkeypatch.setattr(agent_module, "generate_video_multiscene_v3_chromakey", _chromakey_should_not_be_called)
    monkeypatch.setattr(agent_module, "generate_video_multiscene", fake_v2_gen)
    monkeypatch.setattr(agent_module.Agent, "_poll_until_complete", fake_poll)

    message = (
        "AVATAR ID: cbc2c423747542eda390ffaeb269202c\n"  # Daniel Mercer — v3_unsupported
        "VOICE ID: voice-123\n"
        "BACKGROUND ASSET ID: bg-123\n"
        "DESK_SLUG: entertainment_test_desk_not_configured\n"
        "VIDEO STYLE: pip_v3_chromakey\n\n"
        "=== SCRIPT ===\n"
        "Good evening, this is a test broadcast.\n\n"
        "Now perform your role.\n"
    )

    result = await agent.process_message(message)

    assert result["success"] is True
    assert result["video_url"] == "https://cdn.example.com/v2-fallback.mp4"


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


# ── tools/heygen_tool.py: _allocate_broll_windows (2026-07-28 timed b-roll switching) ──
# Regression/feature guard: pip_v3_chromakey originally held a single b-roll item for
# the whole take (see _build_v3_chromakey_background's docstring) — a known Phase 1
# limitation. The user reported this after a real run and asked for intelligent,
# periodic switching "the same as a real newscast" without flashing too fast. These
# tests exercise the pure proportional-timing allocator with no ffmpeg/network involved.

from tools.heygen_tool import _allocate_broll_windows, _BROLL_MIN_HOLD_SECONDS


def test_allocate_windows_single_segment_spans_full_duration():
    segments = [{"script": "one two three", "image_url": "https://img/1.jpg"}]
    windows = _allocate_broll_windows(segments, total_duration_s=30.0)
    assert len(windows) == 1
    assert windows[0]["start"] == 0.0
    assert windows[0]["end"] == 30.0
    assert windows[0]["image_url"] == "https://img/1.jpg"


def test_allocate_windows_splits_proportionally_to_word_count():
    segments = [
        {"script": " ".join(["word"] * 10), "image_url": "https://img/1.jpg"},
        {"script": " ".join(["word"] * 30), "image_url": "https://img/2.jpg"},
    ]
    # Both windows comfortably clear min_hold_s at this total duration, so no merging.
    windows = _allocate_broll_windows(segments, total_duration_s=40.0, min_hold_s=5.0)
    assert len(windows) == 2
    assert windows[0]["end"] - windows[0]["start"] == pytest.approx(10.0, abs=0.01)
    assert windows[1]["end"] - windows[1]["start"] == pytest.approx(30.0, abs=0.01)
    assert windows[0]["image_url"] == "https://img/1.jpg"
    assert windows[1]["image_url"] == "https://img/2.jpg"
    assert windows[-1]["end"] == 40.0


def test_allocate_windows_merges_short_segments_forward_to_respect_min_hold():
    # 10 tiny equal segments over a short total duration — naive per-segment allocation
    # would give ~2s each, well under min_hold_s, so adjacent ones must merge.
    segments = [{"script": "hello there", "image_url": f"https://img/{i}.jpg"} for i in range(10)]
    windows = _allocate_broll_windows(segments, total_duration_s=20.0, min_hold_s=5.0)
    assert all((w["end"] - w["start"]) >= 5.0 - 1e-6 for w in windows[:-1])
    assert len(windows) < len(segments)
    # Ascending, contiguous, covering the full duration with no gaps or overlaps.
    assert windows[0]["start"] == 0.0
    assert windows[-1]["end"] == 20.0
    for a, b in zip(windows, windows[1:]):
        assert a["end"] == b["start"]


def test_allocate_windows_merges_too_short_final_window_backward():
    # First segment is long, second is tiny — the tiny trailing window would be well
    # under min_hold_s and must be absorbed into the previous window instead of flashing
    # a new b-roll for the last instant of the take.
    segments = [
        {"script": " ".join(["word"] * 100), "image_url": "https://img/1.jpg"},
        {"script": "hi", "image_url": "https://img/2.jpg"},
    ]
    windows = _allocate_broll_windows(segments, total_duration_s=60.0, min_hold_s=5.0)
    assert len(windows) == 1
    assert windows[0]["image_url"] == "https://img/1.jpg"
    assert windows[0]["end"] == 60.0


def test_allocate_windows_preserves_studio_only_segments_as_none():
    segments = [
        {"script": " ".join(["word"] * 20), "image_url": None, "video_url": None},
        {"script": " ".join(["word"] * 20), "image_url": "https://img/1.jpg"},
    ]
    windows = _allocate_broll_windows(segments, total_duration_s=40.0, min_hold_s=5.0)
    assert windows[0]["image_url"] is None
    assert windows[0]["video_url"] is None
    assert windows[1]["image_url"] == "https://img/1.jpg"


def test_allocate_windows_empty_segments_returns_empty():
    assert _allocate_broll_windows([], total_duration_s=30.0) == []


def test_allocate_windows_zero_duration_returns_empty():
    segments = [{"script": "hello", "image_url": "https://img/1.jpg"}]
    assert _allocate_broll_windows(segments, total_duration_s=0.0) == []


# ── tools/heygen_tool.py: _get_clip_duration_seconds ────────────────────────────

def test_get_clip_duration_seconds_parses_ffmpeg_banner(monkeypatch):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: "ffmpeg")

    class _Result:
        returncode = 0
        stderr = b"Duration: 00:01:23.45, start: 0.000000, bitrate: 1234 kb/s"

    monkeypatch.setattr(heygen_tool.subprocess, "run", lambda *a, **k: _Result())
    duration = heygen_tool._get_clip_duration_seconds(b"fake-mp4-bytes")
    assert duration == pytest.approx(83.45, abs=0.01)


def test_get_clip_duration_seconds_returns_none_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: None)
    assert heygen_tool._get_clip_duration_seconds(b"fake-mp4-bytes") is None


def test_get_clip_duration_seconds_returns_none_when_unparseable(monkeypatch):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: "ffmpeg")

    class _Result:
        returncode = 0
        stderr = b"no duration info here"

    monkeypatch.setattr(heygen_tool.subprocess, "run", lambda *a, **k: _Result())
    assert heygen_tool._get_clip_duration_seconds(b"fake-mp4-bytes") is None


# ── tools/heygen_tool.py: _build_v3_chromakey_timed_background ──────────────────

def test_timed_background_returns_none_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: None)
    segments = [
        {"script": " ".join(["word"] * 20), "image_url": "https://img/1.jpg"},
        {"script": " ".join(["word"] * 20), "image_url": "https://img/2.jpg"},
    ]
    result = heygen_tool._build_v3_chromakey_timed_background(
        segments, "bg-id", b"bg-bytes", "left", total_duration_s=40.0,
    )
    assert result is None


def test_timed_background_returns_none_when_fewer_than_two_windows(monkeypatch):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: "ffmpeg")
    segments = [{"script": "hello", "image_url": "https://img/1.jpg"}]
    result = heygen_tool._build_v3_chromakey_timed_background(
        segments, "bg-id", b"bg-bytes", "left", total_duration_s=10.0,
    )
    assert result is None


def test_timed_background_builds_one_chunk_per_window_and_concatenates(monkeypatch, tmp_path):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: "ffmpeg")

    segments = [
        {"script": " ".join(["word"] * 20), "image_url": "https://img/1.jpg"},
        {"script": " ".join(["word"] * 20), "image_url": "https://img/2.jpg"},
    ]

    composite_calls = []

    def fake_image_composite(image_bytes, bg_bytes, pip_position="left", duration_s=15):
        composite_calls.append(duration_s)
        return f"chunk-for-{duration_s}".encode()

    monkeypatch.setattr(heygen_tool, "_create_broll_video_composite", fake_image_composite)
    monkeypatch.setattr(
        heygen_tool.requests, "get",
        lambda url, timeout=None, headers=None: type("R", (), {"ok": True, "content": b"img-bytes"})(),
    )

    def fake_run(cmd, capture_output=None, timeout=None):
        # The concat step — write real bytes so the final read_bytes() succeeds.
        Path(cmd[-1]).write_bytes(b"final-timed-bg")
        return type("Result", (), {"returncode": 0, "stderr": b""})()

    monkeypatch.setattr(heygen_tool.subprocess, "run", fake_run)

    result = heygen_tool._build_v3_chromakey_timed_background(
        segments, "bg-id", b"bg-bytes", "left", total_duration_s=40.0, min_hold_s=5.0,
    )

    assert result == b"final-timed-bg"
    assert len(composite_calls) == 2  # one composite call per window


def test_timed_background_falls_back_to_studio_clip_when_composite_fails(monkeypatch):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: "ffmpeg")

    segments = [
        {"script": " ".join(["word"] * 20), "image_url": "https://img/1.jpg"},
        {"script": " ".join(["word"] * 20), "image_url": "https://img/2.jpg"},
    ]

    monkeypatch.setattr(heygen_tool, "_create_broll_video_composite", lambda *a, **k: None)
    monkeypatch.setattr(
        heygen_tool.requests, "get",
        lambda url, timeout=None, headers=None: type("R", (), {"ok": True, "content": b"img-bytes"})(),
    )

    studio_calls = []
    def fake_trim(bg_bytes, duration_s):
        studio_calls.append(duration_s)
        return f"studio-{duration_s}".encode()
    monkeypatch.setattr(heygen_tool, "_trim_studio_clip", fake_trim)

    def fake_run(cmd, capture_output=None, timeout=None):
        Path(cmd[-1]).write_bytes(b"final-timed-bg")
        return type("Result", (), {"returncode": 0, "stderr": b""})()
    monkeypatch.setattr(heygen_tool.subprocess, "run", fake_run)

    result = heygen_tool._build_v3_chromakey_timed_background(
        segments, "bg-id", b"bg-bytes", "left", total_duration_s=40.0, min_hold_s=5.0,
    )

    assert result == b"final-timed-bg"
    assert len(studio_calls) == 2  # both windows fell back to a plain studio hold


def test_timed_background_returns_none_when_concat_fails(monkeypatch):
    monkeypatch.setattr(heygen_tool, "_get_ffmpeg_exe", lambda: "ffmpeg")
    segments = [
        {"script": " ".join(["word"] * 20), "image_url": "https://img/1.jpg"},
        {"script": " ".join(["word"] * 20), "image_url": "https://img/2.jpg"},
    ]
    monkeypatch.setattr(heygen_tool, "_create_broll_video_composite", lambda *a, **k: b"chunk-bytes")
    monkeypatch.setattr(
        heygen_tool.requests, "get",
        lambda url, timeout=None, headers=None: type("R", (), {"ok": True, "content": b"img-bytes"})(),
    )
    monkeypatch.setattr(
        heygen_tool.subprocess, "run",
        lambda *a, **k: type("Result", (), {"returncode": 1, "stderr": b"concat boom"})(),
    )
    result = heygen_tool._build_v3_chromakey_timed_background(
        segments, "bg-id", b"bg-bytes", "left", total_duration_s=40.0, min_hold_s=5.0,
    )
    assert result is None


# ── generate_video_multiscene_v3_chromakey: timed-background dispatch ───────────

def test_generate_v3_chromakey_uses_timed_background_when_multiple_broll_items(monkeypatch):
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")
    monkeypatch.setattr(heygen_tool, "_render_avatar_clip_v3_greenscreen", lambda *a, **k: (b"clip", None))
    monkeypatch.setattr(heygen_tool, "_get_clip_duration_seconds", lambda clip: 40.0)

    timed_calls = []
    def fake_timed(segments, background_asset_id, bg_bytes_override, pip_position, total_duration_s, **kw):
        timed_calls.append(total_duration_s)
        return b"timed-bg-bytes"
    monkeypatch.setattr(heygen_tool, "_build_v3_chromakey_timed_background", fake_timed)

    def _single_should_not_be_called(*a, **k):
        raise AssertionError("Single-background path must not run when the timed path succeeds")
    monkeypatch.setattr(heygen_tool, "_build_v3_chromakey_background", _single_should_not_be_called)

    monkeypatch.setattr(heygen_tool, "_chromakey_composite", lambda *a, **k: b"final-bytes")
    monkeypatch.setattr(heygen_tool, "_upload_final_composite", lambda data: ("final-id", "https://cdn.example.com/final.mp4"))

    segments = [
        {"script": "First segment.", "image_url": "https://img/1.jpg"},
        {"script": "Second segment.", "image_url": "https://img/2.jpg"},
    ]
    result = generate_video_multiscene_v3_chromakey(
        segments, "5c71aeacd9fc4b4f91c50312180f189b", "voice-id", "bg-id",
    )

    assert result["status"] == "completed"
    assert result["video_url"] == "https://cdn.example.com/final.mp4"
    assert timed_calls == [40.0]


def test_generate_v3_chromakey_falls_back_to_single_background_when_timed_fails(monkeypatch):
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")
    monkeypatch.setattr(heygen_tool, "_render_avatar_clip_v3_greenscreen", lambda *a, **k: (b"clip", None))
    monkeypatch.setattr(heygen_tool, "_get_clip_duration_seconds", lambda clip: 40.0)
    monkeypatch.setattr(heygen_tool, "_build_v3_chromakey_timed_background", lambda *a, **k: None)

    single_calls = []
    def fake_single(segments, background_asset_id, bg_bytes_override, pip_position):
        single_calls.append(True)
        return b"single-bg-bytes"
    monkeypatch.setattr(heygen_tool, "_build_v3_chromakey_background", fake_single)

    monkeypatch.setattr(heygen_tool, "_chromakey_composite", lambda *a, **k: b"final-bytes")
    monkeypatch.setattr(heygen_tool, "_upload_final_composite", lambda data: ("final-id", "https://cdn.example.com/final.mp4"))

    segments = [
        {"script": "First segment.", "image_url": "https://img/1.jpg"},
        {"script": "Second segment.", "image_url": "https://img/2.jpg"},
    ]
    result = generate_video_multiscene_v3_chromakey(
        segments, "5c71aeacd9fc4b4f91c50312180f189b", "voice-id", "bg-id",
    )

    assert result["status"] == "completed"
    assert single_calls == [True]


def test_generate_v3_chromakey_skips_timed_path_with_only_one_broll_item(monkeypatch):
    """A single b-roll item (or none) has nothing to switch between — must not probe
    clip duration or attempt the timed path at all; this also keeps the pre-existing
    single-segment tests (SEGMENTS constant above) behaving exactly as before."""
    monkeypatch.setattr(heygen_tool.settings, "HEYGEN_API_KEY", "fake-key")
    monkeypatch.setattr(heygen_tool, "_render_avatar_clip_v3_greenscreen", lambda *a, **k: (b"clip", None))

    def _duration_should_not_be_called(*a, **k):
        raise AssertionError("Must not probe clip duration when there's only one b-roll item")
    monkeypatch.setattr(heygen_tool, "_get_clip_duration_seconds", _duration_should_not_be_called)

    monkeypatch.setattr(heygen_tool, "_build_v3_chromakey_background", lambda *a, **k: b"single-bg-bytes")
    monkeypatch.setattr(heygen_tool, "_chromakey_composite", lambda *a, **k: b"final-bytes")
    monkeypatch.setattr(heygen_tool, "_upload_final_composite", lambda data: ("final-id", "https://cdn.example.com/final.mp4"))

    segments = [{"script": "Only segment.", "image_url": "https://img/1.jpg"}]
    result = generate_video_multiscene_v3_chromakey(
        segments, "5c71aeacd9fc4b4f91c50312180f189b", "voice-id", "bg-id",
    )
    assert result["status"] == "completed"
