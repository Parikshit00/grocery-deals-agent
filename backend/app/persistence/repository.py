"""Data access for long-term memory and the prospekt offer cache."""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import delete, desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import ProspektOffers, SearchHistory, UserProfile
from app.schemas.offer import Offer


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
        "recent": [{"location": r.location, "query": r.query, "mode": r.mode} for r in rows],
    }


async def get_prospekt(
    session: AsyncSession, retailer: str, region_key: str
) -> ProspektOffers | None:
    return (
        await session.execute(
            select(ProspektOffers).where(
                ProspektOffers.retailer == retailer, ProspektOffers.region_key == region_key
            )
        )
    ).scalar_one_or_none()


async def upsert_prospekt(
    session: AsyncSession, retailer: str, region_key: str, valid_from, valid_to, payload: dict
) -> None:
    stmt = pg_insert(ProspektOffers).values(
        retailer=retailer,
        region_key=region_key,
        valid_from=valid_from,
        valid_to=valid_to,
        payload=payload,
        fetched_at=datetime.now(UTC),
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_prospekt_retailer_region",
        set_={
            "valid_from": stmt.excluded.valid_from,
            "valid_to": stmt.excluded.valid_to,
            "payload": stmt.excluded.payload,
            "fetched_at": stmt.excluded.fetched_at,
        },
    )
    await session.execute(stmt)
    await session.commit()


async def delete_prospekt(session: AsyncSession, retailer: str, region_key: str) -> None:
    await session.execute(
        delete(ProspektOffers).where(
            ProspektOffers.retailer == retailer, ProspektOffers.region_key == region_key
        )
    )
    await session.commit()


async def get_region_offers(
    session: AsyncSession, pairs: list[tuple[str, str]]
) -> list[Offer]:
    """Load still-valid cached offers across retailers for a region (today <= valid_to)."""
    offers: list[Offer] = []
    for retailer, region_key in pairs:
        row = await get_prospekt(session, retailer, region_key)
        if row and row.valid_to and date.today() <= row.valid_to:
            offers.extend(Offer.model_validate(o) for o in row.payload.get("offers", []))
    return offers
