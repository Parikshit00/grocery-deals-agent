"""Prospekt endpoint: browse a retailer's prospekt + VLM-extract, streaming over SSE."""
from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.logging import get_logger
from app.services.prospekt import get_prospekt_offers

router = APIRouter(prefix="/api", tags=["prospekt"])
log = get_logger(__name__)

_LABELS = {
    "resolve": "Resolving region",
    "cache": "Checking cache",
    "browse": "Browsing official prospekt",
    "extract": "Reading pages with the vision model",
}


class ProspektRequest(BaseModel):
    retailer: str = "lidl"
    location: str
    force_refresh: bool = False


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


@router.post("/prospekt")
async def prospekt(req: ProspektRequest) -> StreamingResponse:
    queue: asyncio.Queue = asyncio.Queue()

    def emit(ev: dict) -> None:
        out = dict(ev)
        out["event"] = "progress"
        out["label"] = _LABELS.get(ev.get("step"), ev.get("step"))
        queue.put_nowait(out)

    async def run() -> None:
        try:
            res = await get_prospekt_offers(
                req.retailer, req.location, force_refresh=req.force_refresh, emit=emit
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
        except Exception as exc:  # noqa: BLE001 - surface to the client stream
            log.exception("prospekt.failed")
            queue.put_nowait({"event": "error", "message": str(exc)})
        finally:
            queue.put_nowait({"event": "done"})

    async def gen():
        task = asyncio.create_task(run())
        while True:
            ev = await queue.get()
            yield _sse(ev)
            if ev.get("event") == "done":
                break
        await task

    return StreamingResponse(gen(), media_type="text/event-stream")
