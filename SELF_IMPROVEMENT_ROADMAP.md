# News Room AI — Self-Improvement Roadmap

Written 2026-07-25 after an architecture audit. Goal: move from a periodic,
linear pipeline toward an event-driven, self-correcting, self-improving one —
without rebuilding what already works.

**Baseline reality check** (see audit notes inline below each phase): agents
for researcher/writer/fact_checker/editor/script_writer/anchor/video_editor/
producer/publisher already exist and are orchestrated by a LangGraph
`StateGraph` in `agents/executive_producer/agent.py`. HeyGen generation,
FFmpeg compositing, and YouTube publishing are real and automated end to end.
The gaps are: the orchestration graph is a hardcoded linear step list with no
branching/retry; fact-checker's verdict is computed but never read by the EP;
there's no compliance gate; "memory" is a keyword-overlap dedup log, not a
queryable knowledge store; and scheduling/triggering lives entirely outside
this repo (in the external "Jarvis" caller).

Phases are ordered by leverage-per-effort, not by the order in the original
wishlist. Each can mostly be built independently; noted where one benefits
from another landing first.

---

## Phase 0 — Foundations (cheap, low-risk, do first)

These are small, isolated changes that make every later phase easier or safer.

1. **Centralize model selection.** Every agent currently hardcodes its own
   `ChatOpenAI(model="gpt-4o", ...)` independently (9 separate call sites).
   Move this into `config/settings.py` as a per-role model map (e.g.
   `MODELS = {"writer": "gpt-4o", "fact_checker": "gpt-4o", ...}`) so later
   phases can tier models (cheap/fast for routine drafting, stronger
   reasoning model for adversarial fact-check or compliance) without editing
   every agent file.
2. **Adversarial fact-checker prompt.** `agents/fact_checker/prompts.py` is
   currently a neutral verification checklist. Reframe it to an
   assume-false-until-corroborated stance ("your job is to find the strongest
   case that this draft is wrong, not to confirm it's right"). Prompt-only
   change, no code/architecture impact. Do this before Phase 3 deepens the
   fact-checker's scope.
3. **Surface `story_history.find_similar()` to the writer.** This data is
   already computed for the dedup gate in `_dedup_check_node`
   (`executive_producer/agent.py`) and then discarded. Pass the matched prior
   entries into the writer's context as "prior coverage" so scripts can
   reference continuity ("as we reported last week...") — no new
   infrastructure needed, just wiring existing output further downstream.
4. **Harden the YouTube token refresh path.** `recreate_tokens.py` is a fully
   manual, human-in-the-loop OAuth flow; if the refresh token in
   `tools/youtube_tool.py` ever becomes invalid, publishing silently fails
   with no alert. Add a check that surfaces (log at ERROR + return a clear
   failure reason to the caller) rather than failing quietly, so a broken
   token doesn't go unnoticed for days.

---

## Phase 1 — Self-correcting orchestration (highest leverage)

This is the single biggest gap versus "self-correcting": the fact-checker
already does independent verification work, but `_route_after_step` in
`executive_producer/agent.py` just walks a static `WORKFLOW_STEPS` list
regardless of what fact_checker concluded.

- Replace the linear step-list routing with a conditional edge that reads the
  fact-checker's `verdict` field. On `HOLD_FOR_CORRECTIONS`, route back to
  writer (or editor, depending on severity) instead of always proceeding to
  editor→script_writer.
- Add a bounded retry counter in graph state (e.g. `max_retries = 2`). If
  still failing after retries, **hard-abort before publish** and write the
  draft + fact-check notes to a "needs human review" output folder, rather
  than either publishing a flawed script or silently discarding the run.
- Same pattern applies to the editor's corrections — right now editor patches
  once and nothing re-checks the patch. Consider a single re-verification
  pass after editor applies corrections, bounded by the same retry counter to
  avoid infinite loops.

This phase touches only `executive_producer/agent.py`'s graph wiring — no new
agents required.

---

## Phase 2 — Compliance/safety gate

Currently absent entirely: publisher uploads directly to YouTube
(`agents/publisher/agent.py`) with no policy screening step.

- Add a `compliance_checker` agent, structurally similar to `fact_checker`
  (LLM + prompt + verdict), reviewing the final script for YouTube Community
  Guidelines risk (graphic violence, hate speech, copyright flags,
  misinformation policy).
- Insert it into `WORKFLOW_STEPS` right before `publisher`.
- Unlike fact-check corrections, compliance failures often aren't
  auto-fixable by rewriting a claim — route failures to the same "needs
  human review" queue from Phase 1 rather than attempting automatic retry.

Independent of Phase 1, but shares the "human review queue" output location —
worth building that queue mechanism once and reusing it here.

---

## Phase 3 — Deeper adversarial verification — DONE (2026-07-25)

Builds on Phase 0's model tiering and Phase 0.2's prompt change.

- ~~Fact-checker currently only cross-checks named officials/titles and
  "significant claims" narrowly (regex-extracted). Expand scope to require
  independent corroboration for every major factual assertion, not just
  names/titles.~~ Done: `agents/fact_checker/agent.py` now also deterministically
  pre-runs Tavily searches for direct quotes (`_extract_quotes`) and
  statistics/casualty figures with their containing sentence
  (`_extract_statistic_sentences`), on top of the existing named-officials
  check — same "Python pre-run + LLM does the rest" pattern, not left to the
  LLM's own discretion which claims to check. Covered by
  `tests/test_fact_checker_extraction.py`.
- Note: this increases Tavily call volume per fact-check run (up to 8 official
  + 5 quote + 6 statistic pre-run searches, plus whatever the LLM agent does
  on top) — real cost/latency tradeoff for the added corroboration coverage,
  worth watching in practice.
- Research and fact-checking both currently rely on a single provider
  (Tavily) for text search. If corroboration quality becomes a real problem,
  consider adding a second independent search provider so "cross-referencing"
  means cross-*source*, not just multiple queries against one provider.
  (Don't build this speculatively — only if Phase 1/3 surface actual
  corroboration failures traceable to single-provider blind spots.)

---

## Phase 4 — Real memory / continuity — dossier version DONE (2026-07-25)

`story_history.py` and `breaking_news_log.py` are flat JSON logs matched by
keyword-set overlap — fine for dedup, not a knowledge store.

- ~~Consider "evolving dossier" files per ongoing story/key figure~~ Done:
  `tools/dossiers.py` maintains a markdown file per story thread
  (`./output/dossiers/{slug}.md`), matched/created by the same 2+
  keyword-overlap convention as `story_history.find_similar`. The EP looks up
  a match (read-only) during the dedup-check node and injects it into
  Researcher/Writer step input as a `STORY DOSSIER` block; a short
  lead-paragraph summary (cheap heuristic, no extra LLM call) is appended
  after a successful run. Entry count per dossier (30) and total dossier
  count (150, LRU-pruned) are both capped. Covered by `tests/test_dossiers.py`
  and `tests/test_ep_dossiers.py`.
- Embeddings/vector DB explicitly **not built** — per the original plan here,
  evaluate whether the dossier + keyword-overlap combination is actually
  insufficient in practice before reaching for that. If precision genuinely
  becomes a problem later, the incremental upgrade is embedding story
  summaries (not full articles) for similarity search — small addition, not
  a new subsystem.

---

## Phase 5 — Event-driven triggers

This is the one item that may not belong in *this* repo. Scheduling and the
breaking-news poll cadence currently live entirely in the external Jarvis
caller — `news-room-ai` has no trigger loop of its own, just HTTP endpoints
Jarvis calls.

**Decision needed:** should anomaly/event detection be built into
`jarvis-assistant-ai` (which already owns scheduling) or into a new
lightweight monitor inside `news-room-ai`? Leaning toward Jarvis, since
duplicating a scheduler here would fight the existing division of
responsibility.

Regardless of where it lives:
- Upgrade `breaking_news_checker`'s static qualifying-criteria rubric
  (`prompts.py`) toward a rolling-baseline comparison (e.g. story volume/
  velocity on a topic vs. a trailing average) rather than a fixed keyword/
  criteria list, so "breaking" is judged relatively, not against a hardcoded
  bar.
- True enterprise webhook/streaming ingestion (Reuters/Bloomberg/Dataminr) is
  a bigger lift requiring paid data access — treat as a stretch goal, not a
  near-term milestone.

**Concrete near-term build — `/webhook/ingest` endpoint.** Separate the two
concerns: *receiving* a push (belongs in `news-room-ai`, since `main.py`
already has the public HTTP surface and `breaking_news_checker` already has
the exact "fire a new production" pattern to reuse — see
`agent.py:247-260`) vs. *deciding* whether it's newsworthy (can stay
Jarvis-side or be delegated back to `breaking_news_checker`'s LLM classifier).

Add one shared `POST /webhook/ingest` route in `main.py` that accepts a
normalized `{source, headline, detail, url, keywords}` payload from any
upstream feed and routes it through the same qualifying-criteria /
dedup/cooldown gates `breaking_news_checker` already runs, then fires
`/produce/async` on a pass. Cheap/free sources worth wiring first, roughly in
order of setup effort:
1. **USGS earthquake GeoJSON feed** — polled every 1–2 min (no native
   webhook, but free, fast-changing, and simple to normalize).
2. **NWS/weather.gov CAP alerts** — similar polling model, free.
3. **Market-data websocket streams** (Polygon.io / Finnhub free tiers) for
   stock-spike-driven stories.
4. **RSS-via-WebSub or a no-code bridge** (Zapier/IFTTT/n8n watching RSS →
   POST to `/webhook/ingest`) for outlets that don't offer a real API —
   gets push-like behavior without building a poller per source.
5. **X/Twitter API v2 filtered stream** for keyword-matched social signals,
   if API access is available.

Each source gets a small adapter (normalize its payload shape → the shared
schema above); the ingest endpoint and downstream gating logic stay common.

---

## Phase 6 — Polish

- Render lower-third/chyron graphics automatically (FFmpeg `drawtext`/overlay)
  instead of just extracting cues (`video_editor/prompts.py` already flags
  this as a known future enhancement).
- Add real unit tests around the EP's LangGraph routing logic. `test_tools.py`
  today is a manual, live-API smoke test with no coverage of orchestration
  decisions — once Phase 1 adds branching/retry logic, that logic needs
  tests that don't require live API calls to catch regressions.

---

## Suggested sequencing

Phase 0 → Phase 1 → Phase 2, in that order, since they're low-risk, build on
existing infrastructure, and directly deliver "self-correcting." Phases 3–6
can be reordered based on which failure mode actually bites first in
practice (bad corroboration vs. missing continuity vs. missing graphics vs.
scheduling).
