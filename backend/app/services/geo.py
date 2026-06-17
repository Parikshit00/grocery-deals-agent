"""Resolve a user-supplied location (postcode or address) to a German postcode."""
from __future__ import annotations

import re

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger

log = get_logger(__name__)
_ZIP_RE = re.compile(r"\b(\d{5})\b")


async def resolve_zip(location: str) -> str | None:
    """Return a 5-digit German postcode, or None if it cannot be resolved."""
    location = location.strip()
    match = _ZIP_RE.search(location)
    if match:
        return match.group(1)

    settings = get_settings()
    async with httpx.AsyncClient(
        timeout=15.0, headers={"user-agent": "grocery-deals-agent/0.1"}
    ) as client:
        try:
            r = await client.get(
                f"{settings.nominatim_base_url}/search",
                params={
                    "q": location,
                    "format": "jsonv2",
                    "addressdetails": 1,
                    "countrycodes": "de",
                    "limit": 1,
                },
            )
            r.raise_for_status()
            data = r.json()
        except httpx.HTTPError as exc:
            log.warning("geo.nominatim_error", error=str(exc))
            return None

    if data:
        return data[0].get("address", {}).get("postcode")
    return None
