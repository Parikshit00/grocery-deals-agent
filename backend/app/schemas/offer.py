"""The offer record shared by chain modules, the cache and the basket.

Money is integer cents and pack sizes are integer base units (g, ml, stk) end to end; only the
UI formats them.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, computed_field

Category = Literal["food", "drinks", "drugstore", "household", "nonfood"]
PackUnit = Literal["g", "ml", "stk"]
Source = Literal["feed", "vlm"]
# feed: structured feed (exact); text: VLM price found in the page text; mismatch: VLM price not
# found in a page that has price text (excluded from plans); image_only: page has no price text.
PriceCheck = Literal["feed", "text", "mismatch", "image_only"]


class Offer(BaseModel):
    chain: str
    product_name: str
    brand: str | None = None
    description: str | None = None
    category: Category | None = None
    price_cents: int | None = None
    old_price_cents: int | None = None
    loyalty_price_cents: int | None = None  # price with the chain's app / card (chain.LOYALTY)
    pack_text: str | None = None  # as printed, e.g. "6 x 1,5 l"
    pack_qty: int | None = None  # in pack_unit base units, e.g. 9000
    pack_unit: PackUnit | None = None
    reference_price_cents: int | None = None  # per kg, l or piece, computed in code
    reference_printed: str | None = None  # as printed, e.g. "1 kg = 3,98"
    condition: str | None = None  # e.g. "ab 2 Stück", "nur mit App"
    min_qty: int = 1
    # Multi-buy ("3 + 1 gratis" for 2.97): exact price of min_qty packs, while price_cents
    # is the rounded per-pack price used for unit comparisons.
    bundle_price_cents: int | None = None
    valid_from: date | None = None  # per offer, e.g. "ab Donnerstag" sections
    valid_to: date | None = None
    page: int | None = None
    image_url: str | None = None
    source: Source
    price_check: PriceCheck

    @computed_field  # type: ignore[prop-decorator]
    @property
    def discount_pct(self) -> int | None:
        if self.old_price_cents and self.price_cents and self.old_price_cents > self.price_cents:
            return round((1 - self.price_cents / self.old_price_cents) * 100)
        return None
