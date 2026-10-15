"""Browser tier with real Chromium against synthetic fixture pages served on 127.0.0.1.

The same server is also reachable as "localhost", which is outside the allowlist: that is how
the context-level navigation guard is exercised without any network access.
"""
from __future__ import annotations

import asyncio
import threading
from collections.abc import Iterator
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from playwright.async_api import Error as PlaywrightError

from app.agents import browser
from app.agents.base import Blocked, NotCovered
from app.core.config import get_settings

FIX = Path(__file__).parents[1] / "fixtures" / "navigator"
ALLOW = ("127.0.0.1",)


class _Handler(SimpleHTTPRequestHandler):
    agents: list[str] = []
    port = 0

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(FIX), **kwargs)

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        _Handler.agents.append(self.headers.get("User-Agent", ""))
        if self.path == "/away":  # a redirect off the allowlist
            self.send_response(302)
            self.send_header("Location", f"http://localhost:{self.port}/shop.html")
            self.end_headers()
            return
        super().do_GET()


@pytest.fixture(scope="session")
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    _Handler.port = server.server_port
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def off_domain(site: str) -> str:
    return site.replace("127.0.0.1", "localhost")


def test_allowed_is_exact_host_or_subdomain():
    allow = ("lidl.de",)
    assert browser.allowed("https://www.lidl.de/q/x", allow)
    assert browser.allowed("https://LIDL.de", allow)
    assert not browser.allowed("https://notlidl.de", allow)
    assert not browser.allowed("https://lidl.de.evil.test/", allow)
    assert not browser.allowed("about:blank", allow)


def test_page_is_honest_and_locked_to_the_allowlist(site):
    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/shop.html")
            ua = await page.evaluate("navigator.userAgent")
            lang = await page.evaluate("navigator.language")
            tz = await page.evaluate("Intl.DateTimeFormat().resolvedOptions().timeZone")
            with pytest.raises(PlaywrightError, match="ERR_BLOCKED_BY_CLIENT"):
                await page.goto(f"{off_domain(site)}/shop.html")
            await page.wait_for_timeout(300)
            blocked_at = page.url  # Chromium shows its error page; the navigator goes back
            await page.goto(f"{site}/shop.html")
            # A subresource from another host passes; only navigations are locked.
            fetched = await page.evaluate(
                "u => fetch(u, {mode: 'no-cors'}).then(() => true, () => false)",
                f"{off_domain(site)}/stores.json",
            )
            # So does an iframe from another host (consent managers, embedded viewers).
            await page.evaluate(
                "u => { const f = document.createElement('iframe'); f.src = u;"
                " document.body.append(f); }",
                f"{off_domain(site)}/akamai.html",
            )
            await page.wait_for_timeout(500)
            return ua, lang, tz, fetched, blocked_at, page.frames[-1].url

    ua, lang, tz, fetched, blocked_at, iframe = asyncio.run(go())
    assert ua.endswith(f" grocery-deals-agent/0.1 (+{get_settings().contact})")
    assert "Chrome/" in ua and "grocery-deals-agent" in _Handler.agents[0]
    assert (lang, tz) == ("de-DE", "Europe/Berlin")
    assert fetched is True
    assert blocked_at.startswith("chrome-error://")
    assert iframe == f"{off_domain(site)}/akamai.html"


def test_new_tabs_and_popups_are_closed(site):
    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/shop.html")
            await page.click("#tab")
            await page.click("#popup")
            await page.wait_for_timeout(1000)
            return len(page.context.pages), page.url

    assert asyncio.run(go()) == (1, f"{site}/shop.html")


def test_browser_cap_holds_concurrent_pages(site, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_browsers", 1)
    monkeypatch.setattr(browser, "_slots", None)
    active = peak = 0

    async def one():
        nonlocal active, peak
        async with browser.browser_page(ALLOW) as page:
            active += 1
            peak = max(peak, active)
            await page.goto(f"{site}/shop.html")
            active -= 1

    async def go():
        await asyncio.gather(one(), one())

    asyncio.run(go())
    assert peak == 1


def test_capture_json_returns_the_response_the_page_receives(site):
    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/shop.html")
            await page.fill("#plz", "80331")
            return await browser.capture_json(
                page, "stores.json", lambda: page.click("#search"), timeout_ms=5000
            )

    data = asyncio.run(go())
    assert data["stores"][0]["id"] == "4711"


@pytest.mark.parametrize(
    ("name", "expected"),
    [("cloudflare.html", True), ("akamai.html", True), ("shop.html", False),
     ("cloudflare_jsd.html", False)],
)
def test_is_challenge(site, name, expected):
    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/{name}")
            return await browser.is_challenge(page)

    assert asyncio.run(go()) is expected


def _selected(page):
    return lambda: page.locator("#selected").is_visible()


def test_step_that_works_never_heals(site, monkeypatch):
    async def no_heal(*args, **kwargs):
        raise AssertionError("heal must not run")

    monkeypatch.setattr("app.agents.navigator.heal", no_heal)

    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/shop.html")
            await page.fill("#plz", "80331")
            await page.click("#search")

            async def do():
                await page.click("#results button", timeout=3000)

            events: list[dict] = []
            heal_log: list = []
            result = await browser.step(
                page, "select_store", "Select the store", do, _selected(page),
                allow=ALLOW, inputs={}, emit=events.append, heal_log=heal_log,
            )
            return result, events, heal_log

    assert asyncio.run(go()) == (None, [], [])


def test_step_on_a_bot_wall_raises_blocked(site):
    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/shop.html")

            async def do():
                await page.goto(f"{site}/cloudflare.html")

            await browser.step(
                page, "open", "Open the offers", do, _selected(page),
                allow=ALLOW, inputs={}, emit=lambda e: None, heal_log=[],
            )

    with pytest.raises(Blocked):
        asyncio.run(go())


def test_scripted_redirect_off_domain_is_undone_and_logged(site):
    async def go():
        async with browser.browser_page(ALLOW) as page:
            await page.goto(f"{site}/shop.html")

            async def do():  # the route guard never sees the 302 hop to localhost
                await page.goto(f"{site}/away")

            heal_log: list = []
            result = await browser.step(
                page, "open", "Open the finder", do, lambda: page.locator("#plz").is_visible(),
                allow=ALLOW, inputs={}, emit=lambda e: None, heal_log=heal_log,
            )
            return result, heal_log, page.url

    result, heal_log, url = asyncio.run(go())
    assert result is None and url == f"{site}/shop.html"
    assert heal_log == [{"step": "open", "action": "script", "ok": False,
                         "violation": f"{off_domain(site)}/shop.html", "forced": False}]


def test_contract_exceptions_from_the_script_are_not_healed(site):
    async def go():
        async with browser.browser_page(ALLOW) as page:
            async def do():
                raise NotCovered("outside the area")

            await browser.step(
                page, "open", "Open the finder", do, lambda: page.locator("#plz").is_visible(),
                allow=ALLOW, inputs={}, emit=lambda e: None, heal_log=[],
            )

    with pytest.raises(NotCovered):
        asyncio.run(go())
