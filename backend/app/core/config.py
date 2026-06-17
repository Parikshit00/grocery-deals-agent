"""Application settings loaded from the environment and .env file."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

    # Network
    api_host: str = "0.0.0.0"
    api_port: int = 28734
    frontend_port: int = 28735

    # Postgres
    postgres_user: str = "angebot"
    postgres_password: str = "angebot"
    postgres_db: str = "angebot"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379

    # Reasoning LLM (local, OpenAI-compatible: Ollama by default, vLLM optional)
    llm_base_url: str = "http://localhost:11434/v1"
    llm_api_key: str = "EMPTY"
    llm_model: str = "qwen3.5:latest"

    # Ollama (vision / fallback / embeddings)
    ollama_base_url: str = "http://localhost:11434"
    vision_model: str = "qwen2.5vl:7b"
    fallback_chat_model: str = "qwen3.5:latest"
    embedding_model: str = "bge-m3"

    # Data sources / cache
    marktguru_base_url: str = "https://api.marktguru.de/api/v1"
    marktguru_web_url: str = "https://www.marktguru.de"
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"
    http_rate_limit_per_min: int = 30
    offer_ttl_hours: int = 24

    # MCP servers
    mcp_host: str = "127.0.0.1"
    mcp_browse_port: int = 28810
    mcp_geo_port: int = 28811
    mcp_browse_url: str = "http://127.0.0.1:28810/mcp"
    mcp_geo_url: str = "http://127.0.0.1:28811/mcp"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
