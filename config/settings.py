import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # Newsroom identity
    NEWSROOM_NAME: str = os.getenv("NEWSROOM_NAME", "Defy Logic News")

    # LLM
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Search
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

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

    # Output directories
    ARTICLES_DIR: str = os.getenv("ARTICLES_DIR", "./output/articles")
    SCRIPTS_DIR: str = os.getenv("SCRIPTS_DIR", "./output/scripts")
    MEDIA_DIR: str = os.getenv("MEDIA_DIR", "./output/media")
    LOGS_DIR: str = os.getenv("LOGS_DIR", "./output/production_logs")

    @classmethod
    def validate(cls):
        if not cls.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is required")
        return True


settings = Settings()
