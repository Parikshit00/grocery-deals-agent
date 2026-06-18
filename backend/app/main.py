"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agents.graph import build_graph
from app.api import health, search
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log = get_logger(__name__)
    settings = get_settings()
    app.state.graph = None

    from app.persistence.db import init_models

    try:
        await init_models()
    except Exception as exc:  # noqa: BLE001 - start degraded; readiness probe reflects health
        log.warning("startup.db_init_failed", error=str(exc))

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        client = MultiServerMCPClient(
            {
                "browse_search": {"transport": "streamable_http", "url": settings.mcp_browse_url},
                "geo": {"transport": "streamable_http", "url": settings.mcp_geo_url},
            }
        )
        tools = await client.get_tools()
        app.state.mcp_client = client
        app.state.graph = build_graph({t.name: t for t in tools})
        log.info("startup", app="grocery-deals-agent", tools=[t.name for t in tools])
    except Exception as exc:  # noqa: BLE001 - search is unavailable until MCP servers are up
        log.warning("startup.mcp_unavailable", error=str(exc))

    refresh_task: asyncio.Task | None = None
    if settings.refresh_enabled:
        from app.services.refresh import refresh_loop

        refresh_task = asyncio.create_task(refresh_loop())
        log.info("startup.refresh_enabled", interval_hours=settings.refresh_interval_hours)

    yield

    if refresh_task is not None:
        refresh_task.cancel()


def create_app() -> FastAPI:
    app = FastAPI(title="grocery-deals-agent", version="0.0.1", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(search.router)

    # Serve the built frontend (if present) from the API process, same-origin.
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app


app = create_app()
