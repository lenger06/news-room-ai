"""
Unit tests for the Pixabay tag-overlap relevance filter in tools/video_search_tool.py.
Regression guard for a real incident (2026-07-28): a generic "space" stock clip tagged
with buzzwords including "spacex" ranked in Pixabay's top 2 results for the query
"SpaceX Starship V3 launch and program history" — sharing only that one word — and the
actual footage was an unrelated aerial desert shot that made it all the way into a
published broadcast. No real Pixabay calls here; requests.get is mocked throughout.
"""
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import tools.video_search_tool as video_search_tool
from tools.video_search_tool import _video_search_impl, _significant_words, _tag_overlap_count


class _FakeResp:
    def __init__(self, ok=True, json_data=None, status_code=200, text=""):
        self.ok = ok
        self._json = json_data or {}
        self.status_code = status_code
        self.text = text

    def json(self):
        return self._json


def _hit(tags: str, url: str = "https://cdn.pixabay.com/video/x.mp4", height: int = 480) -> dict:
    return {
        "tags": tags,
        "duration": 10,
        "videos": {"medium": {"url": url, "height": height, "width": 640}},
    }


def test_significant_words_strips_stopwords_and_short_tokens():
    words = _significant_words("SpaceX Starship V3 launch and program history")
    assert words == {"spacex", "starship", "launch", "program", "history"}


def test_tag_overlap_count_counts_shared_significant_words():
    query_words = {"cargo", "ship", "sailing", "ocean"}
    assert _tag_overlap_count("cargo ship, ocean freight, sailing vessel", query_words) == 4
    assert _tag_overlap_count("scientist, mars, nasa, spacex", {"spacex", "starship", "launch"}) == 1


def test_rejects_single_buzzword_coincidence_match(monkeypatch):
    """The real incident: a generic 'space' stock clip shares only 'spacex' with the
    query and must be rejected, not returned as the top hit."""
    monkeypatch.setattr(video_search_tool.settings, "PIXABAY_API_KEY", "fake-key")

    irrelevant = _hit(
        "scientist, mars, planet, space, galaxy, universe, cosmos, astronomer, "
        "astronomy, telescope, science, alien, nasa, astronautics, research, spacex, globe"
    )
    relevant = _hit("spacex starship launch liftoff rocket program")

    monkeypatch.setattr(
        video_search_tool.requests, "get",
        lambda *a, **k: _FakeResp(json_data={"hits": [irrelevant, relevant]}),
    )

    result = _video_search_impl("SpaceX Starship V3 launch and program history", num_results=3)

    assert len(result["videos"]) == 1
    assert result["videos"][0]["description"] == "spacex starship launch liftoff rocket program"


def test_accepts_well_matched_clip(monkeypatch):
    monkeypatch.setattr(video_search_tool.settings, "PIXABAY_API_KEY", "fake-key")
    good = _hit("cargo ship sailing across the ocean, freight vessel")
    monkeypatch.setattr(
        video_search_tool.requests, "get",
        lambda *a, **k: _FakeResp(json_data={"hits": [good]}),
    )
    result = _video_search_impl("cargo ship sailing ocean", num_results=3)
    assert len(result["videos"]) == 1


def test_short_query_only_requires_available_significant_words(monkeypatch):
    """A one-meaningful-word query can't demand 2 overlapping words — must fall back
    to requiring just the word(s) it actually has, not block everything."""
    monkeypatch.setattr(video_search_tool.settings, "PIXABAY_API_KEY", "fake-key")
    hit = _hit("earthquake damage rubble debris")
    monkeypatch.setattr(
        video_search_tool.requests, "get",
        lambda *a, **k: _FakeResp(json_data={"hits": [hit]}),
    )
    result = _video_search_impl("earthquake", num_results=3)
    assert len(result["videos"]) == 1


def test_no_significant_words_in_query_disables_filtering(monkeypatch):
    """Degenerate case: a query that's entirely stopwords/short tokens must not crash
    or block everything — falls back to the old unfiltered behavior."""
    monkeypatch.setattr(video_search_tool.settings, "PIXABAY_API_KEY", "fake-key")
    hit = _hit("random unrelated tags here")
    monkeypatch.setattr(
        video_search_tool.requests, "get",
        lambda *a, **k: _FakeResp(json_data={"hits": [hit]}),
    )
    result = _video_search_impl("V3 at of", num_results=3)
    assert len(result["videos"]) == 1


def test_returns_empty_when_nothing_clears_the_bar(monkeypatch):
    monkeypatch.setattr(video_search_tool.settings, "PIXABAY_API_KEY", "fake-key")
    irrelevant = _hit("scientist, mars, nasa, spacex, globe")
    monkeypatch.setattr(
        video_search_tool.requests, "get",
        lambda *a, **k: _FakeResp(json_data={"hits": [irrelevant]}),
    )
    result = _video_search_impl("SpaceX Starship V3 launch and program history", num_results=3)
    assert result["videos"] == []
