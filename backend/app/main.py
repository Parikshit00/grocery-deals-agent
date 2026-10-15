"""FastAPI application entry point. The UI is served same-origin by nginx, so no CORS."""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import basket, evals, health, scan
from app.core import tracing
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.mcp_server import mcp_app


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_logging()
    traced = tracing.setup(get_settings())
    get_logger(__name__).info("startup", app="grocery-deals-agent", tracing=traced)
    # A route-mounted ASGI app's own lifespan never runs: start the MCP session manager here.
    async with mcp_app.run():
        yield
    tracing.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(title="grocery-deals-agent", version="0.1.0", lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(scan.router)
    app.include_router(basket.router)
    app.include_router(evals.router)
    # Model Context Protocol (streamable HTTP), read-only tools; a route, not a Mount, so /mcp
    # is served without a redirect to /mcp/.
    app.add_route("/mcp", mcp_app)  # type: ignore[arg-type]  # an ASGI app is a valid endpoint
    return app


app = create_app()
