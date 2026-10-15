"""Kaufland (tier 1): the store finder plus each store's offers page.

filiale.kaufland.de renders its offers page for the store named in the `x-aem-variant` cookie
(the response varies on it; offers, prices and dates differ between stores) and embeds one JSON
document: every current offer plus next week's preview, each with its sales window
(`salesFrom`/`salesTo`; theme sections without one carry only `dateFrom`/`dateTo`). The page
embeds the same document for `?kloffer-week=next`, so one GET per store serves every leaflet.
Each window is one leaflet; the window of the feed's `main` product groups is the main one, while
"Ab Do." sections, Thu..Wed theme weeks and the monthly discount are extras. A window starting
after the last main window ends is next week's preview ("Vorwerbung"): stored partial, so the full
set published under the same key replaces it.
robots.txt allows both URLs used here.
"""
from __future__ import annotations

import json
import re
import time
from collections import defaultdict
from datetime import date
from typing import Any

import httpx

from app.agents.base import Acquired, Blocked, Leaflet, ScanCtx, StepFailed, Store, iso_key
from app.schemas.offer import Category, Offer
from app.services.offers import parse_price_cents

NAME = "kaufland"
LABEL = "Kaufland"
LOYALTY = "Kaufland Card"
DOMAINS = ("filiale.kaufland.de",)
TIER = 1  # acquisition tier: 1 structured feed, 2 leaflet pages via the VLM, 3 browser

STORES_URL = "https://filiale.kaufland.de/.klstorefinder.json"
OFFERS_URL = "https://filiale.kaufland.de/angebote/uebersicht.html"
_STORES_TTL_S = 24 * 3600.0
_PAGE_TTL_S = 600.0  # leaflets() and acquire() of one scan share one page fetch
# The feed's main product groups. Theme sections repeat their offers or have no product group.
_CATEGORIES: dict[str, Category] = {
    "01_Fleisch__Gefluegel__Wurst": "food",
    "01a_Frischer_Fisch": "food",
    "02_Obst__Gemuese__Pflanzen": "food",
    "03_Molkereiprodukte__Fette": "food",
    "04_Tiefkuehlkost": "food",
    "05_Feinkost__Konserven": "food",
    "06_Grundnahrungsmittel": "food",
    "07_Kaffee__Tee__Suesswaren__Knabberartikel": "food",
    "08_Getraenke__Spirituosen": "drinks",
    "09_Drogerie__Tiernahrung": "drugstore",
    "10_Elektro__Buero__Medien": "nonfood",
    "11_Heim__Haus": "household",
    "12_Bekleidung__Auto__Freizeit__Spiel": "nonfood",
}

_stores: tuple[float, list[Store]] = (0.0, [])  # (expiry, stores)
_pages: dict[str, tuple[float, dict[str, tuple[Leaflet, Acquired]]]] = {}  # store id -> page


def parse_stores(data: list[dict[str, Any]]) -> list[Store]:
    """Store finder entries: id "n", name "cn", street "sn", PLZ "pc", town "t", "lat"/"lng"."""
    return [
        Store(id=s["n"], name=s["cn"], address=f"{s['sn']}, {s['pc']} {s['t']}",
              lat=float(s["lat"]), lon=float(s["lng"]), edition=s["n"])
        for s in data
        if s.get("lat") and s.get("lng")
    ]


def _window(item: dict[str, Any]) -> tuple[date, date]:
    """Sales window of an offer or category; its display dates when it has none."""
    return (date.fromisoformat(item.get("salesFrom") or item["dateFrom"]),
            date.fromisoformat(item.get("salesTo") or item["dateTo"]))


def _cents(text: str | None) -> int | None:
    """A feed price string ("1.99", "0.99*", "UVP 23.99") in cents."""
    return parse_price_cents(re.sub(r"[^\d.,]", "", text)) if text else None


def _each(total: int | None, n: int) -> int | None:
    """Per-pack cents of a bundle price, rounded half up."""
    return (2 * total + n) // (2 * n) if total else None


def _offer(o: dict[str, Any], valid: tuple[date, date], category: Category | None) -> Offer:
    title, sub = (o.get("title") or "").strip(), (o.get("subtitle") or "").strip()
    brand = title if sub and title.isupper() else None  # "KANZI" over "Dtsch./ital. Tafelaepfel"
    # Multi-buy ("4 Stueck", "3 + 1 gratis"): price and old price are for the whole bundle; the
    # tile prints the per-pack price ("entspr. pro Packg. 0.75").
    bundle, *notes = o.get("bbyMessage") or [""]
    m = re.match(r"(\d+)(?:\s*\+\s*(\d+))?", bundle)
    n = max(1, int(m[1]) + int(m[2] or 0)) if m else 1
    total = parse_price_cents(o.get("price"))
    printed = re.search(r"entspr\. pro \S+ (\d+\.\d\d)", " ".join(notes))
    return Offer(
        chain=NAME,
        product_name=sub if brand else f"{title} {sub}".strip(),
        brand=brand,
        description=(o.get("detailDescription") or "").strip() or None,
        category=category,
        # A card-only offer has price 0: no public offer price, its regular price is the
        # card price's old price.
        price_cents=parse_price_cents(printed[1]) if printed and total else _each(total, n),
        bundle_price_cents=total if n > 1 else None,
        # A "showPriceOnly" tile prints "nur 0.44" without an old price: the feed's is not claimed.
        old_price_cents=None if o.get("showPriceOnly") else _each(
            _cents(o.get("formattedOldPrice") or o.get("loyaltyFormattedOldPrice")), n),
        loyalty_price_cents=_cents(o.get("loyaltyFormattedPrice")),
        pack_text=(o.get("unit") or "").strip() or None,
        reference_printed=(o.get("basePrice") or "").strip("() ") or None,
        condition=bundle or None,
        min_qty=n,
        valid_from=valid[0],
        valid_to=valid[1],
        image_url=o.get("listImage"),
        source="feed",
        price_check="feed",
    )


def parse_page(html: str) -> dict[str, tuple[Leaflet, Acquired]]:
    """Every offer window of a store's offers page, by leaflet key: its leaflet and offers."""
    start = html.find('{"component":"OfferTemplate"')
    if start < 0:
        raise StepFailed("The Kaufland offers page carries no offer data (layout changed).")
    try:
        return _parse(json.JSONDecoder().raw_decode(html, start)[0]["props"])
    except (KeyError, TypeError, ValueError) as exc:
        raise StepFailed("The Kaufland offers page changed its layout.") from exc


def _parse(props: dict[str, Any]) -> dict[str, tuple[Leaflet, Acquired]]:
    cats = [c for cycle in props["offerData"]["cycles"] for c in cycle["categories"]]
    mains = {_window(c) for c in cats if c.get("main")}
    # Without a main window every window counts as preview: nothing unconfirmed is cached complete.
    week_end = max((vt for _, vt in mains), default=date.min)
    windows: defaultdict[tuple[date, date], dict[str, dict[str, Any]]] = defaultdict(dict)
    groups: dict[str, Category] = {}  # offer id -> product group
    for c in cats:
        for o in c["offers"]:
            # An offer without its own sales dates takes its section's (as the site shows it).
            windows[_window(o if o.get("salesFrom") else c)].setdefault(o["offerId"], o)
            if c["name"] in _CATEGORIES:
                groups.setdefault(o["offerId"], _CATEGORIES[c["name"]])
    out: dict[str, tuple[Leaflet, Acquired]] = {}
    for (vf, vt), raw in sorted(windows.items()):
        preview = vf > week_end
        leaflet = Leaflet(
            key=f"{iso_key(vf)}:{vf:%m%d}-{vt:%m%d}",
            title=f"Angebote {vf:%d.%m.}-{vt:%d.%m.%Y}" + (" (Vorschau)" if preview else ""),
            kind="main" if (vf, vt) in mains else "preview" if preview else "extra",
            valid_from=vf,
            valid_to=vt,
            dates_source="feed",
            source_url=OFFERS_URL,
        )
        offers = [_offer(o, (vf, vt), groups.get(i)) for i, o in raw.items()]
        out[leaflet.key] = (leaflet, Acquired(tier=1, offers=offers, complete=not preview))
    return out


async def _get(ctx: ScanCtx, url: str, **kw: Any) -> httpx.Response:
    r = await ctx.http.get(url, **kw)
    if r.status_code == 403:
        raise Blocked(f"{LABEL} refused the request (HTTP 403).")
    r.raise_for_status()
    return r


async def stores(ctx: ScanCtx) -> list[Store]:
    """Every Kaufland store with coordinates (store finder, cached in memory for 24 h)."""
    global _stores
    if _stores[0] <= time.monotonic():
        found = parse_stores((await _get(ctx, STORES_URL)).json())
        if not found:
            raise StepFailed("The Kaufland store finder returned no stores.")
        _stores = (time.monotonic() + _STORES_TTL_S, found)
    return _stores[1]


async def _page(ctx: ScanCtx, store: Store) -> dict[str, tuple[Leaflet, Acquired]]:
    """The store's parsed offers page, fetched at most once per _PAGE_TTL_S."""
    now = time.monotonic()
    hit = _pages.get(store.edition)
    if hit is None or hit[0] <= now:
        r = await _get(ctx, OFFERS_URL, headers={"Cookie": f"x-aem-variant={store.edition}"})
        for k in [k for k, (until, _) in _pages.items() if until <= now]:
            del _pages[k]
        hit = _pages[store.edition] = (now + _PAGE_TTL_S, parse_page(r.text))
    return hit[1]


async def leaflets(ctx: ScanCtx, store: Store) -> list[Leaflet]:
    """One leaflet per offer window of the store that has not ended (a fresh page per scan)."""
    _pages.pop(store.edition, None)
    return [lf for lf, _ in (await _page(ctx, store)).values() if lf.valid_to >= ctx.today]


async def acquire(ctx: ScanCtx, store: Store, leaflet: Leaflet) -> Acquired:
    """The window's offers from the store's page (the one leaflets() fetched, while cached)."""
    hit = (await _page(ctx, store)).get(leaflet.key)
    return hit[1] if hit else Acquired(tier=1, complete=False)
