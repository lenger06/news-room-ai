import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

import asyncio
import json
import logging
from langchain.agents import create_openai_functions_agent, AgentExecutor
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_openai import ChatOpenAI
from agents.registry import BaseAgent, AgentInfo
from agents.publisher.prompts import PUBLISHER_PROMPT
from tools.file_operations_tool import file_operations_tool
from config.settings import settings

logger = logging.getLogger(__name__)

MEDIA_DIR = "./output/media"


class Agent(BaseAgent):
    """Publisher — reads the video package, uploads to YouTube exactly once, sets thumbnail."""

    def __init__(self):
        self.llm = ChatOpenAI(model="gpt-4o", temperature=0.1, openai_api_key=settings.OPENAI_API_KEY)
        # LLM only gets file_operations_tool to read the package — no upload tool
        self.tools = [file_operations_tool]
        prompt = ChatPromptTemplate.from_messages([
            ("system", PUBLISHER_PROMPT),
            MessagesPlaceholder("chat_history", optional=True),
            ("human", "{input}"),
            MessagesPlaceholder("agent_scratchpad"),
        ])
        agent = create_openai_functions_agent(self.llm, self.tools, prompt)
        self.executor = AgentExecutor(agent=agent, tools=self.tools, verbose=True, max_iterations=5)
        logger.info("Publisher agent initialized")

    def get_info(self) -> AgentInfo:
        return AgentInfo(
            name="publisher",
            display_name="Publisher",
            description="Uploads finished video to YouTube and sets title, description, thumbnail",
            version="1.0.0",
            module_path="agents.publisher.agent",
            parent_agent="executive_producer",
        )

    def _upload_sync(self, video_file: str, title: str, description: str,
                     tags: list, privacy: str) -> dict:
        """Synchronous YouTube upload — called via asyncio.to_thread."""
        from googleapiclient.http import MediaFileUpload
        from tools.youtube_tool import _get_youtube_service

        if not Path(video_file).exists():
            return {"error": f"Video file not found: {video_file}"}

        youtube = _get_youtube_service()
        body = {
            "snippet": {
                "title": title[:100],
                "description": description,
                "tags": tags,
                "categoryId": "25",  # News & Politics
            },
            "status": {
                "privacyStatus": privacy,
                "selfDeclaredMadeForKids": False,
            },
        }
        media = MediaFileUpload(video_file, mimetype="video/mp4", resumable=True,
                                chunksize=1024 * 1024 * 8)
        request = youtube.videos().insert(part="snippet,status", body=body, media_body=media)

        logger.info(f"[publisher] Uploading: {title}")
        response = None
        while response is None:
            status, response = request.next_chunk()
            if status:
                logger.info(f"[publisher] Upload progress: {int(status.progress() * 100)}%")

        video_id = response.get("id")
        url = f"https://www.youtube.com/watch?v={video_id}"
        logger.info(f"[publisher] Upload complete: {url}")
        return {"youtube_video_id": video_id, "youtube_url": url}

    def _set_thumbnail_sync(self, video_id: str, thumbnail_url: str) -> dict:
        """Synchronous thumbnail set — called via asyncio.to_thread."""
        import requests as req
        import tempfile
        import os
        from googleapiclient.http import MediaFileUpload
        from tools.youtube_tool import _get_youtube_service

        try:
            img_response = req.get(thumbnail_url, timeout=15)
            if not img_response.ok:
                return {"error": f"HTTP {img_response.status_code} downloading thumbnail"}

            url_lower = thumbnail_url.lower().split("?")[0]
            if url_lower.endswith(".png"):
                mime_type, suffix = "image/png", ".png"
            elif url_lower.endswith(".webp"):
                mime_type, suffix = "image/webp", ".webp"
            else:
                mime_type, suffix = "image/jpeg", ".jpg"

            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(img_response.content)
                tmp_path = tmp.name

            youtube = _get_youtube_service()
            media = MediaFileUpload(tmp_path, mimetype=mime_type)
            youtube.thumbnails().set(videoId=video_id, media_body=media).execute()
            os.unlink(tmp_path)
            logger.info(f"[publisher] Thumbnail set for {video_id}")
            return {"success": True}
        except Exception as e:
            logger.warning(f"[publisher] Thumbnail error: {e}")
            return {"error": str(e)}

    async def process_message(self, message: str, context: dict = None) -> dict:
        try:
            # Step 1: LLM reads video_package.json and returns structured metadata as JSON
            result = await asyncio.to_thread(
                self.executor.invoke,
                {"input": message + "\n\nRead the video_package.json and return the upload metadata as JSON with keys: video_file, title, description, tags (list), privacy_status, thumbnail_url.", "chat_history": []}
            )
            llm_output = result.get("output", "")

            # Step 2: Parse metadata from LLM output
            metadata = self._extract_metadata(llm_output)
            if "error" in metadata:
                return {"success": False, "response": f"Publisher failed to read package: {metadata['error']}", "agent": "publisher"}

            video_file = metadata.get("video_file", "")
            title = metadata.get("title", "Defy Logic News Video")
            description = metadata.get("description", "")
            tags = metadata.get("tags", [])
            privacy = metadata.get("privacy_status", "unlisted")
            thumbnail_url = metadata.get("thumbnail_url", "")

            # Step 3: Upload exactly once — native Python, not LLM
            upload_result = await asyncio.to_thread(
                self._upload_sync, video_file, title, description, tags, privacy
            )

            if "error" in upload_result:
                return {"success": False, "response": f"YouTube upload failed: {upload_result['error']}", "agent": "publisher"}

            youtube_url = upload_result["youtube_url"]
            youtube_video_id = upload_result["youtube_video_id"]

            # Step 4: Set thumbnail (best-effort, don't fail production if this errors)
            thumb_result = {}
            if thumbnail_url:
                thumb_result = await asyncio.to_thread(
                    self._set_thumbnail_sync, youtube_video_id, thumbnail_url
                )

            thumb_note = "" if thumb_result.get("success") else f" (thumbnail not set: {thumb_result.get('error', 'unknown')})"

            return {
                "success": True,
                "response": (
                    f"Published to YouTube successfully.\n"
                    f"Title: {title}\n"
                    f"URL: {youtube_url}\n"
                    f"Video ID: {youtube_video_id}\n"
                    f"Privacy: {privacy}{thumb_note}"
                ),
                "agent": "publisher",
                "youtube_url": youtube_url,
                "youtube_video_id": youtube_video_id,
            }

        except Exception as e:
            logger.error(f"Publisher error: {e}", exc_info=True)
            return {"success": False, "response": f"Publishing failed: {str(e)}", "agent": "publisher"}

    def _extract_metadata(self, text: str) -> dict:
        """Extract upload metadata from LLM output — tries JSON blocks first, then key scanning."""
        # Try fenced JSON block
        import re
        for pattern in [r"```json\s*(\{.*?\})\s*```", r"```\s*(\{.*?\})\s*```", r"(\{[^{}]*\"video_file\"[^{}]*\})"]:
            m = re.search(pattern, text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass

        # Try reading video_package.json directly as fallback
        try:
            pkg_path = Path(MEDIA_DIR) / "video_package.json"
            if pkg_path.exists():
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                newsroom = settings.NEWSROOM_NAME
                raw_title = pkg.get("title", "News Video")
                title = f"{newsroom} | {raw_title}" if not raw_title.startswith(newsroom) else raw_title
                desc = pkg.get("description", "") + f"\n\n{newsroom}"
                return {
                    "video_file": pkg.get("video_file", ""),
                    "title": title,
                    "description": desc,
                    "tags": pkg.get("tags", []),
                    "privacy_status": "unlisted",
                    "thumbnail_url": pkg.get("thumbnail_url", ""),
                }
        except Exception as e:
            logger.warning(f"[publisher] Fallback package read failed: {e}")

        return {"error": "Could not extract upload metadata from LLM output or video_package.json"}
