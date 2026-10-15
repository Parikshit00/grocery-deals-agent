"""Browser checks of the UI (P7 gate), run in the dev image on the host network.

--states: every backend/tests/fixtures/ui/<state>.json forces one UI state by answering /api
requests with canned responses (page.route over _base.json + the state's routes) and asserts
that its copy shows, that no spinner is left after 30 s and that the console stays free of
errors. Views stay mounted while hidden, so copy, counts and absences consider visible elements
only. Browser network lines ("Failed to load resource") for deliberately failing canned
responses are not app errors and are skipped; an /api request with no canned answer fails.
replay_session.json is the replay state instead: the replay build (npm run build:replay, served
from frontend/dist/replay without a server) plays that synthetic session; the recorded scan must
end in its recorded agent states, recipe and plan must render, and no request may reach /api.
--happy: one unmocked flow against the live stack (location, stores, plan that survives a view
switch, mission-control scan of every chain, runs with trace links, evals).

    python backend/tests/e2e_ui.py --states [--only sse_drop] [--url http://127.0.0.1:28735]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import defaultdict
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Browser, Locator, Page, Route, async_playwright

FIXTURES = Path(__file__).parent / "fixtures" / "ui"
REPLAY_DIST = Path(__file__).parents[2] / "frontend" / "dist" / "replay"
REPLAY_URL = "http://replay.test/"
FINAL = {"ok": "Offers current", "partial": "Partly read", "blocked": "Blocked",
         "no_store": "No store in radius", "not_covered": "Not covered", "failed": "Scan failed"}
NOW = "2026-10-14T10:00:00+02:00"  # the day the fixtures were recorded
WAIT_MS = 30_000
NO_SPINNER = "document.querySelectorAll('.spin').length === 0"


def sse(events: list[dict]) -> str:
    return ": ping\n\n" + "".join(f"data: {json.dumps(e)}\n\n" for e in events)


class CannedApi:
    """Answers /api requests from a {"METHOD /path-glob": spec | [spec, ...]} map; a list is
    served in order (its last entry repeats)."""

    def __init__(self, routes: dict[str, Any]) -> None:
        self.routes = routes
        self.calls: defaultdict[str, int] = defaultdict(int)
        self.log: list[str] = []
        self.unmocked: list[str] = []

    async def handle(self, route: Route) -> None:
        req = route.request
        url = urlparse(req.url)
        self.log.append(f"{req.method} {url.path}?{url.query}")
        key = next((k for k in self.routes if fnmatch(f"{req.method} {url.path}", k)), None)
        if key is None:
            self.unmocked.append(f"{req.method} {url.path}")
            await route.fulfill(status=404, json={"detail": "not mocked"})
            return
        spec = self.routes[key]
        if isinstance(spec, list):
            spec = spec[min(self.calls[key], len(spec) - 1)]
        self.calls[key] += 1
        if spec.get("delay_s"):
            await asyncio.sleep(spec["delay_s"])
        if spec.get("abort"):
            await route.abort()
        elif "sse" in spec:
            await route.fulfill(status=200, content_type="text/event-stream", body=sse(spec["sse"]))
        else:
            await route.fulfill(status=spec.get("status", 200), json=spec.get("json"))


def watch_console(page: Page) -> list[str]:
    errors: list[str] = []

    def on_console(msg: Any) -> None:
        if msg.type == "error" and not msg.text.startswith("Failed to load resource"):
            errors.append(msg.text)

    page.on("console", on_console)
    page.on("pageerror", lambda exc: errors.append(str(exc)))
    return errors


async def locate(page: Page, url: str, location: str) -> None:
    await page.goto(url)
    await page.get_by_label("Address or postcode").fill(location)
    await page.get_by_role("button", name="Set location").click()


def visible(page: Page, selector: str | None = None, text: str | None = None) -> Locator:
    found = page.get_by_text(text) if text is not None else page.locator(selector or "*")
    return found.filter(visible=True)


async def act(page: Page, action: str, spec: dict) -> None:
    if action == "items":
        await page.get_by_label("Items, separated by commas or lines").fill(spec["list"])
        await page.get_by_role("button", name="Make items").click()
    elif action.startswith("view:"):
        await page.get_by_role("button", name=action[5:], exact=True).click()
    elif action.startswith("select:"):
        label, _, value = action[7:].partition("=")
        await page.get_by_label(label, exact=True).select_option(value)
    elif action == "scan":
        await page.get_by_role("button", name="Scan", exact=True).click()
    elif action == "cancel":
        await page.get_by_role("button", name="Cancel", exact=True).click()
    elif action == "plan":
        await page.get_by_label("Items, separated by commas or lines").fill(spec["list"])
        await page.get_by_role("button", name="Make items").click()
        await page.get_by_role("button", name="Find the cheapest plan").click()
    elif action.startswith("wait:"):
        await page.get_by_role("button", name=action[5:], exact=True).wait_for(timeout=WAIT_MS)
    elif action.startswith("click:"):
        await visible(page, text=action[6:]).first.click(timeout=WAIT_MS)
    else:
        raise ValueError(f"unknown action {action}")


async def check_state(browser: Browser, url: str, spec: dict, base: dict) -> list[str]:
    context = await browser.new_context()
    page = await context.new_page()
    await page.clock.set_fixed_time(NOW)
    errors = watch_console(page)
    api = CannedApi({**base["routes"], **spec.get("routes", {})})
    await page.route("**/api/**", api.handle)
    failures: list[str] = []
    try:
        await locate(page, url, spec.get("location", "80331"))
        if spec.get("view"):
            await page.get_by_role("button", name=spec["view"].capitalize(), exact=True).click()
        for action in spec.get("actions", []):
            await act(page, action, spec)
        for text in spec["expect"]:
            try:
                await visible(page, text=text).first.wait_for(timeout=WAIT_MS)
            except Exception:  # noqa: BLE001 - report and continue with the other checks
                failures.append(f"copy not shown: {text!r}")
        try:
            await page.wait_for_function(NO_SPINNER, timeout=WAIT_MS)
        except Exception:  # noqa: BLE001
            failures.append("a spinner was still visible after 30 s")
        for text in spec.get("absent", []):
            if await visible(page, text=text).count():
                failures.append(f"must not show: {text!r}")
        for name in spec.get("disabled", []):
            if await page.get_by_role("button", name=name, exact=True).is_enabled():
                failures.append(f"button should be disabled: {name!r}")
        for selector, n in spec.get("count", {}).items():
            if (got := await visible(page, selector).count()) != n:
                failures.append(f"{selector}: {got} elements, expected {n}")
        for label, want in spec.get("values", {}).items():
            if (got := await page.get_by_label(label, exact=True).input_value()) != want:
                failures.append(f"{label}: value {got!r}, expected {want!r}")
        for pattern in spec.get("requested", []):
            if not any(fnmatch(line, pattern) for line in api.log):
                failures.append(f"no request matching {pattern!r}: {api.log}")
    except Exception as exc:  # noqa: BLE001 - one broken state must not hide the others
        failures.append(f"{type(exc).__name__}: {exc}")
    failures += [f"console error: {e}" for e in errors]
    failures += [f"unmocked request: {u}" for u in api.unmocked]
    await context.close()
    return failures


async def check_replay(browser: Browser, session: dict) -> list[str]:
    if not (REPLAY_DIST / "index.html").is_file():
        return [f"no replay build in {REPLAY_DIST}: run npm run build:replay first"]
    page = await browser.new_page(viewport={"width": 1920, "height": 1080})
    await page.clock.set_fixed_time(NOW)
    errors = watch_console(page)
    api: list[str] = []

    async def serve(route: Route) -> None:
        path = urlparse(route.request.url).path
        file = REPLAY_DIST / (path.lstrip("/") or "index.html")
        if path.startswith("/api/"):
            api.append(f"{route.request.method} {path}")
            await route.abort()
        elif path == "/replay/session.json":
            await route.fulfill(json=session)
        elif file.is_file():
            await route.fulfill(path=file)
        else:
            await route.fulfill(status=404)

    await page.route(f"{REPLAY_URL}**", serve)
    failures: list[str] = []
    try:
        await locate(page, REPLAY_URL, session["location"])
        await page.get_by_text("Replay of a recorded run from 14.10.2026").wait_for(timeout=WAIT_MS)
        await page.get_by_role("button", name="4x").click()
        await act(page, "view:Offers", {})
        await act(page, "scan", {})
        await page.get_by_text("Scan finished.").wait_for(timeout=WAIT_MS)
        labels = {c["id"]: c["label"] for c in session["status"]["chains"]}
        events = [e["data"] for e in session["scan"]["events"]]
        for e in events:
            if e["event"] == "chain_done":
                card = page.get_by_label(f"{labels[e['chain']]} agent", exact=True)
                pill = (await card.locator(".agent-head .pill").inner_text()).strip()
                if pill != FINAL[e["status"]]:
                    failures.append(f"{e['chain']}: {pill!r}, recorded {e['status']!r}")
        pages = sum(e["event"] == "page" for e in events)
        if (got := await visible(page, ".agent .thumb .no-img").count()) != pages:
            failures.append(f"{got} page placeholders for {pages} recorded pages")
        if await page.locator(".agent-view img, .card-img").count():
            failures.append("a retailer image is shown")
        await act(page, "view:Plan", {})
        await page.get_by_text("Recipe", exact=True).click()
        await page.get_by_label("Recipe or dish").fill("anything")
        await page.get_by_role("button", name="Get ingredients").click()
        await page.get_by_text("Thought for").wait_for(timeout=WAIT_MS)
        await page.get_by_role("button", name="Find the cheapest plan").click()
        for text in ("Cheapest single store", "Cheapest split", "Basket agent"):
            await visible(page, text=text).first.wait_for(timeout=WAIT_MS)
        await act(page, "view:Runs", {})
        await page.locator("table.runs tbody tr").first.wait_for(timeout=WAIT_MS)
        if n := await page.locator("a.trace-link, a[href*=':2873']").count():
            failures.append(f"{n} Langfuse or Grafana links shown in replay")
        await act(page, "view:Evals", {})
        await page.locator(".eval-suite").first.wait_for(timeout=WAIT_MS)
        await page.wait_for_function(NO_SPINNER, timeout=WAIT_MS)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{type(exc).__name__}: {exc}")
    failures += [f"console error: {e}" for e in errors]
    failures += [f"request to the API in replay: {a}" for a in api]
    await page.close()
    return failures


async def check_happy(browser: Browser, url: str) -> list[str]:
    """Live stack: stores near 80331, a two-item plan that survives a trip through the other
    views, a (normally cached) mission-control scan of every chain, runs with their trace links,
    the evals tab."""
    page = await browser.new_page(viewport={"width": 1920, "height": 1080})
    errors = watch_console(page)
    failures: list[str] = []
    try:
        await locate(page, url, "80331")
        await page.get_by_text("OpenStreetMap contributors").first.wait_for(timeout=WAIT_MS)
        await act(page, "plan", {"list": "Mango, 250 g Butter"})
        await page.get_by_text("Prices for").wait_for(timeout=180_000)
        await page.get_by_text("Cheapest single store").wait_for(timeout=WAIT_MS)
        await page.get_by_role("button", name="Offers", exact=True).click()
        chains = await page.locator(".agent").filter(visible=True).count()
        await page.get_by_role("button", name="Scan", exact=True).click()
        await page.get_by_text("Scan finished.").wait_for(timeout=600_000)
        cards = await page.locator(".agent").filter(visible=True).count()
        bad = await page.locator(".agent .agent-error").all_inner_texts()
        states = await page.locator(".agent .agent-head .pill").all_inner_texts()
        ticks = await page.locator(".tick").count()
        totals = await page.locator(".totals dd").all_inner_texts()
        print(f"  agents: {cards} (standby {chains}), states: {states}, ticks: {ticks}, "
              f"totals: {totals}, chain errors: {bad}")
        if cards != chains or not ticks:
            failures.append(f"mission control: {cards} windows for {chains} chains, {ticks} ticks")
        await page.get_by_role("button", name="Plan", exact=True).click()
        await page.get_by_text("Cheapest single store").wait_for(timeout=WAIT_MS)
        await page.get_by_role("button", name="Runs", exact=True).click()
        await page.locator("table.runs tbody tr").first.wait_for(timeout=WAIT_MS)
        await page.get_by_role("link", name="Live metrics").wait_for(timeout=WAIT_MS)
        print(f"  trace links: {await page.locator('a.trace-link').count()}")
        await page.get_by_role("button", name="Evals", exact=True).click()
        evals = page.locator("[aria-labelledby=evals-h] :is(.eval-suite, .empty)")
        await evals.first.wait_for(timeout=WAIT_MS)
        await page.wait_for_function(NO_SPINNER, timeout=WAIT_MS)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"{type(exc).__name__}: {exc}")
    failures += [f"console error: {e}" for e in errors]
    await page.close()
    return failures


async def main() -> int:
    parser = argparse.ArgumentParser(description="Browser checks of the UI.")
    parser.add_argument("--url", default="http://127.0.0.1:28735")
    parser.add_argument("--states", action="store_true")
    parser.add_argument("--happy", action="store_true")
    parser.add_argument("--only", help="run one state")
    args = parser.parse_args()
    if not (args.states or args.happy):
        parser.error("pass --states and/or --happy")
    base = json.loads((FIXTURES / "_base.json").read_text())
    failed = 0
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        jobs: list[tuple[str, Any]] = []
        if args.states:
            for path in sorted(FIXTURES.glob("[!_]*.json")):
                if not args.only or path.stem == args.only:
                    spec = json.loads(path.read_text())
                    jobs.append((path.stem, check_replay(browser, spec)
                                 if path.stem == "replay_session"
                                 else check_state(browser, args.url, spec, base)))
        if args.happy:
            jobs.append(("happy (live)", check_happy(browser, args.url)))
        for name, job in jobs:
            failures = await job
            failed += bool(failures)
            print(f"{'FAIL' if failures else 'ok  '} {name}")
            for f in failures:
                print(f"     {f}")
        await browser.close()
    print(f"{len(jobs) - failed}/{len(jobs)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
