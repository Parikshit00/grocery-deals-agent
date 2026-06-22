"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.agents.graph import build_graph
from app.api import health, prospekt, search
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    log = get_logger(__name__)

    from app.persistence.db import init_models

    try:
        await init_models()
    except Exception as exc:  # noqa: BLE001 - start degraded; readiness probe reflects health
        log.warning("startup.db_init_failed", error=str(exc))

    app.state.graph = build_graph()
    log.info("startup", app="grocery-deals-agent")
    yield


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
    app.include_router(prospekt.router)

    # Serve the built frontend (if present) from the API process, same-origin.
    dist = Path(__file__).resolve().parents[2] / "frontend" / "dist"
    if dist.exists():
        app.mount("/", StaticFiles(directory=str(dist), html=True), name="frontend")

    return app


app = create_app()
