"""Penny: no usable flipbook headless; offers render as cards after market selection
(consent -> PLZ -> branch -> "Als Liste") -> screenshots for the VLM. Region key = postcode."""
from __future__ import annotations

from app.agents.base import (
    OnFrame,
    ProspektPages,
    accept_cookies,
    browser_context,
    capture_screenshots,
    current_week_bounds,
    dismiss_popups,
    stream_frames,
)
from app.core.logging import get_logger

log = get_logger(__name__)

_URL = "https://www.penny.de/angebote#handout"


class PennyRecipe:
    name = "penny"

    def region_key(self, zip_code: str) -> str:
        return zip_code.strip()

    async def fetch(
        self,
        zip_code: str,
        max_pages: int = 40,
        on_frame: OnFrame | None = None,
        on_capture: OnFrame | None = None,
    ) -> ProspektPages:
        zip_code = zip_code.strip()
        vf, vt = current_week_bounds(6)  # Penny offers run through Sunday
        shots: list[bytes] = []
        async with browser_context() as ctx:
            page = await ctx.new_page()
            stream_frames(page, on_frame)
            # Accept consent on the homepage first: on /angebote the market popup overlaps the
            # consent banner so "Alle erlauben" can't be clicked. The cookie is context-wide.
            await page.goto("https://www.penny.de/", wait_until="domcontentloaded", timeout=60000)
            await accept_cookies(page)
            await page.wait_for_timeout(2000)
            await page.goto(_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(2500)

            # market popup: enter the postcode and search
            try:
                await page.fill("input.zip-search__input", zip_code, timeout=10000)
                await page.keyboard.press("Enter")
            except Exception:  # noqa: BLE001
                log.warning("penny.zip_input_missing")
            await page.wait_for_timeout(3500)

            # select the nearest branch (top of the distance-sorted list)
            try:
                await page.get_by_role("button", name="Auswählen", exact=True).first.click(
                    timeout=10000
                )
            except Exception:  # noqa: BLE001
                log.warning("penny.branch_select_missing")
            await page.wait_for_timeout(5000)

            # ensure the "Als Liste" view (all offers as cards in the main page)
            try:
                await page.get_by_text("Als Liste", exact=True).first.click(timeout=4000)
                await page.wait_for_timeout(1500)
            except Exception:  # noqa: BLE001 - already on the list view
                pass

            # market chosen; now clear any promo overlays that would block scrolling
            dismiss_popups(page)
            shots, complete = await capture_screenshots(
                page, max_tiles=max_pages, on_capture=on_capture
            )
        log.info("penny.captured", pages=len(shots), region=zip_code, complete=complete)
        return ProspektPages("penny", zip_code, vf, vt, shots, complete)
