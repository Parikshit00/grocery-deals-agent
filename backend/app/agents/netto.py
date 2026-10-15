"""Netto Marken-Discount (tier 2): the weekly Publitas leaflet on wochenprospekt.netto-online.de.

www.netto-online.de and its store finder answer honest clients with an Akamai 403 (robots.txt
unreadable), so there is no store: one unlocated store stands for the only live edition, `posb`,
and its offers show in the offer wall but never in nearby plans. The leaflet of ISO week ww is
`/hz<ww>_posb` (zero-padded: hz01, hz09). Publitas answers an offline slug with 200 and the title
"Diese Publikation ist offline", an unknown one with 404; slugs repeat every year. Its
`spreads.json` lists every page with resized images (paths relative to the host) and a text
layer; the page names the PDF (`downloadPdfUrl`, on view.publitas.com). The week's dates come
from that text ("12.10.-17.10.26"); a leaflet printing only other weeks' dates is last year's and
skipped. wochenprospekt.netto-online.de and view.publitas.com have no robots.txt (404).
"""
from __future__ import annotations

import json
import re
import time
from datetime import date, timedelta
from typing import Any
from urllib.parse import urljoin

import httpx

from app.agents.base import (
    Acquired,
    Blocked,
    Leaflet,
    Page,
    ScanCtx,
    StepFailed,
    Store,
    iso_key,
    iso_week,
    parse_validity,
)
from app.core.logging import get_logger

log = get_logger(__name__)

NAME = "netto"
LABEL = "Netto"
# Leaflet prices: "Nur mit der Netto plus App", "App Preis", "ohne PAYBACK 5.55 / mit 4.99".
LOYALTY = "Netto plus App / PAYBACK"
DOMAINS = ("wochenprospekt.netto-online.de", "view.publitas.com")
TIER = 2  # acquisition tier: 1 structured feed, 2 leaflet pages via the VLM, 3 browser

BASE = "https://wochenprospekt.netto-online.de"
EDITION = "posb"
PAGE_TTL_S = 600.0  # leaflets() and acquire() of one scan share one fetch
IMAGE_PX = (1200, 1600)  # wanted longest side of a page image

_PDF = re.compile(r'"downloadPdfUrl":("[^"]*")')
_FIT = re.compile(r"/fit-in/(\d+)x(\d+)/")
_WEEKDAY = re.compile(r"\b(?:Montag|Dienstag|Mittwoch|Donnerstag|Freitag|Samstag)\s*")

_cache: dict[str, tuple[float, tuple[str | None, Any] | None]] = {}  # slug -> (expiry, body)


def slug(monday: date) -> str:
    """Publitas slug of an ISO week's leaflet: 2026-12-28 -> "hz53_posb"."""
    return f"hz{monday.isocalendar().week:02d}_{EDITION}"


def offline(html: str) -> bool:
    return "Diese Publikation ist offline" in html


def pdf_url(html: str) -> str | None:
    m = _PDF.search(html)
    return json.loads(m[1]) if m else None


def parse_spreads(spreads: list[dict], url: str) -> tuple[list[Page], int]:
    """(pages with an image, all pages) of a spreads.json; image paths resolved against url."""
    pages = [p for s in spreads for p in s.get("pages", [])]
    out = []
    for p in pages:
        sizes = [u for u in (p.get("images") or {}).values() if isinstance(u, str)]
        if sizes:
            out.append(Page(urljoin(url, min(sizes, key=_px_off)), p.get("text") or None))
    return out, len(pages)


def parse_dates(
    texts: list[str | None], monday: date, today: date
) -> tuple[date, date, str] | None:
    """The week's printed dates, or Monday to Saturday (derived) when none are printed.

    Ranges starting in another week are promotions; when every printed range is one, the leaflet
    is another year's (None). Of the week's ranges (those ending by Sunday preferred) the earliest
    start with its latest end wins: "22.10.-24.10." weekend and "19.10.-21.10." windows are part
    of the 19.10.-24.10. leaflet. Weekday names are dropped ("Montag 19.10. - Samstag 24.10.26").
    """
    ranges = {r for t in texts for chunk in (t or "").split("\n\n")
              if (r := parse_validity(_WEEKDAY.sub("", chunk), today))}
    week = [r for r in ranges if iso_week(r[0])[0] == monday]
    if week:
        week = [r for r in week if r[1] <= monday + timedelta(days=6)] or week
        start = min(r[0] for r in week)
        return start, max(r[1] for r in week if r[0] == start), "text"
    return None if ranges else (monday, monday + timedelta(days=5), "derived")


async def stores(ctx: ScanCtx) -> list[Store]:
    """The finder refuses honest clients: the edition only, without a position."""
    return [Store(id=None, name="Netto", address="", lat=None, lon=None, edition=EDITION,
                  located=False)]


async def leaflets(ctx: ScanCtx, store: Store) -> list[Leaflet]:
    """This week's leaflet unless it is over, and next week's once online."""
    this_monday = iso_week(ctx.today)[0]
    out = []
    for monday in (this_monday, this_monday + timedelta(days=7)):
        name = slug(monday)
        try:
            found = await _publication(ctx, name)
        except (StepFailed, Blocked):
            if monday == this_monday:
                raise
            log.warning("netto.next_week_failed", slug=name)  # next week is best effort
            continue
        if found is None:
            log.info("netto.not_online", slug=name)
            continue
        pdf, spreads = found
        texts = [p.get("text") for s in spreads for p in s.get("pages", [])]
        dates = parse_dates(texts, monday, ctx.today)
        if dates is None:
            log.warning("netto.stale", slug=name)  # a previous year's leaflet under the slug
            continue
        start, end, source = dates
        if end < ctx.today:
            continue
        out.append(Leaflet(
            key=f"{iso_key(monday)}-{EDITION}",
            title=f"Prospekt KW {monday.isocalendar().week}",
            kind="main",
            valid_from=start,
            valid_to=end,
            dates_source=source,
            source_url=f"{BASE}/{name}/",
            pdf_url=pdf,
        ))
    if not out:
        raise StepFailed(f"Netto's leaflet {slug(this_monday)} is not online.")
    return out


async def acquire(ctx: ScanCtx, store: Store, leaflet: Leaflet) -> Acquired:
    """Every page image with its text layer; none (incomplete) while a page lacks its image.

    The runtime keys resumes and offer pages on the list index, so a partial list would shift.
    """
    name = slug(iso_week(leaflet.valid_from)[0])
    found = await _publication(ctx, name)
    if found is None:
        raise StepFailed(f"Netto's leaflet {name} is no longer online.")
    pages, total = parse_spreads(found[1], f"{BASE}/{name}/spreads.json")
    if not pages or len(pages) != total:
        log.warning("netto.pages_without_image", slug=name, missing=total - len(pages))
        return Acquired(tier=2, pages=[], complete=False)
    return Acquired(tier=2, pages=pages, complete=True)


async def _publication(ctx: ScanCtx, name: str) -> tuple[str | None, list[dict]] | None:
    """(PDF URL, spreads) of an online leaflet, None when offline or unknown; cached."""
    hit = _cache.get(name)
    if hit is None or hit[0] <= time.monotonic():
        html = await _get(ctx, f"{BASE}/{name}")
        body = None
        if html is not None and not offline(html.text):
            spreads = await _get(ctx, f"{BASE}/{name}/spreads.json")
            if spreads is not None:
                try:
                    data = spreads.json()
                except ValueError as exc:
                    raise StepFailed(f"Netto's leaflet {name} has no readable page list.") from exc
                if not isinstance(data, list) or not all(isinstance(s, dict) for s in data):
                    raise StepFailed(f"Netto's leaflet {name} has no readable page list.")
                body = (pdf_url(html.text), data)
        hit = _cache[name] = (time.monotonic() + PAGE_TTL_S, body)
    return hit[1]


async def _get(ctx: ScanCtx, url: str) -> httpx.Response | None:
    """GET; None for 404 (no such publication), Blocked for 403, StepFailed otherwise."""
    try:
        response = await ctx.http.get(url)
    except httpx.HTTPError as exc:
        raise StepFailed(f"Netto's leaflet host did not answer ({type(exc).__name__}).") from exc
    if response.status_code == 404:
        return None
    if response.status_code == 403:
        raise Blocked("Netto's leaflet host refuses automated access (HTTP 403).")
    if response.status_code != 200:
        raise StepFailed(f"Netto's leaflet host answered HTTP {response.status_code} for {url}.")
    return response


def _px_off(url: str) -> int:
    """How far an image's longest side (from its "fit-in/WxH" path) is outside IMAGE_PX."""
    m = _FIT.search(url)
    side = max(int(m[1]), int(m[2])) if m else 0
    return max(IMAGE_PX[0] - side, side - IMAGE_PX[1], 0)
