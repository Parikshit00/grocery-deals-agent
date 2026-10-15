"""Tier-2 page reader: one leaflet page image (+ its text layer) -> verified offers via the VLM."""
from __future__ import annotations

import asyncio
import base64
import heapq
import io
import itertools
import re
import time
import unicodedata
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import get_args

from openai import APIError, AsyncOpenAI
from PIL import Image

from app.clients.llm import last_json_object
from app.core.config import get_settings
from app.core.logging import get_logger
from app.schemas.offer import Category, Offer, PriceCheck
from app.services.offers import (
    normalize,
    parse_pack,
    parse_price_cents,
    price_in_text,
    price_tokens,
)

log = get_logger(__name__)

_PROMPT = (Path(__file__).resolve().parents[1] / "agents" / "prompts" / "extract.txt").read_text(
    encoding="utf-8"
)
# Pages are downscaled to this longest side before the VLM: Qwen vision costs 1 token per
# 32x32 px, and 1024px reads a flyer page as reliably as full resolution at ~3x less prompt.
_MAX_SIDE = 1024
_PROMPT_TEXT_CHARS = 3000
_CATEGORIES = set(get_args(Category))
# A full printed date (with year) is a source date; "ab Do. 16.10." alone is not.
_FULL_DATE = re.compile(r"(\d{1,2})\.(\d{1,2})\.(\d{4})")
_MULTI_BUY = re.compile(r"\s*(\d+)\s*f(?:ü|ue|u)r\b", re.IGNORECASE)


@dataclass
class PageResult:
    ok: bool
    offers: list[Offer]
    reason: str | None
    tokens_in: int
    tokens_out: int
    seconds: float
    split: bool = False  # the first read hit the output limit and the halves were read


def _to_jpeg(image: bytes) -> bytes:
    """Normalize any captured page (WebP/AVIF/PNG from leaflet CDNs) to JPEG for the VLM,
    downscaled to _MAX_SIDE longest side."""
    im = Image.open(io.BytesIO(image)).convert("RGB")
    im.thumbnail((_MAX_SIDE, _MAX_SIDE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)
    return buf.getvalue()


def _halves(image: bytes) -> tuple[bytes, bytes]:
    """Top and bottom halves of a page with 5% overlap, as PNG (re-encoded by _to_jpeg)."""
    im = Image.open(io.BytesIO(image)).convert("RGB")
    w, h = im.size
    cut, pad = h // 2, h // 40
    out = []
    for box in ((0, 0, w, cut + pad), (0, cut - pad, w, h)):
        buf = io.BytesIO()
        im.crop(box).save(buf, "PNG")
        out.append(buf.getvalue())
    return out[0], out[1]


class _Gate:
    """Caps concurrent VLM requests; a freed slot goes to the waiter with the lowest priority
    value (FIFO within one), so next week's pages never hold today's back in the client, where
    the server's priority scheduler cannot see them. Waiters exist only while no slot is free."""

    def __init__(self, slots: int) -> None:
        self._free = slots
        self._waiters: list[tuple[int, int, asyncio.Future[None]]] = []
        self._seq = itertools.count()

    @asynccontextmanager
    async def slot(self, priority: int) -> AsyncIterator[None]:
        if self._free > 0:
            self._free -= 1
        else:
            fut = asyncio.get_running_loop().create_future()
            heapq.heappush(self._waiters, (priority, next(self._seq), fut))
            try:
                await fut
            except asyncio.CancelledError:
                if not fut.cancelled():  # handed the slot, then cancelled: pass it on
                    self._release()
                raise  # cancelled while queued: release() skips the dead future
        try:
            yield
        finally:
            self._release()

    def _release(self) -> None:
        while self._waiters:
            fut = heapq.heappop(self._waiters)[2]
            if not fut.done():
                fut.set_result(None)
                return
        self._free += 1


_client: AsyncOpenAI | None = None
_gate: tuple[asyncio.AbstractEventLoop, _Gate] | None = None


def _get_client() -> AsyncOpenAI:
    """One shared client so all scans reuse the same connection pool."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncOpenAI(
            base_url=settings.vlm_base_url, api_key=settings.vllm_api_key, max_retries=0,
            timeout=600,
        )
    return _client


def _inflight() -> _Gate:
    """Process-wide cap on concurrent VLM requests, created inside the running loop."""
    global _gate
    loop = asyncio.get_running_loop()
    if _gate is None or _gate[0] is not loop:
        _gate = (loop, _Gate(get_settings().vlm_max_inflight))
    return _gate[1]


def _prompt(text: str, loyalty: str | None) -> str:
    lp = (
        f"price with the {loyalty} program (app or card), or null"
        if loyalty
        else "always null (this chain has no loyalty price)"
    )
    return _PROMPT.replace("{loyalty}", lp) + (text[:_PROMPT_TEXT_CHARS] or "(none)")


def _str(value: object) -> str | None:
    s = str(value).strip() if value is not None else ""
    return s or None


def _each(total: int | None, n: int) -> int | None:
    """Per-pack cents of a bundle price, rounded half up."""
    return (2 * total + n) // (2 * n) if total else None


def _offer(o: dict, chain: str, page: int, text: str, priced_page: bool) -> Offer | None:
    """One compact-key entry as a verified Offer, or None without a name or any price."""
    name = _str(o.get("n"))
    price, old, lp = (parse_price_cents(o.get(k)) for k in ("p", "o", "lp"))
    printed = [c for c in (price, lp) if c is not None]
    if not name or not printed:
        return None  # a card/app-only offer keeps price None and its loyalty price
    check: PriceCheck = "image_only"
    if priced_page:
        check = "text" if all(price_in_text(c, text) for c in printed) else "mismatch"
    vf, condition, pack = _str(o.get("vf")), _str(o.get("c")), _str(o.get("q"))
    m = _FULL_DATE.search(vf or "")
    if m and "bis" in (vf or "")[: m.start()].lower():
        m = None  # an end date ("bis 18.10.2026") is not a valid-from date
    try:
        valid_from = date(int(m[3]), int(m[2]), int(m[1])) if m else None
    except ValueError:
        valid_from = None
    if vf and valid_from is None:  # keep a yearless "ab Do. 16.10." visible as a condition
        condition = f"{condition}; {vf}" if condition else vf
    # Multi-buy "2 für 3,-": the printed prices are for N packs (verified above as printed).
    bundle, n = None, 1
    if (mb := _MULTI_BUY.match(condition or "")) and int(mb[1]) > 1:
        n, bundle = int(mb[1]), price
        price, old, lp = _each(price, n), _each(old, n), _each(lp, n)
        if pack and ("packung" in pack.lower() or parse_pack(pack) == (n, "stk")):
            pack = None  # "2 Packungen" restates the count, not the size of one pack
    cat = o.get("cat")
    return normalize(Offer(
        chain=chain, product_name=name, brand=_str(o.get("b")),
        category=cat if isinstance(cat, str) and cat in _CATEGORIES else None,
        price_cents=price, old_price_cents=old, loyalty_price_cents=lp,
        pack_text=pack, reference_printed=_str(o.get("bp")), condition=condition,
        min_qty=n, bundle_price_cents=bundle,
        valid_from=valid_from, page=page, source="vlm", price_check=check,
    ))


def _offers(data: dict, chain: str, page: int, text: str) -> list[Offer]:
    """Map the compact keys to Offers, verify each price against the full page text."""
    priced_page = price_tokens(text) >= 3
    offers = []
    for o in data["offers"]:
        if not isinstance(o, dict):
            continue
        try:  # valid JSON with odd values ("p": true, NaN, "cat": [...]) skips that entry
            offer = _offer(o, chain, page, text, priced_page)
        except (ArithmeticError, ValueError, TypeError):
            continue
        if offer:
            offers.append(offer)
    return offers


async def _read(image: bytes, prompt: str, priority: int) -> tuple[str, str | None, int, int]:
    """One VLM call: (finish_reason, content, tokens_in, tokens_out)."""
    settings = get_settings()
    jpeg = _to_jpeg(image)
    w, h = Image.open(io.BytesIO(jpeg)).size
    # Conservative: image tokens rounded up per 32 px, 2 chars per text token (leaflet text with
    # prices measured 2.4-3.4), chat template.
    est = (w // 32 + 1) * (h // 32 + 1) + len(prompt) // 2 + 64
    async with _inflight().slot(priority):
        resp = await _get_client().chat.completions.create(
            model=settings.vision_model,
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()}},
                {"type": "text", "text": prompt},
            ]}],
            temperature=0,
            max_tokens=min(4096, settings.vlm_max_model_len - est),
            extra_body={
                "priority": priority,
                "repetition_penalty": 1.05,
                "chat_template_kwargs": {"enable_thinking": False},
            },
        )
    choice, usage = resp.choices[0], resp.usage
    return (
        choice.finish_reason,
        choice.message.content,
        usage.prompt_tokens if usage else 0,
        usage.completion_tokens if usage else 0,
    )


async def extract_page(
    image: bytes, text: str | None, *, chain: str, loyalty: str | None, page: int,
    priority: int = 0,
) -> PageResult:
    """Read one page; `priority` is passed to the server's priority scheduler (lower = earlier).

    A page that hits the output limit is re-read once as top and bottom halves; ok means the JSON
    parsed and every call finished normally (zero offers on a non-offer page is ok).
    """
    start = time.monotonic()
    full = unicodedata.normalize("NFKC", text or "").strip()
    prompt = _prompt(full, loyalty)
    tin = tout = 0
    datas: list[dict] = []
    reason: str | None = None
    split = False
    try:
        finish, content, tin, tout = await _read(image, prompt, priority)
        parts = [(finish, content)]
        if split := finish == "length":
            halves = await asyncio.gather(*(_read(h, prompt, priority) for h in _halves(image)))
            parts = [(f, c) for f, c, _, _ in halves]
            tin += sum(r[2] for r in halves)
            tout += sum(r[3] for r in halves)
        for finish, content in parts:
            data = last_json_object(content or "")
            if finish != "stop":
                reason = f"finish_reason {finish}"
            elif data is None or not isinstance(data.get("offers"), list):
                reason = "no offers JSON in the reply"
            else:
                datas.append(data)
    except (APIError, OSError) as exc:  # OSError: unreadable or truncated image
        reason = f"{type(exc).__name__}: {exc}"
    seconds = round(time.monotonic() - start, 2)
    if reason:
        log.warning("vision.page_failed", chain=chain, page=page, reason=reason)
        return PageResult(False, [], reason, tin, tout, seconds, split)
    offers = [o for d in datas for o in _offers(d, chain, page, full)]
    if split:  # halves overlap by 5%, so an offer on the cut line can come back twice
        merged: dict[tuple[str, str, int | None, int | None], Offer] = {}
        for o in offers:
            key = ((o.brand or "").casefold(), o.product_name.casefold(), o.price_cents,
                   o.loyalty_price_cents)
            merged.setdefault(key, o)
        offers = list(merged.values())
    return PageResult(True, offers, None, tin, tout, seconds, split)
