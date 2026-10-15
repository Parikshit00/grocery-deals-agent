"""Scan runtime orchestration: single-flight, streaming, reattach, cancel, orphan reaping.

Chain bodies are fakes here; the real chain body is exercised live by `make canary`.
"""
import asyncio
import json
import uuid
from datetime import date
from types import SimpleNamespace

import pytest

from app.agents.base import Acquired, Leaflet, Page, Store
from app.core.config import get_settings
from app.services import scan
from app.services.geo import Location


async def _collect(scan_id, after=0) -> list[dict]:
    out = []
    async for chunk in scan.stream(scan_id, after):
        if chunk.startswith("data: "):
            out.append(json.loads(chunk[6:]))
    return out


def _quick(started: list):
    def make(chain):
        async def body(run):
            started.append(chain)
            run.emit({"event": "store", "store": {"name": "X"}})
            await asyncio.sleep(0.05)
            run.emit({"event": "chain_done", "status": "ok"})
        return body
    return make


def test_identical_requests_share_one_run():
    async def go():
        started: list = []
        key = [("fake", "11111", None)]
        a = scan.start({}, key, _quick(started))
        b = scan.start({}, key, _quick(started))
        ea, eb = await asyncio.gather(_collect(a), _collect(b))
        return started, ea, eb

    started, ea, eb = asyncio.run(go())
    assert started == ["fake"]  # second request joined, no second body
    kinds = [e["event"] for e in ea]
    assert kinds == ["run", "store", "chain_done", "done"]
    assert [e["event"] for e in eb] == kinds
    assert ea[1]["seq"] == eb[1]["seq"]


def test_reattach_replays_only_newer_events():
    async def go():
        sid = scan.start({}, [("fake", "22222", None)], _quick([]))
        first = await _collect(sid)
        later = await _collect(sid, after=first[1]["seq"])
        return first, later

    first, later = asyncio.run(go())
    assert [e["event"] for e in later] == ["chain_done", "done"]
    assert later[0]["seq"] > first[1]["seq"]


def _slow(cancelled: list):
    def make(chain):
        async def body(run):
            try:
                await asyncio.sleep(30)
            except asyncio.CancelledError:
                cancelled.append(chain)
                run.emit({"event": "chain_done", "status": "cancelled"})
                raise
        return body
    return make


def test_explicit_cancel_stops_the_run():
    async def go():
        cancelled: list = []
        sid = scan.start({}, [("fake", "33333", None)], _slow(cancelled))
        consumer = asyncio.create_task(_collect(sid))
        await asyncio.sleep(0.05)
        assert scan.cancel(sid) == 1
        events = await asyncio.wait_for(consumer, 5)
        return cancelled, events

    cancelled, events = asyncio.run(go())
    assert cancelled == ["fake"]
    assert [e["event"] for e in events][-2:] == ["chain_done", "done"]


def test_last_subscriber_leaving_cancels_after_grace(monkeypatch):
    monkeypatch.setattr(scan, "DETACH_GRACE_S", 0.05)

    async def go():
        cancelled: list = []
        sid = scan.start({}, [("fake", "44444", None)], _slow(cancelled))
        gen = scan.stream(sid)
        await gen.__anext__()  # attached; got the intro event
        await gen.aclose()  # client disconnects
        await asyncio.sleep(0.3)
        return cancelled

    assert asyncio.run(go()) == ["fake"]


def test_reattach_within_grace_keeps_the_run(monkeypatch):
    monkeypatch.setattr(scan, "DETACH_GRACE_S", 0.2)

    async def go():
        cancelled: list = []
        sid = scan.start({}, [("fake", "55555", None)], _slow(cancelled))
        gen = scan.stream(sid)
        await gen.__anext__()
        await gen.aclose()
        again = scan.stream(sid, after=0)
        await again.__anext__()  # reattached before the grace period ends
        await asyncio.sleep(0.4)
        alive = not scan._groups[sid][1][0].done
        await again.aclose()
        scan.cancel(sid)
        return cancelled, alive

    cancelled, alive = asyncio.run(go())
    assert alive and cancelled in ([], ["fake"])


class _NoSession:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return None


@pytest.mark.parametrize(("slow", "reason"), [
    ("pages", None), ("acquire", "The chain did not finish within its time budget.")])
def test_budget_bounds_acquire_but_not_page_reads(monkeypatch, slow, reason):
    """CHAIN_BUDGET_S covers the network and browser phases; the VLM may take longer."""
    monkeypatch.setattr(get_settings(), "chain_budget_s", 0.05)
    store = Store(id="s1", name="S", address="A", lat=48.1, lon=11.6, edition="e1")
    leaflet = Leaflet(key="2026-W42", title="T", kind="main", valid_from=date(2026, 1, 1),
                      valid_to=date(2099, 1, 1), dates_source="feed")

    async def leaflets(ctx, store):
        return [leaflet]

    async def acquire(ctx, store, leaflet):
        await asyncio.sleep(0.2 if slow == "acquire" else 0)
        return Acquired(tier=2, pages=[Page(image_url="http://x/1.jpg")])

    async def read_pages(run, module, ctx, leaflet, pages, done_before, later):
        await asyncio.sleep(0.2 if slow == "pages" else 0)
        return [], {0}, {"tokens_in": 0, "tokens_out": 0}

    async def pick(module, ctx, radius_km, override, force):
        return store, 0.5, []

    async def nothing(*args, **kwargs):
        return None

    module = SimpleNamespace(NAME="fake", LABEL="Fake", LOYALTY=None, leaflets=leaflets,
                             acquire=acquire)
    monkeypatch.setattr(scan, "_pick_store", pick)
    monkeypatch.setattr(scan, "_read_pages", read_pages)
    monkeypatch.setattr(scan, "SessionLocal", _NoSession)
    monkeypatch.setattr(scan.repo, "get_leaflet", nothing)
    monkeypatch.setattr(scan.repo, "upsert_leaflet", nothing)
    monkeypatch.setattr(scan, "_save", nothing)
    run = scan.Run(chain="fake", plz="80331", scan_id=uuid.uuid4())
    loc = Location(lat=48.1, lon=11.6, plz="80331", label="80331")
    asyncio.run(scan._run_chain(run, module, loc, 5.0, None, False, "user", None))
    done = [e for e in run.events if e["event"] == "chain_done"]
    assert len(done) == 1 and done[0]["reason"] == reason


def test_finish_keeps_heal_actions_on_the_run_row(monkeypatch):
    """The Runs view shows ScanRun.self_heal: the heal events the chain emitted."""
    saved: list = []

    async def save(row):
        saved.append(row)

    monkeypatch.setattr(scan, "_save", save)
    run = scan.Run(chain="fake", plz="80331", scan_id=uuid.uuid4())
    run.emit({"event": "step", "step": "stores"})
    run.emit({"event": "heal", "step": "select_store", "action": "click", "ok": True,
              "forced": True})
    asyncio.run(scan._finish(run, scan.ScanRun(chain="fake"), []))
    assert saved[0].self_heal == [{"step": "select_store", "action": "click", "ok": True,
                                   "forced": True}]
    run.events.clear()
    asyncio.run(scan._finish(run, scan.ScanRun(chain="fake"), []))
    assert saved[1].self_heal is None
