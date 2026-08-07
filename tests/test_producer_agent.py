"""
Regression guard for the 2026-08-07 incident: Producer had file_operations_tool
available and, prompted to "confirm all output files are saved correctly," used it to
independently verify files via a guessed/default directory (the tool's own docstring
advertises ./output/articles as a default) — which no longer matches the per-run
directory every production actually uses, so it reported real, successfully-saved
files as "not available". Producer now has no tools at all and is told to read paths
directly from the Writer/Script Writer outputs already in its context.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from agents.producer.agent import Agent as ProducerAgent
from agents.producer.prompts import PRODUCER_PROMPT


def test_producer_has_no_tools():
    agent = ProducerAgent()
    assert agent.tools == []


def test_producer_prompt_tells_it_not_to_guess_a_directory():
    assert "do not guess" in PRODUCER_PROMPT.lower()


def test_producer_prompt_tells_it_to_use_context_paths():
    assert "already state" in PRODUCER_PROMPT.lower() or "already in your context" in PRODUCER_PROMPT.lower()
