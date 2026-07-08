"""Prospekt scans: cache-first by validity window, else browse + VLM-extract (overlapped).

Cache key = (retailer, region_key); region_key is the ZIP for market-specific chains, a
chain-wide key otherwise. A row is served only while valid_from <= today <= valid_to.
"""
from __future__ import annotations

import asyncio
from base64 import b64encode
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from app.agents import get_recipe
from app.agents.base import jpeg_thumb
from app.clients.vision import extract_offers_streamed
from app.core.logging import get_logger
from app.persistence import repository as repo
from app.persistence.db import SessionLocal
from app.schemas.offer import Offer

log = get_logger(__name__)


@dataclass
class ProspektResult:
    retailer: str
    region_key: str
    valid_from: date | None
    valid_to: date | None
    offers: list[Offer]
    from_cache: bool
    page_count: int


def _noop(event: dict) -> None:
    pass


def _is_current(valid_from: date | None, valid_to: date | None) -> bool:
    """A cached prospekt is served only within its own week: valid_from <= today <= valid_to.

    valid_to is required (an unbounded row is never served); valid_from is tolerated as unknown
    but, when present, blocks serving next week's leaflet before it starts.
    """
    today = date.today()
    if valid_to is None or today > valid_to:
        return False
    return valid_from is None or valid_from <= today


def _cache_decision(complete: bool, page_count: int, existing) -> str | None:
    """Why a fresh scan must NOT overwrite the cache (None = safe to write).

    Guards a good row against interrupted/degraded rescans: a capture that never reached the
    bottom ("partial"), or one with far fewer pages than the current row it would replace
    ("regression", the Aldi 27->15 class of bug). An empty scan is handled by the caller.
    """
    if not complete:
        return "partial"
    old = (
        existing.payload.get("page_count", 0)
        if existing is not None and _is_current(existing.valid_from, existing.valid_to)
        else 0
    )
    if old and page_count < 0.6 * old:
        return "regression"
    return None


async def get_prospekt_offers(
    retailer: str,
    zip_code: str,
    force_refresh: bool = False,
    emit: Callable[[dict], None] = _noop,
) -> ProspektResult:
    recipe = get_recipe(retailer)
    region = recipe.region_key(zip_code)
    emit({"step": "resolve", "status": "done", "detail": {"region": region}})

    if not force_refresh:
        async with SessionLocal() as session:
            row = await repo.get_prospekt(session, retailer, region)
        if row is not None and _is_current(row.valid_from, row.valid_to):
            offers = [Offer.model_validate(o) for o in row.payload.get("offers", [])]
            emit({"step": "cache", "status": "done",
                  "detail": {"hit": True, "valid_to": str(row.valid_to), "offers": len(offers)}})
            return ProspektResult(retailer, region, row.valid_from, row.valid_to, offers, True,
                                  row.payload.get("page_count", 0))
    emit({"step": "cache", "status": "done", "detail": {"hit": False}})

    emit({"step": "browse", "status": "running"})
    page_q: asyncio.Queue[bytes | None] = asyncio.Queue()
    streamed = False

    def on_frame(jpeg: bytes) -> None:
        emit({"event": "frame", "image": b64encode(jpeg).decode()})

    def on_capture(img: bytes) -> None:
        nonlocal streamed
        streamed = True
        page_q.put_nowait(img)

    def on_page(done: int, total: int, img: bytes, found: int) -> None:
        emit({"step": "extract", "status": "running", "detail": {"done": done, "total": total}})
        emit({"event": "page", "index": done, "total": total,
              "image": b64encode(jpeg_thumb(img)).decode(), "offers_so_far": found})

    # Extract pages as the agent captures them; if a recipe never streamed (no on_capture
    # fired), fall back to enqueueing its whole set after fetch.
    consumer = asyncio.create_task(extract_offers_streamed(page_q, retailer, on_page=on_page))
    pages = await recipe.fetch(zip_code, on_frame=on_frame, on_capture=on_capture)
    emit({"step": "browse", "status": "done",
          "detail": {"pages": len(pages.pages), "valid_to": str(pages.valid_to)}})
    if not streamed:
        for p in pages.pages:
            page_q.put_nowait(p)
    page_q.put_nowait(None)
    offers = await consumer
    emit({"step": "extract", "status": "done", "detail": {"offers": len(offers)}})

    page_count = len(pages.pages)

    if not pages.pages or not offers:
        # A prospekt never has zero offers; an empty extraction (e.g. every page-read failed)
        # must not overwrite a good cache row.
        emit({"step": "error",
              "detail": {"summary": "Scan produced no offers; not cached.", "recovery": "none"}})
        return ProspektResult(
            retailer, region, pages.valid_from, pages.valid_to, [], False, page_count
        )

    # Guard a good cache row against interrupted/degraded rescans (partial capture or a big
    # page-count drop): return the offers to the UI but do not write them back.
    async with SessionLocal() as session:
        existing = await repo.get_prospekt(session, retailer, region)
    kept = _cache_decision(pages.complete, page_count, existing)

    if kept is not None:
        emit({"step": "cache", "status": "kept",
              "detail": {"reason": kept, "pages": page_count, "offers": len(offers)}})
        return ProspektResult(
            retailer, region, pages.valid_from, pages.valid_to, offers, False, page_count
        )

    payload = {
        "offers": [o.model_dump(mode="json") for o in offers],
        "page_count": page_count,
    }
    async with SessionLocal() as session:
        await repo.upsert_prospekt(
            session, retailer, region, pages.valid_from, pages.valid_to, payload
        )
    return ProspektResult(
        retailer, region, pages.valid_from, pages.valid_to, offers, False, page_count
    )
