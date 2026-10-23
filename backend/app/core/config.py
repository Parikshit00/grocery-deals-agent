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

    # Scanning: who we are (sent in every User-Agent) and how hard we may work.
    contact: str = "https://github.com/Parikshit00/grocery-deals-agent"
    chain_budget_s: int = 300  # network/browser phases of one chain scan
    max_browsers: int = 3  # concurrent Chromium instances across all chain scans
    vlm_max_inflight: int = 32  # concurrent page requests to the VLM (its --max-num-seqs)
    vlm_max_model_len: int = 8192  # the vlm server's --max-model-len (prompt + output budget)
    chromium_sandbox: bool = True  # keep Chromium's sandbox on (needs the seccomp profile)
    # Demo only: "<chain>:<step>" skips that scripted step so the navigator has to heal it.
    force_heal: str = ""
    # MCP server: when set, /mcp requires "Authorization: Bearer <token>".
    mcp_token: str = ""

    # Tracing (OpenTelemetry -> the local Langfuse); off unless enabled and configured.
    tracing_enabled: bool = False
    langfuse_base_url: str = ""
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
