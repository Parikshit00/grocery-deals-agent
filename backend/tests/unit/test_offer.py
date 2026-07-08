"""Offer schema: the computed discount field."""
from app.schemas.offer import Offer


def _offer(price=None, old_price=None):
    return Offer(source="t", product_name="x", price=price, old_price=old_price)


def test_discount_pct_rounds():
    assert _offer(price=1.99, old_price=2.99).discount_pct == 33


def test_no_discount_without_old_price():
    assert _offer(price=1.99).discount_pct is None


def test_no_discount_when_old_price_not_higher():
    assert _offer(price=1.99, old_price=1.99).discount_pct is None
    assert _offer(price=1.99, old_price=1.49).discount_pct is None


def test_no_discount_without_price():
    assert _offer(old_price=2.99).discount_pct is None
