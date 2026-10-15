"""Scan runtime.

A scan request starts (or joins) one run per chain. Runs are single-flighted by
(chain, plz, store override): every request asking for the same run shares its task and its
events. Acquisition is single-flighted again per (chain, edition, leaflet), so postcodes that
map to the same edition fetch once. A run ends when its chain is done, on explicit cancel, or
DETACH_GRACE_S after its last subscriber left (so a page reload can reattach).
"""
from __future__ import annotations

import asyncio
import base64
import functools
import itertools
import json
import time
import uuid
from collections import OrderedDict, defaultdict
from collections.abc import AsyncIterator, Callable, Coroutine
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime
from types import ModuleType
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from openinference.instrumentation import using_session
from opentelemetry import trace

from app.agents.base import (
    Blocked,
    Leaflet,
    NotCovered,
    Page,
    ScanCtx,
    StepFailed,
    Store,
    http_client,
    jpeg_thumb,
)
from app.clients.embeddings import embed_async
from app.core import tracing
from app.core.config import get_settings
from app.core.logging import get_logger
from app.persistence import repository as repo
from app.persistence.db import SessionLocal
from app.persistence.models import ScanRun
from app.schemas.offer import Offer
from app.services.geo import Location, nearest
from app.services.offers import merge_duplicates, normalize

log = get_logger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")
MAX_RADIUS_KM = 25.0
DETACH_GRACE_S = 30.0
PING_S = 15.0
_KEEP_GROUPS = 100

_seq = itertools.count(1)


def today() -> date:
    return datetime.now(BERLIN).date()


class NoStore(Exception):
    """No store of the chain within the requested radius."""


@dataclass
class Run:
    chain: str
    plz: str
    scan_id: uuid.UUID
    events: list[dict] = field(default_factory=list)
    task: asyncio.Task | None = None
    subscribers: int = 0
    reaper: asyncio.TimerHandle | None = None

    @property
    def done(self) -> bool:
        return self.task is not None and self.task.done()

    def emit(self, event: dict) -> None:
        self.events.append({"seq": next(_seq), "chain": self.chain, **event})

    def attach(self) -> None:
        self.subscribers += 1
        if self.reaper:
            self.reaper.cancel()
            self.reaper = None

    def detach(self) -> None:
        self.subscribers -= 1
        if self.subscribers <= 0 and not self.done:
            self.reaper = asyncio.get_running_loop().call_later(DETACH_GRACE_S, self.cancel)

    def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()


_runs: dict[tuple, Run] = {}
_groups: OrderedDict[uuid.UUID, tuple[dict, list[Run]]] = OrderedDict()
_acquire_locks: defaultdict[tuple, asyncio.Lock] = defaultdict(asyncio.Lock)

ChainBody = Callable[[Run], Coroutine[Any, Any, None]]


def start(
    intro: dict, keys: list[tuple[str, str, str | None]], make: Callable[[str], ChainBody]
) -> uuid.UUID:
    """Start or join one run per (chain, plz, override) key; returns the request's scan_id.

    `make(chain)` builds the body of a new run; it is not called for joined runs.
    """
    scan_id = uuid.uuid4()
    runs: list[Run] = []
    new: list[asyncio.Task] = []
    # One trace per request (Langfuse session = scan_id); new runs are its children, it ends
    # when they do. Joined runs stay in the trace of the request that started them.
    with using_session(str(scan_id)), tracing.span("scan", "chain", end=False, input=intro,
                                                   chains=[k[0] for k in keys]) as root:
        for key in keys:
            run = _runs.get(key)
            if run is None or run.done:
                run = Run(chain=key[0], plz=key[1], scan_id=scan_id)
                run.task = asyncio.create_task(make(key[0])(run))
                new.append(run.task)
                _runs[key] = run
                run.task.add_done_callback(functools.partial(_forget, key, run))
            runs.append(run)
    if new:
        asyncio.gather(*new, return_exceptions=True).add_done_callback(lambda _f: root.end())
    else:
        root.end()
    _groups[scan_id] = ({**intro, "scan_id": str(scan_id)}, runs)
    while len(_groups) > _KEEP_GROUPS:
        _groups.popitem(last=False)
    return scan_id


def _forget(key: tuple, run: Run, _task: asyncio.Task) -> None:
    if _runs.get(key) is run:
        del _runs[key]


def known(scan_id: uuid.UUID) -> bool:
    return scan_id in _groups


def cancel(scan_id: uuid.UUID) -> int:
    """Cancel every run of a request (shared runs too: an explicit cancel stops the work)."""
    runs = _groups.get(scan_id, ({}, []))[1]
    for run in runs:
        run.cancel()
    return sum(not r.done for r in runs)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


async def stream(scan_id: uuid.UUID, after: int = 0) -> AsyncIterator[str]:
    """Server-sent events of a request's runs in sequence order, from `after` on."""
    intro, runs = _groups[scan_id]
    for run in runs:
        run.attach()
    try:
        if after == 0:
            yield _sse({"event": "run", **intro})
        cursor, last_sent = after, time.monotonic()
        while True:
            finished = all(r.done for r in runs)
            new = sorted((e for r in runs for e in r.events if e["seq"] > cursor),
                         key=lambda e: e["seq"])
            for event in new:
                yield _sse(event)
                cursor = event["seq"]
            if new:
                last_sent = time.monotonic()
            elif finished:
                yield _sse({"event": "done", "scan_id": str(scan_id)})
                return
            elif time.monotonic() - last_sent > PING_S:
                yield ": ping\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(0.2)
    finally:
        for run in runs:
            run.detach()


# --- one chain run -------------------------------------------------------------------------


def chain_body(
    module: ModuleType,
    location: Location,
    radius_km: float,
    override: str | None,
    force: bool,
    trigger: str = "user",
    event_hooks: dict[str, list[Callable]] | None = None,
) -> ChainBody:
    async def body(run: Run) -> None:
        with tracing.span("chain_run", "agent", chain=module.NAME, plz=location.plz,
                          force=force, trigger=trigger):
            await _run_chain(run, module, location, radius_km, override, force, trigger,
                             event_hooks)

    return body


def _store_json(store: Store, km: float | None) -> dict:
    return {**asdict(store), "km": km}


async def _pick_store(
    module: ModuleType, ctx: ScanCtx, radius_km: float, override: str | None, force: bool
) -> tuple[Store, float | None, list[dict]]:
    """(chosen store, its km, nearby stores for the picker). Store lists are cached per PLZ."""
    cached = None
    if not force:
        async with SessionLocal() as session:
            cached = await repo.cached_stores(session, module.NAME, ctx.plz, datetime.now(UTC))
    if cached:
        stores = [Store(**{k: v for k, v in s.items() if k != "km"}) for s in cached]
    else:
        stores = await module.stores(ctx)
    near = nearest(stores, ctx.lat, ctx.lon, MAX_RADIUS_KM)
    if near:
        nearby = [_store_json(s, km) for s, km in near]
        chosen = next(((s, km) for s, km in near if s.id == override), near[0])
        if chosen[1] > radius_km and chosen[0].id != override:
            raise NoStore(f"No {module.LABEL} within {radius_km:g} km.")
        return chosen[0], chosen[1], nearby
    unlocated = [s for s in stores if not s.located]
    if unlocated:
        return unlocated[0], None, [_store_json(unlocated[0], None)]
    raise NoStore(f"No {module.LABEL} within {MAX_RADIUS_KM:g} km.")


async def _read_pages(
    run: Run, module: ModuleType, ctx: ScanCtx, leaflet: Leaflet, pages: list[Page],
    done_before: set[int], later: bool,
) -> tuple[list[Offer], set[int], dict[str, int]]:
    """Tier 2: fetch every page image not read yet and have the VLM read it with its text layer.

    Today's leaflets get priority over next week's (lower = earlier on the vLLM server).
    Returns the new offers (tagged with their page), the pages read successfully and counters.
    """
    from app.clients.vision import extract_page  # the tier-2 reader, only needed here

    total, base_priority = len(pages), (1000 if later else 0)
    ok_pages, stats = set(done_before), {"tokens_in": 0, "tokens_out": 0}

    async def one(index: int, page: Page) -> list[Offer]:
        offers: list[Offer] = []
        ok = False
        thumb = None
        with tracing.span("page_read", "chain", leaflet=leaflet.key, page=index) as sp:
            try:
                resp = await ctx.http.get(page.image_url)
                resp.raise_for_status()
                # A small preview so the UI can show the page while the VLM reads it.
                small = await asyncio.to_thread(jpeg_thumb, resp.content, 240, 60)
                thumb = base64.b64encode(small).decode()
                result = await extract_page(resp.content, page.text, chain=module.NAME,
                                            loyalty=module.LOYALTY, page=index,
                                            priority=base_priority + index)
                stats["tokens_in"] += result.tokens_in
                stats["tokens_out"] += result.tokens_out
                ok = result.ok
                offers = [o.model_copy(update={"page": index}) for o in result.offers]
                if not ok:
                    log.warning("scan.page_failed", chain=module.NAME, leaflet=leaflet.key,
                                page=index, reason=result.reason)
            except httpx.HTTPError as exc:
                log.warning("scan.page_fetch_failed", chain=module.NAME, page=index,
                            error=str(exc))
            checks = [o.price_check for o in offers]
            tracing.attrs(sp, ok=ok, offers=len(offers), price_text=checks.count("text"),
                          price_mismatch=checks.count("mismatch"),
                          price_image_only=checks.count("image_only"))
        if ok:
            ok_pages.add(index)
        run.emit({"event": "page", "leaflet": leaflet.key, "index": index, "total": total,
                  "ok": ok, "offers": len(offers), "thumb": thumb})
        return offers

    todo = [(i, p) for i, p in enumerate(pages) if i not in done_before]
    run.emit({"event": "step", "step": "pages", "leaflet": leaflet.key, "n": len(todo)})
    parts = await asyncio.gather(*(one(i, p) for i, p in todo))
    return [o for part in parts for o in part], ok_pages, stats


async def _leaflet(
    run: Run, module: ModuleType, ctx: ScanCtx, store: Store, leaflet: Leaflet, force: bool,
    later: bool = False,
) -> dict:
    """Serve a leaflet from the cache or acquire it; returns its summary."""
    with tracing.span("leaflet", "chain", leaflet=leaflet.key, title=leaflet.title,
                      later=later) as sp:
        summary = await _serve_leaflet(run, module, ctx, store, leaflet, force, later)
        tracing.attrs(sp, output=summary, status=summary["status"], offers=summary["offers"],
                      tier=summary["tier"])
        return summary


async def _serve_leaflet(
    run: Run, module: ModuleType, ctx: ScanCtx, store: Store, leaflet: Leaflet, force: bool,
    later: bool,
) -> dict:
    summary = {"key": leaflet.key, "title": leaflet.title, "kind": leaflet.kind,
               "valid_from": leaflet.valid_from, "valid_to": leaflet.valid_to,
               "dates_source": leaflet.dates_source}
    lock = _acquire_locks[(module.NAME, store.edition, leaflet.key)]
    async with lock:
        async with SessionLocal() as session:
            row = await repo.get_leaflet(session, module.NAME, store.edition, leaflet.key)
        if not force and repo.is_hit(row, ctx.today):
            assert row is not None
            run.emit({"event": "leaflet", **summary, "status": "cached",
                      "offers": len(row.offers), "tier": row.tier})
            return {**summary, "status": "complete", "offers": len(row.offers), "tier": row.tier}

        run.emit({"event": "step", "step": "acquire", "leaflet": leaflet.key})
        async with asyncio.timeout(get_settings().chain_budget_s):
            acquired = await module.acquire(ctx, store, leaflet)
        offers = list(acquired.offers)
        complete = acquired.complete
        pages_done: set[int] = set()
        stats = {"tokens_in": 0, "tokens_out": 0}
        if acquired.pages:
            # A partial leaflet keeps the pages already read; only the rest are read again.
            done_before: set[int] = set()
            if row is not None and not force and row.status == "partial":
                done_before = set(row.pages_done or [])
                offers += [Offer.model_validate(o) for o in row.offers
                           if o.get("page") in done_before]
            read, pages_done, stats = await _read_pages(
                run, module, ctx, leaflet, acquired.pages, done_before, later)
            offers += read
            complete = complete and pages_done >= set(range(len(acquired.pages)))
        offers = [normalize(o) for o in offers]
        vectors = np.zeros((0, 0), dtype=np.float32)
        if offers:
            run.emit({"event": "step", "step": "embed", "leaflet": leaflet.key, "n": len(offers)})
            vectors = await embed_async([o.product_name for o in offers])
            if acquired.tier > 1:
                # Only page reading sees one product twice (overlapping tiles, repeated
                # spreads); feed offers are unique by their id, so merging them would drop
                # real variants that share a price.
                kept_ids = {id(o) for o in merge_duplicates(offers, vectors)}
                keep = [i for i, o in enumerate(offers) if id(o) in kept_ids]
                offers, vectors = [offers[i] for i in keep], vectors[keep]
        status = "complete" if complete and offers else "partial"
        async with SessionLocal() as session:
            await repo.upsert_leaflet(session, {
                "chain": module.NAME, "edition": store.edition, "leaflet_key": leaflet.key,
                "title": leaflet.title, "kind": leaflet.kind, "valid_from": leaflet.valid_from,
                "valid_to": leaflet.valid_to, "dates_source": leaflet.dates_source,
                "source_url": leaflet.source_url, "pdf_url": leaflet.pdf_url,
                "tier": acquired.tier, "status": status,
                "page_count": len(acquired.pages) or None,
                "pages_done": sorted(pages_done),
                "offers": [o.model_dump(mode="json") for o in offers],
                "embeddings": vectors.astype(np.float16).tobytes() if offers else None,
                "fetched_at": datetime.now(UTC),
            })
    flagged = sum(o.price_check == "mismatch" for o in offers)
    run.emit({"event": "leaflet", **summary, "status": status, "offers": len(offers),
              "tier": acquired.tier})
    return {**summary, "status": status, "offers": len(offers), "tier": acquired.tier,
            "pages": len(acquired.pages), "flagged": flagged, **stats}


async def _save(row: ScanRun) -> None:
    async with SessionLocal() as session:
        await session.merge(row)
        await session.commit()


async def _run_chain(
    run: Run,
    module: ModuleType,
    location: Location,
    radius_km: float,
    override: str | None,
    force: bool,
    trigger: str,
    event_hooks: dict[str, list[Callable]] | None,
) -> None:
    settings = get_settings()
    row = ScanRun(id=uuid.uuid4(), scan_id=run.scan_id, chain=module.NAME, plz=location.plz,
                  trigger=trigger, status="failed", started_at=datetime.now(UTC),
                  trace_id=tracing.trace_id(trace.get_current_span()))
    summaries: list[dict] = []
    later: list[Leaflet] = []
    async with http_client(settings.contact) as http:
        if event_hooks:
            http.event_hooks = event_hooks
        ctx = ScanCtx(http=http, plz=location.plz, lat=location.lat, lon=location.lon,
                      today=today(), emit=run.emit)
        try:
            # The budget bounds the network and browser phases (here and each acquire); page
            # reads run under the VLM client's own timeout, so a long leaflet still finishes.
            async with asyncio.timeout(settings.chain_budget_s):
                run.emit({"event": "step", "step": "stores"})
                store, km, row.stores_nearby = await _pick_store(
                    module, ctx, radius_km, override, force)
                row.store = _store_json(store, km)
                run.emit({"event": "store", "store": row.store})
                run.emit({"event": "step", "step": "leaflets"})
                leaflets = await module.leaflets(ctx, store)
            later = [lf for lf in leaflets if lf.valid_from > ctx.today]
            for leaflet in [lf for lf in leaflets if lf.valid_from <= ctx.today]:
                summaries.append(await _leaflet(run, module, ctx, store, leaflet, force))
            if not summaries:
                row.error = "No leaflet valid today was found."
            elif all(s["status"] == "complete" for s in summaries):
                row.status = "ok"
            else:
                row.status = "partial"
        except NoStore as exc:
            row.status, row.error = "no_store", str(exc)
        except NotCovered as exc:
            row.status, row.error = "not_covered", str(exc)
        except Blocked as exc:
            row.status, row.error = "blocked", str(exc)
        except StepFailed as exc:
            row.error = str(exc)
        except TimeoutError:
            row.status = "partial" if summaries else "failed"
            row.error = "The chain did not finish within its time budget."
        except asyncio.CancelledError:
            row.status, row.error = "cancelled", "Cancelled."
            await _finish(run, row, summaries)
            raise
        except Exception as exc:  # noqa: BLE001 - one chain must never take the scan down
            log.exception("scan.chain_failed", chain=module.NAME)
            row.error = f"Unexpected error: {type(exc).__name__}"
        await _finish(run, row, summaries)

        if later and row.status in ("ok", "partial"):
            # Next week's leaflets continue after chain_done, under the same run.
            try:
                done = [await _leaflet(run, module, ctx, store, lf, force, later=True)
                        for lf in later]
                row.next_week = "ok" if all(s["status"] == "complete" for s in done) else "partial"
            except asyncio.CancelledError:
                row.next_week = "cancelled"
                await _save(row)
                raise
            except Exception as exc:  # noqa: BLE001 - next week is best effort
                log.warning("scan.next_week_failed", chain=module.NAME, error=str(exc))
                row.next_week = "failed"
            await _save(row)


async def _finish(run: Run, row: ScanRun, summaries: list[dict]) -> None:
    row.finished_at = datetime.now(UTC)
    row.tier = max((s["tier"] for s in summaries), default=None)
    row.leaflet_ids = [s["key"] for s in summaries]
    row.offers = sum(s.get("offers", 0) for s in summaries)
    row.pages = sum(s.get("pages", 0) for s in summaries)
    row.flagged = sum(s.get("flagged", 0) for s in summaries)
    row.tokens_in = sum(s.get("tokens_in", 0) for s in summaries)
    row.tokens_out = sum(s.get("tokens_out", 0) for s in summaries)
    row.self_heal = [{k: v for k, v in e.items() if k not in ("seq", "chain", "event")}
                     for e in run.events if e["event"] == "heal"] or None
    try:
        await _save(row)
    except Exception as exc:  # noqa: BLE001 - still report the outcome to the stream
        log.warning("scan.run_not_saved", chain=row.chain, error=str(exc))
    tracing.attrs(trace.get_current_span(), status=row.status, error=row.error,
                  store=(row.store or {}).get("name"), km=(row.store or {}).get("km"),
                  tier=row.tier, offers=row.offers, pages=row.pages, flagged=row.flagged,
                  output={"status": row.status, "reason": row.error, "leaflets": summaries})
    run.emit({"event": "chain_done", "status": row.status, "reason": row.error,
              "store": row.store, "leaflets": summaries})
