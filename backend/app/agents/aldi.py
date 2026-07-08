"""Aldi Sued official prospekt recipe (deterministic Playwright).

The weekly leaflet lives in a Publitas viewer at `prospekt.aldi-sued.de/kw<WW>-<YY>-...` whose pages
are plain JPEGs (`/pages/<uuid>-at<width>.jpg`, several widths per page). We flip with ArrowRight,
capture the page images, and keep the best-resolution variant of each page (`publitas_best_pages`).
Aldi Nord (aldi-nord.de) is a separate region, not yet implemented.
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, timedelta

from app.agents.base import (
    OnFrame,
    ProspektPages,
    accept_cookies,
    attach_image_capture,
    browser_context,
    dismiss_popups,
    paginate_capture,
    publitas_best_pages,
    stream_frames,
)
from app.core.logging import get_logger

log = get_logger(__name__)

_LISTING = "https://www.aldi-sued.de/angebote"
_IMAGE_PATH = "/pages/"
_KW = re.compile(r"prospekt\.aldi-sued\.de/(kw(\d{2})-(\d{2})-[a-z-]+)", re.I)


def _iso_week_dates(week: int, yy: int) -> tuple[date | None, date | None]:
    try:
        mon = date.fromisocalendar(2000 + yy, week, 1)
    except ValueError:
        return None, None
    return mon, mon + timedelta(days=5)  # Mon..Sat


def _pick_current(links: list[str]) -> tuple[str | None, date | None, date | None]:
    current = date.today().isocalendar().week
    cands = []
    for u in links:
        if "/page/" in u:
            continue
        m = _KW.search(u)
        if m:
            cands.append((int(m.group(2)), int(m.group(3)), m.group(1)))
    if not cands:
        return None, None, None

    # prefer the current ISO week + the main leaflet (op-mp), else the nearest week
    def _key(c: tuple[int, int, str]) -> tuple:
        week, _yy, slug = c
        return (week == current, "op-mp" in slug, -abs(week - current))

    cands.sort(key=_key, reverse=True)
    week, yy, slug = cands[0]
    vf, vt = _iso_week_dates(week, yy)
    return f"https://prospekt.aldi-sued.de/{slug}", vf, vt


class AldiRecipe:
    name = "aldi"

    def region_key(self, zip_code: str) -> str:
        return "sued"

    async def fetch(
        self,
        zip_code: str,
        max_pages: int = 80,
        on_frame: OnFrame | None = None,
        on_capture: OnFrame | None = None,
    ) -> ProspektPages:
        # Publitas pages arrive at several widths. When a live consumer is attached (on_capture),
        # each new page uuid is refetched at high width and streamed the moment it appears, so
        # extraction overlaps browsing; otherwise fall back to the batch best-width pass.
        captured: dict[str, bytes] = {}
        async with browser_context() as ctx:
            page = await ctx.new_page()
            stream_frames(page, on_frame)
            dismiss_popups(page)
            tasks = attach_image_capture(
                page, captured, _IMAGE_PATH, min_bytes=20000,
                on_capture=on_capture, publitas_stream=on_capture is not None,
            )
            await page.goto(_LISTING, wait_until="domcontentloaded", timeout=60000)
            await accept_cookies(page)
            await page.wait_for_timeout(3000)
            hrefs = await page.eval_on_selector_all("a", "els=>[...new Set(els.map(e=>e.href))]")
            links = [h for h in hrefs if "prospekt.aldi-sued.de/kw" in h.lower()]
            url, vf, vt = _pick_current(links)
            if not url:
                log.warning("aldi.no_prospekt_link")
                return ProspektPages("aldi", "sued", None, None, [])
            log.info("aldi.prospekt", url=url, valid_from=str(vf), valid_to=str(vt))
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await accept_cookies(page)
            await page.wait_for_timeout(4000)
            await paginate_capture(page, captured, max_pages)
        pages = list(await asyncio.gather(*tasks)) if tasks else await publitas_best_pages(captured)
        log.info("aldi.captured", pages=len(pages))
        return ProspektPages("aldi", "sued", vf, vt, pages)
