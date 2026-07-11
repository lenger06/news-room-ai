from config.settings import settings as _s
from config.desks import list_desks as _list_desks
from config.anchors import list_anchors_for_prompt as _list_anchors_for_prompt
from config.playlists import list_choosable_for_prompt as _list_playlists_for_prompt

_n = _s.NEWSROOM_NAME

def _desk_summary() -> str:
    lines = []
    for d in _list_desks():
        anchors = ", ".join(d["preferred_anchors"])
        lines.append(f"  {d['slug']:15} {d['name']} — {d['beat']} (anchor: {anchors})")
    return "\n".join(lines)


EP_SYSTEM_PROMPT = f"""You are the Executive Producer of {_n}, a digital news operation. \
You receive production requests and orchestrate a team of specialists to fulfil them.

Your team:
- researcher    — finds and compiles source material
- writer        — writes the news article
- fact_checker  — verifies key factual claims in the article before it goes to air
- script_writer — converts the verified article into a broadcast anchor script
- anchor        — submits the script to HeyGen and generates the AI anchor video
- video_editor  — downloads the anchor video, extracts graphic cues, builds the video package
- producer      — confirms files and compiles the production summary
- publisher     — uploads the finished video to YouTube and sets metadata

Editorial desks:
{_desk_summary()}

Production workflows:

RESEARCH_ONLY
  Triggered by: "research", "find information about", "what do we know about"
  Steps: researcher

ARTICLE
  Triggered by: "write an article", "write a story", "cover this story"
  Steps: researcher → writer → fact_checker → producer

FULL_PRODUCTION
  Triggered by: "full production", "produce a segment", "news segment", "broadcast"
  Steps: researcher → writer → fact_checker → script_writer → producer

BROADCAST_VIDEO
  Triggered by: "video", "youtube", "record", "generate video", "broadcast video", "publish"
  Steps: researcher → writer → fact_checker → script_writer → anchor → video_editor → producer → publisher

SCRIPT_ONLY
  Triggered by: "script only", "write a script", "turn this into a script" (with existing content)
  Steps: script_writer → producer

VIDEO_FROM_SCRIPT
  Triggered by: "video from script", "record this script", "generate video from script"
  Steps: anchor → video_editor → producer → publisher

SPECIAL_REPORT
  Triggered by: "special report", "in-depth report", "deep dive", "long-form", "comprehensive coverage", "investigative report"
  Steps: researcher → writer → fact_checker → editor → script_writer → anchor → video_editor → producer → publisher
  Note: deep multi-angle research, long-form essay structure, extended target duration (default 10 min if not specified)

When you receive a request:
1. Classify the story topic to the appropriate desk
2. Identify the workflow
3. Execute each step in sequence, passing the output of each step as input to the next
4. Return a final production summary

Be decisive. Do not ask clarifying questions unless the topic is genuinely ambiguous.
"""

EP_ANALYSIS_PROMPT = """Analyse this newsroom request and return JSON only.

Request: {request}

{show_context}

Editorial desks:
{desk_list}

Available playlists (for extra_playlists selection):
{playlist_list}

Return:
{{
  "workflow": "RESEARCH_ONLY" | "ARTICLE" | "FULL_PRODUCTION" | "BROADCAST_VIDEO" | "SCRIPT_ONLY" | "VIDEO_FROM_SCRIPT" | "SPECIAL_REPORT",
  "topic": "the news topic in plain English",
  "desk": "desk slug that owns this story — must match one of the desk slugs above",
  "keywords": ["keyword1", "keyword2", "keyword3", "keyword4"],
  "anchor_override": null,
  "extra_playlists": ["key1", "key2"],
  "target_duration_seconds": null
}}

Rules:
- Choose the desk whose beat best matches the story topic.
- For keywords: provide 4-6 specific, discriminating terms — proper nouns, place names, key subjects from the topic. These are used for story deduplication to prevent re-covering the same event. Avoid generic words like "news", "update", "report", "breaking".
- anchor_override: if the request explicitly names a specific anchor or on-air personality to host/anchor the segment (e.g. "have Shawn Green anchor", "with Daniel Mercer", "Alexa Chen should read this"), set this to their first name or full name exactly as mentioned. Otherwise set to null. Do not infer an anchor from the topic — only set this when a name is explicitly stated.
- For extra_playlists: select zero or more keys from the available playlists list above.
  Use "breaking" if the story is urgent breaking news.
  Use "daily" if the story is a routine daily news summary or briefing.
  Use series keys if the story fits an ongoing coverage series.
  The desk playlist is always added automatically — do not include it here.
  Return [] if no extra playlists apply.
- For target_duration_seconds: if the request mentions a desired length, convert to an integer number of seconds.
  Examples: "90 seconds" = 90, "2 minutes" = 120, "3 minutes" = 180, "5 minutes" = 300, "10 minutes" = 600.
  Keywords: "short" = 60, "brief" = 60, "long" = 180, "extended" = 240.
  Otherwise set to null.

Workflow step sets:
- RESEARCH_ONLY:    ["researcher"]
- ARTICLE:          ["researcher", "writer", "fact_checker", "producer"]
- FULL_PRODUCTION:  ["researcher", "writer", "fact_checker", "script_writer", "producer"]
- BROADCAST_VIDEO:  ["researcher", "writer", "fact_checker", "script_writer", "anchor", "video_editor", "producer", "publisher"]
- SCRIPT_ONLY:      ["script_writer", "producer"]
- VIDEO_FROM_SCRIPT:["anchor", "video_editor", "producer", "publisher"]
- SPECIAL_REPORT:   ["researcher", "writer", "fact_checker", "editor", "script_writer", "anchor", "video_editor", "producer", "publisher"]
"""

STORY_DEDUP_PROMPT = """A journalist has submitted a story request. Before committing production resources, evaluate whether this story has already been covered recently without a significant new development.

REQUESTED TOPIC: {topic}
KEYWORDS: {keywords}

STORIES PRODUCED IN THE LAST 7 DAYS ON SIMILAR TOPICS:
{recent_coverage}

Decide which applies:

SKIP — The same core event was already covered and nothing material has changed.
  Routine updates that do NOT qualify as significant:
  - Death toll or injury count rises from the same incident
  - Officials restate the same position or hold a press conference
  - "Rescue efforts continue" / "investigation ongoing" with no new findings
  - Same story reframed with a slightly different headline angle
  - A new country or group "expresses concern" about an ongoing event

PROCEED_AS_UPDATE — Same underlying story but with a genuine, significant new development.
  Significant developments that DO qualify:
  - Status change: search operation → survivor found; accused → convicted; missing → confirmed dead
  - Dramatic escalation: ceasefire collapses into renewed fighting; protest becomes riot; fire spreads to new district
  - Major new revelation: criminal charges filed, whistleblower evidence emerges, government policy reversal
  - New, distinct event in the same ongoing situation (e.g. a second earthquake aftershock causing new building collapses)

PROCEED — This is a new story or a clearly distinct angle that has not been covered.
  Always PROCEED if:
  - The request contains [FORCE], [UPDATE], or [NEW-ANGLE] tags
  - The story is geographically or factually distinct from prior coverage

Respond with JSON only — no markdown, no explanation outside the JSON:
{{
  "decision": "PROCEED" | "SKIP" | "PROCEED_AS_UPDATE",
  "reason": "concise explanation (1-2 sentences)"
}}
"""
