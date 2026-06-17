"""Data access for the offer cache."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import OfferCache
from app.schemas.offer import OfferSearchResult


async def get_cached(
    session: AsyncSession, zip_code: str, query: str, ttl_hours: int
) -> OfferSearchResult | None:
    """Return a cached result if present and fresher than ttl_hours, else None."""
    row = (
        await session.execute(
            select(OfferCache).where(
                OfferCache.zip_code == zip_code, OfferCache.query == query
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if datetime.now(UTC) - row.fetched_at > timedelta(hours=ttl_hours):
        return None
    return OfferSearchResult.model_validate(row.payload)


async def upsert_cache(
    session: AsyncSession, zip_code: str, query: str, result: OfferSearchResult
) -> None:
    stmt = pg_insert(OfferCache).values(
        zip_code=zip_code,
        query=query,
        payload=result.model_dump(mode="json"),
        fetched_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_offer_cache_zip_query",
        set_={"payload": stmt.excluded.payload, "fetched_at": stmt.excluded.fetched_at},
    )
    await session.execute(stmt)
    await session.commit()
