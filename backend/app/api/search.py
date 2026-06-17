"""Search endpoint: runs the agent and streams progress over SSE."""
from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.core.logging import get_logger
from app.schemas.search import SearchRequest

router = APIRouter(prefix="/api", tags=["search"])
log = get_logger(__name__)

_LABELS = {
    "resolve": "Resolving location",
    "plan": "Planning shopping items",
    "retrieve": "Searching offers",
}


def _sse(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _detail(node: str, state: dict[str, Any]) -> dict[str, Any]:
    if node == "resolve":
        return {"zip_code": state.get("zip_code")}
    if node == "plan":
        return {"items": state.get("items")}
    if node == "retrieve":
        return {"items_done": len(state.get("results") or [])}
    return {}


@router.post("/search")
async def search(req: SearchRequest, request: Request) -> Any:
    graph = getattr(request.app.state, "graph", None)
    if graph is None:
        return JSONResponse(
            status_code=503, content={"detail": "Agent unavailable (MCP servers not connected)."}
        )

    async def gen():
        state: dict[str, Any] = {
            "location": req.location,
            "query": req.query,
            "mode": req.mode,
        }
        final = dict(state)
        try:
            async for chunk in graph.astream(state, stream_mode="updates"):
                for node, update in chunk.items():
                    if update:
                        final.update(update)
                    yield _sse(
                        {
                            "event": "progress",
                            "step": node,
                            "label": _LABELS.get(node, node),
                            "detail": _detail(node, final),
                        }
                    )
            if final.get("error"):
                yield _sse({"event": "error", "message": final["error"]})
            else:
                yield _sse(
                    {
                        "event": "result",
                        "zip_code": final.get("zip_code"),
                        "items": final.get("items"),
                        "results": final.get("results"),
                    }
                )
        except Exception as exc:  # noqa: BLE001 - surface failures to the client stream
            log.exception("search.failed")
            yield _sse({"event": "error", "message": str(exc)})
        yield _sse({"event": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")
