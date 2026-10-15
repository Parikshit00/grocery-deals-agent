"""Basket and recipe API contract, shared by services/basket.py and the UI.

POST /api/recipe  (SSE)  RecipeRequest -> events: thinking {text}, items {items: [Item]},
                         error {code, message}, done.
POST /api/basket (JSON) BasketRequest -> BasketResponse. Cache-only: chains without a current
                         leaflet near the location are listed in needs_scan, never scanned here.

Money is integer cents, quantities are integer base units (g, ml, stk), as in Offer.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.offer import Offer, PackUnit


class Item(BaseModel):
    """One thing to buy: a German search term plus the amount needed, if known."""

    name: str = Field(min_length=1, max_length=80)
    qty: int | None = Field(None, gt=0)  # in `unit` base units; None = one pack
    unit: PackUnit | None = None


class RecipeRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)
    servings: int = Field(4, ge=1, le=20)


class BasketRequest(BaseModel):
    location: str = Field(min_length=1, max_length=200)
    radius_km: float = Field(5.0, ge=0.5, le=25.0)
    items: list[Item] = Field(min_length=1, max_length=40)
    max_stores: int = Field(2, ge=1, le=6)
    loyalty: bool = False  # "I have the app / card": loyalty prices count
    overrides: dict[str, str] = {}  # chain -> store id picked by the user


class Match(BaseModel):
    """An accepted offer for an item at one chain, costed for the needed amount."""

    chain: str
    store: str  # store name
    km: float | None
    offer: Offer
    packs: int  # packs to buy (multi-buys: a multiple of offer.min_qty)
    cost_cents: int
    unit_price_cents: int | None  # per kg, l or piece
    note: str | None = None  # e.g. "amount unknown: priced as one pack"


class Decision(BaseModel):
    chain: str
    offer_id: str  # short per-request id shown to the agent
    name: str
    why: str | None = None


class ItemResult(BaseModel):
    item: Item
    status: Literal["matched", "no_offer", "matcher_unavailable"]
    best: list[Match]  # up to 3 cheapest by unit price across chains in the radius
    accepted: list[Decision]
    rejected: list[Decision]
    trace: list[dict]  # the agent's tool calls: {tool, args, n, ms, repeat?}
    reason: str | None = None
    turns: int = 0
    tokens: int = 0


class PlanStore(BaseModel):
    chain: str
    store: str
    km: float | None


class PlanLine(BaseModel):
    item: str
    match: Match


class Plan(BaseModel):
    kind: Literal["single", "split"]
    stores: list[PlanStore]
    total_cents: int
    covered: list[str]
    missing: list[str]
    lines: list[PlanLine]
    # Saving against the best single-store plan that covers exactly the same items, if any.
    saves_cents: int | None = None


class ChainState(BaseModel):
    chain: str
    status: str  # ok | partial | expired | blocked | not_covered | no_store | failed | needs_scan
    reason: str | None = None
    store: PlanStore | None = None


class BasketResponse(BaseModel):
    location: dict
    shop_day: date  # Mon..Sat: today; Sunday: next Monday
    chains: list[ChainState]
    needs_scan: list[str]
    items: list[ItemResult]
    single: list[Plan]  # one per store, sorted by coverage desc then total asc
    split: Plan | None  # cheapest plan over at most max_stores stores
    gaps: list[str]  # items with no current offer within the radius
    trace_id: str | None = None  # Langfuse trace of this basket, None when tracing is off
