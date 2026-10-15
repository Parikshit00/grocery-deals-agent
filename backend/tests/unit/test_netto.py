"""Netto tier 2 against trimmed real Publitas responses recorded Wednesday 2026-10-14 (KW 42):
hz42_posb and hz43_posb (spreads.json pages 1-3 plus one page with another promotion window, the
page HTML cut to its title and the JSON fragment naming the PDF) and the offline edition hz42_posn
(its title and body)."""
from __future__ import annotations

import asyncio
import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from app.agents import netto
from app.agents.base import Blocked, ScanCtx, StepFailed, iso_key

FIX = Path(__file__).parents[1] / "fixtures" / "netto"
MONDAY = date(2026, 10, 12)  # KW 42
TODAY = date(2026, 10, 14)
STORE = asyncio.run(netto.stores(None))[0]  # type: ignore[arg-type]


def _spreads(name: str) -> list[dict]:
    return json.loads((FIX / f"{name}.spreads.json").read_text(encoding="utf-8"))


def _texts(name: str) -> list[str]:
    return [p["text"] for s in _spreads(name) for p in s["pages"]]


def test_week_slug_and_key_across_new_year():
    assert netto.slug(MONDAY) == "hz42_posb"
    dec = date(2026, 12, 28)
    jan = date(2027, 1, 4)
    assert (netto.slug(dec), iso_key(dec)) == ("hz53_posb", "2026-W53")
    assert (netto.slug(jan), iso_key(jan)) == ("hz01_posb", "2027-W01")
    assert netto.slug(date(2026, 1, 1)) == "hz01_posb"  # Thursday of 2026-W01


def test_store_is_the_edition_without_a_position():
    assert (STORE.id, STORE.edition, STORE.located, STORE.lat) == (None, "posb", False, None)


def test_offline_and_pdf_from_the_page():
    html = (FIX / "hz42_posb.html").read_text(encoding="utf-8")
    assert not netto.offline(html)
    assert netto.offline((FIX / "hz42_posn.html").read_text(encoding="utf-8"))
    assert netto.pdf_url(html) == (
        "https://view.publitas.com/100989/3423652/pdfs/77ff179b-69e4-4f15-9e72-50ea0e692ea4.pdf"
        "?response-content-disposition=attachment%3B+filename%2A%3DUTF-8%27%27hz42_posb.pdf")


def test_pages_pick_the_image_near_1200_to_1600_px_with_their_text():
    url = f"{netto.BASE}/hz42_posb/spreads.json"
    pages, total = netto.parse_spreads(_spreads("hz42_posb"), url)
    assert (len(pages), total) == (4, 4)
    assert pages[0].image_url == (
        "https://wochenprospekt.netto-online.de/resize/rev-95488569/"
        "biwpjj1AKkzDWnpTwbGPppE21_pXILy5sGA5gvbm5_U/fit-in/1014x1420/filters:quality(90)/"
        "production-revolution-publitas-com/100989/3423652/pages/"
        "04f116a7-8a17-443b-a27e-c6e5ee2f15a2-downscaled.jpg")
    assert all("/fit-in/1014x1420/" in p.image_url for p in pages)
    assert pages[0].text is not None and "Reinigungsmittel" in pages[0].text
    spreads = _spreads("hz42_posb")
    spreads[1]["pages"][0]["images"] = {}
    pages, total = netto.parse_spreads(spreads, url)
    assert (len(pages), total) == (3, 4)


def test_dates_from_the_text_layer():
    # Page 18 also prints a "28.09. - 01.11.2026" PAYBACK campaign: not the week's.
    assert netto.parse_dates(_texts("hz42_posb"), MONDAY, TODAY) == (
        MONDAY, date(2026, 10, 17), "text")
    # Page 55 also prints a "22.10. - 24.10.26" weekend window: the earliest start wins.
    assert netto.parse_dates(_texts("hz43_posb"), date(2026, 10, 19), TODAY) == (
        date(2026, 10, 19), date(2026, 10, 24), "text")


def test_dates_short_windows_and_weekday_names_keep_the_week():
    week = "Gültig von\n12.10.-17.10.26"
    assert netto.parse_dates(["Nur 12.10.-14.10.26", week, "Ab Donnerstag 15.10.-17.10.26"],
                             MONDAY, TODAY) == (MONDAY, date(2026, 10, 17), "text")
    # A campaign starting Monday and ending weeks later does not stretch the leaflet.
    assert netto.parse_dates(["12.10.-01.11.2026", week], MONDAY, TODAY) == (
        MONDAY, date(2026, 10, 17), "text")
    # The KW 43 cover form, seen without the "Gültig von" page.
    assert netto.parse_dates(["Montag 19.10. - Samstag 24.10.26", "22.10.-24.10.26"],
                             date(2026, 10, 19), TODAY) == (
        date(2026, 10, 19), date(2026, 10, 24), "text")


def test_dates_derived_without_print_and_none_for_another_years_leaflet():
    assert netto.parse_dates(["4.99", None], MONDAY, TODAY) == (
        MONDAY, date(2026, 10, 17), "derived")
    # hz42 of 2025 under the same slug.
    assert netto.parse_dates(["Gültig von\n13.10.-18.10.25"], MONDAY, TODAY) is None


class Publitas:
    """MockTransport handler serving the fixtures; unknown slugs 404 like Publitas; override maps
    a path to a canned response."""

    def __init__(self, offline: tuple[str, ...] = (),
                 override: dict[str, httpx.Response] | None = None):
        self.offline = offline
        self.override = override or {}
        self.paths: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.paths.append(path)
        if path in self.override:
            return self.override[path]
        name = path.split("/")[1]
        if name in self.offline:
            if path.endswith("/spreads.json"):
                return httpx.Response(404)
            return httpx.Response(200, text=(FIX / "hz42_posn.html").read_text("utf-8"))
        if not (FIX / f"{name}.html").exists():
            return httpx.Response(404, text="Oops, this publication was not found.")
        if path.endswith("/spreads.json"):
            return httpx.Response(200, json=_spreads(name))
        return httpx.Response(200, text=(FIX / f"{name}.html").read_text("utf-8"))


def _run(fn, publitas: Publitas, today: date = TODAY):
    netto._cache.clear()

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(publitas)) as http:
            return await fn(ScanCtx(http=http, plz="80331", lat=48.14, lon=11.57, today=today))

    return asyncio.run(go())


async def _scan(ctx):
    found = await netto.leaflets(ctx, STORE)
    return found, [await netto.acquire(ctx, STORE, leaflet) for leaflet in found]


def test_scan_gives_this_and_next_week_with_one_fetch_each():
    publitas = Publitas()
    found, acquired = _run(_scan, publitas)
    assert [(f.key, f.valid_from, f.valid_to, f.dates_source, f.kind) for f in found] == [
        ("2026-W42-posb", MONDAY, date(2026, 10, 17), "text", "main"),
        ("2026-W43-posb", date(2026, 10, 19), date(2026, 10, 24), "text", "main"),
    ]
    assert found[0].source_url == "https://wochenprospekt.netto-online.de/hz42_posb/"
    assert found[1].pdf_url is not None and found[1].pdf_url.endswith("hz43_posb.pdf")
    assert [(a.tier, len(a.pages), a.complete) for a in acquired] == [(2, 4, True), (2, 4, True)]
    assert publitas.paths == ["/hz42_posb", "/hz42_posb/spreads.json",
                              "/hz43_posb", "/hz43_posb/spreads.json"]


def test_offline_missing_and_ended_weeks_are_skipped():
    found = _run(lambda ctx: netto.leaflets(ctx, STORE), Publitas(offline=("hz43_posb",)))
    assert [f.key for f in found] == ["2026-W42-posb"]
    # Sunday 18.10.: the Monday to Saturday KW 42 is over; KW 43 is online.
    found = _run(lambda ctx: netto.leaflets(ctx, STORE), Publitas(), date(2026, 10, 18))
    assert [f.key for f in found] == ["2026-W43-posb"]
    with pytest.raises(StepFailed, match="hz42_posb"):
        _run(lambda ctx: netto.leaflets(ctx, STORE), Publitas(offline=("hz42_posb", "hz43_posb")))
    # Wednesday 21.10.: KW 43 is current, KW 44 not published yet (404).
    found = _run(lambda ctx: netto.leaflets(ctx, STORE), Publitas(), date(2026, 10, 21))
    assert [f.key for f in found] == ["2026-W43-posb"]


@pytest.mark.parametrize("status", [403, 429, 503])
def test_next_week_failures_keep_this_week(status):
    publitas = Publitas(override={"/hz43_posb": httpx.Response(status)})
    found = _run(lambda ctx: netto.leaflets(ctx, STORE), publitas)
    assert [f.key for f in found] == ["2026-W42-posb"]


def test_this_week_refused_is_blocked_and_unreadable_pages_fail():
    with pytest.raises(Blocked):
        _run(lambda ctx: netto.leaflets(ctx, STORE),
             Publitas(override={"/hz42_posb": httpx.Response(403)}))
    for body in ("<html>Wartung</html>", "{}"):
        broken = Publitas(override={"/hz42_posb/spreads.json": httpx.Response(200, text=body)})
        with pytest.raises(StepFailed, match="no readable page list"):
            _run(lambda ctx: netto.leaflets(ctx, STORE), broken)


def test_a_page_without_image_gives_no_pages_so_indices_never_shift():
    spreads = _spreads("hz42_posb")
    spreads[1]["pages"][0]["images"] = {}
    publitas = Publitas(override={"/hz42_posb/spreads.json": httpx.Response(200, json=spreads)})

    async def go(ctx):
        return await netto.acquire(ctx, STORE, (await netto.leaflets(ctx, STORE))[0])

    acquired = _run(go, publitas)
    assert (acquired.pages, acquired.complete) == ([], False)
