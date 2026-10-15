"""Offer schema: the computed discount field."""
from app.schemas.offer import Offer


def _offer(price=None, old_price=None):
    return Offer(
        chain="t", product_name="x", price_cents=price, old_price_cents=old_price,
        source="feed", price_check="feed",
    )


def test_discount_pct_rounds():
    assert _offer(price=199, old_price=299).discount_pct == 33


def test_no_discount_without_old_price():
    assert _offer(price=199).discount_pct is None


def test_no_discount_when_old_price_not_higher():
    assert _offer(price=199, old_price=199).discount_pct is None
    assert _offer(price=199, old_price=149).discount_pct is None


def test_no_discount_without_price():
    assert _offer(old_price=299).discount_pct is None
