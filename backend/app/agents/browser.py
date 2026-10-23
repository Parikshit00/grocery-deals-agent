"""Tier 3: honest Chromium pages, a navigation domain lock, response capture and healable steps.

A chain module drives its site with scripted `step()`s. Each step has a post-condition; only when
it fails does `navigator.heal` take over that one step with the local vision model.

robots.txt: inside a scan (`robots.enforced`), every top-level navigation is checked against the
site's robots.txt before Chromium sends it (service workers are blocked, so none can answer a
navigation past the route guard). Redirect hops never reach the route guard: every hop of a
main-frame navigation is recorded as it is requested and checked afterwards, by
`robots_checkpoint` (each step, each heal turn and the end of a heal) and when the page closes,
so a disallowed hop has been fetched but the run then ends as RobotsBlocked. URL changes without
a request (history.pushState, fragment changes) fetch nothing and are not checked. Subresources
and XHR the retailer's own page loads are not checked; see `agents/robots.py` for the scope.
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
from playwright.async_api import Frame, Page, Request, Route, async_playwright

from app.agents import base, robots
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


async def _robots_deny(guard: robots.Guard, url: str) -> str | None:
    """Why robots.txt forbids `url` (the refusal is recorded), or None. A check that fails
    counts as a refusal: the route guard must answer every route, or the page hangs."""
    try:
        reason = await guard.denial(url)
    except Exception as exc:  # noqa: BLE001 - never leave a route unanswered
        reason = f"robots.txt check failed for {url} ({type(exc).__name__})"
    if reason is not None:
        log.warning("browser.robots_refused", url=url, reason=reason)
        guard.refuse(url, reason)
    return reason


async def _refusal(guard: robots.Guard, since: int = 0) -> robots.RobotsBlocked | None:
    """RobotsBlocked for the first URL the run refused at index `since` or later, or None."""
    if len(guard.refused) <= since:
        return None
    url = guard.refused[since]
    reason = guard.reasons.get(url)
    if reason is None:  # recorded without a reason: ask again (served from the cache)
        try:
            reason = await guard.denial(url)
        except Exception:  # noqa: BLE001 - only the wording of the reason depends on it
            reason = None
    return robots.RobotsBlocked(reason or f"robots.txt refused {url}")


# Redirect hops of main-frame navigations on the allowlist that each open page requested and
# robots.txt has not been asked about yet, keyed by id(page). Hops never reach the route guard
# (the first request of a navigation does); they are checked here, at the next checkpoint or
# when the page closes. Only requested URLs are recorded: a same-document URL change
# (history.pushState, a fragment) fetches nothing, so robots.txt has nothing to say about it.
_hops: dict[int, list[str]] = {}


async def _check_hops(guard: robots.Guard, page: Page) -> None:
    """Ask robots.txt about the page's unchecked redirect hops; a disallowed one is recorded as a
    refusal (it was fetched already, so the run must end as blocked)."""
    pending = _hops.get(id(page))
    while pending:
        await _robots_deny(guard, pending.pop(0))


async def robots_checkpoint(page: Page, allow: tuple[str, ...]) -> None:
    """Raise RobotsBlocked when this run has refused a URL (the browser's route guard or an httpx
    request), or when a main-frame redirect hop since the last checkpoint went to a URL
    robots.txt disallows: Playwright does not route redirect hops, so a redirect is only seen
    after the fact. Off-domain landings are `step`'s and the navigator's to undo (only hops on
    `allow` are recorded). Does nothing outside `robots.enforced()`."""
    guard = robots.current()
    if guard is None:
        return
    await _check_hops(guard, page)
    if (blocked := await _refusal(guard)) is not None:
        raise blocked


@asynccontextmanager
async def browser_page(
    allow: tuple[str, ...], *, on_frame: base.OnFrame | None = None
) -> AsyncIterator[Page]:
    """One Chromium page under the browser cap: real UA plus our contact, direct top-level
    navigations locked to `allow` (subresources and iframes pass), no downloads, no service
    workers (Playwright's routing does not see requests a service worker answers, so one could
    carry navigations past the route guard), popups and new tabs closed. HTTP redirects bypass
    the route guard: they are logged here and undone by `step` and the navigator's after-action
    check.

    Inside `robots.enforced()` the route guard also asks robots.txt about every top-level
    navigation on the allowlist and aborts a disallowed one (ERR_BLOCKED_BY_CLIENT), so the URL
    is never requested. Every redirect hop of a main-frame navigation is recorded as it is
    requested and checked too (at the next `robots_checkpoint` and when the block ends): a hop is
    fetched before it can be checked, then the run ends as blocked. If a navigation was refused
    or a hop was disallowed while the page was open, leaving the block raises RobotsBlocked, even
    when the chain swallowed the failed goto or read the page without a step; any other
    exception is then chained to it."""
    settings = get_settings()
    # Read here: Playwright runs route handlers in its own tasks, outside this context.
    robots_guard = robots.current()
    refused_before = len(robots_guard.refused) if robots_guard is not None else 0
    async with _slot(), async_playwright() as p:
        browser = await p.chromium.launch(chromium_sandbox=settings.chromium_sandbox)
        try:
            probe = await browser.new_page()
            ua = await probe.evaluate("navigator.userAgent")
            await probe.close()
            context = await browser.new_context(
                user_agent=f"{ua} {base.PRODUCT}/0.1 (+{settings.contact})",
                locale="de-DE",
                timezone_id="Europe/Berlin",
                viewport={"width": 1440, "height": 1100},
                accept_downloads=False,
                service_workers="block",  # the route guard must see every navigation
            )

            async def guard(route: Route) -> None:
                """Top-level navigations: the domain lock, then robots.txt (in a scan).
                Subresources, XHR and iframes are the retailer's page's own and pass."""
                req = route.request
                top = req.is_navigation_request()
                with contextlib.suppress(PlaywrightError):  # a new page has no frame yet: top
                    top = top and req.frame.parent_frame is None
                if top and not allowed(req.url, allow):
                    log.warning("browser.navigation_blocked", url=req.url)
                    await route.abort("blockedbyclient")
                elif top and robots_guard is not None and await _robots_deny(robots_guard,
                                                                             req.url):
                    await route.abort("blockedbyclient")
                else:
                    await route.continue_()

            await context.route("**/*", guard)
            page = await context.new_page()

            async def close_extra(extra: Page) -> None:
                log.info("browser.popup_closed", url=extra.url)
                await extra.close()

            context.on("page", close_extra)  # registered after our page: only extras close

            hops: list[str] = []

            def landed(frame: Frame) -> None:
                if frame is page.main_frame and _off_domain(frame.url, allow):
                    log.warning("browser.off_domain_landing", url=frame.url)

            def requested(req: Request) -> None:
                """Record a redirect hop of a main-frame navigation (the route guard never sees
                it); checked by robots_checkpoint or on exit."""
                if req.redirected_from is None or not req.is_navigation_request():
                    return
                try:
                    main = req.frame is page.main_frame
                except PlaywrightError:  # no frame (a worker's request): not a navigation of ours
                    return
                if main and allowed(req.url, allow):
                    hops.append(req.url)

            async def robots_exit() -> robots.RobotsBlocked | None:
                assert robots_guard is not None
                await _check_hops(robots_guard, page)
                return await _refusal(robots_guard, refused_before)

            page.on("framenavigated", landed)
            base.stream_frames(page, on_frame)
            if robots_guard is not None:
                _hops[id(page)] = hops
                page.on("request", requested)
            try:
                yield page
            except robots.RobotsBlocked:
                raise
            except Exception as exc:
                if robots_guard is not None and (blocked := await robots_exit()) is not None:
                    raise blocked from exc
                raise
            else:
                if robots_guard is not None and (blocked := await robots_exit()) is not None:
                    raise blocked
            finally:
                _hops.pop(id(page), None)
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
    wall, RobotsBlocked after a robots.txt refusal (never healed around) and StepFailed when
    healing does not reach the goal either.
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
    await robots_checkpoint(page, allow)
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
