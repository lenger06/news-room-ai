import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import re
import logging
from datetime import date
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from agents.editor.prompts import EDITOR_PROMPT
from tools.web_research_tool import web_research_tool
from tools.file_operations_tool import file_operations_tool
from config.settings import settings

logger = logging.getLogger(__name__)

# Titles that commonly appear with "former" incorrectly
_TITLE_PATTERN = re.compile(
    r'former\s+'
    r'(?:(?:U\.S\.?|United\s+States|British|French|German|Russian|Chinese|Canadian|Australian|Japanese|Israeli|Iranian|Saudi)\s+)?'
    r'(?:President|Prime\s+Minister|Vice\s+President|Secretary(?:\s+of\s+State)?|'
    r'Secretary[-\s]General|Director(?:\s+General)?|Chancellor|Minister|Senator|'
    r'Governor|Speaker|CEO|Chairman|Chair(?:woman|man|person)?|Ambassador|'
    r'General|Admiral|Commissioner)'
    r'(?:\s+[A-Z][a-zA-Z\'-]+){1,4}',
    re.IGNORECASE,
)


def _extract_article_text(message: str) -> str:
    """Pull article text from the pipeline context block."""
    for marker in ("EDITOR OUTPUT", "WRITER OUTPUT"):
        m = re.search(
            rf'=== {re.escape(marker)} ===\s*(.*?)(?:===|\Z)',
            message, re.DOTALL | re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
    return message


def _find_former_references(text: str) -> list[str]:
    """Return deduplicated list of 'former [title] [Name]' strings found in text."""
    return list(dict.fromkeys(m.group(0) for m in _TITLE_PATTERN.finditer(text)))


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
        self.executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True, max_iterations=15)
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
            today = date.today().strftime("%B %d, %Y")

            # Deterministic scan: find every "former [title] [Name]" in the article
            article_text = _extract_article_text(message)
            former_refs = _find_former_references(article_text)

            if former_refs:
                ref_list = "\n".join(f"  • {r}" for r in former_refs)
                preamble = (
                    f"TODAY'S DATE: {today}\n\n"
                    f"🚨 MANDATORY FIRST TASK — DO NOT SKIP:\n"
                    f"The following 'former [title]' phrases were found in the article. "
                    f"Each one MUST be verified with web_research_tool before you do anything else. "
                    f"Search for \"[person name] current title {date.today().year}\" for each. "
                    f"If the person currently holds that office, remove 'former' and use their correct current title — "
                    f"this is a critical on-air error.\n\n"
                    f"VERIFY EACH OF THESE:\n{ref_list}\n\n"
                    f"Only after completing these verifications should you address other corrections.\n\n"
                )
                logger.info(f"[Editor] Flagged {len(former_refs)} 'former' reference(s) for mandatory verification: {former_refs}")
            else:
                preamble = f"TODAY'S DATE: {today}\n\n"

            augmented_input = preamble + message
            result = self.executor.invoke({"input": augmented_input, "chat_history": []})
            return {"success": True, "response": result.get("output", ""), "agent": "editor"}

        except Exception as e:
            logger.error(f"Editor error: {e}", exc_info=True)
            return {"success": False, "response": f"Editing failed: {str(e)}", "agent": "editor"}
