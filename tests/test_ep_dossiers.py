"""
Unit tests for the Phase 4 dossier wiring in executive_producer/agent.py:
matching a dossier during the dedup-check node, injecting it into
researcher/writer step input, and appending an entry after a successful run.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import agents.executive_producer.agent as ep_module
import tools.dossiers as dossiers_module
import tools.story_history as story_history_module


def _patch_dir(tmp_path, monkeypatch):
    d = tmp_path / "dossiers"
    monkeypatch.setattr(dossiers_module, "_DOSSIER_DIR", d)
    monkeypatch.setattr(dossiers_module, "_INDEX_PATH", d / "_index.json")
    return d


async def test_dedup_check_finds_matching_dossier(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)
    dossiers_module.get_or_create_slug("Iran shipping dispute", ["iran", "hormuz", "shipping"])
    dossiers_module.append_entry(
        dossiers_module.find_matching_slug(["iran", "hormuz"]),
        "Iran shipping dispute", ["iran", "hormuz"], "Tankers rerouted after sanctions.",
    )

    ep = ep_module.Agent()
    state = {
        "workflow": "ARTICLE",
        "request": "Iran shipping update",
        "topic": "Iran shipping update",
        "keywords": ["iran", "hormuz", "tankers"],
    }
    result = await ep._dedup_check_node(state)

    assert result["dossier_slug"] != ""
    assert "Tankers rerouted after sanctions." in result["dossier_context"]


async def test_dedup_check_no_dossier_when_nothing_matches(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)
    ep = ep_module.Agent()
    state = {
        "workflow": "ARTICLE",
        "request": "Brand new topic",
        "topic": "Brand new topic",
        "keywords": ["brand", "new"],
    }
    result = await ep._dedup_check_node(state)
    assert result["dossier_slug"] == ""
    assert result["dossier_context"] == ""


class _CapturingAgent:
    def __init__(self):
        self.received_message = None

    async def process_message(self, message, context=None):
        self.received_message = message
        return {"success": True, "response": "research brief text", "agent": "researcher"}


async def test_execute_step_injects_dossier_context_for_researcher(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)
    capturing_agent = _CapturingAgent()

    async def fake_get_agent(name):
        return capturing_agent

    ep = ep_module.Agent()
    monkeypatch.setattr(ep_module.agent_registry, "get_agent", fake_get_agent)

    state = {
        "steps": ["researcher"],
        "current_step_index": 0,
        "request": "test request",
        "topic": "test topic",
        "outputs": {},
        "workflow": "ARTICLE",
        "dossier_context": "# Iran shipping dispute\n\n## 2026-07-20\n\nTankers rerouted.",
    }
    await ep._execute_step_node(state)

    assert "STORY DOSSIER" in capturing_agent.received_message
    assert "Tankers rerouted." in capturing_agent.received_message


def test_dossier_summary_snippet_strips_editors_note():
    article = (
        "First paragraph of the article.\n\n"
        "Second paragraph with more detail.\n\n"
        "## EDITOR'S NOTE\n- Changed a title\n"
    )
    snippet = ep_module._dossier_summary_snippet(article)
    assert "EDITOR'S NOTE" not in snippet
    assert "First paragraph of the article." in snippet
    assert "Second paragraph with more detail." in snippet


async def test_summarise_node_appends_dossier_entry_on_success(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(story_history_module, "_LOG_PATH", tmp_path / "story_history.json")
    # _summarise_node also writes a hardcoded "./output/last_broadcast.json" inline (not a
    # patchable module constant) — chdir into tmp_path so that relative write, and any other
    # unlisted one, lands here instead of polluting the real project's live output/ directory.
    monkeypatch.chdir(tmp_path)

    ep = ep_module.Agent()
    state = {
        "workflow": "ARTICLE",
        "topic": "Iran shipping dispute",
        "keywords": ["iran", "hormuz", "shipping"],
        "steps": ["writer"],
        "outputs": {"writer": "Tankers were rerouted today after new sanctions were announced.\n\nOfficials say the impact will be significant."},
        "desk_name": "",
        "anchor_name": "",
        "show_slug": "evening-news",
        "show_name": "Evening News",
        "researcher_failed": False,
        "anchor_failed": False,
        "needs_human_review": False,
        "dedup_suppressed": False,
        "output_dir": str(tmp_path / "run1"),
    }
    await ep._summarise_node(state)

    slug = dossiers_module.find_matching_slug(["iran", "hormuz"])
    assert slug is not None
    content = dossiers_module.read_dossier(slug)
    assert "Tankers were rerouted today after new sanctions were announced." in content
