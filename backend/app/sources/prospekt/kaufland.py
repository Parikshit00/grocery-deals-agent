"""Kaufland official prospekt recipe.

Kaufland (Schwarz group, like Lidl) lists weekly leaflets on `leaflets.kaufland.com`; the URL slug
encodes the ISO week (e.g. `..._D25`). Page images come from the same `leaflets.schwarz` CDN as Lidl
and the viewer pages with ArrowRight; the weekly prospekt is effectively national.
"""
from __future__ import annotations

import re
from datetime import date

from app.core.logging import get_logger
from app.sources.prospekt.base import (
    ProspektPages,
    accept_cookies,
    attach_image_capture,
    browser_context,
    paginate_capture,
)

log = get_logger(__name__)

_LISTING = "https://www.kaufland.de/prospekte.html"
_IMAGE_HOST = "leaflets.schwarz"
_WEEK = re.compile(r"_D(\d{2})", re.I)


def _iso_week_dates(week: int) -> tuple[date | None, date | None]:
    try:
        mon = date.fromisocalendar(date.today().year, week, 1)
    except ValueError:
        return None, None
    return mon, date.fromisocalendar(date.today().year, week, 6)  # Mon..Sat


def _pick_current(links: list[str]) -> tuple[str | None, date | None, date | None]:
    current = date.today().isocalendar().week
    weeked = [(int(m.group(1)), u) for u in links for m in [_WEEK.search(u)] if m]
    if not weeked:
        return (links[0] if links else None), None, None
    # prefer the current ISO week + the main weekly leaflet (KDZ), else nearest week
    def _key(wu: tuple[int, str]) -> tuple:
        week, url = wu
        return (week == current, "kdz" in url.lower(), -abs(week - current))

    weeked.sort(key=_key, reverse=True)
    week, url = weeked[0]
    vf, vt = _iso_week_dates(week)
    return url, vf, vt


class KauflandRecipe:
    name = "kaufland"

    def region_key(self, zip_code: str) -> str:
        return "national"

    async def fetch(self, zip_code: str, max_pages: int = 80) -> ProspektPages:
        captured: dict[str, bytes] = {}
        async with browser_context() as ctx:
            page = await ctx.new_page()
            attach_image_capture(page, captured, _IMAGE_HOST)
            await page.goto(_LISTING, wait_until="domcontentloaded", timeout=60000)
            await accept_cookies(page)
            await page.wait_for_timeout(3000)
            hrefs = await page.eval_on_selector_all("a", "els=>[...new Set(els.map(e=>e.href))]")
            links = [h for h in hrefs if "leaflets.kaufland.com" in h.lower()]
            url, vf, vt = _pick_current(links)
            if not url:
                log.warning("kaufland.no_prospekt_link")
                return ProspektPages("kaufland", "national", None, None, [])
            log.info("kaufland.prospekt", valid_from=str(vf), valid_to=str(vt))
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await accept_cookies(page)
            await page.wait_for_timeout(4000)
            await paginate_capture(page, captured, max_pages)
        log.info("kaufland.captured", pages=len(captured))
        return ProspektPages("kaufland", "national", vf, vt, list(captured.values()))
