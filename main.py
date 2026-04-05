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


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("=== Newsroom AI starting up ===")
    settings.validate()

    from agents.registry import agent_registry
    for name in ["researcher", "writer", "fact_checker", "script_writer", "anchor", "video_editor", "producer", "publisher", "executive_producer"]:
        agent = await agent_registry.get_agent(name)
        logger.info(f"  {'✓' if agent else '✗'} {name}")

    # Ensure output directories exist
    for d in [settings.ARTICLES_DIR, settings.SCRIPTS_DIR, settings.MEDIA_DIR, settings.LOGS_DIR]:
        Path(d).mkdir(parents=True, exist_ok=True)

    logger.info("=== Newsroom AI ready ===")
    yield
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
class ProductionRequest(BaseModel):
    request: str                          # Natural language production request
    client_datetime: Optional[str] = None


class ProductionResponse(BaseModel):
    success: bool
    response: str
    workflow: Optional[str] = None
    topic: Optional[str] = None
    agent: str = "executive_producer"


# ── Routes ────────────────────────────────────────────────────────────────────
@app.get("/")
async def root():
    return {
        "name": "Newsroom AI",
        "version": "1.0.0",
        "status": "running",
        "workflows": ["RESEARCH_ONLY", "ARTICLE", "FULL_PRODUCTION", "BROADCAST_VIDEO", "SCRIPT_ONLY", "VIDEO_FROM_SCRIPT"],
        "endpoints": {
            "produce": "POST /produce",
            "produce_stream": "POST /produce/stream",
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
            for name in ["executive_producer", "researcher", "writer", "fact_checker", "script_writer", "anchor", "video_editor", "producer", "publisher"]
        },
    }


@app.post("/produce", response_model=ProductionResponse)
async def produce(body: ProductionRequest):
    """Trigger a newsroom production run."""
    try:
        from agents.registry import agent_registry
        ep = await agent_registry.get_agent("executive_producer")
        if not ep:
            raise HTTPException(status_code=503, detail="Executive Producer not available")

        message = body.request
        if body.client_datetime:
            message = f"[Current date/time: {body.client_datetime}]\n{message}"

        result = await ep.process_message(message)
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

            message = body.request
            if body.client_datetime:
                message = f"[Current date/time: {body.client_datetime}]\n{message}"

            result = await ep.process_message(message)

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
            ep = await agent_registry.get_agent("executive_producer")
            if not ep:
                _jobs[job_id]["status"] = "error"
                _jobs[job_id]["error"] = "Executive Producer not available"
                return

            message = body.request
            if body.client_datetime:
                message = f"[Current date/time: {body.client_datetime}]\n{message}"

            result = await ep.process_message(message)
            _jobs[job_id]["status"] = "complete"
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


@app.get("/job/{job_id}")
async def get_job(job_id: str):
    """Poll for the status and result of an async production job."""
    if job_id not in _jobs:
        raise HTTPException(status_code=404, detail="Job not found")
    return _jobs[job_id]


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level=settings.LOG_LEVEL.lower(),
    )
