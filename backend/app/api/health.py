"""Liveness and readiness endpoints."""
from __future__ import annotations

import redis.asyncio as aioredis
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.logging import get_logger
from app.persistence import db

router = APIRouter(tags=["health"])
log = get_logger(__name__)


@router.get("/health")
async def health() -> dict[str, str]:
    """Liveness: process is up."""
    return {"status": "ok"}


@router.get("/readyz")
async def readyz() -> JSONResponse:
    """Readiness: Postgres and Redis reachable."""
    settings = get_settings()
    checks: dict[str, str] = {}

    try:
        await db.ping()
        checks["postgres"] = "ok"
    except Exception as exc:  # noqa: BLE001 - report any failure to caller
        checks["postgres"] = f"error: {exc}"

    try:
        client = aioredis.from_url(settings.redis_url)
        await client.ping()
        await client.aclose()
        checks["redis"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["redis"] = f"error: {exc}"

    ready = all(v == "ok" for v in checks.values())
    return JSONResponse(
        status_code=200 if ready else 503,
        content={"ready": ready, "checks": checks},
    )
