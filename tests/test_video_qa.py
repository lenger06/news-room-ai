"""
Unit tests for Phase 7.3 (SELF_IMPROVEMENT_ROADMAP.md) — automated visual QA.
tools.video_tools.check_visual_qa samples frames from a finished video and asks
a vision LLM whether anything looks wrong; agents/video_editor/agent.py wires a
flagged result into the human review queue. FFmpeg, the vision LLM, and the
review queue are all mocked — no real API calls, no cost.
"""
import sys
import json
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest

import tools.video_tools as video_tools
from tools.video_tools import check_visual_qa


class _FakeLLMResponse:
    def __init__(self, content):
        self.content = content


class _FakeChatOpenAI:
    """Stand-in for langchain_openai.ChatOpenAI — captures the messages it was
    invoked with and returns a scripted response."""
    last_instance = None

    def __init__(self, response_text="FLAGGED: no - frames look clean.", **kwargs):
        self.init_kwargs = kwargs
        self._response_text = response_text
        self.invoked_with = None
        _FakeChatOpenAI.last_instance = self

    def invoke(self, messages):
        self.invoked_with = messages
        return _FakeLLMResponse(self._response_text)


class _FakeFailingChatOpenAI:
    def __init__(self, **kwargs):
        pass

    def invoke(self, messages):
        raise RuntimeError("simulated vision API failure")


def _patch_frame_extraction(monkeypatch, num_frames_written=3):
    """Mock ffmpeg presence, duration lookup, and subprocess.run so
    check_visual_qa believes it successfully extracted frames without touching
    a real video file."""
    monkeypatch.setattr(video_tools, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_tools, "_get_video_duration_seconds", lambda p: 20.0)

    def fake_run(cmd, capture_output=None, timeout=None):
        out_path = Path(cmd[-1])
        out_path.write_bytes(b"fake-jpeg-bytes")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(video_tools.subprocess, "run", fake_run)


def test_check_visual_qa_returns_unflagged_without_ffmpeg(monkeypatch):
    monkeypatch.setattr(video_tools, "_get_ffmpeg", lambda: None)
    result = check_visual_qa(Path("irrelevant.mp4"))
    assert result["flagged"] is False
    assert "FFmpeg" in result["notes"]


def test_check_visual_qa_returns_unflagged_without_duration(monkeypatch):
    monkeypatch.setattr(video_tools, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_tools, "_get_video_duration_seconds", lambda p: None)
    result = check_visual_qa(Path("irrelevant.mp4"))
    assert result["flagged"] is False
    assert "duration" in result["notes"].lower()


def test_check_visual_qa_extracts_requested_frame_count(monkeypatch):
    calls = []
    monkeypatch.setattr(video_tools, "_get_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(video_tools, "_get_video_duration_seconds", lambda p: 30.0)

    def fake_run(cmd, capture_output=None, timeout=None):
        calls.append(cmd)
        Path(cmd[-1]).write_bytes(b"fake-jpeg")
        return type("Result", (), {"returncode": 0})()

    monkeypatch.setattr(video_tools.subprocess, "run", fake_run)
    monkeypatch.setattr(video_tools, "ChatOpenAI", lambda **kw: _FakeChatOpenAI(**kw))

    check_visual_qa(Path("video.mp4"), num_frames=4)
    assert len(calls) == 4


def test_check_visual_qa_sends_all_frames_to_vision_llm_and_parses_clean(monkeypatch):
    _patch_frame_extraction(monkeypatch)
    monkeypatch.setattr(video_tools, "ChatOpenAI",
                         lambda **kw: _FakeChatOpenAI(response_text="FLAGGED: no - looks clean.", **kw))

    result = check_visual_qa(Path("video.mp4"), num_frames=3)

    assert result["flagged"] is False
    assert "looks clean" in result["notes"]
    instance = _FakeChatOpenAI.last_instance
    message = instance.invoked_with[0]
    image_parts = [p for p in message.content if p.get("type") == "image_url"]
    assert len(image_parts) == 3
    assert all(p["image_url"]["url"].startswith("data:image/jpeg;base64,") for p in image_parts)


def test_check_visual_qa_flags_when_llm_says_yes(monkeypatch):
    _patch_frame_extraction(monkeypatch)
    monkeypatch.setattr(
        video_tools, "ChatOpenAI",
        lambda **kw: _FakeChatOpenAI(response_text="FLAGGED: yes - visible watermark in bottom-right corner.", **kw),
    )

    result = check_visual_qa(Path("video.mp4"))
    assert result["flagged"] is True
    assert "watermark" in result["notes"].lower()


def test_check_visual_qa_flags_when_response_unparseable(monkeypatch):
    """A response that doesn't follow the expected format should err toward
    flagging for human review, not silently assume clean."""
    _patch_frame_extraction(monkeypatch)
    monkeypatch.setattr(
        video_tools, "ChatOpenAI",
        lambda **kw: _FakeChatOpenAI(response_text="I looked at the frames and they seem fine I guess.", **kw),
    )

    result = check_visual_qa(Path("video.mp4"))
    assert result["flagged"] is True


def test_check_visual_qa_returns_unflagged_on_vision_call_exception(monkeypatch):
    _patch_frame_extraction(monkeypatch)
    monkeypatch.setattr(video_tools, "ChatOpenAI", lambda **kw: _FakeFailingChatOpenAI(**kw))

    result = check_visual_qa(Path("video.mp4"))
    assert result["flagged"] is False
    assert "failed" in result["notes"].lower()


# ── agents/video_editor/agent.py wiring: flagged QA reaches the review queue ───

import agents.video_editor.agent as video_editor_module
import tools.review_queue as review_queue


class _FakeExecutor:
    def __init__(self, output_text):
        self._output_text = output_text

    def invoke(self, inputs):
        return {"output": self._output_text}


_ANCHOR_OUTPUT_MESSAGE = (
    "DESK_SLUG: national\nTOPIC: Test topic\n\n"
    "=== ANCHOR OUTPUT ===\n"
    "Anchor video generation complete.\n"
    "video_id: abc123\n"
    "video_url: https://files2.heygen.ai/example/abc123.mp4\n"
    "thumbnail_url: \n"
    "scenes: 1\n\n"
    "MEDIA_DIR: {media_dir}"
)


def _setup_pkg(tmp_path, monkeypatch):
    """Point settings.MEDIA_DIR at tmp_path, stub the deterministic download to write a
    real (dummy) video file there instead of making a network call, and stub
    assemble_final_video (depends on ambient ./assets/promo|outro files this test
    shouldn't rely on — not Phase 7.3's concern here)."""
    monkeypatch.setattr(video_editor_module.settings, "MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(video_editor_module, "assemble_final_video", lambda path: None)

    video_path = tmp_path / "anchor_video.mp4"

    def fake_download(url, filename=None, directory=None):
        video_path.write_bytes(b"fake-mp4-bytes")
        return str(video_path)

    monkeypatch.setattr(video_editor_module, "_download_video_impl", fake_download)
    pkg_path = tmp_path / "video_package.json"
    return pkg_path, video_path


async def test_video_editor_flagged_qa_writes_to_review_queue(tmp_path, monkeypatch):
    pkg_path, video_path = _setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(review_queue, "_LOG_PATH", tmp_path / "needs_review.json")

    agent = video_editor_module.Agent()
    agent.executor = _FakeExecutor("Video package built.")
    monkeypatch.setattr(
        video_editor_module, "check_visual_qa",
        lambda path, **kw: {"flagged": True, "notes": "Visible watermark in bottom-right corner."},
    )

    result = await agent.process_message(_ANCHOR_OUTPUT_MESSAGE.format(media_dir=tmp_path))

    assert result["success"] is True
    pending = review_queue.list_pending()
    assert len(pending) == 1
    assert pending[0]["stage"] == "visual_qa"
    assert "watermark" in pending[0]["reason"].lower()

    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    assert pkg["visual_qa"]["flagged"] is True
    assert pkg["video_url"] == "https://files2.heygen.ai/example/abc123.mp4"


async def test_video_editor_clean_qa_does_not_write_to_review_queue(tmp_path, monkeypatch):
    pkg_path, video_path = _setup_pkg(tmp_path, monkeypatch)
    monkeypatch.setattr(review_queue, "_LOG_PATH", tmp_path / "needs_review.json")

    agent = video_editor_module.Agent()
    agent.executor = _FakeExecutor("Video package built.")
    monkeypatch.setattr(
        video_editor_module, "check_visual_qa",
        lambda path, **kw: {"flagged": False, "notes": "Frames look clean."},
    )

    result = await agent.process_message(_ANCHOR_OUTPUT_MESSAGE.format(media_dir=tmp_path))

    assert result["success"] is True
    assert review_queue.list_pending() == []


async def test_video_editor_fails_hard_when_no_video_url_in_anchor_output(tmp_path, monkeypatch):
    """Regression guard for the 2026-07-28 incident: no video_url in the anchor's
    output must be a hard, clearly-labeled failure — never a silent fall-through to
    whatever video_package.json happens to already exist on disk."""
    monkeypatch.setattr(video_editor_module.settings, "MEDIA_DIR", str(tmp_path))
    stale_pkg = tmp_path / "video_package.json"
    stale_pkg.write_text(json.dumps({
        "video_file": str(tmp_path / "old_unrelated_video.mp4"),
        "video_url": "https://files2.heygen.ai/example/some-other-story.mp4",
        "title": "An unrelated earlier story",
    }), encoding="utf-8")
    (tmp_path / "old_unrelated_video.mp4").write_bytes(b"stale-video-bytes")

    agent = video_editor_module.Agent()
    agent.executor = _FakeExecutor("Video package built.")

    result = await agent.process_message("DESK_SLUG: national\nTOPIC: Test topic\n\n=== ANCHOR OUTPUT ===\nAnchor step failed.\n")

    assert result["success"] is False
    assert "VIDEO EDITOR FAILED" in result["response"]
    assert "no video_url" in result["response"].lower()
    # The stale package must be left untouched — not overwritten, not reused.
    assert json.loads(stale_pkg.read_text(encoding="utf-8"))["title"] == "An unrelated earlier story"


async def test_video_editor_discards_stale_metadata_when_video_url_differs(tmp_path, monkeypatch):
    """If an existing video_package.json references a different video_url than this
    run's anchor output, its title/description/tags must be discarded as stale, not
    carried into the new package."""
    pkg_path, video_path = _setup_pkg(tmp_path, monkeypatch)
    pkg_path.write_text(json.dumps({
        "video_file": str(tmp_path / "old.mp4"),
        "video_url": "https://files2.heygen.ai/example/completely-different-story.mp4",
        "title": "A totally different, unrelated story",
        "tags": ["unrelated"],
    }), encoding="utf-8")

    agent = video_editor_module.Agent()
    agent.executor = _FakeExecutor("Video package built.")
    monkeypatch.setattr(video_editor_module, "check_visual_qa", lambda path, **kw: {"flagged": False, "notes": ""})

    result = await agent.process_message(_ANCHOR_OUTPUT_MESSAGE.format(media_dir=tmp_path))

    assert result["success"] is True
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    assert pkg["video_url"] == "https://files2.heygen.ai/example/abc123.mp4"
    assert pkg["title"] != "A totally different, unrelated story"
    assert pkg["tags"] != ["unrelated"]
