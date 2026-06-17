"""Cache-first offer retrieval: serve from the Postgres cache, else fetch and store."""
from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import get_logger
from app.persistence import repository as repo
from app.persistence.db import SessionLocal
from app.schemas.offer import OfferSearchResult
from app.sources import get_sources

log = get_logger(__name__)


async def get_offers(
    zip_code: str, query: str, limit: int = 20, force_refresh: bool = False
) -> OfferSearchResult:
    settings = get_settings()

    if not force_refresh:
        async with SessionLocal() as session:
            cached = await repo.get_cached(session, zip_code, query, settings.offer_ttl_hours)
        if cached is not None:
            log.info("offers.cache_hit", zip_code=zip_code, query=query, n=len(cached.offers))
            return cached

    result: OfferSearchResult | None = None
    for source in get_sources():
        try:
            result = await source.search(zip_code, query, limit=limit)
            if result.offers:
                break
        except Exception as exc:  # noqa: BLE001 - one bad source must not break the chain
            log.warning("offers.source_error", source=source.name, error=str(exc))

    if result is None:
        result = OfferSearchResult(query=query, zip_code=zip_code, total=0, offers=[])

    async with SessionLocal() as session:
        await repo.upsert_cache(session, zip_code, query, result)
    log.info("offers.fetched", zip_code=zip_code, query=query, n=len(result.offers))
    return result
