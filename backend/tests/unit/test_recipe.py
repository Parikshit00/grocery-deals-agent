"""Recipe decomposition (streamed reasoning from a fake vLLM) and the recipe/basket API."""
from __future__ import annotations

import asyncio
import json
from datetime import date

import httpx
from fastapi.testclient import TestClient
from openai import AsyncOpenAI

from app.api import basket as api
from app.clients import llm
from app.main import create_app
from app.schemas.basket import BasketResponse, Item
from app.services.geo import Location

ANSWER = '[{"name": "Hackfleisch", "qty": 0.5, "unit": "kg"}, {"name": "Lasagneplatten"}]'


def _chunk(delta: dict, finish: str | None = None) -> str:
    return "data: " + json.dumps({
        "id": "x", "object": "chat.completion.chunk", "created": 0, "model": "llm",
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}) + "\n\n"


def _stream(*runs: list[str]) -> list[dict]:
    """Patch the LLM client to stream one scripted run per request; returns the requests."""
    requests: list[dict] = []
    script = list(runs)

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(json.loads(request.content))
        body = "".join(script.pop(0)) + "data: [DONE]\n\n"
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    llm._client = AsyncOpenAI(base_url="http://llm/v1", api_key="k", max_retries=0,
                              http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return requests


def test_decompose_streams_reasoning_and_parses_items(monkeypatch):
    monkeypatch.setattr(llm, "_client", None)
    requests = _stream([_chunk({"role": "assistant", "reasoning": "Lasagne braucht "}),
                        _chunk({"reasoning": "Hack."}), _chunk({"content": ANSWER}, "stop")])
    thoughts: list[str] = []
    items = asyncio.run(llm.decompose_recipe("Lasagne", 4, thoughts.append))
    assert "".join(thoughts) == "Lasagne braucht Hack."
    assert items == [Item(name="Hackfleisch", qty=500, unit="g"), Item(name="Lasagneplatten")]
    assert "Portionen: 4" in requests[0]["messages"][1]["content"]
    assert "chat_template_kwargs" not in requests[0]  # thinking on


def test_decompose_retries_without_thinking_on_length(monkeypatch):
    monkeypatch.setattr(llm, "_client", None)
    requests = _stream([_chunk({"reasoning_content": "lange..."}, "length")],
                       [_chunk({"content": ANSWER}, "stop")])
    items = asyncio.run(llm.decompose_recipe("Lasagne", 2))
    assert len(items) == 2
    assert requests[1]["chat_template_kwargs"] == {"enable_thinking": False}


# --- API --------------------------------------------------------------------------------


def _client(monkeypatch, llm_state: str = "up", embeddings: str = "up") -> TestClient:
    async def deps() -> dict:
        return {"llm": llm_state, "vlm": "up", "db": "up", "embeddings": embeddings}

    monkeypatch.setattr(api, "_dependencies", deps)
    return TestClient(create_app())


def _events(text: str) -> list[dict]:
    return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ")]


def test_recipe_sse(monkeypatch):
    async def fake(text, servings, on_think):
        on_think("Ich ")
        on_think("denke.")
        await asyncio.sleep(0)
        return [Item(name="Hackfleisch", qty=500, unit="g")]

    monkeypatch.setattr(api, "decompose_recipe", fake)
    resp = _client(monkeypatch).post("/api/recipe", json={"text": "Lasagne", "servings": 4})
    events = _events(resp.text)
    assert resp.status_code == 200
    assert "".join(e["text"] for e in events if e["event"] == "thinking") == "Ich denke."
    assert events[-2] == {"event": "items",
                          "items": [{"name": "Hackfleisch", "qty": 500, "unit": "g"}]}
    assert events[-1] == {"event": "done"}


def test_recipe_errors_are_events(monkeypatch):
    async def empty(text, servings, on_think):
        return []

    monkeypatch.setattr(api, "decompose_recipe", empty)
    events = _events(_client(monkeypatch).post("/api/recipe", json={"text": "x"}).text)
    assert [e["event"] for e in events] == ["error", "done"] and events[0]["code"] == "no_items"


def test_503_when_models_missing(monkeypatch):
    assert _client(monkeypatch, llm_state="down").post(
        "/api/recipe", json={"text": "x"}).status_code == 503
    body = {"location": "80331", "items": [{"name": "Butter"}]}
    assert _client(monkeypatch, embeddings="missing").post(
        "/api/basket", json=body).status_code == 503


def test_basket_endpoint(monkeypatch):
    loc = Location(lat=48.1, lon=11.6, plz="80331", label="80331 Muenchen")

    async def locate(query):
        return loc

    async def run(req, location):
        assert location is loc and req.items[0].name == "Butter"
        return BasketResponse(location=vars(location), shop_day=date(2026, 10, 14), chains=[],
                              needs_scan=["penny"], items=[], single=[], split=None, gaps=[])

    monkeypatch.setattr(api, "_locate", locate)
    monkeypatch.setattr(api.service, "run", run)
    resp = _client(monkeypatch).post("/api/basket", json={
        "location": "80331", "items": [{"name": "Butter"}]})
    assert resp.status_code == 200 and resp.json()["needs_scan"] == ["penny"]
    assert _client(monkeypatch).post("/api/basket", json={
        "location": "80331", "items": []}).status_code == 422
