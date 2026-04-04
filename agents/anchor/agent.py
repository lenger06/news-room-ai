import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import re
import time
import logging
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from agents.anchor.prompts import ANCHOR_PROMPT
from tools.heygen_tool import generate_anchor_video, check_video_status, list_heygen_avatars, list_heygen_voices
from tools.file_operations_tool import file_operations_tool
from config.settings import settings
import logging

logger = logging.getLogger(__name__)


class Agent(BaseAgent):
    """Anchor — submits the broadcast script to HeyGen and retrieves the generated video."""

    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.1, openai_api_key=settings.OPENAI_API_KEY)
        self.tools = [generate_anchor_video, check_video_status, list_heygen_avatars, list_heygen_voices, file_operations_tool]
        prompt = ChatPromptTemplate.from_messages([
            ("system", ANCHOR_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_openai_functions_agent(self.llm, self.tools, prompt)
        self.executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True, max_iterations=25)
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

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            result = self.executor.invoke({"input": message, "chat_history": []})
            return {"success": True, "response": result.get("output", ""), "agent": "anchor"}
        except Exception as e:
            logger.error(f"Anchor error: {e}", exc_info=True)
            return {"success": False, "response": f"Anchor video generation failed: {str(e)}", "agent": "anchor"}
