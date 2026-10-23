"""Geocode a user location (PLZ, address or place) and rank stores by distance from it.

Nominatim usage policy: callers pass the identifying base.http_client(settings.contact); requests
are spaced at least 1 s apart process-wide; results are cached in memory (misses briefly).
"""
from __future__ import annotations

import asyncio
import math
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import httpx

from app.agents.base import Store
from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
_PLZ = re.compile(r"\b\d{5}\b")
_MISS_TTL_S = 600.0
_EARTH_KM = 6371.0088  # mean Earth radius


@dataclass(frozen=True)
class Location:
    lat: float
    lon: float
    plz: str
    label: str


# NOTE: unbounded in-process cache (normalized query -> (location, expiry)); fine for a
# single-operator deployment, use a bounded LRU if exposed publicly.
_cache: dict[str, tuple[Location | None, float]] = {}
_lock = asyncio.Lock()
_last = -math.inf  # monotonic time the previous Nominatim request finished


async def _get(client: httpx.AsyncClient, path: str, params: dict[str, Any]) -> Any:
    """One Nominatim GET, at least 1 s after the previous one ended; the caller holds _lock."""
    global _last
    wait = _last + 1.0 - time.monotonic()
    if wait > 0:
        await asyncio.sleep(wait)
    try:
        r = await client.get(
            f"{get_settings().nominatim_base_url}/{path}",
            params={**params, "format": "jsonv2", "addressdetails": 1},
        )
    finally:
        _last = time.monotonic()
    r.raise_for_status()
    return r.json()


def _plz(address: dict[str, str]) -> str | None:
    m = _PLZ.search(address.get("postcode", ""))
    return m.group() if m else None


def _label(address: dict[str, str], plz: str) -> str:
    place = address.get("city") or address.get("town") or address.get("village") or ""
    street = " ".join(filter(None, (address.get("road"), address.get("house_number"))))
    return ", ".join(filter(None, (street, f"{plz} {place}".strip())))


async def _resolve(q: str, client: httpx.AsyncClient) -> Location | None:
    digits = re.fullmatch(r"[0-9]+", q) is not None
    if not q or (digits and not (len(q) == 5 and 1001 <= int(q) <= 99998)):
        return None  # blank, or not a German PLZ (a bare number names no place)
    query = {"postalcode": q} if digits else {"q": q}
    hits = await _get(client, "search", {**query, "countrycodes": "de", "limit": 1})
    if not hits:
        return None
    lat, lon, address = float(hits[0]["lat"]), float(hits[0]["lon"]), hits[0].get("address", {})
    plz = q if digits else _plz(address)
    if plz is None:
        plz = _plz((await _get(client, "reverse", {"lat": lat, "lon": lon})).get("address", {}))
    return Location(lat, lon, plz, _label(address, plz)) if plz else None


async def geocode(query: str, client: httpx.AsyncClient) -> Location | None:
    """Resolve a German PLZ (its centroid), address or place name; None if nothing resolves.

    Nominatim failures (outage, 429, timeout) raise httpx.HTTPError and are never cached, so
    callers can tell "unavailable" from "not found".
    """
    key = " ".join(query.lower().split())
    entry = _cache.get(key)
    if entry and entry[1] > time.monotonic():
        return entry[0]
    async with _lock:  # one lookup at a time; a waiter on the same query reuses its result
        entry = _cache.get(key)
        if entry and entry[1] > time.monotonic():
            return entry[0]
        loc = await _resolve(key, client)  # an HTTPError skips the cache write below
        _cache[key] = (loc, math.inf if loc else time.monotonic() + _MISS_TTL_S)
    return loc


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 2 * _EARTH_KM * math.asin(math.sqrt(a))


def nearest(
    stores: Iterable[Store], lat: float, lon: float, radius_km: float, limit: int = 10
) -> list[tuple[Store, float]]:
    """Located stores within radius_km, nearest first, with km rounded to 0.1."""
    hits = [
        (s, haversine_km(lat, lon, s.lat, s.lon))
        for s in stores
        if s.located and s.lat is not None and s.lon is not None
    ]
    hits.sort(key=lambda h: h[1])
    return [(s, round(km, 1)) for s, km in hits if km <= radius_km][:limit]
