"""
Regression guard for the 2026-08-07 incident: agents/publisher/agent.py's
_set_thumbnail_sync called os.unlink(tmp_path) AFTER the thumbnail upload already
succeeded, but a cleanup failure there (WinError 32 — file briefly locked by AV
scanning, observed live on Windows) was caught by the same except block and
misreported as a thumbnail failure, even though youtube.thumbnails().set(...) had
already completed. Cleanup failures must not override an already-successful result.
No real network/YouTube calls — requests.get and the YouTube service are mocked.
"""
import sys
import os
import requests
from pathlib import Path

project_root = Path(__file__).parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from agents.publisher.agent import Agent as PublisherAgent


class _FakeImgResponse:
    ok = True
    content = b"fake-jpeg-bytes"


class _FakeThumbnailsResource:
    def set(self, videoId, media_body):
        class _Req:
            def execute(self_inner):
                return {}
        return _Req()


class _FakeYouTubeService:
    def thumbnails(self):
        return _FakeThumbnailsResource()


def test_thumbnail_set_succeeds_despite_cleanup_failure(monkeypatch):
    # requests and os.unlink are imported locally inside _set_thumbnail_sync, so the
    # real modules must be patched directly (patching agents.publisher.agent's own
    # namespace wouldn't reach them — there's no module-level import to intercept).
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _FakeImgResponse())

    import tools.youtube_tool as youtube_tool
    monkeypatch.setattr(youtube_tool, "_get_youtube_service", lambda: _FakeYouTubeService())

    def fake_unlink(path):
        raise OSError("WinError 32: file is being used by another process")

    monkeypatch.setattr(os, "unlink", fake_unlink)

    agent = PublisherAgent()
    result = agent._set_thumbnail_sync("video123", "https://example.com/thumb.jpg")

    assert result == {"success": True}


def test_thumbnail_set_still_reports_real_upload_errors(monkeypatch):
    monkeypatch.setattr(requests, "get", lambda url, timeout=None: _FakeImgResponse())

    import tools.youtube_tool as youtube_tool

    class _FailingYouTubeService:
        def thumbnails(self):
            class _R:
                def set(self, videoId, media_body):
                    raise RuntimeError("simulated YouTube API failure")
            return _R()

    monkeypatch.setattr(youtube_tool, "_get_youtube_service", lambda: _FailingYouTubeService())

    agent = PublisherAgent()
    result = agent._set_thumbnail_sync("video123", "https://example.com/thumb.jpg")

    assert "error" in result
