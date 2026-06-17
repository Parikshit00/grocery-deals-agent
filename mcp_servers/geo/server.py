"""MCP server exposing location resolution (postcode or address -> German postcode)."""
from __future__ import annotations

from app.core.config import get_settings
from app.services.geo import resolve_zip as _resolve_zip
from mcp.server.fastmcp import FastMCP

settings = get_settings()
mcp = FastMCP("geo", host=settings.mcp_host, port=settings.mcp_geo_port)


@mcp.tool()
async def resolve_zip(location: str) -> str | None:
    """Resolve a postcode or free-form German address to a 5-digit postcode."""
    return await _resolve_zip(location)


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
