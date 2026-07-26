"""
Story dossiers — evolving per-thread markdown files that accumulate a running
account of an ongoing story ("Iran/Hormuz shipping", "Ukraine ceasefire talks"),
distinct from story_history.py's flat dedup log (which only tracks *that* a
topic was covered, not *what's actually known about it*).

A dossier is matched or created by keyword overlap against config.settings-style
extracted keywords (the same 2+ shared keyword convention already used by
story_history.find_similar and breaking_news_log.same_story_fire_count), then
surfaced to the researcher/writer as accumulated context and appended to after
a successful production.

This is the cheap alternative to a vector DB described in
SELF_IMPROVEMENT_ROADMAP.md Phase 4 — plain files, no embeddings.
"""

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DOSSIER_DIR = Path("./output/dossiers")
_INDEX_PATH = _DOSSIER_DIR / "_index.json"
_MAX_DOSSIERS = 150       # bounded like story_history/breaking_news_log's entry caps
_MIN_KEYWORD_OVERLAP = 2  # same convention as story_history.find_similar
_MAX_ENTRIES_PER_DOSSIER = 30
_SLUG_RE = re.compile(r'[^a-z0-9]+')


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("_", text.lower()).strip("_")
    return slug[:60] or "story"


def _load_index() -> dict:
    if _INDEX_PATH.exists():
        try:
            return json.loads(_INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_index(index: dict) -> None:
    _DOSSIER_DIR.mkdir(parents=True, exist_ok=True)
    try:
        _INDEX_PATH.write_text(json.dumps(index, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning(f"[dossiers] Could not save index: {e}")


def _prune_oldest(index: dict) -> dict:
    if len(index) <= _MAX_DOSSIERS:
        return index
    ordered = sorted(index.items(), key=lambda kv: kv[1].get("updated_unix", 0))
    to_remove = ordered[: len(index) - _MAX_DOSSIERS]
    for slug, _ in to_remove:
        index.pop(slug, None)
        try:
            (_DOSSIER_DIR / f"{slug}.md").unlink(missing_ok=True)
        except Exception:
            pass
        logger.info(f"[dossiers] Pruned inactive dossier: {slug}")
    return index


def find_matching_slug(keywords: list[str]) -> str | None:
    """Return the slug of an existing dossier sharing 2+ keywords with the given list,
    preferring the most recently updated match if more than one qualifies."""
    kw_set = {k.lower() for k in keywords}
    if len(kw_set) < _MIN_KEYWORD_OVERLAP:
        return None
    index = _load_index()
    candidates = [
        (slug, entry) for slug, entry in index.items()
        if len(kw_set & {k.lower() for k in entry.get("keywords", [])}) >= _MIN_KEYWORD_OVERLAP
    ]
    if not candidates:
        return None
    candidates.sort(key=lambda kv: kv[1].get("updated_unix", 0), reverse=True)
    return candidates[0][0]


def get_or_create_slug(topic: str, keywords: list[str]) -> str:
    """Match an existing dossier by keyword overlap, or create a new one for this topic."""
    existing = find_matching_slug(keywords)
    if existing:
        return existing

    index = _load_index()
    base_slug = _slugify(topic)
    slug = base_slug
    n = 2
    while slug in index:
        slug = f"{base_slug}_{n}"
        n += 1

    now = datetime.now(timezone.utc)
    index[slug] = {
        "title": topic,
        "keywords": keywords,
        "created": now.isoformat(),
        "updated": now.isoformat(),
        "updated_unix": now.timestamp(),
        "entry_count": 0,
    }
    index = _prune_oldest(index)
    _save_index(index)
    logger.info(f"[dossiers] Created new dossier: {slug} ({topic[:80]})")
    return slug


def append_entry(slug: str, topic: str, keywords: list[str], summary: str, show_slug: str = "") -> None:
    """Append a dated entry to a dossier, creating it if needed, and refresh its index
    metadata (keywords are merged, not replaced, so a thread's keyword set grows with it)."""
    _DOSSIER_DIR.mkdir(parents=True, exist_ok=True)
    path = _DOSSIER_DIR / f"{slug}.md"
    now = datetime.now(timezone.utc)

    index = _load_index()
    entry = index.get(slug, {
        "title": topic, "keywords": [], "created": now.isoformat(), "entry_count": 0,
    })
    merged_keywords = list(dict.fromkeys(entry.get("keywords", []) + keywords))
    entry.update({
        "title": entry.get("title") or topic,
        "keywords": merged_keywords,
        "updated": now.isoformat(),
        "updated_unix": now.timestamp(),
        "entry_count": entry.get("entry_count", 0) + 1,
    })
    index[slug] = entry
    index = _prune_oldest(index)
    _save_index(index)

    date_str = now.strftime("%Y-%m-%d %H:%M UTC")
    show_note = f" ({show_slug})" if show_slug else ""
    new_section = f"## {date_str}{show_note}\n\n{summary.strip()}\n\n"

    try:
        if path.exists():
            combined = path.read_text(encoding="utf-8") + new_section
        else:
            combined = f"# {topic}\n\n" + new_section

        # Cap entries per file by keeping only the most recent _MAX_ENTRIES_PER_DOSSIER
        # "## " sections — prevents an extremely long-running story from growing unbounded.
        sections = combined.split("\n## ")
        if len(sections) - 1 > _MAX_ENTRIES_PER_DOSSIER:
            title_block = sections[0]
            kept = sections[1:][-_MAX_ENTRIES_PER_DOSSIER:]
            combined = title_block + "".join(f"\n## {s}" for s in kept)

        path.write_text(combined, encoding="utf-8")
        logger.info(f"[dossiers] Appended entry to {slug} ({path})")
    except Exception as e:
        logger.warning(f"[dossiers] Could not write {path}: {e}")


def read_dossier(slug: str) -> str:
    path = _DOSSIER_DIR / f"{slug}.md"
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def format_for_prompt(slug: str, max_chars: int = 3000) -> str:
    """Return dossier content for injection into an agent prompt, keeping the most
    recent entries if the full file would exceed max_chars."""
    text = read_dossier(slug)
    if not text or len(text) <= max_chars:
        return text
    sections = text.split("\n## ")
    title_block = sections[0]
    kept: list[str] = []
    total = len(title_block)
    for s in reversed(sections[1:]):
        piece = f"\n## {s}"
        if total + len(piece) > max_chars:
            break
        kept.insert(0, piece)
        total += len(piece)
    return title_block + "".join(kept)
