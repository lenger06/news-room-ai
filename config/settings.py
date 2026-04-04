import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    # LLM
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")

    # Search
    TAVILY_API_KEY: str = os.getenv("TAVILY_API_KEY", "")

    # YouTube upload (optional — producer agent)
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

    @classmethod
    def validate(cls):
        if not cls.OPENAI_API_KEY:
            raise ValueError("OPENAI_API_KEY is required")
        return True


settings = Settings()
