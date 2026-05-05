import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import json
import re
import logging
import requests
from langchain_core.messages import SystemMessage, HumanMessage
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from tools.heygen_tool import get_heygen_credits, generate_video_multiscene
from config.settings import settings

logger = logging.getLogger(__name__)

HEYGEN_BASE_URL = "https://api.heygen.com"
POLL_INTERVAL_SECONDS = 30
MAX_POLL_ATTEMPTS = 120  # 60 minutes max

_active_polls: dict[str, asyncio.Task] = {}


def cancel_poll(video_id: str) -> bool:
    """Cancel an in-progress poll for video_id. Returns True if a task was found and cancelled."""
    task = _active_polls.get(video_id)
    if task and not task.done():
        task.cancel()
        return True
    return False


class Agent(BaseAgent):
    """
    Anchor — cleans the script, resolves [BROLL:] markers to image URLs,
    submits a single- or multi-scene video to HeyGen, then polls until complete.
    """

    def __init__(self):
        from agents.anchor.prompts import ANCHOR_PROMPT
        self._system_prompt = ANCHOR_PROMPT
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.1, openai_api_key=settings.OPENAI_API_KEY)
        logger.info("Anchor agent initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="anchor",
            display_name="Anchor",
            description="Generates AI news anchor video from broadcast script using HeyGen",
            version="2.0.0",
            module_path="agents.anchor.agent",
            parent_agent="executive_producer",
        )

    # ── Script cleaning ──────────────────────────────────────────────────────

    def _clean_script_sync(self, message: str) -> str:
        """Use LLM to strip [GRAPHIC:] / [PAUSE] markers while preserving [BROLL:] markers."""
        response = self.llm.invoke([
            SystemMessage(content=self._system_prompt),
            HumanMessage(content=message),
        ])
        return response.content.strip()

    # ── B-roll parsing ───────────────────────────────────────────────────────

    def _parse_segments(self, script: str) -> list[dict]:
        """
        Split script on [BROLL: ...] markers into alternating anchor/broll segments.
        re.split with a capture group gives: [text, desc, text, desc, ...]
        """
        parts = re.split(r'\[BROLL:\s*([^\]]+)\]', script, flags=re.IGNORECASE)
        segments = []
        for i, part in enumerate(parts):
            part = part.strip()
            if not part:
                continue
            if i % 2 == 0:
                segments.append({"type": "anchor", "script": part})
            else:
                segments.append({"type": "broll", "description": part, "image_url": None})
        return segments

    def _search_image_sync(self, query: str) -> str | None:
        """Fetch the first image URL for a query via Tavily."""
        if not settings.TAVILY_API_KEY:
            return None
        try:
            payload = {
                "api_key": settings.TAVILY_API_KEY,
                "query": query,
                "search_depth": "basic",
                "include_images": True,
                "include_image_descriptions": False,
                "max_results": 3,
            }
            resp = requests.post("https://api.tavily.com/search", json=payload, timeout=15)
            if not resp.ok:
                return None
            images = resp.json().get("images", [])
            if not images:
                return None
            first = images[0]
            return first.get("url") if isinstance(first, dict) else first
        except Exception as e:
            logger.warning(f"[anchor] Image search failed for '{query}': {e}")
            return None

    async def _resolve_broll_images(self, segments: list[dict]) -> list[dict]:
        """For each broll segment, search for a public image URL."""
        resolved = []
        for seg in segments:
            if seg["type"] == "broll":
                url = await asyncio.to_thread(self._search_image_sync, seg["description"])
                if url:
                    seg["image_url"] = url
                    logger.info(f"[anchor] B-roll resolved: '{seg['description']}' → {url}")
                else:
                    logger.warning(f"[anchor] No image found for b-roll: '{seg['description']}' — scene will be skipped")
            resolved.append(seg)
        return resolved

    # ── HeyGen param extraction ──────────────────────────────────────────────

    def _extract_heygen_params(self, message: str) -> tuple[str, str, str]:
        """Parse AVATAR ID / VOICE ID / BACKGROUND ASSET ID injected by the executive producer."""
        def find(pattern):
            m = re.search(pattern, message, re.IGNORECASE)
            return m.group(1).strip() if m else ""

        avatar_id = find(r'AVATAR ID[:\s]+([^\n]+)')
        voice_id  = find(r'VOICE ID[:\s]+([^\n]+)')
        bg_id     = find(r'BACKGROUND ASSET ID[:\s]+([^\n]+)')

        return (
            avatar_id or settings.HEYGEN_AVATAR_ID,
            voice_id  or settings.HEYGEN_VOICE_ID,
            bg_id     or "f6fa4085043140deaba8258a96233036",
        )

    # ── HeyGen polling ───────────────────────────────────────────────────────

    def _check_status_sync(self, video_id: str) -> dict:
        try:
            response = requests.get(
                f"{HEYGEN_BASE_URL}/v1/video_status.get",
                headers={"x-api-key": settings.HEYGEN_API_KEY},
                params={"video_id": video_id},
                timeout=30,
            )
            if not response.ok:
                return {"error": f"HTTP {response.status_code}: {response.text[:200]}"}
            data = response.json().get("data", {})
            return {
                "video_id": video_id,
                "status": data.get("status", "unknown"),
                "video_url": data.get("video_url"),
                "thumbnail_url": data.get("thumbnail_url"),
            }
        except Exception as e:
            logger.error(f"[heygen] status check error: {e}", exc_info=True)
            return {"error": str(e)}

    async def _poll_until_complete(self, video_id: str) -> dict:
        logger.info(f"[heygen] Waiting 15s before first status check for {video_id}...")
        await asyncio.sleep(15)

        consecutive_errors = 0
        for attempt in range(1, MAX_POLL_ATTEMPTS + 1):
            logger.info(f"[heygen] Poll {attempt}/{MAX_POLL_ATTEMPTS} for {video_id}")
            result = await asyncio.to_thread(self._check_status_sync, video_id)

            if "error" in result:
                consecutive_errors += 1
                logger.warning(f"[heygen] Poll error ({consecutive_errors}/5): {result['error']}")
                if consecutive_errors >= 5:
                    return result
                if attempt < MAX_POLL_ATTEMPTS:
                    await asyncio.sleep(POLL_INTERVAL_SECONDS)
                continue

            consecutive_errors = 0
            status = result.get("status", "unknown")
            logger.info(f"[heygen] Video {video_id} status: {status}")

            if status == "completed":
                return result
            if status == "failed":
                return {"error": f"HeyGen video generation failed for {video_id}", "video_id": video_id}

            if attempt < MAX_POLL_ATTEMPTS:
                logger.info(f"[heygen] Status '{status}', waiting {POLL_INTERVAL_SECONDS}s...")
                await asyncio.sleep(POLL_INTERVAL_SECONDS)

        return {"error": f"Timed out waiting for video {video_id} after {MAX_POLL_ATTEMPTS} attempts"}

    # ── Main entry point ─────────────────────────────────────────────────────

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            # Step 0: Credit check
            credits_before = None
            try:
                credits_before = await asyncio.to_thread(get_heygen_credits)
                minimum = settings.HEYGEN_CREDIT_MINIMUM
                logger.info(f"[anchor] HeyGen credits: {credits_before} (min: {minimum})")
                if credits_before < minimum:
                    msg = (
                        f"HeyGen credit balance too low: {credits_before} remaining "
                        f"(minimum: {minimum}). Please top up your HeyGen account."
                    )
                    logger.error(f"[anchor] {msg}")
                    return {"success": False, "response": msg, "agent": "anchor"}
            except Exception as credit_err:
                logger.warning(f"[anchor] Could not verify HeyGen credits: {credit_err}")

            # Step 1: Extract HeyGen params from the message
            avatar_id, voice_id, bg_id = self._extract_heygen_params(message)

            # Step 2: Extract script_writer output from EP context, then clean it
            script_match = re.search(
                r'=== SCRIPT_WRITER OUTPUT ===\s*(.*?)(?:===|\Z)',
                message, re.DOTALL | re.IGNORECASE,
            )
            script_to_clean = script_match.group(1).strip() if script_match else message
            if script_match:
                logger.info(f"[anchor] Extracted script_writer output ({len(script_to_clean)} chars)")
            else:
                logger.warning("[anchor] No SCRIPT_WRITER OUTPUT section found — using full message")
            cleaned = await asyncio.to_thread(self._clean_script_sync, script_to_clean)
            logger.info(f"[anchor] Cleaned script ({len(cleaned)} chars)")

            # Step 3: Parse [BROLL:] markers into segments
            segments = self._parse_segments(cleaned)
            has_broll = any(s["type"] == "broll" for s in segments)
            logger.info(
                f"[anchor] Segments: {len(segments)} "
                f"({sum(1 for s in segments if s['type'] == 'anchor')} anchor, "
                f"{sum(1 for s in segments if s['type'] == 'broll')} b-roll)"
            )

            # Step 4: Resolve b-roll image URLs
            if has_broll:
                segments = await self._resolve_broll_images(segments)

            # Step 5: Submit to HeyGen
            title = re.search(r'TOPIC[:\s]+([^\n]+)', message, re.IGNORECASE)
            title = title.group(1).strip() if title else "News Segment"

            submit_result = await asyncio.to_thread(
                generate_video_multiscene,
                segments, avatar_id, voice_id, bg_id, title,
            )

            if not submit_result.get("video_id"):
                err = submit_result.get("error", "Unknown error submitting to HeyGen")
                logger.error(f"[anchor] Submit failed: {err}")
                return {"success": False, "response": f"Anchor FAILED: {err}", "agent": "anchor"}

            video_id = submit_result["video_id"]
            scene_count = submit_result.get("scene_count", 1)
            logger.info(f"[anchor] Submitted. video_id={video_id}, scenes={scene_count}. Polling...")

            # Step 6: Poll until complete
            poll_task = asyncio.create_task(self._poll_until_complete(video_id))
            _active_polls[video_id] = poll_task
            try:
                poll_result = await poll_task
            except asyncio.CancelledError:
                logger.info(f"[anchor] Poll for {video_id} cancelled")
                return {
                    "success": False,
                    "response": f"Polling cancelled for video {video_id}. It may still be rendering in HeyGen.",
                    "agent": "anchor",
                    "video_id": video_id,
                }
            finally:
                _active_polls.pop(video_id, None)

            if "error" in poll_result:
                return {
                    "success": False,
                    "response": f"Anchor video polling FAILED: {poll_result['error']}",
                    "agent": "anchor",
                    "video_id": video_id,
                }

            video_url = poll_result.get("video_url", "")
            thumbnail_url = poll_result.get("thumbnail_url", "")

            # Log credit cost
            credits_used_str = ""
            if credits_before is not None:
                try:
                    credits_after = await asyncio.to_thread(get_heygen_credits)
                    credits_used = credits_before - credits_after
                    logger.info(f"[anchor] Credits used: {credits_used} (before: {credits_before}, after: {credits_after})")
                    credits_used_str = f"\ncredits_used: {credits_used} (balance: {credits_after} remaining)"
                except Exception:
                    pass

            logger.info(f"[anchor] Video ready: {video_url}")

            return {
                "success": True,
                "response": (
                    f"Anchor video generation complete.\n"
                    f"video_id: {video_id}\n"
                    f"video_url: {video_url}\n"
                    f"thumbnail_url: {thumbnail_url}\n"
                    f"scenes: {scene_count}"
                    f"{credits_used_str}"
                ),
                "agent": "anchor",
                "video_id": video_id,
                "video_url": video_url,
                "thumbnail_url": thumbnail_url,
            }

        except Exception as e:
            logger.error(f"Anchor error: {e}", exc_info=True)
            return {"success": False, "response": f"Anchor video generation FAILED: {str(e)}", "agent": "anchor"}
