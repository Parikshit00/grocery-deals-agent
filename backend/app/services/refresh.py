"""Background task that keeps the offer cache fresh (proactive hybrid freshness)."""
from __future__ import annotations

import asyncio

from app.core.config import get_settings
from app.core.logging import get_logger
from app.persistence import repository as repo
from app.persistence.db import SessionLocal
from app.services.offers import get_offers

log = get_logger(__name__)


async def refresh_stale_once() -> int:
    settings = get_settings()
    async with SessionLocal() as session:
        keys = await repo.list_stale_keys(session, settings.offer_ttl_hours)
    for zip_code, query in keys:
        try:
            await get_offers(zip_code, query, force_refresh=True)
        except Exception as exc:  # noqa: BLE001 - one bad refresh must not stop the rest
            log.warning("refresh.error", zip_code=zip_code, query=query, error=str(exc))
        await asyncio.sleep(1.0)  # stay gentle on the source
    if keys:
        log.info("refresh.done", refreshed=len(keys))
    return len(keys)


async def refresh_loop() -> None:
    interval = get_settings().refresh_interval_hours * 3600
    while True:
        await asyncio.sleep(interval)
        try:
            await refresh_stale_once()
        except Exception as exc:  # noqa: BLE001 - keep the loop alive across failures
            log.warning("refresh.loop_error", error=str(exc))
