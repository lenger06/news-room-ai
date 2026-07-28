"""
Unit tests for the researcher's deterministic b-roll backstop
(agents/researcher/agent.py). Regression guard for the 2026-07-28 incident: log
inspection showed the LLM tool-calling agent sometimes never calls
image_search_tool/video_search_tool at all, despite the prompt instructing it to,
leaving the SPACEX-style research brief with empty "## SOURCED B-ROLL
IMAGES/VIDEOS" sections. _image_search_impl/_video_search_impl (the plain-Python
cores) are mocked here — no real Tavily/Pixabay calls, no LLM calls.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import pytest

import agents.researcher.agent as researcher_module
from agents.researcher.agent import Agent as ResearcherAgent


_EMPTY_BROLL_MESSAGE = (
    "Some research brief text here with facts and sources: http://example.com/a\n\n"
    "## SOURCED B-ROLL IMAGES\n"
    "(No image URLs were provided in the search results)\n\n"
    "## SOURCED B-ROLL VIDEOS\n"
    "(No video URLs were provided in the search results)\n"
)

_OMITTED_BROLL_MESSAGE = (
    "Some research brief text here with facts and sources: http://example.com/a\n"
)

_REAL_BROLL_MESSAGE = (
    "Some research brief text.\n\n"
    "## SOURCED B-ROLL IMAGES\n"
    "https://images.example.com/real1.jpg | A real image\n\n"
    "## SOURCED B-ROLL VIDEOS\n"
    "https://videos.example.com/real1.mp4 | A real clip | 12s\n"
)


class _FakeExecutor:
    def __init__(self, output_text):
        self._output_text = output_text

    def invoke(self, inputs):
        return {"output": self._output_text}


def _agent_with_output(output_text):
    agent = ResearcherAgent()
    agent.executor = _FakeExecutor(output_text)
    return agent


async def test_backfills_images_and_videos_when_llm_wrote_placeholder_text(monkeypatch):
    monkeypatch.setattr(
        researcher_module, "_image_search_impl",
        lambda query, num_results=3: {"images": [
            {"url": "https://img.example.com/1.jpg", "caption": "SpaceX Starship on the pad"},
        ]},
    )
    monkeypatch.setattr(
        researcher_module, "_video_search_impl",
        lambda query, num_results=2: {"videos": [
            {"url": "https://vid.example.com/1.mp4", "description": "Rocket launch", "duration_seconds": 8},
        ]},
    )

    agent = _agent_with_output(_EMPTY_BROLL_MESSAGE)
    result = await agent.process_message("TOPIC: SpaceX Starship V3\n\nBegin your work.")

    assert result["success"] is True
    assert "https://img.example.com/1.jpg | SpaceX Starship on the pad" in result["response"]
    assert "https://vid.example.com/1.mp4 | Rocket launch | 8s" in result["response"]
    assert "No image URLs were provided" not in result["response"]
    assert "No video URLs were provided" not in result["response"]


async def test_backfills_when_llm_omitted_sections_entirely(monkeypatch):
    monkeypatch.setattr(
        researcher_module, "_image_search_impl",
        lambda query, num_results=3: {"images": [{"url": "https://img.example.com/2.jpg", "caption": "x"}]},
    )
    monkeypatch.setattr(
        researcher_module, "_video_search_impl",
        lambda query, num_results=2: {"videos": []},
    )

    agent = _agent_with_output(_OMITTED_BROLL_MESSAGE)
    result = await agent.process_message("TOPIC: SpaceX Starship V3\n\nBegin your work.")

    assert "## SOURCED B-ROLL IMAGES" in result["response"]
    assert "https://img.example.com/2.jpg" in result["response"]
    # No videos found deterministically either — must not fabricate a section.
    assert "## SOURCED B-ROLL VIDEOS" not in result["response"]


async def test_does_not_touch_sections_llm_already_populated(monkeypatch):
    calls = {"image": 0, "video": 0}

    def fake_image(query, num_results=3):
        calls["image"] += 1
        return {"images": []}

    def fake_video(query, num_results=2):
        calls["video"] += 1
        return {"videos": []}

    monkeypatch.setattr(researcher_module, "_image_search_impl", fake_image)
    monkeypatch.setattr(researcher_module, "_video_search_impl", fake_video)

    agent = _agent_with_output(_REAL_BROLL_MESSAGE)
    result = await agent.process_message("TOPIC: SpaceX Starship V3\n\nBegin your work.")

    assert calls["image"] == 0
    assert calls["video"] == 0
    assert "https://images.example.com/real1.jpg | A real image" in result["response"]
    assert "https://videos.example.com/real1.mp4 | A real clip | 12s" in result["response"]


async def test_leaves_placeholder_text_when_deterministic_search_also_finds_nothing(monkeypatch):
    monkeypatch.setattr(researcher_module, "_image_search_impl", lambda query, num_results=3: {"images": []})
    monkeypatch.setattr(researcher_module, "_video_search_impl", lambda query, num_results=2: {"videos": []})

    agent = _agent_with_output(_EMPTY_BROLL_MESSAGE)
    result = await agent.process_message("TOPIC: SpaceX Starship V3\n\nBegin your work.")

    assert result["success"] is True
    assert "No image URLs were provided" in result["response"]
    assert "No video URLs were provided" in result["response"]


async def test_skips_backstop_entirely_when_topic_missing(monkeypatch):
    calls = {"image": 0, "video": 0}
    monkeypatch.setattr(
        researcher_module, "_image_search_impl",
        lambda query, num_results=3: calls.__setitem__("image", calls["image"] + 1) or {"images": []},
    )
    monkeypatch.setattr(
        researcher_module, "_video_search_impl",
        lambda query, num_results=2: calls.__setitem__("video", calls["video"] + 1) or {"videos": []},
    )

    agent = _agent_with_output(_EMPTY_BROLL_MESSAGE)
    result = await agent.process_message("No topic line in this message at all.\n\nBegin your work.")

    assert calls["image"] == 0
    assert calls["video"] == 0
    assert result["response"] == _EMPTY_BROLL_MESSAGE


async def test_backfill_exception_does_not_break_the_response(monkeypatch):
    def boom(query, num_results=3):
        raise RuntimeError("simulated Tavily outage")

    monkeypatch.setattr(researcher_module, "_image_search_impl", boom)
    monkeypatch.setattr(researcher_module, "_video_search_impl", lambda query, num_results=2: {"videos": []})

    agent = _agent_with_output(_EMPTY_BROLL_MESSAGE)
    result = await agent.process_message("TOPIC: SpaceX Starship V3\n\nBegin your work.")

    assert result["success"] is True
    assert "No image URLs were provided" in result["response"]
