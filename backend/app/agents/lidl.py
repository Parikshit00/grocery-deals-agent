"""Lidl: weekly Aktionsprospekt slug carries the validity dates; the viewer lazy-loads page
images from the leaflets.schwarz CDN. National prospekt."""
from __future__ import annotations

import re
from datetime import date

from app.agents.base import (
    OnFrame,
    ProspektPages,
    accept_cookies,
    attach_image_capture,
    browser_context,
    dismiss_popups,
    paginate_capture,
    stream_frames,
)
from app.core.logging import get_logger

log = get_logger(__name__)

_LISTING = "https://www.lidl.de/c/online-prospekte/s10005610"
_IMAGE_HOST = "leaflets.schwarz"
_SLUG_DATES = re.compile(
    r"aktionsprospekt-(\d{2})-(\d{2})-(\d{4})-(\d{2})-(\d{2})-(\d{4})", re.I
)


def _parse_dates(url: str) -> tuple[date | None, date | None]:
    m = _SLUG_DATES.search(url)
    if not m:
        return None, None
    d1, m1, y1, d2, m2, y2 = (int(x) for x in m.groups())
    try:
        return date(y1, m1, d1), date(y2, m2, d2)
    except ValueError:
        return None, None


def _pick_current(links: list[str]) -> tuple[str | None, date | None, date | None]:
    today = date.today()
    dated = [(vf, vt, u) for u in links for vf, vt in [_parse_dates(u)] if vf and vt]
    if not dated:
        return (links[0] if links else None), None, None
    current = [t for t in dated if t[0] <= today <= t[1]]
    vf, vt, url = (current or sorted(dated))[0]
    return url, vf, vt


class LidlRecipe:
    name = "lidl"

    def region_key(self, zip_code: str) -> str:
        return "national"

    async def fetch(
        self,
        zip_code: str,
        max_pages: int = 80,
        on_frame: OnFrame | None = None,
        on_capture: OnFrame | None = None,
    ) -> ProspektPages:
        captured: dict[str, bytes] = {}
        async with browser_context() as ctx:
            page = await ctx.new_page()
            stream_frames(page, on_frame)
            dismiss_popups(page)
            attach_image_capture(page, captured, _IMAGE_HOST, on_capture=on_capture)
            await page.goto(_LISTING, wait_until="domcontentloaded", timeout=60000)
            await accept_cookies(page)
            await page.wait_for_timeout(2500)
            hrefs = await page.eval_on_selector_all(
                "a", "els=>[...new Set(els.map(e=>e.href))]"
            )
            links = [u for u in hrefs if "/l/prospekte/aktionsprospekt-" in u.lower()]
            url, vf, vt = _pick_current(links)
            if not url:
                log.warning("lidl.no_prospekt_link")
                return ProspektPages("lidl", "national", None, None, [])
            log.info("lidl.prospekt", valid_from=str(vf), valid_to=str(vt))
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await accept_cookies(page)
            await page.wait_for_timeout(4000)
            await paginate_capture(page, captured, max_pages)
        log.info("lidl.captured", pages=len(captured))
        return ProspektPages("lidl", "national", vf, vt, list(captured.values()))
