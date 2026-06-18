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


# Semantic ranking: keep offers close to the query in embedding space, then cheapest-first.
# The cutoff is relative (best score minus a margin) with an absolute floor, so it adapts per query.
_SCORE_FLOOR = 0.45
_SCORE_MARGIN = 0.10


def _offer_text(offer: Offer) -> str:
    return offer.product_name or ""


def semantic_rank(query: str, offers: list[Offer], top: int = 20) -> list[Offer]:
    if not offers:
        return []
    from app.core.embeddings import embed

    vectors = embed([query, *[_offer_text(o) for o in offers]])
    query_vec = vectors[0]
    sims = [float(vectors[i + 1] @ query_vec) for i in range(len(offers))]
    cutoff = max(_SCORE_FLOOR, max(sims) - _SCORE_MARGIN)
    relevant = [o for o, s in zip(offers, sims, strict=True) if s >= cutoff]
    relevant.sort(key=lambda o: (o.price is None, o.price if o.price is not None else 0.0))
    return relevant[:top]
