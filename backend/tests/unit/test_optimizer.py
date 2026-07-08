from app.services.optimizer import build_baskets


def _o(retailer: str, price: float) -> dict:
    return {"source": "t", "product_name": "x", "retailer": retailer, "price": price}


def test_cross_store_picks_cheapest_per_item():
    results = [
        {"item": "butter", "offers": [_o("REWE", 1.39), _o("Lidl", 0.99)]},
        {"item": "milch", "offers": [_o("Lidl", 0.79), _o("Aldi", 0.69)]},
    ]
    baskets = build_baskets(results)
    assert baskets.cross_store.total == round(0.99 + 0.69, 2)
    assert baskets.cross_store.coverage == 2
    assert {line.offer.retailer for line in baskets.cross_store.lines} == {"Lidl", "Aldi"}


def test_single_store_prefers_coverage_then_total():
    results = [
        {"item": "butter", "offers": [_o("REWE", 1.39), _o("Lidl", 1.10)]},
        {"item": "milch", "offers": [_o("Lidl", 0.79)]},
    ]
    baskets = build_baskets(results)
    assert baskets.single_store.store == "Lidl"
    assert baskets.single_store.coverage == 2
    assert baskets.single_store.total == round(1.10 + 0.79, 2)


def test_missing_items_tracked():
    baskets = build_baskets([{"item": "butter", "offers": []}])
    assert baskets.cross_store.missing == ["butter"]
    assert baskets.cross_store.coverage == 0
