"""Recipe and basket API (contract: app/schemas/basket.py).

POST /api/recipe streams server-sent events, one JSON object per `data:` line:

    thinking  {text}                 the model's reasoning, in chunks as it arrives
    items     {items: [Item]}        the shopping items (editable chips in the UI)
    error     {code, message}        llm_unavailable | no_items
    done      {}

POST /api/basket returns a BasketResponse. Both answer 503 while the LLM is down; the basket
also while the embedding model is missing.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from openai import OpenAIError

from app.api.health import _dependencies
from app.api.scan import _locate
from app.clients.llm import decompose_recipe
from app.schemas.basket import BasketRequest, BasketResponse, RecipeRequest
from app.services import basket as service

router = APIRouter(prefix="/api", tags=["basket"])


async def _require(embeddings: bool) -> None:
    deps = await _dependencies()
    if deps["llm"] != "up":
        raise HTTPException(503, "The language model is down: start it with make models-up.")
    if embeddings and deps["embeddings"] != "up":
        raise HTTPException(503, "Embedding model missing: run make models-pull NAME=bge-m3.")


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


async def _recipe_events(body: RecipeRequest) -> AsyncIterator[str]:
    chunks: asyncio.Queue[str] = asyncio.Queue()
    task = asyncio.create_task(decompose_recipe(body.text, body.servings, chunks.put_nowait))
    try:
        while not (task.done() and chunks.empty()):
            try:
                text = await asyncio.wait_for(chunks.get(), 0.5)
            except TimeoutError:
                continue
            while not chunks.empty():
                text += chunks.get_nowait()
            yield _sse({"event": "thinking", "text": text})
            await asyncio.sleep(0.1)  # coalesce token-sized chunks
        try:
            items = task.result()
        except OpenAIError as exc:
            yield _sse({"event": "error", "code": "llm_unavailable",
                        "message": f"The language model failed: {type(exc).__name__}."})
        else:
            if items:
                yield _sse({"event": "items", "items": [i.model_dump() for i in items]})
            else:
                yield _sse({"event": "error", "code": "no_items",
                            "message": "No shopping items could be read from the answer."})
        yield _sse({"event": "done"})
    finally:
        task.cancel()


@router.post("/recipe")
async def recipe(body: RecipeRequest) -> StreamingResponse:
    await _require(embeddings=False)
    return StreamingResponse(_recipe_events(body), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/basket")
async def basket(body: BasketRequest) -> BasketResponse:
    await _require(embeddings=True)
    return await service.run(body, await _locate(body.location))
