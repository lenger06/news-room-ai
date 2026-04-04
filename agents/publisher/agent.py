import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import logging
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from agents.publisher.prompts import PUBLISHER_PROMPT
from tools.youtube_tool import youtube_upload_video, youtube_set_thumbnail
from tools.file_operations_tool import file_operations_tool
from config.settings import settings

logger = logging.getLogger(__name__)


class Agent(BaseAgent):
    """Publisher — uploads the finished video to YouTube and sets metadata."""

    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.1, openai_api_key=settings.OPENAI_API_KEY)
        self.tools = [youtube_upload_video, youtube_set_thumbnail, file_operations_tool]
        prompt = ChatPromptTemplate.from_messages([
            ("system", PUBLISHER_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_openai_functions_agent(self.llm, self.tools, prompt)
        self.executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True, max_iterations=10)
        logger.info("Publisher agent initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="publisher",
            display_name="Publisher",
            description="Uploads finished video to YouTube and sets title, description, thumbnail",
            version="1.0.0",
            module_path="agents.publisher.agent",
            parent_agent="executive_producer",
        )

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            result = self.executor.invoke({"input": message, "chat_history": []})
            return {"success": True, "response": result.get("output", ""), "agent": "publisher"}
        except Exception as e:
            logger.error(f"Publisher error: {e}", exc_info=True)
            return {"success": False, "response": f"Publishing failed: {str(e)}", "agent": "publisher"}
