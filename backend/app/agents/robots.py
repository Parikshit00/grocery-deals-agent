"""robots.txt enforcement for the chain agents (RFC 9309), applied to every request they make.

A disallowed URL is not fetched: the check runs as an httpx request event hook, before the
request (and before every redirect hop) leaves the client, and raises `RobotsBlocked`. That is a
`Blocked`, so a chain ends honestly as "blocked" with the reason shown to users. (The browser's
redirect hops are the exception: they are checked once they have loaded, see Scope.)

Scope
-----
- Checked: every httpx request of a chain agent on the scan's client (stores, leaflets, feeds,
  leaflet pages, redirect hops included), the basket agent's product-image download, and every
  top-level browser navigation (`agents/browser.py` asks `Guard.denial` in its route guard;
  service workers are blocked, so none can answer a navigation past it). Playwright does not
  route redirect hops, so the browser records every hop of a main-frame navigation as it is
  requested and checks it afterwards (`robots_checkpoint` and the end of `browser_page`): a
  disallowed redirect target is fetched once, and the run then ends as blocked. A URL change
  without a request (history.pushState) fetches nothing and is not checked.
- Not checked: subresources and XHR that a retailer's own page loads in the browser (scripts,
  styles, images, its API calls). The agent does not choose them, aborting them breaks the pages,
  and the one XHR the agents read (live.api.schwarz) has no robots.txt.
- Exempt by design: the geocoder. Postcode lookups use OpenStreetMap Nominatim as an API client
  under its usage policy (at most one request per second, identifying User-Agent, cached
  results), not as a crawler, so `services/geo.py` runs on a plain `http_client()` that never
  enters `enforced()`.

Rules (RFC 9309)
----------------
- The groups whose user-agent product token equals `AGENT` (case-insensitive, whole token:
  "grocery" does not apply) are merged; if there are none, all "*" groups are merged.
- The longest matching pattern wins, Allow wins a tie; "*" and a trailing "$" are supported; the
  target is the URL's path plus query; an empty "Disallow:" allows everything; "/robots.txt" is
  always allowed. Patterns and targets compare in one spelling: non-ASCII percent-encoded,
  percent-encoded unreserved characters decoded ("/%7Ea" is "/~a"), other percent-encodings
  kept ("/%2F" is not "/"). Matching is linear in pattern times target length (no backtracking
  regex, so a robots.txt full of "*" cannot stall the event loop).
- robots.txt answers 2xx: parsed (first `MAX_BYTES`), cached `TTL_S`. Any other answer below 500
  (404, and also 401/403, unlike `urllib.robotparser`): allow all, cached `TTL_S`. 5xx or a
  network error: disallow all for `UNREACHABLE_TTL_S`, unless an earlier 2xx/4xx answer for that
  origin exists, which is then kept (RFC 9309 section 2.4).
- robots.txt is fetched through the same client as the request (same User-Agent, timeouts and
  transport, so tests' MockTransport serves it), following redirects; that fetch and its hops
  are not checked themselves.

API (used by services/scan.py, services/basket.py, agents/browser.py, agents/navigator.py and
canary.py)
---------------------------------------------------------------------------------------------
    AGENT, TTL_S, UNREACHABLE_TTL_S, MAX_BYTES
    class RobotsBlocked(Blocked)
    parse(text, source="200") -> Rules              Rules.source, Rules.allows(target) -> bool
    class RobotsCache(clock=time.monotonic)         .entries {(scheme, host, port): (expires,
                                                    Rules)}, .refused deque of refused URLs,
                                                    async .rules(url, fetch) -> Rules
    CACHE: RobotsCache                              process-wide cache (the canary reports it)
    class Guard(http, cache=CACHE)                  httpx request hook; .refused (this run),
                                                    .reasons {url: reason}, async .denial(url)
                                                    -> reason | None, .refuse(url, reason=None)
                                                    records a refusal
    async with enforced(http, cache=CACHE) as guard: installs the guard as the first request
                                                    hook and makes current() return it
    current() -> Guard | None
"""
from __future__ import annotations

import asyncio
import re
import string
import time
from collections import deque
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.agents import base
from app.core.logging import get_logger

log = get_logger(__name__)

AGENT = base.PRODUCT
TTL_S = 86_400.0  # RFC 9309 2.4: do not use a cached robots.txt for more than 24 hours
UNREACHABLE_TTL_S = 300.0  # retry an unreachable robots.txt after 5 minutes
MAX_BYTES = 512_000  # RFC 9309 2.5: parse at least the first 500 KiB

Origin = tuple[str, str, int]
Fetch = Callable[[httpx.URL], Awaitable[httpx.Response]]

_DEFAULT_PORTS = {"http": 80, "https": 443}
_TOKEN = re.compile(r"[A-Za-z_-]+")
_HEX = re.compile(r"%[0-9a-fA-F]{2}")
# Printable ASCII stays as written; only other characters are percent-encoded (UTF-8).
_SAFE = "".join(chr(c) for c in range(0x21, 0x7F))
_UNRESERVED = frozenset(string.ascii_letters + string.digits + "-._~")  # RFC 3986 2.3


class RobotsBlocked(base.Blocked):
    """robots.txt disallows the URL (or could not be read, which counts as disallow all)."""


@dataclass(frozen=True)
class Rule:
    allow: bool
    pattern: str  # normalized by _normalize: one spelling per character

    def matches(self, target: str) -> bool:
        """Whether the pattern matches a prefix of `target` ("*" any characters, a trailing "$"
        the end). The set of candidate positions in `target` is advanced one pattern character
        at a time, as in Google's reference matcher: O(len(pattern) * len(target))."""
        pattern, end = self.pattern, len(target)
        positions = [0]  # sorted: where the rest of the pattern may start matching
        for i, char in enumerate(pattern):
            if char == "$" and i == len(pattern) - 1:
                return positions[-1] == end
            if char == "*":
                positions = list(range(positions[0], end + 1))
            else:
                positions = [p + 1 for p in positions if p < end and target[p] == char]
                if not positions:
                    return False
        return True


@dataclass(frozen=True)
class Rules:
    """The rules for AGENT on one origin, and what they came from.

    source: the robots.txt status code ("200", "404", "503") or the network error's type
    ("ConnectError"); `reachable` is False for 5xx and network errors (disallow all).
    """

    source: str
    rules: tuple[Rule, ...] = ()
    reachable: bool = True

    def allows(self, target: str) -> bool:
        """Whether AGENT may fetch `target` (path plus query, e.g. "/shop?q=1")."""
        target = _normalize(target)
        if target.split("?", 1)[0] == "/robots.txt":
            return True
        if not self.reachable:
            return False
        best: Rule | None = None
        for rule in self.rules:
            if not rule.matches(target):
                continue
            if (best is None or len(rule.pattern) > len(best.pattern)
                    or (len(rule.pattern) == len(best.pattern) and rule.allow)):
                best = rule
        return best is None or best.allow


def _normalize(s: str) -> str:
    """One spelling for patterns and targets (RFC 9309 2.2.2): non-ASCII percent-encoded as
    UTF-8, a percent-encoded unreserved character ("%7E") decoded ("~"), every other
    percent-encoding kept with upper-case hex digits ("%2f" -> "%2F", still not "/")."""
    s = quote(s, safe=_SAFE)

    def octet(m: re.Match[str]) -> str:
        char = chr(int(m.group(0)[1:], 16))
        return char if char in _UNRESERVED else m.group(0).upper()

    return _HEX.sub(octet, s)


def _rule(allow: bool, value: str) -> Rule:
    return Rule(allow=allow, pattern=_normalize(value))


def _token(value: str) -> str:
    value = value.strip()
    if value.startswith("*"):
        return "*"
    m = _TOKEN.match(value)
    return m.group(0).lower() if m else ""


def parse(text: str, source: str = "200") -> Rules:
    """Rules that apply to AGENT in a robots.txt body."""
    groups: list[tuple[set[str], list[Rule]]] = []
    in_agents = False  # the previous record was a user-agent line: same group
    for raw in text.lstrip("﻿").splitlines():
        line = raw.split("#", 1)[0].strip()
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if key == "user-agent":
            if not in_agents:
                groups.append((set(), []))
            groups[-1][0].add(_token(value))
            in_agents = True
        elif key in ("allow", "disallow"):
            in_agents = False
            if groups and value:  # rules before any user-agent line and empty values: none
                groups[-1][1].append(_rule(key == "allow", value))
        # Other records (sitemap, crawl-delay, ...) neither form nor end a group.
    agent = AGENT.lower()
    mine = [rules for agents, rules in groups if agent in agents]
    if not mine:
        mine = [rules for agents, rules in groups if "*" in agents]
    return Rules(source=source, rules=tuple(r for rules in mine for r in rules))


def target(url: httpx.URL) -> str:
    """Path plus query as sent, the string robots.txt patterns are matched against."""
    return url.raw_path.decode("ascii", "replace") or "/"


def origin(url: httpx.URL) -> Origin:
    return url.scheme, url.host, url.port or _DEFAULT_PORTS.get(url.scheme, 0)


class RobotsCache:
    """robots.txt answers per origin with RFC 9309 status handling and expiry."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.entries: dict[Origin, tuple[float, Rules]] = {}
        self.refused: deque[str] = deque(maxlen=100)
        self._good: dict[Origin, Rules] = {}  # last 2xx/4xx answer, kept when a refresh fails
        self._locks: dict[Origin, asyncio.Lock] = {}
        self._loop: asyncio.AbstractEventLoop | None = None

    def _fresh(self, key: Origin) -> Rules | None:
        entry = self.entries.get(key)
        return entry[1] if entry and entry[0] > self.clock() else None

    def _lock(self, key: Origin) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if loop is not self._loop:  # locks belong to a loop; pytest runs a new loop per test
            self._loop, self._locks = loop, {}
        return self._locks.setdefault(key, asyncio.Lock())

    async def rules(self, url: httpx.URL, fetch: Fetch) -> Rules:
        """The rules for url's origin; on a miss `fetch(<origin>/robots.txt)` is awaited once
        per origin, however many requests wait for it."""
        key = origin(url)
        if (hit := self._fresh(key)) is not None:
            return hit
        async with self._lock(key):
            if (hit := self._fresh(key)) is not None:
                return hit
            rules, ttl = await self._load(key, url.join("/robots.txt"), fetch)
            self.entries[key] = (self.clock() + ttl, rules)
            return rules

    async def _load(self, key: Origin, robots_url: httpx.URL, fetch: Fetch) -> tuple[Rules, float]:
        try:
            resp = await fetch(robots_url)
        except httpx.HTTPError as exc:
            return self._unreachable(key, robots_url, type(exc).__name__)
        status = resp.status_code
        if status >= 500:
            return self._unreachable(key, robots_url, str(status))
        if 200 <= status < 300:
            rules = parse(resp.content[:MAX_BYTES].decode("utf-8", "replace"), str(status))
        else:  # 4xx (and an unfollowed 3xx): no usable robots.txt, allow all
            rules = Rules(source=str(status))
        log.info("robots.fetched", url=str(robots_url), source=rules.source,
                 rules=len(rules.rules))
        self._good[key] = rules
        return rules, TTL_S

    def _unreachable(self, key: Origin, robots_url: httpx.URL, source: str) -> tuple[Rules, float]:
        if (good := self._good.get(key)) is not None:
            log.warning("robots.refresh_failed", url=str(robots_url), source=source,
                        kept=good.source)
            return good, UNREACHABLE_TTL_S
        log.warning("robots.unreachable", url=str(robots_url), source=source)
        return Rules(source=source, reachable=False), UNREACHABLE_TTL_S


CACHE = RobotsCache()

# Set while a Guard fetches robots.txt, so that request and its redirect hops are not checked.
_fetching: ContextVar[bool] = ContextVar("robots_fetching", default=False)
_current: ContextVar[Guard | None] = ContextVar("robots_guard", default=None)


class Guard:
    """httpx request event hook enforcing robots.txt for every request of one client."""

    def __init__(self, http: httpx.AsyncClient, cache: RobotsCache = CACHE) -> None:
        self.http = http
        self.cache = cache
        self.refused: list[str] = []  # URLs refused in this run
        self.reasons: dict[str, str] = {}  # why, for the URLs refused with a reason

    async def __call__(self, request: httpx.Request) -> None:
        if _fetching.get() or request.url.path == "/robots.txt":
            return
        reason = await self.denial(request.url)
        if reason is not None:
            self.refuse(str(request.url), reason)
            raise RobotsBlocked(reason)

    async def denial(self, url: httpx.URL | str) -> str | None:
        """Why robots.txt forbids fetching `url`, or None if it may be fetched. Records nothing:
        a caller that then refuses the URL calls `refuse`."""
        url = httpx.URL(url)
        if url.scheme not in _DEFAULT_PORTS or url.path == "/robots.txt":
            return None
        rules = await self.cache.rules(url, self._fetch)
        if not rules.reachable:
            return f"robots.txt unreachable for {url.host}"
        path = target(url)
        return None if rules.allows(path) else f"robots.txt disallows {path}"

    def refuse(self, url: str, reason: str | None = None) -> None:
        """Record a refused URL (and why, if given) for this run and in the cache's recent
        list."""
        self.refused.append(url)
        if reason is not None:
            self.reasons.setdefault(url, reason)
        self.cache.refused.append(url)
        log.warning("robots.refused", url=url)

    async def _fetch(self, robots_url: httpx.URL) -> httpx.Response:
        token = _fetching.set(True)
        try:
            async with self.http.stream("GET", robots_url, follow_redirects=True) as resp:
                body = bytearray()
                async for chunk in resp.aiter_bytes():
                    body += chunk
                    if len(body) >= MAX_BYTES:
                        break
                return httpx.Response(resp.status_code, content=bytes(body[:MAX_BYTES]),
                                      request=resp.request)
        finally:
            _fetching.reset(token)


@asynccontextmanager
async def enforced(http: httpx.AsyncClient, cache: RobotsCache = CACHE) -> AsyncIterator[Guard]:
    """Enforce robots.txt on `http` for the block: the guard becomes the first request hook
    (later hooks must be added after it, not by replacing `http.event_hooks`) and `current()`
    returns it, for the browser and navigator checks. Both are undone on exit."""
    guard = Guard(http, cache)
    hooks = http.event_hooks
    http.event_hooks = {"request": [guard, *hooks["request"]], "response": hooks["response"]}
    token = _current.set(guard)
    try:
        yield guard
    finally:
        _current.reset(token)
        hooks = http.event_hooks
        http.event_hooks = {"request": [h for h in hooks["request"] if h is not guard],
                            "response": hooks["response"]}


def current() -> Guard | None:
    """The guard of the enclosing `enforced()` block in this task, if any."""
    return _current.get()
