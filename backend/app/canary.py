"""Live check of one chain against the real site.

    python -m app.canary --chain penny --plz 80331 [--radius 5] [--force]

Runs the same scan as the API (trigger=canary, recorded in scan_runs), where robots.txt is
enforced on every request and top-level browser navigation (agents/robots.py). Prints the events,
then each host's robots.txt status (its answer, e.g. 200, 404, 503 or ConnectError) and every
refused URL, and exits non-zero unless the chain ends ok, not_covered or blocked (outcomes the
README documents) with no robots.txt refusal: a robots block fails the canary although "blocked"
is otherwise an expected status. Geocoding is exempt from robots.txt by design (an API client
under the Nominatim usage policy), so it uses a plain http_client().
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

from app.agents import CHAINS, robots
from app.agents.base import http_client
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services import scan
from app.services.geo import geocode

_EXPECTED = {"ok", "not_covered", "blocked"}


def _line(event: dict) -> str:
    kind = event.get("event")
    if kind == "store":
        s = event["store"]
        return (f"store   {s.get('name')} {s.get('address')} km={s.get('km')}"
                f" edition={s.get('edition')}")
    if kind == "leaflet":
        return (f"leaflet {event['key']} {event['status']} offers={event['offers']}"
                f" tier={event['tier']} {event['valid_from']}..{event['valid_to']}"
                f" ({event['dates_source']})")
    if kind == "chain_done":
        return f"done    {event['status']} {event.get('reason') or ''}"
    return f"{kind:<7} " + json.dumps({k: v for k, v in event.items() if k not in ('seq', 'event')},
                                      default=str)


async def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--chain", required=True, choices=sorted(CHAINS))
    ap.add_argument("--plz", required=True)
    ap.add_argument("--radius", type=float, default=5.0)
    ap.add_argument("--force", action="store_true", help="ignore cached stores and leaflets")
    args = ap.parse_args(argv)
    configure_logging()
    settings = get_settings()

    # Geocoder exemption: a plain client, never robots.enforced() (Nominatim usage policy).
    async with http_client(settings.contact) as http:
        try:
            location = await geocode(args.plz, http)
        except httpx.HTTPError as exc:
            print(f"geocoding unavailable: {exc}")
            return 2
    if location is None:
        print(f"could not geocode {args.plz}")
        return 2
    scan_id = scan.start(
        {"location": vars(location)}, [(args.chain, location.plz, None)],
        lambda chain: scan.chain_body(CHAINS[chain], location, args.radius, None, args.force,
                                      trigger="canary"),
    )
    status = None
    async for chunk in scan.stream(scan_id):
        if chunk.startswith("data: "):
            event = json.loads(chunk[6:])
            print(_line(event))
            if event.get("event") == "chain_done":
                status = event["status"]
    cache = robots.CACHE
    print(f"robots  {len(cache.entries)} hosts, {len(cache.refused)} refused requests")
    for (scheme, host, port), (_expires, rules) in sorted(cache.entries.items()):
        print(f"        {scheme}://{host}:{port} robots.txt {rules.source}")
    for url in cache.refused:
        print(f"        refused: {url}")
    return 0 if status in _EXPECTED and not cache.refused else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
