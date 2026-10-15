"""Basket service: the pool of usable offers near a location, the per-item matching agent and
the plans.

The pool is cache-only: each chain's latest scan run for the postcode names its store edition,
and that edition's current leaflets supply offers valid on the shop day. The LLM only decides
which pool offers ARE the item (tool calls over short per-request ids); code computes every
price and total (services/optimizer.py).
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
import uuid
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import numpy as np
from openai import AsyncOpenAI, OpenAIError
from openinference.instrumentation import using_session
from opentelemetry import trace
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import CHAINS
from app.agents.base import Store, http_client, jpeg_thumb
from app.clients import llm
from app.clients.embeddings import embed_async
from app.core import tracing
from app.core.config import get_settings
from app.core.logging import get_logger
from app.persistence import repository as repo
from app.persistence.db import SessionLocal
from app.persistence.models import ScanRun
from app.schemas.basket import (
    BasketRequest,
    BasketResponse,
    ChainState,
    Decision,
    Item,
    ItemResult,
    PlanStore,
)
from app.schemas.offer import Offer
from app.services import optimizer
from app.services.geo import Location, nearest

log = get_logger(__name__)

BERLIN = ZoneInfo("Europe/Berlin")
STATUS_TTL = timedelta(hours=24)
FLOOR = 0.5  # score below which a pool offer is never shown to the agent
LEXICAL_BOOST = 0.3
_UMLAUTS = str.maketrans({"\u00e4": "ae", "\u00f6": "oe", "\u00fc": "ue", "\u00df": "ss"})
K_PER_CHAIN = 4
MAX_TURNS = 3
ITEM_BUDGET_S = 20.0
_MEMO_MAX = 2000


def shop_day(now: datetime) -> date:
    """The day prices are for: today (Berlin) on Mon..Sat, the next Monday on Sunday."""
    day = now.astimezone(BERLIN).date()
    return day + timedelta(days=1) if day.weekday() == 6 else day


# --- the pool -------------------------------------------------------------------------------


@dataclass
class Entry:
    id: str  # short per-request id shown to the agent
    store: PlanStore
    offer: Offer


@dataclass
class Pool:
    entries: list[Entry] = field(default_factory=list)
    vectors: np.ndarray = field(default_factory=lambda: np.zeros((0, 0), dtype=np.float32))
    key: str = ""  # identifies the leaflet versions and shop day (memo key)

    def __post_init__(self) -> None:
        self.by_id = {e.id: e for e in self.entries}
        chains: defaultdict[str, list[int]] = defaultdict(list)
        for i, e in enumerate(self.entries):
            chains[e.store.chain].append(i)
        self.chains = {c: np.array(ix) for c, ix in chains.items()}

        self.words = [_words(" ".join(filter(None, (e.offer.brand, e.offer.product_name,
                                                     e.offer.description))))
                      for e in self.entries]

    def search(self, query: str, query_vec: np.ndarray, k_per_chain: int) -> list[Entry]:
        """Top k per chain, so one chain never crowds out the others.

        Score = cosine + LEXICAL_BOOST when an offer word ENDS with a query word: a German
        compound names its kind last ("Landmilch" is milk, "Butterkekse" is not butter), and
        short-name cosine alone ranks such compounds poorly.
        """
        if not self.entries:
            return []
        query_words = [w for w in _words(query) if len(w) >= 3]
        lexical = np.array([any(w.endswith(q) for w in words for q in query_words)
                            for words in self.words], dtype=np.float32)
        sims = self.vectors @ query_vec.astype(np.float32) + LEXICAL_BOOST * lexical
        hits: list[Entry] = []
        for ix in self.chains.values():
            top = ix[np.argsort(-sims[ix], kind="stable")[:k_per_chain]]
            hits += [self.entries[i] for i in top if sims[i] >= FLOOR]
        return hits


def _words(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold().translate(_UMLAUTS))


# ponytail: decoded leaflets keyed by (id, fetched_at), unbounded; a handful of leaflets per PLZ.
_decoded: dict[tuple[int, datetime], tuple[list[Offer], np.ndarray]] = {}


def _decode(row) -> tuple[list[Offer], np.ndarray]:
    key = (row.id, row.fetched_at)
    if key not in _decoded:
        offers = [Offer.model_validate(o) for o in row.offers]
        vectors = (np.frombuffer(row.embeddings, dtype=np.float16)
                   .reshape(len(offers), -1).astype(np.float32)
                   if row.embeddings and offers else np.zeros((len(offers), 0), np.float32))
        _decoded[key] = (offers, vectors)
    return _decoded[key]


def _usable(o: Offer, lf_from: date, lf_to: date, day: date) -> bool:
    """Valid on the shop day, not non-food, price not flagged by the page-text check."""
    return ((o.valid_from or lf_from) <= day <= (o.valid_to or lf_to)
            and o.category != "nonfood" and o.price_check != "mismatch")


def _pick_store(
    run: ScanRun, location: Location, radius_km: float, override: str | None, label: str
) -> tuple[dict | None, ChainState | None]:
    """(store dict with km, None) or (None, the state that keeps the chain out of plans).

    Nearest store and km are recomputed from the CURRENT location; an override wins if the
    run saw that store. An unknown override returns (None, None): the chain needs a scan.
    """
    nearby = run.stores_nearby or ([run.store] if run.store else [])
    stores = [Store(**{k: v for k, v in s.items() if k != "km"}) for s in nearby]
    if override:
        hit = next((s for s in stores if s.id == override), None)
        if hit is None:
            return None, None
        km = next((km for s, km in nearest([hit], location.lat, location.lon, 1e9)), None)
        return {**vars(hit), "km": km}, None
    near = nearest(stores, location.lat, location.lon, 1e9, limit=1)
    if near:
        store, km = near[0]
        if km > radius_km:
            return None, ChainState(chain=run.chain, status="no_store",
                                    reason=f"No {label} within {radius_km:g} km.")
        return {**vars(store), "km": km}, None
    if stores:  # edition known, store position not
        s = stores[0]
        return None, ChainState(
            chain=run.chain, status=run.status,
            reason="Store location unknown: its offers are in the offer wall, not in plans.",
            store=PlanStore(chain=run.chain, store=s.name, km=None))
    return None, None


async def load_pool(
    session: AsyncSession, location: Location, req: BasketRequest, day: date, now: datetime
) -> tuple[list[ChainState], list[str], Pool]:
    """Per chain state, chains needing a scan, and the pool of offers usable on `day`."""
    runs = {r.chain: r for r in await repo.latest_runs(session, location.plz)}
    states: list[ChainState] = []
    needs: list[str] = []
    entries: list[Entry] = []
    vectors: list[np.ndarray] = []
    versions: list[str] = [day.isoformat()]
    for name, module in CHAINS.items():
        run = runs.get(name)
        if run is not None and run.status not in ("ok", "partial", "cancelled"):
            if run.finished_at and now - run.finished_at < STATUS_TTL:
                states.append(ChainState(chain=name, status=run.status, reason=run.error))
                continue
        store, state = (None, None)
        if run is not None and run.status in ("ok", "partial"):
            store, state = _pick_store(run, location, req.radius_km,
                                       req.overrides.get(name), module.LABEL)
        if state is not None:
            states.append(state)
            continue
        leaflets = [] if store is None else [
            lf for lf in await repo.current_leaflets(session, name, store["edition"], day)
            if lf.valid_from <= day]
        if store is None or not leaflets:
            needs.append(name)
            states.append(ChainState(chain=name, status="needs_scan"))
            continue
        assert run is not None
        plan_store = PlanStore(chain=name, store=store["name"], km=store["km"])
        states.append(ChainState(chain=name, status=run.status, reason=run.error,
                                 store=plan_store))
        for lf in leaflets:
            versions.append(f"{lf.id}:{lf.fetched_at.isoformat()}")
            offers, vecs = _decode(lf)
            keep = [i for i, o in enumerate(offers)
                    if _usable(o, lf.valid_from, lf.valid_to, day)]
            entries += [Entry(f"o{len(entries) + n}", plan_store, offers[i])
                        for n, i in enumerate(keep)]
            vectors.append(vecs[keep])
    key = hashlib.sha1("|".join(sorted(versions)).encode()).hexdigest()[:16]
    pool = Pool(entries, np.concatenate(vectors) if vectors else np.zeros((0, 0), np.float32),
                key)
    return states, needs, pool


# --- the per-item agent ---------------------------------------------------------------------

_SYSTEM = (
    "You match ONE item of a German shopping list to supermarket offers. Accept an offer only "
    "if it IS that product, in any brand, pack size or plain variety; quantities and prices "
    "are handled elsewhere, never reject for size. In a German compound the last part names the "
    "product, yet a compound can still be another product (Leberkaese is meat loaf, not cheese; "
    "Fleischsalat is a salad). A product that merely contains the item, is flavoured with it or "
    "is a snack, drink or dish made of it is a different product: for \"Butter\" accept "
    "Markenbutter or "
    "Suessrahmbutter, but reject Butterkekse, Buttermilch, Butterschmalz, Erdnussbutter and "
    "spreads such as \"Rama mit Butter\"; for \"Milch\" reject Buttermilch, Schokolade and "
    "Scheuermilch. Reject non-food unless the item is non-food. Accept every fitting offer, "
    "several per chain if they fit. The item name was already searched: never repeat it. "
    "Only if those results miss the item, call search_offers once with a "
    "different German term (synonym, singular, a more specific product). Use inspect_offer only "
    "when the name cannot decide. Finish with the answer tool. Be brief, every word costs time: "
    "reason at most 12 words; rejected only for up to 3 near misses, at most 4 words each. Use "
    "only ids you were shown."
)


def _fn(name: str, description: str, properties: dict, required: list[str]) -> Any:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties, "required": required}}}


_ANSWER = _fn(
    "answer", "Final decision for this item.",
    {"accept": {"type": "object", "description": "chain -> list of accepted offer ids",
                "additionalProperties": {"type": "array", "items": {"type": "string"}}},
     "rejected": {"type": "object", "description": "near miss id -> reason, max 4 words",
                  "additionalProperties": {"type": "string"}},
     "reason": {"type": "string", "description": "max 12 words"}},
    ["accept", "reason"])
_FORCE_ANSWER: Any = {"type": "function", "function": {"name": "answer"}}
_REPEAT = ("Already searched: those results are above. Try a different German term (a synonym, "
           "a broader or narrower product word) or answer now.")
_TOOLS = [
    _fn("search_offers", "Semantic search over the current offers near the user; returns the "
        "top k offers per chain.", {"query": {"type": "string"},
                                    "k_per_chain": {"type": "integer", "default": K_PER_CHAIN}},
        ["query"]),
    _fn("inspect_offer", "Ask the vision model a question about one offer's product image.",
        {"id": {"type": "string"}, "question": {"type": "string"}}, ["id", "question"]),
    _ANSWER,
]


@dataclass
class Matched:
    status: str  # matched | no_offer | matcher_unavailable
    accepted: list[Decision] = field(default_factory=list)
    rejected: list[Decision] = field(default_factory=list)
    trace: list[dict] = field(default_factory=list)
    reason: str | None = None
    turns: int = 0
    tokens: int = 0


_memo: dict[tuple[str, str], Matched] = {}


def _line(e: Entry) -> str:
    o = e.offer
    name = " ".join(filter(None, (o.brand, o.product_name)))
    rest = " | ".join(filter(None, ((o.description or "")[:80], o.pack_text)))
    return f"{e.id} | {e.store.chain} | {name} | {rest} | {o.category or 'other'}"


_vlm: AsyncOpenAI | None = None


async def _inspect(entry: Entry, question: str) -> str:
    """Ask the VLM about the offer's product image (fetched with the honest client)."""
    global _vlm
    settings = get_settings()
    if not entry.offer.image_url:
        return "This offer has no image."
    async with http_client(settings.contact) as http:
        resp = await http.get(entry.offer.image_url)
        resp.raise_for_status()
    image = base64.b64encode(jpeg_thumb(resp.content, width=512, quality=80)).decode()
    if _vlm is None:
        _vlm = AsyncOpenAI(base_url=settings.vlm_base_url, api_key=settings.vllm_api_key,
                           max_retries=0, timeout=15.0)
    out = await _vlm.chat.completions.create(
        model=settings.vision_model, max_tokens=120, temperature=0,
        messages=[{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image}"}},
            {"type": "text", "text": f"{_line(entry)}\n{question} Answer in one sentence."}]}],
        extra_body={**llm.NO_THINK, "priority": 0})
    return out.choices[0].message.content or ""


def _decide(answer: dict, seen: dict[str, Entry], result: Matched) -> None:
    accept = answer.get("accept")
    if isinstance(accept, dict):
        ids = [i for v in accept.values() for i in (v if isinstance(v, list) else [v])]
    else:
        ids = accept if isinstance(accept, list) else []
    valid = [str(i) for i in ids if str(i) in seen]
    whys = answer.get("rejected")
    if not isinstance(whys, dict):
        whys = {}
    result.reason = str(answer.get("reason") or "") or None
    for oid, e in seen.items():
        d = Decision(chain=e.store.chain, offer_id=oid,
                     name=" ".join(filter(None, (e.offer.brand, e.offer.product_name))))
        if oid in valid:
            result.accepted.append(d)
        else:
            result.rejected.append(d.model_copy(update={"why": whys.get(oid)}))
    if ids and not valid:
        result.status, result.reason = "matcher_unavailable", "The model answered unknown ids."
    else:
        result.status = "matched" if valid else "no_offer"


async def match_item(item: Item, pool: Pool) -> Matched:
    """Run the matching agent for one item: prefetched candidates, at most MAX_TURNS turns."""
    with tracing.span("item_agent", "agent", input=item.model_dump(), item=item.name) as sp:
        result = await _match_item(item, pool)
        tracing.attrs(sp, status=result.status, turns=result.turns, tokens=result.tokens,
                      output={"status": result.status, "reason": result.reason,
                              "accepted": [d.name for d in result.accepted]})
        return result


async def _match_item(item: Item, pool: Pool) -> Matched:
    memo_key = (item.name.casefold(), pool.key)
    if memo_key in _memo:
        return _memo[memo_key]
    result = Matched(status="no_offer")
    if not pool.entries:
        result.reason = "No offers in the pool."
        return result
    seen: dict[str, Entry] = {}
    inspected = False
    searched: set[str] = set()

    async def search_offers(query: str, k_per_chain: int = K_PER_CHAIN) -> tuple[str, int]:
        key = " ".join(_words(query))
        if key in searched:
            return _REPEAT, 0
        searched.add(key)
        vec = (await embed_async([query]))[0]
        hits = pool.search(query, vec, max(1, min(int(k_per_chain), 8)))
        seen.update({e.id: e for e in hits})
        return "\n".join(_line(e) for e in hits) or "No offers matched this query.", len(hits)

    async def inspect_offer(id: str, question: str) -> tuple[str, int]:
        nonlocal inspected
        if inspected:
            return "inspect_offer was already used for this item.", 0
        if id not in seen:
            return f"Unknown id {id}.", 0
        inspected = True
        return await _inspect(seen[id], question), 1

    tools: dict[str, Callable[..., Awaitable[tuple[str, int]]]] = {
        "search_offers": search_offers, "inspect_offer": inspect_offer}

    async def call(name: str, args: dict) -> str:
        t0 = time.monotonic()
        with tracing.span(name, "tool", input=args) as sp:
            try:
                out, n = await tools[name](**args)
            except (TypeError, KeyError) as exc:
                out, n = f"Invalid call: {exc}", 0
            except (httpx.HTTPError, OpenAIError, OSError) as exc:
                out, n = f"Tool failed: {type(exc).__name__}", 0
            tracing.attrs(sp, output=out, hits=n)
        result.trace.append({"tool": name, "args": args, "n": n,
                             "ms": round((time.monotonic() - t0) * 1000),
                             **({"repeat": True} if out == _REPEAT else {})})
        return out

    # The prefetch is replayed as the agent's own first search, so the usual case is one turn.
    prefetch = {"query": item.name, "k_per_chain": K_PER_CHAIN}
    messages: list[Any] = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Item: {item.name}"},
        {"role": "assistant", "content": None, "tool_calls": [{
            "id": "prefetch", "type": "function",
            "function": {"name": "search_offers", "arguments": json.dumps(prefetch)}}]},
        {"role": "tool", "tool_call_id": "prefetch",
         "content": await call("search_offers", prefetch)}]
    settings = get_settings()
    try:
        async with asyncio.timeout(ITEM_BUDGET_S):
            for turn in range(1, MAX_TURNS + 1):
                result.turns = turn
                resp = await llm.get_client().chat.completions.create(
                    model=settings.llm_model, messages=messages,
                    tools=_TOOLS if turn < MAX_TURNS else [_ANSWER],
                    tool_choice="auto" if turn < MAX_TURNS else _FORCE_ANSWER,
                    temperature=0, max_tokens=300, extra_body=llm.NO_THINK)
                result.tokens += resp.usage.total_tokens if resp.usage else 0
                msg = resp.choices[0].message
                calls = [c for c in msg.tool_calls or [] if c.type == "function"]
                final = next((c for c in calls if c.function.name == "answer"), None)
                answer = (json.loads(final.function.arguments or "{}") if final
                          else None if calls else llm.last_json_object(msg.content or ""))
                if isinstance(answer, dict):
                    result.trace.append({"tool": "answer", "args": answer, "n": 0, "ms": 0})
                    _decide(answer, seen, result)
                    break
                if not calls:
                    raise ValueError("no tool call and no JSON answer")
                messages.append({"role": "assistant", "content": msg.content,
                                 "tool_calls": [c.model_dump() for c in calls]})
                for c in calls:
                    args = json.loads(c.function.arguments or "{}")
                    out = await call(c.function.name, args if isinstance(args, dict) else {})
                    messages.append({"role": "tool", "tool_call_id": c.id, "content": out})
            else:
                raise ValueError(f"no answer within {MAX_TURNS} turns")
    except (OpenAIError, TimeoutError, ValueError) as exc:
        log.warning("basket.matcher_failed", item=item.name, error=repr(exc))
        return Matched(status="matcher_unavailable", trace=result.trace, turns=result.turns,
                       tokens=result.tokens, reason=f"Matcher unavailable: {type(exc).__name__}")
    if result.status != "matcher_unavailable":
        if len(_memo) >= _MEMO_MAX:
            _memo.clear()
        _memo[memo_key] = result
    return result


# --- the basket -----------------------------------------------------------------------------


async def run(req: BasketRequest, location: Location, now: datetime | None = None
              ) -> BasketResponse:
    with using_session(f"basket-{uuid.uuid4().hex[:12]}"), tracing.span(
            "basket", "agent", input=req.model_dump(mode="json"), plz=location.plz) as sp:
        response = await _run(req, location, now or datetime.now(UTC))
        response.trace_id = tracing.trace_id(sp)
        tracing.attrs(sp, output=response.model_dump(
            mode="json", include={"single", "split", "gaps", "needs_scan"}))
        return response


async def _run(req: BasketRequest, location: Location, now: datetime) -> BasketResponse:
    day = shop_day(now)
    async with SessionLocal() as session:
        states, needs, pool = await load_pool(session, location, req, day, now)
    tracing.attrs(trace.get_current_span(), pool=len(pool.entries))
    matched = await asyncio.gather(*(match_item(item, pool) for item in req.items))
    candidates = [[optimizer.Candidate(pool.by_id[d.offer_id].store, pool.by_id[d.offer_id].offer)
                   for d in m.accepted] for m in matched]
    tops, single, split, gaps = optimizer.plan(req.items, candidates, req.max_stores, req.loyalty)
    items = [ItemResult(item=item, status=m.status, best=top,  # type: ignore[arg-type]
                        accepted=m.accepted, rejected=m.rejected, trace=m.trace,
                        reason=m.reason, turns=m.turns, tokens=m.tokens)
             for item, m, top in zip(req.items, matched, tops, strict=True)]
    return BasketResponse(location=vars(location), shop_day=day, chains=states,
                          needs_scan=needs, items=items, single=single, split=split, gaps=gaps)
