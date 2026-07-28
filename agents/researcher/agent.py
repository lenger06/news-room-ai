import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import re
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from agents.researcher.prompts import RESEARCHER_PROMPT
from tools.web_research_tool import web_research_tool
from tools.image_search_tool import image_search_tool, _image_search_impl
from tools.video_search_tool import video_search_tool, _video_search_impl
from tools.file_operations_tool import file_operations_tool
from config.settings import settings
import logging

logger = logging.getLogger(__name__)

_IMAGES_HEADER = "## SOURCED B-ROLL IMAGES"
_VIDEOS_HEADER = "## SOURCED B-ROLL VIDEOS"


class Agent(BaseAgent):
    """Researcher — gathers and summarises source material for a story."""

    def __init__(self):
        self.llm = ChatOpenAI(model=settings.model_for("researcher"), temperature=0.1, openai_api_key=settings.OPENAI_API_KEY)
        self.tools = [web_research_tool, image_search_tool, video_search_tool, file_operations_tool]
        prompt = ChatPromptTemplate.from_messages([
            ("system", RESEARCHER_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_openai_functions_agent(self.llm, self.tools, prompt)
        self.executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True, max_iterations=10)
        logger.info("Researcher agent initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="researcher",
            display_name="Researcher",
            description="Researches topics using web search and compiles source material",
            version="1.0.0",
            module_path="agents.researcher.agent",
            parent_agent="executive_producer",
        )

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            result = self.executor.invoke({"input": message, "chat_history": []})
            response_text = result.get("output", "")

            # Deterministic b-roll backstop (2026-07-28 incident): log inspection showed
            # the LLM sometimes never calls image_search_tool/video_search_tool at all —
            # zero tool-call attempts logged, despite the prompt instructing it to call
            # them after the text research. Likely cause: after a long, thorough text
            # brief the model treats the trailing b-roll step as optional. Rather than
            # trust the LLM to remember every time, check its own output for real URLs
            # and, if either section is empty, search ourselves in plain Python.
            topic_m = re.search(r'TOPIC:\s*([^\n]+)', message, re.IGNORECASE)
            topic = topic_m.group(1).strip() if topic_m else ""
            if topic:
                response_text = self._ensure_broll(response_text, topic)

            return {"success": True, "response": response_text, "agent": "researcher"}
        except Exception as e:
            logger.error(f"Researcher error: {e}", exc_info=True)
            return {"success": False, "response": f"Research failed: {str(e)}", "agent": "researcher"}

    def _ensure_broll(self, response_text: str, topic: str) -> str:
        if not self._section_has_url(response_text, _IMAGES_HEADER):
            try:
                data = _image_search_impl(topic, num_results=3)
                images = data.get("images", [])
            except Exception as e:
                logger.warning(f"[researcher] Deterministic image backfill failed: {e}")
                images = []
            lines = [f"{img['url']} | {img.get('caption') or topic}" for img in images if img.get("url")]
            if lines:
                logger.info(f"[researcher] B-roll backstop: found {len(lines)} image(s) LLM missed for '{topic}'")
            response_text = self._replace_section(response_text, _IMAGES_HEADER, lines)

        if not self._section_has_url(response_text, _VIDEOS_HEADER):
            try:
                data = _video_search_impl(topic, num_results=2)
                videos = data.get("videos", [])
            except Exception as e:
                logger.warning(f"[researcher] Deterministic video backfill failed: {e}")
                videos = []
            lines = [
                f"{v['url']} | {v.get('description') or topic} | {v.get('duration_seconds', 0)}s"
                for v in videos if v.get("url")
            ]
            if lines:
                logger.info(f"[researcher] B-roll backstop: found {len(lines)} video(s) LLM missed for '{topic}'")
            response_text = self._replace_section(response_text, _VIDEOS_HEADER, lines)

        return response_text

    @staticmethod
    def _section_has_url(text: str, header: str) -> bool:
        idx = text.find(header)
        if idx == -1:
            return False
        section = text[idx + len(header):]
        next_header = re.search(r'\n##\s', section)
        if next_header:
            section = section[:next_header.start()]
        return "http" in section

    @staticmethod
    def _replace_section(text: str, header: str, lines: list) -> str:
        if not lines:
            # Deterministic search found nothing either — leave the LLM's output
            # (placeholder text or an omitted section) as-is rather than injecting
            # an empty header.
            return text
        body = "\n".join(lines)
        idx = text.find(header)
        if idx == -1:
            return text.rstrip() + f"\n\n{header}\n{body}\n"
        start = idx + len(header)
        rest = text[start:]
        next_header = re.search(r'\n##\s', rest)
        after = rest[next_header.start():] if next_header else ""
        return text[:start] + "\n" + body + "\n" + after
