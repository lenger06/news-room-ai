import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import re
import logging
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from agents.compliance_checker.prompts import COMPLIANCE_CHECKER_PROMPT
from config.settings import settings

logger = logging.getLogger(__name__)


def _extract_script_text(message: str) -> str:
    """Pull the broadcast script text from the pipeline context block, if present."""
    for marker in ("SCRIPT", "SCRIPT_WRITER OUTPUT", "WRITER OUTPUT"):
        m = re.search(
            rf'=== {re.escape(marker)} ===\s*(.*?)(?:===|\Z)',
            message, re.DOTALL | re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
    return message


class Agent(BaseAgent):
    """Compliance Checker — screens the final broadcast script for YouTube policy risk before publish."""

    def __init__(self):
        self.llm = ChatOpenAI(
            model=settings.model_for("compliance_checker"),
            temperature=0.0,
            openai_api_key=settings.OPENAI_API_KEY,
        )
        logger.info("Compliance Checker agent initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="compliance_checker",
            display_name="Compliance Checker",
            description="Screens the final broadcast script for YouTube policy risk before publish",
            version="1.0.0",
            module_path="agents.compliance_checker.agent",
            parent_agent="executive_producer",
        )

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            script_text = _extract_script_text(message)
            response = await self.llm.ainvoke([
                SystemMessage(content=COMPLIANCE_CHECKER_PROMPT),
                HumanMessage(content=f"Review this broadcast script for policy risk:\n\n{script_text}"),
            ])
            output = response.content

            verdict = "UNKNOWN"
            for line in output.splitlines():
                line = line.strip()
                if "CLEAR TO PUBLISH" in line:
                    verdict = "CLEAR TO PUBLISH"
                    break
                elif "HOLD FOR REVIEW" in line:
                    verdict = "HOLD FOR REVIEW"
                    break

            logger.info(f"[Compliance Checker] Verdict: {verdict}")
            return {
                "success": True,
                "response": output,
                "verdict": verdict,
                "agent": "compliance_checker",
            }
        except Exception as e:
            logger.error(f"Compliance Checker error: {e}", exc_info=True)
            return {
                "success": False,
                "response": f"Compliance check failed: {str(e)}",
                "verdict": "ERROR",
                "agent": "compliance_checker",
            }
