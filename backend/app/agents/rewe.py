"""REWE (tier 3): markets from the market search API, offers from the market's offers page.

`/api/wksmarketsearch?searchTerm=<plz>` answers honest clients with JSON (markets with wwIdent and
coordinates); robots.txt does not disallow /api/. Every HTML page of www.rewe.de, the market's
offers page `/angebote/<city>/<wwIdent>/<slug>/` included, answers with a Cloudflare managed
challenge ("Zeig uns, dass du ein Mensch bist.", HTTP 403) as of 2026-10-15, in curl and in
headless Chromium alike. acquire() opens that page in an honest browser, waits briefly for the
challenge to clear on its own and otherwise raises Blocked; it never solves or evades it.
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import time
from datetime import timedelta
from typing import Any

import httpx

from app.agents import base, browser
from app.agents.base import (
    Acquired,
    Blocked,
    Leaflet,
    ScanCtx,
    StepFailed,
    Store,
    iso_key,
    iso_week,
)
from app.core.logging import get_logger
from app.schemas.offer import Offer
from app.services.offers import parse_price_cents

log = get_logger(__name__)

NAME = "rewe"
LABEL = "REWE"
LOYALTY = "REWE Bonus"  # the REWE app's credit program since 2025 (replaced PAYBACK)
DOMAINS = ("www.rewe.de", "rewe.de")
TIER = 3  # acquisition tier: 1 structured feed, 2 leaflet pages via the VLM, 3 browser

BASE = "https://www.rewe.de"
CHALLENGE_WAIT_S = 20.0  # a non-interactive challenge clears itself within seconds

_urls: dict[str, str] = {}  # market id -> offers page, filled by stores()
_UMLAUT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})
_JSON_SCRIPT = re.compile(r'<script[^>]*type="application/(?:ld\+)?json"[^>]*>(.*?)</script>',
                          re.S)


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower().translate(_UMLAUT)).strip("-")


def offers_url(market: dict) -> str:
    city = _slug(market.get("city", "").split("/")[0]) or "markt"
    name = _slug("rewe markt " + market.get("street", ""))
    return f"{BASE}/angebote/{city}/{market['wwIdent']}/{name}/"


def parse_markets(data: dict) -> list[Store]:
    """Markets with a position; the market id is the edition (offers vary by market)."""
    out = []
    for m in data.get("markets") or []:
        loc = m.get("location") or {}
        if not m.get("wwIdent") or loc.get("latitude") is None or loc.get("longitude") is None:
            continue
        out.append(Store(
            id=str(m["wwIdent"]),
            name=m.get("name") or "REWE",
            address=f"{m.get('street', '')}, {m.get('zipCode', '')} {m.get('city', '')}".strip(
                ", "),
            lat=float(loc["latitude"]),
            lon=float(loc["longitude"]),
            edition=str(m["wwIdent"]),
        ))
    return out


def parse_offers(html: str) -> list[Offer]:
    """Offers from the page's embedded JSON: any object with a title and a priceData.price.

    Unverified against a live page (never reached past the challenge); a page without such
    objects yields none and acquire() reports it.
    """
    found: list[Offer] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            price = (node.get("priceData") or {}).get("price") if isinstance(
                node.get("priceData"), dict) else None
            if isinstance(node.get("title"), str) and price is not None:
                found.append(Offer(
                    chain=NAME, product_name=node["title"], pack_text=node.get("subtitle"),
                    price_cents=parse_price_cents(price), source="feed", price_check="feed"))
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    for raw in _JSON_SCRIPT.findall(html):
        try:
            walk(json.loads(raw))
        except ValueError:
            continue
    return found


async def stores(ctx: ScanCtx) -> list[Store]:
    try:
        response = await ctx.http.get(f"{BASE}/api/wksmarketsearch",
                                      params={"searchTerm": ctx.plz})
    except httpx.HTTPError as exc:
        raise StepFailed(f"REWE's market search did not answer ({type(exc).__name__}).") from exc
    if response.status_code == 403:
        raise Blocked("REWE's market search refuses automated access (HTTP 403).")
    if response.status_code != 200:
        raise StepFailed(f"REWE's market search answered HTTP {response.status_code}.")
    try:
        data = response.json()
    except ValueError as exc:
        raise Blocked("REWE's market search answered with a page, not data (bot protection).") \
            from exc
    _urls.update({str(m["wwIdent"]): offers_url(m) for m in data.get("markets") or []
                  if m.get("wwIdent")})
    return parse_markets(data)



async def leaflets(ctx: ScanCtx, store: Store) -> list[Leaflet]:
    """The market's offers of the current ISO week, Monday to Saturday (derived)."""
    monday = iso_week(ctx.today)[0]
    return [Leaflet(
        key=f"{iso_key(monday)}-{store.edition}",
        title=f"Angebote KW {monday.isocalendar().week}",
        kind="offers",
        valid_from=monday,
        valid_to=monday + timedelta(days=5),
        dates_source="derived",
        source_url=_urls.get(store.edition, f"{BASE}/angebote/"),
    )]


async def acquire(ctx: ScanCtx, store: Store, leaflet: Leaflet) -> Acquired:
    """Open the offers page with live frames; Blocked while a bot challenge stands."""

    def on_frame(jpeg: bytes) -> None:
        ctx.emit({"event": "frame", "image": base64.b64encode(jpeg).decode()})

    url = leaflet.source_url or f"{BASE}/angebote/"
    async with browser.browser_page(DOMAINS) as page:
        t0 = time.monotonic()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
        except Exception as exc:  # noqa: BLE001 - a challenge may keep the load pending
            log.info("rewe.goto", error=str(exc))
        # Frames start after the navigation: a screenshot during it ends base.stream_frames.
        base.stream_frames(page, on_frame)
        while await browser.is_challenge(page):
            if time.monotonic() - t0 > CHALLENGE_WAIT_S:
                log.warning("rewe.blocked", url=url, s=round(time.monotonic() - t0, 1))
                raise Blocked("REWE blocks automated access (bot protection).")
            await asyncio.sleep(1)
        offers = parse_offers(await page.content())
    if not offers:
        raise StepFailed("REWE's offers page loaded but showed no offer data.")
    for o in offers:
        o.valid_from, o.valid_to = leaflet.valid_from, leaflet.valid_to
    return Acquired(tier=3, offers=offers)
