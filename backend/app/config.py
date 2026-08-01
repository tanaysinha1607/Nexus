"""Application configuration via environment variables."""

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Central configuration loaded from environment / .env file."""

    # Postgres
    database_url: str = "postgresql+asyncpg://nexus:nexus_dev@localhost:5432/nexus"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # App
    app_name: str = "Nexus"
    debug: bool = True

    # LLM
    nexus_llm_provider: str = "gemini"
    nexus_llm_model: str = "openai/gpt-oss-120b"
    gemini_api_key: str = ""
    groq_api_key: str = ""

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8"}


settings = Settings()
