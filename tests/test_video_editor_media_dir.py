"""
Regression guard for the 2026-08-02 incident: agents/video_editor/agent.py always
downloaded the anchor video and wrote video_package.json to the global
settings.MEDIA_DIR, ignoring the per-run MEDIA_DIR value the Executive Producer put
in its own message — the same per-run value agents/publisher/agent.py correctly
parses and reads from. Since the two never matched (global default vs. a
show/timestamp-scoped directory), Publisher would hit "File not found" whenever the
LLM followed the per-run instruction instead of its system prompt's hardcoded
./output/media fallback. These tests prove video_editor now writes to the message's
MEDIA_DIR, not settings.MEDIA_DIR, when the two differ. No real HeyGen/FFmpeg calls.
"""
import sys
import json
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import agents.video_editor.agent as video_editor_module
import agents.publisher.prompts as publisher_prompts


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


async def test_video_editor_writes_to_per_run_media_dir_not_global_settings_dir(tmp_path, monkeypatch):
    global_dir = tmp_path / "global_default"
    per_run_dir = tmp_path / "weekend-roundup" / "20260802_100013" / "media"
    global_dir.mkdir(parents=True)
    per_run_dir.mkdir(parents=True)

    # settings.MEDIA_DIR points somewhere completely different from the per-run
    # directory the EP actually passes in the message — exactly like production.
    monkeypatch.setattr(video_editor_module.settings, "MEDIA_DIR", str(global_dir))
    monkeypatch.setattr(video_editor_module, "assemble_final_video", lambda path: None)

    captured_download_dir = {}

    def fake_download(url, filename=None, directory=None):
        captured_download_dir["directory"] = directory
        path = Path(directory) / "anchor_video.mp4"
        path.write_bytes(b"fake-mp4-bytes")
        return str(path)

    monkeypatch.setattr(video_editor_module, "_download_video_impl", fake_download)
    monkeypatch.setattr(video_editor_module, "check_visual_qa", lambda path, **kw: {"flagged": False, "notes": ""})

    agent = video_editor_module.Agent()
    agent.executor = _FakeExecutor("Video package built.")

    result = await agent.process_message(_ANCHOR_OUTPUT_MESSAGE.format(media_dir=per_run_dir))

    assert result["success"] is True
    # The video must be downloaded into the per-run directory, not the global default.
    assert captured_download_dir["directory"] == str(per_run_dir)
    # video_package.json must be written there too.
    pkg_path = per_run_dir / "video_package.json"
    assert pkg_path.exists()
    assert json.loads(pkg_path.read_text(encoding="utf-8"))["video_url"] == "https://files2.heygen.ai/example/abc123.mp4"
    # And critically, nothing should have been written to the global default at all —
    # that's the exact mismatch that caused Publisher's "File not found".
    assert not (global_dir / "video_package.json").exists()


async def test_video_editor_falls_back_to_settings_media_dir_when_message_omits_it(tmp_path, monkeypatch):
    """Standalone/legacy calls without a MEDIA_DIR line must still work — falls back
    to settings.MEDIA_DIR rather than erroring out."""
    monkeypatch.setattr(video_editor_module.settings, "MEDIA_DIR", str(tmp_path))
    monkeypatch.setattr(video_editor_module, "assemble_final_video", lambda path: None)
    monkeypatch.setattr(video_editor_module, "check_visual_qa", lambda path, **kw: {"flagged": False, "notes": ""})

    def fake_download(url, filename=None, directory=None):
        path = Path(directory) / "anchor_video.mp4"
        path.write_bytes(b"fake-mp4-bytes")
        return str(path)

    monkeypatch.setattr(video_editor_module, "_download_video_impl", fake_download)

    agent = video_editor_module.Agent()
    agent.executor = _FakeExecutor("Video package built.")

    message = (
        "DESK_SLUG: national\nTOPIC: Test topic\n\n"
        "=== ANCHOR OUTPUT ===\n"
        "Anchor video generation complete.\n"
        "video_id: abc123\n"
        "video_url: https://files2.heygen.ai/example/abc123.mp4\n"
        "thumbnail_url: \n"
        "scenes: 1\n"
    )
    result = await agent.process_message(message)

    assert result["success"] is True
    assert (tmp_path / "video_package.json").exists()


def test_publisher_prompt_no_longer_hardcodes_output_media():
    """Regression guard: the prompt must not contradict the per-run MEDIA_DIR value
    process_message() already injects at runtime — that conflict is what caused the
    LLM to non-deterministically pick the wrong directory."""
    assert "./output/media" not in publisher_prompts.PUBLISHER_PROMPT
