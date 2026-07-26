# Newsroom AI

An AI-powered broadcast newsroom that researches topics, adversarially fact-checks and self-corrects articles, screens scripts for YouTube policy compliance before publish, writes news content, produces broadcast anchor scripts, generates AI anchor videos via HeyGen, and publishes to YouTube — all orchestrated by an Executive Producer agent.

Designed to run as a standalone backend service called by [Jarvis](https://github.com/lenger06/jarvis-assistant-ai) or any other client via a simple HTTP API. It can also be pushed into via `POST /webhook/ingest` from external event feeds (earthquake/weather/market-data pollers, an RSS bridge, etc.) instead of only being pulled on a schedule.

See [`SELF_IMPROVEMENT_ROADMAP.md`](SELF_IMPROVEMENT_ROADMAP.md) for the in-progress plan toward a more autonomous, self-improving newsroom — what's landed, what's next, and why.

---

## Example Prompts

These can be sent directly to `POST /produce` or spoken to Jarvis naturally.

### Research Only

```
Research the latest developments in the US-China trade war
Find information about the recent OPEC production cuts
What do we know about the SpaceX Starship test flight?
Research key figures and background on the Iran nuclear negotiations
```

### Write an Article

```
Write a news article about the Fed rate decision today
Cover the story of the NATO summit in Brussels
Write a story about the Supreme Court ruling on immigration
Write a news article on the latest White House press briefing
```

### Full Production (Article + Script, no video)

```
Produce a full news segment on the Strait of Hormuz shipping situation
Full production on the Israel-Hamas ceasefire negotiations
Produce a broadcast segment covering the G7 summit outcomes
News segment on the latest Congressional budget vote
```

### Broadcast Video (Full pipeline → YouTube)

```
Generate a news video about the Fed rate decision
Produce a broadcast video on the Iran conflict and publish it to YouTube
Create a news video covering the Supreme Court's latest ruling — have Shawn Green read it
Generate a video on the White House press conference — have Daniel Mercer anchor it
Produce a broadcast video on the rescue of the downed pilots — have Darlene Smith read it
Publish a news video on the latest developments in Ukraine
```

### Special Report (Long-form, in-depth)

```
[SHOW: special-report] Do an in-depth special report on the New Glenn rocket development and the Blue Origin explosion. Make it approximately 15 minutes.
Special report on the history and future of US nuclear policy — serious tone, 10 minutes
Do a deep dive on the opioid crisis — causes, current state, and what comes next
```

Special reports default to 10 minutes if no duration is specified. The pipeline runs the same steps as a Broadcast Video but with extended research (8–10 search angles), a long-form analytical article format, and a script written to fill the full target duration.

### Script Only (when you already have article content)

```
Write a script only — here is the article: [paste article text]
Turn this into a broadcast script: [paste content]
Script only for this story: [paste text]
```

### Video From Script (when you already have a script)

```
Generate a video from this script — have Shawn Green read it: [paste script]
Record this script with Daniel Mercer: [paste script]
Video from script, use Alexa Chen: [paste script]
```

### Requesting a Specific Anchor

```
Produce a broadcast video on the Iran war — have Shawn Green read it
Generate a news video with Darlene Smith anchoring
Alexa Chen should read the entertainment roundup
Have Daniel Mercer anchor the White House briefing video
```

> If no anchor is specified, the Executive Producer selects the anchor assigned to the active show and desk.

---

## Shows & Scheduling

The Executive Producer auto-detects the active broadcast based on time of day and day of week:

| Show | Slug | Trigger | Tone |
|------|------|---------|------|
| Morning Report | `morning-report` | Weekdays before 1 pm | Conversational and upbeat |
| Evening News | `evening-news` | Weekdays 1 pm–midnight | Serious and authoritative |
| Weekend Roundup | `weekend-roundup` | Saturdays and Sundays | Measured and reflective |
| Entertainment Weekly | `entertainment-weekly` | Scheduled explicitly | Upbeat and conversational |
| Special Report | `special-report` | "special report", "deep dive", "in-depth", "long-form" | Measured, thorough, and authoritative |
| Breaking News | `breaking-news` | Breaking News Checker agent | Urgent and direct |

Each show defines which anchor covers which desk and can specify a look preference (formal, casual, sitting, etc.). To override the auto-detected show, prefix your request with `[SHOW: slug]`:

```
[SHOW: special-report] In-depth report on the future of nuclear energy
[SHOW: breaking-news] Alert: major earthquake reported in Tokyo
```

Show definitions live in `config/shows.py`.

---

## Anchor Roster & Look Rotation

Anchors are defined in `config/anchors.py`. Each anchor has an on-air name, one or more `AvatarLook` entries (avatar IDs from HeyGen), a voice ID, and a bio.

The Executive Producer selects the anchor assigned to the active show and desk, then rotates through that anchor's looks round-robin on each production. A `look_preference` (e.g. "formal", "casual") filters the rotation pool to matching looks. Shows can also configure a stand-in anchor that rotates in every N productions (`alt_anchor_name` / `alt_every`).

**Current roster:**

| On-air name | HeyGen actor | Desks | Engine |
|---|---|---|---|
| Shawn Green | Shawn (3 looks) | Politics, National, Foreign, Special Reports | v2 |
| Daniel Mercer | Daniel Mercer | National, Politics, Foreign (Morning Report lead) | v2 |
| Nicholas Stavros | Kurt | National (Evening News lead) | v2 |
| Dominic Fairchild | Man in the Sport Coat | Politics, National | v2 |
| Alexa Chen | Alexa | Entertainment | v2 |
| Monica Hayes | Saskia (3 looks) | Entertainment | v2 |
| Valerie Brooks | Candace (2 looks) | Entertainment | v2 |
| Zayne Carter | Zayne (2 looks) | Entertainment | v2 |
| Karoline Faye | Brooklyn (2 looks) | Entertainment | v2 |
| Victor Marinos | Ricardo (3 looks) | Politics | v2 |
| Brandon Jones | Brandon in Grey Suit | Business | v2 |
| Alister Blackwood | Dexter Suit Front | Investigative | v2 |
| Darlene Smith | Crystal Veil | Health & Science | v2 |
| **Marco Reyes** | PAOS (3 looks) | National | **Avatar V only** |
| **Elise Navarro** | PAOS | National | **Avatar V only** |
| **Elena Vasquez** | PAOS | National | **Avatar V only** |

### Avatar V Anchors (`avatar_v_only`)

Marco Reyes, Elise Navarro, and Elena Vasquez are **PAOS** (Public Avatar On-Screen) avatars that require HeyGen's **Avatar V** engine and the **v3 API**. They are flagged `avatar_v_only=True` in `config/anchors.py`, which means:

- They are **excluded from all automatic desk and random anchor lookups** — they cannot be accidentally assigned to a standard `pip_v2` production
- They are only used when **explicitly named** in a request (e.g. `have Elise Navarro anchor this`) or when a show is configured with `video_style = "fullscreen_v3"`
- Their look IDs are **incompatible with the v2 API** — only use them with `[SHOW: ...]` + `fullscreen_v3` style or direct v3 API calls

To produce a full-screen Avatar V broadcast, set `video_style = "fullscreen_v3"` on the show in `config/shows.py` or pass `[VIDEO-STYLE:fullscreen_v3]` in the request.

To add an anchor, add an entry to the `ANCHORS` list in `config/anchors.py`. Each look is an `AvatarLook(avatar_id, description)` — HeyGen names are noted in comments next to each ID:

```python
Anchor(
    name="Jordan Lee",
    avatars=[
        AvatarLook("avatar_id_here", "formal suit, news desk — hard news"),  # HeyGen: "Avatar Name"
    ],
    voice_id="voice_id_here",
    desk="national",
    bio="Warm and conversational. Strong on human interest stories.",
)
```

Get IDs by calling with your HeyGen API key:
- `GET https://api.heygen.com/v2/avatars`
- `GET https://api.heygen.com/v2/voices`

---

## Agent Roles

### Executive Producer
The orchestrator. Receives every production request, determines the appropriate workflow, auto-detects the active show, selects and rotates anchors per show schedule, and delegates to the team in sequence. Aborts the pipeline early if the Researcher returns no usable content (Tavily unavailable or rate-limited) rather than producing and publishing an empty broadcast. Saves a full production log to `./output/{show_slug}/{run_id}/production_logs/` at the end of every run.

### Breaking News Checker
A background monitor (runs via Jarvis scheduler) that checks for significant breaking news every 30 minutes. Uses an LLM to evaluate whether current events meet broadcast-worthy criteria — major political events, natural disasters, crashes, explosions, corporate collapses, and more. When a qualifying story is detected, it triggers an immediate Breaking News production.

Deduplication and rate-limiting:
- **72-hour dedup window** — all breaking news covered in the last 72 hours is passed to the LLM as context; multi-day developing stories (earthquakes, ongoing conflicts, political crises) stay visible long enough to prevent re-triggering on subsequent days
- **60-minute cooldown** — minimum gap between any two productions regardless of topic
- **Ongoing conflict rule** — if a story shares 2+ keywords with a recent log entry, the LLM requires a dramatic, unambiguous escalation (war declared, head of state killed, ceasefire signed) before qualifying a new production; routine updates and slight headline variations are suppressed
- **Same-story suppression (code-level)** — tiered hard gate based on total coverage count across both breaking news and regular EP productions: 3-hour gap after 1+ fires; 6-hour gap after 2+ fires; 12-hour gap after 4+ fires
- **Cross-log awareness** — also checks the EP story history (see below) so a story covered in the Morning Report won't re-trigger as breaking news an hour later

Criteria are defined in `agents/breaking_news_checker/prompts.py`. The coverage log is persisted to `./output/breaking_news_log.json`.

**Event webhooks:** `POST /webhook/ingest` gives external push sources a second way in, alongside the 30-minute poll. Send a normalized event:

```json
{
  "source": "usgs_earthquake",
  "headline": "M7.2 earthquake strikes Region X",
  "detail": "Depth 12km, optional extra context",
  "url": "https://earthquake.usgs.gov/example",
  "keywords": ["earthquake", "region-x"]
}
```

It's evaluated by the same LLM qualifying-criteria judgment and same-story dedup/cooldown gates as the headline-scan path (`breaking_news_checker.process_webhook_event()`), then fires a production the same way if it clears the bar.

**Built-in event feed pollers** (`tools/event_feeds.py`) cover three sources: the USGS significant-earthquakes feed and active NWS/weather.gov CAP alerts (both need no API key or account at all), plus a configurable RSS/Atom poller. The RSS poller exists because the real wire services (AP, Reuters, Bloomberg, Dataminr) are either pull-only or enterprise-priced with no self-serve push access — pointing this at outlets' public "breaking news" category feeds (many of which syndicate AP wire content) is the practical alternative. Each candidate, from any of the three sources, is deduplicated against a seen-event cache (`./output/event_feed_seen.json`, 72-hour TTL) so a still-active earthquake, alert, or RSS item isn't re-submitted every poll. **Disabled by default** — a qualifying event firing a real, credit-spending, publish-to-YouTube production with no human in the loop is a genuinely new capability, not something to turn on silently. Enable it in `.env`:

```env
EVENT_FEEDS_ENABLED=true
EVENT_FEED_POLL_SECONDS=300
EVENT_FEED_MIN_MAGNITUDE=6.0
EVENT_FEED_NWS_SEVERITIES=Extreme,Severe
EVENT_FEED_USER_AGENT="news-room-ai (contact: you@example.com)"   # required by weather.gov's usage policy
EVENT_FEED_RSS_URLS=""    # comma-separated feed URLs — empty = RSS polling stays off even if enabled above
EVENT_FEED_RSS_MAX_ITEMS_PER_FEED=10
```

When enabled, a background `asyncio` task inside this process (started in `main.py`'s lifespan, not a separate scheduler) polls every configured feed and calls `process_webhook_event()` directly in-process for each new candidate — every RSS item still has to clear the same strict qualifying-criteria LLM gate as an earthquake or weather alert, so pointing `EVENT_FEED_RSS_URLS` at a high-volume general-news feed just means a lot of wasted evaluation calls, not a lot of extra breaking news. Market-data streams and X/Twitter's filtered stream still need a paid/authenticated source that isn't configured here — see `SELF_IMPROVEMENT_ROADMAP.md` Phase 5 if you want to add one; they'd plug into the same gating either way.

### Story Deduplication (EP Dedup Gate)

Before committing to any production pipeline, the Executive Producer runs a **dedup check** against the last 7 days of story history. This prevents the same event from being covered repeatedly when nothing material has changed — which wastes HeyGen credits and fills the channel with duplicate content.

**How it works:**

1. The EP's LLM analysis extracts 4–6 discriminating keywords from the requested topic (proper nouns, place names, key subjects — not generic words like "news" or "update").
2. The story history is queried for any production in the last 7 days sharing 2+ of those keywords.
3. If matches are found, a second LLM call evaluates whether the new request represents a genuine new development or a duplicate:
   - **PROCEED** — new story, not covered before → production continues normally
   - **PROCEED_AS_UPDATE** — same event but with a significant new development (status change, dramatic escalation, major new revelation) → production continues with an `[UPDATE NOTE]` injected so the Researcher and Writer focus on what's new
   - **SKIP** — same story, no meaningful change → production is suppressed; no HeyGen credits consumed; clear explanation returned to the caller

**What counts as a significant new development (PROCEED_AS_UPDATE):**
- Status change: search operation → survivor found; accused → convicted; missing → confirmed dead
- Dramatic escalation: ceasefire collapses; protest becomes riot; fire spreads to a new area
- Major new revelation: criminal charges filed, government policy reversal, whistleblower evidence

**What does NOT qualify (SKIP):**
- Death toll rises from the same incident
- Officials restate the same position
- "Rescue efforts continue" / "investigation ongoing" with no new findings
- Same story reframed with a slightly different headline angle

**Bypassing dedup:**

To override suppression — e.g. when you know there is a genuine update or want to force a new angle — add one of these tags anywhere in the request:

```
[FORCE]      — bypass dedup entirely, produce unconditionally
[UPDATE]     — signal this is a known follow-up; proceeds as PROCEED_AS_UPDATE
[NEW-ANGLE]  — signal this is a distinct angle, not a repeat
```

Examples:
```
[UPDATE] Produce a broadcast video on the Venezuela earthquake — rescue operation has found survivors
[FORCE] Generate a video on the France flooding situation
[NEW-ANGLE] Cover the economic impact of the Venezuela earthquake — different story from the rescue coverage
```

The story history is persisted to `./output/story_history.json`. The dedup gate is bypassed automatically for `RESEARCH_ONLY`, `SCRIPT_ONLY`, and `VIDEO_FROM_SCRIPT` workflows (these don't produce new researched broadcast content).

### Story Dossiers

Separate from the dedup log — `story_history.json` only tracks *that* a topic was covered, not *what's actually known about it*. `tools/dossiers.py` maintains an evolving per-thread markdown file for ongoing stories ("Iran/Hormuz shipping", "a developing earthquake response"), matched or created by the same 2+ keyword-overlap convention used elsewhere:

1. During the dedup check, the EP looks for an existing dossier sharing 2+ keywords with the current story (read-only — no new dossier is created for a story that turns out to be `SKIP`ped as a duplicate).
2. If found, its content is injected into the Researcher's and Writer's step input as a `STORY DOSSIER` block — accumulated context to understand what's already established and focus on what's new, not a fact source for the current story.
3. After a production completes successfully, a short lead-paragraph summary (no extra LLM call — a cheap heuristic snippet of the article) is appended as a new dated section, and the dossier is matched-or-created at this point if it didn't already exist.

Dossiers live in `./output/dossiers/{slug}.md`, indexed in `./output/dossiers/_index.json`. Both entry count per dossier (30) and total dossier count (150, pruning the least-recently-updated) are capped, matching the bounded-log convention used by `story_history.json` and `breaking_news_log.json`.

This is a deliberately cheaper alternative to a vector database — see `SELF_IMPROVEMENT_ROADMAP.md` Phase 4 for why.

### Researcher
Gathers source material using real-time web search (Tavily). Searches for multiple angles — latest developments, background context, key figures, and statistics. Compiles a sourced research brief with URLs. Also sources b-roll media: still images via Tavily and short video clips via the Pixabay API (if configured). Outputs a `## SOURCED B-ROLL IMAGES` and `## SOURCED B-ROLL VIDEOS` section for the script writer to choose from.

In Special Report mode: runs 8–10 searches across seven angles (latest developments, historical timeline, key figures, expert analysis, opposing viewpoints, economic/social impact, and international context) to build a research brief comprehensive enough to support a 10+ minute broadcast.

### Writer
Receives the research brief and writes a polished news article. Standard productions target 400–600 words in broadcast style (inverted pyramid, active voice, short sentences). When a target duration is specified, the word count scales proportionally (~150 words per minute). Includes a branded dateline. Saves to `./output/{show_slug}/{run_id}/articles/`.

In Special Report mode: writes a long-form analytical piece structured as Executive Summary → Background & Context → Key Developments → Multiple Perspectives & Expert Analysis → Implications & What's Next → Conclusion. May add explanatory context and analytical commentary beyond the raw research facts to fill the target word count.

### Fact Checker
Reads the draft article and verifies key factual claims using web search — with an **adversarial bias**: its job is to try to prove the article wrong, not confirm it's right. It actively searches for disconfirming/debunking coverage in addition to corroborating sources, rather than stopping at the first source that agrees. Three categories get a **deterministic Tavily pre-run** before the LLM even starts (guaranteeing a baseline of independent corroboration rather than leaving it to the LLM's own discretion which claims to check):
- Named officials/titles — "former" applied to a sitting official is a broadcast-level error
- Direct quotes — a misattributed or fabricated quote is a broadcast-level error independent of the surrounding facts
- Statistics and casualty figures — extracted with their containing sentence for a meaningful search query, not just the bare number

Produces a Fact Check Report with three sections — **Verified**, **Unverified**, and **Corrections Needed** — and issues one of three verdicts:
- `CLEAR TO PUBLISH` — all significant claims verified
- `PUBLISH WITH NOTES` — minor unverified items, no outright errors
- `HOLD FOR CORRECTIONS` — factual errors found, must be fixed before publishing

### Editor
Receives the draft article and the Fact Check Report. Applies every correction listed under Corrections Needed — uses web search to confirm accurate information before making each change. Particular focus on current vs. former titles for political figures and officials. Outputs the complete corrected article plus an editorial note listing every change made. The Script Writer uses this corrected article, not the original draft.

**Self-correction loop:** the Executive Producer reads the Fact Checker's verdict. If it comes back `HOLD FOR CORRECTIONS` (or errors/is unparseable), the EP doesn't just trust the Editor's one-shot patch — it re-runs the Fact Checker against the corrected article to verify the correction actually worked, for up to 2 total fact-check passes. If it's still failing after that, the pipeline **halts before script/video/publish** and the run is logged to the human review queue (see below) instead of publishing an uncorrected story.

### Script Writer
Converts the editor-reviewed article into a spoken broadcast anchor script. Formats it for on-air delivery: natural spoken English, breath-pause markers, and `[GRAPHIC: ...]` cues for supporting visuals. Places `[BROLL: url | description]` markers for still images and `[BROLL: url | description | video]` markers for video clips — B-roll markers must appear at the very start of each new story segment so the visual switches the instant the topic changes. Uses the selected anchor's name in the sign-off. Target read time scales with the requested duration. Saves to `./output/{show_slug}/{run_id}/scripts/`.

### Anchor
Takes the broadcast script, applies TTS text normalisation (see below), strips formatting markers with a pure-regex cleaner (no LLM pass — prevents refusal text from being read aloud), and submits it to HeyGen using the selected anchor's avatar and voice IDs. For scenes with `[BROLL:]` markers, b-roll media (still images **or** video clips) is composited as a Picture-in-Picture in the upper-left corner of the studio background video using FFmpeg, uploaded as a new HeyGen video asset, and used as the scene background. The PIP preserves the original aspect ratio of the source media. Video clip b-roll loops seamlessly for the duration of the scene. Falls back to a Pillow static image composite if FFmpeg is unavailable (images only). Polls for completion natively in Python (every 30 seconds, up to 10 minutes) — does not rely on the LLM to manage polling. Returns the video URL and thumbnail URL when complete.

### Video Editor
Downloads the completed anchor video from HeyGen, burns every `[GRAPHIC: ...]` cue into the video as an on-screen lower-third (dark bar + accent stripe + bold white text, rendered with Pillow and composited with FFmpeg's `overlay` filter — `tools/video_tools.py:render_graphic_overlays`), then assembles a `video_package.json` in `./output/{show_slug}/{run_id}/media/` containing the video file path, thumbnail URL, graphic cues, and suggested YouTube metadata.

Timing is a deliberate approximation, not exact speech alignment: each cue's on-screen moment is estimated from its proportional position in the script text (character offset ÷ script length), mapped onto the rendered video's actual duration, and shown for ~4.5 seconds. HeyGen doesn't return word-level caption timing, so this is the practical alternative to a real forced-alignment pass. If FFmpeg can't determine the video's duration or isn't available, graphic rendering is skipped (not a hard failure) and the rest of the pipeline continues.

### Compliance Checker
The last gate before publish, for `BROADCAST_VIDEO`, `VIDEO_FROM_SCRIPT`, and `SPECIAL_REPORT` workflows only. Screens the final broadcast script for content that could violate YouTube's Community Guidelines — graphic violence, hate speech, harassment of private individuals, promotion of dangerous acts, sexual content, and policy-sensitive misinformation (elections, medical claims) — without re-litigating factual accuracy, which is the Fact Checker's job. Issues one of two verdicts: `CLEAR TO PUBLISH` or `HOLD FOR REVIEW`. Unlike the fact-check loop, there's **no retry** here — a policy concern usually isn't something an automated rewrite can reliably fix, so a `HOLD` halts the pipeline immediately and logs to the human review queue.

### Producer
Confirms all output files are saved and compiles a final production summary — article path, script path, video path, topic, and word counts.

### Publisher
Reads `video_package.json` and uploads the finished MP4 to YouTube. The title is the story subject only — newsroom name and show-type prefixes ("Defy Logic News | Morning Report | …", "Breaking News: …", etc.) are stripped, leaving just the headline. Sets the HeyGen thumbnail. Adds the video to the appropriate YouTube playlists (see Playlists below). Uploads exactly once in native Python. Returns the final YouTube URL.

---

## Human Review Queue

When either the fact-check retry loop or the Compliance Checker halts a production, it's logged to `./output/needs_review.json` (`tools/review_queue.py`) instead of silently failing or publishing anyway — a flat, append-only log in the same style as `story_history.json` and `breaking_news_log.json`, capped at 200 entries. Each entry records the topic, the reason, which stage halted it (`fact_check` or `compliance`), the run's output directory (so you can read the full article/script/fact-check report), and the workflow.

A halted run is also reported back as `"success": false` from the Executive Producer, and `/produce/async` marks the job `status: "error"` accordingly — so a caller (Jarvis) polling `/job/{job_id}` can tell a halt apart from a normal completion, rather than seeing a silent "complete" with no video.

There's no UI for this yet — check the file directly, or call `tools.review_queue.list_pending()`.

---

## YouTube Playlists

Each uploaded video is automatically added to the relevant YouTube playlists. Assignment is multi-layered:

| Layer | Source | Example |
|-------|--------|---------|
| **Show playlist** | Active show slug | Breaking News → Breaking News playlist |
| **Desk playlist** | Editorial desk | Foreign desk → World News playlist |
| **Anchor playlist** | On-air anchor | Shawn Green → Shawn Green — World Report |
| **Series playlists** | Topic keywords | Defined in `config/playlists.py` |

Show playlists are configured in `SHOW_PLAYLISTS` in `config/playlists.py`. The Breaking News playlist ID is pre-configured. Morning Report, Evening News, Special Reports, and Weekend Roundup playlist IDs can be filled in after creating them in YouTube Studio (Content → Playlists → copy the `PLxxxxxx` ID from the URL).

The EP can also assign extra playlists explicitly via `extra_playlists` in its analysis (e.g. adding a story to a series playlist). All playlist IDs are deduplicated before upload.

---

## TTS Text Normalisation

All anchor script text is normalised before submission to HeyGen via `_normalize_tts()` in `tools/heygen_tool.py`. Prevents common anchor mispronunciations:

- `U.S.` → `U S`, `U.S.A.` → `U S A`, `D.C.` → `D C`, `F.B.I.` → `F B I`, `C.I.A.` → `C I A`
- `vs.` → `versus`, `etc.` → `etcetera`
- `Dr.` → `Doctor`, `Mr.` → `Mister`, `Mrs.` → `Missus`, `Sen.` → `Senator`, `Gov.` → `Governor`, `Gen.` → `General`, `Rep.` → `Representative`
- Em-dash `—` → `, ` (prevents "dash" being read aloud)

To add a new rule, append a tuple to `_TTS_REPLACEMENTS` at the top of `tools/heygen_tool.py`:
```python
(_re.compile(r'\bNATO\b'), "NAY-toh"),
```

---

## Workflows

| Workflow | Trigger phrases | Steps |
|----------|----------------|-------|
| `RESEARCH_ONLY` | "research", "find information about", "what do we know about" | Researcher |
| `ARTICLE` | "write an article", "write a story", "cover this story" | Researcher → Writer → Fact Checker → Editor → Producer |
| `FULL_PRODUCTION` | "full production", "produce a segment", "news segment", "broadcast" | Researcher → Writer → Fact Checker → Editor → Script Writer → Producer *(no video)* |
| `BROADCAST_VIDEO` | "video", "youtube", "record", "generate video", "broadcast video", "publish" | Researcher → Writer → Fact Checker → Editor → Script Writer → Anchor → Video Editor → Compliance Checker → Producer → Publisher |
| `SPECIAL_REPORT` | "special report", "deep dive", "in-depth", "long-form", "comprehensive coverage" | Same steps as BROADCAST_VIDEO with extended research, long-form writing, and full-duration scripting |
| `SCRIPT_ONLY` | "script only", "write a script", "turn this into a script" (with content) | Script Writer → Producer |
| `VIDEO_FROM_SCRIPT` | "video from script", "record this script", "generate video from script" | Anchor → Video Editor → Compliance Checker → Producer → Publisher |

The Fact Checker's `HOLD FOR CORRECTIONS` verdict can re-run Fact Checker → Editor as a bounded self-correction pass (see [Human Review Queue](#human-review-queue)), and the Compliance Checker's `HOLD FOR REVIEW` halts before Producer/Publisher — so the step list above is what runs on the clean path, not a strict guarantee of what runs on every request.

> **Note:** `FULL_PRODUCTION` produces a script but **no video**. Use `BROADCAST_VIDEO` (or say "broadcast video", "generate a video", "publish") to get a HeyGen render and YouTube upload.

You can also override the workflow explicitly with a tag anywhere in the request — the EP treats this as authoritative and will not infer a different workflow from the request text:

```
[WORKFLOW: BROADCAST_VIDEO] Produce a segment on the latest White House briefing
[WORKFLOW: RESEARCH_ONLY] SpaceX Starship latest developments
```

The pipeline aborts early and logs an error (without publishing) if the Researcher returns no usable content — e.g. when Tavily is rate-limited or unavailable.

Each step receives the full output of all prior steps as context.

---

## Architecture

```
Jarvis (or any HTTP client)              External event feeds
 └─► POST /produce/async                  └─► POST /webhook/ingest
       │                                        └─► Breaking News Checker
       │                                              (same gates as the 30-min poll)
       └─► Executive Producer (orchestrator)
             ├─► Breaking News Checker  — background monitor (Jarvis scheduler)
             ├─► Researcher      — web_research_tool, file_operations_tool
             ├─► Writer          — file_operations_tool
             ├─► Fact Checker    — web_research_tool (adversarial; can re-run after Editor)
             ├─► Editor          — web_research_tool, file_operations_tool
             ├─► Script Writer   — file_operations_tool
             ├─► Anchor          — HeyGen API (generate + native async poll)
             ├─► Video Editor    — video_tools (download, extract cues, package)
             ├─► Compliance Checker — policy screen; halts to review queue on HOLD
             ├─► Producer        — file_operations_tool
             └─► Publisher       — YouTube API (upload once + thumbnail)
```

Output files are saved per-run under a timestamped directory:
```
output/
  {show_slug}/
    {run_id}/
      articles/         — finished news articles (.md)
      scripts/          — broadcast anchor scripts (.md)
      media/            — anchor videos (.mp4) and video_package.json
      production_logs/  — full production logs with all agent outputs (.md)
  breaking_news_log.json  — breaking news coverage log (72-hour dedup window)
  story_history.json      — universal story log for EP dedup (7-day window)
  needs_review.json       — productions halted for human review (fact-check/compliance)
  last_broadcast.json     — timestamp of the most recent completed production
```

---

## Setup

### Requirements

- Python 3.10+
- OpenAI API key
- Tavily API key
- HeyGen API key *(for Anchor agent — video generation)*
- Google Cloud project with YouTube Data API v3 enabled *(for Publisher agent)*

### Installation

```bash
cd news-room-ai
pip install -r requirements.txt
```

### HeyGen Setup

1. Sign up at [heygen.com](https://heygen.com) and get your API key from **Settings → API**
2. Add anchors to `config/anchors.py` with avatar and voice IDs:
   - Call `GET https://api.heygen.com/v2/avatars` with your API key to list available avatars
   - Call `GET https://api.heygen.com/v2/voices` to list voices

#### V3 API & Avatar V

The newsroom uses two HeyGen API versions in parallel:

| | V2 (`/v2/video/generate`) | V3 (`/v3/videos`) |
|---|---|---|
| Used by | `pip_v2` shows (standard) | `fullscreen_v3` shows |
| Background | `type: "video"` looping asset | `type: "color"` or `type: "image"` (no video) |
| Caption | `"caption": true` (boolean) | Not supported — omit |
| Engine | Default | `{"type": "avatar_v"}` for PAOS avatars |
| `voice_settings.emotion` | Supported | Not supported — omit |
| `remove_background` | Supported | Not supported for PAOS — causes render failure |

Key V3 rules learned from production:
- **Background must be `"color"` or `"image"`** — `"video"` returns HTTP 400
- **Background image must be uploaded** via `POST https://upload.heygen.com/v1/asset` — use the returned `asset_id`
- **No `emotion` in voice_settings** — omit the field entirely; including it causes render failure
- **No `remove_background`** — PAOS avatars don't support matting; field causes render failure
- **No `caption`** — not supported in v3; omit entirely

A Postman collection covering all v3 endpoints, known-good payloads for each PAOS anchor, and documented failure cases is included at `HeyGen_V3.postman_collection.json`. Import it and set the `HEYGEN_API_KEY` collection variable to test.

### YouTube Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com) → **APIs & Services → Library**
2. Enable **YouTube Data API v3**
3. Go to **Credentials → Create Credentials → OAuth 2.0 Client ID** (Desktop app)
4. Download the JSON file and save it to `credentials/youtube_client_secrets.json`
5. On first run the Publisher agent will open a browser to authorize — token saved to `credentials/youtube_token.pickle`

See [`credentials/README.md`](credentials/README.md) for full step-by-step setup instructions including OAuth consent screen configuration.

### B-Roll Compositing

The Anchor agent composites b-roll media (still images or video clips) as a Picture-in-Picture overlay on a studio background video using FFmpeg, then uploads the result to HeyGen as a video asset.

- Place background videos in `./assets/` named after their HeyGen video asset ID (e.g. `./assets/f6fa4085043140deaba8258a96233036.mp4`)
- Multiple backgrounds are supported — each desk automatically uses its configured `background_asset_id` from `config/desks.py`; shows can override this with their own background (e.g. Special Report uses a distinct look)
- Requires `imageio-ffmpeg` (already in `requirements.txt` — bundles FFmpeg, no system install needed)
- Composite results are cached in `./cache/broll_composites/`; downloaded video clips cached in `./cache/broll_video_downloads/`
- For video clips, the source footage loops seamlessly for the 15-second composite window HeyGen then loops
- The PIP preserves the original aspect ratio of the source media — no stretching
- Falls back to a Pillow static image composite if FFmpeg is unavailable (images only; video b-roll falls back to studio background)

### Environment Variables

Copy `.env.example` to `.env` and fill in your keys:

```bash
cp .env.example .env
```

```env
NEWSROOM_NAME="Defy Logic News"

OPENAI_API_KEY="sk-..."
TAVILY_API_KEY="tvly-..."
PIXABAY_API_KEY=""         # Free at pixabay.com/api — enables video b-roll search

HEYGEN_API_KEY="sk_..."

HOST=0.0.0.0
PORT=8091
DEBUG=True
LOG_LEVEL=INFO

ARTICLES_DIR=./output/articles
SCRIPTS_DIR=./output/scripts
MEDIA_DIR=./output/media
LOGS_DIR=./output/production_logs

YOUTUBE_CLIENT_SECRETS_PATH=credentials/youtube_client_secrets.json

# Event feeds — disabled by default, see "Event webhooks" above before enabling
EVENT_FEEDS_ENABLED=False
EVENT_FEED_POLL_SECONDS=300
EVENT_FEED_MIN_MAGNITUDE=6.0
EVENT_FEED_NWS_SEVERITIES=Extreme,Severe
EVENT_FEED_USER_AGENT="news-room-ai (contact: you@example.com)"
```

### Per-Agent Model Configuration

Every agent's model is resolved through `settings.model_for(role)` (`config/settings.py`), which defaults every role to `gpt-4o` but can be overridden independently per agent via env vars — useful for putting a stronger reasoning model on verification-heavy roles (Fact Checker, Compliance Checker) without paying for it on routine drafting roles:

```env
MODEL_EXECUTIVE_PRODUCER=gpt-4o
MODEL_RESEARCHER=gpt-4o
MODEL_WRITER=gpt-4o
MODEL_FACT_CHECKER=gpt-4o
MODEL_EDITOR=gpt-4o
MODEL_SCRIPT_WRITER=gpt-4o
MODEL_ANCHOR=gpt-4o
MODEL_VIDEO_EDITOR=gpt-4o
MODEL_PRODUCER=gpt-4o
MODEL_PUBLISHER=gpt-4o
MODEL_BREAKING_NEWS_CHECKER=gpt-4o
MODEL_COMPLIANCE_CHECKER=gpt-4o
```

Unset vars fall back to `gpt-4o`; no code changes needed to swap models.

---

## Running

```bash
cd news-room-ai
python main.py
```

Server starts at `http://0.0.0.0:8091`.

### Logging

`main.py` writes to `logs/newsroom_YYYY-MM-DD.log` — a fresh dated file each day (or on any restart that crosses into a new day), size-capped within that day via `concurrent_log_handler.ConcurrentRotatingFileHandler` (`LOG_MAX_BYTES`, default 25MB), with gzip'd numbered backups (`LOG_BACKUP_COUNT`, default 10) and old dated files pruned after `LOG_RETENTION_DAYS` (default 30) — a log4j-style combined size+time rolling policy. `ConcurrentRotatingFileHandler` specifically (not stdlib's `RotatingFileHandler`) because uvicorn's `--reload` briefly runs two processes across a restart, and plain rotating handlers aren't safe when more than one process can write/rotate the same file concurrently.

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service info and available workflows |
| `/health` | GET | Agent status |
| `/produce` | POST | Run a production synchronously (blocks until complete) |
| `/produce/async` | POST | Start a production in the background — returns `job_id` immediately |
| `/job/{job_id}` | GET | Poll for the status and result of an async production job |
| `/produce/stream` | POST | Run a production via SSE (streams status updates) |
| `/webhook/ingest` | POST | Push a normalized external event (earthquake/weather/market-data feed, RSS bridge, etc.) through the same breaking-news qualifying-criteria and dedup/cooldown gates as the scheduled poll |
| `/docs` | GET | Swagger UI |

### Request format

```json
{
  "request": "Produce a full news segment on the situation in the Strait of Hormuz",
  "client_datetime": "Saturday, April 5, 2026, 03:00 PM PDT"
}
```

### Async response (`/produce/async`)

```json
{ "job_id": "e4b130b8-a012-4722-9562-388a9ab7aa4b", "status": "started" }
```

### Job status (`/job/{job_id}`)

```json
{
  "status": "running | complete | error",
  "result": "**Production Complete — BROADCAST_VIDEO**\nTopic: ...",
  "workflow": "BROADCAST_VIDEO",
  "topic": "Strait of Hormuz",
  "error": null
}
```

---

## Calling from Jarvis

Jarvis routes news production requests automatically. Just talk naturally:

```
Jarvis, produce a full news segment on the Iran situation
Jarvis, research the latest on shipping through the Strait of Hormuz
Jarvis, generate a news video about the drone strike near Dubai — have Darlene Smith read it
Jarvis, schedule a daily broadcast video at 6am on the latest White House announcements
Jarvis, write a news article about the SpaceX launch
Jarvis, do a special report on the development of the New Glenn rocket — make it 15 minutes
```

Jarvis responds immediately confirming production has started, then notifies you when the video is published. The newsroom backend must be running at `http://localhost:8091`.

To call the API directly:

```bash
curl -X POST http://localhost:8091/produce/async \
  -H "Content-Type: application/json" \
  -d '{"request": "Write a news article about the Strait of Hormuz shipping situation"}'
```

---

## Testing

`tests/` is a `pytest` suite covering the Executive Producer's orchestration logic — self-correction routing/retries, the compliance gate, the human review queue, the webhook endpoint and event feed adapters, story dossiers, and the success/error status surfaced to callers. Everything in it is mocked (no live LLM/API calls, no external network access, no cost):

```bash
pip install pytest pytest-asyncio
pytest
```

One exception: `tests/test_video_editor_graphics_smoke.py` runs the real bundled FFmpeg binary against a synthetic in-memory test clip (generated via FFmpeg's own `lavfi` source, no external assets) to actually prove the graphic-overlay filter chain and duration parsing work on this system — a few real (but fast, seconds-long) encodes, still no network calls or cost, still safe to run as part of the normal suite.

`test_tools.py` is separate and different in kind — a manual smoke test (`python test_tools.py`) that makes real calls against every configured API (OpenAI, Tavily, Pixabay, HeyGen, YouTube) to confirm credentials and connectivity. Run it deliberately, not as part of routine iteration.
