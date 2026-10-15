"""Extraction eval: render leaflet pages, bench a VLM on them, score it against gold labels.

    python -m app.eval render [--pdf PATH --chain NAME]
    python -m app.eval bench --model NAME [--chains C ...] [--all]
    python -m app.eval score --model NAME
    python -m app.eval agreement --chain C
    python -m app.eval experiment --model NAME [--suite extraction|matcher|baskets|all]

Everything lives under the repo's eval/ dir: pages/<CHAIN>/pNNN.{jpg,txt} (gitignored, 0-based
like Experiments/pages), gold/<chain>.json, review/<chain>.json (owner spot-check exported from
label.html), recorded/<model>/<chain>.json, results/<model>.json and CHOSEN (the picked model).

`experiment` runs the suites against the live models: extraction (the gold pages through the
VLM, via bench), matcher (match_pairs.json through the basket matcher on the frozen pool.json)
and baskets (baskets.json must/must_not through the same matcher; recipes are decomposed by the
LLM first). Aggregates go to results/<model>.json (served by GET /api/evals); with Langfuse
configured, each suite is also a Langfuse dataset (items upserted by stable id) and this run a
dataset experiment whose evaluators are the same scoring functions.

Gold offers use the prompt's compact keys: {"n", "b", "p", "o", "lp", "q", "bp", "c", "cat"} with
prices in integer cents; an optional top-level "loyalty" names the chain's app/card program.
Review offers add "v" ("ok" | "wrong") and "gi" (index of the gold offer on that page; null for
an offer the reviewer added); the review keeps the gold "pages" it was made against as
"gold_pages".
"""
from __future__ import annotations

import argparse
import asyncio
import difflib
import hashlib
import importlib
import json
import math
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.services.offers import parse_pack

ROOT = Path(__file__).resolve().parents[2]
EVAL = ROOT / "eval"
PROSPEKTS = ROOT / "Experiments" / "Prospekts"
SIDE = 1024  # longest page side in px, as served to the VLM
MATCH_RATIO = 0.8  # difflib name ratio for a gold/extracted pair on the same page
GATES = {"served_price_acc": 0.98, "name_recall": 0.95, "json_validity": 0.99}
FLAG_GATE = 0.03  # max flagged (price_check mismatch) share per chain
AGREEMENT_GATE = 0.05  # max share of gold offers the owner's review disagrees with
COVERAGE_GATE = 0.2  # min share of gold offers the owner must have judged
FIELDS = ("n", "b", "p", "o", "lp", "q", "bp", "c", "cat")
PRICES = ("p", "o", "lp")
# Gold compact key -> Offer field of an extracted offer.
PRED = {
    "n": "product_name", "b": "brand", "p": "price_cents", "o": "old_price_cents",
    "lp": "loyalty_price_cents", "q": "pack_text", "cat": "category",
}

Json = dict[str, Any]


def page_file(chain: str, page: int, ext: str) -> Path:
    return EVAL / "pages" / chain.upper() / f"p{page:03d}.{ext}"


def _load(path: Path) -> Json:
    return json.loads(path.read_text(encoding="utf-8"))


def _dump(path: Path, data: Json) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")


def _pct(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, math.ceil(q * len(s)) - 1)]


def _rate(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


# --- render -------------------------------------------------------------------------------------


def render_pdf(pdf: Path, chain: str) -> int:
    """Write pNNN.jpg (SIDE px longest side) and pNNN.txt (text layer) for every page."""
    import pymupdf

    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc):
            jpg = page_file(chain, i, "jpg")
            jpg.parent.mkdir(parents=True, exist_ok=True)
            zoom = SIDE / max(page.rect.width, page.rect.height)
            pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB)
            pix.save(jpg, jpg_quality=85)
            page_file(chain, i, "txt").write_text(page.get_text(), encoding="utf-8")
        return doc.page_count


# --- bench --------------------------------------------------------------------------------------


def _round_robin(jobs: dict[str, list[int]]) -> list[tuple[str, int, int]]:
    """(chain, rank, page): page 0 of every chain, then page 1 of every chain, ..."""
    longest = max((len(p) for p in jobs.values()), default=0)
    return [(c, i, p[i]) for i in range(longest) for c, p in jobs.items() if i < len(p)]


def _gold_chains() -> list[str]:
    return sorted(p.stem for p in (EVAL / "gold").glob("*.json"))


def _loyalty(chain: str, gold: Json | None) -> str | None:
    """The gold file's "loyalty", else the chain module's LOYALTY (registered or not)."""
    if gold and gold.get("loyalty"):
        return str(gold["loyalty"])
    try:
        loyalty = getattr(importlib.import_module(f"app.agents.{chain}"), "LOYALTY", None)
    except ModuleNotFoundError:
        loyalty = None
    if loyalty is None and gold and any(
        o.get("lp") is not None for offers in gold["pages"].values() for o in offers
    ):
        raise SystemExit(f"{chain}: gold has lp prices but no loyalty program is known")
    return loyalty


def _served_model(base_url: str | None = None) -> str | None:
    """Weights the vlm (or `base_url`) server has loaded (its /models root): a label cannot lie."""
    from app.core.config import get_settings

    s = get_settings()
    try:
        r = httpx.get(f"{base_url or s.vlm_base_url}/models", timeout=10,
                      headers={"Authorization": f"Bearer {s.vllm_api_key}"})
        return str(r.json()["data"][0]["root"])
    except (httpx.HTTPError, ValueError, KeyError, IndexError):
        return None


async def bench(model: str, chains: list[str], all_pages: bool = False) -> Json:
    """Extract every gold page (or every rendered page) of `chains`, submitted round-robin.

    Each chain's recording is rewritten after every page; a rerun with the same served weights
    resumes, skipping pages already recorded (except those that raised).
    """
    from app.clients.vision import extract_page
    from app.core.config import get_settings

    served = _served_model()
    jobs: dict[str, list[int]] = {}
    loyalty: dict[str, str | None] = {}
    recorded: dict[str, Json] = {}
    for chain in chains:
        gold_file = EVAL / "gold" / f"{chain}.json"
        gold = _load(gold_file) if gold_file.exists() else None
        if all_pages:
            files = (EVAL / "pages" / chain.upper()).glob("p*.jpg")
            pages = sorted(int(f.stem[1:]) for f in files)
        else:
            pages = sorted(int(p) for p in _load(gold_file)["pages"])
        loyalty[chain] = _loyalty(chain, gold)
        out = EVAL / "recorded" / model / f"{chain}.json"
        old = _load(out) if out.exists() else {}
        recorded[chain] = {
            p: r for p, r in old.get("pages", {}).items()
            if old.get("served") == served and not (r["reason"] or "").startswith("error:")
        }
        jobs[chain] = [p for p in pages if str(p) not in recorded[chain]]
    resumed = sum(len(r) for r in recorded.values())
    sem = asyncio.Semaphore(get_settings().vlm_max_inflight)  # FIFO, so the order holds
    done: list[Json] = []
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    t0 = time.monotonic()

    def save(chain: str) -> None:
        pages = dict(sorted(recorded[chain].items(), key=lambda kv: int(kv[0])))
        _dump(EVAL / "recorded" / model / f"{chain}.json",
              {"model": model, "served": served, "chain": chain, "loyalty": loyalty[chain],
               "pages": pages})

    async def one(chain: str, rank: int, page: int) -> None:
        image = page_file(chain, page, "jpg").read_bytes()
        text = page_file(chain, page, "txt").read_text(encoding="utf-8")
        async with sem:
            try:
                r = await extract_page(
                    image, text, chain=chain, loyalty=loyalty[chain], page=page, priority=rank
                )
                rec = {
                    "ok": r.ok, "reason": r.reason, "split": getattr(r, "split", None),
                    "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "seconds": r.seconds,
                    "offers": [o.model_dump(mode="json") for o in r.offers],
                }
            except Exception as exc:  # noqa: BLE001 - one bad page must not end the bench
                rec = {"ok": False, "reason": f"error: {exc}"[:200], "split": None,
                       "tokens_in": None, "tokens_out": None, "seconds": None, "offers": []}
        recorded[chain][str(page)] = rec
        done.append(rec)
        save(chain)
        first.setdefault(chain, time.monotonic() - t0)
        last[chain] = time.monotonic() - t0

    await asyncio.gather(*(one(*job) for job in _round_robin(jobs)))
    wall = time.monotonic() - t0

    secs = [r["seconds"] for r in done if r["seconds"] is not None]
    splits = [r["split"] for r in done if r["split"] is not None]
    summary = {
        "model": model, "served": served, "pages": len(done), "resumed": resumed,
        "all": all_pages, "wall_s": round(wall, 1),
        "pages_per_min": round(60 * len(done) / wall, 1) if wall else None,
        "p50_s": _pct(secs, 0.5), "p95_s": _pct(secs, 0.95),
        "first_done_spread_s": round(max(first.values()) - min(first.values()), 1)
        if first else None,
        "failure_rate": _rate(sum(not r["ok"] for r in done), len(done)),
        # Pages still cut at the output limit after vision's half-page retry.
        "unrecovered_length_rate": _rate(
            sum("length" in (r["reason"] or "") for r in done), len(done)
        ),
        # Pages that hit the output limit at all (repetition loops); needs PageResult.split.
        "loop_rate": _rate(sum(splits), len(splits)),
        "chains": {
            c: {"pages": len(recorded[c]), "first_done_s": round(first[c], 1),
                "last_done_s": round(last[c], 1)}
            for c in recorded if c in first
        },
    }
    _dump(EVAL / "recorded" / model / "bench.json", summary)
    return summary


# --- score --------------------------------------------------------------------------------------


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().split())


def _ratio(gold: Json, pred: Json) -> float:
    """Name similarity, with and without the brand on either side: models put it anywhere."""
    gn, pn = _norm(gold.get("n")), _norm(pred.get("product_name"))
    gb = _norm(f"{gold.get('b') or ''} {gn}")
    pb = _norm(f"{pred.get('brand') or ''} {pn}")
    return max(difflib.SequenceMatcher(None, a, b).ratio() for a in (gn, gb) for b in (pn, pb))


def match(gold: list[Json], pred: list[Json]) -> list[tuple[int, int]]:
    """One-to-one (gold index, pred index) pairs, best name ratio first, equal price breaks ties."""
    cands = sorted(
        (
            (r, g.get("p") == o.get("price_cents"), gi, oi)
            for gi, g in enumerate(gold)
            for oi, o in enumerate(pred)
            if (r := _ratio(g, o)) >= MATCH_RATIO
        ),
        key=lambda c: (-c[0], not c[1], c[2], c[3]),
    )
    used_g: set[int] = set()
    used_o: set[int] = set()
    pairs = []
    for _, _, gi, oi in cands:
        if gi not in used_g and oi not in used_o:
            used_g.add(gi)
            used_o.add(oi)
            pairs.append((gi, oi))
    return sorted(pairs)


def _pack(text: str | None) -> tuple[int, str] | str | None:
    if not text:
        return None
    return parse_pack(text) or re.sub(r"\s+", "", text.lower()).replace(",", ".")


def score_chain(gold: Json, recorded: Json | None) -> tuple[Counter[str], list[int]]:
    """Counts over the gold pages of one chain, and output tokens of its recorded pages.

    A gold page without a recording counts as a failed page with no offers. Gold offers without
    a price ("PREIS FOLGT") are only counted: extraction keeps priced offers only. A price field
    is scored when gold or extraction has a value (both empty is not a hit); a gold "p" also
    matches the multi-buy total (bundle_price_cents), as gold "p" is the printed price.
    """
    c: Counter[str] = Counter()
    tokens: list[int] = []
    rec_pages = (recorded or {}).get("pages", {})
    for page, all_gold in gold["pages"].items():
        rec = rec_pages.get(str(page))
        pred = rec["offers"] if rec else []
        c["pages"] += 1
        c["ok_pages"] += bool(rec and rec["ok"])
        if rec and rec.get("tokens_out") is not None:
            tokens.append(rec["tokens_out"])
        gold_offers = [g for g in all_gold if g.get("p") is not None]
        c["gold_unpriced"] += len(all_gold) - len(gold_offers)
        c["gold"] += len(gold_offers)
        c["pred"] += len(pred)
        for o in pred:
            c[f"check_{o.get('price_check')}"] += 1
        pairs = match(gold_offers, pred)
        c["matched"] += len(pairs)
        for gi, oi in pairs:
            g, o = gold_offers[gi], pred[oi]
            served = o.get("price_check") != "mismatch"
            wrong = False
            for f in PRICES:
                gv, ov = g.get(f), o.get(PRED[f])
                if gv is None and ov is None:
                    continue
                right = gv == ov or (f == "p" and gv == o.get("bundle_price_cents"))
                wrong |= not right
                c["price_fields"] += 1
                c["price_right"] += right
                c["served_fields"] += served
                c["served_right"] += served and right
            c["wrong_priced"] += wrong
            c["wrong_flagged"] += wrong and not served
            if g.get("q"):
                c["pack"] += 1
                c["pack_right"] += _pack(g["q"]) == (
                    (o["pack_qty"], o["pack_unit"])
                    if o.get("pack_qty") and o.get("pack_unit")
                    else _pack(o.get("pack_text"))
                )
            if g.get("cat"):
                c["cat"] += 1
                c["cat_right"] += g["cat"] == o.get("category")
    return c, tokens


def rates(c: Counter[str], tokens: list[int]) -> Json:
    return {
        "price_acc": _rate(c["price_right"], c["price_fields"]),
        "served_price_acc": _rate(c["served_right"], c["served_fields"]),
        "name_recall": _rate(c["matched"], c["gold"]),
        "name_precision": _rate(c["matched"], c["pred"]),
        "pack_acc": _rate(c["pack_right"], c["pack"]),
        "category_acc": _rate(c["cat_right"], c["cat"]),
        "json_validity": _rate(c["ok_pages"], c["pages"]),
        "flagged_share": _rate(c["check_mismatch"], c["pred"]),
        "catch_rate": _rate(c["wrong_flagged"], c["wrong_priced"]),
        "image_only_share": _rate(c["check_image_only"], c["pred"]),
        "text_share": _rate(c["check_text"], c["pred"]),
        "p95_tokens_out": _pct([float(t) for t in tokens], 0.95),
        "counts": dict(sorted(c.items())),
    }


def scores(model: str) -> Json:
    """Per chain and overall (micro-averaged) metrics of recorded/<model> against every gold."""
    total: Counter[str] = Counter()
    all_tokens: list[int] = []
    chains: Json = {}
    for chain in _gold_chains():
        gold = _load(EVAL / "gold" / f"{chain}.json")
        path = EVAL / "recorded" / model / f"{chain}.json"
        c, tokens = score_chain(gold, _load(path) if path.exists() else None)
        chains[chain] = {"reviewed": gold.get("reviewed", False), **rates(c, tokens)}
        total += c
        all_tokens += tokens
    overall = rates(total, all_tokens)
    bench_file = EVAL / "recorded" / model / "bench.json"
    return {
        "model": model,
        "gates": {**GATES, "flagged_share_per_chain_max": FLAG_GATE},
        "passed": all((overall[k] or 0) >= v for k, v in GATES.items())
        and all((m["flagged_share"] or 0) <= FLAG_GATE for m in chains.values()),
        "overall": overall,
        "chains": chains,
        "bench": _load(bench_file) if bench_file.exists() else None,
    }


_COLS = {
    "price_acc": "price", "served_price_acc": "served", "name_recall": "recall",
    "name_precision": "prec", "pack_acc": "pack", "category_acc": "cat",
    "json_validity": "json", "flagged_share": "flag", "catch_rate": "catch",
    "image_only_share": "img",
}


def table(result: Json) -> str:
    rows = {**result["chains"], "overall": result["overall"]}
    lines = [f"{'chain':<10}{'pages':>6}{'gold':>6}" + "".join(f"{h:>8}" for h in _COLS.values())]
    for name, m in rows.items():
        cells = "".join("       -" if m[k] is None else f"{100 * m[k]:>7.1f}%" for k in _COLS)
        lines.append(f"{name:<10}{m['counts'].get('pages', 0):>6}{m['counts'].get('gold', 0):>6}"
                     + cells)
    gates = ", ".join(f"{k} >= {v:.0%}" for k, v in GATES.items())
    gates += f", flagged_share <= {FLAG_GATE:.0%} per chain"
    if result.get("bench"):
        lines.append(f"served weights: {result['bench'].get('served')}")
    lines.append(f"gates ({gates}): {'PASS' if result['passed'] else 'FAIL'}")
    return "\n".join(lines)


# --- agreement ----------------------------------------------------------------------------------


def agreement(gold: Json, review: Json) -> Json:
    """Share of reviewed gold offers the owner marked wrong or edited, plus offers they added.

    Passes only when the owner judged at least COVERAGE_GATE of the gold offers.
    """
    if review.get("gold_pages") not in (None, gold["pages"]):
        raise SystemExit("review is stale (the gold changed since): redo it in label.html")
    judged = wrong = added = 0
    pages = 0
    for page, offers in review["pages"].items():
        gold_page = gold["pages"].get(page, [])
        seen = False
        for o in offers:
            gi = o.get("gi")
            if gi is None:
                added += 1
                seen = True
                continue
            if o.get("v") not in ("ok", "wrong"):
                continue
            seen = True
            judged += 1
            if gi >= len(gold_page):
                raise SystemExit(f"review is stale (page {page} offer {gi} not in gold): redo it")
            g = gold_page[gi]
            wrong += o["v"] == "wrong" or any(o.get(f) != g.get(f) for f in FIELDS)
        pages += seen
    rate = _rate(wrong + added, judged + added)
    total = sum(len(v) for v in gold["pages"].values())
    return {
        "chain": gold.get("chain"), "reviewed_pages": pages, "gold_pages": len(gold["pages"]),
        "judged": judged, "wrong": wrong, "added": added, "disagreement": rate,
        "coverage": _rate(judged, total),
        "passed": rate is not None and rate <= AGREEMENT_GATE and judged >= COVERAGE_GATE * total,
    }


# --- experiment ---------------------------------------------------------------------------------

SUITES = ("extraction", "matcher", "baskets")
# Gates of the agent suites (extraction uses GATES and FLAG_GATE above).
SUITE_GATES = {"matcher": {"accuracy": 0.9}, "baskets": {"pass_rate": 0.8}}
MATCH_CONCURRENCY = 4  # matcher agents at once, so each stays inside its time budget
PAGE_SCORES = ("served_price_acc", "name_recall", "flagged_share", "json_validity")
EXTRACTION_SCORES = ("served_price_acc", "price_acc", "name_recall", "name_precision",
                     "json_validity", "flagged_share", "catch_rate")
DESCRIPTIONS = {
    "extraction": "Gold leaflet pages: the VLM's offers vs hand-checked gold offers.",
    "matcher": "Hand-labelled (query, offer) pairs: does the basket matcher accept the offer?",
    "baskets": "Shopping lists and recipes: required and forbidden products among the matches.",
}


def _item(suite: str, key: str, input: Any, expected: Any) -> Json:
    """A dataset item whose id is stable across runs and unique across datasets."""
    return {"id": f"gda-{suite}-{hashlib.sha1(key.encode()).hexdigest()[:12]}",
            "input": input, "expected": expected}


def score_page(output: Json | None, expected: list[Json]) -> Json:
    """One gold page: `output` is its recorded extraction (None = never extracted)."""
    recorded = {"pages": {"0": output}} if output else None
    r = rates(*score_chain({"pages": {"0": expected}}, recorded))
    return {k: r[k] for k in PAGE_SCORES}


def score_pair(output: Json, expected: Json) -> Json:
    """A matcher that was unavailable gets no credit, even for a true reject."""
    ok = output["status"] != "matcher_unavailable" and output["accepted"] == expected["relevant"]
    return {"correct": float(ok)}


def score_basket(output: Json, expected: Json) -> Json:
    """`output["items"]` maps item names to accepted product names. A must/must_not key names
    the items containing it; the listed words are looked for in their accepted product names
    (both case-insensitive substrings)."""
    def hit(key: str, words: list[str]) -> bool:
        return any(w.casefold() in name.casefold()
                   for item, names in output["items"].items() if key.casefold() in item.casefold()
                   for name in names for w in words)

    must = all(hit(k, v) for k, v in expected["must"].items())
    must_not = not any(hit(k, v) for k, v in expected["must_not"].items())
    return {"must": float(must), "must_not": float(must_not), "pass": float(must and must_not)}


SCORERS: dict[str, Callable[[Any, Any], Json]] = {
    "extraction": score_page, "matcher": score_pair, "baskets": score_basket}


def _mean(items: list[Json], name: str) -> float | None:
    values = [it["scores"][name] for it in items if it["scores"].get(name) is not None]
    return round(sum(values) / len(values), 4) if values else None


def _scored(suite: str, items: list[Json]) -> list[Json]:
    for it in items:
        it["scores"] = SCORERS[suite](it["output"], it["expected"])
    return items


def _gated(suite: str, items: list[Json], scores_: Json) -> Json:
    gates = SUITE_GATES[suite]
    return {"items": len(items), "scores": scores_, "gates": gates,
            "passed": all((scores_[k] or 0) >= v for k, v in gates.items())}


def extraction_summary(result: Json) -> Json:
    """The results-file entry of the extraction suite from scores()."""
    return {
        "items": result["overall"]["counts"].get("pages", 0),
        "scores": {k: result["overall"][k] for k in EXTRACTION_SCORES},
        "gates": result["gates"], "passed": result["passed"],
        "chains": {c: {k: m[k] for k in EXTRACTION_SCORES} for c, m in result["chains"].items()},
        "bench": result["bench"],
    }


def matcher_summary(items: list[Json]) -> Json:
    n: Counter[str] = Counter()
    for it in items:
        acc, rel = it["output"]["accepted"], it["expected"]["relevant"]
        n["tp" if acc and rel else "fp" if acc else "fn" if rel else "tn"] += 1
        n["unavailable"] += it["output"]["status"] == "matcher_unavailable"
    return _gated("matcher", items, {
        "accuracy": _mean(items, "correct"),
        "precision": _rate(n["tp"], n["tp"] + n["fp"]),
        "recall": _rate(n["tp"], n["tp"] + n["fn"]),
        "unavailable_share": _rate(n["unavailable"], len(items)),
    })


def baskets_summary(items: list[Json]) -> Json:
    return _gated("baskets", items, {"pass_rate": _mean(items, "pass"),
                                     "must_rate": _mean(items, "must"),
                                     "must_not_rate": _mean(items, "must_not")})


async def run_extraction(model: str) -> tuple[list[Json], Json]:
    """Bench the gold pages (resumes recorded ones), then one item per gold page."""
    chains = _gold_chains()
    await bench(model, chains)
    items = []
    for chain in chains:
        gold = _load(EVAL / "gold" / f"{chain}.json")
        path = EVAL / "recorded" / model / f"{chain}.json"
        recorded = _load(path)["pages"] if path.exists() else {}
        for page, offers in gold["pages"].items():
            it = _item("extraction", f"{chain}:{page}", {"chain": chain, "page": int(page)},
                       offers)
            items.append({**it, "output": recorded.get(page)})
    return _scored("extraction", items), extraction_summary(scores(model))


def eval_pool() -> Any:
    """The frozen offer pool of eval/pool.json as a basket Pool (embeds every product name)."""
    from app.clients.embeddings import embed
    from app.schemas.basket import PlanStore
    from app.schemas.offer import Offer
    from app.services import basket

    data = _load(EVAL / "pool.json")
    entries = [
        basket.Entry(f"o{i}", PlanStore(chain=o["chain"], store=o["edition"], km=None),
                     Offer.model_validate({**o, "source": "feed", "price_check": "feed"}))
        for i, o in enumerate(data["offers"])
    ]
    vectors = embed([e.offer.product_name for e in entries])
    return basket.Pool(entries, vectors, f"eval-pool:{data['recorded_on']}")


async def _match(names: set[str], pool: Any) -> dict[str, Json]:
    """Matcher status and accepted (chain, product name) pairs per item name."""
    from app.schemas.basket import Item
    from app.services import basket

    sem = asyncio.Semaphore(MATCH_CONCURRENCY)

    async def one(name: str) -> tuple[str, Json]:
        async with sem:
            m = await basket.match_item(Item(name=name), pool)
        offers = [pool.by_id[d.offer_id] for d in m.accepted]
        return name, {"status": m.status,
                      "accepted": [(e.store.chain, e.offer.product_name) for e in offers]}

    return dict(await asyncio.gather(*(one(n) for n in sorted(names))))


async def run_matcher(pool: Any) -> tuple[list[Json], Json]:
    pairs = _load(EVAL / "match_pairs.json")["pairs"]
    matched = await _match({p["query"] for p in pairs}, pool)
    items = []
    for p in pairs:
        m = matched[p["query"]]
        it = _item("matcher", f"{p['query']}|{p['chain']}|{p['offer_name']}",
                   {"query": p["query"], "chain": p["chain"], "offer": p["offer_name"]},
                   {"relevant": p["relevant"]})
        accepted = (p["chain"], p["offer_name"]) in m["accepted"]
        items.append({**it, "output": {"accepted": accepted, "status": m["status"]}})
    items = _scored("matcher", items)
    return items, matcher_summary(items)


async def run_baskets(pool: Any) -> tuple[list[Json], Json]:
    """Comma lists are split into items; a basket with servings is a recipe for the LLM."""
    from app.clients.llm import decompose_recipe

    specs = _load(EVAL / "baskets.json")["baskets"]

    async def names(spec: Json) -> list[str]:
        if spec.get("servings"):
            return [i.name for i in await decompose_recipe(spec["query"], spec["servings"])]
        return [s.strip() for s in spec["query"].split(",") if s.strip()]

    lists = await asyncio.gather(*(names(s) for s in specs))
    matched = await _match({n for ns in lists for n in ns}, pool)
    items = []
    for spec, ns in zip(specs, lists, strict=True):
        it = _item("baskets", spec["query"],
                   {"query": spec["query"], "servings": spec.get("servings")},
                   {"must": spec["must"], "must_not": spec["must_not"]})
        out = {"items": {n: [name for _, name in matched[n]["accepted"]] for n in ns},
               "unavailable": [n for n in ns if matched[n]["status"] == "matcher_unavailable"]}
        items.append({**it, "output": out})
    items = _scored("baskets", items)
    return items, baskets_summary(items)


async def run_suites(model: str, suites: list[str]) -> dict[str, tuple[list[Json], Json]]:
    """Extraction (VLM) runs alongside the matcher suites (LLM): different GPUs."""
    extraction = (asyncio.create_task(run_extraction(model))
                  if "extraction" in suites else None)
    out = {}
    if {"matcher", "baskets"} & set(suites):
        pool = await asyncio.to_thread(eval_pool)
        if "matcher" in suites:
            out["matcher"] = await run_matcher(pool)
        if "baskets" in suites:  # after the matcher: repeated items reuse its memo
            out["baskets"] = await run_baskets(pool)
    if extraction:
        out["extraction"] = await extraction
    return out


def _langfuse() -> Any:
    """A Langfuse client from the LANGFUSE_* settings, or None (with the reason printed).

    Never falls back to the SDK's default cloud host: no base URL configured means no upload.
    """
    from app.core.config import get_settings

    s = get_settings()
    host, public, secret = s.langfuse_base_url, s.langfuse_public_key, s.langfuse_secret_key
    if not (host and public and secret):
        print("langfuse: LANGFUSE_BASE_URL / PUBLIC_KEY / SECRET_KEY not set: results file only")
        return None
    from langfuse import Langfuse

    client = Langfuse(public_key=public, secret_key=secret, base_url=host)
    try:
        client.auth_check()
    except Exception as exc:  # noqa: BLE001 - any failure here just skips the upload
        print(f"langfuse: {host} unusable ({type(exc).__name__}: {exc}): results file only")
        return None
    return client


def publish(client: Any, model: str, suite: str, items: list[Json], summary: Json,
            stamp: str) -> str | None:
    """Upsert the suite's dataset items (only new or changed ones), then record this run as a
    dataset experiment: the task returns the outputs computed above and the evaluators are the
    suite's scoring function. Returns the experiment URL."""
    from langfuse import Evaluation

    name = f"gda-{suite}"
    try:
        dataset = client.get_dataset(name)
    except Exception:  # noqa: BLE001 - the SDK raises its NotFoundError: create the dataset
        client.create_dataset(name=name, description=DESCRIPTIONS[suite])
        dataset = client.get_dataset(name)
    have = {i.id: i for i in dataset.items}
    changed = [it for it in items if it["id"] not in have
               or (have[it["id"]].input, have[it["id"]].expected_output)
               != (it["input"], it["expected"])]
    for it in changed:
        client.create_dataset_item(dataset_name=name, id=it["id"], input=it["input"],
                                   expected_output=it["expected"])
    if changed:
        dataset = client.get_dataset(name)
    by_id = {it["id"]: it for it in items}

    def task(*, item: Any, **_: Any) -> Any:
        return by_id[item.id]["output"]

    def evaluator(*, output: Any, expected_output: Any, **_: Any) -> list[Any]:
        return [Evaluation(name=k, value=v)
                for k, v in SCORERS[suite](output, expected_output).items() if v is not None]

    def aggregate(**_: Any) -> list[Any]:
        return [Evaluation(name=k, value=v) for k, v in summary["scores"].items()
                if isinstance(v, int | float)]

    result = client.run_experiment(
        name=f"{suite}: {model}", run_name=f"{model} {stamp}",
        description=f"{DESCRIPTIONS[suite]} Outputs of python -m app.eval experiment.",
        data=[i for i in dataset.items if i.id in by_id], task=task,
        evaluators=[evaluator], run_evaluators=[aggregate],
        metadata={"model": model, "suite": suite})
    return result.experiment_url


def write_results(model: str, summaries: Json, stamp: str, served: Json | None = None,
                  urls: Json | None = None) -> Json:
    """Merge suite summaries into results/<model>.json (suites not rerun are kept)."""
    path = EVAL / "results" / f"{model}.json"
    old = _load(path) if path.exists() else {}
    suites = {**old.get("suites", {}), **summaries}
    result = {
        "model": model, "timestamp": stamp,
        "served": {**old.get("served", {}), **(served or {})},
        "passed": all(s["passed"] for s in suites.values()),
        "suites": suites,
        "langfuse": {**old.get("langfuse", {}), **(urls or {})},
    }
    _dump(path, result)
    return result


def experiment(model: str, suites: list[str]) -> Json:
    from app.core.config import get_settings

    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    served = {"llm": _served_model(get_settings().llm_base_url), "vlm": _served_model()}
    done = asyncio.run(run_suites(model, suites))
    client = _langfuse()
    urls: Json = {}
    for suite, (items, summary) in done.items():
        if client is None:
            break
        try:
            urls[suite] = publish(client, model, suite, items, summary, stamp)
        except Exception as exc:  # noqa: BLE001 - the results file is written regardless
            print(f"langfuse: {suite} upload failed ({type(exc).__name__}: {exc})")
    if client is not None:
        client.flush()
    return write_results(model, {s: summary for s, (_, summary) in done.items()}, stamp,
                         served, urls)


# --- cli ----------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m app.eval")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("render", help="render Experiments/Prospekts (or --pdf) to eval/pages")
    r.add_argument("--pdf", type=Path)
    r.add_argument("--chain")
    b = sub.add_parser("bench", help="run the VLM over gold pages, record outputs")
    b.add_argument("--model", required=True, help="label for eval/recorded/<model>")
    b.add_argument("--chains", nargs="+")
    b.add_argument("--all", action="store_true", help="every rendered page (throughput)")
    s = sub.add_parser("score", help="score recorded outputs against gold")
    s.add_argument("--model", required=True)
    a = sub.add_parser("agreement", help="gold vs the owner's review")
    a.add_argument("--chain", required=True)
    e = sub.add_parser("experiment", help="run suites live; results file + Langfuse experiment")
    e.add_argument("--model", required=True, help="label, e.g. the served model's name")
    e.add_argument("--suite", choices=[*SUITES, "all"], default="all")
    args = ap.parse_args(argv)

    if args.cmd == "render":
        if bool(args.pdf) != bool(args.chain):
            ap.error("--pdf and --chain go together")
        todo = [(args.pdf, args.chain)] if args.pdf else [
            (pdf, pdf.stem.lower()) for pdf in sorted(PROSPEKTS.glob("*.pdf"))
        ]
        for pdf, chain in todo:
            print(f"{chain}: {render_pdf(pdf, chain)} pages")
        return 0
    if args.cmd == "bench":
        if args.chains:
            chains = args.chains
        elif args.all:
            chains = sorted(d.name.lower() for d in (EVAL / "pages").iterdir() if d.is_dir())
        else:
            chains = _gold_chains()
        print(json.dumps(asyncio.run(bench(args.model, chains, args.all)), indent=1))
        return 0
    if args.cmd == "score":
        result = scores(args.model)
        write_results(args.model, {"extraction": extraction_summary(result)},
                      datetime.now(UTC).isoformat(timespec="seconds"))
        print(table(result))
        return 0 if result["passed"] else 1
    if args.cmd == "experiment":
        out = experiment(args.model, list(SUITES) if args.suite == "all" else [args.suite])
        for suite, s in out["suites"].items():
            print(f"{suite:<11}{'PASS' if s['passed'] else 'FAIL'}  {json.dumps(s['scores'])}")
        for suite, url in out["langfuse"].items():
            print(f"langfuse {suite}: {url}")
        return 0 if out["passed"] else 1
    result = agreement(
        _load(EVAL / "gold" / f"{args.chain}.json"), _load(EVAL / "review" / f"{args.chain}.json")
    )
    print(json.dumps(result, indent=1))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
