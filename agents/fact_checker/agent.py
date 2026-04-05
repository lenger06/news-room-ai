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
from agents.fact_checker.prompts import FACT_CHECKER_PROMPT
from tools.web_research_tool import web_research_tool
from config.settings import settings

logger = logging.getLogger(__name__)


class Agent(BaseAgent):
    """Fact Checker — verifies key claims in the draft article before it reaches the script writer."""

    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.0, openai_api_key=settings.OPENAI_API_KEY)
        self.tools = [web_research_tool]
        prompt = ChatPromptTemplate.from_messages([
            ("system", FACT_CHECKER_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_openai_functions_agent(self.llm, self.tools, prompt)
        self.executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True, max_iterations=15)
        logger.info("Fact Checker agent initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="fact_checker",
            display_name="Fact Checker",
            description="Verifies factual claims in draft articles before script production",
            version="1.0.0",
            module_path="agents.fact_checker.agent",
            parent_agent="executive_producer",
        )

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            result = self.executor.invoke({"input": message, "chat_history": []})
            output = result.get("output", "")

            # Parse verdict so the EP can log it clearly
            verdict = "UNKNOWN"
            for line in output.splitlines():
                line = line.strip()
                if "CLEAR TO PUBLISH" in line:
                    verdict = "CLEAR TO PUBLISH"
                    break
                elif "PUBLISH WITH NOTES" in line:
                    verdict = "PUBLISH WITH NOTES"
                    break
                elif "HOLD FOR CORRECTIONS" in line:
                    verdict = "HOLD FOR CORRECTIONS"
                    break

            logger.info(f"[Fact Checker] Verdict: {verdict}")
            return {
                "success": True,
                "response": output,
                "verdict": verdict,
                "agent": "fact_checker",
            }
        except Exception as e:
            logger.error(f"Fact Checker error: {e}", exc_info=True)
            return {
                "success": False,
                "response": f"Fact checking failed: {str(e)}",
                "verdict": "ERROR",
                "agent": "fact_checker",
            }
