"""Aldi Süd (tier 1): the weekly leaflet on Publitas, prospekt.aldi-sued.de, with its product feed.

Aldi Süd prints one leaflet for its whole area, so the edition is "sued" and no store is located:
the store finder is an Uberall widget that needs a key embedded in the site's script (never used
here), and the static store pages carry no coordinates. Aldi Nord (the north and east) is not
covered; which half a postcode belongs to is the table below.

https://prospekt.aldi-sued.de/ redirects to the current publication's slug ("kw42-26-op-mp": ISO
week, year, variant) and www.aldi-sued.de/angebote links the current and the coming ones (seen:
"vop" two weeks ahead, "op" next week, "op-mp" once current, the "op" one then answering 404).
The leaflet key is the ISO week alone, so the current "op-mp" leaflet replaces next week's
partial "op" row instead of sitting beside it. A slug's `spreads.json` lists every page with its
text layer, page 1 printing the dates ("Gültig von Mo. 12.10. - Sa. 17.10."); a withdrawn slug
answers 404 there, while its viewer page answers 200 with "Diese Publikation ist offline". Each
spread's `page/<n>-<m>/hotspots_data.json` lists the products placed on it.

Prices, verified on kw42-26-op-mp (2026-10-13, 209 priced products): `price` alone is the offer
price. With `discountedPrice` (45 products, always the lower one) the discounted price is the
offer and `price` the struck-through one: the printed kg/l price (customLabel9) matches
discountedPrice for 34 of 37 single-size products and `price` for none, and customLabel0 is the
printed percentage, floor(1 - discountedPrice / price) for 43 of 43. customLabel1 is the first
day ("15.10."), customLabel2 the pack ("je 200-g-Packung"). The one multi-buy seen (a coffee,
"KAUFE 6 + 2 Gratis" in webshopUrl) prices the carton: description "(Einzelpreis 10.99, 8
Packungen à 1,0 kg = 87.92)", price 87.92 = 8 x 10.99, discountedPrice 65.94 for the 8 packs,
printed kg-Preis 8.24 = 65.94 / 8; it becomes min_qty 8, bundle 65.94, per pack 8.24, was 10.99.
Next week's "op" leaflet carries no priced products (2026-10-14: 2 recipe hotspots, 0 prices), so
a leaflet without any is handed to the VLM tier as page images with their text layer.
robots.txt: www.aldi-sued.de allows /angebote; prospekt.aldi-sued.de has none (404).
"""
from __future__ import annotations

import asyncio
import re
import weakref
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

import httpx

from app.agents.base import (
    Acquired,
    Blocked,
    Leaflet,
    NotCovered,
    Page,
    ScanCtx,
    StepFailed,
    Store,
    iso_key,
    parse_validity,
)
from app.core.logging import get_logger
from app.schemas.offer import Offer
from app.services.offers import parse_price_cents

log = get_logger(__name__)

NAME = "aldi"
LABEL = "Aldi Süd"
LOYALTY = None  # page 2 of the leaflet: "günstig ohne Bonus-App", "auch ohne Treue-Programm"
DOMAINS = ("prospekt.aldi-sued.de", "www.aldi-sued.de")
TIER = 1  # acquisition tier: 1 structured feed, 2 leaflet pages via the VLM, 3 browser

PROSPEKT = "https://prospekt.aldi-sued.de"
LISTING = "https://www.aldi-sued.de/angebote"
CONCURRENCY = 4
IMAGE_SIZE = "at1200"  # 903 x 1594 px; the VLM reads pages at 1024 px

# Aldi Süd's area: 3-digit PLZ prefix ranges plus single-PLZ exceptions; every other PLZ is Aldi
# Nord. Derived 2026-10-12 from both chains' official store sitemaps
# (www.aldi-sued.de/sitemap_stores.xml, www.aldi-nord.de/sitemaps/.aldi-nord-sitemap-stores.xml):
# store towns matched to the PLZs of the 27 two-digit regions where both chains have stores (07,
# 08, 32-37, 40-42, 44-48, 50-53, 56-59, 96, 98, 99; place names from openplzapi.org), the
# majority per prefix, towns of the same name elsewhere and towns with both chains (Siegen,
# Gummersbach, Wetzlar, the Fulda and Alsfeld areas) decided by hand. 54, 55, 60-95 and 97 have
# Aldi Süd stores only; 991 (around Erfurt) has no store of either chain and stays Nord.
SUED_PREFIXES = (
    (353, 355), (360, 361), (363, 363), (402, 418), (454, 454), (460, 462), (464, 479),
    (501, 568), (575, 576), (600, 979),
)
SUED_PLZ = frozenset({
    "35606", "35614", "35619", "35625", "35630", "35633", "35638", "35641", "35647", "35753",
    "35764", "35781", "35789", "35792", "35794", "35796", "35799", "42799", "46395", "46397",
    "46399", "57080", "57258", "57290", "57299",
})
NORD_PLZ = frozenset({
    "35466", "36088", "36132", "36151", "36166", "36167", "36169", "36179", "36199", "36304",
    "36318", "36320", "36323", "36325", "36326", "36329", "40822", "46282", "46284", "46286",
    "51643", "51647", "51702", "51709",
})

_SLUG = re.compile(r"prospekt\.aldi-sued\.de/(kw\d{2}-\d{2}(?:-[a-z0-9]+)*)")
_WEEKDAY = re.compile(r"\b(?:Mo|Di|Mi|Do|Fr|Sa|So)\.\s*")  # "Mo. 12.10. - Sa. 17.10."
_DAY = re.compile(r"(\d{1,2})\.(\d{1,2})\.")
_BUNDLE = re.compile(r"Einzelpreis (\d+[.,]\d{2}), (\d+) Packungen")

# leaflets() and acquire() of one scan (one client) share the listing and spreads fetches
_cache: weakref.WeakKeyDictionary[httpx.AsyncClient, dict[str, Any]] = weakref.WeakKeyDictionary()


def is_sued(plz: str) -> bool:
    """Whether a German postcode lies in Aldi Süd's area."""
    if plz in SUED_PLZ or plz in NORD_PLZ:
        return plz in SUED_PLZ
    prefix = int(plz[:3])
    return any(lo <= prefix <= hi for lo, hi in SUED_PREFIXES)


def parse_slugs(text: str) -> list[str]:
    """Publication slugs linked or redirected to, in order of appearance, without repeats."""
    return list(dict.fromkeys(_SLUG.findall(text)))


def slug_monday(slug: str) -> date | None:
    """Monday of the ISO week a slug names ("kw42-26-op" -> 2026-10-12)."""
    week, year = slug.split("-")[:2]
    try:
        return date.fromisocalendar(2000 + int(year), int(week[2:]), 1)
    except ValueError:
        return None


def printed_validity(spreads: list[dict], today: date) -> tuple[date, date] | None:
    """The date range page 1 prints ("Gültig von Mo. 12.10. - Sa. 17.10.")."""
    pages = [p for s in spreads for p in s.get("pages") or []]
    text = (pages[0].get("text") or "") if pages else ""
    return parse_validity(_WEEKDAY.sub("", text), today)


def spread_pages(spreads: list[dict]) -> list[Page]:
    """Every page's image (IMAGE_SIZE, a path on the prospekt host) and text layer."""
    return [
        Page(PROSPEKT + p["images"][IMAGE_SIZE], p.get("text") or None)
        for s in spreads for p in s.get("pages") or []
        if (p.get("images") or {}).get(IMAGE_SIZE)
    ]


def parse_hotspots(hotspots: list[dict], pages: list[int], leaflet: Leaflet) -> list[Offer]:
    """Priced products of one spread; recipes and links carry no price and are skipped."""
    offers = []
    for spot in hotspots:
        pos = spot.get("position") or {}
        right = len(pages) > 1 and pos.get("left", 0) + pos.get("width", 0) / 2 > 0.5
        for product in spot.get("products") or []:
            if product.get("removedFromFeed") or not _cents(product.get("price")):
                continue
            offers.append(_offer(product, pages[1] if right else pages[0], leaflet))
    return offers


async def stores(ctx: ScanCtx) -> list[Store]:
    """Aldi Süd's one edition, unlocated (no keyless store finder); Aldi Nord is not covered."""
    if not is_sued(ctx.plz):
        raise NotCovered("Aldi Nord is not covered.")
    return [Store(id=None, name=LABEL, address="", lat=None, lon=None, edition="sued",
                  located=False)]


async def leaflets(ctx: ScanCtx, store: Store) -> list[Leaflet]:
    """This week's and next week's online leaflets, dated by the range page 1 prints."""
    monday = ctx.today - timedelta(days=ctx.today.weekday())
    weeks = (monday, monday + timedelta(days=7))
    found: list[tuple[str, date, tuple[date, date] | None]] = []  # slug, Monday, printed range
    for slug in await _slugs(ctx):
        week = slug_monday(slug)
        if week not in weeks or week in [f[1] for f in found]:  # the redirect's slug comes first
            continue
        spreads = await _spreads(ctx, slug)
        if spreads is None:
            log.info("aldi.offline", slug=slug)
            continue
        found.append((slug, week, printed_validity(spreads, ctx.today)))
    found.sort(key=lambda f: f[1])
    out = []
    for i, (slug, week, printed) in enumerate(found):
        if printed:
            (start, end), source = printed, "text"
        else:  # the slug's week; it ends where the next printed leaflet starts, else on Saturday
            later = [p[0] for _, _, p in found[i + 1:] if p and p[0] > week]
            start, source = week, "derived"
            end = min(later) - timedelta(days=1) if later else week + timedelta(days=5)
        if end < ctx.today:
            continue
        out.append(Leaflet(
            key=iso_key(week),
            title=f"Prospekt KW {week.isocalendar().week}",
            kind="preview" if start > ctx.today else "main",
            valid_from=start,
            valid_to=end,
            dates_source=source,
            source_url=f"{PROSPEKT}/{slug}/",
        ))
    return out


async def acquire(ctx: ScanCtx, store: Store, leaflet: Leaflet) -> Acquired:
    """Every spread's hotspots, a few at a time; complete when every spread answered."""
    slug = (leaflet.source_url or "").rstrip("/").rsplit("/", 1)[-1]
    spreads = await _spreads(ctx, slug)
    if spreads is None:
        raise StepFailed(f"Aldi Süd took the leaflet {slug} offline.")
    gate = asyncio.Semaphore(CONCURRENCY)
    pages = [[int(p["number"]) for p in s.get("pages") or []] for s in spreads]
    try:  # a 403 cancels the other requests and reaches the caller
        async with asyncio.TaskGroup() as tg:
            tasks = [tg.create_task(_hotspots(ctx, slug, n, gate)) for n in pages]
    except ExceptionGroup as group:
        raise group.exceptions[0] from None
    parts = [t.result() for t in tasks]
    ok = all(p is not None for p in parts)
    offers = [
        offer
        for numbers, spots in zip(pages, parts, strict=True)
        for offer in parse_hotspots(spots or [], numbers, leaflet)
    ]
    # A leaflet not started yet is re-read on every scan until it starts, then stored complete.
    preview = leaflet.kind == "preview"
    if not offers:  # next week's "op" leaflet has no priced products yet: the VLM reads its pages
        if not ok:  # unknown whether the feed has prices: let the next scan ask again
            raise StepFailed("Aldi Süd's product feed did not answer.")
        log.info("aldi.no_feed", slug=slug)
        return Acquired(tier=2, pages=spread_pages(spreads), complete=not preview)
    return Acquired(tier=1, offers=offers, complete=ok and not preview)


async def feed_gold(ctx: ScanCtx, store: Store, leaflet: Leaflet) -> list[tuple[int, list[Offer]]]:
    """The feed's offers per page index of spread_pages (the VLM tier's index), the automatic gold
    for scoring the VLM tier; pages without an image are left out."""
    slug = (leaflet.source_url or "").rstrip("/").rsplit("/", 1)[-1]
    numbers = [
        int(p["number"]) for s in await _spreads(ctx, slug) or [] for p in s.get("pages") or []
        if (p.get("images") or {}).get(IMAGE_SIZE)
    ]
    index = {n: i for i, n in enumerate(numbers)}
    by_page: dict[int, list[Offer]] = defaultdict(list)
    for offer in (await acquire(ctx, store, leaflet)).offers:
        if offer.page in index:
            by_page[index[offer.page]].append(offer)
    return sorted(by_page.items())


def _offer(product: dict, page: int, leaflet: Leaflet) -> Offer:
    price = _cents(product.get("price"))
    discounted = _cents(product.get("discountedPrice"))
    description = _text(product.get("description"))
    old, bundle, qty, condition = price if discounted else None, None, 1, None
    if m := _BUNDLE.search(description):  # the prices are for qty packs; webshopUrl names the deal
        bundle, qty, old = discounted or price, int(m[2]), _cents(m[1])
        condition = _text(product.get("webshopUrl")).removeprefix("http://").rstrip(
            "*\N{SUPERSCRIPT ONE}\N{SUPERSCRIPT TWO}\N{SUPERSCRIPT THREE}") or None
    return Offer(
        chain=NAME,
        product_name=_text(product.get("title")),
        brand=_text(product.get("brand")) or None,
        description=description or None,
        price_cents=round(bundle / qty) if bundle else discounted or price,
        old_price_cents=old,
        condition=condition,
        min_qty=qty,
        bundle_price_cents=bundle,
        pack_text=_text(product.get("customLabel2")) or None,
        reference_printed=_text(product.get("customLabel9")) or None,
        valid_from=_day(product.get("customLabel1"), leaflet.valid_from),
        valid_to=leaflet.valid_to,
        page=page,
        image_url=product.get("photoSharingUrl"),
        source="feed",
        price_check="feed",
    )


async def _get(ctx: ScanCtx, url: str, **kwargs: Any) -> httpx.Response:
    """GET; 403 is Blocked, a failed request StepFailed; other statuses are the caller's."""
    try:
        response = await ctx.http.get(url, **kwargs)
    except httpx.HTTPError as exc:
        raise StepFailed(f"Aldi Süd did not answer ({type(exc).__name__}).") from exc
    if response.status_code == 403:
        raise Blocked("Aldi Süd refused the request (HTTP 403).")
    return response


async def _slugs(ctx: ScanCtx) -> list[str]:
    """The current slug (the prospekt root's redirect) and the ones the offers page links."""
    cache = _cache.setdefault(ctx.http, {})
    if LISTING not in cache:
        root = await _get(ctx, f"{PROSPEKT}/", follow_redirects=False)
        listing = await _get(ctx, LISTING)
        if listing.status_code != 200:
            log.warning("aldi.listing_failed", status=listing.status_code)
        slugs = parse_slugs(root.headers.get("location", "") + " " + (
            listing.text if listing.status_code == 200 else ""))
        if not slugs:
            raise StepFailed("Aldi Süd names no current leaflet.")
        cache[LISTING] = slugs
    return cache[LISTING]


async def _spreads(ctx: ScanCtx, slug: str) -> list[dict] | None:
    """A publication's spreads with their pages' text, or None when it is offline."""
    url = f"{PROSPEKT}/{slug}/spreads.json"
    cache = _cache.setdefault(ctx.http, {})
    if url not in cache:
        response = await _get(ctx, url)
        try:
            body = response.json() if response.status_code == 200 else None
        except ValueError:  # an HTML page instead of JSON
            body = None
        if not isinstance(body, list):  # offline or failed: not cached, asked again
            return None
        cache[url] = body
    return cache[url]


async def _hotspots(
    ctx: ScanCtx, slug: str, pages: list[int], gate: asyncio.Semaphore
) -> list[dict] | None:
    """One spread's hotspots, or None when the request fails (the leaflet is partial)."""
    url = f"{PROSPEKT}/{slug}/page/{'-'.join(map(str, pages))}/hotspots_data.json"
    async with gate:
        try:
            response = await _get(ctx, url)
            body = response.json() if response.status_code == 200 else None
        except (StepFailed, ValueError) as exc:
            log.warning("aldi.hotspots_failed", url=url, error=str(exc))
            return None
    return body if isinstance(body, list) else None


def _day(label: object, start: date) -> date:
    """A first-day label ("15.10.") in the leaflet's year (the next one across New Year)."""
    m = _DAY.fullmatch(_text(label))
    if not m:
        return start
    for year in (start.year, start.year + 1):
        try:
            day = date(year, int(m[2]), int(m[1]))
        except ValueError:
            return start
        if day >= start - timedelta(days=7):
            return day
    return start


def _cents(value: object) -> int | None:
    return parse_price_cents(value) if isinstance(value, str) else None


def _text(value: object) -> str:
    """Feed text without soft hyphens, whitespace collapsed."""
    return " ".join(str(value or "").replace("\N{SOFT HYPHEN}", "").split())
