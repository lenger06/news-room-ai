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
from agents.editor.prompts import EDITOR_PROMPT
from tools.web_research_tool import web_research_tool
from tools.file_operations_tool import file_operations_tool
from config.settings import settings

logger = logging.getLogger(__name__)


class Agent(BaseAgent):
    """Editor — applies fact-check corrections and verifies current titles before script production."""

    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.1, openai_api_key=settings.OPENAI_API_KEY)
        self.tools = [web_research_tool, file_operations_tool]
        prompt = ChatPromptTemplate.from_messages([
            ("system", EDITOR_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_openai_functions_agent(self.llm, self.tools, prompt)
        self.executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True, max_iterations=12)
        logger.info("Editor agent initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="editor",
            display_name="Editor",
            description="Applies fact-check corrections and verifies current titles/status before script production",
            version="1.0.0",
            module_path="agents.editor.agent",
            parent_agent="executive_producer",
        )

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            result = self.executor.invoke({"input": message, "chat_history": []})
            return {"success": True, "response": result.get("output", ""), "agent": "editor"}
        except Exception as e:
            logger.error(f"Editor error: {e}", exc_info=True)
            return {"success": False, "response": f"Editing failed: {str(e)}", "agent": "editor"}
