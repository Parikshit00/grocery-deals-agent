"""Penny tier 1 against trimmed real responses recorded Monday 2026-10-12 (KW 42; offers of region
15A-04-80): market list, offers page, offer tiles, and leaflets per week and region."""
from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.agents import penny
from app.agents.base import Blocked, ScanCtx, StepFailed, Store, iso_key
from app.schemas.offer import Offer

FIX = Path(__file__).parents[1] / "fixtures" / "penny"
MONDAY = date(2026, 10, 12)  # KW 42
FRIDAY = date(2026, 10, 16)
SATURDAY = date(2026, 10, 17)
PAGE = (FIX / "angebote.html").read_text(encoding="utf-8")
MARKETS = json.loads((FIX / "market.json").read_text(encoding="utf-8"))
# Next week's categories are listed from Friday. The recording is from a Monday, so this block is
# built as Penny's own script selects it: ".tabs__content--next-week .js-category-section".
NEXT_WEEK = (
    '<div class="week-offers tabs__content tabs__content--next-week" data-week="next">'
    '<section id="ab-montag--top-angebote" data-period-category="false"'
    ' data-category-name="top-angebote"></section>'
    '<section id="ab-donnerstag--douceur" data-period-category="false"'
    ' data-category-name="douceur"></section></div>'
)
FRIDAY_PAGE = PAGE.replace("<h2 ", NEXT_WEEK + "<h2 ", 1)


def _tiles(slug: str) -> list[dict]:
    return json.loads((FIX / "offers" / f"{slug}.json").read_text(encoding="utf-8"))["offerTiles"]


def _offers(slug: str, start: date = MONDAY) -> dict:
    return {o.product_name: o for o in penny.parse_offers(_tiles(slug), slug, start, SATURDAY)}


def _sekt(**changes) -> Offer:
    """The real FREIXENET Sekt tile with some fields changed."""
    tile = {**_tiles("top-angebote")[6], **changes}
    [offer] = penny.parse_offers([tile], "top-angebote", MONDAY, SATURDAY)
    return offer


def _store(market_id: str) -> Store:
    return next(s for s in penny.parse_markets(MARKETS, MONDAY) if s.id == market_id)


def test_markets_become_stores_with_their_selling_region():
    stores = {s.id: s for s in penny.parse_markets(MARKETS, MONDAY)}
    assert stores["430153"] == Store(
        id="430153", name="Penny Gärtnerplatz", address="Klenzestr. 26, 80469 München",
        lat=48.13148, lon=11.57541, edition="15A-04-80",
    )
    assert stores["1930734"].edition == "15A-02-35"  # closed in 2021, open now
    assert "8546611" not in stores  # no coordinates
    assert "830797" not in stores  # closed 11.10. to 22.10.2026
    assert "830797" in {s.id for s in penny.parse_markets(MARKETS, date(2026, 10, 23))}


def test_page_gives_this_week_with_printed_dates_and_day_sections():
    [week] = penny.parse_page(PAGE, MONDAY)
    assert week[:6] == ("current", "2026-42", MONDAY, MONDAY, SATURDAY, "text")
    assert len(week.categories) == 25  # the category menu above repeats them
    assert week.categories["top-angebote"] == MONDAY
    assert week.categories["douceur"] == date(2026, 10, 15)  # "ab Donnerstag"
    assert week.categories["framstag"] == FRIDAY  # "ab Freitag"


def test_page_gives_next_week_from_friday():
    this, nxt = penny.parse_page(FRIDAY_PAGE, FRIDAY)
    assert (this.which, len(this.categories)) == ("current", 25)
    assert nxt[:6] == ("next", "2026-43", date(2026, 10, 19), date(2026, 10, 19),
                       date(2026, 10, 24), "text")
    assert nxt.categories == {"top-angebote": date(2026, 10, 19), "douceur": date(2026, 10, 22)}


def test_dates_are_derived_monday_to_saturday_when_not_printed():
    unprinted = re.sub(r'<div class="handout__subline">[^<]*</div>', "", PAGE)
    [week] = penny.parse_page(unprinted, MONDAY)
    assert (week.valid_from, week.valid_to, week.dates_source) == (MONDAY, SATURDAY, "derived")


def test_weeks_across_new_year_keep_their_iso_year():
    page = FRIDAY_PAGE.replace('data-current-week="2026-42" data-next-week="2026-43"',
                               'data-current-week="2026-53" data-next-week="2027-01"')
    this, nxt = penny.parse_page(page, date(2027, 1, 1))
    assert (iso_key(this.monday), this.feed_week, this.valid_to) == (
        "2026-W53", "2026-53", date(2027, 1, 2))
    assert (iso_key(nxt.monday), nxt.feed_week, nxt.dates_source) == (
        "2027-W01", "2027-01", "derived")


def test_cat_map_covers_every_live_category_slug():
    [week] = penny.parse_page(PAGE, MONDAY)
    assert [s for s in week.categories if s.rstrip("0123456789") not in penny.CAT_MAP] == []


def test_offer_with_uvp_and_app_price():
    o = _offers("top-angebote")["PHILADELPHIA Frischkäsezubereitung"]  # soft hyphen, "*" gone
    assert (o.price_cents, o.old_price_cents, o.loyalty_price_cents) == (111, 229, 99)
    assert (o.pack_text, o.reference_printed, o.category, o.condition) == (
        "je 195 g", "1 kg = 5.69", "food", None)
    assert (o.valid_from, o.valid_to, o.chain, o.source, o.price_check) == (
        MONDAY, SATURDAY, "penny", "feed", "feed")
    assert o.image_url and o.image_url.startswith("https://cdn.penny.de/")


def test_teasers_are_skipped_and_plain_offers_keep_their_comparison_price():
    offers = _offers("top-angebote")
    assert len(offers) == 7  # 8 tiles, one is an app teaser
    assert offers["Cherry-Romatomaten"].old_price_cents is None
    sekt = offers["FREIXENET Sekt"]  # the feed prints "je 0,75 I"
    assert (sekt.price_cents, sekt.old_price_cents, sekt.pack_text) == (388, 679, "je 0,75 l")


def test_app_only_offer_has_no_price_without_the_app():
    o = _offers("fleisch-und-wurst")["GUTFRIED Hähnchen-Salami"]
    assert (o.price_cents, o.old_price_cents, o.loyalty_price_cents) == (None, 199, 149)
    assert (o.condition, o.reference_printed) == ("nur mit PENNY App", "1 kg = 14.90")


def test_struck_through_former_prices():
    offers = _offers("fleisch-und-wurst")
    assert len(offers) == 3  # one recipe teaser skipped
    hack = offers["MÜHLENHOF Frisches Rinder-Hackfleisch"]
    assert (hack.price_cents, hack.old_price_cents, hack.loyalty_price_cents) == (499, 579, 479)
    assert hack.image_url is None  # Penny's placeholder image, the photo was not published yet
    assert offers["MÜHLENHOF Frische Hähnchen-Schenkel"].old_price_cents == 899
    mini = _offers("dauerhaft-im-preis-gesenkt")["MÜHLENHOF Mini Wiener"]
    assert (mini.price_cents, mini.old_price_cents) == (239, 249)


def test_from_price_and_special_buy_non_food():
    offers = _offers("haushalt-und-wohnen", date(2026, 10, 15))
    mop = offers["DIRT DEVIL Praktische Reinigungshelfer im MEGA-PACK"]
    assert (mop.price_cents, mop.old_price_cents, mop.condition, mop.pack_text) == (
        299, None, "ab-Preis", "je Packung")  # zero-width spaces dropped
    vac = offers["ROWENTA 2-in-1-Akku-Handstaubsauger X-PERT 5.80RH5A21E0"]
    assert (vac.price_cents, vac.old_price_cents, vac.loyalty_price_cents) == (9999, 18999, 8999)
    assert {o.category for o in offers.values()} == {"nonfood"}
    assert {o.valid_from for o in offers.values()} == {date(2026, 10, 15)}


@pytest.mark.parametrize(("price", "cents"), [
    ("3.88", 388),
    ("0.99\u00b2", 99),  # app prices carry a footnote mark
    ("5.-", 500),  # whole euros: Penny's own tile script strips ".-" from prices
    ("0.00", None),
])
def test_feed_price_formats(price, cents):
    assert _sekt(price=price).price_cents == cents


def test_multi_buy_gets_no_price_while_its_format_is_unknown():
    # No tile had multiBuy set on 2026-10-12 or 2026-10-13; the real Sekt tile stands in.
    o = _sekt(multiBuy=True)
    assert (o.price_cents, o.old_price_cents, o.loyalty_price_cents, o.min_qty) == (
        None, None, None, 1)
    assert (o.condition, o.product_name) == ("Mehrfachkauf, Preis siehe Penny", "FREIXENET Sekt")


@pytest.mark.parametrize(("slug", "kind", "category"), [
    ("getraenke0", "food", "drinks"),  # a numbered repeat of a known slug
    ("pflanzen", "food", "nonfood"),  # plants are typed food
    ("top-angebote", "non-food", "nonfood"),  # Penny's special-buy type wins
    ("neue-aktion", "food", None),  # unknown slug
])
def test_category_from_the_special_buy_type_then_the_slug(slug, kind, category):
    tile = {**_tiles("top-angebote")[0], "type": kind}
    [o] = penny.parse_offers([tile], slug, MONDAY, SATURDAY)
    assert o.category == category


class _Feed:
    """MockTransport handler serving the fixtures; records every requested URL."""

    def __init__(self, page: str = PAGE, markets: object = MARKETS, fail: tuple = (),
                 status: int = 404):
        self.page, self.markets, self.fail, self.status = page, markets, fail, status
        self.urls: list[httpx.URL] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.urls.append(request.url)
        path = request.url.path
        if path == "/.rest/market":
            if isinstance(self.markets, str):
                return httpx.Response(200, text=self.markets)
            return httpx.Response(200, json=self.markets)
        if path == "/angebote":
            return httpx.Response(200, text=self.page)
        slug = path.rsplit("/", 1)[-1]
        if path.startswith("/.rest/offers/by-category/") and slug not in self.fail:
            f = FIX / "offers" / f"{slug}.json"
            body = f.read_bytes() if f.exists() else b'{"offerTiles":[]}'
            return httpx.Response(200, content=body)
        return httpx.Response(self.status)

    def offer_calls(self) -> set[tuple[str, str]]:
        return {(u.path, u.params["region"]) for u in self.urls
                if u.path.startswith("/.rest/offers/")}


@pytest.fixture(autouse=True)
def _empty_cache(monkeypatch):
    monkeypatch.setattr(penny, "_cache", {})


def _run(fn, feed: _Feed, today: date = MONDAY):
    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(feed)) as http:
            return await fn(ScanCtx(http=http, plz="80469", lat=48.13, lon=11.575, today=today))

    return asyncio.run(go())


def _scan(store: Store, pick: int = 0):
    async def go(ctx):
        found = await penny.leaflets(ctx, store)
        return found, await penny.acquire(ctx, store, found[pick])

    return go


def test_market_list_is_fetched_once_and_reused():
    feed = _Feed()

    async def twice(ctx):
        return await penny.stores(ctx), await penny.stores(ctx)

    first, second = _run(twice, feed)
    assert first == second and len(first) == 3
    assert [u.path for u in feed.urls] == ["/.rest/market"]


@pytest.mark.parametrize("markets", [[], "<html>Wartungsarbeiten</html>", {"error": "x"}, ["x"]])
def test_an_empty_or_unreadable_market_list_is_a_failed_step(markets):
    with pytest.raises(StepFailed):
        _run(penny.stores, _Feed(markets=markets))


def test_monday_scan_fetches_every_category_for_the_markets_region():
    feed = _Feed()
    [leaflet], got = _run(_scan(_store("430153")), feed)
    assert (leaflet.key, leaflet.kind, leaflet.valid_from, leaflet.valid_to) == (
        "2026-W42", "offers", MONDAY, SATURDAY)
    assert (leaflet.dates_source, leaflet.source_url) == ("text", MARKETS[0]["flippingBookURL"])
    paths = [u.path for u in feed.urls]
    assert (paths.count("/angebote"), paths.count("/.rest/market")) == (1, 1)
    calls = feed.offer_calls()
    assert len(calls) == 25
    assert {(p.split("/")[4], region) for p, region in calls} == {("2026-42", "15A-04-80")}
    assert (got.tier, got.complete, len(got.offers)) == (1, True, 20)
    by_name = {o.product_name: o for o in got.offers}
    assert by_name["DOUCEUR Dominosteine"].valid_from == date(2026, 10, 15)
    assert by_name["MILPRIMA Haltbare fettarme Milch"].valid_from == FRIDAY


def test_one_failed_category_makes_the_leaflet_incomplete():
    _, got = _run(_scan(_store("430153")), _Feed(fail=("douceur",)))
    assert (got.complete, len(got.offers)) == (False, 17)


def test_a_feed_failing_for_every_category_is_a_failed_step():
    every = tuple(penny.parse_page(PAGE, MONDAY)[0].categories)
    with pytest.raises(StepFailed, match="every category"):
        _run(_scan(_store("430153")), _Feed(fail=every))


def test_a_store_closed_since_the_store_list_was_cached_gets_no_leaflet():
    reopened = next(s for s in penny.parse_markets(MARKETS, date(2026, 10, 23)) if s.id == "830797")
    with pytest.raises(StepFailed, match="closed for renovation until 22.10.2026"):
        _run(_scan(reopened), _Feed())


def test_a_week_id_that_is_no_iso_week_lists_no_week():
    assert penny.parse_page(PAGE.replace('"2026-42"', '"2026-60"'), MONDAY) == []


def test_a_refused_request_blocks_the_chain():
    with pytest.raises(Blocked):
        _run(_scan(_store("430153")), _Feed(fail=("douceur",), status=403))


def test_next_week_is_a_preview_fetched_for_the_region_the_market_moves_to():
    markets = [{**m, "nextWeekSellingRegion": "15A-02-35"} if m["wwIdent"] == "430153" else m
               for m in MARKETS]
    feed = _Feed(page=FRIDAY_PAGE, markets=markets)
    found, got = _run(_scan(_store("430153"), pick=1), feed, today=FRIDAY)
    assert [(lf.key, lf.kind) for lf in found] == [
        ("2026-W42", "offers"), ("2026-W43-15A-02-35", "preview")]
    assert (found[1].valid_from, found[1].source_url) == (
        date(2026, 10, 19), "https://www.penny.de/angebote")
    assert feed.offer_calls() == {
        ("/.rest/offers/by-category/2026-43/top-angebote", "15A-02-35"),
        ("/.rest/offers/by-category/2026-43/douceur", "15A-02-35")}
    assert (got.complete, len(got.offers)) == (False, 10)  # re-read until the week starts

    staying = _Feed(page=FRIDAY_PAGE)  # nextWeekSellingRegion is empty: the region stands in
    found, _ = _run(_scan(_store("430297"), pick=1), staying, FRIDAY)
    assert [lf.key for lf in found] == ["2026-W42", "2026-W43"]
    assert {region for _, region in staying.offer_calls()} == {"15A-04-80"}


def test_a_store_listed_before_a_region_change_gets_a_region_key():
    stale = dataclasses.replace(_store("430153"), edition="15A-02-35")
    feed = _Feed()
    [leaflet], _ = _run(_scan(stale), feed)
    assert leaflet.key == "2026-W42-15A-04-80"
    assert {region for _, region in feed.offer_calls()} == {"15A-04-80"}


def test_sunday_skips_the_finished_week():
    found, _ = _run(_scan(_store("430153")), _Feed(page=FRIDAY_PAGE), today=date(2026, 10, 18))
    assert [(lf.key, lf.kind) for lf in found] == [("2026-W43", "preview")]
