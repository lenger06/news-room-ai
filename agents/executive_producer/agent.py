"""
Executive Producer — orchestrates the full news production workflow.
Receives requests from Jarvis and delegates to researcher, writer,
script_writer, and producer in sequence.
"""

import sys
import json
import re
import logging
from pathlib import Path
from typing import TypedDict, List, Dict, Any, Optional

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from langgraph.graph import StateGraph, END, START
from langchain_openai import ChatOpenAI
from langchain_core.messages import SystemMessage, HumanMessage

from agents.registry import BaseAgent, AgentInfo, agent_registry
from agents.executive_producer.prompts import EP_SYSTEM_PROMPT, EP_ANALYSIS_PROMPT, STORY_DEDUP_PROMPT
from config.settings import settings
from config.anchors import get_anchor, list_anchors
from config.desks import get_desk, list_desks
from config.playlists import resolve_playlist_ids, get_ids_by_keys

logger = logging.getLogger(__name__)


class ProductionState(TypedDict):
    request: str
    topic: str
    workflow: str
    steps: List[str]

    # Assigned desk
    desk: str
    desk_name: str
    desk_prompt_style: str
    desk_background_asset_id: str

    # Selected anchor for this production
    anchor_name: str
    anchor_avatar_id: str
    anchor_avatar_position: str     # "left" | "center" | "right"
    anchor_voice_id: str
    anchor_voice_emotion: str
    anchor_talking_style: str
    anchor_expression: str

    # Target video duration (seconds); None = let script_writer use its default
    target_duration_seconds: Optional[int]

    # Active broadcast (set from show schedule or passed in context["show_slug"])
    show_slug: str
    show_name: str
    show_tone: str
    video_style: str                 # "pip_v2" or "fullscreen_v3" — inherited from Show, overridable

    # Per-run output directory: ./output/{show_slug}/{run_id}
    output_dir: str
    run_id: str

    # YouTube playlists
    playlist_ids: List[str]         # fully resolved IDs (automatic + EP picks)
    extra_playlist_keys: List[str]  # EP-selected keys from the playlists menu

    # Accumulated outputs keyed by agent name
    outputs: Dict[str, str]

    # Story keywords extracted by LLM analysis (used for dedup matching)
    keywords: List[str]

    # Dedup gate — set by _dedup_check_node before any pipeline step runs
    dedup_suppressed: bool
    dedup_reason: str

    # Formatted prior-coverage entries (from story_history), surfaced to the writer
    # for continuity context — set by _dedup_check_node whenever similar stories exist,
    # independent of the dedup SKIP/PROCEED decision.
    prior_coverage: str

    # Story dossier (see tools/dossiers.py) — an evolving per-thread markdown doc,
    # matched by keyword overlap in _dedup_check_node and appended to in _summarise_node.
    dossier_slug: str
    dossier_context: str

    # Current step tracking
    current_step_index: int
    last_step_name: str
    anchor_failed: bool
    researcher_failed: bool
    error: Optional[str]
    final_summary: str

    # Self-correction loop state (see _route_after_step)
    fact_check_verdict: str
    fact_check_attempts: int
    compliance_verdict: str
    needs_human_review: bool
    human_review_reason: str
    review_stage: str


def _dossier_summary_snippet(text: str, max_chars: int = 500) -> str:
    """Extract a short lead-paragraph summary from an article for a dossier entry —
    strips the editor's note block if present and caps length. A cheap heuristic, not
    a dedicated summarization pass, so dossier updates don't cost an extra LLM call."""
    if not text:
        return ""
    body = text.split("## EDITOR'S NOTE")[0].strip()
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    snippet = "\n\n".join(paragraphs[:2])
    return snippet[:max_chars].strip()


class Agent(BaseAgent):
    """Executive Producer — newsroom orchestrator."""

    WORKFLOW_STEPS = {
        "RESEARCH_ONLY":    ["researcher"],
        "ARTICLE":          ["researcher", "writer", "fact_checker", "editor", "producer"],
        "FULL_PRODUCTION":  ["researcher", "writer", "fact_checker", "editor", "script_writer", "producer"],
        "BROADCAST_VIDEO":  ["researcher", "writer", "fact_checker", "editor", "script_writer", "anchor", "video_editor", "compliance_checker", "producer", "publisher"],
        "SCRIPT_ONLY":      ["script_writer", "producer"],
        "VIDEO_FROM_SCRIPT":["anchor", "video_editor", "compliance_checker", "producer", "publisher"],
        "SPECIAL_REPORT":   ["researcher", "writer", "fact_checker", "editor", "script_writer", "anchor", "video_editor", "compliance_checker", "producer", "publisher"],
    }

    # Self-correction loop (see _route_after_step): how many total fact_checker passes
    # to allow (initial check + re-verifications after editor corrections) before
    # giving up and halting the pipeline for human review instead of publishing.
    MAX_FACT_CHECK_ATTEMPTS = 2
    _FAILING_FACT_CHECK_VERDICTS = {"HOLD FOR CORRECTIONS", "ERROR", "UNKNOWN"}
    _FAILING_COMPLIANCE_VERDICTS = {"HOLD FOR REVIEW", "ERROR", "UNKNOWN"}

    def __init__(self):
        self.llm = ChatOpenAI(
            model=settings.model_for("executive_producer"),
            temperature=0.1,
            openai_api_key=settings.OPENAI_API_KEY,
        )
        self.workflow = self._build_workflow()
        logger.info("Executive Producer initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="executive_producer",
            display_name="Executive Producer",
            description="Orchestrates the full news production workflow",
            version="1.0.0",
            module_path="agents.executive_producer.agent",
            parent_agent=None,
            manages_agents=["researcher", "writer", "script_writer", "producer"],
        )

    # ------------------------------------------------------------------ #
    #  Workflow                                                            #
    # ------------------------------------------------------------------ #

    def _build_workflow(self):
        graph = StateGraph(ProductionState)
        graph.add_node("analyse", self._analyse_node)
        graph.add_node("dedup_check", self._dedup_check_node)
        graph.add_node("execute_step", self._execute_step_node)
        graph.add_node("summarise", self._summarise_node)

        graph.add_edge(START, "analyse")
        graph.add_edge("analyse", "dedup_check")
        graph.add_conditional_edges(
            "dedup_check",
            lambda s: "suppressed" if s.get("dedup_suppressed") else "proceed",
            {"suppressed": "summarise", "proceed": "execute_step"},
        )
        graph.add_conditional_edges(
            "execute_step",
            self._route_after_step,
            {"next_step": "execute_step", "done": "summarise"},
        )
        graph.add_edge("summarise", END)
        return graph.compile()

    async def _analyse_node(self, state: ProductionState) -> ProductionState:
        """Detect the active show, use LLM for workflow/desk/topic, select anchor deterministically."""
        from config.shows import detect_show, get_show
        from tools.anchor_rotation import get_next_look, get_show_anchor_name

        # Show detection is deterministic — no API call needed
        show_slug = state.get("show_slug", "")
        show = get_show(show_slug) if show_slug else detect_show()
        state["show_slug"] = show.slug
        state["show_name"] = show.name
        state["show_tone"] = show.tone

        from datetime import datetime as _dt
        _run_id = _dt.now().strftime("%Y%m%d_%H%M%S")
        _output_dir = f"./output/{show.slug}/{_run_id}"
        for _sub in ("articles", "scripts", "media", "production_logs"):
            (Path(_output_dir) / _sub).mkdir(parents=True, exist_ok=True)
        state["run_id"] = _run_id
        state["output_dir"] = _output_dir

        try:
            from config.playlists import list_choosable_for_prompt
            desk_list = "\n".join(
                f"  {d['slug']:15} {d['name']} — {d['beat']}"
                for d in list_desks()
            )
            response = await self.llm.ainvoke([
                SystemMessage(content=EP_SYSTEM_PROMPT),
                HumanMessage(content=EP_ANALYSIS_PROMPT.format(
                    request=state["request"],
                    desk_list=desk_list,
                    playlist_list=list_choosable_for_prompt(),
                    show_context=show.for_prompt(),
                )),
            ])
            content = response.content
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if match:
                parsed = json.loads(match.group())
                workflow = parsed.get("workflow", "ARTICLE")
                steps = self.WORKFLOW_STEPS.get(workflow, self.WORKFLOW_STEPS["ARTICLE"])
                state["workflow"] = workflow
                state["topic"] = parsed.get("topic", state["request"])
                state["steps"] = steps

                # Resolve desk
                desk_slug = parsed.get("desk", "national")
                desk = get_desk(desk_slug)
                state["desk"] = desk.slug if desk else "national"
                state["desk_name"] = desk.name if desk else "National Desk"
                state["desk_prompt_style"] = desk.prompt_style if desk else ""
                state["desk_background_asset_id"] = desk.background_asset_id if desk else "f6fa4085043140deaba8258a96233036"

                # Force special-report show for SPECIAL_REPORT workflow
                if workflow == "SPECIAL_REPORT" and show_slug != "special-report":
                    from config.shows import get_show as _get_show
                    _sr = _get_show("special-report")
                    if _sr:
                        show = _sr
                        show_slug = "special-report"
                        state["show_slug"] = show_slug
                        state["show_name"] = show.name
                        state["show_tone"] = show.tone
                        logger.info("[EP] SPECIAL_REPORT — overriding show to special-report")

                # Honour explicit anchor request; otherwise use show schedule
                anchor_override_name = parsed.get("anchor_override") or ""
                if anchor_override_name:
                    anchor = get_anchor(name=anchor_override_name)
                    if anchor:
                        logger.info(f"[EP] Anchor override: {anchor.name}")
                        avatar_id, avatar_position = get_next_look(anchor, desk=state["desk"])
                    else:
                        logger.warning(f"[EP] Anchor override '{anchor_override_name}' not found — falling back to schedule")
                        anchor_override_name = ""

                if not anchor_override_name:
                    # Select anchor deterministically from show schedule (with stand-in rotation)
                    assignment = show.anchor_for_desk(state["desk"])
                    if assignment:
                        anchor_name = get_show_anchor_name(show.slug, assignment)
                        anchor = get_anchor(name=anchor_name)
                        avatar_id, avatar_position = get_next_look(anchor, assignment.look_preference, state["desk"])
                    else:
                        anchor = get_anchor(desk=state["desk"])
                        avatar_id, avatar_position = get_next_look(anchor, desk=state["desk"])

                state["anchor_name"] = anchor.name
                state["anchor_avatar_id"] = avatar_id
                state["anchor_avatar_position"] = avatar_position
                state["anchor_voice_id"] = anchor.voice_id
                state["anchor_voice_emotion"] = anchor.voice_emotion or ""
                state["anchor_talking_style"] = anchor.talking_style or ""
                state["anchor_expression"] = anchor.expression or ""
                state["keywords"] = parsed.get("keywords") or []
                state["extra_playlist_keys"] = parsed.get("extra_playlists") or []
                state["playlist_ids"] = resolve_playlist_ids(
                    state["desk"], anchor.name, workflow, state["topic"],
                    show_slug=state["show_slug"],
                )
                raw_dur = parsed.get("target_duration_seconds")
                if raw_dur:
                    state["target_duration_seconds"] = int(raw_dur)
                elif workflow == "SPECIAL_REPORT":
                    state["target_duration_seconds"] = 600   # default 10 min for special reports
                else:
                    state["target_duration_seconds"] = None

                # Show-level background and video style override desk defaults
                if show.background_asset_id:
                    state["desk_background_asset_id"] = show.background_asset_id
                state["video_style"] = show.video_style
                logger.info(
                    f"[EP] Show: {show.name} | Workflow: {workflow} | Desk: {state['desk_name']} | "
                    f"Anchor: {anchor.name} | Avatar: {avatar_id[:24]}… | "
                    f"Duration: {state['target_duration_seconds']}s | Topic: {state['topic']}"
                )
            else:
                state["workflow"] = "ARTICLE"
                state["steps"] = self.WORKFLOW_STEPS["ARTICLE"]
                state["topic"] = state["request"]
                state["desk"] = "national"
                state["desk_name"] = "National Desk"
                state["desk_prompt_style"] = ""
                state["desk_background_asset_id"] = show.background_asset_id or "f6fa4085043140deaba8258a96233036"
                assignment = show.anchor_for_desk("national")
                if assignment:
                    anchor_name = get_show_anchor_name(show.slug, assignment)
                    anchor = get_anchor(name=anchor_name)
                    avatar_id, avatar_position = get_next_look(anchor, assignment.look_preference, "national")
                else:
                    anchor = get_anchor()
                    avatar_id = anchor.default_avatar_id
                    avatar_position = anchor.avatars[0].avatar_position if anchor.avatars else "center"
                state["anchor_name"] = anchor.name
                state["anchor_avatar_id"] = avatar_id
                state["anchor_avatar_position"] = avatar_position
                state["anchor_voice_id"] = anchor.voice_id
                state["anchor_voice_emotion"] = anchor.voice_emotion or ""
                state["anchor_talking_style"] = anchor.talking_style or ""
                state["anchor_expression"] = anchor.expression or ""
                state["keywords"] = []
                state["extra_playlist_keys"] = []
                state["playlist_ids"] = resolve_playlist_ids(
                    "national", anchor.name, "ARTICLE", state["topic"],
                    show_slug=state.get("show_slug", ""),
                )
        except Exception as e:
            logger.error(f"[EP] Analysis error: {e}", exc_info=True)
            state["workflow"] = "ARTICLE"
            state["steps"] = self.WORKFLOW_STEPS["ARTICLE"]
            state["topic"] = state["request"]
            state["desk"] = "national"
            state["desk_name"] = "National Desk"
            state["desk_prompt_style"] = ""
            state["desk_background_asset_id"] = "f6fa4085043140deaba8258a96233036"
            anchor = get_anchor()
            state["anchor_name"] = anchor.name
            state["anchor_avatar_id"] = anchor.default_avatar_id
            state["anchor_avatar_position"] = anchor.avatars[0].avatar_position if anchor.avatars else "center"
            state["anchor_voice_id"] = anchor.voice_id
            state["anchor_voice_emotion"] = anchor.voice_emotion or ""
            state["anchor_talking_style"] = anchor.talking_style or ""
            state["anchor_expression"] = anchor.expression or ""
            state["keywords"] = []
            state["extra_playlist_keys"] = []
            state["playlist_ids"] = []
            state["error"] = str(e)
        return state

    async def _dedup_check_node(self, state: ProductionState) -> ProductionState:
        """Check story history to prevent duplicate coverage before the pipeline starts,
        and look up a matching story dossier (see tools/dossiers.py) for continuity context."""
        from tools.story_history import find_similar, format_for_llm
        from tools.dossiers import find_matching_slug, format_for_prompt

        state["dedup_suppressed"] = False
        state["dedup_reason"] = ""
        state["prior_coverage"] = ""
        state["dossier_slug"] = ""
        state["dossier_context"] = ""

        workflow = state.get("workflow", "")
        # Dedup/dossier only applies to workflows that produce original researched content
        if workflow in ("RESEARCH_ONLY", "SCRIPT_ONLY", "VIDEO_FROM_SCRIPT"):
            return state

        # Dossier lookup is read-only here — creation happens in _summarise_node, only for
        # productions that actually complete, so a SKIPped/suppressed story doesn't register
        # an empty dossier entry.
        keywords_for_dossier = state.get("keywords", [])
        if keywords_for_dossier:
            try:
                slug = find_matching_slug(keywords_for_dossier)
                if slug:
                    state["dossier_slug"] = slug
                    state["dossier_context"] = format_for_prompt(slug)
                    logger.info(f"[EP] Dossier match: {slug}")
            except Exception as e:
                logger.warning(f"[EP] Dossier lookup failed: {e}")

        # Allow explicit force/update overrides
        request_upper = state.get("request", "").upper()
        if any(tag in request_upper for tag in ("[FORCE]", "[UPDATE]", "[NEW-ANGLE]", "[FORCE-PRODUCE]")):
            logger.info("[EP] Dedup bypassed — override tag detected in request")
            return state

        keywords = state.get("keywords", [])
        if len(keywords) < 2:
            logger.info("[EP] Dedup skipped — fewer than 2 keywords extracted")
            return state

        similar = find_similar(keywords, hours=168.0)
        if not similar:
            return state

        recent_text = format_for_llm(similar[:10])
        # Surface to the writer for continuity ("as we reported last Tuesday") regardless
        # of the SKIP/PROCEED decision below — this is prior-coverage context, not a dedup gate.
        state["prior_coverage"] = format_for_llm(similar[:5])
        logger.info(f"[EP] Dedup: found {len(similar)} similar story/stories in history — evaluating")

        try:
            response = await self.llm.ainvoke([
                HumanMessage(content=STORY_DEDUP_PROMPT.format(
                    topic=state["topic"],
                    keywords=", ".join(keywords),
                    recent_coverage=recent_text,
                )),
            ])
            content = response.content.strip()
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
            parsed = json.loads(content)
            decision = parsed.get("decision", "PROCEED").upper()
            reason = parsed.get("reason", "")

            if decision == "SKIP":
                logger.info(f"[EP] Dedup suppressed production: {reason[:120]}")
                state["dedup_suppressed"] = True
                state["dedup_reason"] = reason
            elif decision == "PROCEED_AS_UPDATE":
                logger.info(f"[EP] Dedup: story allowed as update — {reason[:120]}")
                state["dedup_reason"] = reason
                state["request"] = (
                    state["request"]
                    + f"\n\n[UPDATE NOTE: This is a follow-up to prior coverage. "
                    f"Focus on the new development and do not recap the full backstory: {reason}]"
                )
            else:
                logger.info(f"[EP] Dedup: story is new — proceeding")
        except Exception as e:
            logger.warning(f"[EP] Dedup check failed ({e}) — allowing production to proceed")

        return state

    async def _execute_step_node(self, state: ProductionState) -> ProductionState:
        """Execute the current step in the workflow."""
        idx = state.get("current_step_index", 0)
        steps = state["steps"]

        if idx >= len(steps):
            return state

        agent_name = steps[idx]
        output_dir = state.get("output_dir", "")
        logger.info(f"[EP] Executing step {idx + 1}/{len(steps)}: {agent_name}")

        try:
            agent = await agent_registry.get_agent(agent_name)
            if not agent:
                raise RuntimeError(f"Agent '{agent_name}' not available")

            # Build input for this step: request + prior outputs as context
            prior_outputs = state.get("outputs", {})
            anchor_name = state.get("anchor_name", "")
            anchor_avatar_id = state.get("anchor_avatar_id", "")
            anchor_voice_id = state.get("anchor_voice_id", "")

            from datetime import date as _date
            today_str = _date.today().strftime("%B %d, %Y")

            if prior_outputs:
                context_block = "\n\n".join(
                    f"=== {name.upper()} OUTPUT ===\n{text}"
                    for name, text in prior_outputs.items()
                )
                step_input = (
                    f"TODAY'S DATE: {today_str}\n\n"
                    f"TOPIC: {state['topic']}\n\n"
                    f"ORIGINAL REQUEST: {state['request']}\n\n"
                    f"{context_block}\n\n"
                    f"Now perform your role for this story."
                )
            else:
                step_input = (
                    f"TODAY'S DATE: {today_str}\n\n"
                    f"TOPIC: {state['topic']}\n\n"
                    f"REQUEST: {state['request']}\n\n"
                    f"Begin your work."
                )

            is_special_report = state.get("workflow") == "SPECIAL_REPORT"

            if state.get("dossier_context") and agent_name in ("researcher", "writer"):
                step_input += (
                    "\n\nSTORY DOSSIER (accumulated context from our own prior coverage of this "
                    "ongoing story — use it to understand what's already established and focus "
                    "on what's new; it is not a substitute for live research on today's request):\n"
                    f"{state['dossier_context']}"
                )

            if output_dir and agent_name in ("writer", "fact_checker", "editor"):
                step_input += f"\n\nSAVE_DIR: {output_dir}/articles"

            if agent_name == "researcher" and is_special_report:
                step_input += (
                    "\n\nSPECIAL REPORT MODE — deep multi-angle research required. "
                    "Run at least 8–10 searches covering: (1) latest developments, "
                    "(2) historical background and timeline, (3) key figures and their positions, "
                    "(4) expert analysis and commentary, (5) opposing viewpoints and criticism, "
                    "(6) economic or social impact, (7) international or comparative context, "
                    "(8) what happens next / what to watch. "
                    "Your brief must be comprehensive enough to support a 10+ minute broadcast."
                )

            if agent_name == "writer":
                if state.get("prior_coverage"):
                    step_input += (
                        "\n\nPRIOR COVERAGE (from our own recent reporting — for continuity only, "
                        "e.g. \"as we reported earlier this week\"; do not treat this as a fact "
                        "source for the current story, and do not re-report old details as new):\n"
                        f"{state['prior_coverage']}"
                    )
                target_dur = state.get("target_duration_seconds")
                if target_dur:
                    target_words = round(target_dur * 150 / 60)
                    step_input += (
                        f"\n\nTARGET WORD COUNT: approximately {target_words} words "
                        f"(needed to support a {target_dur}-second broadcast). "
                        f"This overrides the default 400–600 word target."
                    )
                if is_special_report:
                    step_input += (
                        "\n\nSPECIAL REPORT FORMAT — this is a long-form analytical piece, "
                        "not a standard news article. Structure it as: "
                        "(1) Executive Summary, (2) Background & Context, "
                        "(3) Key Developments, (4) Multiple Perspectives & Expert Analysis, "
                        "(5) Implications & What's Next, (6) Conclusion. "
                        "Write in depth — every section must be substantive, multiple paragraphs. "
                        "IMPORTANT: specific facts (names, dates, statistics, direct quotes) must "
                        "come from the research brief. However, you MAY and SHOULD add explanatory "
                        "context, analytical commentary, historical parallels, and elaboration that "
                        "helps a general audience understand why these facts matter. "
                        "Do not pad with repetition — expand with genuine analysis."
                    )

            # Inject desk + anchor context for script_writer and anchor steps
            if agent_name == "script_writer" and anchor_name:
                desk_name = state.get("desk_name", "")
                desk_style = state.get("desk_prompt_style", "")
                target_dur = state.get("target_duration_seconds")
                show_name = state.get("show_name", "")
                show_tone = state.get("show_tone", "")
                step_input += (
                    f"\n\nSHOW: {show_name}\n"
                    f"SHOW TONE: {show_tone}\n"
                    f"DESK: {desk_name}\n"
                    f"DESK STYLE: {desk_style}\n"
                    f"ANCHOR: {anchor_name}\n"
                    f"Write the script for {anchor_name} to read. "
                    f"Use their name in the sign-off line instead of [ANCHOR]."
                )
                if target_dur:
                    target_words = round(target_dur * 150 / 60)
                    step_input += (
                        f"\nTARGET DURATION: {target_dur} seconds "
                        f"(approximately {target_words} words). "
                        f"This overrides the default read-time target."
                    )
                if is_special_report:
                    step_input += (
                        "\nSPECIAL REPORT SCRIPT — do NOT include any markdown headings, bold labels, "
                        "or chapter titles (no **Introduction**, no # Section, nothing like that). "
                        "The anchor reads everything aloud — section titles become spoken words the "
                        "audience hears, which sounds wrong. Use natural spoken transitions between "
                        "topics instead: 'To understand how we got here...', 'Now, looking at the "
                        "broader picture...', 'What does this mean going forward?'. "
                        "Each major topic shift should get its own [BROLL:] marker. "
                        "Open with a compelling hook and close with a strong forward-looking sign-off. "
                        "Do not rush — write the full target word count."
                    )
                if output_dir:
                    step_input += f"\nSAVE_DIR: {output_dir}/scripts"
            elif agent_name == "anchor" and anchor_avatar_id:
                background_asset_id = state.get("desk_background_asset_id", "")
                avatar_position = state.get("anchor_avatar_position", "center")
                step_input += (
                    f"\n\nANCHOR NAME: {anchor_name}\n"
                    f"AVATAR ID: {anchor_avatar_id}\n"
                    f"AVATAR POSITION: {avatar_position}\n"
                    f"VOICE ID: {anchor_voice_id}\n"
                    f"VOICE EMOTION: {state.get('anchor_voice_emotion', '')}\n"
                    f"TALKING STYLE: {state.get('anchor_talking_style', '')}\n"
                    f"EXPRESSION: {state.get('anchor_expression', '')}\n"
                    f"BACKGROUND ASSET ID: {background_asset_id}\n"
                    f"DESK_SLUG: {state.get('desk', '')}\n"
                    f"TOPIC: {state.get('topic', '')}\n"
                    f"VIDEO STYLE: {state.get('video_style', 'pip_v2')}\n"
                )
            elif agent_name == "video_editor":
                step_input += f"\n\nDESK_SLUG: {state.get('desk', '')}\n"
                if output_dir:
                    step_input += f"MEDIA_DIR: {output_dir}/media\n"
            elif agent_name == "publisher":
                import json as _json
                auto_ids = resolve_playlist_ids(
                    state.get("desk", ""),
                    state.get("anchor_name", ""),
                    state.get("workflow", ""),
                    state.get("topic", ""),
                    show_slug=state.get("show_slug", ""),
                )
                extra_ids = get_ids_by_keys(state.get("extra_playlist_keys", []))
                # Merge, deduplicate, preserve order
                seen: set[str] = set()
                playlist_ids: list[str] = []
                for pid in auto_ids + extra_ids:
                    if pid not in seen:
                        seen.add(pid)
                        playlist_ids.append(pid)
                if playlist_ids:
                    step_input += f"\n\nPLAYLIST_IDS: {_json.dumps(playlist_ids)}"
                    logger.info(
                        f"[EP] Publisher: {len(playlist_ids)} playlist(s) — "
                        f"auto={auto_ids} extra={extra_ids}"
                    )
                if output_dir:
                    step_input += f"\n\nMEDIA_DIR: {output_dir}/media"
                show_ep = state.get("show_name", "")
                if show_ep:
                    step_input += f"\nSHOW_NAME: {show_ep}"

            result = await agent.process_message(step_input)
            outputs = dict(state.get("outputs", {}))
            anchor_output = result.get("response", "")
            outputs[agent_name] = anchor_output
            state["outputs"] = outputs

            # Capture fact-checker verdict for the self-correction routing in _route_after_step.
            if agent_name == "fact_checker":
                verdict = result.get("verdict", "UNKNOWN")
                state["fact_check_verdict"] = verdict
                state["fact_check_attempts"] = state.get("fact_check_attempts", 0) + 1
                logger.info(
                    f"[EP] Fact-check attempt {state['fact_check_attempts']}: verdict={verdict}"
                )

            # Capture compliance verdict for the publish gate in _route_after_step.
            if agent_name == "compliance_checker":
                verdict = result.get("verdict", "UNKNOWN")
                state["compliance_verdict"] = verdict
                logger.info(f"[EP] Compliance verdict: {verdict}")

            # Guard: abort if researcher returned failure/empty content instead of real research.
            if agent_name == "researcher":
                _failure_phrases = [
                    "unable to access", "can't provide", "cannot provide",
                    "persistent issue", "i'm unable", "i cannot",
                    "failed to retrieve", "unable to retrieve",
                    "no results found", "unfortunately, without",
                ]
                _out_lower = anchor_output.lower()
                _has_failure = any(p in _out_lower for p in _failure_phrases)
                _has_sources = "http" in anchor_output or any(
                    kw in _out_lower
                    for kw in ("according to", "reported that", "sources:", "source:", "published")
                )
                _too_short = len(anchor_output.strip()) < 300
                if (_has_failure or _too_short) and not _has_sources:
                    logger.warning(
                        f"[EP] Researcher returned failure output (len={len(anchor_output)}) — "
                        "aborting pipeline. Likely cause: Tavily API unavailable or rate-limited."
                    )
                    state["researcher_failed"] = True
                    state["error"] = (
                        "Researcher was unable to retrieve source material "
                        "(Tavily API may be unavailable or rate-limited). "
                        "Production halted — no article or video generated."
                    )

            # If the anchor step returned no video_id, flag the pipeline to stop.
            if agent_name == "anchor":
                import json as _json
                try:
                    parsed = _json.loads(anchor_output)
                    if not parsed.get("video_id"):
                        raise ValueError("no video_id in anchor response")
                except Exception:
                    # Also catch plain-text failure messages
                    if '"video_id": null' in anchor_output or "FAILED" in anchor_output or "failed" in anchor_output.lower():
                        logger.warning("[EP] Anchor produced no video_id — halting pipeline.")
                        state["anchor_failed"] = True
                        state["error"] = "Anchor step failed to produce a video_id. Pipeline stopped."

        except Exception as e:
            logger.error(f"[EP] Step '{agent_name}' failed: {e}", exc_info=True)
            outputs = dict(state.get("outputs", {}))
            outputs[agent_name] = f"[FAILED: {str(e)}]"
            state["outputs"] = outputs
            state["error"] = str(e)
            if agent_name == "anchor":
                state["anchor_failed"] = True

        state["current_step_index"] = idx + 1
        state["last_step_name"] = agent_name
        return state

    def _route_after_step(self, state: ProductionState) -> str:
        idx = state.get("current_step_index", 0)

        # Self-correction loop: after editor patches a failing fact-check, re-verify with
        # fact_checker instead of trusting the one-shot correction blindly. Bounded by
        # MAX_FACT_CHECK_ATTEMPTS total fact-check passes; if still failing after that many,
        # halt before script/video/publish steps and route to human review instead of
        # guessing further or publishing an uncorrected story.
        if (
            state.get("last_step_name") == "editor"
            and state.get("fact_check_verdict") in self._FAILING_FACT_CHECK_VERDICTS
        ):
            attempts = state.get("fact_check_attempts", 0)
            if attempts < self.MAX_FACT_CHECK_ATTEMPTS:
                state["steps"] = state["steps"][:idx] + ["fact_checker", "editor"] + state["steps"][idx:]
                logger.info(
                    f"[EP] Re-verifying after correction (fact-check attempt {attempts + 1}/"
                    f"{self.MAX_FACT_CHECK_ATTEMPTS})"
                )
            else:
                state["needs_human_review"] = True
                state["review_stage"] = "fact_check"
                state["human_review_reason"] = (
                    f"Fact-checker still returned '{state['fact_check_verdict']}' after "
                    f"{attempts} attempt(s) and correction pass(es) — halting before "
                    f"script/video/publish steps."
                )
                logger.warning(f"[EP] {state['human_review_reason']}")
                return "done"

        # Compliance gate: unlike fact-check corrections, policy concerns aren't something
        # an automatic rewrite pass can reliably fix, so there's no retry loop here — a
        # HOLD verdict halts immediately, before the publish step, for a human look.
        if (
            state.get("last_step_name") == "compliance_checker"
            and state.get("compliance_verdict") in self._FAILING_COMPLIANCE_VERDICTS
        ):
            state["needs_human_review"] = True
            state["review_stage"] = "compliance"
            state["human_review_reason"] = (
                f"Compliance check returned '{state['compliance_verdict']}' — halting before publish."
            )
            logger.warning(f"[EP] {state['human_review_reason']}")
            return "done"

        if idx >= len(state["steps"]):
            return "done"
        if state.get("researcher_failed") or state.get("anchor_failed"):
            return "done"
        return "next_step"

    async def _summarise_node(self, state: ProductionState) -> ProductionState:
        """Build the final production summary returned to Jarvis and save it to disk."""
        from datetime import datetime, timezone

        # Short-circuit for dedup-suppressed productions — no log, no broadcast update
        if state.get("dedup_suppressed"):
            state["final_summary"] = (
                f"**Production Suppressed — Duplicate Story**\n\n"
                f"Topic: {state.get('topic', '')}\n\n"
                f"This story has already been covered recently with no significant new development detected.\n"
                f"Reason: {state.get('dedup_reason', '')}\n\n"
                f"No video was generated and no HeyGen credits were consumed.\n"
                f"To override, include [FORCE] or [UPDATE] in your request."
            )
            return state

        outputs = state.get("outputs", {})
        anchor_name = state.get("anchor_name", "")
        desk_name = state.get("desk_name", "")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        show_name = state.get("show_name", "")
        lines = [
            f"**Production Complete — {state['workflow']}**",
            f"Show: {show_name}" if show_name else "",
            f"Topic: {state['topic']}",
            f"Desk: {desk_name}" if desk_name else "",
            f"Anchor: {anchor_name}" if anchor_name else "",
            "",
        ]
        lines = [l for l in lines if l != ""]  # remove blank placeholder if no anchor
        lines.append("")

        for step in state["steps"]:
            output = outputs.get(step, "[no output]")
            # Show the full output for single-step workflows, truncate otherwise
            preview = output if len(state["steps"]) == 1 else output[:600] + ("…" if len(output) > 600 else "")
            lines.append(f"**{step.replace('_', ' ').title()}:**\n{preview}")
            lines.append("")

        if state.get("researcher_failed"):
            lines.append(f"🛑 PRODUCTION ABORTED — Researcher returned no usable content. {state.get('error', '')}")
        elif state.get("needs_human_review"):
            lines.append(
                f"🛑 HALTED FOR HUMAN REVIEW — {state.get('human_review_reason', '')} "
                f"No script/video/publish steps were run. Review the article and fact-check "
                f"notes above (or in this run's production log) before manually resuming."
            )
        elif state.get("error"):
            lines.append(f"⚠️ One or more steps encountered an error: {state['error']}")

        state["final_summary"] = "\n".join(lines)

        # Save full production log (all outputs untruncated)
        try:
            _out = state.get("output_dir", "")
            log_dir = Path(_out) / "production_logs" if _out else Path(settings.LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "production.md"

            full_lines = [
                f"# Production Log — {state['workflow']}",
                f"**Date:** {timestamp}",
                f"**Topic:** {state['topic']}",
            ]
            if desk_name:
                full_lines.append(f"**Desk:** {desk_name}")
            if anchor_name:
                full_lines.append(f"**Anchor:** {anchor_name}")
            full_lines.append("")

            for step in state["steps"]:
                output = outputs.get(step, "[no output]")
                full_lines.append(f"## {step.replace('_', ' ').title()}")
                full_lines.append(output)
                full_lines.append("")

            if state.get("error"):
                full_lines.append(f"## ⚠️ Errors")
                full_lines.append(state["error"])

            log_path.write_text("\n".join(full_lines), encoding="utf-8")
            logger.info(f"[EP] Production log saved: {log_path}")
        except Exception as e:
            logger.warning(f"[EP] Could not save production log: {e}")

        # Write last_broadcast.json for the breaking news checker's anchor handoff logic
        try:
            lb_path = Path("./output/last_broadcast.json")
            lb_path.parent.mkdir(parents=True, exist_ok=True)
            now_utc = datetime.now(timezone.utc)
            lb_path.write_text(json.dumps({
                "show_slug": state.get("show_slug", ""),
                "show_name": state.get("show_name", ""),
                "ts": now_utc.isoformat(),
                "ts_unix": now_utc.timestamp(),
            }, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning(f"[EP] Could not save last_broadcast.json: {e}")

        # Record to the human-review queue so halted productions are discoverable
        # without scanning every run's production log individually.
        if state.get("needs_human_review"):
            try:
                from tools.review_queue import record as _review_record
                _review_record(
                    topic=state.get("topic", ""),
                    reason=state.get("human_review_reason", ""),
                    stage=state.get("review_stage", "unknown"),
                    output_dir=state.get("output_dir", ""),
                    workflow=state.get("workflow", ""),
                )
            except Exception as e:
                logger.warning(f"[EP] Could not record to review queue: {e}")

        # Record to story_history so future productions can dedup against this one.
        # Skip if researcher/anchor failed or the piece was halted for human review —
        # none of these produced usable/publishable content.
        if not state.get("researcher_failed") and not state.get("anchor_failed") and not state.get("needs_human_review"):
            try:
                from tools.story_history import record as _sh_record
                _sh_record(
                    topic=state.get("topic", ""),
                    keywords=state.get("keywords", []),
                    show_slug=state.get("show_slug", ""),
                    workflow=state.get("workflow", ""),
                )
            except Exception as e:
                logger.warning(f"[EP] Could not record to story_history: {e}")

            # Append to the story dossier (tools/dossiers.py) — same success gate as
            # story_history above. Skipped when there are no keywords to match/create by.
            keywords = state.get("keywords", [])
            if keywords:
                try:
                    from tools.dossiers import get_or_create_slug, append_entry as _dossier_append
                    summary = _dossier_summary_snippet(
                        outputs.get("editor") or outputs.get("writer") or ""
                    )
                    if summary:
                        slug = get_or_create_slug(state.get("topic", ""), keywords)
                        _dossier_append(
                            slug=slug,
                            topic=state.get("topic", ""),
                            keywords=keywords,
                            summary=summary,
                            show_slug=state.get("show_slug", ""),
                        )
                except Exception as e:
                    logger.warning(f"[EP] Could not append to dossier: {e}")

        return state

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    async def process_message(self, message: str, context: dict = None) -> dict:
        import time
        _start_ts = time.time()
        logger.info(f"[EP] Received request: {message[:120]}")
        try:
            initial_state: ProductionState = {
                "request": message,
                "topic": "",
                "workflow": "",
                "steps": [],
                "desk": "",
                "desk_name": "",
                "desk_prompt_style": "",
                "desk_background_asset_id": "",
                "anchor_name": "",
                "anchor_avatar_id": "",
                "anchor_avatar_position": "center",
                "anchor_voice_id": "",
                "anchor_voice_emotion": "",
                "anchor_talking_style": "",
                "anchor_expression": "",
                "target_duration_seconds": None,
                "show_slug": (context or {}).get("show_slug", ""),
                "show_name": "",
                "show_tone": "",
                "video_style": "pip_v2",
                "output_dir": "",
                "run_id": "",
                "playlist_ids": [],
                "extra_playlist_keys": [],
                "outputs": {},
                "keywords": [],
                "dedup_suppressed": False,
                "dedup_reason": "",
                "prior_coverage": "",
                "dossier_slug": "",
                "dossier_context": "",
                "current_step_index": 0,
                "last_step_name": "",
                "anchor_failed": False,
                "researcher_failed": False,
                "error": None,
                "final_summary": "",
                "fact_check_verdict": "",
                "fact_check_attempts": 0,
                "compliance_verdict": "",
                "needs_human_review": False,
                "human_review_reason": "",
                "review_stage": "",
            }
            final_state = await self.workflow.ainvoke(initial_state)
            # dedup_suppressed is an intentional no-op (story already covered), not a
            # failure. researcher_failed/anchor_failed/needs_human_review all mean no
            # publishable output was produced — callers (Jarvis) need to see that as a
            # failure, not a silent "complete", or a halted production goes unnoticed.
            succeeded = not (
                final_state.get("researcher_failed")
                or final_state.get("anchor_failed")
                or final_state.get("needs_human_review")
            )

            # Phase 7.1 (SELF_IMPROVEMENT_ROADMAP.md) — record a structured outcome for
            # every run that actually reached the graph, dedup-suppressed runs included
            # (they're a real, meaningful outcome: nothing new was produced on purpose).
            # Never let this break the actual response — it's diagnostics, not the product.
            try:
                from tools.agent_outcomes import record as _outcome_record
                _published = "Published to YouTube successfully" in (
                    final_state.get("outputs", {}).get("publisher", "")
                )
                _outcome_record(
                    workflow=final_state.get("workflow", ""),
                    show_slug=final_state.get("show_slug", ""),
                    desk=final_state.get("desk", ""),
                    anchor_name=final_state.get("anchor_name", ""),
                    topic=final_state.get("topic", ""),
                    keywords=final_state.get("keywords", []),
                    fact_check_verdict=final_state.get("fact_check_verdict", ""),
                    fact_check_attempts=final_state.get("fact_check_attempts", 0),
                    compliance_verdict=final_state.get("compliance_verdict", ""),
                    published=_published,
                    succeeded=succeeded,
                    duration_seconds=time.time() - _start_ts,
                    run_id=final_state.get("run_id", ""),
                )
            except Exception as e:
                logger.warning(f"[EP] Could not record outcome: {e}")

            return {
                "success": succeeded,
                "response": final_state["final_summary"],
                "agent": "executive_producer",
                "workflow": final_state.get("workflow"),
                "topic": final_state.get("topic"),
                "desk": final_state.get("desk"),
                "desk_name": final_state.get("desk_name"),
            }
        except Exception as e:
            logger.error(f"[EP] Fatal error: {e}", exc_info=True)
            return {
                "success": False,
                "response": f"Newsroom production failed: {str(e)}",
                "agent": "executive_producer",
            }
