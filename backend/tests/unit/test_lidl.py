"""Lidl against its real answers for 80331 recorded 2026-10-15 (trimmed): the finder's store list
(live.api.schwarz, as the page received it), the leaflet overview of offer region 9 and five
pages of the current Aktionsprospekt with their linked products; the store flow runs on a local
stand-in finder page."""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from datetime import date
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from app.agents import lidl
from app.agents.base import Blocked, ScanCtx

FIX = Path(__file__).parents[1] / "fixtures" / "lidl"
TODAY = date(2026, 10, 15)  # Thursday of 2026-W42


def _load(name: str) -> dict:
    return json.loads((FIX / name).read_text(encoding="utf-8"))


def _ctx(handler, events: list | None = None) -> ScanCtx:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emit = events.append if events is not None else (lambda e: None)
    return ScanCtx(http=http, plz="80331", lat=48.137, lon=11.575, today=TODAY, emit=emit)


def test_stores_carry_offer_region_and_skip_incomplete():
    data = _load("stores_80331.json")
    data["items"].append({"objectNumber": "X", "address": {"latitude": 1, "longitude": 2}})
    found = lidl.parse_stores(data)
    assert [s.id for s in found] == ["DE05452", "DE05993", "DE05015", "DE06637"]
    first = found[0]
    assert first.name == "Lidl München - Schwanthaler Str."
    assert first.address == "Schwanthaler Str. 31, 80336 München"
    assert (first.lat, first.lon, first.edition) == (48.1373, 11.56004, "9")


def test_leaflets_current_and_next_main_flyer_with_feed_dates():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v4/overview"
        assert dict(request.url.params) == {
            "client_locale": "lidl/de-DE", "region_id": "9", "store_id": "0"}
        return httpx.Response(200, json=_load("overview_region9.json"))

    store = lidl.parse_stores(_load("stores_80331.json"))[0]
    out = asyncio.run(lidl.leaflets(_ctx(handler), store))
    assert [(lf.key, lf.valid_from, lf.valid_to) for lf in out] == [
        ("2026-W42-aktionsprospekt-12-10-2026-17-10-2026-102f21",
         date(2026, 10, 12), date(2026, 10, 17)),
        ("2026-W43-aktionsprospekt-19-10-2026-24-10-2026-444123",
         date(2026, 10, 19), date(2026, 10, 24)),
    ]
    assert {(lf.kind, lf.dates_source) for lf in out} == {("main", "feed")}


def test_acquire_grocery_pages_with_images_and_link_text():
    flyer = _load("flyer_page2-4-7-57-61.json")

    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {
            "flyer_identifier": "aktionsprospekt-12-10-2026-17-10-2026-102f21", "region_id": "9"}
        return httpx.Response(200, json=flyer)

    ctx = _ctx(handler)
    store = lidl.parse_stores(_load("stores_80331.json"))[0]
    leaflet = lidl.parse_overview(_load("overview_region9.json"), TODAY)[0]
    got = asyncio.run(lidl.acquire(ctx, store, leaflet))
    # page 4 links only pet goods of the online shop: left out; recipes and wine stay
    assert (got.tier, got.complete, len(got.pages)) == (2, True, 4)
    assert got.pages[0].text is None  # page 2: no keywords, no links
    assert (got.pages[1].text or "").startswith("Saison Highlight")  # page 7: recipe links
    text = got.pages[2].text or ""  # page 57: recipe links and one wine
    assert text.splitlines() == ["CEPA LEBREL Gran Reserva Rioja DOCa trocken, Rotwein 2015"]
    assert "ROKU GIN The Japanese Craft Gin 43% Vol" in (got.pages[3].text or "")
    assert all(p.image_url.startswith("https://imgproxy.leaflets.schwarz/") for p in got.pages)

    flyer["flyer"]["pages"][0]["image"] = None
    partial = asyncio.run(lidl.acquire(ctx, store, leaflet))
    assert (partial.complete, partial.pages) == (False, [])


def test_feed_403_is_blocked():
    store = lidl.parse_stores(_load("stores_80331.json"))[0]
    with pytest.raises(Blocked):
        asyncio.run(lidl.leaflets(_ctx(lambda r: httpx.Response(403)), store))


class _Finder(SimpleHTTPRequestHandler):
    """The stand-in finder plus its store API: the list for a geo box, one store by number."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, directory=str(FIX), **kw)

    def do_GET(self) -> None:  # noqa: N802 - http.server's name
        url = urlsplit(self.path)
        if url.path != "/stores-frontend/stores":
            return super().do_GET()
        items = _load("stores_80331.json")["items"]
        number = parse_qs(url.query).get("object_number")
        if number:
            items = [s for s in items if s["objectNumber"] == number[0]]
        body = json.dumps({"items": items}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a) -> None:
        pass


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Finder)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_store_flow_scripted_without_healing(site, monkeypatch):
    monkeypatch.setattr(lidl, "DOMAINS", ("127.0.0.1",))
    monkeypatch.setattr(lidl, "FINDER", f"{site}/finder.html")
    events: list[dict] = []
    found = asyncio.run(lidl.stores(_ctx(lambda r: httpx.Response(404), events)))
    assert [s.id for s in found] == ["DE05452", "DE05993", "DE05015", "DE06637"]
    assert {s.edition for s in found} == {"9"}
    assert any(e["event"] == "frame" and e["image"] for e in events)
    assert not [e for e in events if e["event"] == "heal"]
