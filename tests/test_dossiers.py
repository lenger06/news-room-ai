"""
Unit tests for tools/dossiers.py — the Phase 4 evolving per-story markdown memory,
the cheap alternative to a vector DB chosen for SELF_IMPROVEMENT_ROADMAP.md Phase 4.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import tools.dossiers as dossiers


def _patch_dir(tmp_path, monkeypatch):
    d = tmp_path / "dossiers"
    monkeypatch.setattr(dossiers, "_DOSSIER_DIR", d)
    monkeypatch.setattr(dossiers, "_INDEX_PATH", d / "_index.json")
    return d


def test_create_new_dossier_and_append_entry(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)

    slug = dossiers.get_or_create_slug("Iran shipping dispute", ["iran", "hormuz", "shipping"])
    dossiers.append_entry(slug, "Iran shipping dispute", ["iran", "hormuz", "shipping"],
                           "Tankers rerouted after new sanctions announced.", show_slug="evening-news")

    content = dossiers.read_dossier(slug)
    assert "# Iran shipping dispute" in content
    assert "Tankers rerouted after new sanctions announced." in content
    assert "(evening-news)" in content


def test_second_story_with_overlapping_keywords_matches_existing_dossier(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)

    slug1 = dossiers.get_or_create_slug("Iran shipping dispute", ["iran", "hormuz", "shipping"])
    dossiers.append_entry(slug1, "Iran shipping dispute", ["iran", "hormuz", "shipping"], "First development.")

    # New story shares 2+ keywords ("iran", "hormuz") — should match, not create a new dossier.
    slug2 = dossiers.get_or_create_slug("Hormuz tensions escalate", ["iran", "hormuz", "navy"])
    assert slug2 == slug1

    dossiers.append_entry(slug2, "Hormuz tensions escalate", ["iran", "hormuz", "navy"], "Second development.")
    content = dossiers.read_dossier(slug1)
    assert "First development." in content
    assert "Second development." in content


def test_unrelated_story_creates_a_different_dossier(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)

    slug1 = dossiers.get_or_create_slug("Iran shipping dispute", ["iran", "hormuz", "shipping"])
    slug2 = dossiers.get_or_create_slug("Local election results", ["election", "mayor", "city-council"])
    assert slug1 != slug2


def test_find_matching_slug_is_read_only_and_does_not_create(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)

    assert dossiers.find_matching_slug(["iran", "hormuz"]) is None
    # No dossier should have been created as a side effect of the lookup.
    assert dossiers._load_index() == {}


def test_find_matching_slug_requires_minimum_overlap(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)
    dossiers.get_or_create_slug("Iran shipping dispute", ["iran", "hormuz", "shipping"])

    # Only 1 shared keyword ("iran") — below the 2-keyword minimum, should not match.
    assert dossiers.find_matching_slug(["iran", "election", "mayor"]) is None


def test_format_for_prompt_truncates_to_most_recent_entries(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)
    slug = dossiers.get_or_create_slug("Ongoing story", ["ongoing", "story"])
    for i in range(5):
        dossiers.append_entry(slug, "Ongoing story", ["ongoing", "story"], f"Update number {i}.")

    formatted = dossiers.format_for_prompt(slug, max_chars=200)
    assert len(formatted) <= 250  # title block + kept sections, roughly bounded
    assert "Update number 4." in formatted  # most recent must survive truncation


def test_entries_per_dossier_are_capped(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(dossiers, "_MAX_ENTRIES_PER_DOSSIER", 3)
    slug = dossiers.get_or_create_slug("Frequently updated story", ["frequent", "updates"])
    for i in range(6):
        dossiers.append_entry(slug, "Frequently updated story", ["frequent", "updates"], f"Entry {i}.")

    content = dossiers.read_dossier(slug)
    assert content.count("## ") == 3
    assert "Entry 5." in content
    assert "Entry 0." not in content


def test_dossier_count_is_pruned_when_exceeding_max(tmp_path, monkeypatch):
    _patch_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(dossiers, "_MAX_DOSSIERS", 3)

    slugs = []
    for i in range(5):
        slug = dossiers.get_or_create_slug(f"Story {i}", [f"topic{i}", f"unique{i}"])
        dossiers.append_entry(slug, f"Story {i}", [f"topic{i}", f"unique{i}"], f"Entry for story {i}.")
        slugs.append(slug)

    index = dossiers._load_index()
    assert len(index) == 3
    # The oldest (first-created) dossiers should have been pruned, most recent kept.
    assert slugs[-1] in index
    assert slugs[0] not in index
