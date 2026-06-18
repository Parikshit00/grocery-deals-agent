"""Data access for the offer cache."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import OfferCache, SearchHistory, UserProfile
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


async def record_search(
    session: AsyncSession, user_id: str, location: str, query: str, mode: str
) -> None:
    profile = pg_insert(UserProfile).values(
        user_id=user_id, last_location=location, updated_at=datetime.now(UTC)
    )
    profile = profile.on_conflict_do_update(
        index_elements=["user_id"],
        set_={
            "last_location": profile.excluded.last_location,
            "updated_at": profile.excluded.updated_at,
        },
    )
    await session.execute(profile)
    session.add(SearchHistory(user_id=user_id, location=location, query=query, mode=mode))
    await session.commit()


async def get_profile(session: AsyncSession, user_id: str, limit: int = 8) -> dict:
    profile = (
        await session.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    ).scalar_one_or_none()
    rows = (
        await session.execute(
            select(SearchHistory)
            .where(SearchHistory.user_id == user_id)
            .order_by(desc(SearchHistory.created_at))
            .limit(limit)
        )
    ).scalars().all()
    return {
        "user_id": user_id,
        "last_location": profile.last_location if profile else None,
        "recent": [
            {"location": r.location, "query": r.query, "mode": r.mode} for r in rows
        ],
    }


async def list_stale_keys(
    session: AsyncSession, ttl_hours: int, limit: int = 100
) -> list[tuple[str, str]]:
    cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)
    rows = (
        await session.execute(
            select(OfferCache.zip_code, OfferCache.query)
            .where(OfferCache.fetched_at < cutoff)
            .limit(limit)
        )
    ).all()
    return [(r.zip_code, r.query) for r in rows]
