import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Newsroom identity
    NEWSROOM_NAME: str = os.getenv("NEWSROOM_NAME", "Defy Logic News")

    # LLM
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Per-agent model selection. Override any role via env var (e.g. MODEL_FACT_CHECKER=gpt-4o)
    # without touching agent code — lets you tier a stronger/more expensive model onto
    # verification-heavy roles (fact_checker, compliance_checker) and a cheaper one onto
    # routine drafting roles, independently of each other.
    MODELS: dict = {
        "executive_producer": os.getenv("MODEL_EXECUTIVE_PRODUCER", "gpt-4o"),
        "researcher": os.getenv("MODEL_RESEARCHER", "gpt-4o"),
        "writer": os.getenv("MODEL_WRITER", "gpt-4o"),
        "fact_checker": os.getenv("MODEL_FACT_CHECKER", "gpt-4o"),
        "editor": os.getenv("MODEL_EDITOR", "gpt-4o"),
        "script_writer": os.getenv("MODEL_SCRIPT_WRITER", "gpt-4o"),
        "anchor": os.getenv("MODEL_ANCHOR", "gpt-4o"),
        "video_editor": os.getenv("MODEL_VIDEO_EDITOR", "gpt-4o"),
        "producer": os.getenv("MODEL_PRODUCER", "gpt-4o"),
        "publisher": os.getenv("MODEL_PUBLISHER", "gpt-4o"),
        "breaking_news_checker": os.getenv("MODEL_BREAKING_NEWS_CHECKER", "gpt-4o"),
        "compliance_checker": os.getenv("MODEL_COMPLIANCE_CHECKER", "gpt-4o"),
    }

    # Search
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")
    PIXABAY_API_KEY: str = os.getenv("PIXABAY_API_KEY", "")  # for video b-roll search

    # HeyGen (anchor video generation)
    # HEYGEN_AVATAR_ID / HEYGEN_VOICE_ID are kept as fallback for the generate_anchor_video tool
    # when no anchor is specified. The anchor roster in config/anchors.py is the primary source.
    HEYGEN_API_KEY: str = os.getenv("HEYGEN_API_KEY", "")
    HEYGEN_AVATAR_ID: str = os.getenv("HEYGEN_AVATAR_ID", "")
    HEYGEN_VOICE_ID: str = os.getenv("HEYGEN_VOICE_ID", "")
    HEYGEN_CREDIT_MINIMUM: int = int(os.getenv("HEYGEN_CREDIT_MINIMUM", "5"))

    # YouTube (publisher agent)
    YOUTUBE_CLIENT_SECRETS_PATH: str = os.getenv("YOUTUBE_CLIENT_SECRETS_PATH", "credentials/youtube_client_secrets.json")

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", 8091))
    DEBUG: bool = os.getenv("DEBUG", "True").lower() == "true"
    LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")

    # Logging rotation (see main.py) — one dated file per day, size-capped within
    # that day via a process-safe rotating handler, with old dated files pruned after
    # LOG_RETENTION_DAYS. Mirrors a log4j-style size+time rolling policy.
    LOG_MAX_BYTES: int = int(os.getenv("LOG_MAX_BYTES", str(25 * 1024 * 1024)))  # 25MB
    LOG_BACKUP_COUNT: int = int(os.getenv("LOG_BACKUP_COUNT", "10"))
    LOG_RETENTION_DAYS: int = int(os.getenv("LOG_RETENTION_DAYS", "30"))

    # Output directories
    ARTICLES_DIR: str = os.getenv("ARTICLES_DIR", "./output/articles")
    SCRIPTS_DIR: str = os.getenv("SCRIPTS_DIR", "./output/scripts")
    MEDIA_DIR: str = os.getenv("MEDIA_DIR", "./output/media")
    LOGS_DIR: str = os.getenv("LOGS_DIR", "./output/production_logs")

    # Event feeds (USGS earthquake / NWS weather alerts) — see tools/event_feeds.py.
    # Disabled by default: this is a genuinely new autonomous trigger path (a qualifying
    # earthquake or severe weather alert can fire a real, credit-spending, publish-to-YouTube
    # production with no human in the loop) — opt in deliberately.
    EVENT_FEEDS_ENABLED: bool = os.getenv("EVENT_FEEDS_ENABLED", "False").lower() == "true"
    EVENT_FEED_POLL_SECONDS: int = int(os.getenv("EVENT_FEED_POLL_SECONDS", "300"))
    EVENT_FEED_MIN_MAGNITUDE: float = float(os.getenv("EVENT_FEED_MIN_MAGNITUDE", "6.0"))
    EVENT_FEED_NWS_SEVERITIES: str = os.getenv("EVENT_FEED_NWS_SEVERITIES", "Extreme,Severe")
    # Comma-separated RSS/Atom feed URLs to poll (empty = disabled even if EVENT_FEEDS_ENABLED
    # is true). Point this at low-volume "breaking news"/"top stories" category feeds, not a
    # full firehose — every new item still goes through the same strict qualifying-criteria
    # LLM gate as the other feeds, so a high-volume feed just means a lot of wasted evaluations.
    EVENT_FEED_RSS_URLS: str = os.getenv("EVENT_FEED_RSS_URLS", "")
    EVENT_FEED_RSS_MAX_ITEMS_PER_FEED: int = int(os.getenv("EVENT_FEED_RSS_MAX_ITEMS_PER_FEED", "10"))
    # NWS/weather.gov requires a descriptive User-Agent identifying the app + contact info —
    # personalize this in .env before enabling event feeds.
    EVENT_FEED_USER_AGENT: str = os.getenv(
        "EVENT_FEED_USER_AGENT", "news-room-ai (set EVENT_FEED_USER_AGENT in .env with contact info)"
    )

    # B-roll PiP compositing
    # Path to a still frame (JPEG/PNG) of the studio background used as the
    # composite base when showing b-roll in the upper-left corner.
    # Only used as a fallback when FFmpeg video compositing is unavailable.
    BROLL_BG_FRAME_PATH: str = os.getenv("BROLL_BG_FRAME_PATH", "")
    # Studio background videos for FFmpeg PiP compositing:
    # Place them in ./assets/ named after the HeyGen asset ID, e.g.:
    #   ./assets/f6fa4085043140deaba8258a96233036.mp4

    @classmethod
    def model_for(cls, role: str) -> str:
        """Return the configured model name for an agent role, defaulting to gpt-4o."""
        return cls.MODELS.get(role, "gpt-4o")

    @classmethod
    def validate(cls):
        if not cls.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is required")
        return True


settings = Settings()
