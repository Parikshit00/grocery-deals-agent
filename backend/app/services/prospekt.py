"""Orchestrate the official-prospekt tier: cache-first by validity, else browse + VLM-extract.

Retrieval-first / token-saving: if a fresh prospekt for the region is cached (today <= valid_to)
we reply from the DB without browsing or calling the VLM. `emit` reports step events for SSE.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

from app.core.logging import get_logger
from app.persistence import repository as repo
from app.persistence.db import SessionLocal
from app.schemas.offer import Offer
from app.services.vision import extract_offers_from_pages
from app.sources.prospekt import get_recipe

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
        if row is not None and row.valid_to and date.today() <= row.valid_to:
            offers = [Offer.model_validate(o) for o in row.payload.get("offers", [])]
            emit({"step": "cache", "status": "done",
                  "detail": {"hit": True, "valid_to": str(row.valid_to), "offers": len(offers)}})
            return ProspektResult(retailer, region, row.valid_from, row.valid_to, offers, True,
                                  row.payload.get("page_count", 0))
    emit({"step": "cache", "status": "done", "detail": {"hit": False}})

    emit({"step": "browse", "status": "running"})
    pages = await recipe.fetch(zip_code)
    emit({"step": "browse", "status": "done",
          "detail": {"pages": len(pages.pages), "valid_to": str(pages.valid_to)}})
    if not pages.pages:
        emit({"step": "error",
              "detail": {"summary": "Could not capture any prospekt pages.", "recovery": "none"}})
        return ProspektResult(retailer, region, pages.valid_from, pages.valid_to, [], False, 0)

    emit({"step": "extract", "status": "running", "detail": {"total": len(pages.pages)}})
    offers = await extract_offers_from_pages(
        pages.pages,
        retailer,
        on_page=lambda d, t: emit(
            {"step": "extract", "status": "running", "detail": {"done": d, "total": t}}
        ),
    )
    emit({"step": "extract", "status": "done", "detail": {"offers": len(offers)}})

    payload = {
        "offers": [o.model_dump(mode="json") for o in offers],
        "page_count": len(pages.pages),
    }
    async with SessionLocal() as session:
        await repo.upsert_prospekt(
            session, retailer, region, pages.valid_from, pages.valid_to, payload
        )
    return ProspektResult(
        retailer, region, pages.valid_from, pages.valid_to, offers, False, len(pages.pages)
    )
