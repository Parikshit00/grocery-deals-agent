"""Rank and filter offers for a requested item (cheapest first)."""
from __future__ import annotations

from app.schemas.offer import Offer

# Semantic ranking: keep offers close to the query in embedding space, then cheapest-first.
# The cutoff is relative (best score minus a margin) with an absolute floor, so it adapts per query.
_SCORE_FLOOR = 0.45
_SCORE_MARGIN = 0.10


def _offer_text(offer: Offer) -> str:
    return offer.product_name or ""


def semantic_rank(query: str, offers: list[Offer], top: int = 20) -> list[Offer]:
    if not offers:
        return []
    from app.clients.embeddings import embed

    vectors = embed([query, *[_offer_text(o) for o in offers]])
    query_vec = vectors[0]
    sims = [float(vectors[i + 1] @ query_vec) for i in range(len(offers))]
    cutoff = max(_SCORE_FLOOR, max(sims) - _SCORE_MARGIN)
    relevant = [o for o, s in zip(offers, sims, strict=True) if s >= cutoff]
    relevant.sort(key=lambda o: (o.price is None, o.price if o.price is not None else 0.0))
    return relevant[:top]
