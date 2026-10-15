"""Demo dry run of the showcase videos V1 to V4 against the live stack (DOM, JSON and numbers only).

V1 force rescan at 80331 in the Offers view: every agent window reaches its expected final state,
netto streams page thumbnails, lidl shows live browser frames (and, with FORCE_HEAL=lidl:..., a
successful self-heal), rewe ends blocked with a reason. V2 per input (a recipe, or "list:" and
items; repeat --v2 to compare inputs): thinking streams and stays bounded, ingredient chips,
basket, matched items, single-store and split plans, the basket's "Open trace" exists in Langfuse,
a "Why this match" with agent steps. V3 Runs view: "Open trace" ids exist in Langfuse, the lidl
heal steps expand, the Grafana dashboard panels return series. V4 Evals tab shows every suite for
at least two models. Each step is timed.

    python backend/tests/demo_dryrun.py --base http://127.0.0.1:28735 [--v2 "Lasagne fuer 4"]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
from e2e_ui import watch_console
from playwright.async_api import Page, async_playwright

ENV = dict(
    line.split("=", 1) for line in (Path(__file__).parents[2] / ".env").read_text().splitlines()
    if "=" in line and not line.startswith("#")
)
EXPECT = {"penny": "ok", "kaufland": "ok", "aldi": "ok", "netto": "ok", "lidl": "ok",
          "rewe": "blocked"}
FINAL = {"Offers current": "ok", "Cached": "cached", "Blocked": "blocked", "Scan failed": "failed",
         "Partly read": "partial", "Not covered": "not_covered", "No store in radius": "no_store",
         "Cancelled": "cancelled"}
SNAPSHOT = """() => [...document.querySelectorAll('.agent')].filter(a => a.offsetParent).map(a => ({
  chain: a.getAttribute('aria-label').split(' ')[0].toLowerCase(),
  pill: a.querySelector('.agent-head .pill').innerText.trim(),
  state: a.querySelector('.agent-state').innerText.trim(),
  frame: !!a.querySelector('.frame img[src^="data:image"]'),
  thumbs: a.querySelectorAll('.thumbs .thumb').length,
  thumb_imgs: a.querySelectorAll('.thumbs .thumb img[src^="data:image"]').length,
  bars: [...a.querySelectorAll('[role=progressbar]')].map(b => b.innerText.trim()),
  heals: a.querySelectorAll('.heals li').length,
  healed: !!a.querySelector('.heals .badge.heal'),
  heal_steps: [...a.querySelectorAll('.heals li')].map(l => l.innerText.trim()),
  offers: a.querySelector('.counters .big').innerText.trim(),
}))"""

timings: dict[str, float] = {}
failures: list[str] = []
picks: list[dict[str, Any]] = []
LANGFUSE = "http://127.0.0.1:28736/api/public/v2/observations"
AUTH = (ENV["LANGFUSE_PUBLIC_KEY"], ENV["LANGFUSE_SECRET_KEY"])


def check(ok: bool, what: str) -> None:
    print(f"  {'ok  ' if ok else 'FAIL'} {what}")
    if not ok:
        failures.append(what)


class Timer:
    def __init__(self, name: str) -> None:
        self.name = name

    def __enter__(self) -> None:
        self.t0 = time.monotonic()

    def __exit__(self, *exc: object) -> None:
        timings[self.name] = round(time.monotonic() - self.t0, 1)


async def v1(page: Page) -> None:
    await page.get_by_role("button", name="Offers", exact=True).click()
    await page.get_by_role("button", name="Force rescan").click()
    t0 = time.monotonic()
    seen: dict[str, dict[str, Any]] = {c: {"frames": 0, "max_thumbs": 0, "thumb_imgs": 0,
                                           "bars": False, "final_s": None} for c in EXPECT}
    last: list[dict] = []
    while not await page.get_by_text("Scan finished.").count():
        if time.monotonic() - t0 > 1800:
            check(False, "V1 scan finished within 30 min")
            break
        last = await page.evaluate(SNAPSHOT)
        for a in last:
            s = seen.setdefault(a["chain"], {"frames": 0, "max_thumbs": 0, "thumb_imgs": 0,
                                             "bars": False, "final_s": None})
            s["frames"] += a["frame"]
            s["max_thumbs"] = max(s["max_thumbs"], a["thumbs"])
            s["thumb_imgs"] = max(s["thumb_imgs"], a["thumb_imgs"])
            s["bars"] = s["bars"] or bool(a["bars"])
            if a["pill"] in FINAL and s["final_s"] is None:
                s["final_s"] = round(time.monotonic() - t0, 1)
            if a["thumbs"] and "first_thumb_s" not in s:
                s["first_thumb_s"] = round(time.monotonic() - t0, 1)
            if a["frame"] and "first_frame_s" not in s:
                s["first_frame_s"] = round(time.monotonic() - t0, 1)
        await asyncio.sleep(0.25)
    timings["V1 force scan"] = round(time.monotonic() - t0, 1)
    last = await page.evaluate(SNAPSHOT)
    totals = await page.locator(".totals").filter(visible=True).first.inner_text()
    print("  totals:", " ".join(totals.split()))
    for a in last:
        s = seen[a["chain"]]
        print(f"  {a['chain']:9} {a['pill']!r} {a['state']!r} offers={a['offers']} "
              f"heals={a['heals']} bars={a['bars']} seen={json.dumps(s)}")
        if s["final_s"] is not None:
            timings[f"V1 {a['chain']} final"] = s["final_s"]
    by = {a["chain"]: a for a in last}
    for chain, want in EXPECT.items():
        pill = by.get(chain, {}).get("pill")
        check(FINAL.get(pill or "") == want, f"V1 {chain} ends {want} (got {pill!r})")
    netto = seen["netto"]
    check(netto["max_thumbs"] > 0 and netto["thumb_imgs"] > 0 and netto["bars"],
          f"V1 netto streamed page thumbnails ({netto['max_thumbs']} thumbs, "
          f"{netto['thumb_imgs']} with image, progress bar {netto['bars']})")
    frames = seen["lidl"]["frames"]
    check(frames > 0, f"V1 lidl showed live frames ({frames} polls)")
    rewe = by.get("rewe")
    check(rewe is not None and rewe["state"] not in ("", "Blocked", "Offers current"),
          f"V1 rewe blocked with a reason ({rewe and rewe['state']!r})")
    if ENV.get("FORCE_HEAL", "").startswith("lidl:"):
        lidl = by.get("lidl", {})
        check(bool(lidl.get("heals")) and bool(lidl.get("healed")),
              f"V1 lidl forced heal visible and ok ({lidl.get('heal_steps')})")
    ticks = await page.locator(".tick").count()
    check(ticks > 0, f"V1 ticker shows events ({ticks})")


async def tree(http: httpx.AsyncClient, **params: str) -> Counter[str]:
    r = await http.get(LANGFUSE, auth=AUTH,
                       params={"limit": "1000", "fields": "core,basic,usage", **params})
    obs = r.json().get("data", []) if r.status_code == 200 else []
    kinds = Counter(f"{o['type']}:{o['name']}" for o in obs)
    kinds["tokens"] = sum((o.get("usageDetails") or {}).get("total", 0) for o in obs)
    return kinds


async def locate(page: Page, base: str) -> None:
    await page.goto(base)
    await page.get_by_label("Address or postcode").fill("80331")
    await page.get_by_role("button", name="Set location").click()
    await page.get_by_text("OpenStreetMap contributors").first.wait_for(timeout=30_000)


async def v2(page: Page, http: httpx.AsyncClient, spec: str) -> None:
    is_list = spec.startswith("list:")
    text = spec.removeprefix("list:")
    print(f"  input: {spec!r}")
    await page.get_by_role("button", name="Plan", exact=True).click()
    await page.get_by_text("Shopping list" if is_list else "Recipe", exact=True).click()
    await page.locator("form.command textarea").fill(text)
    thought = 0
    if is_list:
        await page.get_by_role("button", name="Make items").click()
    else:
        await page.get_by_label("Servings").fill("4")
        t0 = time.monotonic()
        await page.get_by_role("button", name="Get ingredients").click()
        think = page.locator("details.think p")
        with Timer(f"V2 {text}: first thinking text"):
            await page.wait_for_function(
                "() => (document.querySelector('details.think p')?.textContent ?? '').length > 0",
                timeout=180_000)
        with Timer(f"V2 {text}: thinking to chips"):
            await page.locator(".item-chip").first.wait_for(timeout=900_000)
        timings[f"V2 {text}: recipe total"] = round(time.monotonic() - t0, 1)
        summary = await page.locator("details.think summary").inner_text()
        thought = int(re.sub(r"\D", "", summary) or 0)
        chars = len(await think.text_content() or "")
        print(f"  thinking: {chars} chars, {summary!r}")
        check(thought <= 90, f"V2 {text}: thinking bounded ({thought} s)")
    chips = await page.locator(".item-chip input:not([type=number])").evaluate_all(
        "els => els.map(e => e.value)")
    print(f"  chips: {chips}")
    check(len(chips) >= 3, f"V2 {text}: ingredient chips ({len(chips)})")
    with Timer(f"V2 {text}: basket to plan cards"):
        await page.get_by_role("button", name="Find the cheapest plan").click()
        await page.get_by_text("Prices for").wait_for(timeout=600_000)
    plans = page.locator("article.plan").filter(visible=True)
    titles = await plans.locator("h3").all_inner_texts()
    totals = await plans.locator(".total").all_inner_texts()
    saves = await page.locator(".save-badge").filter(visible=True).all_inner_texts()
    matched = await page.locator(".activity li.matched").count()
    activity = await page.locator(".activity li").all_inner_texts()
    print(f"  plans: {list(zip(titles, totals, strict=True))} badges: {saves}")
    print(f"  activity: {[' '.join(a.split()) for a in activity]}")
    check(await plans.count() >= 1, f"V2 {text}: plan cards shown ({await plans.count()})")
    waiting = await page.locator(".agent-grid .agent").filter(visible=True).count()
    if waiting:
        print(f"  note: the plan view started a scan for {waiting} chains")
    href = await page.locator(".shop-day a.trace-link").get_attribute("href") or ""
    kinds = await tree(http, traceId=href.rsplit("/", 1)[-1]) if href else Counter()
    print(f"  basket trace {href}: {dict(kinds)}")
    check(kinds["TOOL:search_offers"] > 0, f"V2 {text}: basket 'Open trace' resolves in Langfuse")
    why = page.locator("details.why").filter(visible=True)
    check(await why.count() > 0, f"V2 {text}: 'Why this match' present ({await why.count()})")
    if await why.count():
        await why.first.locator("summary").click()
        steps = await why.first.locator("ol.trace li").all_inner_texts()
        print(f"  why #1: {[' '.join(s.split())[:120] for s in steps]}")
        check(len(steps) > 0, f"V2 {text}: 'Why this match' lists agent steps ({len(steps)})")
    picks.append({"input": spec, "matched": matched, "of": len(activity), "thought_s": thought,
                  "plans": dict(zip(titles, totals, strict=True)), "saves": saves})


async def v3(page: Page, http: httpx.AsyncClient) -> None:
    await page.get_by_role("button", name="Runs", exact=True).click()
    await page.locator("table.runs tbody tr").first.wait_for(timeout=30_000)
    rows = await page.locator("table.runs tbody tr:not(.expand)").count()
    hrefs = await page.locator("a.trace-link").evaluate_all("els => els.map(e => e.href)")
    metrics = await page.get_by_role("link", name="Live metrics").get_attribute("href") or ""
    print(f"  rows: {rows}, trace links: {len(hrefs)}, metrics: {metrics}")
    check(bool(hrefs), "V3 runs show 'Open trace' links")
    check(bool(re.search(r":28737/d/gda-vllm", metrics)), "V3 'Live metrics' links the dashboard")
    lidl = page.locator("table.runs tbody tr:not(.expand)").filter(
        has=page.locator('.chain-mark[title^="Lidl"]')).first
    await lidl.get_by_role("button", name=re.compile("Show")).click()
    heals = await page.locator("table.runs tr.expand ol.trace li").all_inner_texts()
    print(f"  lidl heal steps: {[' '.join(h.split())[:120] for h in heals]}")
    check(any(" ok " in f" {h} " for h in heals),
          f"V3 runs view shows the lidl heal ({len(heals)} steps)")
    missing, generations = [], 0
    with Timer("V3 Langfuse lookups"):
        for href in dict.fromkeys(hrefs):
            url = urlparse(href)
            check(url.port == 28736 and "/project/grocery-deals-agent/traces/" in url.path,
                  f"V3 trace link shape {url.netloc}{url.path}")
            tid = url.path.rsplit("/", 1)[-1]
            kinds = await tree(http, traceId=tid)
            if kinds.total() == kinds["tokens"]:
                missing.append(tid)
            generations += kinds["GENERATION:ChatCompletion"] * bool(kinds["tokens"])
            print(f"    trace {tid}: {dict(kinds)}")
        basket = await http.get(LANGFUSE, auth=AUTH, params={"name": "basket", "limit": "1"})
        bid = (basket.json().get("data") or [{}])[0].get("traceId")
        kinds = await tree(http, traceId=bid) if bid else Counter()
        print(f"    newest basket trace {bid}: {dict(kinds)}")
    check(not missing, f"V3 every linked trace exists in Langfuse (missing {missing})")
    check(generations > 0, f"V3 linked scan traces hold generations with tokens ({generations})")
    check(kinds["TOOL:search_offers"] > 0 and kinds["GENERATION:ChatCompletion"] > 0,
          "V3 basket trace holds item agents, tool calls and LLM generations")
    page_ok = await http.get(hrefs[0].replace(urlparse(hrefs[0]).hostname or "", "127.0.0.1"))
    print(f"  Langfuse trace page HTTP {page_ok.status_code}")
    dash = (await http.get("http://127.0.0.1:28737/api/dashboards/uid/gda-vllm")).json()
    empty = []
    with Timer("V3 Grafana panel queries"):
        for panel in dash["dashboard"]["panels"]:
            for target in panel.get("targets", []):
                r = await http.get(
                    "http://127.0.0.1:28737/api/datasources/proxy/uid/prometheus/api/v1/query",
                    params={"query": target["expr"].replace("$__range", "1h")})
                res = r.json().get("data", {}).get("result", [])
                vals = [x["value"][1] for x in res]
                print(f"    {panel['title']}: {len(res)} series {vals[:4]}")
                if not res:
                    empty.append(panel["title"])
    check(not [p for p in empty if "GPU" in p], "V3 GPU panels have series")
    check("Generation tokens/s" not in empty and "Running requests" not in empty,
          f"V3 vLLM panels have series (empty: {empty})")
    check(not [p for p in empty if "nergy" in p], f"V3 energy panels have series (empty: {empty})")


async def v4(page: Page) -> None:
    with Timer("V4 evals tab"):
        await page.get_by_role("button", name="Evals", exact=True).click()
        await page.locator("[aria-labelledby=evals-h] :is(.eval-suite, .empty)").first.wait_for(
            timeout=30_000)
    suites = page.locator(".eval-suite").filter(visible=True)
    for i in range(await suites.count()):
        cap = await suites.nth(i).locator("caption").inner_text()
        heads = await suites.nth(i).locator("thead th").all_inner_texts()
        cells = await suites.nth(i).locator("tbody tr").first.locator("th, td").all_inner_texts()
        print(f"  {cap}: {dict(zip(heads, cells, strict=False))}")
        scores = {h.lower() for h in heads} - {"model", "items"}
        check(bool(scores), f"V4 {cap} shows score columns ({heads})")
        models = await suites.nth(i).locator("tbody th[scope=row]").all_inner_texts()
        check(len(set(models)) >= 2, f"V4 {cap} shows two models ({models})")
    check(await suites.count() >= 1, f"V4 evals show suites ({await suites.count()})")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Demo dry run of V1 to V4.")
    parser.add_argument("--base", default="http://127.0.0.1:28735")
    parser.add_argument("--only", default="v1,v2,v3,v4")
    parser.add_argument("--v2", action="append", help='a recipe, or "list:" and items; repeatable')
    args = parser.parse_args()
    async with async_playwright() as pw, httpx.AsyncClient(timeout=30) as http:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width": 1920, "height": 1080})
        errors = watch_console(page)
        with Timer("load + locate"):
            await locate(page, args.base)
        for name in args.only.split(","):
            for spec in (args.v2 or ["Lasagne fuer 4"]) if name == "v2" else [""]:
                print(f"== {name.upper()}")
                try:
                    if name == "v2":
                        await locate(page, args.base)  # a fresh plan per input
                        await v2(page, http, spec)
                    elif name == "v3":
                        await v3(page, http)
                    else:
                        await {"v1": v1, "v4": v4}[name](page)
                except Exception as exc:  # noqa: BLE001 - one broken video must not hide the others
                    check(False, f"{name}: {type(exc).__name__}: {str(exc).splitlines()[0]}")
        await browser.close()
    for e in errors:
        check(False, f"console error: {e[:200]}")
    for p in picks:
        print(f"== V2 {p['input']!r}: {p['matched']}/{p['of']} matched, "
              f"thought {p['thought_s']} s, {p['plans']} {p['saves']}")
    print("== timings (s)\n" + json.dumps(timings, indent=1))
    print(f"== {len(failures)} failures")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
