"""Live check of one chain against the real site.

    python -m app.canary --chain penny --plz 80331 [--radius 5] [--force]

Runs the same scan as the API (trigger=canary, recorded in scan_runs), checks every URL the
chain module requests against that host's robots.txt, prints the events, and exits non-zero
unless the chain ends ok, not_covered or blocked (outcomes the README documents) with no
robots violation.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.robotparser

import httpx

from app.agents import CHAINS
from app.agents.base import http_client
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.services import scan
from app.services.geo import geocode

_EXPECTED = {"ok", "not_covered", "blocked"}
_AGENT = "grocery-deals-agent"


class RobotsCheck:
    """httpx request hook: records requested URLs that the host's robots.txt disallows."""

    def __init__(self, contact: str) -> None:
        self.contact = contact
        self.parsers: dict[str, urllib.robotparser.RobotFileParser] = {}
        self.unreadable: set[str] = set()
        self.disallowed: list[str] = []

    async def __call__(self, request: httpx.Request) -> None:
        host = f"{request.url.scheme}://{request.url.host}"
        if host not in self.parsers:
            parser = urllib.robotparser.RobotFileParser()
            lines: list[str] = []
            async with http_client(self.contact) as http:
                try:
                    resp = await http.get(f"{host}/robots.txt")
                    if resp.status_code == 200:
                        lines = resp.text.splitlines()
                    elif resp.status_code in (401, 403):
                        self.unreadable.add(host)  # blocked for us; recorded, not guessed
                except httpx.HTTPError:
                    self.unreadable.add(host)
            parser.parse(lines)
            self.parsers[host] = parser
        if not self.parsers[host].can_fetch(_AGENT, str(request.url)):
            self.disallowed.append(str(request.url))


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

    async with http_client(settings.contact) as http:
        try:
            location = await geocode(args.plz, http)
        except httpx.HTTPError as exc:
            print(f"geocoding unavailable: {exc}")
            return 2
    if location is None:
        print(f"could not geocode {args.plz}")
        return 2
    robots = RobotsCheck(settings.contact)
    scan_id = scan.start(
        {"location": vars(location)}, [(args.chain, location.plz, None)],
        lambda chain: scan.chain_body(CHAINS[chain], location, args.radius, None, args.force,
                                      trigger="canary", event_hooks={"request": [robots]}),
    )
    status = None
    async for chunk in scan.stream(scan_id):
        if chunk.startswith("data: "):
            event = json.loads(chunk[6:])
            print(_line(event))
            if event.get("event") == "chain_done":
                status = event["status"]
    print(f"robots  {len(robots.parsers)} hosts, {len(robots.disallowed)} disallowed requests"
          + (f", unreadable: {', '.join(sorted(robots.unreadable))}" if robots.unreadable else ""))
    for url in robots.disallowed[:10]:
        print(f"        disallowed: {url}")
    return 0 if status in _EXPECTED and not robots.disallowed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
