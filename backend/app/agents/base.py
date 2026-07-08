"""Shared Playwright helpers + the Recipe protocol. Deterministic navigation only - no LLM
in the browse loop."""
from __future__ import annotations

import asyncio
import io
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Protocol

import httpx
from PIL import Image
from playwright.async_api import Page, async_playwright

from app.core.logging import get_logger

log = get_logger(__name__)

# Publitas leaflet viewers (Aldi Sued, Netto) serve pages as <uuid>-at<width>.jpg at several widths.
_PUBLITAS_PAGE = re.compile(r"/pages/([0-9a-f-]+)-at(\d+)\.jpg", re.I)
_PUBLITAS_AT = re.compile(r"-at\d+\.jpg", re.I)

_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124 Safari/537.36"
)
# Realistic fingerprint for sites with bot walls (Netto/Rewe).
_STEALTH_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)
_STEALTH_HEADERS = {
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.8",
    "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not?A_Brand";v="24"',
    "sec-ch-ua-platform": '"Windows"',
    "sec-ch-ua-mobile": "?0",
}
_STEALTH_JS = (
    "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
    "window.chrome={runtime:{}};"
)
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


def current_week_bounds(days: int = 5) -> tuple[date, date]:
    """Validity span for a weekly prospekt: this week's Monday .. Monday+`days` (default Sat)."""
    monday = date.today() - timedelta(days=date.today().weekday())
    return monday, monday + timedelta(days=days)


@dataclass
class ProspektPages:
    retailer: str
    region_key: str
    valid_from: date | None
    valid_to: date | None
    pages: list[bytes]
    # False when the capture demonstrably stopped early (e.g. never reached the page bottom);
    # incomplete fetches are returned to the UI but never cached.
    complete: bool = True


OnFrame = Callable[[bytes], None]


class Recipe(Protocol):
    name: str

    def region_key(self, zip_code: str) -> str: ...

    async def fetch(
        self,
        zip_code: str,
        max_pages: int = 80,
        on_frame: OnFrame | None = None,
        on_capture: OnFrame | None = None,
    ) -> ProspektPages: ...


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
async def browser_context(headless: bool = True, stealth: bool = False, channel: str | None = None):
    # channel="chromium" uses the full Chromium (new headless) instead of the lightweight
    # headless_shell - some sites (Netto) only respond to the full browser fingerprint.
    args = ["--disable-blink-features=AutomationControlled"] if stealth else []
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless, args=args, channel=channel)
        ctx = await browser.new_context(
            locale="de-DE",
            user_agent=_STEALTH_UA if stealth else _UA,
            viewport={"width": 1440, "height": 1100},
            timezone_id="Europe/Berlin",
            extra_http_headers=_STEALTH_HEADERS if stealth else {},
        )
        if stealth:
            await ctx.add_init_script(_STEALTH_JS)
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
    page: Page,
    store: dict[str, bytes],
    url_contains: str,
    min_bytes: int = 50000,
    on_capture: OnFrame | None = None,
    publitas_stream: bool = False,
) -> list[asyncio.Task]:
    """Collect leaflet page images by intercepting image responses from the leaflet CDN.

    `on_capture` receives each new page's bytes as it arrives, so extraction can start while
    the viewer is still flipping. With `publitas_stream`, on_capture instead fires once per NEW
    Publitas page uuid with bytes refetched at high width (viewers emit several widths per page;
    streaming raw captures would extract thumbnails and duplicates). Returns the refetch tasks -
    Publitas recipes must gather them before returning so no streamed page is lost.
    """
    seen_uuids: set[str] = set()
    tasks: list[asyncio.Task] = []

    async def hi_res(url: str, body: bytes) -> bytes:
        async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": _STEALTH_UA}) as client:
            data = await _publitas_hi(client, url, body)
        if on_capture:
            on_capture(data)
        return data

    async def on_response(resp):
        if url_contains not in resp.url:
            return
        if not resp.headers.get("content-type", "").startswith("image/"):
            return
        try:
            body = await resp.body()
        except Exception:  # noqa: BLE001 - response body may be gone
            return
        if len(body) >= min_bytes and resp.url not in store:
            store[resp.url] = body
            if on_capture:
                if publitas_stream:
                    m = _PUBLITAS_PAGE.search(resp.url)
                    if m and m.group(1) not in seen_uuids:
                        seen_uuids.add(m.group(1))
                        tasks.append(asyncio.create_task(hi_res(resp.url, body)))
                else:
                    on_capture(body)

    page.on("response", on_response)
    return tasks


async def paginate_capture(
    page: Page,
    store: dict[str, bytes],
    max_pages: int,
    max_stagnant: int = 8,
    settle_ms: int = 500,
) -> None:
    """Flip a leaflet viewer with ArrowRight until no new pages load (bounded; no LLM, no loops).

    `settle_ms` is how long to wait for each page image to load after a flip - slower viewers
    (e.g. Netto) need more, or pagination stops early thinking no new page arrived.
    """
    await page.mouse.click(700, 550)  # focus the viewer
    last, stagnant = 0, 0
    for _ in range(max_pages + max_stagnant):
        await page.keyboard.press("ArrowRight")
        await page.wait_for_timeout(settle_ms)
        if len(store) == last:
            stagnant += 1
            if stagnant >= max_stagnant:
                break
        else:
            stagnant, last = 0, len(store)
        if len(store) >= max_pages:
            break


# Every img intersecting the viewport has finished loading (lazy loaders included).
_VIEWPORT_IMAGES_LOADED = """() => {
  const vh = window.innerHeight;
  return [...document.querySelectorAll('img')].every((img) => {
    const r = img.getBoundingClientRect();
    if (r.bottom < 0 || r.top > vh || r.width === 0) return true;
    return img.complete && img.naturalWidth > 0;
  });
}"""


async def capture_screenshots(
    page: Page,
    max_tiles: int = 40,
    overlap: float = 0.15,
    settle_ms: int = 400,
    on_capture: OnFrame | None = None,
) -> tuple[list[bytes], bool]:
    """Scroll a rendered page top to bottom, capturing overlapping viewport screenshots.

    For sites that render offers as HTML (no leaflet image to intercept): the VLM reads these tiles
    the same way it reads leaflet pages. Recomputes scrollHeight each step to follow lazy loading,
    and waits for every image in the viewport to finish loading before each shot (bounded, so a
    broken image can never stall the scan). Returns (tiles, reached_bottom) - False means the
    scroll was cut off by `max_tiles` and the capture is incomplete.
    `on_capture` receives each tile as it is taken, so extraction can start while scrolling.
    """
    vh = (page.viewport_size or {"height": 1100})["height"]
    step = max(1, int(vh * (1 - overlap)))
    shots: list[bytes] = []
    y = 0
    complete = False
    while len(shots) < max_tiles:
        await page.evaluate("(y) => window.scrollTo(0, y)", y)
        await page.wait_for_timeout(settle_ms)
        try:
            await page.wait_for_function(_VIEWPORT_IMAGES_LOADED, timeout=2000)
        except Exception:  # noqa: BLE001 - a stuck image must not stall the scan
            pass
        shots.append(await page.screenshot())
        if on_capture:
            on_capture(shots[-1])
        height = await page.evaluate("() => document.body.scrollHeight")
        if y + vh >= height:
            complete = True
            break
        y += step
    return shots, complete


async def publitas_best_pages(
    captured: dict[str, bytes], hi_widths: tuple[int, ...] = (2000, 1000)
) -> list[bytes]:
    """For a Publitas leaflet (pages are <uuid>-at<width>.jpg): one entry per page, upgraded to the
    largest width the CDN serves (tries `hi_widths` high->low for OCR); fall back to captured bytes.
    Capturing even small thumbnails per page is enough - each is re-fetched here at high resolution.
    """
    best: dict[str, tuple[int, str, bytes]] = {}
    for url, body in captured.items():
        m = _PUBLITAS_PAGE.search(url)
        if not m:
            continue
        uuid, width = m.group(1), int(m.group(2))
        if uuid not in best or width > best[uuid][0]:
            best[uuid] = (width, url, body)

    sem = asyncio.Semaphore(6)

    async def one(client: httpx.AsyncClient, url: str, body: bytes) -> bytes:
        async with sem:
            return await _publitas_hi(client, url, body, hi_widths)

    async with httpx.AsyncClient(timeout=30.0, headers={"User-Agent": _STEALTH_UA}) as client:
        return list(await asyncio.gather(*[one(client, u, b) for _w, u, b in best.values()]))


async def _publitas_hi(
    client: httpx.AsyncClient, url: str, body: bytes, hi_widths: tuple[int, ...] = (2000, 1000)
) -> bytes:
    """Refetch one Publitas page at the highest width the CDN serves; fall back to `body`."""
    for w in hi_widths:
        hi = _PUBLITAS_AT.sub(f"-at{w}.jpg", url)
        try:
            r = await client.get(hi)
            if r.status_code == 200 and len(r.content) >= len(body):
                return r.content
        except Exception:  # noqa: BLE001 - try the next width / fall back below
            continue
    return body
