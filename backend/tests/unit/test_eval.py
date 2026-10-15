"""Offline tests of the eval harness: matching, metric math, agreement, bench order, render."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app import eval as ev
from app.clients import vision
from app.schemas.offer import Offer


def _g(n, p=199, **kw):
    return {"n": n, "b": None, "p": p, "o": None, "lp": None, "q": None, "bp": None, "c": None,
            "cat": None, **kw}


def _o(name, p=199, check="text", **kw):
    return Offer(chain="t", product_name=name, price_cents=p, source="vlm", price_check=check,
                 **kw).model_dump(mode="json")


def _rec(*offers, ok=True, tokens_out=100):
    return {"ok": ok, "reason": None if ok else "length", "tokens_in": 900,
            "tokens_out": tokens_out, "seconds": 2.0, "offers": list(offers)}


def test_match_is_one_to_one_above_the_ratio_and_prefers_equal_price():
    gold = [_g("Deutsche Markenbutter", 239), _g("Bio Eier", 299)]
    pred = [_o("Bio-Eier", 299), _o("Deutsche Markenbutter", 199), _o("Deutsche Markenbutter")]
    pred[2]["price_cents"] = 239
    assert ev.match(gold, pred) == [(0, 2), (1, 0)]
    assert ev.match([_g("Butter")], [_o("Butterkekse")]) == []


def test_match_accepts_the_brand_in_either_field():
    gold = [_g("Joghurt mild", b="Weihenstephan")]
    assert ev.match(gold, [_o("Weihenstephan Joghurt mild")]) == [(0, 0)]
    assert ev.match(gold, [_o("Weihenstephan Joghurt mild", brand="Weihenstephan")]) == [(0, 0)]


def test_score_chain_price_fields_served_and_catch_rate():
    gold = {"pages": {"3": [_g("Milch", 99, o=129), _g("Kaffee", 499), _g("Brot", 149)]}}
    rec = {"pages": {"3": _rec(
        _o("Milch", 99, old_price_cents=129),  # p, o right
        _o("Kaffee", 449, check="mismatch"),  # p wrong, flagged (not served)
        _o("Brot", 159),  # p wrong, served
        _o("Gurke", 49),  # unmatched
    )}}
    c, tokens = ev.score_chain(gold, rec)
    m = ev.rates(c, tokens)
    # Both-empty o/lp fields are not counted: Milch p+o, Kaffee p, Brot p.
    assert (c["price_fields"], c["price_right"]) == (4, 2)
    assert (c["served_fields"], c["served_right"]) == (3, 2)
    assert m["name_recall"] == 1.0 and m["name_precision"] == 0.75
    assert m["catch_rate"] == 0.5 and m["flagged_share"] == 0.25 and m["text_share"] == 0.75
    assert m["json_validity"] == 1.0 and m["p95_tokens_out"] == 100


def test_score_chain_skips_unpriced_gold_and_accepts_the_bundle_total():
    gold = {"pages": {"0": [_g("Milch"), _g("Neuheit", None), _g("Kitkat", 300, c="2 fuer 3")]}}
    pred = [_o("Milch"), _o("Kitkat", 150, min_qty=2, bundle_price_cents=300)]
    c, _ = ev.score_chain(gold, {"pages": {"0": _rec(*pred)}})
    m = ev.rates(c, [])
    assert m["name_recall"] == 1.0 and m["price_acc"] == 1.0 and c["gold_unpriced"] == 1


def test_score_chain_hallucinated_old_price_is_wrong():
    c, _ = ev.score_chain({"pages": {"0": [_g("Milch", 99)]}},
                          {"pages": {"0": _rec(_o("Milch", 99, old_price_cents=99))}})
    assert (c["price_fields"], c["price_right"]) == (2, 1)


def test_score_chain_pack_and_category():
    gold = {"pages": {"0": [_g("Hack", q="500 g", cat="food"), _g("Cola", q="6 x 1,5 l",
                                                                    cat="drinks")]}}
    pred = [_o("Hack", pack_text="500g-Packung", category="food"),
            _o("Cola", pack_qty=9000, pack_unit="ml", category="food")]
    c, _ = ev.score_chain(gold, {"pages": {"0": _rec(*pred)}})
    assert (c["pack"], c["pack_right"], c["cat"], c["cat_right"]) == (2, 2, 2, 1)


def test_score_chain_missing_or_failed_pages_count_against_json_validity():
    gold = {"pages": {"0": [_g("A")], "1": [_g("B")], "2": []}}
    c, _ = ev.score_chain(gold, {"pages": {"0": _rec(_o("A")), "1": _rec(ok=False)}})
    assert ev.rates(c, [])["json_validity"] == pytest.approx(1 / 3, abs=1e-4)
    assert ev.rates(c, [])["name_recall"] == 0.5


def test_scores_and_gates_over_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "EVAL", tmp_path)
    (tmp_path / "gold").mkdir()
    (tmp_path / "recorded" / "m").mkdir(parents=True)
    gold = {"chain": "a", "reviewed": True, "pages": {"0": [_g("Milch"), _g("Brot", 149)]}}
    (tmp_path / "gold" / "a.json").write_text(json.dumps(gold))
    rec = {"pages": {"0": _rec(_o("Milch"), _o("Brot", 149))}}
    (tmp_path / "recorded" / "m" / "a.json").write_text(json.dumps(rec))
    result = ev.scores("m")
    assert result["passed"] and result["chains"]["a"]["reviewed"]
    assert "PASS" in ev.table(result)
    rec["pages"]["0"]["offers"][1]["price_check"] = "mismatch"  # 50% flagged, prices still right
    (tmp_path / "recorded" / "m" / "a.json").write_text(json.dumps(rec))
    result = ev.scores("m")
    assert result["overall"]["served_price_acc"] == 1.0 and not result["passed"]
    (tmp_path / "recorded" / "m" / "a.json").write_text(json.dumps(
        {"pages": {"0": _rec(_o("Milch"), _o("Brot", 149))}}))
    (tmp_path / "gold" / "b.json").write_text(json.dumps(gold))  # gold chain never benched
    assert not ev.scores("m")["passed"]


def test_agreement_counts_wrong_edited_and_added_offers():
    gold = {"chain": "a", "pages": {"0": [_g("A"), _g("B"), _g("C")], "1": [_g("D")]}}
    review = {"pages": {"0": [
        {**_g("A"), "gi": 0, "v": "ok"},
        {**_g("B", 189), "gi": 1, "v": "ok"},  # edited price
        {**_g("C"), "gi": 2, "v": "wrong"},
        {**_g("E"), "gi": None, "v": "ok"},  # added
    ], "1": [{**_g("D"), "gi": 0}]}}  # not judged
    r = ev.agreement(gold, review)
    assert (r["judged"], r["wrong"], r["added"], r["reviewed_pages"]) == (3, 2, 1, 1)
    assert r["disagreement"] == 0.75 and not r["passed"]
    one = {"pages": {"0": [{**_g("A"), "gi": 0, "v": "ok"}]}}  # 25% coverage
    assert ev.agreement(gold, one)["passed"]
    assert not ev.agreement({**gold, "pages": {**gold["pages"], "2": [_g("F")] * 2}}, one)["passed"]
    with pytest.raises(SystemExit, match="stale"):
        ev.agreement(gold, {**one, "gold_pages": {"0": [_g("X")]}})
    with pytest.raises(SystemExit, match="stale"):
        ev.agreement(gold, {"pages": {"1": [{**_g("D"), "gi": 3, "v": "ok"}]}})


def test_round_robin_and_percentile():
    assert ev._round_robin({"a": [0, 5, 9], "b": [2]}) == [
        ("a", 0, 0), ("b", 0, 2), ("a", 1, 5), ("a", 2, 9)]
    assert ev._pct([1, 2, 3, 4], 0.5) == 2 and ev._pct([1.0] * 19 + [9.0], 0.95) == 1.0
    assert ev._pct([], 0.5) is None


def test_bench_records_pages_round_robin(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "EVAL", tmp_path)
    (tmp_path / "gold").mkdir()
    for chain, pages in (("a", ["0", "4"]), ("b", ["1"])):
        (tmp_path / "gold" / f"{chain}.json").write_text(
            json.dumps({"chain": chain, "pages": {p: [] for p in pages}}))
        for p in pages:
            ev.page_file(chain, int(p), "jpg").parent.mkdir(parents=True, exist_ok=True)
            ev.page_file(chain, int(p), "jpg").write_bytes(b"jpg")
            ev.page_file(chain, int(p), "txt").write_text("Milch 0,99")
    calls = []

    async def fake(image, text, *, chain, loyalty, page, priority=0):
        calls.append((chain, page, priority))
        if page == 4:
            raise RuntimeError("boom")
        offer = Offer(chain=chain, product_name="Milch", price_cents=99, source="vlm",
                      price_check="text")
        return SimpleNamespace(ok=page != 1, offers=[offer], reason=None if page != 1 else
                               "length", tokens_in=700, tokens_out=50, seconds=1.5)

    monkeypatch.setattr(vision, "extract_page", fake, raising=False)
    monkeypatch.setattr(ev, "_served_model", lambda: "/models/x")
    summary = asyncio.run(ev.bench("m", ["a", "b"]))
    assert calls == [("a", 0, 0), ("b", 1, 0), ("a", 4, 1)]
    rec = json.loads((tmp_path / "recorded" / "m" / "a.json").read_text())
    assert rec["served"] == "/models/x" and rec["loyalty"] is None
    assert rec["pages"]["0"]["offers"][0]["price_cents"] == 99
    assert rec["pages"]["4"]["reason"] == "error: boom" and not rec["pages"]["4"]["ok"]
    assert summary["pages"] == 3 and summary["failure_rate"] == pytest.approx(0.6667)
    assert summary["unrecovered_length_rate"] == pytest.approx(0.3333)
    assert summary["p50_s"] == 1.5 and summary["loop_rate"] is None
    calls.clear()
    summary = asyncio.run(ev.bench("m", ["a", "b"]))  # resume: only the page that raised
    assert calls == [("a", 4, 0)] and summary["resumed"] == 2


def test_loyalty_from_gold_or_chain_module():
    lp_gold = {"pages": {"0": [_g("Milch", lp=79)]}}
    assert ev._loyalty("x", {**lp_gold, "loyalty": "X App"}) == "X App"
    assert ev._loyalty("netto", None) == "Netto plus App / PAYBACK"  # unregistered module
    assert ev._loyalty("nochain", {"pages": {"0": [_g("Milch")]}}) is None
    with pytest.raises(SystemExit, match="lp prices"):
        ev._loyalty("nochain", lp_gold)


def test_render_writes_jpg_and_text(tmp_path, monkeypatch):
    pymupdf = pytest.importorskip("pymupdf")
    monkeypatch.setattr(ev, "EVAL", tmp_path)
    doc = pymupdf.open()
    doc.new_page(width=595, height=842).insert_text((72, 72), "Butter 1,99")
    doc.new_page()
    doc.save(tmp_path / "x.pdf")
    assert ev.render_pdf(tmp_path / "x.pdf", "lidl") == 2
    assert "Butter 1,99" in ev.page_file("lidl", 0, "txt").read_text()
    pix = pymupdf.Pixmap(str(ev.page_file("lidl", 0, "jpg")))
    assert max(pix.width, pix.height) == ev.SIDE


# --- experiment suites --------------------------------------------------------------------------


def test_score_page_pair_and_basket():
    page = ev.score_page(_rec(_o("Milch"), _o("Brot", 150, check="mismatch")),
                         [_g("Milch"), _g("Brot", 149)])
    assert page == {"served_price_acc": 1.0, "name_recall": 1.0, "flagged_share": 0.5,
                    "json_validity": 1.0}
    assert ev.score_page(None, [_g("Milch")])["json_validity"] == 0.0
    assert ev.score_pair({"accepted": False, "status": "no_offer"}, {"relevant": False}) == {
        "correct": 1.0}
    assert ev.score_pair({"accepted": False, "status": "matcher_unavailable"},
                         {"relevant": False}) == {"correct": 0.0}
    spec = {"must": {"Butter": ["Butter"], "Milch": ["H-Milch"]},
            "must_not": {"Butter": ["Erdnussbutter"], "Eier": ["Bueno"]}}
    good = {"items": {"butter": ["BERCHTESGADENER Bergbauernbutter"], "Milch": ["Frische H-MILCH"],
                      "Eier": []}}
    assert ev.score_basket(good, spec) == {"must": 1.0, "must_not": 1.0, "pass": 1.0}
    bad = {"items": {**good["items"], "Butter": ["Erdnussbutter"], "Milch": []}}
    assert ev.score_basket(bad, spec) == {"must": 0.0, "must_not": 0.0, "pass": 0.0}


def test_run_matcher_and_baskets_over_a_fake_pool(tmp_path, monkeypatch):
    from app.services import basket

    monkeypatch.setattr(ev, "EVAL", tmp_path)
    (tmp_path / "match_pairs.json").write_text(json.dumps({"pairs": [
        {"query": "Butter", "offer_name": "Butter", "chain": "k", "relevant": True},
        {"query": "Butter", "offer_name": "Erdnussbutter", "chain": "k", "relevant": False},
        {"query": "Kaffee", "offer_name": "Kaffee", "chain": "p", "relevant": True}]}))
    (tmp_path / "baskets.json").write_text(json.dumps({"baskets": [
        {"query": "Butter, Kaffee", "must": {"Butter": ["Butter"]},
         "must_not": {"Butter": ["Erdnuss"]}},
        {"query": "Kuchen", "servings": 4, "must": {"Mehl": ["Mehl"]}, "must_not": {}}]}))
    entries = {"o0": SimpleNamespace(store=SimpleNamespace(chain="k"),
                                     offer=SimpleNamespace(product_name="Butter"))}
    pool = SimpleNamespace(by_id=entries)

    async def match_item(item, pool):
        if item.name == "Kaffee":
            return basket.Matched(status="matcher_unavailable")
        accepted = [basket.Decision(chain="k", offer_id="o0", name="Butter")]
        return basket.Matched(status="matched" if item.name == "Butter" else "no_offer",
                              accepted=accepted if item.name == "Butter" else [])

    async def decompose(text, servings):
        return [basket.Item(name="Mehl")]

    monkeypatch.setattr(basket, "match_item", match_item)
    monkeypatch.setattr("app.clients.llm.decompose_recipe", decompose)
    items, summary = asyncio.run(ev.run_matcher(pool))
    assert [it["scores"]["correct"] for it in items] == [1.0, 1.0, 0.0]
    assert summary["scores"] == {"accuracy": 0.6667, "precision": 1.0, "recall": 0.5,
                                 "unavailable_share": 0.3333}
    assert not summary["passed"] and len({it["id"] for it in items}) == 3
    items, summary = asyncio.run(ev.run_baskets(pool))
    assert items[0]["output"] == {"items": {"Butter": ["Butter"], "Kaffee": []},
                                  "unavailable": ["Kaffee"]}
    assert [it["scores"]["pass"] for it in items] == [1.0, 0.0]
    assert summary["scores"]["pass_rate"] == 0.5 and summary["items"] == 2


def test_write_results_merges_suites_and_experiment_without_langfuse(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, "EVAL", tmp_path)
    for var in ("LANGFUSE_BASE_URL", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
        monkeypatch.delenv(var, raising=False)
    ok = {"items": 3, "scores": {"accuracy": 1.0}, "gates": {"accuracy": 0.9}, "passed": True}
    ev.write_results("m", {"matcher": ok}, "t0", {"llm": "/models/x"}, {"matcher": "u"})

    async def suites(model, names):
        return {"baskets": ([], {**ok, "passed": False})}

    monkeypatch.setattr(ev, "run_suites", suites)
    monkeypatch.setattr(ev, "_served_model", lambda base_url=None: "/models/y")
    result = ev.experiment("m", ["baskets"])
    assert result == json.loads((tmp_path / "results" / "m.json").read_text())
    assert set(result["suites"]) == {"matcher", "baskets"} and not result["passed"]
    assert result["langfuse"] == {"matcher": "u"} and result["served"]["llm"] == "/models/y"
    assert result["model"] == "m" and result["timestamp"] != "t0"
