"""Application settings, read from the process environment (compose passes them in)."""
from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: str = "local"
    log_level: str = "INFO"

    # Postgres
    postgres_user: str = "angebot"
    postgres_password: str = "angebot"
    postgres_db: str = "angebot"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # Local vLLM servers (OpenAI-compatible), both behind the same API key.
    llm_base_url: str = "http://localhost:28800/v1"
    llm_model: str = "llm"
    vlm_base_url: str = "http://localhost:28801/v1"
    vision_model: str = "vlm"
    vllm_api_key: str = ""

    # Embeddings (sentence-transformers): a local directory under MODELS_DIR in compose.
    embedding_model: str = "/models/bge-m3"

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
