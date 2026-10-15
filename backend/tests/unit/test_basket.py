"""Basket service: shop day, the pool, and the matching agent against a fake LLM
(httpx.MockTransport answering with scripted OpenAI tool calls)."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from openai import AsyncOpenAI
from opentelemetry.sdk.trace import TracerProvider

from app.clients import llm
from app.core import tracing
from app.schemas.basket import BasketRequest, Item, PlanStore
from app.schemas.offer import Offer
from app.services import basket
from app.services.geo import Location

DAY = date(2026, 10, 14)  # a Wednesday
NOW = datetime(2026, 10, 14, 10, tzinfo=UTC)
LOC = Location(lat=48.137, lon=11.575, plz="80331", label="80331 Muenchen")


@pytest.mark.parametrize(
    ("now", "day"),
    [
        (datetime(2026, 10, 14, 10, tzinfo=UTC), date(2026, 10, 14)),  # Wednesday
        (datetime(2026, 10, 18, 10, tzinfo=UTC), date(2026, 10, 19)),  # Sunday -> Monday
        (datetime(2026, 10, 17, 21, 30, tzinfo=UTC), date(2026, 10, 17)),  # Sat 23:30 Berlin
        (datetime(2026, 10, 18, 22, 30, tzinfo=UTC), date(2026, 10, 19)),  # Mon 00:30 Berlin
    ],
)
def test_shop_day(now, day):
    assert basket.shop_day(now) == day


# --- fakes ----------------------------------------------------------------------------------

VOCAB = ["butter", "milch", "keks", "kaese"]


def _vec(text: str) -> np.ndarray:
    v = np.array([text.lower().count(w) for w in VOCAB], dtype=np.float32) + 0.1
    return v / np.linalg.norm(v)


async def _fake_embed(texts: list[str]) -> np.ndarray:
    return np.stack([_vec(t) for t in texts])


def _offer(chain: str, name: str, price: int = 199, **kw) -> Offer:
    return Offer(**{"chain": chain, "product_name": name, "price_cents": price, "pack_qty": 250,
                    "pack_unit": "g", "source": "feed", "price_check": "feed", **kw})


def _pool(*offers: Offer) -> basket.Pool:
    entries = [basket.Entry(f"o{i}", PlanStore(chain=o.chain, store=f"{o.chain} store", km=1.0),
                            o) for i, o in enumerate(offers)]
    return basket.Pool(entries, np.stack([_vec(o.product_name) for o in offers]), "k1")


def _llm(monkeypatch, *replies: dict | int, by_item: dict | None = None) -> list[dict]:
    """Serve scripted chat completions (a dict = the message, an int = an HTTP error), in
    order or, with by_item, by the item named in the request."""
    requests: list[dict] = []
    script = list(replies)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body)
        item = body["messages"][1]["content"].split("\n")[0].removeprefix("Item: ")
        reply = by_item[item] if by_item else script.pop(0)
        if isinstance(reply, int):
            return httpx.Response(reply, json={"error": "down"})
        return httpx.Response(200, json={
            "id": "x", "object": "chat.completion", "created": 0, "model": "llm",
            "choices": [{"index": 0, "message": {"role": "assistant", **reply},
                         "finish_reason": "tool_calls" if reply.get("tool_calls") else "stop"}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}})

    monkeypatch.setattr(llm, "_client", AsyncOpenAI(
        base_url="http://llm/v1", api_key="k", max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))))
    monkeypatch.setattr(basket, "embed_async", _fake_embed)
    monkeypatch.setattr(basket, "_memo", {})
    return requests


def _call(name: str, args: dict, i: int = 0) -> dict:
    return {"content": None, "tool_calls": [{"id": f"c{i}", "type": "function", "function": {
        "name": name, "arguments": json.dumps(args)}}]}


BUTTER_POOL = (_offer("a", "Butter"), _offer("a", "Butterkekse"), _offer("b", "Markenbutter"),
               _offer("b", "Buttermilch"), _offer("b", "Gouda Kaese"))


# --- pool search ----------------------------------------------------------------------------


def test_search_is_per_chain_with_lexical_boost():
    pool = _pool(*BUTTER_POOL)
    hits = pool.search("Butter", _vec("Butter"), 1)
    assert [e.offer.product_name for e in hits] == ["Butter", "Markenbutter"]
    # "Butterkekse" does not END with butter: no boost, ranked below the real butter
    assert [e.offer.product_name for e in pool.search("Butter", _vec("Butter"), 2)][:2] == [
        "Butter", "Butterkekse"]


# --- the agent ------------------------------------------------------------------------------


def test_one_turn_accepts_and_rejects(monkeypatch):
    requests = _llm(monkeypatch, _call("answer", {
        "accept": {"a": ["o0"], "b": ["o2"]}, "rejected": {"o1": "Kekse, keine Butter"},
        "reason": "Butter only."}))
    result = asyncio.run(basket.match_item(Item(name="Butter"), _pool(*BUTTER_POOL)))
    assert result.status == "matched" and result.turns == 1 and result.tokens == 120
    assert [d.offer_id for d in result.accepted] == ["o0", "o2"]
    assert {d.offer_id: d.why for d in result.rejected}["o1"] == "Kekse, keine Butter"
    assert [t["tool"] for t in result.trace] == ["search_offers", "answer"]
    body = requests[0]
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert body["messages"][1]["content"] == "Item: Butter"
    assert body["messages"][2]["tool_calls"][0]["function"]["name"] == "search_offers"
    assert "o0 | a | Butter" in body["messages"][3]["content"]


def test_reworded_search_then_answer(monkeypatch):
    requests = _llm(monkeypatch, _call("search_offers", {"query": "Kaese"}),
                    _call("answer", {"accept": {"b": ["o4"]}, "reason": "Gouda is cheese."}, 1))
    result = asyncio.run(basket.match_item(Item(name="Gouda"), _pool(*BUTTER_POOL)))
    assert result.status == "matched" and result.turns == 2
    assert [t["args"].get("query") for t in result.trace[:2]] == ["Gouda", "Kaese"]
    assert requests[1]["messages"][-1]["role"] == "tool"
    assert "o4 | b | Gouda Kaese" in requests[1]["messages"][-1]["content"]


def test_json_in_content_fallback_and_memo(monkeypatch):
    requests = _llm(monkeypatch, {"content": 'ok {"accept": {"a": ["o0"]}, "reason": "r"}'})
    pool = _pool(*BUTTER_POOL)
    first = asyncio.run(basket.match_item(Item(name="Butter"), pool))
    again = asyncio.run(basket.match_item(Item(name="butter", qty=500, unit="g"), pool))
    assert first.status == "matched" and again is first and len(requests) == 1


def test_no_fitting_offer(monkeypatch):
    _llm(monkeypatch, _call("answer", {"accept": {}, "reason": "Nothing is butter."}))
    result = asyncio.run(basket.match_item(Item(name="Butter"), _pool(*BUTTER_POOL)))
    assert result.status == "no_offer" and result.accepted == [] and result.rejected


@pytest.mark.parametrize(
    "reply",
    [
        500,  # server error
        _call("answer", {"accept": {"a": ["o99"]}, "reason": "invented"}),  # unknown id
        {"content": "I think the butter is fine."},  # no tool call, no JSON
    ],
)
def test_matcher_failure_is_never_a_silent_pick(monkeypatch, reply):
    _llm(monkeypatch, reply)
    result = asyncio.run(basket.match_item(Item(name="Butter"), _pool(*BUTTER_POOL)))
    assert result.status == "matcher_unavailable" and result.accepted == []
    assert basket._memo == {}  # failures are retried, never memoized


def test_repeated_search_is_not_run_again(monkeypatch):
    queries: list[str] = []

    async def embed(texts: list[str]) -> np.ndarray:
        queries.extend(texts)
        return await _fake_embed(texts)

    requests = _llm(monkeypatch, _call("search_offers", {"query": " kaese"}),
                    _call("answer", {"accept": {"b": ["o4"]}, "reason": "r"}, 1))
    monkeypatch.setattr(basket, "embed_async", embed)
    result = asyncio.run(basket.match_item(Item(name="K\u00e4se"), _pool(*BUTTER_POOL)))
    assert queries == ["K\u00e4se"] and result.status == "matched"
    assert result.trace[1] == {"tool": "search_offers", "args": {"query": " kaese"}, "n": 0,
                               "ms": result.trace[1]["ms"], "repeat": True}
    assert "results are above" in requests[1]["messages"][-1]["content"]

def test_last_turn_offers_only_the_answer_tool(monkeypatch):
    requests = _llm(monkeypatch, _call("search_offers", {"query": "a"}),
                    _call("search_offers", {"query": "b"}, 1),
                    _call("answer", {"accept": {"a": ["o0"]}, "reason": "r"}, 2))
    result = asyncio.run(basket.match_item(Item(name="Butter"), _pool(*BUTTER_POOL)))
    assert result.turns == 3 and result.status == "matched"
    assert [t["function"]["name"] for t in requests[2]["tools"]] == ["answer"]


def test_inspect_offer_at_most_once(monkeypatch):
    seen: list[str] = []

    async def fake_inspect(entry, question):
        seen.append(entry.id)
        return "It is a pack of butter."

    monkeypatch.setattr(basket, "_inspect", fake_inspect)
    requests = _llm(monkeypatch, {"content": None, "tool_calls": [
        {"id": "c0", "type": "function", "function": {
            "name": "inspect_offer", "arguments": json.dumps({"id": "o0", "question": "?"})}},
        {"id": "c1", "type": "function", "function": {
            "name": "inspect_offer", "arguments": json.dumps({"id": "o2", "question": "?"})}}]},
        _call("answer", {"accept": {"a": ["o0"]}, "reason": "r"}, 2))
    asyncio.run(basket.match_item(Item(name="Butter"), _pool(*BUTTER_POOL)))
    assert seen == ["o0"]
    assert "already used" in requests[1]["messages"][-1]["content"]


# --- pool loading and the whole basket ------------------------------------------------------


def _run(chain: str, status: str = "ok", age_h: float = 1.0, **kw) -> SimpleNamespace:
    store = {"id": f"{chain}1", "name": f"{chain} Mitte", "address": "x", "lat": 48.14,
             "lon": 11.58, "edition": f"{chain}-ed", "located": True}
    return SimpleNamespace(chain=chain, status=status, error=kw.get("error"),
                           finished_at=NOW - timedelta(hours=age_h), store=store,
                           stores_nearby=kw.get("nearby", [store]))


def _leaflet(chain: str, offers: list[Offer], lid: int = 1) -> SimpleNamespace:
    vecs = np.stack([_vec(o.product_name) for o in offers]).astype(np.float16)
    return SimpleNamespace(id=lid, fetched_at=NOW, valid_from=date(2026, 10, 12),
                           valid_to=date(2026, 10, 17),
                           offers=[o.model_dump(mode="json") for o in offers],
                           embeddings=vecs.tobytes())


def _patch_repo(monkeypatch, runs, leaflets):
    async def latest_runs(session, plz):
        return runs

    async def current_leaflets(session, chain, edition, today):
        return leaflets.get((chain, edition), [])

    monkeypatch.setattr(basket, "_decoded", {})
    monkeypatch.setattr(basket.repo, "latest_runs", latest_runs)
    monkeypatch.setattr(basket.repo, "current_leaflets", current_leaflets)
    monkeypatch.setattr(basket, "CHAINS", {c: SimpleNamespace(NAME=c, LABEL=c.title())
                                           for c in ("a", "b", "c", "d", "e")})


def _req(**kw) -> BasketRequest:
    return BasketRequest(location="80331", items=[Item(name="Butter")], **kw)


def test_load_pool_states_and_filters(monkeypatch):
    far = {"id": "b9", "name": "b Far", "address": "y", "lat": 48.30, "lon": 11.58,
           "edition": "b-far", "located": True}
    runs = [
        _run("a"),
        _run("b", nearby=[far]),  # nearest store 18 km away
        _run("c", status="blocked", error="Bot protection.", age_h=2),
        _run("d", status="failed", age_h=30),  # old failure: scan again
    ]
    a_offers = [
        _offer("a", "Butter"),
        _offer("a", "Grill", category="nonfood"),
        _offer("a", "Flagged Butter", price_check="mismatch"),
        _offer("a", "Butter ab Do", valid_from=date(2026, 10, 15)),
        _offer("a", "Kaese", category=None),
    ]
    _patch_repo(monkeypatch, runs, {("a", "a-ed"): [_leaflet("a", a_offers)]})
    states, needs, pool = asyncio.run(basket.load_pool(None, LOC, _req(), DAY, NOW))
    by = {s.chain: s for s in states}
    assert by["a"].status == "ok" and by["a"].store and by["a"].store.km == 0.5
    assert by["b"].status == "no_store" and "5 km" in (by["b"].reason or "")
    assert (by["c"].status, by["c"].reason) == ("blocked", "Bot protection.")
    assert needs == ["d", "e"]
    assert [e.offer.product_name for e in pool.entries] == ["Butter", "Kaese"]
    assert pool.vectors.shape == (2, len(VOCAB))


def test_load_pool_override_and_expired(monkeypatch):
    other = {"id": "a2", "name": "a Ost", "address": "z", "lat": 48.13, "lon": 11.60,
             "edition": "a-ost", "located": True}
    near = _run("a").store
    _patch_repo(monkeypatch, [_run("a", nearby=[near, other])],
                {("a", "a-ost"): [_leaflet("a", [_offer("a", "Butter")])]})
    states, needs, pool = asyncio.run(
        basket.load_pool(None, LOC, _req(overrides={"a": "a2"}), DAY, NOW))
    assert states[0].store and states[0].store.store == "a Ost" and len(pool.entries) == 1
    # without the override the nearest store's edition has no current leaflet
    states, needs, pool = asyncio.run(basket.load_pool(None, LOC, _req(), DAY, NOW))
    assert needs[0] == "a" and states[0].status == "needs_scan" and not pool.entries


def test_run_plans_from_accepted_offers(monkeypatch):
    _patch_repo(monkeypatch, [_run("a"), _run("b")], {
        ("a", "a-ed"): [_leaflet("a", [_offer("a", "Butter", 199), _offer("a", "Milch", 99)])],
        ("b", "b-ed"): [_leaflet("b", [_offer("b", "Markenbutter", 129)], lid=2)],
    })

    class Session:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(basket, "SessionLocal", Session)
    _llm(monkeypatch, by_item={
        "Butter": _call("answer", {"accept": {"a": ["o0"], "b": ["o2"]}, "reason": "r"}),
        "Milch": _call("answer", {"accept": {"a": ["o1"]}, "reason": "r"})})
    req = BasketRequest(location="80331", items=[Item(name="Butter"), Item(name="Milch")])
    resp = asyncio.run(basket.run(req, LOC, NOW))
    assert resp.shop_day == DAY and resp.needs_scan == ["c", "d", "e"]
    assert [i.status for i in resp.items] == ["matched", "matched"]
    assert [m.chain for m in resp.items[0].best] == ["b", "a"]
    assert [(p.stores[0].chain, p.total_cents) for p in resp.single] == [("a", 298), ("b", 129)]
    assert resp.split and resp.split.total_cents == 228 and resp.gaps == []
    assert resp.trace_id is None  # tracing off
    provider = TracerProvider()
    monkeypatch.setattr(tracing, "_tracer", provider.get_tracer("test"))
    traced = asyncio.run(basket.run(req, LOC, NOW))
    assert re.fullmatch(r"[0-9a-f]{32}", traced.trace_id or "")
    assert traced.model_dump(mode="json")["trace_id"] == traced.trace_id
