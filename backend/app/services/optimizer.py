"""Cheapest baskets: cross_store = cheapest offer per item; single_store = one retailer,
best coverage then lowest total."""
from __future__ import annotations

from typing import Any

from app.schemas.basket import Basket, BasketLine, Baskets
from app.schemas.offer import Offer


def _cheapest(offers: list[Offer]) -> Offer | None:
    priced = [o for o in offers if o.price is not None]
    return min(priced, key=lambda o: o.price) if priced else None


def build_baskets(results: list[dict[str, Any]]) -> Baskets:
    items: list[tuple[str, list[Offer]]] = [
        (r["item"], [Offer.model_validate(o) for o in r.get("offers", [])]) for r in results
    ]
    all_items = [item for item, _ in items]

    # cross-store: cheapest offer per item, regardless of retailer.
    cross_lines: list[BasketLine] = []
    cross_missing: list[str] = []
    for item, offers in items:
        best = _cheapest(offers)
        if best is not None:
            cross_lines.append(BasketLine(item=item, offer=best))
        else:
            cross_missing.append(item)
    cross = Basket(
        mode="cross_store",
        total=round(sum(line.offer.price or 0.0 for line in cross_lines), 2),
        coverage=len(cross_lines),
        lines=cross_lines,
        missing=cross_missing,
    )

    # single-store: each retailer's cheapest offer per item; pick the best retailer.
    per_store: dict[str, dict[str, Offer]] = {}
    for item, offers in items:
        for offer in offers:
            if offer.price is None or not offer.retailer:
                continue
            by_item = per_store.setdefault(offer.retailer, {})
            if item not in by_item or offer.price < (by_item[item].price or float("inf")):
                by_item[item] = offer

    best_store: str | None = None
    best_key: tuple[int, float] | None = None
    for store, by_item in per_store.items():
        total = sum(o.price or 0.0 for o in by_item.values())
        key = (len(by_item), -total)  # maximize coverage, then minimize total
        if best_key is None or key > best_key:
            best_key, best_store = key, store

    if best_store is not None:
        chosen = per_store[best_store]
        single_lines = [
            BasketLine(item=item, offer=chosen[item]) for item in all_items if item in chosen
        ]
        single = Basket(
            mode="single_store",
            store=best_store,
            total=round(sum(line.offer.price or 0.0 for line in single_lines), 2),
            coverage=len(single_lines),
            lines=single_lines,
            missing=[item for item in all_items if item not in chosen],
        )
    else:
        single = Basket(
            mode="single_store", total=0.0, coverage=0, lines=[], missing=list(all_items)
        )

    return Baskets(cross_store=cross, single_store=single)
