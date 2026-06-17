"""Rank and filter offers for a requested item (cheapest first)."""
from __future__ import annotations

import re
import unicodedata

from app.schemas.offer import Offer


def _normalize(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9 ]+", " ", text)


def rank_offers(item: str, offers: list[Offer], top: int = 5) -> list[Offer]:
    tokens = [t for t in _normalize(item).split() if len(t) >= 3]

    def relevant(offer: Offer) -> bool:
        if not tokens:
            return True
        parts = [offer.product_name, offer.description, offer.brand]
        haystack = _normalize(" ".join(p for p in parts if p))
        return any(token in haystack for token in tokens)

    pool = [o for o in offers if relevant(o)]
    pool.sort(key=lambda o: (o.price is None, o.price if o.price is not None else 0.0))
    return pool[:top]
