"""MCP server: extract grocery offers from a retailer's official prospekt (browse + local VLM)."""
from __future__ import annotations

from app.core.config import get_settings
from app.services.prospekt import get_prospekt_offers
from mcp.server.fastmcp import FastMCP

settings = get_settings()
mcp = FastMCP("vision", host=settings.mcp_host, port=settings.mcp_vision_port)


@mcp.tool()
async def extract_prospekt_offers(
    retailer: str, zip_code: str, force_refresh: bool = False
) -> dict:
    """Browse a retailer's official weekly prospekt near a postcode and extract offers via the VLM.

    Cache-first: returns stored offers if this week's prospekt was already scanned for the region.
    """
    res = await get_prospekt_offers(retailer, zip_code, force_refresh=force_refresh)
    return {
        "retailer": res.retailer,
        "region_key": res.region_key,
        "valid_from": str(res.valid_from) if res.valid_from else None,
        "valid_to": str(res.valid_to) if res.valid_to else None,
        "from_cache": res.from_cache,
        "page_count": res.page_count,
        "offers": [o.model_dump(mode="json") for o in res.offers],
    }


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
