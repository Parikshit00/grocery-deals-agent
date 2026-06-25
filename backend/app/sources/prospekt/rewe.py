"""Rewe offers recipe (VLM-browse, deterministic navigation).

REWE markets are individual, so offers are reached via the market search: pick the nearest market
for the postcode, open its offers page (offers render as HTML cards), screenshot it, and let the VLM
read them. REWE Group uses the same "Alle erlauben" consent as Penny (handled by accept_cookies).
"""
from __future__ import annotations

from app.core.logging import get_logger
from app.sources.prospekt.base import (
    ProspektPages,
    accept_cookies,
    browser_context,
    capture_screenshots,
    current_week_bounds,
)

log = get_logger(__name__)

_MARKTSUCHE = "https://www.rewe.de/marktsuche"


class ReweRecipe:
    name = "rewe"

    def region_key(self, zip_code: str) -> str:
        return zip_code.strip()

    async def fetch(self, zip_code: str, max_pages: int = 40) -> ProspektPages:
        zip_code = zip_code.strip()
        vf, vt = current_week_bounds()
        shots: list[bytes] = []
        async with browser_context(stealth=True) as ctx:
            page = await ctx.new_page()
            await page.goto(_MARKTSUCHE, wait_until="domcontentloaded", timeout=60000)
            await accept_cookies(page)
            await page.wait_for_timeout(3000)

            try:
                await page.fill('input[type="search"]:visible', zip_code, timeout=10000)
                await page.keyboard.press("Enter")
            except Exception:  # noqa: BLE001
                log.warning("rewe.search_input_missing")
            await page.wait_for_timeout(4500)

            # each market links to its own offers page: /angebote/<city>/<id>/<slug>/
            href = await page.evaluate(
                "() => { const a = [...document.querySelectorAll('a')]"
                ".find(e => /^\\/angebote\\/[^\\/]+\\/\\d+\\//.test(e.getAttribute('href')||''));"
                " return a ? a.getAttribute('href') : null; }"
            )
            if not href:
                log.warning("rewe.no_market", zip=zip_code)
                return ProspektPages("rewe", zip_code, None, None, [])
            log.info("rewe.market", href=href)
            await page.goto(
                "https://www.rewe.de" + href, wait_until="domcontentloaded", timeout=60000
            )
            await accept_cookies(page)
            await page.wait_for_timeout(3000)
            shots = await capture_screenshots(page, max_tiles=max_pages)
        log.info("rewe.captured", pages=len(shots), region=zip_code)
        return ProspektPages("rewe", zip_code, vf, vt, shots)
