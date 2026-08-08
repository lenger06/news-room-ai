import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from agents.producer.prompts import PRODUCER_PROMPT
from config.settings import settings
import logging

logger = logging.getLogger(__name__)


class Agent(BaseAgent):
    """Producer — final production step: summarizes the run from context already provided."""

    def __init__(self):
        self.llm = ChatOpenAI(model=settings.model_for("producer"), temperature=0.1, openai_api_key=settings.OPENAI_API_KEY)
        # Plain LLM call, not a tool-calling AgentExecutor: Producer genuinely needs no
        # tools (every path it reports is already in the Writer/Script Writer outputs
        # in its context — see agents/producer/prompts.py). Confirmed live 2026-08-08:
        # create_openai_functions_agent with an empty tools list still sends an empty
        # `functions` array to the OpenAI API, which rejects it outright
        # (invalid_request_error: "Invalid 'functions': empty array") — broke every
        # single run. A direct chat call has no `functions` parameter to omit or empty
        # out, so this class of bug isn't reachable here.
        logger.info("Producer agent initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="producer",
            display_name="Producer",
            description="Handles final production — saves files and prepares for YouTube upload",
            version="1.0.0",
            module_path="agents.producer.agent",
            parent_agent="executive_producer",
        )

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            response = self.llm.invoke([
                SystemMessage(content=PRODUCER_PROMPT),
                HumanMessage(content=message),
            ])
            return {"success": True, "response": response.content, "agent": "producer"}
        except Exception as e:
            logger.error(f"Producer error: {e}", exc_info=True)
            return {"success": False, "response": f"Production failed: {str(e)}", "agent": "producer"}
