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

## Phase 5 — Event-driven triggers — receiver + free adapters DONE (2026-07-26)

**Decision made:** built inside `news-room-ai` itself, not `jarvis-assistant-ai`.
The receiving endpoint already lived here (`/webhook/ingest`); rather than
duplicate a second external scheduler in Jarvis, the poller is a plain
`asyncio` background task started in `main.py`'s own lifespan
(`_event_feed_loop`), calling `breaking_news_checker.process_webhook_event()`
directly in-process — no self-HTTP round-trip, no new scheduler to
coordinate. This also sidesteps Jarvis's own scheduler having a live,
unresolved bug (its 30-min breaking-news task has been stuck in a permanent
`FAILED` state since 2026-07-18 because nothing resets its error counter).

`tools/event_feeds.py` implements three sources: the USGS
significant-earthquakes GeoJSON feed, active NWS/weather.gov CAP alerts
(neither needs an API key), and a configurable RSS/Atom poller
(`fetch_rss_feeds`, added 2026-07-26 after checking what real wire-service
webhooks actually cost — AP's Breaking News API is self-serve but pull-only;
Reuters/Bloomberg/Dataminr are genuinely push-capable but enterprise-priced
with no public signup, ballpark $10k+/month for Dataminr specifically). The
RSS poller is the practical substitute: point `EVENT_FEED_RSS_URLS` at
outlets' public "breaking news" category feeds (many syndicate AP content)
rather than a general firehose, since every item still runs through the same
strict LLM qualifying-criteria gate — a high-volume feed just means wasted
evaluation calls, not more real breaking news. A seen-event cache
(`./output/event_feed_seen.json`, 72h TTL) prevents re-submitting the same
still-active earthquake/alert/RSS item every poll. **Disabled by default**
(`EVENT_FEEDS_ENABLED=false`, and RSS additionally requires
`EVENT_FEED_RSS_URLS` to be non-empty) — this is a genuinely new autonomous
trigger path (a qualifying event can fire a real, credit-spending,
publish-to-YouTube production with no human involved) and defaulting it on
silently felt like the wrong call; the user enables it deliberately in `.env`.
Covered by `tests/test_event_feeds.py`, `tests/test_event_feeds_rss.py`, and
`tests/test_event_feed_loop.py`.

Not built: market-data websocket streams and the X/Twitter filtered stream —
both need a paid/authenticated source that isn't configured in this project.
They'd plug into the same `/webhook/ingest` → `process_webhook_event()` path
either way; only the adapter (normalize the source's payload into the shared
candidate shape) would need building.

Still true regardless of source:
- Upgrade `breaking_news_checker`'s static qualifying-criteria rubric
  (`prompts.py`) toward a rolling-baseline comparison (e.g. story volume/
  velocity on a topic vs. a trailing average) rather than a fixed keyword/
  criteria list, so "breaking" is judged relatively, not against a hardcoded
  bar.
- True enterprise webhook/streaming ingestion (Reuters/Bloomberg/Dataminr) is
  a bigger lift requiring paid data access — treat as a stretch goal, not a
  near-term milestone.

---

## Phase 6 — Polish — DONE (2026-07-26)

- ~~Render lower-third/chyron graphics automatically~~ Done:
  `tools/video_tools.py:render_graphic_overlays` burns each `[GRAPHIC: ...]`
  cue into the video as a Pillow-rendered lower-third (dark bar + accent
  stripe + bold text), composited with FFmpeg's `overlay` filter rather than
  `drawtext` (avoids depending on FFmpeg being built with freetype support).
  Timing is a proportional-position approximation, not real speech alignment
  — documented clearly in the code and README since HeyGen doesn't return
  word-level caption data to sync against. Verified against the real bundled
  FFmpeg binary with a synthetic test clip (not just mocked control flow) in
  `tests/test_video_editor_graphics_smoke.py`, including a visual check of
  a rendered frame before trusting it in the live pipeline.
- ~~Add real unit tests around the EP's LangGraph routing logic~~ Done as
  part of Phase 1/2/3/4/5's own work — see `tests/`.

---

## Phase 7 — Outcome-driven, human-approved tuning ("Tier 2" self-improvement) — 7.1/7.3 DONE (2026-07-28), 7.2/7.4 not started

Written 2026-07-27 after a discussion about what "self-improving" should and
shouldn't mean for this system. The short version: real agentic-AI incidents
labeled "self-improvement" in press coverage (models editing a shutdown
script rather than complying with it, finding a misconfigured sandbox during
a CTF eval) are actually **specification gaming** — a system optimizing
against a measured proxy finds it easier to game the measurement than to
improve the underlying thing, especially once it's capable enough to edit
its own evaluator. That risk is not hypothetical for this repo: tonight
alone produced two real instances of exactly this shape of problem —
`agents/executive_producer/prompts.py`'s `anchor_override` rule used a real
anchor name as its example and the LLM echoed it unprompted, and the
`avatar_iii` engine tier has an intermittent visual defect (a "Veo"
watermark) that nothing detects automatically. Both are outcome-blindness
problems: the system has no way to notice its own failures except a human
reading logs by hand.

**Explicitly out of scope: agents autonomously rewriting their own or each
other's prompts/code based on a self-computed signal, with no human review.**
This is the mechanism behind the shutdown-script-editing finding — an
unsupervised loop reliably finds the proxy's blind spots. Every sub-phase
below keeps a human in the loop for anything that changes prompts or config.
`compliance_checker` and `fact_checker` are never eligible to be tuned by
this loop at all, autonomous or not — if their thresholds seem miscalibrated
that's a separate, explicit human decision, never something "self-improved"
into looser.

### 7.1 — Outcome aggregation (foundation for everything else) — DONE (2026-07-28)

The data this needs mostly already exists but is scattered and unaggregated
— `story_history.json`, `breaking_news_log.json`, `needs_review.json`
(`tools/review_queue.py`), and per-run fact-check/compliance verdicts, which
today are only readable by opening individual
`./output/{show}/{run_id}/production_logs/*.md` files by hand.

- ~~Add a structured per-run outcome record~~ Done: `tools/agent_outcomes.py`
  (same flat-JSON-log convention as the other `tools/*_log.py` files) —
  `record()` appends one entry per production, `load_recent(days)` reads a
  rolling window. `agents/executive_producer/agent.py:process_message()`
  calls it right after the graph completes (workflow, show, desk, anchor,
  topic, keywords, fact-check verdict + attempt count, compliance verdict,
  published — detected by checking the publisher step's output text for
  "Published to YouTube successfully" — `succeeded`, and wall-clock
  duration). Wrapped in try/except so a broken outcome-log write can never
  take down the actual production response.
- ~~A read-only aggregation script~~ Done: `tools/outcome_report.py`,
  runnable standalone (`python tools/outcome_report.py [days]`, default 7)
  or via `generate_report(days)`. Reports overall succeed/publish/hold
  rates plus the same broken down by desk, anchor, and workflow. Not an
  agent, not scheduled, not autonomous — run it on request and read the
  output, same as this roadmap itself.
- Tests: `tests/test_outcome_tracking.py` — record/load round-trip, window
  filtering, aggregation math against synthetic data, and the EP wiring
  (including that a broken `agent_outcomes.record()` call doesn't break
  `process_message()`).
- **Real bug caught while building this:** existing tests in
  `tests/test_ep_self_correction.py` call the real `process_message()` with
  only `ep.workflow.ainvoke` mocked — once outcome recording was wired in,
  every test run started writing real entries into the actual project's
  `./output/agent_outcomes.json`. Fixed by no-op'ing `agent_outcomes.record`
  in that file's shared test helper. Worth remembering for Phase 7.2/7.4 —
  any new EP-level side effect needs the same check across existing tests,
  not just its own new ones.

### 7.2 — Positive-example grounding (extends the existing dossier pattern)

Cheap, low-risk, reuses infrastructure that already exists rather than
building something new.

- Tag productions in the new outcome log (7.1) that cleared fact-check on
  the first attempt, cleared compliance, and published successfully as
  "clean."
- Alongside the dossier/prior-coverage context the EP already injects into
  Researcher/Writer step input (Phase 4), optionally surface one clean past
  example for a similar desk/topic — same keyword-overlap matching
  convention already used everywhere else in this repo. No new retrieval
  infrastructure, no embeddings — consistent with Phase 4's existing stance
  that a vector DB isn't worth it until keyword-overlap demonstrably fails.

### 7.3 — Automated output QA (directly justified by tonight's watermark finding) — DONE (2026-07-28)

The most concretely motivated piece of this phase — this would have caught
the `avatar_iii` Veo watermark automatically tonight instead of needing a
manual frame-by-frame review.

- ~~A new check after `video_editor` builds `video_package.json`~~ Done:
  `tools/video_tools.py:check_visual_qa()` samples frames at evenly-spaced
  timestamps (default 3, avoiding the very start/end) via FFmpeg, base64-
  encodes them, and sends them to a vision-capable LLM call (reuses
  `settings.model_for("video_editor")` — no new per-role model config, no
  new ML/CV pipeline) asking specifically whether any frame shows a
  watermark, logo, chromakey fringe, or mismatched background — deliberately
  not scoped wider than the defect classes already known to occur. Wired
  into `agents/video_editor/agent.py` as a new step after promo/outro
  assembly, against the actual final video that will be published.
  Never raises — any failure (no FFmpeg, no duration, extraction failure,
  vision-call error) returns `flagged=False` with an explanatory note,
  treated as "couldn't check," not "confirmed clean." An unparseable vision
  response (missing the expected `FLAGGED: yes/no` prefix) errs toward
  flagging rather than silently assuming clean.
- On a flag: **never auto-blocks publish** — logs to `tools/review_queue.py`
  with `stage="visual_qa"`, same pattern as an existing fact-check/
  compliance hold, surfaced for a human to decide. The QA result is also
  saved into `video_package.json` either way, flagged or not.
- Tests: `tests/test_video_qa.py` — frame-count/extraction behavior, both
  flagged and clean vision responses, the unparseable-response fallback,
  vision-call failure handling, and the `video_editor` wiring (flagged →
  one review_queue entry with the right stage; clean → none).

### 7.4 — Human-approved prompt tuning loop

The actual "improvement" step — closes the loop from 7.1's data back into
real prompt changes, deliberately kept out of the autonomous-loop shape.

- Triggered on request ("review the last week's outcome data and suggest
  prompt tuning"), not on a background schedule. Reads 7.1's aggregated
  report, identifies the highest-signal issue (e.g. "researcher's
  fact-check hold rate on health_science stories is 3x the average"), reads
  the relevant prompt file, and proposes one specific, scoped diff with
  reasoning — for review, the same way the HeyGen V3 migration plan was
  written up and approved before any code changed.
- `compliance_checker`/`fact_checker` prompts are hard-excluded from this
  loop, per the scope note above.
- Every change that comes out of this loop, once approved, should be a
  clearly-tagged commit (e.g. a `[tier2-tuning]` prefix) so the audit trail
  distinguishes deliberate, reviewed tuning from any other change — a
  regression should always be traceable to a specific, human-approved diff.

**Build order actually followed:** 7.1 and 7.3 built together 2026-07-28, in
that order. 7.2 is next in line — cheap, but only worth doing once 7.1 has
accumulated enough real "clean" runs to have something worth surfacing as a
positive example. 7.4 should come last, once 7.1 has enough real data to
make its suggestions worth reviewing rather than guessing from a small
sample — not started, not scheduled, no code exists for it beyond the
process description above.

---

## Status

All phases (0–6) are implemented, tested, and merged as of 2026-07-26.
Phase 7.1 and 7.3 are implemented, tested, and merged as of 2026-07-28 —
outcome logging/reporting is live and the visual QA check is wired into
every `BROADCAST_VIDEO`/`VIDEO_FROM_SCRIPT`/`SPECIAL_REPORT` run. **7.2 and
7.4 are not started** — 7.2 needs 7.1 to accumulate real "clean" runs
before it's worth building, and 7.4 (the actual prompt-tuning loop) is
deliberately the last piece, per the human-approval design in Phase 7's
intro. What's deliberately still open otherwise, not because it was missed
but because it needs something this repo alone can't provide:

- **Event feeds are disabled by default** (`EVENT_FEEDS_ENABLED=false`) —
  enable deliberately in `.env` when ready to let earthquakes/weather
  alerts/RSS items autonomously trigger productions. RSS specifically also
  needs `EVENT_FEED_RSS_URLS` populated — empty by default.
- **Paid/authenticated event sources** (market data, X filtered stream) —
  adapters aren't built; would need an account/API key first. The
  wire-service webhook question (AP/Reuters/Dataminr) was investigated
  2026-07-26 — all either pull-only or enterprise-priced with no self-serve
  push access, which is why the RSS poller exists as the practical
  alternative instead.
- **Graphic overlay timing is an approximation**, not real speech alignment
  — revisit only if it visibly looks wrong in practice; a real fix would
  need caption/forced-alignment data this pipeline doesn't currently have.
- **Second search provider for fact-checking** — not added; only worth it if
  single-provider (Tavily) corroboration proves insufficient in practice.
- **Embeddings/vector DB for memory** — not added; dossiers (Phase 4) were
  the cheaper option tried first, per the original plan.

If new gaps surface through actual use, add them here rather than
re-deriving this whole document from scratch.
