from __future__ import annotations

import pytest

from app.schemas.basket import Item, PlanStore
from app.schemas.offer import Offer
from app.services.optimizer import Candidate, cost, plan


def _offer(chain: str = "a", price: int | None = 100, qty: int | None = 250, unit: str = "g",
           **kw) -> Offer:
    return Offer(chain=chain, product_name=kw.pop("name", "x"), price_cents=price, pack_qty=qty,
                 pack_unit=unit if qty else None, source="feed", price_check="feed", **kw)


def _cand(chain: str = "a", **kw) -> Candidate:
    return Candidate(PlanStore(chain=chain, store=f"{chain} store", km=1.0), _offer(chain, **kw))


@pytest.mark.parametrize(
    ("item", "offer", "packs", "total", "unit_price", "noted"),
    [
        (Item(name="Hack", qty=2100, unit="g"), {"price": 300, "qty": 700}, 3, 900, 429, False),
        (Item(name="Milch", qty=2), {"price": 99, "qty": 1000, "unit": "ml"}, 2, 198, 99, False),
        (Item(name="Butter"), {"price": 199, "qty": 250}, 1, 199, 796, True),
        (Item(name="Eier", qty=10, unit="stk"), {"price": 150, "qty": 500}, 1, 150, 300, True),
        (Item(name="Mehl", qty=500, unit="g"), {"price": 80, "qty": None}, 1, 80, None, True),
        # min_qty without a bundle price: at least min_qty packs at the per-pack price
        (Item(name="Joghurt", qty=150, unit="g"),
         {"price": 50, "qty": 150, "min_qty": 2}, 2, 100, 333, False),
        # "3 for 2.97" bundle: always multiples of min_qty at the bundle price
        (Item(name="Saft", qty=4000, unit="ml"),
         {"price": 99, "qty": 1000, "unit": "ml", "min_qty": 3, "bundle_price_cents": 297},
         6, 594, 99, False),
        (Item(name="Saft", qty=1000, unit="ml"),
         {"price": 99, "qty": 1000, "unit": "ml", "min_qty": 3, "bundle_price_cents": 296},
         3, 296, 99, False),
    ],
)
def test_pack_math(item, offer, packs, total, unit_price, noted):
    m = cost(item, _cand(**offer), loyalty=False)
    assert m is not None
    assert (m.packs, m.cost_cents, m.unit_price_cents) == (packs, total, unit_price)
    assert (m.note is not None) is noted


def test_loyalty_only_when_on():
    c = _cand(price=500, loyalty_price_cents=450)
    assert cost(Item(name="x"), c, loyalty=False).cost_cents == 500  # type: ignore[union-attr]
    assert cost(Item(name="x"), c, loyalty=True).cost_cents == 450  # type: ignore[union-attr]


def test_card_only_offer_needs_loyalty():
    c = _cand(price=None, loyalty_price_cents=139)
    assert cost(Item(name="x"), c, loyalty=False) is None
    assert cost(Item(name="x"), c, loyalty=True).cost_cents == 139  # type: ignore[union-attr]
    _, single, split, gaps = plan([Item(name="x")], [[c]], 2, loyalty=False)
    assert (single, split, gaps) == ([], None, ["x"])


ITEMS = [Item(name="Butter"), Item(name="Milch"), Item(name="Hack")]
# Butter: a 199, b 129, c 150; Milch: a 99, b 120; Hack: only at c (priciest store), 600.
CANDS = [
    [_cand("a", price=199), _cand("b", price=129), _cand("c", price=150)],
    [_cand("a", price=99), _cand("b", price=120)],
    [_cand("c", price=600)],
]


def test_single_store_plans_sorted_and_priciest_only_item_kept():
    _, single, _, gaps = plan(ITEMS, CANDS, 2, loyalty=False)
    assert [(p.stores[0].chain, len(p.covered), p.total_cents) for p in single] == [
        ("b", 2, 249), ("a", 2, 298), ("c", 2, 750)]
    assert single[2].covered == ["Butter", "Hack"] and single[2].missing == ["Milch"]
    assert gaps == []


@pytest.mark.parametrize(
    ("max_stores", "chains", "total", "covered", "saves"),
    [
        (1, ["b"], 249, 2, 0),  # same as the best single plan
        (2, ["c", "a"], 849, 3, None),  # full coverage, no single plan to compare with
        (3, ["b", "a", "c"], 828, 3, None),
    ],
)
def test_split_max_stores(max_stores, chains, total, covered, saves):
    _, _, split, _ = plan(ITEMS, CANDS, max_stores, loyalty=False)
    assert split is not None
    assert sorted(s.chain for s in split.stores) == sorted(chains)
    assert (split.total_cents, len(split.covered), split.saves_cents) == (total, covered, saves)


def test_saves_only_against_equal_coverage():
    items = [Item(name="Butter"), Item(name="Milch")]
    cands = [[_cand("a", price=199), _cand("b", price=129)],
             [_cand("a", price=99), _cand("b", price=120)]]
    _, single, split, _ = plan(items, cands, 2, loyalty=False)
    assert split is not None and split.total_cents == 228
    assert split.saves_cents == min(p.total_cents for p in single) - 228 == 21


def test_split_prefers_fewer_stores_on_tie():
    items = [Item(name="Butter")]
    cands = [[_cand("a", price=129), _cand("b", price=129)]]
    _, _, split, _ = plan(items, cands, 2, loyalty=False)
    assert split is not None and len(split.stores) == 1


def test_gaps_and_top3():
    items = [Item(name="Butter", qty=500, unit="g"), Item(name="Safran")]
    cands = [[_cand("a", price=199), _cand("b", price=300, qty=500), _cand("c", price=150),
              _cand("d", price=100, qty=100)], []]
    tops, _, _, gaps = plan(items, cands, 2, loyalty=False)
    assert gaps == ["Safran"]
    assert [m.chain for m in tops[0]] == ["b", "c", "a"]
    assert tops[1] == []
