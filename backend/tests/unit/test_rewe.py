"""REWE tier 3 against the real market search answer for 80331 recorded 2026-10-15 and the
Cloudflare challenge every www.rewe.de page returned that day (trimmed to its markers)."""
from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Iterator
from dataclasses import replace
from datetime import date
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from app.agents import rewe
from app.agents.base import Blocked, ScanCtx

FIX = Path(__file__).parents[1] / "fixtures" / "rewe"
TODAY = date(2026, 10, 15)  # Thursday of 2026-W42


def _ctx(handler, events: list | None = None) -> ScanCtx:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    emit = events.append if events is not None else (lambda e: None)
    return ScanCtx(http=http, plz="80331", lat=48.137, lon=11.575, today=TODAY, emit=emit)


def test_markets_with_positions_and_offers_urls():
    body = (FIX / "wksmarketsearch_80331.json").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/wksmarketsearch"
        assert request.url.params["searchTerm"] == "80331"
        return httpx.Response(200, text=body, headers={"content-type": "application/json"})

    ctx = _ctx(handler)
    found = asyncio.run(rewe.stores(ctx))
    assert len(found) == 7
    first = found[0]
    assert (first.id, first.edition) == ("562368", "562368")
    assert first.address == "Sendlinger Straße 46, 80331 München"
    assert (first.lat, first.lon) == (48.13479, 11.56891)
    (lf,) = asyncio.run(rewe.leaflets(ctx, first))
    assert lf.key == "2026-W42-562368" and lf.kind == "offers" and lf.dates_source == "derived"
    assert (lf.valid_from, lf.valid_to) == (date(2026, 10, 12), date(2026, 10, 17))
    assert lf.source_url == (
        "https://www.rewe.de/angebote/muenchen/562368/rewe-markt-sendlinger-strasse-46/")


def test_market_search_challenge_page_is_blocked():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text=(FIX / "challenge.html").read_text(encoding="utf-8"))

    with pytest.raises(Blocked):
        asyncio.run(rewe.stores(_ctx(handler)))


def test_embedded_json_offers():
    html = '<script type="application/json">' + json.dumps(
        {"offers": [{"title": "Milch", "subtitle": "1 l", "priceData": {"price": "0,99 €"}}]}
    ) + "</script>"
    (offer,) = rewe.parse_offers(html)
    assert (offer.product_name, offer.pack_text, offer.price_cents) == ("Milch", "1 l", 99)


@pytest.fixture(scope="module")
def site() -> Iterator[str]:
    handler = partial(SimpleHTTPRequestHandler, directory=str(FIX))
    handler.log_message = lambda *a: None  # type: ignore[attr-defined]
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_challenge_raises_blocked_with_live_frames(site, monkeypatch):
    monkeypatch.setattr(rewe, "DOMAINS", ("127.0.0.1",))
    monkeypatch.setattr(rewe, "CHALLENGE_WAIT_S", 2.0)
    events: list[dict] = []
    ctx = _ctx(lambda r: httpx.Response(404), events)
    store = rewe.parse_markets(
        json.loads((FIX / "wksmarketsearch_80331.json").read_text(encoding="utf-8")))[0]
    lf = asyncio.run(rewe.leaflets(ctx, store))[0]
    lf = replace(lf, source_url=f"{site}/challenge.html")
    with pytest.raises(Blocked, match="bot protection"):
        asyncio.run(rewe.acquire(ctx, store, lf))
    assert any(e["event"] == "frame" and e["image"] for e in events)
