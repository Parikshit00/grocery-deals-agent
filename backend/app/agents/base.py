"""The chain module contract plus shared HTTP and Playwright helpers.

A chain module (`agents/<chain>.py`) is a plain module exposing:

    NAME: str            registry key, e.g. "penny"
    LABEL: str           display name
    LOYALTY: str | None  name of the chain's app/card price program, if any
    DOMAINS: tuple[str, ...]  hosts the chain may touch (browser and navigator allowlist)
    async def stores(ctx) -> list[Store]               candidate stores near ctx.lat/ctx.lon
    async def leaflets(ctx, store) -> list[Leaflet]    offer windows valid today or next week
    async def acquire(ctx, store, leaflet) -> Acquired

It raises NotCovered (location outside the chain's area), Blocked (the site refuses honest
automation) or StepFailed (a scripted step's post-condition failed); the message is shown to
users. No other module branches on a chain's name.
"""
from __future__ import annotations

import asyncio
import io
import re
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, timedelta

import httpx
from PIL import Image
from playwright.async_api import Page as BrowserPage
from playwright.async_api import async_playwright

from app.core.logging import get_logger
from app.schemas.offer import Offer

log = get_logger(__name__)


class NotCovered(Exception):
    """The location is outside the area this chain (edition) serves."""


class Blocked(Exception):
    """The site refuses honest automation (bot challenge, 403); never worked around."""


class StepFailed(Exception):
    """A scripted step's post-condition failed (and healing, if any, did not recover it)."""


@dataclass(frozen=True)
class Store:
    id: str | None
    name: str
    address: str
    lat: float | None
    lon: float | None
    edition: str  # what the offers vary by: store id, selling region, or a national edition
    located: bool = True  # False: edition known but no store position (offer wall only)


@dataclass(frozen=True)
class Leaflet:
    key: str  # unique per (chain, edition); always contains the ISO year, e.g. "2026-W41"
    title: str
    kind: str  # main | extra | offers | preview (next week's not yet complete set)
    valid_from: date
    valid_to: date
    dates_source: str  # feed | text | derived (derived = calendar estimate, shown as such)
    source_url: str | None = None
    pdf_url: str | None = None


@dataclass(frozen=True)
class Page:
    """One leaflet page for the VLM tier: image plus its text layer, if any."""

    image_url: str
    text: str | None = None


@dataclass
class Acquired:
    tier: int  # 1 structured feed, 2 leaflet pages via VLM, 3 browser
    offers: list[Offer] = field(default_factory=list)
    pages: list[Page] = field(default_factory=list)
    complete: bool = True  # every request of the tier succeeded / every page accounted for


@dataclass
class ScanCtx:
    """Everything a chain module may use for one scan of one location."""

    http: httpx.AsyncClient
    plz: str
    lat: float
    lon: float
    today: date
    emit: Callable[[dict], None] = lambda event: None


def http_client(contact: str) -> httpx.AsyncClient:
    """Honest HTTP client: identifies the project and a contact; small connection pool."""
    return httpx.AsyncClient(
        timeout=httpx.Timeout(20.0),
        follow_redirects=True,
        headers={
            "User-Agent": f"grocery-deals-agent/0.1 (+{contact}; personal, non-commercial)",
            "Accept-Language": "de-DE,de;q=0.9",
        },
        limits=httpx.Limits(max_connections=4),
    )


def iso_key(d: date) -> str:
    """ISO week key with the ISO year, e.g. 2026-12-28 -> "2026-W53"."""
    year, week, _ = d.isocalendar()
    return f"{year}-W{week:02d}"


def iso_week(d: date) -> tuple[date, date]:
    """Monday and Sunday of the ISO week containing d."""
    monday = d - timedelta(days=d.weekday())
    return monday, monday + timedelta(days=6)


_DATE = r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})?"
_RANGE = re.compile(_DATE + r"\s*(?:-|\u2013|bis(?:\s+(?:zum|einschl\.?))?)\s*" + _DATE)


def _date(day: str, month: str, year: str | None, today: date) -> date | None:
    try:
        if year:
            return date(int(year) + (2000 if len(year) == 2 else 0), int(month), int(day))
        # No year printed: take the year that puts the date closest to today.
        candidates = [date(today.year + dy, int(month), int(day)) for dy in (-1, 0, 1)]
    except ValueError:
        return None
    return min(candidates, key=lambda c: abs((c - today).days))


def parse_validity(text: str, today: date) -> tuple[date, date] | None:
    """First printed date range in text, e.g. "vom 02.07. bis 08.07.2026" or "13.10.-18.10."."""
    m = _RANGE.search(text)
    if not m:
        return None
    end = _date(m.group(4), m.group(5), m.group(6), today)
    if end is None:
        return None
    # A start without a year belongs to the end's year (or the year before, across New Year).
    start = _date(m.group(1), m.group(2), m.group(3) or str(end.year), today)
    if start is not None and start > end and not m.group(3):
        start = _date(m.group(1), m.group(2), str(end.year - 1), today)
    return (start, end) if start is not None and start <= end else None

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


def stream_frames(page: BrowserPage, on_frame: OnFrame | None, interval_ms: int = 700) -> None:
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


def dismiss_popups(page: BrowserPage, interval_ms: int = 1500) -> None:
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


async def accept_cookies(page: BrowserPage) -> None:
    for sel in _COOKIE_SELECTORS:
        try:
            await page.click(sel, timeout=3000)
            return
        except Exception:  # noqa: BLE001 - banner may be absent or already dismissed
            continue
