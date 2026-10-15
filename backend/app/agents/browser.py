"""Tier 3: honest Chromium pages, a navigation domain lock, response capture and healable steps.

A chain module drives its site with scripted `step()`s. Each step has a post-condition; only when
it fails does `navigator.heal` take over that one step with the local vision model.
"""
from __future__ import annotations

import asyncio
import contextlib
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Frame, Page, Route, async_playwright

from app.agents import base
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)

Check = Callable[[], Awaitable[bool]]
Emit = Callable[[dict], None]

_slots: tuple[asyncio.AbstractEventLoop, asyncio.Semaphore] | None = None


def _slot() -> asyncio.Semaphore:
    """Process-wide cap of settings.max_browsers Chromiums, bound to the running loop."""
    global _slots
    loop = asyncio.get_running_loop()
    if _slots is None or _slots[0] is not loop:
        _slots = (loop, asyncio.Semaphore(get_settings().max_browsers))
    return _slots[1]


def allowed(url: str, allow: tuple[str, ...]) -> bool:
    """The URL's host is one of `allow` or a subdomain of one."""
    host = (urlsplit(url).hostname or "").lower()
    return any(host == h or host.endswith("." + h) for h in allow)


def _off_domain(url: str, allow: tuple[str, ...]) -> bool:
    """A loaded web page outside `allow` (not about:blank or Chromium's error page)."""
    return url.startswith("http") and not allowed(url, allow)


@asynccontextmanager
async def browser_page(
    allow: tuple[str, ...], *, on_frame: base.OnFrame | None = None
) -> AsyncIterator[Page]:
    """One Chromium page under the browser cap: real UA plus our contact, direct top-level
    navigations locked to `allow` (subresources and iframes pass), no downloads, popups and new
    tabs closed. HTTP redirects bypass the route guard: they are logged here and undone by
    `step` and the navigator's after-action check."""
    settings = get_settings()
    async with _slot(), async_playwright() as p:
        browser = await p.chromium.launch(chromium_sandbox=settings.chromium_sandbox)
        try:
            probe = await browser.new_page()
            ua = await probe.evaluate("navigator.userAgent")
            await probe.close()
            context = await browser.new_context(
                user_agent=f"{ua} grocery-deals-agent/0.1 (+{settings.contact})",
                locale="de-DE",
                timezone_id="Europe/Berlin",
                viewport={"width": 1440, "height": 1100},
                accept_downloads=False,
            )

            async def guard(route: Route) -> None:
                req = route.request
                top = req.is_navigation_request()
                with contextlib.suppress(PlaywrightError):  # a new page has no frame yet: top
                    top = top and req.frame.parent_frame is None
                if top and not allowed(req.url, allow):
                    log.warning("browser.navigation_blocked", url=req.url)
                    await route.abort("blockedbyclient")
                else:
                    await route.continue_()

            await context.route("**/*", guard)
            page = await context.new_page()

            async def close_extra(extra: Page) -> None:
                log.info("browser.popup_closed", url=extra.url)
                await extra.close()

            context.on("page", close_extra)  # registered after our page: only extras close

            def landed(frame: Frame) -> None:
                if frame is page.main_frame and _off_domain(frame.url, allow):
                    log.warning("browser.off_domain_landing", url=frame.url)

            page.on("framenavigated", landed)
            base.stream_frames(page, on_frame)
            yield page
        finally:
            await browser.close()


async def capture_json(
    page: Page, url_contains: str, action: Callable[[], Awaitable[Any]], timeout_ms: int = 15000
) -> Any:
    """Run `action` and return the JSON body of the first response the page itself receives whose
    URL contains `url_contains` (how key-protected finder APIs are read without the key)."""
    async with page.expect_response(lambda r: url_contains in r.url, timeout=timeout_ms) as info:
        await action()
    return await (await info.value).json()


# Challenge-only markers: Cloudflare's JS detections and Turnstile widgets also put
# /cdn-cgi/challenge-platform/ and cf-chl-widget ids into ordinary pages.
_CHALLENGE_TITLES = ("just a moment", "einen moment bitte", "access denied")
_CHALLENGE_HTML = re.compile(r"_cf_chl_opt|Zeig uns, dass du ein Mensch bist|Reference #\d+\.")


async def is_challenge(page: Page) -> bool:
    """A Cloudflare or Akamai bot wall instead of the site."""
    try:
        title = (await page.title()).strip().lower()
        html = await page.content()
    except PlaywrightError:  # navigating right now; the next check sees the settled page
        return False
    return title.startswith(_CHALLENGE_TITLES) or bool(_CHALLENGE_HTML.search(html))


async def passes(check: Check) -> bool:
    """A post-condition that raises has not passed."""
    try:
        return await check()
    except Exception as exc:  # noqa: BLE001 - any failure of the probe means "not there yet"
        log.info("browser.check_error", error=str(exc))
        return False


@dataclass
class HealResult:
    ok: bool
    actions: list[dict]
    ms: int
    forced: bool = False


async def step(
    page: Page,
    name: str,
    goal: str,
    do: Callable[[], Awaitable[Any]],
    check: Check,
    *,
    allow: tuple[str, ...],
    inputs: dict[str, str],
    emit: Emit,
    heal_log: list,
    force_heal: bool = False,
) -> HealResult | None:
    """Run the scripted action, then its post-condition; heal only this step when it fails.

    Returns None when the script worked, else the successful HealResult. Raises Blocked on a bot
    wall and StepFailed when healing does not reach the goal either.
    """
    from app.agents import navigator  # navigator builds on this module

    if not force_heal:
        t0 = time.monotonic()
        try:
            await do()
        except (base.Blocked, base.NotCovered, base.StepFailed):
            raise
        except Exception as exc:  # noqa: BLE001 - a broken selector is what healing is for
            log.info("browser.step_action_failed", step=name, error=str(exc))
        log.info("browser.step", step=name, ms=int((time.monotonic() - t0) * 1000))
        if _off_domain(page.url, allow):  # a redirect the route guard cannot see
            log.warning("browser.off_domain", step=name, url=page.url)
            heal_log.append(
                {"step": name, "action": "script", "ok": False, "violation": page.url,
                 "forced": False}
            )
            with contextlib.suppress(PlaywrightError):
                await page.go_back(timeout=10000)
    if await is_challenge(page):
        raise base.Blocked("bot protection")
    if await passes(check):
        return None

    def emit_action(action: dict) -> None:  # logged as it happens, so a raising heal keeps it
        heal_log.append({"step": name, **action, "forced": force_heal})
        emit({"event": "heal", "step": name, **action, "forced": force_heal})

    result = await navigator.heal(
        page, goal, check, allow=allow, inputs=inputs, emit=emit_action
    )
    result.forced = force_heal
    if not result.ok:
        raise base.StepFailed(f"{name}: {goal} (not reached, self-heal failed)")
    return result
