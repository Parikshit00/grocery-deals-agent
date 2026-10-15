"""Geocoding against a mocked Nominatim, plus distance ranking of stores."""
from __future__ import annotations

import asyncio
import math
import time

import httpx
import pytest

from app.agents.base import Store
from app.services import geo

_real_sleep = asyncio.sleep  # captured before the fixture patches it

# Live Nominatim jsonv2 responses recorded 2026-10-12 (licence, importance, boundingbox trimmed).
# /search?postalcode=80331&countrycodes=de&limit=1&addressdetails=1
PLZ_80331 = [{
    "place_id": 378736829, "osm_type": "relation", "osm_id": 1100802,
    "lat": "48.1359146", "lon": "11.5730984", "category": "boundary", "type": "postal_code",
    "place_rank": 21, "addresstype": "postcode", "name": "80331",
    "display_name": "80331, Altstadt-Lehel, München, Bayern, Deutschland",
    "address": {"postcode": "80331", "suburb": "Altstadt-Lehel", "city": "München",
                "state": "Bayern", "ISO3166-2-lvl4": "DE-BY", "country": "Deutschland",
                "country_code": "de"},
}]
# /search?q=marienplatz 1, münchen&countrycodes=de&limit=1&addressdetails=1
MARIENPLATZ_1 = [{
    "place_id": 127722801, "osm_type": "way", "osm_id": 85765727,
    "lat": "48.1374990", "lon": "11.5747545", "category": "building", "type": "apartments",
    "place_rank": 30, "addresstype": "building", "name": "Thomass-Eck",
    "display_name": "Thomass-Eck, 1, Marienplatz, Kreuzviertel, Altstadt-Lehel, München, Bayern,"
                    " 80331, Deutschland",
    "address": {"building": "Thomass-Eck", "house_number": "1", "road": "Marienplatz",
                "quarter": "Kreuzviertel", "suburb": "Altstadt-Lehel", "city": "München",
                "state": "Bayern", "ISO3166-2-lvl4": "DE-BY", "postcode": "80331",
                "country": "Deutschland", "country_code": "de"},
}]
# /search?q=81669 münchen&countrycodes=de&limit=1&addressdetails=1
PLZ_CITY_81669 = [{
    "place_id": 378736929, "osm_type": "relation", "osm_id": 1102659,
    "lat": "48.1201752", "lon": "11.6010871", "category": "boundary", "type": "postal_code",
    "place_rank": 21, "addresstype": "postcode", "name": "81669",
    "display_name": "81669, Ramersdorf-Perlach, München, Bayern, Deutschland",
    "address": {"postcode": "81669", "city_district": "Ramersdorf-Perlach", "city": "München",
                "state": "Bayern", "ISO3166-2-lvl4": "DE-BY", "country": "Deutschland",
                "country_code": "de"},
}]
# /search?q=münchen&countrycodes=de&limit=1&addressdetails=1 (a city hit carries no postcode)
MUENCHEN = [{
    "place_id": 127720233, "osm_type": "relation", "osm_id": 62428,
    "lat": "48.1371079", "lon": "11.5753822", "category": "boundary", "type": "administrative",
    "place_rank": 12, "addresstype": "city", "name": "München",
    "display_name": "München, Bayern, Deutschland",
    "address": {"city": "München", "state": "Bayern", "ISO3166-2-lvl4": "DE-BY",
                "country": "Deutschland", "country_code": "de"},
}]
# /reverse?lat=48.1371079&lon=11.5753822&addressdetails=1
REVERSE = {
    "place_id": 128160149, "osm_type": "node", "osm_id": 2758352701,
    "lat": "48.1370884", "lon": "11.5754277", "category": "amenity", "type": "bench",
    "place_rank": 30, "addresstype": "amenity", "name": "",
    "display_name": "Marienplatz, Kreuzviertel, Altstadt-Lehel, München, Bayern, 80331,"
                    " Deutschland",
    "address": {"road": "Marienplatz", "quarter": "Kreuzviertel", "suburb": "Altstadt-Lehel",
                "city": "München", "state": "Bayern", "ISO3166-2-lvl4": "DE-BY",
                "postcode": "80331", "country": "Deutschland", "country_code": "de"},
}
# /reverse?lat=55.0&lon=4.0&addressdetails=1 (open sea; the status is 200)
REVERSE_MISS = {"error": "Unable to geocode"}
# /search?postalcode=11111&countrycodes=de&limit=1&addressdetails=1 (in range, no such PLZ)
NO_HITS: list[dict] = []


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Empty cache, new lock, no previous request; sleep only advances a virtual clock."""
    monkeypatch.setattr(geo, "_cache", {})
    monkeypatch.setattr(geo, "_lock", asyncio.Lock())  # a contended Lock binds to its loop
    monkeypatch.setattr(geo, "_last", -math.inf)
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(geo.asyncio, "sleep", sleep)
    return slept


def run(handler, *queries):
    """Geocode queries concurrently against a mocked Nominatim; return results and requests."""
    seen: list[httpx.Request] = []

    def record(request: httpx.Request):
        seen.append(request)
        return handler(request)  # a Response, or a coroutine that MockTransport awaits

    async def main():
        async with httpx.AsyncClient(transport=httpx.MockTransport(record)) as client:
            return await asyncio.gather(*(geo.geocode(q, client) for q in queries))

    return asyncio.run(main()), seen


def test_plz_uses_structured_search():
    (loc,), seen = run(lambda r: httpx.Response(200, json=PLZ_80331), "80331")
    assert loc == geo.Location(48.1359146, 11.5730984, "80331", "80331 München")
    assert [r.url.path for r in seen] == ["/search"]
    assert dict(seen[0].url.params) == {
        "postalcode": "80331", "countrycodes": "de", "limit": "1", "format": "jsonv2",
        "addressdetails": "1"}


@pytest.mark.parametrize(("query", "hits", "q", "loc"), [
    ("  Marienplatz 1,   München ", MARIENPLATZ_1, "marienplatz 1, münchen",
     geo.Location(48.137499, 11.5747545, "80331", "Marienplatz 1, 80331 München")),
    ("81669 München", PLZ_CITY_81669, "81669 münchen",
     geo.Location(48.1201752, 11.6010871, "81669", "81669 München")),
])
def test_address_or_plz_with_city_uses_free_text(query, hits, q, loc):
    results, seen = run(lambda r: httpx.Response(200, json=hits), query)
    assert results == [loc]
    assert [r.url.params["q"] for r in seen] == [q]
    assert "postalcode" not in seen[0].url.params


def test_missing_postcode_reverse_geocodes():
    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=MUENCHEN if r.url.path == "/search" else REVERSE)

    (loc,), seen = run(handler, "München")
    assert loc == geo.Location(48.1371079, 11.5753822, "80331", "80331 München")
    assert [r.url.path for r in seen] == ["/search", "/reverse"]
    assert seen[1].url.params["lat"] == "48.1371079"
    assert seen[1].url.params["lon"] == "11.5753822"


def test_reverse_without_postcode_is_none():
    def handler(r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=MUENCHEN if r.url.path == "/search" else REVERSE_MISS)

    assert run(handler, "München")[0] == [None]


def test_invalid_plz_or_blank_makes_no_request():
    results, seen = run(lambda r: httpx.Response(200, json=PLZ_80331),
                        "00999", "1234", "99999", "   ")
    assert results == [None] * 4
    assert seen == []


def test_cache_hit_makes_no_second_request():
    results, seen = run(lambda r: httpx.Response(200, json=PLZ_80331), "80331", " 80331 ")
    assert results[0] == results[1] is not None
    assert len(seen) == 1


def test_miss_is_cached_briefly():
    results, seen = run(lambda r: httpx.Response(200, json=NO_HITS), "11111", " 11111 ")
    assert results == [None, None]
    assert len(seen) == 1
    expiry = geo._cache["11111"][1]
    assert time.monotonic() < expiry < time.monotonic() + 3600


def test_http_error_raises_and_is_not_cached():
    for _ in range(2):  # a failure must not stick: the second lookup asks Nominatim again
        with pytest.raises(httpx.HTTPStatusError):
            run(lambda r: httpx.Response(503), "80331")
    (loc,), _ = run(lambda r: httpx.Response(200, json=PLZ_80331), "80331")
    assert loc is not None


def test_concurrent_lookups_are_spaced_and_single_flighted(fresh):
    clock: list[float] = []

    async def handler(r: httpx.Request) -> httpx.Response:
        clock.append(time.monotonic() + sum(fresh))  # virtual time: real + slept
        await _real_sleep(0.01)  # yield: requests the lock failed to serialize would overlap
        return httpx.Response(200, json=PLZ_80331)

    results, seen = run(handler, "80331", "80333", "80331")
    assert results[0] == results[2] is not None
    assert [r.url.params["postalcode"] for r in seen] == ["80331", "80333"]
    assert clock[1] - clock[0] >= 1.0


def test_haversine_munich_marienplatz_to_hamburg_rathaus():
    km = geo.haversine_km(48.137154, 11.576124, 53.550341, 9.992196)
    assert km == pytest.approx(612, rel=0.01)


def test_nearest_filters_sorts_and_limits():
    def store(name, lat, lon, located=True):
        return Store(name, name, "", lat, lon, "e", located)

    far, mid, near = store("far", 48.2, 11.5755), store("mid", 48.15, 11.5755), store(
        "near", 48.14, 11.5755)
    stores = [far, mid, store("unlocated", 48.1374, 11.5755, False), store("nopos", None, None),
              near]
    assert geo.nearest(stores, 48.1374, 11.5755, radius_km=5) == [(near, 0.3), (mid, 1.4)]
    assert geo.nearest(stores, 48.1374, 11.5755, radius_km=5, limit=1) == [(near, 0.3)]
    assert geo.nearest(stores, 48.1374, 11.5755, radius_km=10)[-1] == (far, 7.0)
