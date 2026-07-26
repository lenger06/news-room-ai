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
    download_video, extract_graphic_cues, save_video_package, assemble_final_video,
    compose_foreground_layers, extract_graphic_cues_with_position, render_graphic_overlays,
)
from tools.file_operations_tool import file_operations_tool
from config.settings import settings
from config.overlays import get_foreground_layers

logger = logging.getLogger(__name__)


class Agent(BaseAgent):
    """Video Editor — downloads the anchor video, extracts graphic cues, builds the video package."""

    def __init__(self):
        self.llm = ChatOpenAI(model=settings.model_for("video_editor"), temperature=0.1, openai_api_key=settings.OPENAI_API_KEY)
        self.tools = [download_video, extract_graphic_cues, save_video_package, file_operations_tool]
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

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            result = self.executor.invoke({"input": message, "chat_history": []})
            response_text = result.get("output", "")

            pkg_path = Path(settings.MEDIA_DIR) / "video_package.json"
            if pkg_path.exists():
                try:
                    pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                    broadcast_path = Path(pkg.get("video_file", ""))

                    if broadcast_path.exists():
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

                        pkg_path.write_text(json.dumps(pkg, indent=2), encoding="utf-8")
                except Exception as e:
                    logger.warning(f"[video_editor] Post-processing failed: {e}")

            return {"success": True, "response": response_text, "agent": "video_editor"}
        except Exception as e:
            logger.error(f"Video Editor error: {e}", exc_info=True)
            return {"success": False, "response": f"Video editing failed: {str(e)}", "agent": "video_editor"}
