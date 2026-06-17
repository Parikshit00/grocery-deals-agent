"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import health
from app.core.logging import configure_logging, get_logger


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    get_logger(__name__).info("startup", app="grocery-deals-agent")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="grocery-deals-agent", version="0.0.1", lifespan=lifespan)
    app.include_router(health.router)
    return app


app = create_app()
