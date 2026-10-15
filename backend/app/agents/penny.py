"""Penny (tier 1): markets and regional offer feeds are keyless JSON on www.penny.de.

Stores come from `/.rest/market` (cached in memory for 24 h): every market with coordinates and
its `sellingRegion`, the edition Penny's offers vary by (44 regions). The `/angebote` page names
the current and next week (`data-current-week="2026-42"`), prints each week's dates ("12.10. bis
17.10.", Monday to Saturday) and lists the week's feed categories in day sections
(`ab-donnerstag--douceur`); next week's categories appear from Friday. There is no JSON category
list: Penny's own page script reads the categories from those sections and fetches
`/.rest/offers/by-category/<week>/<category>?region=<region>` once per category, as this module
does. robots.txt allows everything.

A leaflet is one week of one region. Next week uses the market's `nextWeekSellingRegion` (empty
until Penny sets it; the current region stands in) and stays a partial preview until it starts,
so every scan re-reads it until then. When a week's region is not the store's edition (a market
changing region, or a store list cached before the change), the region is appended to the key
("2026-W43-15A-02-35"), so one cached (edition, key) never holds two regions' offers.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple

import httpx

from app.agents.base import (
    Acquired,
    Blocked,
    Leaflet,
    ScanCtx,
    StepFailed,
    Store,
    iso_key,
    parse_validity,
)
from app.core.logging import get_logger
from app.schemas.offer import Category, Offer
from app.services.offers import parse_price_cents

log = get_logger(__name__)

NAME = "penny"
LABEL = "Penny"
LOYALTY = "PENNY App"
DOMAINS = ("www.penny.de", "cdn.penny.de")
TIER = 1  # acquisition tier: 1 structured feed, 2 leaflet pages via the VLM, 3 browser

BASE = "https://www.penny.de"
MARKETS_TTL_S = 24 * 3600.0
PAGE_TTL_S = 600.0  # leaflets() and acquire() of one scan share one page fetch

# Feed category slug -> offer category. Slugs follow the weekly promotions and repeat with a
# digit ("getraenke0"), which is dropped before the lookup; an unknown slug gives None. A tile's
# own type "non-food" (special-buy goods; plants and soap are typed "food") wins over the slug.
CAT_MAP: dict[str, Category] = {
    "top-angebote": "food",
    "obst-und-gemuese": "food",
    "kuehlregal": "food",
    "fleisch-und-wurst": "food",
    "lindenhof": "food",
    "suessigkeiten-und-snacks": "food",
    "weitere-angebote": "food",
    "dauerhaft-im-preis-gesenkt": "food",
    "food-highlights-fuer-alle": "food",
    "taste-of-asia": "food",
    "douceur": "food",
    "sparen-auf-top-marken": "food",
    "framstag": "food",
    "getraenke": "drinks",
    "drogerie-und-haushalt": "drugstore",
    "haushalt-und-wohnen": "nonfood",
    "kochen-und-backen": "nonfood",
    "sport-und-freizeit": "nonfood",
    "weihnachten": "nonfood",
    "pflanzen": "nonfood",
    "pflanzen-mo-sa": "nonfood",
}

_DAYS = ("montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag")
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')
_WEEKS = re.compile(r"<span\b[^>]*\bjs-weeks\b[^>]*>")
# A week's offer list; not the category menu, whose class is "tabs__content--current-week-menu".
_BLOCK = re.compile(r"<div\b[^>]*\btabs__content--(current|next)-week(?![\w-])[^>]*>")
_SECTION = re.compile(r"<section\b[^>]*>")
_SUBLINE = re.compile(r'class="handout__subline">([^<]*)<')
_LITRE = re.compile(r"(?<=\d) I\b")  # the feed writes litres as a capital i: "je 0,75 I"
_INVISIBLE = str.maketrans({"\N{SOFT HYPHEN}": None, "\N{ZERO WIDTH SPACE}": None})
_OFFER_TILES = ("offer", "promo", "promoPlus")  # the other tiles are recipe and app teasers

_cache: dict[str, tuple[float, Any]] = {}  # path -> (expiry, parsed body)


class Week(NamedTuple):
    which: str  # current | next, as the page names its week blocks
    feed_week: str  # the page's week id, used as is in feed paths, e.g. "2026-42"
    monday: date
    valid_from: date
    valid_to: date
    dates_source: str  # text: printed on the page; derived: Monday to Saturday of the ISO week
    categories: dict[str, date]  # feed category slug -> first day its offers are valid


def parse_markets(markets: list[dict], today: date) -> list[Store]:
    """Stores from /.rest/market; skips markets without coordinates or closed for renovation."""
    return [
        Store(
            id=m["wwIdent"],
            name=m["marketName"],
            address=f"{m['streetWithHouseNumber']}, {m['zipCode']} {m['city']}",
            lat=float(m["latitude"]),
            lon=float(m["longitude"]),
            edition=m["sellingRegion"],
        )
        for m in markets
        if m.get("latitude") and m.get("longitude") and not _closed(m, today)
    ]


def parse_page(html: str, today: date) -> list[Week]:
    """The weeks the /angebote page lists categories for: the current one, the next from Friday."""
    span = _WEEKS.search(html)
    ids = _attrs(span.group()) if span else {}
    printed = [r for r in (parse_validity(s, today) for s in _SUBLINE.findall(html)) if r]
    blocks = list(_BLOCK.finditer(html))
    weeks = []
    for i, block in enumerate(blocks):
        feed_week = ids.get(f"data-{block[1]}-week", "")
        m = re.fullmatch(r"(\d{4})-(\d{1,2})", feed_week)
        if not m:
            continue
        try:
            monday = date.fromisocalendar(int(m[1]), int(m[2]), 1)
        except ValueError:
            continue  # not an ISO week: the id format changed
        dates = next((r for r in printed if iso_key(r[0]) == iso_key(monday)), None)
        start, end = dates or (monday, monday + timedelta(days=5))  # the page's "Mo-Sa"
        stop = blocks[i + 1].start() if i + 1 < len(blocks) else len(html)
        categories = {}
        for tag in _SECTION.findall(html, block.end(), stop):
            a = _attrs(tag)
            if a.get("data-period-category") == "false" and a.get("data-category-name"):
                day = re.search(r"\bab-([a-z]+)--", a.get("id", ""))
                offset = _DAYS.index(day[1]) if day and day[1] in _DAYS else 0
                categories[a["data-category-name"]] = max(start, monday + timedelta(days=offset))
        if categories:
            source = "text" if dates else "derived"
            weeks.append(Week(block[1], feed_week, monday, start, end, source, categories))
    return weeks


def parse_offers(tiles: list[dict], slug: str, valid_from: date, valid_to: date) -> list[Offer]:
    """Offers of one category response; recipe and app teaser tiles are skipped."""
    return [
        _offer(tile, slug, valid_from, valid_to)
        for tile in tiles
        if tile.get("primaryType") in _OFFER_TILES
    ]


async def stores(ctx: ScanCtx) -> list[Store]:
    """Every open Penny market with coordinates; the runtime picks the nearest."""
    found = parse_markets(await _markets(ctx), ctx.today)
    if not found:
        raise StepFailed("Penny's market list is empty.")
    return found


async def leaflets(ctx: ScanCtx, store: Store) -> list[Leaflet]:
    """This week unless it is over, and next week once listed, each for that week's region."""
    weeks = parse_page(await _cached(ctx, "/angebote", PAGE_TTL_S), ctx.today)
    if not weeks:
        raise StepFailed("Penny's offers page lists no offer categories.")
    markets = await _markets(ctx)
    market: dict = next((m for m in markets if m.get("wwIdent") == store.id), {})
    if _closed(market, ctx.today):  # the store list is cached longer than the market list
        raise StepFailed(
            f"{store.name} is closed for renovation until {market['temporarilyClosedUntil']}.")
    region = market.get("sellingRegion") or store.edition
    out = []
    for week in weeks:
        if week.valid_to < ctx.today:
            continue  # Sunday: the Monday to Saturday week is over
        nxt = week.which == "next"
        week_region = (market.get("nextWeekSellingRegion") or region) if nxt else region
        out.append(Leaflet(
            key=iso_key(week.monday) + ("" if week_region == store.edition else f"-{week_region}"),
            title=f"Angebote KW {week.monday.isocalendar().week}",
            kind="preview" if week.valid_from > ctx.today else "offers",
            valid_from=week.valid_from,
            valid_to=week.valid_to,
            dates_source=week.dates_source,
            source_url=market.get("nextWeekFlippingBookURL" if nxt else "flippingBookURL")
            or f"{BASE}/angebote",
        ))
    return out


async def acquire(ctx: ScanCtx, store: Store, leaflet: Leaflet) -> Acquired:
    """Every category of the leaflet's week for its region, fetched concurrently."""
    weeks = parse_page(await _cached(ctx, "/angebote", PAGE_TTL_S), ctx.today)
    week = next((w for w in weeks if leaflet.key.startswith(iso_key(w.monday))), None)
    if week is None:
        raise StepFailed(f"Penny no longer lists the offers of {leaflet.key[:8]}.")
    region = leaflet.key[9:] or store.edition  # "2026-W43-15A-02-35": a region other than ours
    parts = await asyncio.gather(
        *(_tiles(ctx, week.feed_week, slug, region) for slug in week.categories)
    )
    if all(p is None for p in parts):
        raise StepFailed(f"Penny's offer feed failed for every category of {leaflet.key[:8]}.")
    offers = [
        offer
        for (slug, start), tiles in zip(week.categories.items(), parts, strict=True)
        for offer in parse_offers(tiles or [], slug, start, leaflet.valid_to)
    ]
    # A preview is re-read on every scan until its week starts, then stored complete.
    done = all(p is not None for p in parts) and leaflet.kind != "preview"
    return Acquired(tier=1, offers=offers, complete=done)


def _offer(tile: dict, slug: str, valid_from: date, valid_to: date) -> Offer:
    """One tile, priced the way Penny's own tile script shows it."""
    title = _text(tile.get("title")).rstrip("* ")  # "*" refers to a stock note
    price = _cents(tile.get("price"))
    old: int | None = None
    app: int | None = None
    base = tile.get("basePrice")  # "(1 kg = 5.69)"
    conditions: list[str] = []
    if tile["primaryType"] == "offer":
        if _true(tile.get("showOriginalPrice")):
            old = _cents(tile.get("originalPrice"))  # the UVP or the struck-through former price
    else:
        old = _cents(tile.get("listPrice")) or _cents(tile.get("crossOutPrice"))
        if tile["primaryType"] == "promoPlus":  # an offer price plus a lower app price
            app = _cents(tile.get("benefitPrice"))
        else:  # app only: "price" is the app price, the struck one the price without the app
            price, app, base = None, price, tile.get("benefitGroundPrice")
            conditions.append(f"nur mit {LOYALTY}")
    if _true(tile.get("priceFrom")) or _true(tile.get("actionPriceFrom")):
        old = None  # the page prints "ab" instead of an old price
        conditions.append("ab-Preis")
    if _true(tile.get("multiBuy")):
        # Never seen live yet: whether its price is per pack or per bundle is unknown, so no
        # price is claimed for it.
        log.warning("penny.multibuy", title=title, price=tile.get("price"),
                    quantity=tile.get("quantity"))
        price = old = app = None
        conditions.append("Mehrfachkauf, Preis siehe Penny")
    category: Category | None = (
        "nonfood" if tile.get("type") == "non-food" else CAT_MAP.get(slug.rstrip("0123456789"))
    )
    ref = re.search(r"\((.*)\)", _text(base))  # printed in brackets: "(1 kg = 5.69)"
    image = (tile.get("imageRendition") or {}).get("tileLg")
    return Offer(
        chain=NAME,
        product_name=title,
        category=category,
        price_cents=price,
        old_price_cents=old,
        loyalty_price_cents=app,
        pack_text=_LITRE.sub(" l", _text(tile.get("quantity"))) or None,
        reference_printed=ref[1] if ref else _text(base) or None,
        condition=", ".join(conditions) or None,
        valid_from=valid_from,
        valid_to=valid_to,
        # Unpublished product photos come as Penny's grey placeholder image.
        image_url=None if image and "/placeholders/" in image else image,
        source="feed",
        price_check="feed",
    )


async def _get(ctx: ScanCtx, path: str) -> httpx.Response:
    """GET a www.penny.de path; 403 is Blocked, any other failure a StepFailed."""
    try:
        response = await ctx.http.get(BASE + path)
    except httpx.HTTPError as exc:
        raise StepFailed(f"Penny did not answer ({type(exc).__name__}).") from exc
    if response.status_code == 403:
        raise Blocked("Penny refused the request (HTTP 403).")
    if response.status_code != 200:
        raise StepFailed(f"Penny answered HTTP {response.status_code} for {path}.")
    return response


async def _cached(ctx: ScanCtx, path: str, ttl_s: float) -> Any:
    """The body of a GET (JSON under /.rest/, else text), fetched at most once per ttl_s."""
    hit = _cache.get(path)
    if hit is None or hit[0] <= time.monotonic():
        response = await _get(ctx, path)
        try:
            body = response.json() if path.startswith("/.rest/") else response.text
        except ValueError as exc:  # e.g. a maintenance or bot-check page instead of JSON
            raise StepFailed(f"Penny sent unreadable data for {path}.") from exc
        hit = _cache[path] = (time.monotonic() + ttl_s, body)
    return hit[1]


async def _markets(ctx: ScanCtx) -> list[dict]:
    """The /.rest/market list, checked to be a list of market records."""
    body = await _cached(ctx, "/.rest/market", MARKETS_TTL_S)
    if not isinstance(body, list) or not all(isinstance(m, dict) for m in body):
        _cache.pop("/.rest/market", None)  # ask again on the next scan
        raise StepFailed("Penny's market list has an unknown format.")
    return body


async def _tiles(ctx: ScanCtx, feed_week: str, slug: str, region: str) -> list[dict] | None:
    """One category's offer tiles, or None when the request fails (the leaflet is partial)."""
    path = f"/.rest/offers/by-category/{feed_week}/{slug}?region={region}"
    try:
        tiles = (await _get(ctx, path)).json()["offerTiles"]
    except (StepFailed, ValueError, KeyError, TypeError) as exc:
        log.warning("penny.category_failed", path=path, error=str(exc))
        return None
    return tiles if isinstance(tiles, list) else None


def _closed(market: dict, today: date) -> bool:
    """Renovation closures come as temporarilyClosedFrom/Until "dd.mm.yyyy" pairs."""
    try:
        start, end = (
            datetime.strptime(market[k], "%d.%m.%Y").date()
            for k in ("temporarilyClosedFrom", "temporarilyClosedUntil")
        )
    except (KeyError, TypeError, ValueError):
        return False  # "" for most markets; an unknown format counts as open
    return start <= today <= end


def _attrs(tag: str) -> dict[str, str]:
    return dict(_ATTR.findall(tag))


def _cents(value: object) -> int | None:
    """Cents of a feed price ("1.99", " 24.99", "5.-", app prices with a superscript footnote
    mark); None if absent or 0."""
    return parse_price_cents(re.sub(r"[^\d.,-]", "", value)) if isinstance(value, str) else None


def _true(value: object) -> bool:
    """Feed flags are booleans or "true" strings (Penny's own script tests toString())."""
    return str(value).lower() == "true"


def _text(value: object) -> str:
    """Feed text without soft hyphens and zero-width spaces, whitespace collapsed."""
    return " ".join(str(value or "").translate(_INVISIBLE).split())
