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
from agents.executive_producer.prompts import EP_SYSTEM_PROMPT, EP_ANALYSIS_PROMPT
from config.settings import settings
from config.anchors import get_anchor, list_anchors, list_anchors_for_prompt
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
    anchor_voice_id: str

    # Target video duration (seconds); None = let script_writer use its default
    target_duration_seconds: Optional[int]

    # YouTube playlists
    playlist_ids: List[str]         # fully resolved IDs (automatic + EP picks)
    extra_playlist_keys: List[str]  # EP-selected keys from the playlists menu

    # Accumulated outputs keyed by agent name
    outputs: Dict[str, str]

    # Current step tracking
    current_step_index: int
    anchor_failed: bool
    error: Optional[str]
    final_summary: str


class Agent(BaseAgent):
    """Executive Producer — newsroom orchestrator."""

    WORKFLOW_STEPS = {
        "RESEARCH_ONLY":    ["researcher"],
        "ARTICLE":          ["researcher", "writer", "fact_checker", "producer"],
        "FULL_PRODUCTION":  ["researcher", "writer", "fact_checker", "script_writer", "producer"],
        "BROADCAST_VIDEO":  ["researcher", "writer", "fact_checker", "script_writer", "anchor", "video_editor", "producer", "publisher"],
        "SCRIPT_ONLY":      ["script_writer", "producer"],
        "VIDEO_FROM_SCRIPT":["anchor", "video_editor", "producer", "publisher"],
    }

    def __init__(self):
        self.llm = ChatOpenAI(
            model="gpt-4o",
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
        graph.add_node("execute_step", self._execute_step_node)
        graph.add_node("summarise", self._summarise_node)

        graph.add_edge(START, "analyse")
        graph.add_edge("analyse", "execute_step")
        graph.add_conditional_edges(
            "execute_step",
            self._route_after_step,
            {"next_step": "execute_step", "done": "summarise"},
        )
        graph.add_edge("summarise", END)
        return graph.compile()

    async def _analyse_node(self, state: ProductionState) -> ProductionState:
        """Use LLM to determine workflow, desk, topic, and anchor selection."""
        try:
            from config.playlists import list_choosable_for_prompt
            anchor_list = list_anchors_for_prompt()
            desk_list = "\n".join(
                f"  {d['slug']:15} {d['name']} — {d['beat']}"
                for d in list_desks()
            )
            response = await self.llm.ainvoke([
                SystemMessage(content=EP_SYSTEM_PROMPT),
                HumanMessage(content=EP_ANALYSIS_PROMPT.format(
                    request=state["request"],
                    anchor_list=anchor_list,
                    desk_list=desk_list,
                    playlist_list=list_choosable_for_prompt(),
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

                # Select anchor: explicit name > desk preferred > random
                anchor = get_anchor(
                    name=parsed.get("anchor_name"),
                    desk=state["desk"] if not parsed.get("anchor_name") else None,
                )
                state["anchor_name"] = anchor.name
                state["anchor_avatar_id"] = anchor.get_avatar_id(parsed.get("avatar_look"))
                state["anchor_voice_id"] = anchor.voice_id
                state["extra_playlist_keys"] = parsed.get("extra_playlists") or []
                state["playlist_ids"] = resolve_playlist_ids(
                    state["desk"], anchor.name, workflow, state["topic"]
                )
                raw_dur = parsed.get("target_duration_seconds")
                state["target_duration_seconds"] = int(raw_dur) if raw_dur else None
                logger.info(
                    f"[EP] Workflow: {workflow} | Desk: {state['desk_name']} | "
                    f"Anchor: {anchor.name} | Look: {parsed.get('avatar_look', 'default')} | "
                    f"Duration: {state['target_duration_seconds']}s | Topic: {state['topic']}"
                )
            else:
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
                state["anchor_voice_id"] = anchor.voice_id
                state["extra_playlist_keys"] = []
                state["playlist_ids"] = resolve_playlist_ids(
                    "national", anchor.name, "ARTICLE", state["topic"]
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
            state["anchor_voice_id"] = anchor.voice_id
            state["extra_playlist_keys"] = []
            state["playlist_ids"] = []
            state["error"] = str(e)
        return state

    async def _execute_step_node(self, state: ProductionState) -> ProductionState:
        """Execute the current step in the workflow."""
        idx = state.get("current_step_index", 0)
        steps = state["steps"]

        if idx >= len(steps):
            return state

        agent_name = steps[idx]
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

            if prior_outputs:
                context_block = "\n\n".join(
                    f"=== {name.upper()} OUTPUT ===\n{text}"
                    for name, text in prior_outputs.items()
                )
                step_input = (
                    f"TOPIC: {state['topic']}\n\n"
                    f"ORIGINAL REQUEST: {state['request']}\n\n"
                    f"{context_block}\n\n"
                    f"Now perform your role for this story."
                )
            else:
                step_input = (
                    f"TOPIC: {state['topic']}\n\n"
                    f"REQUEST: {state['request']}\n\n"
                    f"Begin your work."
                )

            # Inject desk + anchor context for script_writer and anchor steps
            if agent_name == "script_writer" and anchor_name:
                desk_name = state.get("desk_name", "")
                desk_style = state.get("desk_prompt_style", "")
                step_input += (
                    f"\n\nDESK: {desk_name}\n"
                    f"DESK STYLE: {desk_style}\n"
                    f"ANCHOR: {anchor_name}\n"
                    f"Write the script for {anchor_name} to read. "
                    f"Use their name in the sign-off line instead of [ANCHOR]."
                )
            elif agent_name == "anchor" and anchor_avatar_id:
                background_asset_id = state.get("desk_background_asset_id", "")
                step_input += (
                    f"\n\nANCHOR NAME: {anchor_name}\n"
                    f"AVATAR ID: {anchor_avatar_id}\n"
                    f"VOICE ID: {anchor_voice_id}\n"
                    f"BACKGROUND ASSET ID: {background_asset_id}\n"
                    f"TOPIC: {state.get('topic', '')}\n"
                )
            elif agent_name == "publisher":
                import json as _json
                auto_ids = resolve_playlist_ids(
                    state.get("desk", ""),
                    state.get("anchor_name", ""),
                    state.get("workflow", ""),
                    state.get("topic", ""),
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

            result = await agent.process_message(step_input)
            outputs = dict(state.get("outputs", {}))
            anchor_output = result.get("response", "")
            outputs[agent_name] = anchor_output
            state["outputs"] = outputs

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
        return state

    def _route_after_step(self, state: ProductionState) -> str:
        idx = state.get("current_step_index", 0)
        if idx >= len(state["steps"]):
            return "done"
        # anchor_failed flag is set by _execute_step_node when HeyGen returns no video_id
        if state.get("anchor_failed"):
            return "done"
        return "next_step"

    async def _summarise_node(self, state: ProductionState) -> ProductionState:
        """Build the final production summary returned to Jarvis and save it to disk."""
        from datetime import datetime, timezone

        outputs = state.get("outputs", {})
        anchor_name = state.get("anchor_name", "")
        desk_name = state.get("desk_name", "")
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

        lines = [
            f"**Production Complete — {state['workflow']}**",
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

        if state.get("error"):
            lines.append(f"⚠️ One or more steps encountered an error: {state['error']}")

        state["final_summary"] = "\n".join(lines)

        # Save full production log (all outputs untruncated)
        try:
            log_dir = Path(settings.LOGS_DIR)
            log_dir.mkdir(parents=True, exist_ok=True)
            ts_file = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_path = log_dir / f"production_{ts_file}.md"

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

        return state

    # ------------------------------------------------------------------ #
    #  Public interface                                                    #
    # ------------------------------------------------------------------ #

    async def process_message(self, message: str, context: dict = None) -> dict:
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
                "anchor_voice_id": "",
                "playlist_ids": [],
                "extra_playlist_keys": [],
                "outputs": {},
                "current_step_index": 0,
                "anchor_failed": False,
                "error": None,
                "final_summary": "",
            }
            final_state = await self.workflow.ainvoke(initial_state)
            return {
                "success": True,
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
