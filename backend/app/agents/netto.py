"""Netto: per-store Publitas Handzettel, full Chromium only (headless_shell is blocked).
Flow: Online-Prospekte -> filialfinder -> pick a store -> wochenprospekt.netto-online.de/
hz<WW>_posb. PLZ autocomplete is blocked under automation, so region is "national"."""
from __future__ import annotations

import asyncio
from datetime import date

from app.agents.base import (
    OnFrame,
    ProspektPages,
    accept_cookies,
    attach_image_capture,
    browser_context,
    current_week_bounds,
    dismiss_popups,
    paginate_capture,
    publitas_best_pages,
    stream_frames,
)
from app.core.logging import get_logger

log = get_logger(__name__)

_ONLINE_PROSPEKTE = "https://www.netto-online.de/ueber-netto/Online-Prospekte.chtm"


class NettoRecipe:
    name = "netto"

    def region_key(self, zip_code: str) -> str:
        return "national"

    async def fetch(
        self,
        zip_code: str,
        max_pages: int = 80,
        on_frame: OnFrame | None = None,
        on_capture: OnFrame | None = None,
    ) -> ProspektPages:
        vf, vt = current_week_bounds()
        week = date.today().isocalendar().week

        # Phase 1: discover the current-week Handzettel URL by selecting a store.
        target = await self._find_handzettel(week, on_frame)
        if not target:
            return ProspektPages("netto", "national", None, None, [])
        log.info("netto.prospekt", target=target)

        # Phase 2: capture the flipbook in a fresh single-page context (the storeid is in the URL).
        # A second page in the store-flow context never advances the Publitas viewer; a clean
        # single-page context does. Each new page uuid is refetched at high width and streamed as
        # it appears (on_capture) so extraction overlaps the flip; else fall back to batch.
        captured: dict[str, bytes] = {}
        async with browser_context(stealth=True, channel="chromium") as ctx:
            viewer = await ctx.new_page()
            stream_frames(viewer, on_frame)
            dismiss_popups(viewer)
            tasks = attach_image_capture(
                viewer, captured, "/pages/", min_bytes=3000,
                on_capture=on_capture, publitas_stream=on_capture is not None,
            )
            await viewer.goto(target, wait_until="domcontentloaded", timeout=60000)
            await viewer.wait_for_timeout(5000)
            await paginate_capture(viewer, captured, max_pages, max_stagnant=30, settle_ms=900)
        pages = list(await asyncio.gather(*tasks)) if tasks else await publitas_best_pages(captured)
        log.info("netto.captured", pages=len(pages))
        return ProspektPages("netto", "national", vf, vt, pages)

    async def _find_handzettel(self, week: int, on_frame: OnFrame | None = None) -> str | None:
        """Store flow -> the current-week Handzettel flipbook URL (or None)."""
        async with browser_context(stealth=True, channel="chromium") as ctx:
            page = await ctx.new_page()
            stream_frames(page, on_frame)
            await page.goto(_ONLINE_PROSPEKTE, wait_until="domcontentloaded", timeout=60000)
            await accept_cookies(page)
            await page.wait_for_timeout(2000)

            # Filial-Angebote "Zum Prospekt" -> filialfinder (RedirectURL back to the store page)
            href = await page.evaluate(
                "() => { const a = [...document.querySelectorAll('a')].find(e =>"
                " /zum prospekt/i.test(e.innerText||'')"
                " && /filialfinder/i.test(e.getAttribute('href')||'')); return a ? a.href : null; }"
            )
            if not href:
                log.warning("netto.no_filialfinder")
                return None
            await page.goto(href, wait_until="domcontentloaded", timeout=60000)
            await accept_cookies(page)
            await page.wait_for_timeout(2000)

            # pick the first real listed store (skip "Ich möchte keine Filiale")
            loc = page.locator('a:has-text("Filiale wählen"), button:has-text("Filiale wählen")')
            picked = False
            for i in range(min(await loc.count(), 8)):
                el = loc.nth(i)
                if "keine" in ((await el.inner_text()) or "").lower():
                    continue
                try:
                    await el.scroll_into_view_if_needed(timeout=2000)
                    await el.click(timeout=3000)
                    picked = True
                    break
                except Exception:  # noqa: BLE001
                    continue
            if not picked:
                log.warning("netto.no_store")
                return None
            await page.wait_for_timeout(5000)

            # the store page lists weekly Handzettel: hz<WW>_posb - pick the current ISO week
            links = await page.eval_on_selector_all(
                "a",
                "els => els.map(e => e.getAttribute('href')||'')"
                ".filter(h => /wochenprospekt\\.netto-online\\.de\\/hz/.test(h))",
            )
            if not links:
                log.warning("netto.no_handzettel")
                return None
            return next(
                (u for u in links if f"hz{week}_" in u or f"hz{week:02d}_" in u), links[0]
            )
