"""Data access for leaflets (the offer cache) and scan runs. Dates are always bound as
parameters (Europe/Berlin "today" from the caller), never CURRENT_DATE in SQL."""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy import desc, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.persistence.models import Leaflet, ScanRun

STORE_TTL = timedelta(days=7)


async def get_leaflet(
    session: AsyncSession, chain: str, edition: str, key: str
) -> Leaflet | None:
    return (
        await session.execute(
            select(Leaflet).where(
                Leaflet.chain == chain, Leaflet.edition == edition, Leaflet.leaflet_key == key
            )
        )
    ).scalar_one_or_none()


def is_hit(row: Leaflet | None, today: date) -> bool:
    """A cached leaflet can be served without rescanning."""
    return row is not None and row.status == "complete" and row.valid_to >= today


async def upsert_leaflet(session: AsyncSession, values: dict) -> None:
    """Insert or update a leaflet, but never replace a complete row with a partial one."""
    stmt = pg_insert(Leaflet).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_leaflets_key",
        set_={k: stmt.excluded[k] for k in values if k not in ("chain", "edition", "leaflet_key")},
        where=(Leaflet.status != "complete") | (stmt.excluded.status == "complete"),
    )
    await session.execute(stmt)
    await session.commit()


async def current_leaflets(
    session: AsyncSession, chain: str, edition: str, today: date
) -> list[Leaflet]:
    """Leaflets of an edition that have not expired, today's first."""
    return list(
        (
            await session.execute(
                select(Leaflet)
                .where(Leaflet.chain == chain, Leaflet.edition == edition,
                       Leaflet.valid_to >= today)
                .order_by(Leaflet.valid_from)
            )
        ).scalars()
    )


async def last_expired(
    session: AsyncSession, chain: str, edition: str, today: date
) -> Leaflet | None:
    """The most recent expired leaflet: served marked as expired when a rescan fails."""
    return (
        await session.execute(
            select(Leaflet)
            .where(Leaflet.chain == chain, Leaflet.edition == edition, Leaflet.valid_to < today)
            .order_by(desc(Leaflet.valid_to))
            .limit(1)
        )
    ).scalar_one_or_none()


async def cached_stores(
    session: AsyncSession, chain: str, plz: str, now: datetime
) -> list[dict] | None:
    """Stores found near this PLZ by a recent run, so normal scans skip the store lookup."""
    row = (
        await session.execute(
            select(ScanRun.stores_nearby)
            .where(
                ScanRun.chain == chain,
                ScanRun.plz == plz,
                ScanRun.stores_nearby.is_not(None),
                ScanRun.finished_at >= now - STORE_TTL,
            )
            .order_by(desc(ScanRun.finished_at))
            .limit(1)
        )
    ).scalar_one_or_none()
    return row


async def latest_runs(session: AsyncSession, plz: str) -> list[ScanRun]:
    """The newest finished run per chain for a PLZ."""
    rows = (
        await session.execute(
            select(ScanRun)
            .where(ScanRun.plz == plz, ScanRun.finished_at.is_not(None))
            .order_by(ScanRun.chain, desc(ScanRun.finished_at))
            .distinct(ScanRun.chain)
        )
    ).scalars()
    return list(rows)


async def recent_runs(session: AsyncSession, limit: int = 50) -> list[ScanRun]:
    return list(
        (
            await session.execute(
                select(ScanRun).order_by(desc(ScanRun.started_at)).limit(limit)
            )
        ).scalars()
    )
