"""Kaufland tier 1 against trimmed real responses recorded 2026-10-12 (München DE8700, Hamburg
DE1760) and 2026-10-14 (München DE8700, offers_DE8700_20261014.html): store finder, the offers
page's embedded JSON, offer windows, next week's preview, card and bundle prices."""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.agents import kaufland
from app.agents.base import Blocked, ScanCtx, StepFailed, Store

FIX = Path(__file__).parents[1] / "fixtures" / "kaufland"


def _html(name: str) -> str:
    return (FIX / f"offers_{name}.html").read_text(encoding="utf-8")


def _page(store_id: str) -> dict:
    return kaufland.parse_page(_html(store_id))


def _offers(store_id: str) -> dict:
    """product name -> (leaflet key, offer) over every window of the page."""
    return {o.product_name: (key, o) for key, (_, acq) in _page(store_id).items()
            for o in acq.offers}


def test_parse_stores():
    data = json.loads((FIX / "storefinder.json").read_text(encoding="utf-8"))
    stores = kaufland.parse_stores(data)
    assert len(stores) == 6
    assert next(s for s in stores if s.id == "DE8700") == Store(
        id="DE8700", name="Kaufland München-Berg am Laim",
        address="Berg-am-Laim-Straße 117, 81673 München", lat=48.1295951, lon=11.6260178,
        edition="DE8700")
    assert kaufland.parse_stores([{**data[0], "lat": "", "lng": ""}]) == []


def test_each_validity_window_is_its_own_leaflet_with_feed_dates():
    page = _page("DE8700")
    got = {key: (lf.kind, lf.valid_from, lf.valid_to, lf.dates_source, a.complete, len(a.offers))
           for key, (lf, a) in page.items()}
    assert got == {
        "2026-W40:1001-1031": ("extra", date(2026, 10, 1), date(2026, 10, 31), "feed", True, 1),
        "2026-W41:1008-1014": ("extra", date(2026, 10, 8), date(2026, 10, 14), "feed", True, 2),
        "2026-W42:1012-1017": ("main", date(2026, 10, 12), date(2026, 10, 17), "feed", True, 13),
        "2026-W42:1015-1017": ("extra", date(2026, 10, 15), date(2026, 10, 17), "feed", True, 2),
        "2026-W42:1016-1017": ("extra", date(2026, 10, 16), date(2026, 10, 17), "feed", True, 1),
        "2026-W43:1019-1024": ("preview", date(2026, 10, 19), date(2026, 10, 24), "feed", False, 2),
    }
    for lf, a in page.values():
        assert all((o.valid_from, o.valid_to) == (lf.valid_from, lf.valid_to) for o in a.offers)


def test_thursday_to_wednesday_theme_week_stays_apart_from_the_main_week():
    page = _page("DE8700")
    theme = [o.product_name for o in page["2026-W41:1008-1014"][1].offers]
    thursday = [o.product_name for o in page["2026-W42:1015-1017"][1].offers]
    assert theme == ["Zwetschgenhälften", "Backzutaten"]
    assert thursday == ["Ital. Tafeltrauben, hell XXL", "Bio-Olivenöl"]
    assert page["2026-W43:1019-1024"][0].title == "Angebote 19.10.-24.10.2026 (Vorschau)"


def test_offer_listed_in_several_sections_is_kept_once_with_its_product_group():
    main = _page("DE8700")["2026-W42:1012-1017"][1].offers
    names = [o.product_name for o in main]
    assert len(names) == len(set(names))
    by_name = {o.product_name: o for o in main}
    assert by_name["Fix, Airfryer oder Würzpaste"].category == "food"
    assert by_name["Weizen-Mixgetränk"].category == "drinks"
    assert by_name["Filets"].category == "drugstore"
    assert by_name["Brotmesser"].category == "household"
    assert _offers("DE8700")["Backzutaten"][1].category is None  # theme section only


def test_offer_fields():
    offers = _offers("DE8700")
    o = offers["Dtsch./ital. Tafeläpfel"][1]
    assert (o.brand, o.price_cents, o.old_price_cents, o.pack_text) == (
        "KANZI", 199, 299, "je 900-g-Packg.")
    assert (o.reference_printed, o.source, o.price_check, o.min_qty) == (
        "1 kg = 2.22", "feed", "feed", 1)
    assert o.image_url and o.image_url.startswith("https://kaufland.media.schwarz/")
    o = offers["Südafrik. Orangen"][1]
    assert (o.brand, o.price_cents, o.old_price_cents) == (None, 199, 349)
    assert offers["Bezüge »STEAM PLUS XXL«"][1].old_price_cents == 2399  # "UVP 23.99"
    # "showPriceOnly": the tile prints "nur 8.49" without the feed's old price 11.99.
    o = offers["Schweinefilet am Stück"][1]
    assert (o.price_cents, o.old_price_cents, o.discount_pct) == (849, None, None)


def test_kaufland_card_prices():
    offers = _offers("DE8700")
    card_only = offers["Dtsch./span. Broccoli"][1]
    assert (card_only.price_cents, card_only.old_price_cents, card_only.loyalty_price_cents) == (
        None, 179, 99)
    both = offers["Der Große Bauer oder Yo&Go"][1]
    assert (both.price_cents, both.old_price_cents, both.loyalty_price_cents) == (49, None, 39)


@pytest.mark.parametrize(("name", "price", "bundle", "old", "n", "condition"), [
    ("Salamistangen", 100, 400, 149, 4, "4 Stück"),  # 4.00 for 4, was 5.96
    ("Backzutaten", 75, 297, 99, 4, "3 + 1 gratis"),  # 2.97 for 4 ("pro Packg. 0.75"), was 3.96
    ("Fix, Airfryer oder Würzpaste", 44, None, None, 1, None),  # single, "nur 0.44"
])
def test_multi_buy_keeps_exact_bundle_price_and_per_pack_price(name, price, bundle, old, n,
                                                                condition):
    o = _offers("DE8700")[name][1]
    assert (o.price_cents, o.bundle_price_cents, o.old_price_cents, o.min_qty, o.condition) == (
        price, bundle, old, n, condition)


def test_preview_with_sunday_display_dates_stays_partial():
    """2026-10-14: half of "Vorwerbung" is displayed 18.10.-24.10., overlapping its sales window
    19.10.-24.10.; it is still next week's preview and must not be cached complete."""
    page = kaufland.parse_page(_html("DE8700_20261014"))
    got = {key: (lf.kind, a.complete, len(a.offers)) for key, (lf, a) in page.items()}
    assert got == {
        "2026-W40:1001-1031": ("extra", True, 1),
        "2026-W41:1008-1014": ("extra", True, 1),
        "2026-W42:1012-1017": ("main", True, 2),
        "2026-W42:1015-1017": ("extra", True, 1),
        "2026-W43:1019-1024": ("preview", False, 4),
    }
    lf, a = page["2026-W43:1019-1024"]
    assert lf.title == "Angebote 19.10.-24.10.2026 (Vorschau)"
    assert all((o.valid_from, o.valid_to) == (date(2026, 10, 19), date(2026, 10, 24))
               for o in a.offers)


def test_theme_week_without_sales_dates_uses_its_printed_window():
    page = kaufland.parse_page(_html("DE8700_20261014"))
    lf, a = page["2026-W41:1008-1014"]  # 307_Asien: "Gültig vom 08.10. bis 14.10."
    o = a.offers[0]
    assert (o.product_name, o.price_cents, o.bundle_price_cents, o.min_qty) == (
        "Instant-Nudeln", 40, 400, 10)
    o = page["2026-W42:1015-1017"][1].offers[0]  # "2 + 1 gratis" for 50.00, was 75.00
    assert (o.price_cents, o.bundle_price_cents, o.old_price_cents, o.min_qty) == (
        1667, 5000, 2500, 3)


def test_offer_without_sales_dates_takes_its_sections_sales_window():
    html = _html("DE8700_20261014")
    start = html.find('{"component":"OfferTemplate"')
    doc = json.JSONDecoder().raw_decode(html, start)[0]
    preview = doc["props"]["offerData"]["cycles"][0]["categories"][-1]
    assert preview["name"] == "Vorwerbung"
    for o in preview["offers"]:
        del o["salesFrom"], o["salesTo"]  # display dates 11.10.-16.10. / 18.10.-24.10. remain
    page = kaufland.parse_page(json.dumps(doc, separators=(",", ":")))
    assert page["2026-W43:1019-1024"][0].kind == "preview"
    assert len(page["2026-W43:1019-1024"][1].offers) == 4
    assert not any(key.endswith(("1011-1016", "1018-1024")) for key in page)


def test_without_main_groups_every_window_is_a_partial_preview():
    html = _html("DE8700_20261014").replace('"main":true', '"main":false')
    page = kaufland.parse_page(html)
    assert {(lf.kind, a.complete) for lf, a in page.values()} == {("preview", False)}


def test_two_stores_get_different_offers():
    munich, hamburg = _offers("DE8700"), _offers("DE1760")
    maggi = "Fix, Airfryer oder Würzpaste"
    assert (munich[maggi][0], munich[maggi][1].price_cents) == ("2026-W42:1012-1017", 44)
    assert (hamburg[maggi][0], hamburg[maggi][1].price_cents) == ("2026-W42:1012-1017", 39)
    assert munich["Bio-Olivenöl"][1].price_cents == 489
    assert hamburg["Bio-Olivenöl"][1].price_cents == 479
    # Same article, sold from Monday in Munich but only from Thursday in Hamburg.
    assert munich["Schweinefilet am Stück"][0] == "2026-W42:1012-1017"
    assert hamburg["Schweinefilet am Stück"][0] == "2026-W42:1015-1017"
    assert "Schinkenhäxle" in hamburg and "Schinkenhäxle" not in munich
    assert _page("DE1760")["2026-W42:1015-1017"][0].kind == "extra"


def test_page_without_offer_data_fails_the_step():
    with pytest.raises(StepFailed):
        kaufland.parse_page("<html><body>Wartungsarbeiten</body></html>")
    with pytest.raises(StepFailed):  # layout drift inside the document
        kaufland.parse_page(_html("DE8700").replace('"offerId"', '"id"'))


@pytest.fixture
def fresh(monkeypatch):
    monkeypatch.setattr(kaufland, "_stores", (0.0, []))
    monkeypatch.setattr(kaufland, "_pages", {})


def _run(handler, today, body):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            ctx = ScanCtx(http=http, plz="81673", lat=48.13, lon=11.62, today=today)
            return await body(ctx)

    return asyncio.run(go())


def test_scan_fetches_store_finder_and_page_once_with_the_store_cookie(fresh):
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request.url.path, request.headers.get("cookie")))
        if request.url.path == "/.klstorefinder.json":
            return httpx.Response(200, content=(FIX / "storefinder.json").read_bytes())
        sid = request.headers["cookie"].removeprefix("x-aem-variant=")
        return httpx.Response(200, content=(FIX / f"offers_{sid}.html").read_bytes())

    async def body(ctx):
        stores = await kaufland.stores(ctx)
        assert await kaufland.stores(ctx) is stores
        store = next(s for s in stores if s.id == "DE8700")
        leaflets = await kaufland.leaflets(ctx, store)
        acquired = [await kaufland.acquire(ctx, store, lf) for lf in leaflets]
        await kaufland.leaflets(ctx, store)  # the next (or forced) scan fetches a fresh page
        return leaflets, acquired

    leaflets, acquired = _run(handler, date(2026, 10, 15), body)
    assert seen == [("/.klstorefinder.json", None),
                    ("/angebote/uebersicht.html", "x-aem-variant=DE8700"),
                    ("/angebote/uebersicht.html", "x-aem-variant=DE8700")]
    # The Thu..Wed theme week ended on Wednesday 14.10.
    assert [lf.key for lf in leaflets] == [
        "2026-W40:1001-1031", "2026-W42:1012-1017", "2026-W42:1015-1017",
        "2026-W42:1016-1017", "2026-W43:1019-1024"]
    assert [a.tier for a in acquired] == [1] * 5 and all(a.offers for a in acquired)


def test_http_403_is_blocked(fresh):
    async def body(ctx):
        return await kaufland.stores(ctx)

    with pytest.raises(Blocked):
        _run(lambda request: httpx.Response(403), date(2026, 10, 12), body)
