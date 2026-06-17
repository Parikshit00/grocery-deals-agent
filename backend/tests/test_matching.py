from app.schemas.offer import Offer
from app.services.matching import rank_offers


def _offer(name: str, price: float) -> Offer:
    return Offer(source="t", product_name=name, price=price, retailer="X")


def test_rank_filters_irrelevant_and_sorts_cheapest_first():
    offers = [_offer("Butter", 1.39), _offer("Apfel", 0.50), _offer("Bio Butter", 0.99)]
    ranked = rank_offers("butter", offers, top=5)
    assert [o.product_name for o in ranked] == ["Bio Butter", "Butter"]


def test_rank_respects_top_limit():
    offers = [_offer(f"Butter {i}", float(i)) for i in range(10)]
    assert len(rank_offers("butter", offers, top=3)) == 3


def test_discount_pct_computed():
    assert Offer(source="t", product_name="x", price=0.66, old_price=1.49).discount_pct == 56
    assert Offer(source="t", product_name="x", price=1.0).discount_pct is None
