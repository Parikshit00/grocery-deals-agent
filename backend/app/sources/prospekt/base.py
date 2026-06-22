"""Shared Playwright helpers + the Recipe protocol for browsing official retailer prospekts.

A recipe navigates a retailer's official online prospekt for a region and returns the leaflet
page images (bytes). The deterministic helpers here keep the LLM out of the navigation loop.
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from typing import Protocol

from playwright.async_api import Page, async_playwright

from app.core.logging import get_logger

log = get_logger(__name__)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36"
)
_COOKIE_SELECTORS = (
    "#onetrust-accept-btn-handler",
    'button:has-text("Alle akzeptieren")',
    'button:has-text("Akzeptieren")',
    'button:has-text("Zustimmen")',
)


@dataclass
class ProspektPages:
    retailer: str
    region_key: str
    valid_from: date | None
    valid_to: date | None
    pages: list[bytes]


class Recipe(Protocol):
    name: str

    def region_key(self, zip_code: str) -> str: ...

    async def fetch(self, zip_code: str, max_pages: int = 80) -> ProspektPages: ...


@asynccontextmanager
async def browser_context(headless: bool = True):
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(
            locale="de-DE", user_agent=_UA, viewport={"width": 1440, "height": 1100}
        )
        try:
            yield ctx
        finally:
            await browser.close()


async def accept_cookies(page: Page) -> None:
    for sel in _COOKIE_SELECTORS:
        try:
            await page.click(sel, timeout=3000)
            return
        except Exception:  # noqa: BLE001 - banner may be absent or already dismissed
            continue


def attach_image_capture(
    page: Page, store: dict[str, bytes], url_contains: str, min_bytes: int = 50000
) -> None:
    """Collect leaflet page images by intercepting image responses from the leaflet CDN."""

    async def on_response(resp):
        if url_contains not in resp.url:
            return
        if not resp.headers.get("content-type", "").startswith("image/"):
            return
        try:
            body = await resp.body()
        except Exception:  # noqa: BLE001 - response body may be gone
            return
        if len(body) >= min_bytes:
            store.setdefault(resp.url, body)

    page.on("response", on_response)


async def paginate_capture(
    page: Page, store: dict[str, bytes], max_pages: int, max_stagnant: int = 8
) -> None:
    """Flip a leaflet viewer with ArrowRight until no new pages load (bounded; no LLM, no loops)."""
    await page.mouse.click(700, 550)  # focus the viewer
    last, stagnant = 0, 0
    for _ in range(max_pages + max_stagnant):
        await page.keyboard.press("ArrowRight")
        await page.wait_for_timeout(500)
        if len(store) == last:
            stagnant += 1
            if stagnant >= max_stagnant:
                break
        else:
            stagnant, last = 0, len(store)
        if len(store) >= max_pages:
            break
