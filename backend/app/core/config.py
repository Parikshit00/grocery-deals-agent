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

    # Reasoning LLM (local vLLM, OpenAI-compatible) - Qwen3-30B-A3B-Thinking on GPU 0
    llm_base_url: str = "http://localhost:28800/v1"
    llm_api_key: str = "EMPTY"
    llm_model: str = "qwen3-thinking"

    # Vision VLM (local vLLM, OpenAI-compatible) - Qwen3-VL-32B-Instruct on GPU 1
    vlm_base_url: str = "http://localhost:28801/v1"
    vision_model: str = "qwen3-vl"

    # Embeddings (sentence-transformers, local)
    embedding_model: str = "BAAI/bge-m3"

    # Geocoding
    nominatim_base_url: str = "https://nominatim.openstreetmap.org"

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
