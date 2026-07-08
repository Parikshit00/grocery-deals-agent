"""Search the cached prospekt offers (DB) and rank matches per requested item."""
from __future__ import annotations

from typing import Any

from app.agents import available_retailers, get_recipe
from app.persistence import repository as repo
from app.persistence.db import SessionLocal
from app.services.matching import semantic_rank


async def search_items(
    zip_code: str, items: list[str], top: int = 8
) -> tuple[list[dict[str, Any]], int]:
    """For each item, semantic-match against all retailers' cached offers for the region.

    Reads ONLY the cache (no scanning); returns the results plus how many distinct chains had
    cached offers for this region, so the UI can show the search's coverage.
    """
    pairs = [(name, get_recipe(name).region_key(zip_code)) for name in available_retailers()]
    async with SessionLocal() as session:
        offers = await repo.get_region_offers(session, pairs)
    chains = len({o.retailer for o in offers if o.retailer})

    results: list[dict[str, Any]] = []
    for item in items:
        ranked = semantic_rank(item, offers, top=top) if offers else []
        results.append({"item": item, "offers": [o.model_dump(mode="json") for o in ranked]})
    return results, chains
