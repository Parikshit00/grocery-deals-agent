"""Pure basket math: cost accepted offers for the needed amounts, then plan where to shop.

Integer cents and integer base units (g, ml, stk) throughout. The LLM never computes a price.
"""
from __future__ import annotations

import itertools
from dataclasses import dataclass

from app.schemas.basket import Item, Match, Plan, PlanLine, PlanStore
from app.schemas.offer import Offer
from app.services.offers import reference_cents


@dataclass(frozen=True)
class Candidate:
    """An offer the matcher accepted for an item, at the chain's chosen store."""

    store: PlanStore
    offer: Offer


def cost(item: Item, cand: Candidate, loyalty: bool) -> Match | None:
    """The match for buying `item` with this offer, or None when the price does not apply.

    Card/app prices count only with `loyalty`; card-only offers (no public price) need it.
    A multi-buy with a bundle price is bought in multiples of min_qty at the bundle price.
    """
    o = cand.offer
    price = o.price_cents
    use_loyalty = loyalty and o.loyalty_price_cents is not None and (
        price is None or o.loyalty_price_cents < price)
    if use_loyalty:
        price = o.loyalty_price_cents
    if price is None:
        return None
    note = None
    if item.qty is None:
        need_packs, note = 1, "amount unknown: priced as one pack"
    elif item.unit is None:
        need_packs = item.qty  # a bare count ("Milch 2x") is a number of packs
    elif o.pack_qty and o.pack_unit == item.unit:
        need_packs = -(-item.qty // o.pack_qty)
    else:
        need_packs, note = 1, "pack size not comparable: priced as one pack"
    bundle = o.bundle_price_cents if o.min_qty > 1 and not use_loyalty else None
    if bundle:
        packs = -(-max(need_packs, o.min_qty) // o.min_qty) * o.min_qty
        total = bundle * packs // o.min_qty
        unit = reference_cents(bundle, o.pack_qty * o.min_qty if o.pack_qty else None,
                               o.pack_unit)
    else:
        packs = max(need_packs, o.min_qty)
        total = price * packs
        unit = reference_cents(price, o.pack_qty, o.pack_unit)
    return Match(chain=cand.store.chain, store=cand.store.store, km=cand.store.km, offer=o,
                 packs=packs, cost_cents=total, unit_price_cents=unit, note=note)


def best(matches: list[Match], n: int = 3) -> list[Match]:
    """The n cheapest by unit price (unknown unit price last, then by cost)."""
    return sorted(matches, key=lambda m: (m.unit_price_cents is None,
                                          m.unit_price_cents or 0, m.cost_cents))[:n]


def _plan(kind: str, names: list[str], per_item: list[dict[str, Match]],
          chains: tuple[str, ...], stores: dict[str, PlanStore]) -> Plan:
    """Each item at its cheapest store among `chains`."""
    lines = []
    for name, by_chain in zip(names, per_item, strict=True):
        options = [by_chain[c] for c in chains if c in by_chain]
        if options:
            lines.append(PlanLine(item=name, match=min(options, key=lambda m: m.cost_cents)))
    used = list(dict.fromkeys(line.match.chain for line in lines)) or list(chains)
    covered = [line.item for line in lines]
    return Plan(kind=kind, stores=[stores[c] for c in used],  # type: ignore[arg-type]
                total_cents=sum(line.match.cost_cents for line in lines), covered=covered,
                missing=[n for n in names if n not in covered], lines=lines)


def plan(
    items: list[Item],
    candidates: list[list[Candidate]],
    max_stores: int,
    loyalty: bool,
) -> tuple[list[list[Match]], list[Plan], Plan | None, list[str]]:
    """(top 3 per item, single-store plans, split plan, gaps); candidates align with items.

    Single-store plans sort by coverage desc then total; the split plan is the brute-force
    best over every combination of up to max_stores stores (max coverage, then min total,
    then fewest stores). It saves money only against a single-store plan covering exactly
    the same items.
    """
    names = [i.name for i in items]
    stores: dict[str, PlanStore] = {}
    per_item: list[dict[str, Match]] = []
    tops: list[list[Match]] = []
    for item, cands in zip(items, candidates, strict=True):
        matches: list[Match] = []
        cheapest: dict[str, Match] = {}
        for c in cands:
            if (m := cost(item, c, loyalty)) is None:
                continue
            matches.append(m)
            stores[m.chain] = c.store
            if m.chain not in cheapest or m.cost_cents < cheapest[m.chain].cost_cents:
                cheapest[m.chain] = m
        per_item.append(cheapest)
        tops.append(best(matches))
    gaps = [n for n, by_chain in zip(names, per_item, strict=True) if not by_chain]
    single = sorted((_plan("single", names, per_item, (c,), stores) for c in stores),
                    key=lambda p: (-len(p.covered), p.total_cents))
    split: Plan | None = None
    for size in range(1, min(max_stores, len(stores)) + 1):
        for combo in itertools.combinations(sorted(stores), size):
            p = _plan("split", names, per_item, combo, stores)
            key = (-len(p.covered), p.total_cents, len(p.stores))
            if split is None or key < (-len(split.covered), split.total_cents, len(split.stores)):
                split = p
    if split is not None:
        same = [s.total_cents for s in single if s.covered == split.covered]
        split.saves_cents = same[0] - split.total_cents if same else None
    return tops, single, split, gaps
