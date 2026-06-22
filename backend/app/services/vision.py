"""Read prospekt page images with the local VLM -> structured offers.

Served via Ollama's native vision API (driver-compatible on this host; vLLM/Qwen3-VL needs a newer
GPU driver). The VLM is the only thing that interprets the flyer pages.
"""
from __future__ import annotations

import asyncio
import base64
import io
import json
from pathlib import Path

import httpx
from PIL import Image

from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.offer import Offer

log = get_logger(__name__)

_PROMPTS = Path(__file__).resolve().parents[1] / "sources" / "prospekt" / "prompts"
_GENERIC_PROMPT = (
    "You are reading ONE page of a German supermarket weekly flyer. Extract every product offer. "
    'Return ONLY JSON {"offers":[{"product_name":string,"brand":string|null,"price":number|null,'
    '"old_price":number|null,"unit":string|null,"description":string|null}]}. '
    'Prices in EUR (comma is the decimal separator). If no offers, return {"offers":[]}.'
)


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
    offers: list[Offer] = []
    for o in data.get("offers", []):
        name = (o.get("product_name") or "").strip()
        if not name:
            continue
        offers.append(
            Offer(
                source=f"{retailer}-prospekt",
                retailer=retailer.capitalize(),
                product_name=name,
                brand=o.get("brand"),
                description=o.get("description"),
                price=_num(o.get("price")),
                old_price=_num(o.get("old_price")),
                unit=o.get("unit"),
            )
        )
    return offers


def _to_jpeg(image: bytes) -> bytes:
    """Normalize any captured page (WebP/AVIF/PNG from leaflet CDNs) to JPEG for the VLM."""
    im = Image.open(io.BytesIO(image)).convert("RGB")
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return buf.getvalue()


async def extract_offers_from_image(
    image: bytes, retailer: str, prompt: str | None = None
) -> list[Offer]:
    settings = get_settings()
    b64 = base64.b64encode(_to_jpeg(image)).decode()
    payload = {
        "model": settings.vision_model,
        "messages": [
            {"role": "user", "content": prompt or prompt_for(retailer), "images": [b64]}
        ],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1},
    }
    async with httpx.AsyncClient(timeout=180.0) as client:
        r = await client.post(f"{settings.ollama_base_url}/api/chat", json=payload)
        r.raise_for_status()
        content = r.json().get("message", {}).get("content", "")
    return _parse_offers(content, retailer)


def _dedupe(offers: list[Offer]) -> list[Offer]:
    seen: dict[tuple, Offer] = {}
    for o in offers:
        seen.setdefault((o.retailer, (o.product_name or "").lower(), o.price), o)
    return list(seen.values())


async def extract_offers_from_pages(
    pages: list[bytes], retailer: str, concurrency: int = 2, on_page=None
) -> list[Offer]:
    """Extract offers from all pages; `on_page(done, total)` reports progress."""
    prompt = prompt_for(retailer)
    sem = asyncio.Semaphore(concurrency)
    done = 0
    total = len(pages)

    async def one(img: bytes) -> list[Offer]:
        nonlocal done
        async with sem:
            try:
                return await extract_offers_from_image(img, retailer, prompt)
            except Exception as exc:  # noqa: BLE001 - one bad page must not fail the batch
                log.warning("vision.page_failed", error=str(exc))
                return []
            finally:
                done += 1
                if on_page:
                    on_page(done, total)

    results = await asyncio.gather(*[one(p) for p in pages])
    return _dedupe([o for r in results for o in r])
