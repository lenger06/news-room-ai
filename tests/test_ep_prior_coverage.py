"""
Unit tests for the Phase 0.3 change: story_history matches surfaced to the writer
as prior-coverage context, instead of being discarded after the dedup gate.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import agents.executive_producer.agent as ep_module
import tools.story_history as story_history_module


class _FakeDedupLLMResponse:
    def __init__(self, content):
        self.content = content


async def test_dedup_check_sets_prior_coverage_on_proceed(monkeypatch):
    fake_similar = [
        {
            "topic": "Iran shipping dispute",
            "keywords": ["iran", "shipping"],
            "ts": "2026-07-20T10:00:00+00:00",
            "ts_unix": 1000.0,
        },
    ]
    monkeypatch.setattr(
        story_history_module, "find_similar",
        lambda keywords, hours=168.0, min_overlap=2: fake_similar,
    )

    async def fake_ainvoke(self, messages, *args, **kwargs):
        return _FakeDedupLLMResponse('{"decision": "PROCEED", "reason": "different angle, new development"}')

    ep = ep_module.Agent()
    # ChatOpenAI is a pydantic model — instance-level attribute patching is rejected
    # by its __setattr__, so patch the class method instead.
    monkeypatch.setattr(type(ep.llm), "ainvoke", fake_ainvoke)

    state = {
        "workflow": "ARTICLE",
        "request": "Iran shipping update",
        "topic": "Iran shipping update",
        "keywords": ["iran", "shipping", "hormuz"],
    }
    result = await ep._dedup_check_node(state)

    assert result["dedup_suppressed"] is False
    assert "Iran shipping dispute" in result["prior_coverage"]


async def test_dedup_check_no_prior_coverage_when_nothing_similar(monkeypatch):
    monkeypatch.setattr(
        story_history_module, "find_similar",
        lambda keywords, hours=168.0, min_overlap=2: [],
    )
    ep = ep_module.Agent()
    state = {
        "workflow": "ARTICLE",
        "request": "Brand new topic",
        "topic": "Brand new topic",
        "keywords": ["brand", "new", "topic"],
    }
    result = await ep._dedup_check_node(state)
    assert result["prior_coverage"] == ""


class _CapturingAgent:
    def __init__(self):
        self.received_message = None

    async def process_message(self, message, context=None):
        self.received_message = message
        return {"success": True, "response": "article text", "agent": "writer"}


async def test_execute_step_injects_prior_coverage_for_writer(monkeypatch):
    capturing_agent = _CapturingAgent()

    async def fake_get_agent(name):
        assert name == "writer"
        return capturing_agent

    ep = ep_module.Agent()
    monkeypatch.setattr(ep_module.agent_registry, "get_agent", fake_get_agent)

    state = {
        "steps": ["writer"],
        "current_step_index": 0,
        "request": "test request",
        "topic": "test topic",
        "outputs": {},
        "workflow": "ARTICLE",
        "prior_coverage": "[2026-07-20 10:00 UTC] Iran shipping dispute  (keywords: iran, shipping)",
    }
    await ep._execute_step_node(state)

    assert capturing_agent.received_message is not None
    assert "PRIOR COVERAGE" in capturing_agent.received_message
    assert "Iran shipping dispute" in capturing_agent.received_message


async def test_execute_step_no_prior_coverage_block_when_empty(monkeypatch):
    capturing_agent = _CapturingAgent()

    async def fake_get_agent(name):
        return capturing_agent

    ep = ep_module.Agent()
    monkeypatch.setattr(ep_module.agent_registry, "get_agent", fake_get_agent)

    state = {
        "steps": ["writer"],
        "current_step_index": 0,
        "request": "test request",
        "topic": "test topic",
        "outputs": {},
        "workflow": "ARTICLE",
        "prior_coverage": "",
    }
    await ep._execute_step_node(state)

    assert "PRIOR COVERAGE" not in capturing_agent.received_message
