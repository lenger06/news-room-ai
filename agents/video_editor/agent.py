import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import json
import logging
import re
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from agents.video_editor.prompts import VIDEO_EDITOR_PROMPT
from tools.video_tools import (
    assemble_final_video, compose_foreground_layers, extract_graphic_cues_with_position,
    render_graphic_overlays, check_visual_qa, _download_video_impl,
)
from tools.file_operations_tool import file_operations_tool
from config.settings import settings
from config.overlays import get_foreground_layers

logger = logging.getLogger(__name__)


class Agent(BaseAgent):
    """Video Editor — downloads the anchor video, extracts graphic cues, builds the video package."""

    def __init__(self):
        self.llm = ChatOpenAI(model=settings.model_for("video_editor"), temperature=0.1, openai_api_key=settings.OPENAI_API_KEY)
        # download_video/save_video_package intentionally NOT included: the video download
        # and video_package.json write are both fully deterministic now (see the 2026-08-02
        # per-run MEDIA_DIR fix below) — giving the LLM its own copies of these tools meant
        # it independently re-downloaded the video and re-saved the package to the OLD
        # hardcoded ./output/media/ path from its prompt, which no longer matches the
        # per-run directory the deterministic code uses. Confirmed live 2026-08-07: this
        # silently discarded the LLM's own title/description/tags (written to the wrong,
        # now-unread path) so YouTube uploads fell back to a bland topic-string title.
        # extract_graphic_cues also dropped: the deterministic render_graphic_overlays path
        # (Step 0 below) extracts cues itself and never consulted the LLM's copy.
        self.tools = [file_operations_tool]
        prompt = ChatPromptTemplate.from_messages([
            ("system", VIDEO_EDITOR_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_openai_functions_agent(self.llm, self.tools, prompt)
        self.executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True, max_iterations=12)
        logger.info("Video Editor agent initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="video_editor",
            display_name="Video Editor",
            description="Downloads anchor video, extracts graphic cues, builds video package",
            version="1.0.0",
            module_path="agents.video_editor.agent",
            parent_agent="executive_producer",
        )

    @staticmethod
    def _parse_llm_metadata(text: str) -> dict:
        """Extract the suggested title/description/tags JSON block from the LLM's
        text response — it no longer writes anything to disk itself, so this is the
        only way its suggestions reach video_package.json."""
        for pattern in (r"```json\s*(\{.*?\})\s*```", r"```\s*(\{.*?\})\s*```", r"(\{[^{}]*\"title\"[^{}]*\})"):
            m = re.search(pattern, text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass
        return {}

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            # Deterministic extraction of the ANCHOR OUTPUT block, done in plain Python
            # BEFORE trusting the LLM tool-calling agent at all. This is the fix for a
            # real incident (2026-07-28): the LLM occasionally fails to notice a
            # video_url that IS present in its own context and reports it missing —
            # and the old code then silently fell back to whatever video_package.json
            # happened to already exist at the global settings.MEDIA_DIR path (from a
            # PREVIOUS, unrelated production), reprocessed that stale video, and
            # Publisher republished it under the wrong title. Never again: the video
            # this step operates on is always the one we deterministically download
            # ourselves, never inferred from ambient on-disk state.
            anchor_block_match = re.search(
                r'=== ANCHOR OUTPUT ===\s*(.*?)(?=\n\n===|\Z)', message, re.DOTALL | re.IGNORECASE,
            )
            anchor_block = anchor_block_match.group(1) if anchor_block_match else ""
            video_url_match = re.search(r'video_url:\s*(\S+)', anchor_block, re.IGNORECASE)
            thumb_match = re.search(r'thumbnail_url:\s*(\S*)', anchor_block, re.IGNORECASE)
            video_url = video_url_match.group(1).strip() if video_url_match else ""
            thumbnail_url = thumb_match.group(1).strip() if thumb_match else ""

            if not video_url:
                logger.error(
                    "[video_editor] No video_url found in ANCHOR OUTPUT — cannot proceed. "
                    "This halts the pipeline rather than falling back to any pre-existing "
                    "video_package.json, which could belong to an unrelated prior run."
                )
                return {
                    "success": False,
                    "response": (
                        "VIDEO EDITOR FAILED: no video_url found in the anchor's output. "
                        "The anchor step must be re-run — refusing to fall back to any "
                        "existing video_package.json, since it may belong to a different story."
                    ),
                    "agent": "video_editor",
                }

            # Per-run media directory (2026-08-02 incident): the EP passes this same
            # MEDIA_DIR value to publisher too, which parses and uses it directly. This
            # step used to ignore it entirely and always write to the global
            # settings.MEDIA_DIR instead — a single shared path across every show/run —
            # which meant publisher, looking in the correct per-run directory, would
            # find nothing there. Never write to the global path again; the per-run
            # directory is the single source of truth every step must agree on.
            media_dir_match = re.search(r'MEDIA_DIR:\s*(\S+)', message, re.IGNORECASE)
            media_dir = media_dir_match.group(1).strip() if media_dir_match else settings.MEDIA_DIR

            # Deterministically download the correct video for THIS run, with a fresh
            # timestamped filename — regardless of what the LLM tool-calling step below
            # does or doesn't manage to do.
            downloaded_path_str = _download_video_impl(video_url, directory=media_dir)
            if downloaded_path_str.startswith("Error"):
                logger.error(f"[video_editor] Deterministic download failed: {downloaded_path_str}")
                return {
                    "success": False,
                    "response": f"VIDEO EDITOR FAILED: could not download the anchor video — {downloaded_path_str}",
                    "agent": "video_editor",
                }
            broadcast_path = Path(downloaded_path_str)

            topic_m = re.search(r'TOPIC[:\s]+([^\n]+)', message, re.IGNORECASE)
            topic = topic_m.group(1).strip() if topic_m else ""

            # Run the LLM for the one thing it's still asked to do: suggest a YouTube
            # title/description/tags. Its output is text-only now, parsed below — never
            # a file it writes itself — so there's no path for it to get out of sync
            # with the deterministic per-run directory this step actually uses.
            try:
                result = self.executor.invoke({"input": message, "chat_history": []})
                response_text = result.get("output", "")
            except Exception as e:
                logger.warning(f"[video_editor] LLM metadata step failed (continuing with deterministic fallbacks): {e}")
                response_text = f"[LLM step failed: {e}]"

            llm_metadata = self._parse_llm_metadata(response_text)

            pkg_path = Path(media_dir) / "video_package.json"
            pkg: dict = {}
            if pkg_path.exists():
                try:
                    candidate = json.loads(pkg_path.read_text(encoding="utf-8"))
                    # Staleness check: only trust the LLM's title/description/tags if the
                    # package it wrote actually references the video we just downloaded
                    # for THIS run — otherwise it's leftover from a different production.
                    if candidate.get("video_url") == video_url:
                        pkg = candidate
                    else:
                        logger.warning(
                            "[video_editor] Existing video_package.json references a different "
                            "video_url than this run's anchor output — discarding its metadata "
                            "as stale rather than trusting it."
                        )
                except Exception as e:
                    logger.warning(f"[video_editor] Could not read existing video_package.json: {e}")

            pkg.setdefault("topic", topic)
            pkg.setdefault("title", llm_metadata.get("title") or topic or "News Segment")
            pkg.setdefault("description", llm_metadata.get("description") or topic or "")
            pkg.setdefault("tags", llm_metadata.get("tags") or [])
            pkg.setdefault("graphic_cues", [])
            # Always authoritative, regardless of the LLM's own tool call above.
            pkg["video_file"] = str(broadcast_path)
            pkg["video_url"] = video_url
            pkg["thumbnail_url"] = thumbnail_url

            try:
                # Step 0: Burn [GRAPHIC:] cues into the video as lower-third overlays.
                # Extracted directly from the script text in this message (not from the
                # LLM's own tool call above) so rendering doesn't depend on the LLM
                # having faithfully preserved cue text/order when building the package.
                script_match = re.search(
                    r'=== SCRIPT ===\s*(.*?)(?=\n\nDESK_SLUG:|\n\nMEDIA_DIR:|===|\Z)',
                    message, re.DOTALL | re.IGNORECASE,
                )
                if script_match:
                    cues = extract_graphic_cues_with_position(script_match.group(1))
                    if cues:
                        gfx_path = render_graphic_overlays(broadcast_path, cues)
                        if gfx_path:
                            broadcast_path = gfx_path
                            pkg["video_file"] = str(gfx_path)
                            pkg["graphic_overlays_applied"] = len(cues)
                            logger.info(f"[video_editor] Graphic overlays applied → {gfx_path.name}")
                            response_text += f"\nGraphic overlays applied ({len(cues)}) → {gfx_path.name}"

                # Step 1: Apply foreground layers (in front of avatar) if any
                desk_slug = ""
                m = re.search(r'DESK_SLUG[:\s]+([^\n]+)', message, re.IGNORECASE)
                if m:
                    desk_slug = m.group(1).strip()

                fg_layers = get_foreground_layers(desk_slug)
                if fg_layers:
                    fg_path = compose_foreground_layers(broadcast_path, fg_layers)
                    if fg_path:
                        broadcast_path = fg_path
                        pkg["video_file"] = str(fg_path)
                        pkg["foreground_layers_applied"] = True
                        logger.info(f"[video_editor] Foreground layers applied → {fg_path.name}")
                        response_text += f"\nForeground layers applied → {fg_path.name}"

                # Step 2: Assemble with promo/outro
                final_path = assemble_final_video(broadcast_path)
                if final_path:
                    pkg["video_file"] = str(final_path)
                    pkg["promo_prepended"] = True
                    logger.info(f"[video_editor] video_package.json updated → {final_path.name}")
                    response_text += f"\nFinal video assembled → {final_path.name}"
                    broadcast_path = final_path

                # Step 3: Visual QA (Phase 7.3, SELF_IMPROVEMENT_ROADMAP.md) — sample
                # frames from the final composited video and ask a vision LLM whether
                # anything looks wrong (watermark, chromakey fringe, mismatched
                # background). Never blocks publish — flags go to the human review
                # queue, same pattern as a fact-check/compliance hold.
                try:
                    qa_result = check_visual_qa(broadcast_path)
                    pkg["visual_qa"] = qa_result
                    if qa_result.get("flagged"):
                        from tools.review_queue import record as _review_record
                        _review_record(
                            topic=topic,
                            reason=f"Visual QA flag: {qa_result.get('notes', '')}",
                            stage="visual_qa",
                            output_dir=str(Path(media_dir).parent),
                            workflow="",
                        )
                        logger.warning(f"[video_editor] Visual QA flagged: {qa_result.get('notes', '')[:200]}")
                        response_text += f"\nVisual QA flagged for review: {qa_result.get('notes', '')[:200]}"
                    else:
                        logger.info(f"[video_editor] Visual QA clean: {qa_result.get('notes', '')[:120]}")
                except Exception as e:
                    logger.warning(f"[video_editor] Visual QA check failed: {e}")

                pkg_path.parent.mkdir(parents=True, exist_ok=True)
                pkg_path.write_text(json.dumps(pkg, indent=2), encoding="utf-8")
            except Exception as e:
                logger.warning(f"[video_editor] Post-processing failed: {e}")

            return {"success": True, "response": response_text, "agent": "video_editor"}
        except Exception as e:
            logger.error(f"Video Editor error: {e}", exc_info=True)
            return {"success": False, "response": f"Video editing failed: {str(e)}", "agent": "video_editor"}
