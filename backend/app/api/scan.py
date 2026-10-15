"""Scan API: start or join chain scans (SSE), reattach, cancel; nearby stores; run log.

POST /api/scan {location, radius_km=5, chains?, overrides={chain: store_id}, force=false} streams
server-sent events, one JSON object per `data:` line, each with a global, increasing `seq`:

    run         {scan_id, location: {lat, lon, plz, label}, radius_km, chains}
    step        {chain, step: stores|leaflets|acquire|embed|pages, leaflet?, n?}
    store       {chain, store: {id, name, address, lat, lon, edition, located, km}}
    leaflet     {chain, key, title, kind, valid_from, valid_to, dates_source,
                 status: cached|complete|partial, offers, tier}
    page        {chain, leaflet, index, total, ok, offers, thumb} (tier 2: one per page read;
                 thumb = small base64 JPEG of the page)
    frame       {chain, image}                                    (tier 3: base64 JPEG live view)
    heal        {chain, step, action, el_text, url, ok, forced}   (tier 3: self-heal action)
    error       {scope: request|chain, chain?, code, message}
    chain_done  {chain, status, reason, store, leaflets}          (status as in scan_runs)
    done        {scan_id}

A disconnect only detaches; GET /api/scan/{id}/events?after=<seq> reattaches, and the run is
cancelled 30 s after its last subscriber left or on POST /api/scan/{id}/cancel. Lines starting
with ":" are keep-alive pings.

GET /api/offers?location=&radius_km= returns, per registered chain, the last scan outcome near the
location and the offers of its chosen store's current leaflets (or the last expired leaflet,
marked with expired_on, when nothing current exists).
"""
from __future__ import annotations

import uuid
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agents import CHAINS
from app.agents.base import http_client
from app.core.config import get_settings
from app.persistence import repository as repo
from app.persistence.db import SessionLocal
from app.services import scan
from app.services.geo import Location, geocode, haversine_km

router = APIRouter(prefix="/api", tags=["scan"])

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class ScanBody(BaseModel):
    location: str = Field(min_length=1, max_length=200)
    radius_km: float = Field(5.0, ge=0.5, le=scan.MAX_RADIUS_KM)
    chains: list[str] | None = None  # default: every registered chain
    overrides: dict[str, str] = {}  # chain -> store id picked by the user
    force: bool = False


async def _locate(query: str) -> Location:
    async with http_client(get_settings().contact) as http:
        try:
            location = await geocode(query, http)
        except httpx.HTTPError as exc:
            raise HTTPException(503, "Geocoding service unavailable, try again shortly.") from exc
    if location is None:
        raise HTTPException(422, "Location not found. Enter a German postcode or address.")
    return location


def _sse(scan_id: uuid.UUID, after: int = 0) -> StreamingResponse:
    return StreamingResponse(scan.stream(scan_id, after), media_type="text/event-stream",
                             headers=_SSE_HEADERS)


@router.post("/scan")
async def start_scan(body: ScanBody) -> StreamingResponse:
    unknown = set(body.chains or []) - set(CHAINS)
    if unknown:
        raise HTTPException(422, f"Unknown chains: {', '.join(sorted(unknown))}")
    chains = list(dict.fromkeys(body.chains or CHAINS))
    if not Path(get_settings().embedding_model).is_dir():
        raise HTTPException(503, "Embedding model missing: run make models-pull NAME=bge-m3.")
    location = await _locate(body.location)
    intro = {"location": vars(location), "radius_km": body.radius_km, "chains": chains}
    keys = [(c, location.plz, body.overrides.get(c)) for c in chains]
    scan_id = scan.start(intro, keys, lambda chain: scan.chain_body(
        CHAINS[chain], location, body.radius_km, body.overrides.get(chain), body.force))
    return _sse(scan_id)


@router.get("/scan/{scan_id}/events")
async def reattach(scan_id: uuid.UUID, after: int = Query(0, ge=0)) -> StreamingResponse:
    if not scan.known(scan_id):
        raise HTTPException(404, "Unknown or expired scan.")
    return _sse(scan_id, after)


@router.post("/scan/{scan_id}/cancel")
async def cancel_scan(scan_id: uuid.UUID) -> dict:
    if not scan.known(scan_id):
        raise HTTPException(404, "Unknown or expired scan.")
    return {"cancelling": scan.cancel(scan_id)}


@router.get("/stores")
async def stores(
    location: str = Query(min_length=1, max_length=200),
    radius_km: float = Query(5.0, ge=0.5, le=scan.MAX_RADIUS_KM),
) -> dict:
    """Per chain: last scan outcome near this postcode and its stores, with km measured from
    this exact location (a store list is cached per postcode, distances are not)."""
    loc = await _locate(location)
    async with SessionLocal() as session:
        runs = await repo.latest_runs(session, loc.plz)
    out: dict[str, dict] = {}
    for run in runs:
        nearby = []
        for s in run.stores_nearby or []:
            km = None
            if s.get("lat") is not None and s.get("lon") is not None:
                km = round(haversine_km(loc.lat, loc.lon, s["lat"], s["lon"]), 1)
                if km > radius_km:
                    continue
            nearby.append({**s, "km": km})
        out[run.chain] = {"status": run.status, "reason": run.error, "store": run.store,
                          "stores": sorted(nearby, key=lambda s: s["km"] or 0.0),
                          "finished_at": run.finished_at}
    return {"location": vars(loc), "chains": out}


def _summary(row) -> dict:
    return {"key": row.leaflet_key, "title": row.title, "kind": row.kind,
            "valid_from": row.valid_from, "valid_to": row.valid_to,
            "dates_source": row.dates_source, "status": row.status,
            "offers": len(row.offers), "tier": row.tier}


@router.get("/offers")
async def offers(
    location: str = Query(min_length=1, max_length=200),
    radius_km: float = Query(5.0, ge=0.5, le=scan.MAX_RADIUS_KM),
) -> dict:
    """Per chain: last scan outcome near this location and its chosen store's offers."""
    loc = await _locate(location)
    today = scan.today()
    chains: list[dict] = []
    async with SessionLocal() as session:
        latest = {r.chain: r for r in await repo.latest_runs(session, loc.plz)}
        for name, module in CHAINS.items():
            run = latest.get(name)
            if run is None:
                chains.append({"chain": name, "status": "needs_scan", "reason": None,
                               "store": None, "leaflets": []})
                continue
            status, reason = run.status, run.error
            store = dict(run.store) if run.store else None
            if store and store.get("lat") is not None and store.get("lon") is not None:
                store["km"] = round(haversine_km(loc.lat, loc.lon, store["lat"], store["lon"]), 1)
                if store["km"] > radius_km:
                    status, reason = "no_store", f"No {module.LABEL} within {radius_km:g} km."
            leaflets: list[dict] = []
            if store and status != "no_store":
                rows = await repo.current_leaflets(session, name, store["edition"], today)
                leaflets = [{**_summary(r), "expired_on": None, "offers": r.offers} for r in rows]
                if not leaflets:
                    last = await repo.last_expired(session, name, store["edition"], today)
                    if last is not None:
                        leaflets = [{**_summary(last), "expired_on": last.valid_to,
                                     "offers": last.offers}]
            chains.append({"chain": name, "status": status, "reason": reason, "store": store,
                           "leaflets": leaflets})
    return {"location": vars(loc), "chains": chains}


@router.get("/runs")
async def runs(limit: int = Query(50, ge=1, le=500)) -> list[dict]:
    async with SessionLocal() as session:
        rows = await repo.recent_runs(session, limit)
    return [{c.name: getattr(r, c.name) for c in r.__table__.columns} for r in rows]
