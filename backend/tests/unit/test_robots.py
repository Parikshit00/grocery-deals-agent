"""robots.txt enforcement (RFC 9309): the matcher, the request hook and the per-origin cache,
and its wiring into the browser tier, the navigator, the scan runtime and the basket agent.

Every robots.txt here is served by an httpx.MockTransport, so nothing leaves the process; time is
a fake clock and every test has its own RobotsCache (wiring tests reset the module's CACHE). The
browser tests drive real Chromium against fixture pages on 127.0.0.1, like test_browser.py.
"""
from __future__ import annotations

import asyncio
import threading
import time
import uuid
from collections import deque
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from playwright.async_api import Error as PlaywrightError

from app import canary
from app.agents import browser, navigator, robots
from app.agents.base import Acquired, Blocked, Leaflet, Page, Store
from app.api import scan as scan_api
from app.core.config import get_settings
from app.schemas.basket import PlanStore
from app.schemas.offer import Offer
from app.services import basket, scan
from app.services.geo import Location

SITE = "https://shop.test"
ORIGIN = ("https", "shop.test", 443)
UA = "grocery-deals-agent/0.1 (+test)"


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class Site:
    """A retailer: serves `answer` at /robots.txt (and at /assets/robots), 302s from
    `redirects`, "ok" elsewhere, and records every path it is asked for."""

    def __init__(self, answer: tuple[int, str] | Exception = (200, ""),
                 redirects: dict[str, str] | None = None, delay: float = 0.0) -> None:
        self.answer = answer
        self.redirects = redirects or {}
        self.delay = delay
        self.seen: list[str] = []
        self.agents: list[str] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.raw_path.decode()
        self.seen.append(path)
        self.agents.append(request.headers.get("User-Agent", ""))
        if path in self.redirects:
            return httpx.Response(302, headers={"Location": self.redirects[path]})
        if path in ("/robots.txt", "/assets/robots"):
            if self.delay:
                await asyncio.sleep(self.delay)
            if isinstance(self.answer, Exception):
                raise self.answer
            status, body = self.answer
            return httpx.Response(status, text=body)
        return httpx.Response(200, text="ok")

    @property
    def robots_fetches(self) -> int:
        return self.seen.count("/robots.txt")


def _client(site: Site) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(site), follow_redirects=True,
                             headers={"User-Agent": UA})


async def _get(site: Site, cache: robots.RobotsCache, *paths: str) -> list[int | str]:
    """Status code, or the RobotsBlocked message, for each path in turn."""
    out: list[int | str] = []
    async with _client(site) as http, robots.enforced(http, cache):
        for path in paths:
            try:
                out.append((await http.get(SITE + path)).status_code)
            except robots.RobotsBlocked as exc:
                out.append(str(exc))
    return out


# --- matcher -------------------------------------------------------------------------------

PARSE = [
    # Our own group beats "*", whatever its case or version suffix.
    ("User-agent: *\nDisallow: /\n\nUser-agent: grocery-deals-agent\nDisallow: /private",
     "/shop", True),
    ("User-agent: *\nDisallow: /\n\nUser-agent: grocery-deals-agent\nDisallow: /private",
     "/private/x", False),
    ("User-agent: Grocery-Deals-Agent/2.0\nDisallow: /p\n\nUser-agent: *\nDisallow:",
     "/p", False),
    ("User-agent: other\nUser-agent: grocery-deals-agent\nDisallow: /p", "/p", False),
    # A different token does not apply, even when it is a prefix of ours.
    ("User-agent: grocery\nDisallow: /", "/x", True),
    ("User-agent: grocery\nDisallow: /\n\nUser-agent: *\nDisallow: /b", "/x", True),
    ("User-agent: grocery\nDisallow: /\n\nUser-agent: *\nDisallow: /b", "/b", False),
    # Two "*" groups are merged (REWE has two).
    ("User-agent: *\nDisallow: /a\n\nUser-agent: bot\nDisallow: /\n\nUser-agent: *\n"
     "Disallow: /b", "/a/1", False),
    ("User-agent: *\nDisallow: /a\n\nUser-agent: bot\nDisallow: /\n\nUser-agent: *\n"
     "Disallow: /b", "/b/1", False),
    ("User-agent: *\nDisallow: /a\n\nUser-agent: bot\nDisallow: /\n\nUser-agent: *\n"
     "Disallow: /b", "/c", True),
    # The longest match wins, in either order.
    ("User-agent: *\nDisallow: /shop\nAllow: /shop/offers", "/shop/offers/1", True),
    ("User-agent: *\nDisallow: /shop\nAllow: /shop/offers", "/shop/cart", False),
    ("User-agent: *\nAllow: /shop\nDisallow: /shop/cart", "/shop/cart", False),
    # Allow wins a tie.
    ("User-agent: *\nDisallow: /page\nAllow: /page", "/page", True),
    ("User-agent: *\nDisallow: /*.pdf\nAllow: /a/*.pdf", "/a/x.pdf", True),
    # "*" matches any characters; patterns see the query.
    ("User-agent: *\nDisallow: /*?search=", "/suche?search=milch", False),
    ("User-agent: *\nDisallow: /*?search=", "/suche?page=2", True),
    ("User-agent: *\nDisallow: /*?search=", "/suche", True),
    ("User-agent: *\nDisallow: /*?q=", "/p/q=1", True),
    ("User-agent: *\nDisallow: *id=*", "/p?id=3", False),
    ("User-agent: *\nDisallow: *id=*", "/p?x=3", True),
    # "$" anchors the end.
    ("User-agent: *\nDisallow: /*.pdf$", "/a/b.pdf", False),
    ("User-agent: *\nDisallow: /*.pdf$", "/a/b.pdf?x=1", True),
    ("User-agent: *\nDisallow: /exact$", "/exact/more", True),
    # An empty Disallow allows everything (and our empty group still beats "*").
    ("User-agent: *\nDisallow:", "/anything", True),
    ("User-agent: grocery-deals-agent\nDisallow:\n\nUser-agent: *\nDisallow: /", "/x", True),
    # robots.txt itself is always allowed.
    ("User-agent: *\nDisallow: /", "/robots.txt", True),
    ("User-agent: *\nDisallow: /", "/x", False),
    # Comments, a BOM, record names in any case; other records do not end a group.
    ("﻿USER-AGENT: * # everyone\nSitemap: https://shop.test/s.xml\nDISALLOW: /x # no",
     "/x/1", False),
    # Rules before any user-agent line belong to no group; no group at all allows.
    ("Disallow: /\nUser-agent: *\nAllow: /", "/x", True),
    ("# nothing here", "/x", True),
    # Non-ASCII patterns compare percent-encoded, hex digits in any case.
    ("User-agent: *\nDisallow: /käse", "/k%C3%A4se", False),
    ("User-agent: *\nDisallow: /k%c3%a4se", "/k%C3%A4se/1", False),
    # A percent-encoded unreserved character is the character itself, both ways; a reserved
    # one ("%2F") stays distinct from "/".
    ("User-agent: *\nDisallow: /~a", "/%7Ea", False),
    ("User-agent: *\nDisallow: /~a", "/%7ea/b", False),
    ("User-agent: *\nDisallow: /%7Ea", "/~a", False),
    ("User-agent: *\nDisallow: /%41b", "/Ab", False),
    ("User-agent: *\nDisallow: /a%2Fb", "/a/b", True),
    ("User-agent: *\nDisallow: /a/b", "/a%2Fb", True),
    ("User-agent: *\nDisallow: /a%2fb", "/a%2Fb", False),
    ("User-agent: *\nDisallow: /", "/robots%2Etxt", True),
    # Many "*" match like one; "$" before the end is a literal character.
    ("User-agent: *\nDisallow: /*a*a*a*b", "/xaYaZa-b", False),
    ("User-agent: *\nDisallow: /*a*a*a*b", "/xaYa-b", True),
    ("User-agent: *\nDisallow: /**x", "/abx", False),
    ("User-agent: *\nDisallow: /*$", "/anything", False),
    ("User-agent: *\nDisallow: /a$b", "/a$b/c", False),
    ("User-agent: *\nDisallow: /a$b", "/ab", True),
]


@pytest.mark.parametrize(("text", "target", "allowed"), PARSE)
def test_parse(text, target, allowed):
    assert robots.parse(text).allows(target) is allowed


def test_many_star_patterns_match_in_linear_time():
    """A backtracking regex needs minutes for this; the matcher is O(pattern * target)."""
    rules = robots.parse("User-agent: *\nDisallow: /" + "*a" * 10 + "*b")
    t0 = time.perf_counter()
    assert rules.allows("/" + "a" * 60)
    assert rules.allows("/" + "a" * 2000)
    assert not rules.allows("/" + "a" * 59 + "b")
    assert time.perf_counter() - t0 < 1.0


def test_unreachable_rules_allow_only_robots_txt():
    rules = robots.Rules(source="503", reachable=False)
    assert not rules.allows("/")
    assert rules.allows("/robots.txt")


# --- request hook ----------------------------------------------------------------------------

def test_disallowed_path_is_refused_before_it_is_sent():
    site = Site((200, "User-agent: *\nDisallow: /private"))
    cache = robots.RobotsCache(clock=Clock())

    async def go():
        async with _client(site) as http, robots.enforced(http, cache) as guard:
            ok = (await http.get(f"{SITE}/ok")).status_code
            with pytest.raises(robots.RobotsBlocked, match=r"robots.txt disallows /private\?a=1"):
                await http.get(f"{SITE}/private?a=1")
            return ok, guard.refused

    ok, refused = asyncio.run(go())
    assert ok == 200
    assert site.seen == ["/robots.txt", "/ok"]
    assert site.agents[0] == UA  # robots.txt is fetched through the same client
    assert refused == list(cache.refused) == [f"{SITE}/private?a=1"]
    assert issubclass(robots.RobotsBlocked, Blocked)


def test_disallowed_redirect_target_is_never_requested():
    site = Site((200, "User-agent: *\nDisallow: /private"), redirects={"/go": "/private"})
    cache = robots.RobotsCache(clock=Clock())
    assert asyncio.run(_get(site, cache, "/go")) == ["robots.txt disallows /private"]
    assert site.seen == ["/robots.txt", "/go"]


@pytest.mark.parametrize(("answer", "source", "allowed"), [
    ((200, "User-agent: *\nDisallow:"), "200", True),
    ((404, "not found"), "404", True),
    ((403, "forbidden"), "403", True),  # RFC 9309: any 4xx is "unavailable", allow all
    ((401, "login"), "401", True),
    ((500, "error"), "500", False),
    ((503, "later"), "503", False),
    (httpx.ConnectError("refused"), "ConnectError", False),
    (httpx.ReadTimeout("slow"), "ReadTimeout", False),
])
def test_robots_status_decides(answer, source, allowed):
    site = Site(answer)
    cache = robots.RobotsCache(clock=Clock())
    [result] = asyncio.run(_get(site, cache, "/ok"))
    assert result == (200 if allowed else "robots.txt unreachable for shop.test")
    assert site.seen == (["/robots.txt", "/ok"] if allowed else ["/robots.txt"])
    expires, rules = cache.entries[ORIGIN]
    assert rules.source == source
    assert expires == 1000.0 + (robots.TTL_S if allowed else robots.UNREACHABLE_TTL_S)


def test_answer_is_cached_per_origin():
    site = Site((200, "User-agent: *\nDisallow: /private"))
    cache = robots.RobotsCache(clock=Clock())

    async def go():
        first = await _get(site, cache, "/a", "/b")
        second = await _get(site, cache, "/private")  # another client, same cache
        async with _client(site) as http, robots.enforced(http, cache):
            await http.get("https://shop.test:8443/c")  # another origin
        return first + second

    assert asyncio.run(go()) == [200, 200, "robots.txt disallows /private"]
    assert site.robots_fetches == 2
    assert set(cache.entries) == {ORIGIN, ("https", "shop.test", 8443)}


def test_answer_is_refetched_after_24_hours():
    site = Site((200, ""))
    clock = Clock()
    cache = robots.RobotsCache(clock=clock)
    assert asyncio.run(_get(site, cache, "/private")) == [200]
    site.answer = (200, "User-agent: *\nDisallow: /private")
    clock.t += robots.TTL_S - 1
    assert asyncio.run(_get(site, cache, "/private")) == [200]
    assert site.robots_fetches == 1
    clock.t += 2
    assert asyncio.run(_get(site, cache, "/private")) == ["robots.txt disallows /private"]
    assert site.robots_fetches == 2


def test_unreachable_robots_txt_is_retried_after_5_minutes():
    site = Site((500, "error"))
    clock = Clock()
    cache = robots.RobotsCache(clock=clock)
    blocked = "robots.txt unreachable for shop.test"
    assert asyncio.run(_get(site, cache, "/ok")) == [blocked]
    site.answer = (200, "")
    clock.t += robots.UNREACHABLE_TTL_S - 1
    assert asyncio.run(_get(site, cache, "/ok")) == [blocked]
    assert site.robots_fetches == 1
    clock.t += 2
    assert asyncio.run(_get(site, cache, "/ok")) == [200]
    assert site.robots_fetches == 2


def test_failed_refresh_keeps_the_earlier_answer():
    site = Site((200, "User-agent: *\nDisallow: /private"))
    clock = Clock()
    cache = robots.RobotsCache(clock=clock)
    asyncio.run(_get(site, cache, "/ok"))
    clock.t += robots.TTL_S + 1
    site.answer = httpx.ConnectError("down")
    assert asyncio.run(_get(site, cache, "/ok", "/private")) == [
        200, "robots.txt disallows /private"]
    assert site.robots_fetches == 2
    expires, rules = cache.entries[ORIGIN]
    assert (expires, rules.source) == (clock.t + robots.UNREACHABLE_TTL_S, "200")
    clock.t += robots.UNREACHABLE_TTL_S + 1
    site.answer = (503, "later")
    assert asyncio.run(_get(site, cache, "/ok")) == [200]
    assert site.robots_fetches == 3


def test_concurrent_requests_share_one_robots_fetch():
    site = Site((200, "User-agent: *\nDisallow: /private"), delay=0.05)
    cache = robots.RobotsCache(clock=Clock())

    async def go():
        async with _client(site) as http, robots.enforced(http, cache):
            async def one(path: str) -> int | str:
                try:
                    return (await http.get(SITE + path)).status_code
                except robots.RobotsBlocked as exc:
                    return str(exc)
            return await asyncio.gather(*(one(p) for p in ("/a", "/b", "/c", "/d", "/private")))

    assert asyncio.run(go()) == [200, 200, 200, 200, "robots.txt disallows /private"]
    assert site.robots_fetches == 1


def test_locks_follow_the_event_loop():
    """One cache (like CACHE) serves several event loops; a lock from an earlier loop would
    fail under contention."""
    site = Site((200, ""), delay=0.02)
    clock = Clock()
    cache = robots.RobotsCache(clock=clock)

    async def go():
        async with _client(site) as http, robots.enforced(http, cache):
            await asyncio.gather(*(http.get(f"{SITE}/{i}") for i in range(3)))

    asyncio.run(go())
    clock.t += robots.TTL_S + 1
    asyncio.run(go())
    assert site.robots_fetches == 2


def test_direct_robots_txt_request_is_not_checked():
    site = Site((200, "User-agent: *\nDisallow: /"))
    cache = robots.RobotsCache(clock=Clock())
    assert asyncio.run(_get(site, cache, "/robots.txt")) == [200]
    assert site.seen == ["/robots.txt"]
    assert cache.entries == {}


def test_redirect_hops_of_the_robots_fetch_are_not_checked():
    """Checking the hop would wait on the origin lock its own fetch holds."""
    site = Site((200, "User-agent: *\nDisallow: /assets/"),
                redirects={"/robots.txt": "/assets/robots"})
    cache = robots.RobotsCache(clock=Clock())

    async def go():
        return await asyncio.wait_for(_get(site, cache, "/ok", "/assets/x"), timeout=5)

    assert asyncio.run(go()) == [200, "robots.txt disallows /assets/x"]
    assert site.seen == ["/robots.txt", "/assets/robots", "/ok"]
    assert cache.entries[ORIGIN][1].source == "200"


def test_large_robots_txt_is_read_up_to_the_limit():
    body = "User-agent: *\nDisallow: /a\n" + "#" * robots.MAX_BYTES + "\nDisallow: /b\n"
    site = Site((200, body))
    cache = robots.RobotsCache(clock=Clock())
    assert asyncio.run(_get(site, cache, "/a", "/b")) == ["robots.txt disallows /a", 200]


# --- enforced() and current() ----------------------------------------------------------------

def test_enforced_puts_the_guard_first_and_removes_it_on_exit():
    site = Site((200, ""))
    cache = robots.RobotsCache(clock=Clock())
    observed: list[str] = []

    async def observer(request: httpx.Request) -> None:
        observed.append(request.url.path)

    async def go():
        async with _client(site) as http:
            http.event_hooks = {"request": [observer], "response": []}
            assert robots.current() is None
            async with robots.enforced(http, cache) as guard:
                inside = (list(http.event_hooks["request"]), robots.current())
                await http.get(f"{SITE}/ok")
            return guard, inside, list(http.event_hooks["request"]), robots.current()

    guard, (hooks, current), after, current_after = asyncio.run(go())
    assert hooks == [guard, observer] and current is guard
    assert after == [observer] and current_after is None
    assert observed == ["/robots.txt", "/ok"]  # the observer also sees the robots fetch


def test_denial_reports_without_recording():
    site = Site((200, "User-agent: *\nDisallow: /private"))
    cache = robots.RobotsCache(clock=Clock())

    async def go():
        async with _client(site) as http, robots.enforced(http, cache) as guard:
            reasons = [await guard.denial(f"{SITE}/private/1"), await guard.denial(f"{SITE}/"),
                       await guard.denial("about:blank"), await guard.denial(f"{SITE}/robots.txt")]
            return reasons, list(guard.refused)

    reasons, refused = asyncio.run(go())
    assert reasons == ["robots.txt disallows /private/1", None, None, None]
    assert refused == [] and list(cache.refused) == []


# --- browser tier: real Chromium, robots.txt from MockTransport ----------------------------

FIX = Path(__file__).parents[1] / "fixtures" / "navigator"
ALLOW = ("127.0.0.1",)
PAGES_ROBOTS = "User-agent: *\nDisallow: /akamai.html\nDisallow: /stores.json"


# A page that installs a pass-through service worker (it answers every fetch of the pages it
# controls, navigations included) and says in its title when it is done trying. With service
# workers blocked the registration succeeds but the worker never becomes ready, hence the race.
_SW_PAGE = b"""<html><head><title>loading</title></head><body>sw<script>
navigator.serviceWorker.register('/sw.js')
  .then(() => Promise.race([navigator.serviceWorker.ready.then(() => 'sw ready'),
                            new Promise(done => setTimeout(() => done('sw not ready'), 2000))]))
  .then(title => { document.title = title; }, () => { document.title = 'sw refused'; });
</script></body></html>"""
_SW_JS = b"""self.addEventListener('install', e => self.skipWaiting());
self.addEventListener('activate', e => e.waitUntil(self.clients.claim()));
self.addEventListener('fetch', e => e.respondWith(fetch(e.request)));"""


class _Recorder(SimpleHTTPRequestHandler):
    """The fixture pages; records every path Chromium asks for. /hop redirects to a page
    robots.txt disallows; /sw.html and /sw.js install a service worker."""

    paths: list[str] = []

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, directory=str(FIX), **kwargs)

    def log_message(self, *args) -> None:
        pass

    def _send(self, body: bytes, content_type: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        _Recorder.paths.append(self.path)
        if self.path == "/hop":
            self.send_response(302)
            self.send_header("Location", "/akamai.html")
            self.end_headers()
            return
        if self.path == "/sw.html":
            return self._send(_SW_PAGE, "text/html")
        if self.path == "/sw.js":
            return self._send(_SW_JS, "text/javascript")
        super().do_GET()


@pytest.fixture(scope="module")
def _pages_server() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


@pytest.fixture
def pages(_pages_server: str) -> str:
    _Recorder.paths.clear()
    return _pages_server


def _no_heal(monkeypatch) -> None:
    async def no_heal(*args, **kwargs):
        raise AssertionError("heal must not run")

    monkeypatch.setattr("app.agents.navigator.heal", no_heal)


def test_disallowed_navigation_is_aborted_and_never_requested(pages):
    robots_site = Site((200, PAGES_ROBOTS))
    cache = robots.RobotsCache(clock=Clock())
    seen: dict = {}

    async def go():
        async with _client(robots_site) as http, robots.enforced(http, cache) as guard:
            seen["guard"] = guard
            async with browser.browser_page(ALLOW) as page:
                await page.goto(f"{pages}/shop.html")
                # A subresource the page itself loads is out of scope: it passes.
                seen["xhr"] = await page.evaluate(
                    "u => fetch(u).then(r => r.ok, () => false)", f"{pages}/stores.json")
                with pytest.raises(PlaywrightError, match="ERR_BLOCKED_BY_CLIENT"):
                    await page.goto(f"{pages}/akamai.html")
                # The chain swallows the failed goto (as REWE's does); leaving still raises.

    with pytest.raises(robots.RobotsBlocked, match="robots.txt disallows /akamai.html"):
        asyncio.run(go())
    assert "/shop.html" in _Recorder.paths and "/akamai.html" not in _Recorder.paths
    assert seen["xhr"] is True and "/stores.json" in _Recorder.paths
    assert seen["guard"].refused == [f"{pages}/akamai.html"]
    assert robots_site.seen == ["/robots.txt"]  # asked once, through the scan's client


def test_step_after_a_refused_navigation_raises_without_healing(pages, monkeypatch):
    _no_heal(monkeypatch)
    cache = robots.RobotsCache(clock=Clock())

    async def go():
        async with _client(Site((200, PAGES_ROBOTS))) as http, robots.enforced(http, cache):
            async with browser.browser_page(ALLOW) as page:
                await page.goto(f"{pages}/shop.html")

                async def do():
                    await page.goto(f"{pages}/akamai.html")

                await browser.step(
                    page, "open", "Open the offers", do,
                    lambda: page.locator("#selected").is_visible(),
                    allow=ALLOW, inputs={}, emit=lambda e: None, heal_log=[],
                )

    with pytest.raises(robots.RobotsBlocked, match="robots.txt disallows /akamai.html"):
        asyncio.run(go())
    assert "/akamai.html" not in _Recorder.paths


def test_step_on_a_redirect_to_a_disallowed_page_raises_without_healing(pages, monkeypatch):
    """Playwright does not route redirect hops: the hop is caught by the checkpoint, before
    the bot-wall check (the target here is a bot wall too) and before any healing."""
    _no_heal(monkeypatch)
    cache = robots.RobotsCache(clock=Clock())
    seen: dict = {}

    async def go():
        async with _client(Site((200, PAGES_ROBOTS))) as http, \
                robots.enforced(http, cache) as guard:
            seen["guard"] = guard
            async with browser.browser_page(ALLOW) as page:
                await page.goto(f"{pages}/shop.html")

                async def do():
                    await page.goto(f"{pages}/hop")

                await browser.step(
                    page, "open", "Open the offers", do,
                    lambda: page.locator("#selected").is_visible(),
                    allow=ALLOW, inputs={}, emit=lambda e: None, heal_log=[],
                )

    with pytest.raises(robots.RobotsBlocked, match="robots.txt disallows /akamai.html"):
        asyncio.run(go())
    assert seen["guard"].refused == [f"{pages}/akamai.html"]


def test_redirect_to_a_disallowed_page_raises_on_exit_without_a_step(pages):
    """REWE reads its page after a plain goto, with no step: a redirect landing on a disallowed
    page has been fetched (hops are not routed), and leaving the page ends the run as blocked."""
    cache = robots.RobotsCache(clock=Clock())
    seen: dict = {}

    async def go():
        async with _client(Site((200, PAGES_ROBOTS))) as http, \
                robots.enforced(http, cache) as guard:
            seen["guard"] = guard
            async with browser.browser_page(ALLOW) as page:
                await page.goto(f"{pages}/hop")
                seen["url"] = page.url
                await page.content()

    with pytest.raises(robots.RobotsBlocked, match="robots.txt disallows /akamai.html"):
        asyncio.run(go())
    assert seen["url"] == f"{pages}/akamai.html"
    assert seen["guard"].refused == [f"{pages}/akamai.html"]


def test_service_worker_cannot_carry_a_navigation_past_the_route_guard(pages):
    """Playwright's routing does not see requests a service worker answers: the browser blocks
    service workers, so a disallowed navigation is still aborted and never requested."""
    cache = robots.RobotsCache(clock=Clock())
    seen: dict = {}

    async def go():
        async with _client(Site((200, PAGES_ROBOTS))) as http, \
                robots.enforced(http, cache) as guard:
            seen["guard"] = guard
            async with browser.browser_page(ALLOW) as page:
                await page.goto(f"{pages}/sw.html")
                await page.wait_for_function("document.title !== 'loading'", timeout=10000)
                seen["title"] = await page.title()
                await page.reload()  # a registered worker would now control the page
                with pytest.raises(PlaywrightError, match="ERR_BLOCKED_BY_CLIENT"):
                    await page.goto(f"{pages}/akamai.html")

    with pytest.raises(robots.RobotsBlocked, match="robots.txt disallows /akamai.html"):
        asyncio.run(go())
    assert seen["title"] != "sw ready"
    assert "/akamai.html" not in _Recorder.paths
    assert seen["guard"].refused == [f"{pages}/akamai.html"]


def test_same_document_url_change_is_not_a_refusal(pages):
    """history.pushState and fragment changes send no request: a single-page app writing a
    disallowed URL into the address bar fetched nothing, so nothing is refused."""
    cache = robots.RobotsCache(clock=Clock())
    seen: dict = {}

    async def go():
        async with _client(Site((200, PAGES_ROBOTS))) as http, \
                robots.enforced(http, cache) as guard:
            async with browser.browser_page(ALLOW) as page:
                await page.goto(f"{pages}/shop.html")
                await page.evaluate("history.pushState({}, '', '/akamai.html?q=milch')")
                await page.evaluate("location.hash = 'offers'")
                await page.wait_for_url(f"{pages}/akamai.html?q=milch#offers")
                await browser.robots_checkpoint(page, ALLOW)
                seen["checked"] = True
            seen["refused"] = list(guard.refused)

    asyncio.run(go())
    assert seen == {"checked": True, "refused": []}
    assert _Recorder.paths == ["/shop.html"]


def test_route_guard_aborts_when_the_robots_check_fails(pages, monkeypatch):
    """An exception inside the check still answers the route (an unanswered route hangs the
    page): the navigation is aborted and the run ends as blocked with the failure."""
    async def broken(self, url):
        raise RuntimeError("cache broke")

    monkeypatch.setattr(robots.Guard, "denial", broken)
    cache = robots.RobotsCache(clock=Clock())

    async def go():
        async with _client(Site((200, PAGES_ROBOTS))) as http, robots.enforced(http, cache):
            async with browser.browser_page(ALLOW) as page:
                with pytest.raises(PlaywrightError, match="ERR_BLOCKED_BY_CLIENT"):
                    await page.goto(f"{pages}/shop.html", timeout=5000)

    with pytest.raises(robots.RobotsBlocked,
                       match=r"robots.txt check failed for .*/shop.html \(RuntimeError\)"):
        asyncio.run(asyncio.wait_for(go(), timeout=60))
    assert "/shop.html" not in _Recorder.paths


def test_checkpoint_does_nothing_outside_a_scan():
    page = SimpleNamespace(url=f"{SITE}/private")
    assert asyncio.run(browser.robots_checkpoint(page, ("shop.test",))) is None


def test_navigator_stops_at_the_top_of_its_loop_after_a_refusal(monkeypatch):
    async def never(*args, **kwargs):
        raise AssertionError("the model must not be asked")

    monkeypatch.setattr(navigator, "_observe", never)
    monkeypatch.setattr(navigator, "_ask", never)
    monkeypatch.setattr(navigator, "is_challenge", never)
    monkeypatch.setattr(navigator, "_client", lambda: _NoSession())  # no VLM client needed
    cache = robots.RobotsCache(clock=Clock())
    page = SimpleNamespace(url=f"{SITE}/shop")

    async def go():
        async with _client(Site((200, "User-agent: *\nDisallow: /private"))) as http, \
                robots.enforced(http, cache) as guard:
            guard.refuse(f"{SITE}/private")
            await navigator.heal(page, "Open the offers", never, allow=("shop.test",),
                                 inputs={}, emit=lambda e: None)

    with pytest.raises(robots.RobotsBlocked, match="robots.txt disallows /private"):
        asyncio.run(go())


def test_navigator_checks_the_redirect_of_its_last_action(pages, monkeypatch):
    """The action that reaches the goal may redirect onto a disallowed page: heal ends with
    RobotsBlocked instead of reporting success."""
    async def observe(page):
        return [], b""

    async def ask(*args, **kwargs):
        return '{"action": "back"}'

    async def perform(page, act, elements, inputs):
        await page.goto(f"{pages}/hop")  # the action's click followed a redirect

    async def no_wall(page):
        return False

    async def reached():
        return True

    monkeypatch.setattr(navigator, "_observe", observe)
    monkeypatch.setattr(navigator, "_ask", ask)
    monkeypatch.setattr(navigator, "_perform", perform)
    monkeypatch.setattr(navigator, "is_challenge", no_wall)
    monkeypatch.setattr(navigator, "_client", lambda: _NoSession())
    cache = robots.RobotsCache(clock=Clock())
    seen: dict = {}

    async def go():
        async with _client(Site((200, PAGES_ROBOTS))) as http, robots.enforced(http, cache):
            async with browser.browser_page(ALLOW) as page:
                await page.goto(f"{pages}/shop.html")
                seen["result"] = await navigator.heal(
                    page, "Open the offers", reached, allow=ALLOW, inputs={},
                    emit=lambda e: None)

    with pytest.raises(robots.RobotsBlocked, match="robots.txt disallows /akamai.html"):
        asyncio.run(go())
    assert "result" not in seen  # heal raised; it did not report success
    assert _Recorder.paths[-2:] == ["/hop", "/akamai.html"]  # the hop was fetched (the gap)


# --- wiring: scan runtime, basket agent, geocoder exemption ----------------------------------

@pytest.fixture
def fresh_cache(monkeypatch) -> robots.RobotsCache:
    """The module's CACHE (what enforced() uses by default), emptied for this test."""
    monkeypatch.setattr(robots.CACHE, "entries", {})
    monkeypatch.setattr(robots.CACHE, "refused", deque(maxlen=100))
    monkeypatch.setattr(robots.CACHE, "_good", {})
    return robots.CACHE


class _NoSession:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return None


def test_scan_enforces_robots_and_ends_blocked(monkeypatch, fresh_cache):
    """The chain's client has the guard first and the caller's hook after it; a refused request
    ends the run "blocked" with the reason. Fakes as in test_scan_runtime's budget test."""
    site = Site((200, "User-agent: *\nDisallow: /private"))
    store = Store(id="s1", name="S", address="A", lat=48.1, lon=11.6, edition="e1")
    leaflet = Leaflet(key="2026-W42", title="T", kind="main", valid_from=date(2026, 1, 1),
                      valid_to=date(2099, 1, 1), dates_source="feed")
    seen: dict = {}
    observed: list[str] = []

    async def observer(request: httpx.Request) -> None:
        observed.append(request.url.path)

    async def leaflets(ctx, store):
        seen["hooks"] = list(ctx.http.event_hooks["request"])
        seen["current"] = robots.current()
        await ctx.http.get(f"{SITE}/ok")
        return [leaflet]

    async def acquire(ctx, store, leaflet):
        await ctx.http.get(f"{SITE}/private/leaflet.pdf")
        return Acquired(tier=2, pages=[Page(image_url=f"{SITE}/1.jpg")])

    async def pick(module, ctx, radius_km, override, force):
        return store, 0.5, []

    async def nothing(*args, **kwargs):
        return None

    module = SimpleNamespace(NAME="fake", LABEL="Fake", LOYALTY=None, leaflets=leaflets,
                             acquire=acquire)
    monkeypatch.setattr(scan, "http_client", lambda contact: _client(site))
    monkeypatch.setattr(scan, "_pick_store", pick)
    monkeypatch.setattr(scan, "SessionLocal", _NoSession)
    monkeypatch.setattr(scan.repo, "get_leaflet", nothing)
    monkeypatch.setattr(scan.repo, "upsert_leaflet", nothing)
    monkeypatch.setattr(scan, "_save", nothing)
    run = scan.Run(chain="fake", plz="80331", scan_id=uuid.uuid4())
    loc = Location(lat=48.1, lon=11.6, plz="80331", label="80331")
    asyncio.run(scan._run_chain(run, module, loc, 5.0, None, False, "canary",
                                {"request": [observer]}))
    guard = seen["current"]
    assert isinstance(guard, robots.Guard) and seen["hooks"] == [guard, observer]
    [done] = [e for e in run.events if e["event"] == "chain_done"]
    assert (done["status"], done["reason"]) == ("blocked",
                                                "robots.txt disallows /private/leaflet.pdf")
    assert site.seen == ["/robots.txt", "/ok"]
    assert observed == ["/robots.txt", "/ok"]  # the caller's hook runs after the guard
    assert list(fresh_cache.refused) == [f"{SITE}/private/leaflet.pdf"]
    assert robots.current() is None


def test_refused_page_image_cancels_the_other_page_reads(monkeypatch, fresh_cache):
    """One disallowed leaflet page ends the run as blocked and stops its sibling reads: no VLM
    call and no "page" event happens after chain_done."""
    site = Site((200, "User-agent: *\nDisallow: /private"))
    store = Store(id="s1", name="S", address="A", lat=48.1, lon=11.6, edition="e1")
    leaflet = Leaflet(key="2026-W42", title="T", kind="main", valid_from=date(2026, 1, 1),
                      valid_to=date(2099, 1, 1), dates_source="feed")
    read: list[int] = []

    async def leaflets(ctx, store):
        return [leaflet]

    async def acquire(ctx, store, leaflet):
        return Acquired(tier=2, pages=[Page(image_url=f"{SITE}/private/0.jpg")]
                        + [Page(image_url=f"{SITE}/{i}.jpg") for i in range(1, 4)])

    async def pick(module, ctx, radius_km, override, force):
        return store, 0.5, []

    async def nothing(*args, **kwargs):
        return None

    async def extract_page(content, text, **kwargs):
        await asyncio.sleep(0.2)  # the VLM reading the page
        read.append(kwargs["page"])
        return SimpleNamespace(ok=True, offers=[], tokens_in=1, tokens_out=1, reason=None)

    monkeypatch.setattr("app.clients.vision.extract_page", extract_page)
    monkeypatch.setattr(scan, "jpeg_thumb", lambda *args: b"x")
    module = SimpleNamespace(NAME="fake", LABEL="Fake", LOYALTY=None, leaflets=leaflets,
                             acquire=acquire)
    monkeypatch.setattr(scan, "http_client", lambda contact: _client(site))
    monkeypatch.setattr(scan, "_pick_store", pick)
    monkeypatch.setattr(scan, "SessionLocal", _NoSession)
    monkeypatch.setattr(scan.repo, "get_leaflet", nothing)
    monkeypatch.setattr(scan.repo, "upsert_leaflet", nothing)
    monkeypatch.setattr(scan, "_save", nothing)
    run = scan.Run(chain="fake", plz="80331", scan_id=uuid.uuid4())
    loc = Location(lat=48.1, lon=11.6, plz="80331", label="80331")

    async def go() -> int:
        await scan._run_chain(run, module, loc, 5.0, None, False, "user", None)
        ended = len(run.events)
        await asyncio.sleep(0.4)  # long enough for an orphaned read to finish
        return ended

    ended = asyncio.run(go())
    [done] = [e for e in run.events if e["event"] == "chain_done"]
    assert (done["status"], done["reason"]) == ("blocked", "robots.txt disallows /private/0.jpg")
    assert run.events[ended - 1]["event"] == "chain_done" and run.events[ended:] == []
    assert read == [] and not any(e["event"] == "page" for e in run.events)
    assert "/private/0.jpg" not in site.seen


def test_basket_inspect_reports_a_refused_image(monkeypatch, fresh_cache):
    site = Site((200, "User-agent: *\nDisallow: /private"))
    monkeypatch.setattr(basket, "http_client", lambda contact: _client(site))
    offer = Offer(chain="penny", product_name="Milch", price_cents=99, pack_qty=1000,
                  pack_unit="ml", source="feed", price_check="feed",
                  image_url=f"{SITE}/private/milch.jpg")
    entry = basket.Entry("o0", PlanStore(chain="penny", store="Penny", km=1.0), offer)
    answer = asyncio.run(basket._inspect(entry, "Is it organic?"))
    assert answer == "Image not fetched: robots.txt disallows /private/milch.jpg"
    assert site.seen == ["/robots.txt"]


def _no_guard(http: httpx.AsyncClient) -> bool:
    return not any(isinstance(h, robots.Guard) for h in http.event_hooks["request"])


def test_geocoder_client_of_locate_is_not_guarded(monkeypatch):
    seen: dict = {}

    async def geocode(query, http):
        seen.update(unguarded=_no_guard(http), current=robots.current(),
                    agent=http.headers["User-Agent"])
        return Location(lat=48.1, lon=11.6, plz="80331", label="München")

    monkeypatch.setattr(scan_api, "geocode", geocode)
    location = asyncio.run(scan_api._locate("80331"))
    assert location.plz == "80331"
    assert seen["unguarded"] and seen["current"] is None
    assert seen["agent"].startswith(f"{robots.AGENT}/0.1 (+{get_settings().contact}")


def test_geocoder_client_of_the_canary_is_not_guarded(monkeypatch):
    seen: dict = {}

    async def geocode(query, http):
        seen.update(unguarded=_no_guard(http), current=robots.current())
        return None

    monkeypatch.setattr(canary, "geocode", geocode)
    monkeypatch.setattr(canary, "configure_logging", lambda: None)
    assert asyncio.run(canary.main(["--chain", "penny", "--plz", "80331"])) == 2
    assert seen == {"unguarded": True, "current": None}
