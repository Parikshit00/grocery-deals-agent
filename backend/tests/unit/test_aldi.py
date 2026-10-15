"""Aldi Süd tier 1 against trimmed real responses recorded Wednesday 2026-10-14 (KW 42): the
offers page's leaflet links, spreads.json of this week's (kw42-26-op-mp) and next week's
(kw43-26-op) leaflet, and hotspots of three spreads; plus the Nord/Süd postcode table."""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.agents import aldi
from app.agents.base import Blocked, Leaflet, NotCovered, ScanCtx, StepFailed, Store

FIX = Path(__file__).parents[1] / "fixtures" / "aldi"
WEDNESDAY = date(2026, 10, 14)
SUNDAY = date(2026, 10, 18)
LISTING = (FIX / "angebote.html").read_text(encoding="utf-8")
STORE = Store(id=None, name="Aldi Süd", address="", lat=None, lon=None, edition="sued",
              located=False)
REDIRECT = "//prospekt.aldi-sued.de/kw42-26-op-mp/"  # the root's Location, protocol-relative
OFFLINE = "<html><head><title>Diese Publikation ist offline</title></head></html>"


def _fix(name: str) -> object:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


ROUTES: dict[str, object] = {
    "/kw42-26-op-mp/spreads.json": _fix("spreads_kw42-26-op-mp.json"),
    "/kw43-26-op/spreads.json": _fix("spreads_kw43-26-op.json"),
    "/kw42-26-op-mp/page/1/hotspots_data.json": [],
    "/kw42-26-op-mp/page/10-11/hotspots_data.json": _fix("hotspots_kw42-26-op-mp_10-11.json"),
    "/kw42-26-op-mp/page/24-25/hotspots_data.json": _fix("hotspots_kw42-26-op-mp_24-25.json"),
    "/kw43-26-op/page/1/hotspots_data.json": [],
    "/kw43-26-op/page/2-3/hotspots_data.json": _fix("hotspots_kw43-26-op_2-3.json"),
}


def _ctx(today: date = WEDNESDAY, plz: str = "80331", **override: object) -> tuple[ScanCtx, list]:
    """A ScanCtx whose client answers from the fixtures; `override` maps paths to responses."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        seen.append(path)
        if path in override:
            return override[path]  # type: ignore[return-value]
        if request.url.host == "www.aldi-sued.de" and path == "/angebote":
            return httpx.Response(200, text=LISTING)
        if path == "/":
            return httpx.Response(302, headers={"location": REDIRECT})
        if path in ROUTES:
            return httpx.Response(200, json=ROUTES[path])
        return httpx.Response(404)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True)
    return ScanCtx(http=http, plz=plz, lat=48.14, lon=11.57, today=today), seen


@pytest.mark.parametrize(("plz", "sued"), [
    ("45307", False),  # Essen
    ("45476", True),  # Mülheim an der Ruhr
    ("80331", True),
    ("70173", True),
    ("20095", False),
    ("10115", False),
    ("57080", True),  # Siegen-Eiserfeld: a Süd PLZ in a Nord prefix
    ("51643", False),  # Gummersbach: a Nord PLZ in a Süd prefix
])
def test_plz_table(plz, sued):
    assert aldi.is_sued(plz) is sued


def test_stores_nord_is_not_covered_and_sued_is_unlocated():
    ctx, seen = _ctx(plz="45307")
    with pytest.raises(NotCovered, match=r"^Aldi Nord is not covered\.$"):
        asyncio.run(aldi.stores(ctx))
    ctx, seen = _ctx(plz="45476")
    assert asyncio.run(aldi.stores(ctx)) == [STORE]
    assert seen == []  # no request: the edition is known, no store finder is used


def test_slugs_from_listing_and_redirect():
    assert aldi.parse_slugs(REDIRECT + " " + LISTING) == [
        "kw42-26-op-mp", "kw43-26-op", "kw44-26-vop"]
    assert aldi.slug_monday("kw42-26-op-mp") == date(2026, 10, 12)
    assert aldi.slug_monday("kw53-26-op") == date(2026, 12, 28)  # ISO year 2026 has 53 weeks
    assert aldi.slug_monday("kw53-27-op") is None


def test_leaflets_this_and_next_week():
    ctx, seen = _ctx()
    this, nxt = asyncio.run(aldi.leaflets(ctx, STORE))
    assert this == Leaflet(
        key="2026-W42", title="Prospekt KW 42", kind="main",
        valid_from=date(2026, 10, 12), valid_to=date(2026, 10, 17), dates_source="text",
        source_url="https://prospekt.aldi-sued.de/kw42-26-op-mp/")
    assert (nxt.key, nxt.kind, nxt.valid_from, nxt.valid_to, nxt.dates_source) == (
        "2026-W43", "preview", date(2026, 10, 19), date(2026, 10, 24), "text")
    assert "/kw44-26-vop/spreads.json" not in seen  # the week after next is not fetched


def test_one_leaflet_per_week_the_redirect_first():
    listing = LISTING.replace("kw43-26-op", "kw42-26-op")
    ctx, seen = _ctx(**{"/angebote": httpx.Response(200, text=listing)})
    assert [lf.source_url for lf in asyncio.run(aldi.leaflets(ctx, STORE))] == [
        "https://prospekt.aldi-sued.de/kw42-26-op-mp/"]
    assert "/kw42-26-op/spreads.json" not in seen


@pytest.mark.parametrize("answer", [httpx.Response(404), httpx.Response(200, text=OFFLINE)])
def test_offline_slug_is_skipped(answer):
    ctx, _ = _ctx(**{"/kw43-26-op/spreads.json": answer})
    assert [lf.key for lf in asyncio.run(aldi.leaflets(ctx, STORE))] == ["2026-W42"]


def test_listing_failure_keeps_the_redirect():
    ctx, _ = _ctx(**{"/angebote": httpx.Response(500)})
    assert [lf.key for lf in asyncio.run(aldi.leaflets(ctx, STORE))] == ["2026-W42"]


def test_no_slug_at_all_fails():
    ctx, _ = _ctx(**{"/angebote": httpx.Response(500), "/": httpx.Response(200, text="")})
    with pytest.raises(StepFailed):
        asyncio.run(aldi.leaflets(ctx, STORE))


def test_sunday_drops_the_finished_week():
    ctx, _ = _ctx(today=SUNDAY)
    (only,) = asyncio.run(aldi.leaflets(ctx, STORE))
    assert (only.key, only.kind) == ("2026-W43", "preview")


def test_unprinted_dates_end_where_the_next_leaflet_starts():
    spreads = _fix("spreads_kw42-26-op-mp.json")
    assert isinstance(spreads, list)
    spreads[0]["pages"][0]["text"] = "TIEFPREIS-HIGHLIGHT"
    ctx, _ = _ctx(**{"/kw42-26-op-mp/spreads.json": httpx.Response(200, json=spreads)})
    this, _ = asyncio.run(aldi.leaflets(ctx, STORE))
    assert (this.valid_from, this.valid_to, this.dates_source) == (
        date(2026, 10, 12), date(2026, 10, 18), "derived")


def test_acquire_maps_hotspots():
    ctx, _ = _ctx()
    this, _ = asyncio.run(aldi.leaflets(ctx, STORE))
    got = asyncio.run(aldi.acquire(ctx, STORE, this))
    assert (got.tier, got.complete, got.pages) == (1, True, [])
    by_name = {o.product_name: o for o in got.offers}
    assert sorted(by_name) == ["Emmentaler", "Espresso Cremoso oder Caffè Crema & Aroma",
                               "Joghurt", "Milk & Crisp"]
    milk = by_name["Milk & Crisp"]  # printed: -15 %, 1.69 instead of 1.99, kg-Preis 7.99
    assert (milk.price_cents, milk.old_price_cents, milk.discount_pct) == (169, 199, 15)
    assert (milk.brand, milk.pack_text, milk.reference_printed) == (
        "CHOCEUR", "je 9 x 23,5 g = 211,5-g-Packung", "kg-Preis 7.99")
    assert (milk.page, milk.valid_from, milk.valid_to) == (11, date(2026, 10, 12),
                                                          date(2026, 10, 17))
    assert (milk.chain, milk.source, milk.price_check) == ("aldi", "feed", "feed")
    assert by_name["Emmentaler"].page == 10
    yoghurt = by_name["Joghurt"]  # one price only: the offer price, valid from Friday
    assert (yoghurt.price_cents, yoghurt.old_price_cents, yoghurt.valid_from, yoghurt.page) == (
        89, None, date(2026, 10, 16), 25)
    # "KAUFE 6 + 2 Gratis": 65.94 for 8 packs (printed kg-Preis 8.24), Einzelpreis 10.99
    coffee = by_name["Espresso Cremoso oder Caffè Crema & Aroma"]
    assert (coffee.price_cents, coffee.bundle_price_cents, coffee.min_qty, coffee.old_price_cents,
            coffee.condition) == (824, 6594, 8, 1099, "KAUFE 6 + 2 Gratis")
    assert (milk.min_qty, milk.bundle_price_cents, milk.condition) == (1, None, None)


def test_a_failed_spread_leaves_the_leaflet_partial():
    ctx, _ = _ctx(**{"/kw42-26-op-mp/page/24-25/hotspots_data.json": httpx.Response(500)})
    this, _ = asyncio.run(aldi.leaflets(ctx, STORE))
    got = asyncio.run(aldi.acquire(ctx, STORE, this))
    assert not got.complete
    assert {o.product_name for o in got.offers} == {"Milk & Crisp", "Emmentaler"}


def test_a_silent_feed_fails_instead_of_going_to_the_vlm():
    paths = [p for p in ROUTES if p.startswith("/kw42-26-op-mp/page/")]
    ctx, _ = _ctx(**dict.fromkeys(paths, httpx.Response(503)))
    this, _ = asyncio.run(aldi.leaflets(ctx, STORE))
    with pytest.raises(StepFailed, match="feed did not answer"):
        asyncio.run(aldi.acquire(ctx, STORE, this))


def test_a_forbidden_spread_is_blocked():
    ctx, _ = _ctx(**{"/kw42-26-op-mp/page/10-11/hotspots_data.json": httpx.Response(403)})
    this, _ = asyncio.run(aldi.leaflets(ctx, STORE))
    with pytest.raises(Blocked):
        asyncio.run(aldi.acquire(ctx, STORE, this))


def test_unpriced_preview_goes_to_the_vlm():
    ctx, _ = _ctx()
    _, nxt = asyncio.run(aldi.leaflets(ctx, STORE))
    got = asyncio.run(aldi.acquire(ctx, STORE, nxt))  # its hotspots are two recipes and a link
    assert (got.tier, got.offers, got.complete, len(got.pages)) == (2, [], False, 3)
    first = got.pages[0]
    assert first.image_url.startswith("https://prospekt.aldi-sued.de/resize/")
    assert "/fit-in/903x1594/" in first.image_url
    assert first.text and first.text.startswith("Gültig von Mo. 19.10.")


def test_feed_gold_groups_by_page():
    ctx, _ = _ctx()
    this, _ = asyncio.run(aldi.leaflets(ctx, STORE))
    gold = asyncio.run(aldi.feed_gold(ctx, STORE, this))
    # pages 1, 10, 11, 24, 25 in the trimmed fixture: the VLM tier's indexes 0 to 4
    assert [(index, len(offers)) for index, offers in gold] == [(1, 1), (2, 1), (4, 2)]


def test_forbidden_is_blocked():
    ctx, _ = _ctx(**{"/angebote": httpx.Response(403)})
    with pytest.raises(Blocked):
        asyncio.run(aldi.leaflets(ctx, STORE))


def test_first_day_label_across_new_year():
    assert aldi._day("2.1.", date(2026, 12, 28)) == date(2027, 1, 2)
    assert aldi._day("15.10.", date(2026, 10, 12)) == date(2026, 10, 15)
    assert aldi._day(None, date(2026, 10, 12)) == date(2026, 10, 12)
