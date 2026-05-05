import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from agents.script_writer.prompts import SCRIPT_WRITER_PROMPT
from tools.file_operations_tool import file_operations_tool
from config.settings import settings
import logging

logger = logging.getLogger(__name__)


class Agent(BaseAgent):
    """Script Writer — converts a news article into a broadcast anchor script."""

    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.3, openai_api_key=settings.OPENAI_API_KEY)
        self.tools = [file_operations_tool]
        prompt = ChatPromptTemplate.from_messages([
            ("system", SCRIPT_WRITER_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_openai_functions_agent(self.llm, self.tools, prompt)
        self.executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True, max_iterations=8)
        logger.info("Script Writer agent initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="script_writer",
            display_name="Script Writer",
            description="Converts articles into broadcast-ready news anchor scripts",
            version="1.0.0",
            module_path="agents.script_writer.agent",
            parent_agent="executive_producer",
        )

    async def process_message(self, message: str, context: dict = None) -> dict:
        import re as _re
        from pathlib import Path as _Path
        try:
            result = self.executor.invoke({"input": message, "chat_history": []})
            output = result.get("output", "")

            # Append the actual script text so downstream agents (anchor) can extract it
            # without re-generating from context.
            script_text = self._read_saved_script(output)
            if script_text:
                output = f"{output}\n\n=== SCRIPT ===\n{script_text}"

            return {"success": True, "response": output, "agent": "script_writer"}
        except Exception as e:
            logger.error(f"Script Writer error: {e}", exc_info=True)
            return {"success": False, "response": f"Script writing failed: {str(e)}", "agent": "script_writer"}

    def _read_saved_script(self, output: str) -> str | None:
        import re as _re
        from pathlib import Path as _Path

        scripts_dir = _Path(settings.SCRIPTS_DIR)
        if not scripts_dir.exists():
            return None

        # Try several patterns the LLM uses to mention the filename
        for pattern in (
            r'output[/\\]scripts[/\\]([\w\-\.]+\.md)',     # full path
            r'filename\s+[`\'"]?([\w\-]+\.md)[`\'"]?',     # "filename `xxx.md`"
            r'`([\w\-]+\.md)`',                             # backtick-quoted name
            r'"([\w\-]+\.md)"',                             # double-quoted name
        ):
            match = _re.search(pattern, output, _re.IGNORECASE)
            if match:
                path = scripts_dir / match.group(1)
                if path.exists():
                    try:
                        content = path.read_text(encoding="utf-8").strip()
                        logger.info(f"[script_writer] Appending script from {path.name}")
                        return content
                    except Exception as e:
                        logger.warning(f"[script_writer] Could not read {path}: {e}")

        # Last resort: most recently modified .md in the scripts directory
        try:
            files = sorted(scripts_dir.glob("*.md"), key=lambda p: p.stat().st_mtime, reverse=True)
            if files:
                content = files[0].read_text(encoding="utf-8").strip()
                logger.info(f"[script_writer] Appending most recent script: {files[0].name}")
                return content
        except Exception as e:
            logger.warning(f"[script_writer] Could not read latest script: {e}")

        return None
