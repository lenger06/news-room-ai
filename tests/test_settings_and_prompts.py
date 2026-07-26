"""
Small regression-guard tests for Phase 0 changes:
- settings.model_for() per-agent override behavior
- fact_checker's adversarial prompt rewrite still contains the exact verdict
  strings agents/fact_checker/agent.py parses via substring match — a prompt
  wording change that silently dropped one of these would break verdict
  detection without raising any error.
- the writer prompt acknowledges the new PRIOR COVERAGE block.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from config.settings import settings
from agents.fact_checker.prompts import FACT_CHECKER_PROMPT
from agents.compliance_checker.prompts import COMPLIANCE_CHECKER_PROMPT
from agents.writer.prompts import WRITER_PROMPT


def test_model_for_returns_default_for_known_role():
    assert settings.model_for("writer") == settings.MODELS["writer"]


def test_model_for_falls_back_to_gpt4o_for_unknown_role():
    assert settings.model_for("some_future_agent") == "gpt-4o"


def test_all_current_agent_roles_have_a_model_entry():
    for role in (
        "executive_producer", "researcher", "writer", "fact_checker", "editor",
        "script_writer", "anchor", "video_editor", "producer", "publisher",
        "breaking_news_checker", "compliance_checker",
    ):
        assert role in settings.MODELS, f"missing MODELS entry for {role}"


def test_fact_checker_prompt_preserves_parsed_verdict_strings():
    # agents/fact_checker/agent.py does an exact substring match for these three
    # lines — if the adversarial rewrite dropped/reworded one, verdict parsing
    # would silently fall back to "UNKNOWN" for every run.
    for verdict in ("CLEAR TO PUBLISH", "PUBLISH WITH NOTES", "HOLD FOR CORRECTIONS"):
        assert verdict in FACT_CHECKER_PROMPT

    for section in ("## VERIFIED", "## UNVERIFIED", "## CORRECTIONS NEEDED", "## VERDICT"):
        assert section in FACT_CHECKER_PROMPT


def test_compliance_checker_prompt_has_parsed_verdict_strings():
    for verdict in ("CLEAR TO PUBLISH", "HOLD FOR REVIEW"):
        assert verdict in COMPLIANCE_CHECKER_PROMPT
    assert "## RECOMMENDATION" in COMPLIANCE_CHECKER_PROMPT


def test_writer_prompt_mentions_prior_coverage_handling():
    assert "PRIOR COVERAGE" in WRITER_PROMPT
