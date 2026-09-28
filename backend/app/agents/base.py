"""Shared Playwright helpers for the chain modules."""
from __future__ import annotations

import asyncio
import io
from collections.abc import Callable
from contextlib import asynccontextmanager

from PIL import Image
from playwright.async_api import Page, async_playwright

from app.core.logging import get_logger

log = get_logger(__name__)

_COOKIE_SELECTORS = (
    '[data-testid="uc-accept-all-button"]',
    "#onetrust-accept-btn-handler",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll",
    'button:has-text("Alle akzeptieren")',
    'button:has-text("Alle erlauben")',
    'button:has-text("Alle Cookies akzeptieren")',
    'button:has-text("Akzeptieren")',
    'button:has-text("Zustimmen")',
    'button:has-text("Einverstanden")',
)
# Named/labelled dismissers only - a bare ".close" or overlay click could kill a leaflet viewer.
_DISMISS_SELECTORS = _COOKIE_SELECTORS + (
    '[aria-label*="schließen" i]',
    '[aria-label*="close" i]',
    ".modal__close",
    'button:has-text("Ablehnen")',
    'button:has-text("Später")',
    'button:has-text("Nein, danke")',
    'button:has-text("Kein Danke")',
    'button:has-text("Schließen")',
)

OnFrame = Callable[[bytes], None]


def jpeg_thumb(data: bytes, width: int = 640, quality: int = 55) -> bytes:
    """Downscale any image to a small JPEG for streaming to the UI."""
    im = Image.open(io.BytesIO(data)).convert("RGB")
    if im.width > width:
        im.thumbnail((width, width * 4))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=quality)
    return buf.getvalue()


_frame_tasks: set[asyncio.Task] = set()


def stream_frames(page: Page, on_frame: OnFrame | None, interval_ms: int = 700) -> None:
    """Stream live JPEG snapshots of `page` to `on_frame` until the page closes.

    Feeds the per-agent viewport in the UI. Fire-and-forget: the loop ends itself when the
    page or browser goes away.
    """
    if on_frame is None:
        return

    async def loop() -> None:
        while True:
            try:
                shot = await page.screenshot(type="jpeg", quality=55)
                on_frame(jpeg_thumb(shot))
            except Exception:  # noqa: BLE001 - page/browser closed; stop streaming
                return
            await asyncio.sleep(interval_ms / 1000)

    task = asyncio.create_task(loop())
    _frame_tasks.add(task)
    task.add_done_callback(_frame_tasks.discard)


def dismiss_popups(page: Page, interval_ms: int = 1500) -> None:
    """Keep dismissing cookie banners and promo popups for the page's whole lifetime.

    The one-shot accept_cookies stays as the fast path; this sentinel catches banners that load
    late (parallel-launch contention) and promo overlays that appear mid-browse. Fire-and-forget,
    ends itself when the page closes.
    """

    async def loop() -> None:
        while True:
            for sel in _DISMISS_SELECTORS:
                try:
                    el = page.locator(sel).first
                    if await el.is_visible():
                        await el.click(force=True, timeout=400)
                        log.info("popup.dismissed", selector=sel)
                except Exception:  # noqa: BLE001 - page closed or element contested; keep going
                    if page.is_closed():
                        return
            await asyncio.sleep(interval_ms / 1000)

    task = asyncio.create_task(loop())
    _frame_tasks.add(task)
    task.add_done_callback(_frame_tasks.discard)


@asynccontextmanager
async def browser_context(headless: bool = True):
    """Plain Chromium with its default (honest) user agent; no fingerprint masking."""
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        ctx = await browser.new_context(
            locale="de-DE",
            viewport={"width": 1440, "height": 1100},
            timezone_id="Europe/Berlin",
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
