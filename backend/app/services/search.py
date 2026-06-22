"""Search the cached prospekt offers (DB) and rank matches per requested item."""
from __future__ import annotations

from typing import Any

from app.persistence import repository as repo
from app.persistence.db import SessionLocal
from app.services.matching import semantic_rank
from app.sources.prospekt import available_retailers, get_recipe


async def search_items(zip_code: str, items: list[str], top: int = 8) -> list[dict[str, Any]]:
    """For each item, semantic-match against all retailers' cached offers for the region."""
    pairs = [(name, get_recipe(name).region_key(zip_code)) for name in available_retailers()]
    async with SessionLocal() as session:
        offers = await repo.get_region_offers(session, pairs)

    results: list[dict[str, Any]] = []
    for item in items:
        ranked = semantic_rank(item, offers, top=top) if offers else []
        results.append({"item": item, "offers": [o.model_dump(mode="json") for o in ranked]})
    return results
