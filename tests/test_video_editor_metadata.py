"""
Regression guard for the 2026-08-07 incident: agents/video_editor/agent.py used to give
the LLM its own download_video/save_video_package tools, and its prompt told it to save
video_package.json to the OLD hardcoded ./output/media/ path. Once the 2026-08-02 fix
made the deterministic code use the per-run MEDIA_DIR instead, the LLM's own save calls
(still hitting the old path) became invisible to the pipeline — its title/description/tags
suggestions were silently discarded, and every YouTube upload fell back to a bland
topic-string title. Now the LLM only returns metadata as text (no tools, no file writes),
parsed deterministically. No real LLM/API/FFmpeg calls — executor and ffmpeg-backed helpers
are mocked throughout.
"""
import sys
import json
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import agents.video_editor.agent as video_editor_module
from agents.video_editor.agent import Agent as VideoEditorAgent


def test_video_editor_tools_no_longer_include_download_or_save():
    agent = VideoEditorAgent()
    tool_names = {t.name for t in agent.tools}
    assert "download_video" not in tool_names
    assert "save_video_package" not in tool_names
    assert "extract_graphic_cues" not in tool_names


def test_parse_llm_metadata_extracts_json_code_block():
    text = (
        "Here is my suggestion:\n\n"
        "```json\n"
        '{"title": "Top Stories Today", "description": "A quick roundup.", "tags": ["news", "roundup"]}\n'
        "```\n"
    )
    metadata = VideoEditorAgent._parse_llm_metadata(text)
    assert metadata == {
        "title": "Top Stories Today",
        "description": "A quick roundup.",
        "tags": ["news", "roundup"],
    }


def test_parse_llm_metadata_returns_empty_dict_when_unparseable():
    assert VideoEditorAgent._parse_llm_metadata("I couldn't come up with anything useful.") == {}


class _FakeExecutor:
    def __init__(self, output_text):
        self._output_text = output_text

    def invoke(self, inputs):
        return {"output": self._output_text}


_ANCHOR_OUTPUT_MESSAGE = (
    "DESK_SLUG: national\nTOPIC: Top 5 news stories\n\n"
    "=== ANCHOR OUTPUT ===\n"
    "Anchor video generation complete.\n"
    "video_id: abc123\n"
    "video_url: https://files2.heygen.ai/example/abc123.mp4\n"
    "thumbnail_url: \n"
    "scenes: 1\n\n"
    "MEDIA_DIR: {media_dir}"
)


def _setup(tmp_path, monkeypatch):
    monkeypatch.setattr(video_editor_module, "assemble_final_video", lambda path: None)
    monkeypatch.setattr(video_editor_module, "check_visual_qa", lambda path, **kw: {"flagged": False, "notes": ""})
    video_path = tmp_path / "anchor_video.mp4"

    def fake_download(url, filename=None, directory=None):
        video_path.write_bytes(b"fake-mp4-bytes")
        return str(video_path)

    monkeypatch.setattr(video_editor_module, "_download_video_impl", fake_download)
    return tmp_path / "video_package.json"


async def test_llm_suggested_metadata_reaches_the_final_package(tmp_path, monkeypatch):
    pkg_path = _setup(tmp_path, monkeypatch)
    agent = VideoEditorAgent()
    agent.executor = _FakeExecutor(
        '```json\n{"title": "Real Suggested Title", "description": "A crafted description.", '
        '"tags": ["politics", "world"]}\n```'
    )

    result = await agent.process_message(_ANCHOR_OUTPUT_MESSAGE.format(media_dir=tmp_path))

    assert result["success"] is True
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    assert pkg["title"] == "Real Suggested Title"
    assert pkg["description"] == "A crafted description."
    assert pkg["tags"] == ["politics", "world"]


async def test_falls_back_to_topic_when_llm_gives_no_parseable_metadata(tmp_path, monkeypatch):
    pkg_path = _setup(tmp_path, monkeypatch)
    agent = VideoEditorAgent()
    agent.executor = _FakeExecutor("I have nothing useful to add.")

    result = await agent.process_message(_ANCHOR_OUTPUT_MESSAGE.format(media_dir=tmp_path))

    assert result["success"] is True
    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
    assert pkg["title"] == "Top 5 news stories"
    assert pkg["tags"] == []
