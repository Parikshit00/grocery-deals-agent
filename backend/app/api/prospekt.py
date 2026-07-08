"""Prospekt endpoint: browse retailers' prospekts in parallel + VLM-extract, streaming over SSE.

One request may select several retailers; each runs as its own agent task over a shared queue,
so the client receives interleaved events tagged with `retailer` (progress, live browser frames,
per-page reading, result, agent_done) and a single final `done`.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agents import available_retailers, get_recipe
from app.core.logging import get_logger
from app.persistence import repository as repo
from app.persistence.db import SessionLocal
from app.services.prospekt import _is_current, get_prospekt_offers

router = APIRouter(prefix="/api", tags=["prospekt"])
log = get_logger(__name__)

_LABELS = {
    "resolve": "Resolving region",
    "cache": "Checking cache",
    "browse": "Browsing official prospekt",
    "extract": "Reading pages with the vision model",
}


class ProspektRequest(BaseModel):
    retailers: list[str] = ["lidl"]
    location: str
    force_refresh: bool = False


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/prospekt")
async def prospekt(req: ProspektRequest) -> StreamingResponse:
    queue: asyncio.Queue = asyncio.Queue()

    def emitter(retailer: str):
        def emit(ev: dict) -> None:
            out = dict(ev)
            out.setdefault("event", "progress")
            if out["event"] == "progress":
                step = ev.get("step") or ""
                out["label"] = _LABELS.get(step, step)
            out["retailer"] = retailer
            queue.put_nowait(out)

        return emit

    async def run(retailer: str, delay: float) -> None:
        await asyncio.sleep(delay)  # stagger launches so 6 Chromiums don't stampede on start
        try:
            res = await get_prospekt_offers(
                retailer, req.location, force_refresh=req.force_refresh, emit=emitter(retailer)
            )
            queue.put_nowait(
                {
                    "event": "result",
                    "retailer": res.retailer,
                    "region_key": res.region_key,
                    "valid_from": str(res.valid_from) if res.valid_from else None,
                    "valid_to": str(res.valid_to) if res.valid_to else None,
                    "from_cache": res.from_cache,
                    "page_count": res.page_count,
                    "offers": [o.model_dump(mode="json") for o in res.offers],
                }
            )
        except Exception as exc:  # noqa: BLE001 - one agent fails alone, stream continues
            log.exception("prospekt.failed")
            queue.put_nowait({"event": "error", "retailer": retailer, "message": str(exc)})
        finally:
            queue.put_nowait({"event": "agent_done", "retailer": retailer})

    async def finish(tasks: list[asyncio.Task]) -> None:
        await asyncio.gather(*tasks, return_exceptions=True)
        queue.put_nowait({"event": "done"})

    async def gen():
        tasks = [asyncio.create_task(run(r, i * 1.5)) for i, r in enumerate(req.retailers)]
        fin = asyncio.create_task(finish(tasks))
        while True:
            ev = await queue.get()
            yield _sse(ev)
            if ev.get("event") == "done":
                break
        await fin

    return StreamingResponse(gen(), media_type="text/event-stream")


@router.get("/prospekt/cache")
async def prospekt_cache(location: str) -> list[dict[str, Any]]:
    """Per configured chain: whether a valid prospekt is cached for this location + its window."""
    out: list[dict[str, Any]] = []
    async with SessionLocal() as session:
        for retailer in available_retailers():
            region = get_recipe(retailer).region_key(location)
            row = await repo.get_prospekt(session, retailer, region)
            current = bool(row and _is_current(row.valid_from, row.valid_to))
            out.append(
                {
                    "retailer": retailer,
                    "cached": current,
                    "valid_from": str(row.valid_from) if row and row.valid_from else None,
                    "valid_to": str(row.valid_to) if row and row.valid_to else None,
                    "offers": len(row.payload.get("offers", [])) if row else 0,
                }
            )
    return out


@router.delete("/prospekt/cache/{retailer}")
async def clear_prospekt_cache(retailer: str, location: str) -> dict[str, str]:
    async with SessionLocal() as session:
        region = get_recipe(retailer).region_key(location)
        await repo.delete_prospekt(session, retailer, region)
    return {"status": "cleared", "retailer": retailer}
