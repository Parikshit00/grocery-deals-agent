"""Offer and search-result schemas shared across sources, services, and the API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, computed_field


class Offer(BaseModel):
    source: str
    external_id: str | None = None
    product_name: str
    description: str | None = None
    brand: str | None = None
    retailer: str | None = None
    price: float | None = None
    old_price: float | None = None
    unit: str | None = None
    valid_from: datetime | None = None
    valid_to: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def discount_pct(self) -> int | None:
        if self.old_price and self.price and self.old_price > self.price:
            return round((1 - self.price / self.old_price) * 100)
        return None


class OfferSearchResult(BaseModel):
    query: str
    zip_code: str
    total: int
    offers: list[Offer]
