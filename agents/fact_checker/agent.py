import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import re
import logging
import requests
from datetime import date
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from agents.fact_checker.prompts import FACT_CHECKER_PROMPT
from tools.web_research_tool import web_research_tool
from config.settings import settings

logger = logging.getLogger(__name__)

# Named political figures: "[Title] [Name]" or "[Country] [Title] [Name]"
_OFFICIAL_RE = re.compile(
    r'(?:(?:U\.S\.?|United\s+States|British|French|German|Russian|Chinese|'
    r'Canadian|Australian|Japanese|Israeli|Iranian|Saudi|South\s+Korean|'
    r'Indian|Brazilian|Mexican|Italian|Spanish)\s+)?'
    r'(?:former\s+)?'
    r'(?:President|Prime\s+Minister|Vice[\s-]President|Secretary(?:\s+of\s+State)?|'
    r'Secretary[-\s]General|Director(?:\s+General)?|Chancellor|Minister\s+of\s+\w+|'
    r'Senator|Governor|Speaker|CEO|Chairman|Chair(?:woman|man|person)?|Ambassador)'
    r'\s+(?:[A-Z][a-zA-Z\'-]+\s+){0,2}[A-Z][a-zA-Z\'-]+',
    re.IGNORECASE,
)

# Direct quoted statements — a misattributed or fabricated quote is a broadcast-level
# error independent of whether the surrounding facts check out.
_QUOTE_RE = re.compile(r'"([^"\n]{15,280})"')

# Statistics / casualty figures — a number paired with a unit or outcome keyword that
# makes it an independently checkable claim (e.g. "12 percent", "3 million displaced").
# Units are usually adjacent to the number ("12 percent"); casualty outcomes are usually
# a few words later ("40 people were killed"), so that pattern allows a short gap.
_STAT_UNIT_RE = re.compile(
    r'\b\d[\d,]*(?:\.\d+)?\s?(?:percent|%|million|billion|thousand)\b',
    re.IGNORECASE,
)
_STAT_CASUALTY_RE = re.compile(
    r'\b\d[\d,]*(?:\.\d+)?\b(?:\s+\S+){0,3}?\s+'
    r'(?:dead|deaths|killed|injured|wounded|casualties|hospitalized|displaced)\b',
    re.IGNORECASE,
)


def _sentence_containing(text: str, start: int, end: int) -> str:
    """Return the full sentence around a regex match span — a much more useful Tavily
    query than the bare matched substring (e.g. the whole "40 people were killed when..."
    sentence, not just the fragment "40 killed")."""
    left = max(text.rfind('.', 0, start), text.rfind('!', 0, start), text.rfind('?', 0, start))
    left = left + 1 if left != -1 else 0
    right_candidates = [i for i in (text.find('.', end), text.find('!', end), text.find('?', end)) if i != -1]
    right = min(right_candidates) + 1 if right_candidates else len(text)
    return text[left:right].strip()


def _extract_quotes(text: str) -> list[str]:
    return list(dict.fromkeys(m.group(1).strip() for m in _QUOTE_RE.finditer(text) if m.group(1).strip()))


def _extract_statistic_sentences(text: str) -> list[str]:
    matches = list(_STAT_UNIT_RE.finditer(text)) + list(_STAT_CASUALTY_RE.finditer(text))
    sentences = (_sentence_containing(text, m.start(), m.end()) for m in matches)
    return list(dict.fromkeys(s for s in sentences if s))


def _tavily_search(query: str) -> str:
    """Direct Tavily search — deterministic Python call, not via LLM tool."""
    if not settings.TAVILY_API_KEY:
        return "TAVILY_API_KEY not configured"
    try:
        resp = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": settings.TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "include_answer": True,
                "max_results": 3,
                "topic": "news",
            },
            timeout=15,
        )
        if not resp.ok:
            return f"Search failed: HTTP {resp.status_code}"
        data = resp.json()
        lines = []
        if data.get("answer"):
            lines.append(f"Summary: {data['answer']}")
        for r in data.get("results", [])[:3]:
            snippet = r.get("content", "")[:200].replace("\n", " ")
            lines.append(f"• {r.get('title', '')}: {snippet}")
        return "\n".join(lines) if lines else "No results"
    except Exception as e:
        return f"Search error: {e}"


def _extract_article_text(message: str) -> str:
    for marker in ("WRITER OUTPUT",):
        m = re.search(
            rf'=== {re.escape(marker)} ===\s*(.*?)(?:===|\Z)',
            message, re.DOTALL | re.IGNORECASE,
        )
        if m:
            return m.group(1).strip()
    return message


class Agent(BaseAgent):
    """Fact Checker — runs Tavily title verification before LLM review of the article."""

    def __init__(self):
        self.llm = ChatOpenAI(model=settings.model_for("fact_checker"), temperature=0.0, openai_api_key=settings.OPENAI_API_KEY)
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
            version="2.0.0",
            module_path="agents.fact_checker.agent",
            parent_agent="executive_producer",
        )

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            today = date.today().strftime("%B %d, %Y")
            year = date.today().year

            # ── Pre-run Tavily searches: named officials, direct quotes, and statistics ──
            # Deterministic Python-level searches, not left to the LLM's own discretion —
            # this guarantees a baseline of independent corroboration for the claim types
            # most likely to contain a broadcast-level error, on top of whatever additional
            # web_research_tool calls the LLM makes for other claims.
            article_text = _extract_article_text(message)
            officials = list(dict.fromkeys(
                m.group(0) for m in _OFFICIAL_RE.finditer(article_text)
            ))
            quotes = _extract_quotes(article_text)
            stat_sentences = _extract_statistic_sentences(article_text)

            preamble_lines = [f"TODAY'S DATE: {today}\n"]

            if officials:
                logger.info(f"[Fact Checker] Pre-running Tavily title checks for {len(officials)} official(s)")
                preamble_lines.append(
                    "PRE-RUN TAVILY TITLE VERIFICATION:\n"
                    "The following named officials appear in the article. "
                    "Live Tavily searches were run for each to verify their current title. "
                    "Use these results in your VERIFIED / CORRECTIONS NEEDED sections.\n"
                )
                for ref in officials[:8]:  # cap at 8 to keep input manageable
                    query = f"{ref} current role title {year}"
                    logger.info(f"[Fact Checker] Tavily: {query!r}")
                    result = _tavily_search(query)
                    preamble_lines.append(f"ARTICLE SAYS: \"{ref}\"")
                    preamble_lines.append(f"TAVILY RESULT: {result}")
                    preamble_lines.append("")

            if quotes:
                logger.info(f"[Fact Checker] Pre-running Tavily quote checks for {len(quotes)} quote(s)")
                preamble_lines.append(
                    "PRE-RUN TAVILY QUOTE VERIFICATION:\n"
                    "The following direct quotes appear in the article. Live Tavily searches "
                    "were run for each to check whether they are accurately reported. A quote "
                    "with no corroborating result is not automatically false — treat it as "
                    "unverified rather than assuming fabrication.\n"
                )
                for q in quotes[:5]:  # cap at 5 to keep input manageable
                    query = f"\"{q[:120]}\""
                    logger.info(f"[Fact Checker] Tavily: {query!r}")
                    result = _tavily_search(query)
                    preamble_lines.append(f"ARTICLE QUOTES: \"{q}\"")
                    preamble_lines.append(f"TAVILY RESULT: {result}")
                    preamble_lines.append("")

            if stat_sentences:
                logger.info(f"[Fact Checker] Pre-running Tavily checks for {len(stat_sentences)} statistic(s)")
                preamble_lines.append(
                    "PRE-RUN TAVILY STATISTIC VERIFICATION:\n"
                    "The following statements contain a specific figure. Live Tavily searches "
                    "were run for each to check the number against current reporting.\n"
                )
                for sentence in stat_sentences[:6]:  # cap at 6 to keep input manageable
                    query = sentence[:150]
                    logger.info(f"[Fact Checker] Tavily: {query!r}")
                    result = _tavily_search(query)
                    preamble_lines.append(f"ARTICLE SAYS: \"{sentence}\"")
                    preamble_lines.append(f"TAVILY RESULT: {result}")
                    preamble_lines.append("")

            if officials or quotes or stat_sentences:
                preamble_lines.append(
                    "Now fact-check the full article using the Tavily results above "
                    "plus any additional web_research_tool calls you need.\n"
                )

            augmented_input = "\n".join(preamble_lines) + "\n" + message
            result = self.executor.invoke({"input": augmented_input, "chat_history": []})
            output = result.get("output", "")

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
