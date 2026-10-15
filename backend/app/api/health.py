"""Liveness, readiness and dependency status.

Readiness gates on Postgres only: the models are a separate compose profile that is started on
demand, so the API must serve (and report them as down) without them.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.agents import CHAINS
from app.core.config import get_settings
from app.core.logging import get_logger
from app.persistence import db

router = APIRouter(tags=["health"])
log = get_logger(__name__)

_STATUS_TTL_S = 5.0
_cache: tuple[float, dict[str, str]] | None = None


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: process is up."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> JSONResponse:
    """Readiness: Postgres reachable."""
    try:
        await db.ping()
    except Exception as exc:  # noqa: BLE001 - report any failure to caller
        log.warning("readyz.postgres_failed", error=str(exc))
        return JSONResponse(status_code=503, content={"ready": False})
    return JSONResponse(status_code=200, content={"ready": True})


async def _model_up(client: httpx.AsyncClient, base_url: str) -> str:
    try:
        resp = await client.get(f"{base_url}/models")
    except httpx.HTTPError:
        return "down"
    return "up" if resp.status_code == 200 else "down"


async def _dependencies() -> dict[str, str]:
    global _cache
    if _cache and time.monotonic() - _cache[0] < _STATUS_TTL_S:
        return _cache[1]
    settings = get_settings()
    headers = {"Authorization": f"Bearer {settings.vllm_api_key}"}
    async with httpx.AsyncClient(timeout=2.0, headers=headers) as client:
        deps = {
            "llm": await _model_up(client, settings.llm_base_url),
            "vlm": await _model_up(client, settings.vlm_base_url),
        }
    try:
        await db.ping()
        deps["db"] = "up"
    except Exception:  # noqa: BLE001 - reported, details are in /readyz logs
        deps["db"] = "down"
    deps["embeddings"] = "up" if Path(settings.embedding_model).is_dir() else "missing"
    _cache = (time.monotonic(), deps)
    return deps


@router.get("/api/status")
async def status() -> dict:
    """What the UI needs to enable or disable scans: dependency state plus the chain list."""
    chains = [{"id": m.NAME, "label": m.LABEL, "loyalty": m.LOYALTY, "tier": m.TIER}
              for m in CHAINS.values()]
    return {**await _dependencies(), "chains": chains}
