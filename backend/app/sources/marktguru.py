"""marktguru aggregator source.

Tier-1 offer acquisition: the marktguru JSON API aggregates weekly offers from German
retailers by postcode. The API key is not published; it is embedded in the public web
client, so it is discovered at runtime by scanning the web app's script bundles for
candidate keys and validating them against the API. The validated key is cached.
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime

import httpx

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.offer import Offer, OfferSearchResult

log = get_logger(__name__)

_BROWSER_HEADERS = {
    "user-agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "accept-language": "de-DE,de;q=0.9,en;q=0.8",
}
_KEY_TTL_SECONDS = 3600
_ENTRY_PATHS = ("/", "/suche?q=angebote")


def _key_candidates(text: str) -> set[str]:
    found: set[str] = set()
    for m in re.finditer(r'x-apikey\s*[\'"]?\s*[:=]\s*[\'"]([^\'"]{10,})[\'"]', text, re.I):
        found.add(m.group(1))
    for m in re.finditer(r'apiKey\s*[:=]\s*[\'"]([^\'"]{10,})[\'"]', text, re.I):
        found.add(m.group(1))
    for m in re.finditer(r"[A-Za-z0-9+/]{40,80}={0,2}", text):
        value = m.group(0)
        if 40 <= len(value) <= 60 and "=" in value:
            found.add(value)
    return found


def _script_urls(html: str, base: str) -> list[str]:
    urls: list[str] = []
    for m in re.finditer(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.I):
        src = m.group(1)
        if src.startswith("//"):
            src = "https:" + src
        elif src.startswith("/"):
            src = base + src
        if src.startswith("http"):
            urls.append(src)
    return urls


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _to_offer(raw: dict) -> Offer:
    advertisers = raw.get("advertisers") or []
    dates = raw.get("validityDates") or []
    product = raw.get("product") or {}
    return Offer(
        source="marktguru",
        external_id=str(raw["id"]) if raw.get("id") is not None else None,
        product_name=product.get("name") or raw.get("description") or "",
        description=raw.get("description"),
        brand=(raw.get("brand") or {}).get("name"),
        retailer=advertisers[0].get("name") if advertisers else None,
        price=raw.get("price"),
        old_price=raw.get("oldPrice"),
        unit=(raw.get("unit") or {}).get("shortName"),
        valid_from=_parse_dt(dates[0].get("from")) if dates else None,
        valid_to=_parse_dt(dates[0].get("to")) if dates else None,
    )


class MarktguruSource:
    name = "marktguru"

    def __init__(self) -> None:
        settings = get_settings()
        self._api_base = settings.marktguru_base_url
        self._web_base = settings.marktguru_web_url
        self._client = httpx.AsyncClient(timeout=20.0, follow_redirects=True)
        self._key: str | None = None
        self._key_expiry = 0.0
        self._lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _validate(self, key: str) -> bool:
        try:
            r = await self._client.get(
                f"{self._api_base}/offers/search",
                params={"as": "web", "q": "test", "limit": 1, "offset": 0, "zipCode": "10115"},
                headers={"x-apikey": key, "accept": "application/json"},
            )
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def _extract_key(self) -> str:
        candidates: set[str] = set()
        for path in _ENTRY_PATHS:
            try:
                html = (
                    await self._client.get(self._web_base + path, headers=_BROWSER_HEADERS)
                ).text
            except httpx.HTTPError:
                continue
            candidates |= _key_candidates(html)
            for url in _script_urls(html, self._web_base)[:25]:
                try:
                    candidates |= _key_candidates(
                        (await self._client.get(url, headers=_BROWSER_HEADERS)).text
                    )
                except httpx.HTTPError:
                    continue
            if candidates:
                break
        for key in candidates:
            if await self._validate(key):
                log.info("marktguru.key_extracted")
                return key
        raise RuntimeError("marktguru: could not extract a valid API key")

    async def _get_key(self, force: bool = False) -> str:
        async with self._lock:
            if not force and self._key and time.time() < self._key_expiry:
                return self._key
            self._key = await self._extract_key()
            self._key_expiry = time.time() + _KEY_TTL_SECONDS
            return self._key

    async def search(
        self, zip_code: str, query: str, limit: int = 20, offset: int = 0
    ) -> OfferSearchResult:
        params = {"as": "web", "q": query, "limit": limit, "offset": offset, "zipCode": zip_code}

        async def _call(key: str) -> httpx.Response:
            return await self._client.get(
                f"{self._api_base}/offers/search",
                params=params,
                headers={"x-apikey": key, "accept": "application/json"},
            )

        r = await _call(await self._get_key())
        if r.status_code == 401:
            r = await _call(await self._get_key(force=True))
        r.raise_for_status()
        data = r.json()
        offers = [_to_offer(o) for o in data.get("results", [])]
        return OfferSearchResult(
            query=query,
            zip_code=zip_code,
            total=data.get("totalResults", len(offers)),
            offers=offers,
        )
