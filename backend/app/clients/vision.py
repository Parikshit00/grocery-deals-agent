"""Read prospekt page images with the local VLM -> structured offers.

Served by Qwen3-VL-32B-Instruct via vLLM's OpenAI-compatible endpoint. The VLM is the only thing
that interprets the flyer pages.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path

from openai import AsyncOpenAI
from PIL import Image

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.offer import Offer

log = get_logger(__name__)

_PROMPTS = Path(__file__).resolve().parents[1] / "agents" / "prompts"
_GENERIC_PROMPT = (
    "You are reading ONE page of a German supermarket weekly flyer. Extract every product offer. "
    'Return ONLY JSON {"offers":[{"n":string,"b":string|null,"p":number|null,'
    '"o":number|null,"u":string|null,"d":string|null}]} '
    "(n=product name, b=brand, p=price, o=old/statt price, u=unit, d=description). "
    'Prices in EUR (comma is the decimal separator). If no offers, return {"offers":[]}.'
)

# Pages are downscaled to this longest side before the VLM: Qwen3-VL costs 1 token per
# 32x32 px, and 1024px reads a flyer page as reliably as full resolution at ~3x less prompt.
_MAX_SIDE = 1024


def prompt_for(retailer: str) -> str:
    path = _PROMPTS / f"{retailer}_extract.txt"
    return path.read_text(encoding="utf-8") if path.exists() else _GENERIC_PROMPT


def _num(value) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def _parse_offers(content: str, retailer: str) -> list[Offer]:
    start, end = content.find("{"), content.rfind("}")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(content[start : end + 1])
    except json.JSONDecodeError:
        return []
    if not isinstance(data, dict):
        return []
    offers: list[Offer] = []
    for o in data.get("offers", []):
        if not isinstance(o, dict):
            continue
        # Compact keys (n/b/p/o/u/d) are the prompt schema; long keys kept as fallback.
        name = str(o.get("n") or o.get("product_name") or "").strip()
        if not name:
            continue
        offers.append(
            Offer(
                source=f"{retailer}-prospekt",
                retailer=retailer.capitalize(),
                product_name=name,
                brand=o.get("b", o.get("brand")),
                description=o.get("d", o.get("description")),
                price=_num(o.get("p", o.get("price"))),
                old_price=_num(o.get("o", o.get("old_price"))),
                unit=o.get("u", o.get("unit")),
            )
        )
    return offers


def _to_jpeg(image: bytes) -> bytes:
    """Normalize any captured page (WebP/AVIF/PNG from leaflet CDNs) to JPEG for the VLM,
    downscaled to _MAX_SIDE longest side."""
    im = Image.open(io.BytesIO(image)).convert("RGB")
    im.thumbnail((_MAX_SIDE, _MAX_SIDE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    """One shared client so all agents reuse the same connection pool."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncOpenAI(
            base_url=settings.vlm_base_url, api_key=settings.llm_api_key, max_retries=0
        )
    return _client


async def extract_offers_from_image(
    image: bytes, retailer: str, prompt: str | None = None, priority: int = 0
) -> list[Offer]:
    """`priority` is the retailer-local page index: the server runs
    --scheduling-policy priority (lower = earlier), so page 0 of every retailer beats
    page 50 of a flooding one and all agents progress evenly."""
    settings = get_settings()
    b64 = base64.b64encode(_to_jpeg(image)).decode()
    resp = await _get_client().chat.completions.create(
        model=settings.vision_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt or prompt_for(retailer)},
                    {"type": "image_url",
                     "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
                ],
            }
        ],
        temperature=0.1,
        max_tokens=2000,
        extra_body={"priority": priority},
    )
    return _parse_offers(resp.choices[0].message.content or "", retailer)


def _dedupe(offers: list[Offer]) -> list[Offer]:
    seen: dict[tuple, Offer] = {}
    for o in offers:
        seen.setdefault((o.retailer, (o.product_name or "").lower(), o.price), o)
    return list(seen.values())


# Shared across all concurrently running agents so their pages merge into one batch stream
# for the vLLM engine instead of each scan opening its own flood of requests. Sized to the
# server's --max-num-seqs; the engine's priority queue does the fair ordering.
_VLM_SEM = asyncio.Semaphore(32)


async def extract_offers_streamed(
    queue: asyncio.Queue[bytes | None], retailer: str, on_page=None
) -> list[Offer]:
    """Extract offers from pages fed through `queue` (a None ends the stream), so reading overlaps
    with browsing. `on_page(done, total_seen, page, offers_so_far)` fires as each page finishes.
    """
    prompt = prompt_for(retailer)
    seen = 0
    done = 0
    found = 0
    results: list[list[Offer]] = []

    async def one(img: bytes, index: int) -> None:
        nonlocal done, found
        async with _VLM_SEM:
            try:
                offers = await extract_offers_from_image(img, retailer, prompt, priority=index)
            except Exception as exc:  # noqa: BLE001 - one bad page must not fail the batch
                log.warning("vision.page_failed", error=str(exc))
                offers = []
        done += 1
        found += len(offers)
        results.append(offers)
        if on_page:
            on_page(done, seen, img, found)

    tasks: list[asyncio.Task] = []
    while True:
        img = await queue.get()
        if img is None:
            break
        tasks.append(asyncio.create_task(one(img, seen)))
        seen += 1
    await asyncio.gather(*tasks)
    return _dedupe([o for r in results for o in r])
