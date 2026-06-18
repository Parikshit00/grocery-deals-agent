"""Basket schemas produced by the optimizer."""
from __future__ import annotations

from pydantic import BaseModel

from app.schemas.offer import Offer


class BasketLine(BaseModel):
    item: str
    offer: Offer


class Basket(BaseModel):
    mode: str  # "cross_store" | "single_store"
    store: str | None = None
    total: float
    currency: str = "EUR"
    coverage: int
    lines: list[BasketLine]
    missing: list[str]


class Baskets(BaseModel):
    cross_store: Basket
    single_store: Basket
