"""Lidl (tier 3 store finder, tier 2 leaflet pages): the nearest store from Lidl's own finder.

The finder (www.lidl.de/s/de-DE/filialen/) is driven in Chromium with healable steps: accept
the OneTrust consent, search the PLZ, mark the nearest store as "Meine Filiale". The page itself
calls live.api.schwarz (`stores-frontend/stores?geo_box=` for the result list,
`?object_number=` when a store is chosen); both answers are read from the page's own responses,
never by calling the key-protected API ourselves. Each store's `marketingData.offerRegion` is
its leaflet edition. The leaflet host endpoints.leaflets.schwarz is an open JSON feed (no
robots.txt, 404): `v4/overview` lists the "Unsere Aktionsprospekte" flyers of a region with exact
offer dates, `v4/flyer` their pages (image plus sparse keywords and product link titles; the
prices are only in the images).
"""
from __future__ import annotations

import asyncio
import base64
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
from playwright.async_api import Response

from app.agents import base, browser
from app.agents.base import Acquired, Leaflet, Page, ScanCtx, Store, iso_key, iso_week
from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.geo import nearest

log = get_logger(__name__)

NAME = "lidl"
LABEL = "Lidl"
LOYALTY = "Lidl Plus"
DOMAINS = ("lidl.de", "leaflets.schwarz", "live.api.schwarz")
TIER = 3  # acquisition tier: 1 structured feed, 2 leaflet pages via the VLM, 3 browser

FINDER = "https://www.lidl.de/s/de-DE/filialen/"
FEED = "https://endpoints.leaflets.schwarz/v4"
STORES_API = "stores-frontend/stores?"  # the finder's own calls to live.api.schwarz
MAIN = "Unsere Aktionsprospekte"
SELECT_WAIT_S = 4.0  # the finder's object_number call after a (healed) click
# Online-shop worlds ("Bedürfniswelten") of the flyer's linked products that hold no
# groceries; "Wein, Bier und Spirituosen" and any unknown world are still read.
NONFOOD = frozenset({"Mode & Accessoires", "Wohnen & Einrichtung", "Küche & Haushalt",
                     "Sport & Freizeit", "Baumarkt & Garten", "Baby, Kind & Spielware"})


def parse_stores(data: dict) -> list[Store]:
    """Stores of a finder result list; the edition is the store's offer region."""
    out = []
    for s in data.get("items") or []:
        a, region = s.get("address") or {}, (s.get("marketingData") or {}).get("offerRegion")
        if region is None or a.get("latitude") is None or a.get("longitude") is None:
            continue
        out.append(Store(
            id=s["objectNumber"], name=f"Lidl {s.get('storeName') or a.get('city', '')}",
            address=f"{a.get('streetName', '')}, {a.get('zip', '')} {a.get('city', '')}",
            lat=float(a["latitude"]), lon=float(a["longitude"]), edition=str(region),
        ))
    return out


def parse_overview(data: dict, today: date) -> list[Leaflet]:
    """This week's and next week's main leaflets ("Unsere Aktionsprospekte") with feed dates."""
    last = iso_week(today)[0] + timedelta(days=13)  # Sunday of next week
    out = []
    for cat in data.get("categories") or []:
        for sub in cat.get("subcategories") or []:
            if sub.get("name") != MAIN:
                continue
            for f in sub.get("flyers") or []:
                start = date.fromisoformat(f["offerStartDate"])
                end = date.fromisoformat(f["offerEndDate"])
                ident = parse_qs(urlsplit(f["flyerJson"]).query)["flyer_identifier"][0]
                if end < today or start > last:
                    continue
                out.append(Leaflet(
                    key=f"{iso_key(start)}-{ident}",
                    title=f"{f.get('name') or 'Aktionsprospekt'} {f.get('title') or ''}".strip(),
                    kind="main", valid_from=start, valid_to=end, dates_source="feed",
                    source_url=f.get("flyerUrlAbsolute"), pdf_url=f.get("pdfUrl"),
                ))
    return sorted(out, key=lambda leaflet: leaflet.valid_from)


def parse_flyer(data: dict) -> tuple[list[Page], int, int]:
    """(grocery pages with an image, grocery pages, non-food pages left out); the text is the
    keywords plus product link titles. A page without recipe links whose product links all lead
    to online-shop articles of a non-food world is a non-food page: the VLM never reads it."""
    flyer = data.get("flyer") or {}
    products = flyer.get("products") or {}
    out, total, skipped = [], 0, 0
    for p in flyer.get("pages") or []:
        links = p.get("links") or []
        shop = [ln for ln in links if ln.get("displayType") == "product"]
        worlds = {((products.get(ln.get("id")) or {}).get("wonCategoryPrimary") or "")
                  .partition("/")[2].partition("/")[0] for ln in shop}
        if shop and worlds <= NONFOOD and not any(ln.get("displayType") == "recipe"
                                                  for ln in links):
            skipped += 1
            continue
        total += 1
        if not p.get("image"):
            continue
        titles = [ln["title"] for ln in shop if ln.get("title")]
        text = "\n".join(t for t in [p.get("keyWords") or "", *titles] if t)
        out.append(Page(p["image"], text or None))
    return out, total, skipped


async def stores(ctx: ScanCtx) -> list[Store]:
    """Lidl's finder for ctx.plz, the nearest store marked as "Meine Filiale" by the script (or,
    when it fails or FORCE_HEAL=lidl:select_store, by the navigator)."""
    t0 = time.monotonic()
    seen: dict[str, Any] = {}
    heal_log: list[dict] = []

    async def keep(response: Response) -> None:  # the page's own answers, healed steps included
        if STORES_API in response.url and response.ok:
            try:
                seen["one" if "object_number=" in response.url else "list"] = (
                    await response.json())
            except Exception as exc:  # noqa: BLE001 - not JSON or page closing: not an answer
                log.info("lidl.response_unreadable", error=str(exc))

    def on_frame(jpeg: bytes) -> None:
        ctx.emit({"event": "frame", "image": base64.b64encode(jpeg).decode()})

    async with browser.browser_page(DOMAINS) as page:
        page.on("response", keep)

        def run(name: str, goal: str, do: Any, check: Any, force: bool = False) -> Any:
            return browser.step(page, name, goal, do, check, allow=DOMAINS,
                                inputs={"plz": ctx.plz}, emit=ctx.emit, heal_log=heal_log,
                                force_heal=force)

        async def open_and_consent() -> None:
            await page.goto(FINDER, wait_until="domcontentloaded")
            base.stream_frames(page, on_frame)  # after the navigation, which ends a stream
            await base.accept_cookies(page)

        async def consented() -> bool:
            names = {c["name"] for c in await page.context.cookies()}
            return "OptanonAlertBoxClosed" in names

        await run("consent", "Accept all cookies in the consent banner.",
                  open_and_consent, consented)

        field = page.locator("input.search-field__input:visible").first

        async def search() -> None:
            await field.fill(ctx.plz)
            await page.locator("li.search-field__flyout-item", has_text=ctx.plz).first.wait_for(
                timeout=10000)
            await browser.capture_json(page, STORES_API, lambda: field.press("Enter"))

        async def listed() -> bool:
            return "list" in seen

        await run("search", f"Search the store finder for postcode {ctx.plz} (input 'plz').",
                  search, listed)
        found = parse_stores(seen["list"])
        near = nearest(found, ctx.lat, ctx.lon, radius_km=1e9, limit=1)
        if not near:
            raise base.NotCovered(f"Lidl's store finder lists no store near {ctx.plz}.")
        target, km = near[0]
        street = target.address.split(",")[0]
        tile = page.locator("li.store-search-result__tile", has_text=street).first

        async def select() -> None:
            await browser.capture_json(page, "object_number=", lambda: tile.get_by_role(
                "button", name="Meine Filiale").click())

        async def selected() -> bool:
            for _ in range(int(SELECT_WAIT_S * 4)):
                items = (seen.get("one") or {}).get("items") or []
                if any(i.get("objectNumber") == target.id for i in items):
                    return True
                await asyncio.sleep(0.25)
            return False

        healed = await run(
            "select_store",
            f"In the result list, click the 'Meine Filiale' button of the store at {street} "
            f"({target.address}), making it my store.",
            select, selected, force=get_settings().force_heal == f"{NAME}:select_store")
        chosen = parse_stores(seen["one"])
    if chosen and chosen[0].edition != target.edition:  # the chosen store's own answer wins
        found = [chosen[0] if s.id == target.id else s for s in found]
    log.info("lidl.store", store=target.id, km=km, region=target.edition, stores=len(found),
             healed=bool(healed), heal_actions=len(heal_log),
             ms=int((time.monotonic() - t0) * 1000))
    return found


async def leaflets(ctx: ScanCtx, store: Store) -> list[Leaflet]:
    """The region's current and next main leaflet, dates from the feed."""
    data = await _get(ctx, f"{FEED}/overview", {"client_locale": "lidl/de-DE",
                                                "region_id": store.edition, "store_id": "0"})
    out = parse_overview(data, ctx.today)
    if not out:
        raise base.StepFailed(f"Lidl lists no current leaflet for region {store.edition}.")
    return out


async def acquire(ctx: ScanCtx, store: Store, leaflet: Leaflet) -> Acquired:
    """Every grocery page image (prices are printed only there); none while one lacks its
    image."""
    ident = leaflet.key.split("-", 2)[2]
    data = await _get(ctx, f"{FEED}/flyer", {"flyer_identifier": ident,
                                             "region_id": store.edition})
    pages, total, skipped = parse_flyer(data)
    if not pages or len(pages) != total:
        log.warning("lidl.pages_without_image", flyer=ident, missing=total - len(pages))
        return Acquired(tier=2, pages=[], complete=False)
    log.info("lidl.flyer", flyer=ident, pages=total, nonfood_skipped=skipped)
    return Acquired(tier=2, pages=pages, complete=True)


async def _get(ctx: ScanCtx, url: str, params: dict[str, str]) -> dict:
    try:
        response = await ctx.http.get(url, params=params)
    except httpx.HTTPError as exc:
        raise base.StepFailed(f"Lidl's leaflet feed did not answer ({type(exc).__name__}).") \
            from exc
    if response.status_code == 403:
        raise base.Blocked("Lidl's leaflet feed refuses automated access (HTTP 403).")
    try:
        data = response.json()
    except ValueError:
        data = None
    if response.status_code != 200 or not isinstance(data, dict) or not data.get("success"):
        raise base.StepFailed(f"Lidl's leaflet feed answered HTTP {response.status_code} "
                              f"for {response.url}.")
    return data
