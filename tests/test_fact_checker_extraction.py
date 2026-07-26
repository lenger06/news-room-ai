"""
Unit tests for the Phase 3 deterministic claim-extraction helpers in
agents/fact_checker/agent.py: quotes and statistic sentences, in addition to
the pre-existing named-official extraction. Pure regex/string functions —
no LLM or API calls.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from agents.fact_checker.agent import (
    _extract_quotes,
    _extract_statistic_sentences,
    _sentence_containing,
    _OFFICIAL_RE,
)


def test_extract_quotes_finds_direct_quote():
    text = 'The senator said, "We will not back down from this fight for the American people."'
    quotes = _extract_quotes(text)
    assert quotes == ["We will not back down from this fight for the American people."]


def test_extract_quotes_ignores_short_fragments():
    # Short quoted fragments like "OK" or a scare-quoted single word shouldn't count as
    # a checkable direct quote — the 15-char minimum in _QUOTE_RE filters these out.
    text = 'He called it "OK" but declined to elaborate further on the record.'
    assert _extract_quotes(text) == []


def test_extract_quotes_deduplicates():
    text = 'She said, "The economy is improving steadily." Later she repeated, "The economy is improving steadily."'
    assert _extract_quotes(text) == ["The economy is improving steadily."]


def test_extract_statistic_sentences_finds_casualty_figure():
    text = "Officials say at least 40 people were killed when the building collapsed overnight. Rescue efforts are ongoing."
    sentences = _extract_statistic_sentences(text)
    assert len(sentences) == 1
    assert "40 people were killed" in sentences[0]
    assert "Rescue efforts" not in sentences[0]


def test_extract_statistic_sentences_finds_percentage():
    text = "Inflation rose 12 percent over the past year, according to the latest report."
    sentences = _extract_statistic_sentences(text)
    assert sentences == ["Inflation rose 12 percent over the past year, according to the latest report."]


def test_extract_statistic_sentences_no_false_positive_on_bare_year():
    text = "The policy was first introduced in 2019 and has been revised twice since."
    assert _extract_statistic_sentences(text) == []


def test_extract_statistic_sentences_deduplicates_same_sentence_multiple_matches():
    text = "The storm caused 3 million dollars in damage and displaced 200 residents from their homes."
    sentences = _extract_statistic_sentences(text)
    # Both "million" and "displaced" match within the same sentence — must not duplicate it.
    assert len(sentences) == 1


def test_sentence_containing_extracts_full_sentence_around_match():
    text = "First sentence here. The city reported 15 injuries in the incident. Third sentence follows."
    idx = text.index("15 injuries")
    result = _sentence_containing(text, idx, idx + len("15 injuries"))
    assert result == "The city reported 15 injuries in the incident."


def test_official_extraction_still_works_alongside_new_patterns():
    text = 'President Martinez met with Secretary of State Alvarez to discuss the "unprecedented crisis," which affected 2 million residents.'
    officials = list(dict.fromkeys(m.group(0) for m in _OFFICIAL_RE.finditer(text)))
    quotes = _extract_quotes(text)
    stats = _extract_statistic_sentences(text)

    assert any("Martinez" in o for o in officials)
    assert any("Alvarez" in o for o in officials)
    assert quotes == ["unprecedented crisis,"]
    assert len(stats) == 1
    assert "2 million residents" in stats[0]
