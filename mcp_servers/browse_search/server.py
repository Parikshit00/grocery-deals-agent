"""MCP server exposing tiered grocery-offer search (Tier 1: marktguru)."""
from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.matching import rank_offers, semantic_rank
from app.services.offers import get_offers
from mcp.server.fastmcp import FastMCP

log = get_logger(__name__)
settings = get_settings()
mcp = FastMCP("browse-search", host=settings.mcp_host, port=settings.mcp_browse_port)


@mcp.tool()
async def search_offers(zip_code: str, query: str, limit: int = 20, top: int = 5) -> list[dict]:
    """Find current discounted grocery offers near a German postcode for a search term.

    Returns the cheapest semantically matching offers (product, price, old price, retailer).
    """
    result = await get_offers(zip_code, query, limit=limit)
    try:
        ranked = await asyncio.to_thread(semantic_rank, query, result.offers, top)
    except Exception as exc:  # noqa: BLE001 - fall back to lexical ranking if embeddings fail
        log.warning("search_offers.semantic_failed", error=str(exc))
        ranked = rank_offers(query, result.offers, top=top)
    return [offer.model_dump(mode="json") for offer in ranked]


if __name__ == "__main__":
    try:
        from app.core.embeddings import embed

        embed(["warmup"])
    except Exception as exc:  # noqa: BLE001 - otherwise the model loads on first request
        log.warning("embeddings.warmup_failed", error=str(exc))
    mcp.run(transport="streamable-http")
