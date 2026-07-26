"""
Newsroom AI — FastAPI backend
Executive Producer orchestrates: researcher → writer → script_writer → producer
Runs on port 8091. Jarvis calls POST /produce to trigger a production run.
"""

import asyncio
import logging
import sys
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

import json
import re
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import Optional

# In-memory job store: job_id -> {status, result, error, workflow, topic}
_jobs: dict = {}

project_root = Path(__file__).parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from config.settings import settings

# ── Logging ───────────────────────────────────────────────────────────────────
Path("logs").mkdir(exist_ok=True)
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.FileHandler("logs/newsroom.log", encoding="utf-8"),
        logging.StreamHandler(
            stream=open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
        ),
    ],
)
logger = logging.getLogger(__name__)


# ── Event feed background poller ─────────────────────────────────────────────
async def _event_feed_loop():
    """Poll free event feeds (USGS earthquakes, NWS weather alerts — see
    tools/event_feeds.py) on an interval and evaluate any new candidate through
    the same qualifying-criteria/dedup gates as /webhook/ingest. Only runs when
    settings.EVENT_FEEDS_ENABLED is true."""
    from tools.event_feeds import fetch_all
    from agents.registry import agent_registry

    while True:
        try:
            await asyncio.sleep(settings.EVENT_FEED_POLL_SECONDS)
            candidates = await asyncio.to_thread(fetch_all)
            if not candidates:
                continue
            checker = await agent_registry.get_agent("breaking_news_checker")
            if not checker:
                logger.warning("[event_feeds] Breaking News Checker not available — skipping candidates")
                continue
            for c in candidates:
                logger.info(f"[event_feeds] Evaluating candidate: {c['headline']}")
                try:
                    result = await checker.process_webhook_event(
                        source=c["source"], headline=c["headline"],
                        detail=c.get("detail", ""), url=c.get("url", ""),
                        keywords=c.get("keywords"),
                    )
                    logger.info(f"[event_feeds] Result: {result.get('response', '')[:200]}")
                except Exception as e:
                    logger.error(f"[event_feeds] Candidate evaluation failed: {e}", exc_info=True)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[event_feeds] Poll loop error: {e}", exc_info=True)


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Newsroom AI starting up ===")
    settings.validate()

    from agents.registry import agent_registry
    for name in ["researcher", "writer", "fact_checker", "editor", "script_writer", "anchor", "video_editor", "compliance_checker", "producer", "publisher", "executive_producer", "breaking_news_checker"]:
        agent = await agent_registry.get_agent(name)
        logger.info(f"  {'✓' if agent else '✗'} {name}")

    # Ensure output directories exist
    for d in [settings.ARTICLES_DIR, settings.SCRIPTS_DIR, settings.MEDIA_DIR, settings.LOGS_DIR]:
        Path(d).mkdir(parents=True, exist_ok=True)

    event_feed_task = None
    if settings.EVENT_FEEDS_ENABLED:
        logger.info(f"=== Event feeds ENABLED — polling every {settings.EVENT_FEED_POLL_SECONDS}s ===")
        event_feed_task = asyncio.create_task(_event_feed_loop())
    else:
        logger.info("=== Event feeds disabled (set EVENT_FEEDS_ENABLED=true in .env to enable) ===")

    logger.info("=== Newsroom AI ready ===")
    yield
    if event_feed_task:
        event_feed_task.cancel()
    logger.info("=== Newsroom AI shutting down ===")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Newsroom AI",
    description="AI-powered newsroom: research, write, script, and produce news segments",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ────────────────────────────────────────────────────────────────────
def _extract_break_check(text: str) -> tuple[str, bool]:
    """Detect [BREAK-CHECK] tag — routes to breaking_news_checker instead of EP.
    Searches anywhere in the text to handle a leading [Current date/time: ...] prefix."""
    m = re.search(r"\[BREAK-CHECK\]", text, re.IGNORECASE)
    if m:
        cleaned = (text[:m.start()] + text[m.end():]).strip()
        return cleaned, True
    return text, False


def _extract_show_slug(text: str) -> tuple[str, Optional[str]]:
    """Extract [SHOW: slug] tag from message, return (cleaned_text, show_slug).
    Searches anywhere in the text to handle a leading [Current date/time: ...] prefix."""
    m = re.search(r"\[SHOW:\s*([\w-]+)\]", text)
    if m:
        cleaned = (text[:m.start()] + text[m.end():]).strip()
        return cleaned, m.group(1)
    return text, None


class ProductionRequest(BaseModel):
    request: str                          # Natural language production request
    client_datetime: Optional[str] = None
    show_slug: Optional[str] = None       # e.g. "morning-report", "evening-news" — None = auto-detect


class ProductionResponse(BaseModel):
    success: bool
    response: str
    workflow: Optional[str] = None
    topic: Optional[str] = None
    agent: str = "executive_producer"


class WebhookEventRequest(BaseModel):
    source: str                        # e.g. "usgs_earthquake", "nws_alert", "market_data", "rss_bridge"
    headline: str                      # short description of the event
    detail: Optional[str] = ""         # additional detail text, if any
    url: Optional[str] = ""            # source URL, if any
    keywords: Optional[list[str]] = None   # dedup keywords, if the feed adapter already knows good ones


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "name": "Newsroom AI",
        "version": "1.0.0",
        "status": "running",
        "workflows": ["RESEARCH_ONLY", "ARTICLE", "FULL_PRODUCTION", "BROADCAST_VIDEO", "SCRIPT_ONLY", "VIDEO_FROM_SCRIPT", "SPECIAL_REPORT"],
        "endpoints": {
            "produce": "POST /produce",
            "produce_stream": "POST /produce/stream",
            "webhook_ingest": "POST /webhook/ingest",
            "health": "GET /health",
            "docs": "GET /docs",
        },
    }


@app.get("/health")
async def health():
    from agents.registry import agent_registry
    return {
        "status": "healthy",
        "agents": {
            name: ("ready" if agent_registry.get_agent_info(name) else "missing")
            for name in ["executive_producer", "researcher", "writer", "fact_checker", "editor", "script_writer", "anchor", "video_editor", "compliance_checker", "producer", "publisher"]
        },
    }


@app.post("/produce", response_model=ProductionResponse)
async def produce(body: ProductionRequest):
    """Trigger a newsroom production run."""
    try:
        from agents.registry import agent_registry

        # Route breaking news checks to the dedicated checker agent
        message, is_break_check = _extract_break_check(body.request)
        if is_break_check:
            checker = await agent_registry.get_agent("breaking_news_checker")
            if not checker:
                raise HTTPException(status_code=503, detail="Breaking News Checker not available")
            result = await checker.process_message(message)
            return ProductionResponse(
                success=result.get("success", False),
                response=result.get("response", ""),
                agent="breaking_news_checker",
            )

        ep = await agent_registry.get_agent("executive_producer")
        if not ep:
            raise HTTPException(status_code=503, detail="Executive Producer not available")

        message, extracted_show = _extract_show_slug(message)
        show_slug = body.show_slug or extracted_show
        if body.client_datetime and "[Current date/time:" not in message:
            message = f"[Current date/time: {body.client_datetime}]\n{message}"

        context = {"show_slug": show_slug} if show_slug else {}
        result = await ep.process_message(message, context=context)
        return ProductionResponse(
            success=result.get("success", False),
            response=result.get("response", ""),
            workflow=result.get("workflow"),
            topic=result.get("topic"),
            agent="executive_producer",
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Produce endpoint error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/produce/stream")
async def produce_stream(body: ProductionRequest):
    """Stream a production run as Server-Sent Events (progress updates + final result)."""
    async def generate():
        try:
            from agents.registry import agent_registry
            ep = await agent_registry.get_agent("executive_producer")
            if not ep:
                yield f"data: {json.dumps({'type': 'error', 'content': 'Executive Producer not available'})}\n\n"
                return

            yield f"data: {json.dumps({'type': 'status', 'content': 'Production started...'})}\n\n"

            message, extracted_show = _extract_show_slug(body.request)
            show_slug = body.show_slug or extracted_show
            if body.client_datetime and "[Current date/time:" not in message:
                message = f"[Current date/time: {body.client_datetime}]\n{message}"

            context = {"show_slug": show_slug} if show_slug else {}
            result = await ep.process_message(message, context=context)

            yield f"data: {json.dumps({'type': 'result', 'content': result.get('response', ''), 'workflow': result.get('workflow'), 'topic': result.get('topic')})}\n\n"
            yield f"data: {json.dumps({'type': 'done'})}\n\n"

        except Exception as e:
            logger.error(f"Stream produce error: {e}", exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'content': str(e)})}\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/produce/async")
async def produce_async(body: ProductionRequest):
    """Start a production run in the background. Returns a job_id immediately."""
    job_id = str(uuid.uuid4())
    _jobs[job_id] = {"status": "running", "result": None, "error": None, "workflow": None, "topic": None}

    async def run_job():
        try:
            from agents.registry import agent_registry

            # Route breaking news checks to the dedicated checker agent
            raw, is_break_check = _extract_break_check(body.request)
            if is_break_check:
                checker = await agent_registry.get_agent("breaking_news_checker")
                if not checker:
                    _jobs[job_id]["status"] = "error"
                    _jobs[job_id]["error"] = "Breaking News Checker not available"
                    return
                result = await checker.process_message(raw)
                _jobs[job_id]["status"] = "complete"
                _jobs[job_id]["result"] = result.get("response", "")
                return

            ep = await agent_registry.get_agent("executive_producer")
            if not ep:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = "Executive Producer not available"
                return

            message, extracted_show = _extract_show_slug(body.request)
            show_slug = body.show_slug or extracted_show
            if body.client_datetime and "[Current date/time:" not in message:
                message = f"[Current date/time: {body.client_datetime}]\n{message}"

            context = {"show_slug": show_slug} if show_slug else {}
            result = await ep.process_message(message, context=context)
            # A halted/failed production (e.g. needs_human_review) must surface as
            # status="error" — Jarvis's polling only alerts on that status, and a
            # halt reported as "complete" is a halt nobody finds out about.
            if result.get("success", True):
                _jobs[job_id]["status"] = "complete"
            else:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = result.get("response", "")
            _jobs[job_id]["result"] = result.get("response", "")
            _jobs[job_id]["workflow"] = result.get("workflow")
            _jobs[job_id]["topic"] = result.get("topic")
        except Exception as e:
            logger.error(f"Async job {job_id} failed: {e}", exc_info=True)
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(e)

    asyncio.create_task(run_job())
    logger.info(f"[async] Job {job_id} started for: {body.request[:80]}")
    return {"job_id": job_id, "status": "started"}


@app.post("/webhook/ingest")
async def webhook_ingest(body: WebhookEventRequest):
    """
    Shared entry point for external event feeds — an earthquake/weather/market-data poller,
    an RSS-to-webhook bridge, etc. Normalizes any push source into one shape and routes it
    through the same newsworthiness/dedup/cooldown gates as the internal breaking-news poll
    (breaking_news_checker), firing a production only if the event clears the bar.
    See SELF_IMPROVEMENT_ROADMAP.md Phase 5 for the intended source adapters.
    """
    try:
        from agents.registry import agent_registry
        checker = await agent_registry.get_agent("breaking_news_checker")
        if not checker:
            raise HTTPException(status_code=503, detail="Breaking News Checker not available")
        result = await checker.process_webhook_event(
            source=body.source,
            headline=body.headline,
            detail=body.detail or "",
            url=body.url or "",
            keywords=body.keywords,
        )
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Webhook ingest error: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/video/{video_id}/poll")
async def cancel_video_poll(video_id: str):
    """Cancel an in-progress HeyGen poll for a given video_id."""
    from agents.anchor.agent import cancel_poll
    cancelled = cancel_poll(video_id)
    if cancelled:
        return {"cancelled": True, "video_id": video_id}
    return {"cancelled": False, "video_id": video_id, "detail": "No active poll found for this video_id"}


@app.get("/job/{job_id}")
async def get_job(job_id: str):
    """Poll for the status and result of an async production job."""
    if job_id not in _jobs:
        # Return a terminal error rather than 404 so pollers (e.g. Jarvis) stop retrying.
        # This happens after a server restart when the in-memory job store is cleared.
        return {"status": "error", "error": "Job not found — server may have restarted", "result": None}
    return _jobs[job_id]


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        reload_excludes=["cache/*", "output/*", "logs/*", "assets/*", "credentials/*"],
        log_level=settings.LOG_LEVEL.lower(),
    )
