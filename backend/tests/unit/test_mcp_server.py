"""MCP server: every tool called in-process through the SDK client against monkeypatched
services, plus the bearer gate and the /mcp route over HTTP."""
from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from fastapi import FastAPI, HTTPException
from mcp import Client

from app import mcp_server
from app.api import basket as basket_api
from app.api import scan as scan_api
from app.core.config import get_settings
from app.persistence import repository as repo
from app.schemas.basket import BasketRequest, BasketResponse, ChainState, PlanStore
from app.schemas.offer import Offer
from app.services import basket
from app.services.geo import Location

LOC = Location(lat=48.137, lon=11.575, plz="80331", label="80331 Muenchen")
STORE = {"id": "p1", "name": "Penny Sendlinger Str.", "address": "Sendlinger Str. 1",
         "lat": 48.135, "lon": 11.570, "edition": "e1", "located": True}
FAR = {**STORE, "id": "p2", "name": "Penny Far", "lat": 48.5, "lon": 11.5}


class _Session:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None


def _call(tool: str, args: dict | None = None):
    async def go():
        async with Client(mcp_server.mcp) as client:
            return await client.call_tool(tool, args or {})
    return asyncio.run(go())


@pytest.fixture
def located(monkeypatch):
    async def locate(query: str) -> Location:
        if query == "nowhere":
            raise HTTPException(422, "Location not found. Enter a German postcode or address.")
        return LOC

    async def deps():
        return {"llm": "up", "vlm": "up", "embeddings": "up"}

    monkeypatch.setattr(scan_api, "_locate", locate)
    monkeypatch.setattr(basket_api, "_dependencies", deps)


@pytest.fixture
def cache(monkeypatch, located):
    """A Penny run at 80331 with one current leaflet; every other chain unscanned."""
    run = SimpleNamespace(chain="penny", status="ok", error=None, store=STORE, tier=1,
                          stores_nearby=[STORE, FAR],
                          finished_at=datetime(2026, 10, 14, tzinfo=UTC))
    leaflet = SimpleNamespace(
        leaflet_key="kw42", title="Wochenangebote", kind="main", valid_from=date(2026, 10, 12),
        valid_to=date(2099, 1, 1), dates_source="feed", status="complete", tier=1,
        offers=[{"product_name": "Butter"}, {"product_name": "Milch"}])

    async def latest_runs(session, plz):
        return [run] if plz == "80331" else []

    async def current_leaflets(session, chain, edition, today):
        return [leaflet]

    monkeypatch.setattr(scan_api, "SessionLocal", _Session)
    monkeypatch.setattr(repo, "latest_runs", latest_runs)
    monkeypatch.setattr(repo, "current_leaflets", current_leaflets)


def test_tools_are_described_and_read_only():
    async def go():
        async with Client(mcp_server.mcp) as client:
            return (await client.list_tools()).tools
    tools = {t.name: t for t in asyncio.run(go())}
    assert set(tools) == {"list_chains", "nearest_stores", "scan_status", "search_offers",
                          "plan_basket"}
    for tool in tools.values():
        assert tool.description and tool.annotations and tool.annotations.read_only_hint
    props = tools["search_offers"].input_schema["properties"]
    assert props["query"]["description"] and props["k_per_chain"]["maximum"] == 10
    assert tools["plan_basket"].input_schema["required"] == ["location", "items"]


def test_list_chains():
    chains = _call("list_chains").structured_content["chains"]
    assert [c["id"] for c in chains] == ["penny", "kaufland", "aldi", "netto", "lidl", "rewe"]
    lidl = next(c for c in chains if c["id"] == "lidl")
    assert lidl == {"id": "lidl", "label": "Lidl", "loyalty": "Lidl Plus", "tier": 3}


def test_nearest_stores_filters_by_radius(cache):
    out = _call("nearest_stores", {"location": "80331", "radius_km": 5}).structured_content
    penny = out["chains"]["penny"]
    assert [s["id"] for s in penny["stores"]] == ["p1"]  # p2 is ~40 km away
    assert penny["stores"][0]["km"] == 0.4 and out["location"]["plz"] == "80331"


def test_scan_status_counts_offers_instead_of_listing_them(cache):
    chains = {c["chain"]: c for c in _call("scan_status", {"location": "80331"})
              .structured_content["chains"]}
    assert chains["penny"]["status"] == "ok" and chains["penny"]["store"]["km"] == 0.4
    assert chains["penny"]["leaflets"][0]["offers"] == 2
    assert chains["penny"]["leaflets"][0]["valid_from"] == "2026-10-12"
    assert chains["rewe"]["status"] == "needs_scan"


def test_search_offers_ranks_the_pool_with_honest_prices(monkeypatch, located):
    butter = Offer(chain="penny", product_name="Butter", price_cents=199,
                   loyalty_price_cents=169, pack_qty=250, pack_unit="g", source="feed",
                   price_check="feed")
    kekse = butter.model_copy(update={"product_name": "Butterkekse", "loyalty_price_cents": None})
    store = PlanStore(chain="penny", store="Penny Sendlinger Str.", km=0.4)
    pool = basket.Pool([basket.Entry("o0", store, butter), basket.Entry("o1", store, kekse)],
                       np.array([[1.0, 0.0], [0.6, 0.8]], dtype=np.float32), "k")
    seen = {}

    async def load_pool(session, location, req, day, now):
        seen["req"] = req
        return [ChainState(chain="penny", status="ok", store=store)], ["rewe"], pool

    async def embed(texts):
        return np.array([[1.0, 0.0]], dtype=np.float32)

    monkeypatch.setattr(basket, "load_pool", load_pool)
    monkeypatch.setattr(mcp_server, "embed_async", embed)
    monkeypatch.setattr(mcp_server, "SessionLocal", _Session)
    out = _call("search_offers", {"location": "80331", "query": "Butter", "k_per_chain": 1,
                                  "radius_km": 3}).structured_content
    assert seen["req"].radius_km == 3
    assert out["needs_scan"] == ["rewe"]
    assert out["offers"] == [{"store": "Penny Sendlinger Str.", "km": 0.4,
                              **butter.model_dump(mode="json", exclude_none=True)}]
    assert out["offers"][0]["loyalty_price_cents"] == 169


def test_plan_basket_calls_the_basket_service(monkeypatch, located):
    seen: dict[str, BasketRequest] = {}

    async def run(req, location):
        seen["req"] = req
        return BasketResponse(location=vars(location), shop_day=date(2026, 10, 14), chains=[],
                              needs_scan=["rewe"], items=[], single=[], split=None,
                              gaps=["Safran"])

    monkeypatch.setattr(basket, "run", run)
    out = _call("plan_basket", {"location": "80331", "loyalty": True, "max_stores": 1, "items": [
        {"name": "Butter", "qty": 500, "unit": "g"}, {"name": "Safran"}]}).structured_content
    req = seen["req"]
    assert req.loyalty and req.max_stores == 1 and req.items[0].qty == 500
    assert out["gaps"] == ["Safran"] and out["needs_scan"] == ["rewe"]


def test_plan_basket_reports_a_down_llm(monkeypatch, located):
    async def deps():
        return {"llm": "down", "vlm": "up", "embeddings": "up"}

    monkeypatch.setattr(basket_api, "_dependencies", deps)
    result = _call("plan_basket", {"location": "80331", "items": [{"name": "Butter"}]})
    assert result.is_error and "language model is down" in result.content[0].text


def test_unknown_location_is_a_tool_error(cache):
    result = _call("scan_status", {"location": "nowhere"})
    assert result.is_error and "Location not found" in result.content[0].text


def test_http_route_and_bearer_gate(monkeypatch):
    """The mount from main.py: POST /mcp, stateless JSON, bearer token when MCP_TOKEN is set."""
    monkeypatch.setattr(get_settings(), "mcp_token", "s3cret")
    app = FastAPI()
    app.add_route("/mcp", mcp_server.mcp_app)
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "list_chains", "arguments": {}}}
    headers = {"Accept": "application/json, text/event-stream",
               "MCP-Protocol-Version": "2025-06-18"}

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://lan-host:8000") as c:
            denied = await c.post("/mcp", json=body, headers=headers)
            wrong = await c.post("/mcp", json=body,
                                 headers={**headers, "Authorization": "Bearer nope"})
            for _ in range(2):  # a second lifespan (e.g. a reload) starts a fresh manager
                async with mcp_server.mcp_app.run():
                    ok = await c.post("/mcp", json=body,
                                      headers={**headers, "Authorization": "Bearer s3cret"})
        return denied, wrong, ok

    denied, wrong, ok = asyncio.run(go())
    assert denied.status_code == wrong.status_code == 401
    assert denied.headers["www-authenticate"] == "Bearer"
    assert ok.status_code == 200, ok.text
    result = ok.json()["result"]
    assert len(json.loads(result["content"][0]["text"])["chains"]) == 6
