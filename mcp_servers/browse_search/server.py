"""MCP server exposing tiered grocery-offer search (Tier 1: marktguru)."""
from __future__ import annotations

from app.core.config import get_settings
from app.services.matching import rank_offers
from app.services.offers import get_offers
from mcp.server.fastmcp import FastMCP

settings = get_settings()
mcp = FastMCP("browse-search", host=settings.mcp_host, port=settings.mcp_browse_port)


@mcp.tool()
async def search_offers(
    zip_code: str, query: str, limit: int = 20, top: int = 5
) -> list[dict]:
    """Find current discounted grocery offers near a German postcode for a search term.

    Returns the cheapest matching offers (product, price, old price, retailer, validity).
    """
    result = await get_offers(zip_code, query, limit=limit)
    return [offer.model_dump(mode="json") for offer in rank_offers(query, result.offers, top=top)]


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
