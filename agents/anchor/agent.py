import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import json
import re
import logging
import requests
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from agents.anchor.prompts import ANCHOR_PROMPT
from tools.heygen_tool import generate_anchor_video, list_heygen_avatars, list_heygen_voices
from tools.file_operations_tool import file_operations_tool
from config.settings import settings

logger = logging.getLogger(__name__)

HEYGEN_BASE_URL = "https://api.heygen.com"
POLL_INTERVAL_SECONDS = 30
MAX_POLL_ATTEMPTS = 20  # 10 minutes max


class Agent(BaseAgent):
    """Anchor — cleans the script, submits to HeyGen, then polls natively until complete."""

    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.1, openai_api_key=settings.OPENAI_API_KEY)
        # Only give the LLM the submit tool — no polling tool
        self.tools = [generate_anchor_video, list_heygen_avatars, list_heygen_voices, file_operations_tool]
        prompt = ChatPromptTemplate.from_messages([
            ("system", ANCHOR_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_openai_functions_agent(self.llm, self.tools, prompt)
        self.executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True, max_iterations=5)
        logger.info("Anchor agent initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="anchor",
            display_name="Anchor",
            description="Generates AI news anchor video from broadcast script using HeyGen",
            version="1.0.0",
            module_path="agents.anchor.agent",
            parent_agent="executive_producer",
        )

    def _check_status_sync(self, video_id: str) -> dict:
        """Synchronous HeyGen status check — runs in executor thread."""
        try:
            response = requests.get(
                f"{HEYGEN_BASE_URL}/v1/video_status.get",
                headers={"x-api-key": settings.HEYGEN_API_KEY},
                params={"video_id": video_id},
                timeout=15,
            )
            if not response.ok:
                return {"error": f"HTTP {response.status_code}: {response.text[:200]}"}
            data = response.json().get("data", {})
            return {
                "video_id": video_id,
                "status": data.get("status", "unknown"),
                "video_url": data.get("video_url"),
                "thumbnail_url": data.get("thumbnail_url"),
            }
        except Exception as e:
            logger.error(f"[heygen] status check error: {e}", exc_info=True)
            return {"error": str(e)}

    async def _poll_until_complete(self, video_id: str) -> dict:
        """Poll HeyGen every POLL_INTERVAL_SECONDS until the video is ready or we give up."""
        for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
            logger.info(f"[heygen] Polling attempt {attempt}/{MAX_POLL_ATTEMPTS} for video {video_id}")
            result = await asyncio.to_thread(self._check_status_sync, video_id)

            status = result.get("status", "unknown")
            logger.info(f"[heygen] Video {video_id} status: {status}")

            if status == "completed":
                return result
            if status == "failed":
                return {"error": f"HeyGen video generation failed for {video_id}", "video_id": video_id}
            if "error" in result:
                return result

            # Still processing — wait before next check
            if attempt < MAX_POLL_ATTEMPTS:
                logger.info(f"[heygen] Status is '{status}', waiting {POLL_INTERVAL_SECONDS}s before next check...")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

        return {"error": f"Timed out waiting for video {video_id} after {MAX_POLL_ATTEMPTS} attempts"}

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            # Step 1: LLM cleans the script and calls generate_anchor_video
            result = await asyncio.to_thread(
                self.executor.invoke,
                {"input": message, "chat_history": []}
            )
            llm_output = result.get("output", "")

            # Step 2: Extract video_id from the LLM output
            video_id = self._extract_video_id(llm_output)
            if not video_id:
                logger.error(f"[anchor] Could not extract video_id from LLM output: {llm_output[:300]}")
                return {
                    "success": False,
                    "response": f"Anchor failed: could not extract video_id. LLM said: {llm_output}",
                    "agent": "anchor",
                }

            logger.info(f"[anchor] Video submitted. video_id={video_id}. Starting polling...")

            # Step 3: Native Python polling — no LLM involved
            poll_result = await self._poll_until_complete(video_id)

            if "error" in poll_result:
                return {
                    "success": False,
                    "response": f"Anchor video polling failed: {poll_result['error']}",
                    "agent": "anchor",
                    "video_id": video_id,
                }

            video_url = poll_result.get("video_url", "")
            thumbnail_url = poll_result.get("thumbnail_url", "")
            logger.info(f"[anchor] Video ready: {video_url}")

            return {
                "success": True,
                "response": (
                    f"Anchor video generation complete.\n"
                    f"video_id: {video_id}\n"
                    f"video_url: {video_url}\n"
                    f"thumbnail_url: {thumbnail_url}"
                ),
                "agent": "anchor",
                "video_id": video_id,
                "video_url": video_url,
                "thumbnail_url": thumbnail_url,
            }

        except Exception as e:
            logger.error(f"Anchor error: {e}", exc_info=True)
            return {"success": False, "response": f"Anchor video generation failed: {str(e)}", "agent": "anchor"}

    def _extract_video_id(self, text: str) -> str | None:
        """Extract video_id from LLM output — tries JSON first, then regex."""
        # Try parsing embedded JSON
        json_matches = re.findall(r'\{[^{}]*"video_id"[^{}]*\}', text)
        for m in json_matches:
            try:
                data = json.loads(m)
                vid = data.get("video_id")
                if vid:
                    return vid
            except Exception:
                pass

        # Fallback: bare video_id pattern (alphanumeric + hyphens/underscores, 8+ chars)
        match = re.search(r'video_id["\s:]+([a-zA-Z0-9_\-]{8,})', text)
        if match:
            return match.group(1)

        return None
