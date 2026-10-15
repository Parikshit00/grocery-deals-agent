"""Tier-2 page reader against a fake OpenAI-compatible VLM (no network, no model)."""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
from pathlib import Path

import httpx
from openai import AsyncOpenAI
from PIL import Image

from app.clients import vision
from app.core.config import Settings

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures" / "vision" / "netto_p002.json").read_text()
)


def _png(w: int = 300, h: int = 400) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), "white").save(buf, "PNG")
    return buf.getvalue()


def _reply(content: str, finish: str = "stop", pin: int = 100, pout: int = 50) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "x", "object": "chat.completion", "created": 0, "model": "vlm",
        "choices": [{"index": 0, "finish_reason": finish,
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": pin, "completion_tokens": pout,
                  "total_tokens": pin + pout},
    })


def _fake(monkeypatch, handler, inflight: int = 32) -> list[dict]:
    """Route the shared client to `handler`; returns the JSON bodies it received."""
    bodies: list[dict] = []

    async def record(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        out = handler(request)
        return await out if asyncio.iscoroutine(out) else out

    client = AsyncOpenAI(
        base_url="http://vlm/v1", api_key="k", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(record)),
    )
    monkeypatch.setattr(vision, "_client", client)
    monkeypatch.setattr(vision, "_gate", None)
    monkeypatch.setattr(vision, "get_settings", lambda: Settings(vlm_max_inflight=inflight))
    return bodies


def _extract(text: str | None, **kw) -> vision.PageResult:
    args = {"chain": "netto", "loyalty": None, "page": 2} | kw
    return asyncio.run(vision.extract_page(_png(), text, **args))


def test_request_prompt_text_and_extra_body(monkeypatch):
    bodies = _fake(monkeypatch, lambda r: _reply('{"offers":[]}'))
    _extract("1,99 " + "x" * 5000, loyalty="Kaufland Card", priority=7)
    body = bodies[0]
    assert body["priority"] == 7
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["temperature"] == 0 and body["model"] == "vlm"
    assert 0 < body["max_tokens"] <= 4096
    image, prompt = body["messages"][0]["content"]
    assert image["image_url"]["url"].startswith("data:image/jpeg;base64,")
    text = prompt["text"]
    assert "price with the Kaufland Card program" in text
    assert text.endswith("x" * 2995) and "x" * 2996 not in text  # trimmed to 3000 chars


def test_real_fenced_reply_is_verified_against_text(monkeypatch):
    _fake(monkeypatch, lambda r: _reply(FIXTURE["content"], pin=2157, pout=469))
    res = _extract(FIXTURE["text"])
    assert res.ok and res.reason is None
    assert (res.tokens_in, res.tokens_out) == (2157, 469)
    assert len(res.offers) == 6
    assert {o.price_check for o in res.offers} == {"text"}
    ritter = next(o for o in res.offers if o.brand == "Ritter Sport")
    # "2 für 3,-" statt 4,58: per-pack prices, the printed bundle, the count is not a pack size.
    assert (ritter.price_cents, ritter.old_price_cents, ritter.condition) == (150, 229, "2 für")
    assert (ritter.bundle_price_cents, ritter.min_qty, ritter.pack_text) == (300, 2, None)
    assert ritter.chain == "netto" and ritter.page == 2 and ritter.source == "vlm"
    assert ritter.category == "food"


def test_price_check_classification(monkeypatch):
    _fake(monkeypatch, lambda r: _reply(FIXTURE["content"]))
    assert {o.price_check for o in _extract(None).offers} == {"image_only"}
    assert {o.price_check for o in _extract("nur 1,11 2,22").offers} == {"image_only"}
    assert {o.price_check for o in _extract("1,11 2,22 3,33").offers} == {"mismatch"}


def test_valid_from_only_from_a_full_printed_date(monkeypatch):
    reply = (
        '{"offers":[{"n":"Milch","p":"0,99","vf":"VON DONNERSTAG, 02.07.2026"},'
        '{"n":"Butter","p":"1,99","c":"nur mit App","vf":"ab Do. 16.10."}]}'
    )
    _fake(monkeypatch, lambda r: _reply(reply))
    milch, butter = _extract(None).offers
    assert str(milch.valid_from) == "2026-07-02"
    assert butter.valid_from is None and butter.condition == "nur mit App; ab Do. 16.10."
    end = '{"offers":[{"n":"Milch","p":"0,99","vf":"bis 18.10.2026"}]}'
    _fake(monkeypatch, lambda r: _reply(end))
    (milch,) = _extract(None).offers
    assert milch.valid_from is None and milch.condition == "bis 18.10.2026"


def test_length_rereads_top_and_bottom_halves(monkeypatch):
    replies = iter([
        _reply('{"offers":[{"n":"Milch","p":"0,99"},{"n":"Milch","p":"0,99"},', "length"),
        _reply('{"offers":[{"n":"Milch","p":"0,99"},{"n":"Brot","p":"1,49"}]}'),
        _reply('{"offers":[{"n":"brot","p":"1,49"},{"n":"Käse","p":"2,29"}]}'),
    ])
    bodies = _fake(monkeypatch, lambda r: next(replies))
    res = _extract(None)
    assert res.ok and res.split and len(bodies) == 3
    assert sorted(o.product_name for o in res.offers) == ["Brot", "Käse", "Milch"]
    assert (res.tokens_in, res.tokens_out) == (300, 150)


def test_length_on_a_half_fails_the_page(monkeypatch):
    _fake(monkeypatch, lambda r: _reply('{"offers":[', "length"))
    res = _extract(None)
    assert not res.ok and res.offers == [] and "length" in (res.reason or "")


def test_malformed_or_missing_json_fails(monkeypatch):
    for content in ("Ich kann die Seite nicht lesen.", '{"offers": [{"n": "Milch"', '{"n": "x"}'):
        _fake(monkeypatch, lambda r, c=content: _reply(c))
        res = _extract(None)
        assert not res.ok and res.reason == "no offers JSON in the reply"


def test_non_offer_page_is_ok_and_server_error_is_not(monkeypatch):
    _fake(monkeypatch, lambda r: _reply('Hier: {"offers": []}'))
    assert _extract("Impressum").ok
    _fake(monkeypatch, lambda r: httpx.Response(500, json={"error": "boom"}))
    res = _extract(None)
    assert not res.ok and "InternalServerError" in (res.reason or "")


def test_inflight_cap_is_honoured(monkeypatch):
    live = peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.02)
        live -= 1
        return _reply('{"offers":[]}')

    _fake(monkeypatch, slow, inflight=2)

    async def go() -> list[vision.PageResult]:
        return await asyncio.gather(*(
            vision.extract_page(_png(), None, chain="netto", loyalty=None, page=i)
            for i in range(6)
        ))

    assert all(r.ok for r in asyncio.run(go()))
    assert peak == 2


def test_gate_admits_lowest_priority_first_fifo_within():
    order: list[str] = []

    async def go() -> None:
        gate = vision._Gate(1)

        async def req(name: str, priority: int) -> None:
            async with gate.slot(priority):
                order.append(name)
                await asyncio.sleep(0)

        # next week's pages (1000+) queue before today's (0+) yet run after them
        await asyncio.gather(req("hold", 0), req("next1", 1000), req("next2", 1000),
                             req("today1", 1), req("today0", 0))

    asyncio.run(go())
    assert order == ["hold", "today0", "today1", "next1", "next2"]


def test_gate_cancelled_waiter_neither_leaks_nor_loses_a_slot():
    async def go() -> None:
        gate = vision._Gate(1)
        entered: list[str] = []

        async def req(name: str) -> None:
            async with gate.slot(0):
                entered.append(name)

        async with gate.slot(0):
            queued = asyncio.create_task(req("cancelled"))
            later = asyncio.create_task(req("later"))
            await asyncio.sleep(0)
            queued.cancel()  # cancelled while queued
            await asyncio.sleep(0)
        await later  # the freed slot skipped the dead waiter
        assert entered == ["later"] and gate._free == 1 and not gate._waiters

        async with gate.slot(0):
            handed = asyncio.create_task(req("handed"))
            await asyncio.sleep(0)
        handed.cancel()  # handed the slot on release, cancelled before it resumed
        with contextlib.suppress(asyncio.CancelledError):
            await handed
        assert entered == ["later"] and gate._free == 1 and not gate._waiters

    asyncio.run(go())

def test_card_only_offer_and_loyalty_price_check(monkeypatch):
    reply = (
        '{"offers":[{"n":"Milch","lp":"0,99","c":"nur mit App"},'
        '{"n":"Butter","p":"1,99","lp":"1,49"}]}'
    )
    _fake(monkeypatch, lambda r: _reply(reply))
    milch, butter = _extract("0,99 1,99 2,49").offers
    assert (milch.price_cents, milch.loyalty_price_cents, milch.price_check) == (None, 99, "text")
    assert butter.price_check == "mismatch"  # 1,49 is not printed on the page


def test_odd_json_values_skip_the_entry_not_the_page(monkeypatch):
    reply = (
        '{"offers":[{"n":"A","p":true},{"n":"B","p":NaN},{"n":"C","p":["0,99"]},'
        '{"n":"D","p":"0,99","cat":["food"]}]}'
    )
    _fake(monkeypatch, lambda r: _reply(reply))
    res = _extract(None)
    assert res.ok and [(o.product_name, o.category) for o in res.offers] == [("D", None)]


def test_single_read_keeps_same_name_and_price_of_two_brands(monkeypatch):
    reply = (
        '{"offers":[{"n":"Gouda","b":"Milram","p":"1,99"},'
        '{"n":"Gouda","b":"Bergader","p":"1,99"}]}'
    )
    _fake(monkeypatch, lambda r: _reply(reply))
    res = _extract(None)
    assert not res.split and [o.brand for o in res.offers] == ["Milram", "Bergader"]


def test_truncated_image_fails_the_page(monkeypatch):
    _fake(monkeypatch, lambda r: _reply('{"offers":[]}'))
    res = asyncio.run(vision.extract_page(
        _png()[:200], None, chain="netto", loyalty=None, page=1))
    assert not res.ok and res.reason
