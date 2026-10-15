"""Model Context Protocol server: the platform's cached offers as read-only tools for external
agents and IDEs (Claude Code, Claude Desktop, Cursor), served as streamable HTTP at /mcp.

Every tool reads what earlier scans stored and never starts a scan or writes. plan_basket runs
the basket agent on the local LLM over that cache, exactly like POST /api/basket. If MCP_TOKEN
is set, requests need "Authorization: Bearer <token>".

main.py adds `mcp_app` as the route /mcp and enters `mcp_app.run()` in its lifespan (a mounted
app's own lifespan never runs).
"""
from __future__ import annotations

import hmac
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated, Any, TypeVar

from fastapi import HTTPException
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import ToolAnnotations
from pydantic import Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.agents import CHAINS
from app.api import basket as basket_api
from app.api import scan as scan_api
from app.clients.embeddings import embed_async
from app.core.config import get_settings
from app.persistence.db import SessionLocal
from app.schemas.basket import BasketRequest, Item
from app.services import basket
from app.services.scan import MAX_RADIUS_KM

T = TypeVar("T")

Location = Annotated[str, Field(
    min_length=1, max_length=200,
    description="German postcode or address, e.g. '80331' or 'Marienplatz 1, Muenchen'.")]
Radius = Annotated[float, Field(
    ge=0.5, le=MAX_RADIUS_KM, description="Search radius around the location in km.")]

_READ = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                        open_world_hint=False)

mcp: MCPServer = MCPServer(
    name="grocery-deals-agent",
    title="Grocery deals (Germany)",
    instructions=(
        "Current supermarket offers near a German location, from leaflets this platform has "
        "already scanned (Penny, Kaufland, Aldi Sued, Netto, Lidl, REWE). Read-only: no tool "
        "starts a scan. Money is integer euro cents, pack sizes are integer g, ml or stk. Call "
        "scan_status first; chains marked needs_scan have no cached offers for that location."),
)


async def _api(call: Awaitable[T]) -> T:
    """Await a call into the HTTP layer, turning its HTTPException into a tool error."""
    try:
        return await call
    except HTTPException as exc:
        raise ToolError(str(exc.detail)) from exc


@mcp.tool(annotations=_READ)
async def list_chains() -> dict[str, Any]:
    """List the supermarket chains this platform covers: id (use it in other tools), label,
    the loyalty program whose prices appear as loyalty_price_cents (null if none), and the
    acquisition tier (1 structured feed, 2 leaflet pages read by a vision model, 3 browser)."""
    return {"chains": [{"id": name, "label": m.LABEL, "loyalty": m.LOYALTY,
                        "tier": m.TIER} for name, m in CHAINS.items()]}


@mcp.tool(annotations=_READ)
async def nearest_stores(location: Location, radius_km: Radius = 5.0) -> dict[str, Any]:
    """Stores of each chain near a location, nearest first, with distance in km, from the
    store lists that earlier scans cached for that postcode. Chains never scanned there are
    missing. Each chain also reports its last scan status and the store that scan chose."""
    return await _api(scan_api.stores(location=location, radius_km=radius_km))


@mcp.tool(annotations=_READ)
async def scan_status(location: Location) -> dict[str, Any]:
    """Per chain: the last scan outcome near a location (ok, partial, blocked, no_store,
    not_covered, failed or needs_scan) with its reason, the chosen store with distance, and its
    leaflets with validity dates and offer counts. A leaflet with expired_on set is the last
    expired one, shown because no current leaflet exists."""
    data = await _api(scan_api.offers(location=location, radius_km=MAX_RADIUS_KM))
    for chain in data["chains"]:
        for leaflet in chain["leaflets"]:
            leaflet["offers"] = len(leaflet["offers"])
    return data


@mcp.tool(annotations=_READ)
async def search_offers(
    location: Location,
    query: Annotated[str, Field(min_length=1, max_length=80, description=(
        "What to look for, best as a German product term, e.g. 'Butter', 'Hackfleisch'."))],
    radius_km: Radius = 5.0,
    k_per_chain: Annotated[int, Field(ge=1, le=10, description="Results per chain.")] = 5,
) -> dict[str, Any]:
    """Semantic search over the current offers of each chain's nearest scanned store, ranked
    per chain with the same embedding search the basket agent uses. Only offers valid on the
    shop day (today, or Monday on a Sunday) are searched; non-food and offers whose price
    failed the leaflet text check are left out. Every hit carries the full offer: price_cents
    (regular offer price), loyalty_price_cents (only with the chain's app or card),
    bundle_price_cents for min_qty packs (multi-buys), reference_price_cents per kg, l or
    piece, condition as printed, validity dates and price_check (feed: exact feed price;
    text: VLM price confirmed in the page text; image_only: page had no text to check)."""
    loc = await _api(scan_api._locate(location))
    await _api(basket_api._require(embeddings=True))
    now = datetime.now(UTC)
    day = basket.shop_day(now)
    req = BasketRequest(location=location, radius_km=radius_km, items=[Item(name=query)])
    async with SessionLocal() as session:
        states, needs, pool = await basket.load_pool(session, loc, req, day, now)
    hits = pool.search(query, (await embed_async([query]))[0], k_per_chain)
    return {"location": vars(loc), "shop_day": day.isoformat(), "needs_scan": needs,
            "chains": [s.model_dump(mode="json") for s in states],
            "offers": [{"store": e.store.store, "km": e.store.km,
                        **e.offer.model_dump(mode="json", exclude_none=True)} for e in hits]}


@mcp.tool(annotations=_READ)
async def plan_basket(
    location: Location,
    items: Annotated[list[Item], Field(min_length=1, max_length=40, description=(
        "Shopping list. name: German search term; qty: amount needed in unit base units "
        "(g, ml or stk), omit for one pack."))],
    radius_km: Radius = 5.0,
    max_stores: Annotated[int, Field(ge=1, le=6, description=(
        "Most stores the split plan may visit."))] = 2,
    loyalty: Annotated[bool, Field(description=(
        "True if the shopper has the chains' apps or cards, so loyalty prices count."))] = False,
) -> dict[str, Any]:
    """Cheapest way to buy a shopping list near a location, from cached offers only. A local
    LLM agent decides per item which offers ARE that product; code computes every price. Returns
    one single-store plan per store (best coverage first), the cheapest split plan over at most
    max_stores stores with its saving, gaps (items with no current offer), chains that need a
    scan, and per item: status, the agent's reason, accepted and rejected offers and the up to
    three cheapest matches by unit price. Takes a few seconds per item."""
    await _api(basket_api._require(embeddings=True))
    req = BasketRequest(location=location, items=items, radius_km=radius_km,
                        max_stores=max_stores, loyalty=loyalty)
    response = await basket.run(req, await _api(scan_api._locate(location)))
    return response.model_dump(mode="json", exclude={"items": {"__all__": {"trace"}}})


def _token() -> str:
    return get_settings().mcp_token


def _sdk_app() -> ASGIApp:
    # Stateless JSON: every tool is one request and one response, no session state to keep.
    # The SDK's DNS rebinding guard admits only localhost Host headers; clients come over the LAN.
    return mcp.streamable_http_app(
        streamable_http_path="/mcp", stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))


class _McpApp:
    """The /mcp endpoint: answers 401 unless the request carries the MCP_TOKEN bearer token
    (when one is set). The host app enters run() in its lifespan."""

    app: ASGIApp

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        # A session manager runs only once, so every lifespan gets a fresh one.
        self.app = _sdk_app()
        async with mcp.session_manager.run():
            yield

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        token = _token()
        if token and scope["type"] == "http":
            sent = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(sent, f"Bearer {token}".encode()):
                await JSONResponse({"error": "invalid_token"}, 401,
                                   {"WWW-Authenticate": "Bearer"})(scope, receive, send)
                return
        await self.app(scope, receive, send)


mcp_app = _McpApp()
