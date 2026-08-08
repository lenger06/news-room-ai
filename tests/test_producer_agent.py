"""
Regression guard for two related incidents:

2026-08-07: Producer had file_operations_tool available and, prompted to "confirm all
output files are saved correctly," used it to independently verify files via a
guessed/default directory (the tool's own docstring advertises ./output/articles as a
default) — which no longer matches the per-run directory every production actually
uses, so it reported real, successfully-saved files as "not available". Fixed by
removing the tool and pointing the prompt at paths already in context.

2026-08-08: removing that tool left Producer with zero tools, still routed through
create_openai_functions_agent/AgentExecutor — which sends an empty `functions` array
to the OpenAI API regardless, and the API rejects that outright
(invalid_request_error: "Invalid 'functions': empty array"). Broke every single run.
Fixed by dropping the tool-calling agent entirely for a direct chat call, since
Producer genuinely has no tools to call.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import agents.producer.agent as producer_module
from agents.producer.agent import Agent as ProducerAgent
from agents.producer.prompts import PRODUCER_PROMPT


def test_producer_has_no_tool_calling_agent():
    """Regression guard: must not route through create_openai_functions_agent /
    AgentExecutor at all — that's what sent the empty functions array that broke
    every run on 2026-08-08. A plain ChatOpenAI instance has no such parameter."""
    agent = ProducerAgent()
    assert not hasattr(agent, "tools")
    assert not hasattr(agent, "executor")
    assert isinstance(agent.llm, producer_module.ChatOpenAI)


def test_producer_prompt_tells_it_not_to_guess_a_directory():
    assert "do not guess" in PRODUCER_PROMPT.lower()


def test_producer_prompt_tells_it_to_use_context_paths():
    assert "already state" in PRODUCER_PROMPT.lower() or "already in your context" in PRODUCER_PROMPT.lower()


async def test_process_message_uses_a_direct_chat_call(monkeypatch):
    """The core regression guard: process_message must succeed via a plain LLM
    invoke, never touching any tool-calling / functions machinery."""
    agent = ProducerAgent()

    captured_messages = []

    class _FakeResponse:
        content = "Production summary: all good."

    def fake_invoke(self, messages):
        captured_messages.extend(messages)
        return _FakeResponse()

    monkeypatch.setattr(type(agent.llm), "invoke", fake_invoke)

    result = await agent.process_message("=== WRITER OUTPUT ===\nArticle saved to ./output/x/articles/a.md")

    assert result["success"] is True
    assert result["response"] == "Production summary: all good."
    assert len(captured_messages) == 2  # SystemMessage + HumanMessage, nothing else


async def test_process_message_reports_failure_on_llm_error(monkeypatch):
    agent = ProducerAgent()

    def fake_invoke(self, messages):
        raise RuntimeError("simulated API failure")

    monkeypatch.setattr(type(agent.llm), "invoke", fake_invoke)

    result = await agent.process_message("some input")

    assert result["success"] is False
    assert "simulated API failure" in result["response"]
