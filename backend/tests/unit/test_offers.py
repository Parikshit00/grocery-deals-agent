"""Offer math: prices, packs, reference prices, VLM plausibility, price-in-text, merging."""
from __future__ import annotations

import numpy as np
import pytest

from app.schemas.offer import Offer
from app.services.offers import (
    merge_duplicates,
    normalize,
    parse_pack,
    parse_price_cents,
    price_in_text,
    price_tokens,
    reference_cents,
)

NBSP, SHY = "\N{NO-BREAK SPACE}", "\N{SOFT HYPHEN}"
EN, EM = "\N{EN DASH}", "\N{EM DASH}"

# Excerpts of real leaflet text layers: pypdf over the official PDFs in Experiments/Prospekts
# (early July 2026), untouched apart from the cut.
PENNY = (
    "GAZI \nGrill- und \nPfannenkäse\nVersch. Sorten,\nje 200 g\n(1 kg = 9.95)\n-31%\n1.49\n"
    "UVP 2.19\nMEGGLE\nKräuterbutter\nVersch. Sorten,\nje 5 x 20 g\n(1 kg = 14.90)"
)
NETTO = (
    f"Bärchenwurst\nversch. Sorten\n90 g \n(14.33 / kg)  \n \n1.{EN}\n*\n1.19\n{EN}15%\n"
    "Frisch-\nwurst-\nAuf-\nschnitt\nversch. \nSorten\n200 g \n(5.- / kg)  \n \n1.99*\n2.39\n"
    f"{EN}16%"
)
# The price is overprinted ("0.990.99"); "10\n10_D27..." is a page number and a page code.
KAUFLAND = (
    "ALMETTE \nAlpenfrischkäse\nDoppelrahmstufe, \nversch. Sorten\nje 150-g-Becher\n"
    "(1 kg = 6.60)  \n 0.990.99\n-50%\n1.99\nStall + Platz\nA\n10\n10_D27_6640_SWP1 \n"
    "*Aktionsware kann aufgrund begrenzter Vorratsmengen bereits im Laufe des ersten "
    "Angebotstages ausverkauft sein."
)
ALDI = (
    "DELIKATO\nGrillsauce\nVersch. Sorten;  \nje 250-ml-Flasche;  \nl-Preis 3.96\n0.99\n"
    "MYVAY/MEINVEGGIETAG\nVegane oder  \nvegetarische \nGrillwürstchen\nVersch. Sorten;  \n"
    "je 180- bzw. 190-g-  \nPackung; kg-Preis \n13.83 bzw. 13.11\n2.49"
)


@pytest.mark.parametrize(("value", "cents"), [
    (1.99, 199), (0.29, 29), (2, 200), ("1,99", 199), ("1.99", 199), ("1,99 €", 199),
    (f"1,99{NBSP}€", 199), ("-.99", 99), (".99", 99), (f"{EN}.99", 99), ("1.-", 100),
    ("1,-", 100), (f"1,{EN}", 100), (f"1.{EN}", 100), ("12,-", 1200),
    ("2 für 3", None), ("199", None), ("", None), (None, None), (0, None), (-1.5, None),
    ("0,00", None), ("-1,99", None), ("ab 1,99", None),
    (1.005, 101), ("1.299,00 €", 129900), ("1.299,-", 129900), ("1.299", None),
])
def test_parse_price_cents(value, cents):
    assert parse_price_cents(value) == cents


@pytest.mark.parametrize(("text", "pack"), [
    ("500 g", (500, "g")), ("500-g-Packg.", (500, "g")), ("je 500 g", (500, "g")),
    (f"500{NBSP}g", (500, "g")), ("1 kg", (1000, "g")), ("2.1 kg", (2100, "g")),
    ("2,1 kg", (2100, "g")), ("1,5 l", (1500, "ml")), ("1,5-L-FLASCHE", (1500, "ml")),
    ("750 ml", (750, "ml")), ("0,75-l-Fl.", (750, "ml")), ("0,75 Liter", (750, "ml")),
    ("6 x 1,5 l", (9000, "ml")), ("6x0,33 l", (1980, "ml")), ("6 × 0,5 l", (3000, "ml")),
    ("20 x 0,5 Liter", (10000, "ml")), ("2 x 12 x 1 Liter", (24000, "ml")),
    ("4 x 125 g", (500, "g")), ("je 5 x 20 g", (100, "g")), ("10 Stück", (10, "stk")),
    ("3-St.-Packg.", (3, "stk")), ("je Stück", (1, "stk")), ("je kg", (1000, "g")),
    ("pro l", (1000, "ml")),
    # A dot before exactly three digits groups thousands in g, ml and pieces only.
    ("je 1.000 g", (1000, "g")), ("je 2.000-g-Packung", (2000, "g")), ("Ca. 1.700 g", (1700, "g")),
    ("1.250 kg", (1250, "g")), ("1,035 kg", (1035, "g")), ("je 0,355-l-Fl.", (355, "ml")),
    ("je 180- bzw. 190-g-Packung", (190, "g")), ("je 250-g-Schale (1 kg = 7,96)", (250, "g")),
    ("1 kg = 7,96 | 250-g-Schale", (250, "g")),
    # Real Kaufland feed (store 8700, recorded 2026-10-12): a multipack of a size range.
    ("je 4 x 57 - 59-g-Becher", (236, "g")), ("je 3 x 4 - 6-St.-Packg.", (18, "stk")),
    # A per-unit phrase before the pack size does not win over it.
    ("pro Stück 0,19 (10 Stück)", (10, "stk")), ("je Stück 0,19, 10-St.-Packg.", (10, "stk")),
    ("Packg.", None), ("je Packung", None), ("2 für 3", None), ("0 g", None), ("", None),
])
def test_parse_pack(text, pack):
    assert parse_pack(text) == pack


@pytest.mark.parametrize(("price", "qty", "unit", "ref"), [
    (199, 500, "g", 398), (249, 750, "ml", 332), (99, 1500, "ml", 66), (129, 3, "stk", 43),
    (50, 3, "stk", 17), (1, 2, "stk", 1), (3, 2, "stk", 2), (199, 9000, "ml", 22),
    (1699, 1254, "g", 1355), (None, 500, "g", None), (199, None, None, None), (199, 0, "g", None),
])
def test_reference_cents(price, qty, unit, ref):
    assert reference_cents(price, qty, unit) == ref


def _o(source="vlm", **kw) -> Offer:
    price_check = "feed" if source == "feed" else "text"
    return Offer(chain="t", product_name="x", source=source, price_check=price_check, **kw)


def test_normalize_fills_pack_and_reference_from_pack_text_else_description():
    o = normalize(_o(price_cents=199, pack_text="500-g-Packg.", description="1 kg"))
    assert (o.pack_qty, o.pack_unit, o.reference_price_cents) == (500, "g", 398)
    o = normalize(_o(price_cents=299, description="je 6 x 1,5 l"))
    assert (o.pack_qty, o.pack_unit, o.reference_price_cents) == (9000, "ml", 33)
    # A pack text without a size keeps the description out (nonfood capacities, load ratings).
    o = normalize(_o(price_cents=299, pack_text="je", description="Tragkraft 175 kg"))
    assert (o.pack_qty, o.reference_price_cents) == (None, None)
    o = normalize(_o(price_cents=299, pack_qty=1000, pack_unit="g", pack_text="500 g"))
    assert (o.pack_qty, o.reference_price_cents) == (1000, 299)
    assert normalize(_o(price_cents=299)).reference_price_cents is None


def test_normalize_keeps_a_plausible_vlm_offer_and_returns_a_copy():
    offer = _o(price_cents=199, old_price_cents=249, loyalty_price_cents=179, pack_text="500 g",
               reference_printed="1 kg = 3,98")
    o = normalize(offer)
    assert (o.price_check, o.old_price_cents, o.discount_pct) == ("text", 249, 20)
    assert o is not offer and offer.reference_price_cents is None


@pytest.mark.parametrize("kw", [
    {"old_price_cents": 199}, {"old_price_cents": 149},
    {"loyalty_price_cents": 199}, {"loyalty_price_cents": 249},
    {"pack_text": "500 g", "reference_printed": "1 kg = 4,49"},
    {"pack_text": "500 g", "reference_printed": "(1 kg = 3.50 €)"},
    {"pack_text": "500 g", "reference_printed": "1 kg = 4,18"},
    {"pack_text": "500 g", "reference_printed": "1 kg = 3,78"},
    {"pack_text": "500 g", "reference_printed": "(4.49 / kg)"},
    {"pack_text": "500 g", "reference_printed": "kg-Preis 9,99"},
    {"pack_text": "500 g", "reference_printed": "1 kg = 4.50 - 5.00"},
    {"description": "je 500 g", "reference_printed": "100 g = 0,49"},
])
def test_normalize_flags_implausible_vlm_prices_never_feed(kw):
    assert normalize(_o(price_cents=199, **kw)).price_check == "mismatch"
    assert normalize(_o(source="feed", price_cents=199, **kw)).price_check == "feed"


@pytest.mark.parametrize("printed", [
    "1 kg = 3,98", "1 kg = 4,17", "1 kg = 3,79", "100 g = 0,40", "(3.98 / kg)", "kg-Preis 3,98",
    f"1 kg = 2.48 {EN} 4.96", "1 l = 9,99", "1 kg = ?", "Kilopreis 9,99", "",
    "Abtropfgewicht 180 g (1 kg = 3.84",
])
def test_normalize_reference_within_5pct_other_unit_or_unreadable_is_not_a_mismatch(printed):
    o = normalize(_o(price_cents=199, pack_text="500 g", reference_printed=printed))
    assert o.price_check == "text"


def test_normalize_drops_old_price_not_above_price():
    for o in (_o(source="feed", price_cents=199, old_price_cents=199),
              _o(price_cents=199, old_price_cents=150)):
        assert normalize(o).old_price_cents is None


# Offers on the real excerpts as a VLM would read them; each matches its printed reference.
@pytest.mark.parametrize(("text", "fields", "ref"), [
    (PENNY, {"price_cents": 149, "old_price_cents": 219, "pack_text": "je 5 x 20 g",
             "reference_printed": "(1 kg = 14.90)"}, 1490),
    (NETTO, {"price_cents": 100, "pack_text": "200 g", "reference_printed": "(5.- / kg)"}, 500),
    (KAUFLAND, {"price_cents": 99, "old_price_cents": 199, "pack_text": "je 150-g-Becher",
                "reference_printed": "(1 kg = 6.60)"}, 660),
    (ALDI, {"price_cents": 99, "pack_text": "je 250-ml-Flasche",
            "reference_printed": "l-Preis 3.96"}, 396),
    (ALDI, {"price_cents": 249, "pack_text": "je 180- bzw. 190-g-Packung",
            "reference_printed": "kg-Preis 13.83 bzw. 13.11"}, 1311),
])
def test_real_offers_verify_against_their_page_text(text, fields, ref):
    o = normalize(_o(**fields))
    assert (o.reference_price_cents, o.price_check) == (ref, "text")
    assert price_in_text(fields["price_cents"], text) and price_tokens(text) >= 3
    misread = {**fields, "price_cents": fields["price_cents"] + 100, "old_price_cents": None}
    assert normalize(_o(**misread)).price_check == "mismatch"


PAGE = f"Bio Butter\n250-g-Packg.\n1 kg = 7,96\n1 99\nMilch -.99 Eier 5,-\n2,49{NBSP}€"


@pytest.mark.parametrize(("cents", "text", "found"), [
    (199, PAGE, True), (99, PAGE, True), (500, PAGE, True), (249, PAGE, True), (796, PAGE, True),
    (299, PAGE, False), (250, PAGE, False),
    (199, "1,99", True), (199, "1.99*", True), (199, f"nur 1{NBSP}99", True),
    (199, "1\n99 €", True), (99, f"{EN}.99", True), (99, f"{EM},99", True), (500, f"5,{EN}", True),
    (100, "1.-", True), (199, f"1,{SHY}99", True), (99, "0.990.99", True),
    (199, "199", False), (199, "Art. 199 und 1 199", False), (1310, "ab 13.10. bis 18.10.", False),
    (1310, "13.10.2026", False), (100, "vom 1.-7.10.", False), (199, "11,99", False),
    (199, "1,999", False), (1010, "10\n10_D27_6640_SWP1", False),
    (100, NETTO, True), (500, NETTO, True), (1433, NETTO, True), (90, NETTO, False),
    (99, KAUFLAND, True), (1010, KAUFLAND, False), (150, KAUFLAND, False),
    # Publitas text layers split a big tag into "5." and "49" items.
    (549, "Kostüm\n\n5.\n\n49\n\n*", True), (1310, "ab 13. 10. bis", False),
    (129900, "nur 1.299,00 €", True), (129900, "1.299,-", True), (29900, "1.299,00", False),
])
def test_price_in_text(cents, text, found):
    assert price_in_text(cents, text) is found


@pytest.mark.parametrize(("text", "n"), [
    (PAGE, 5), (PENNY, 4), (NETTO, 6), (KAUFLAND, 4), (ALDI, 5),
    ("Gültig vom 13.10. bis 18.10.2026, Filiale 199, 2 für 3", 0),
])
def test_price_tokens_counts_accepted_forms_only(text, n):
    assert price_tokens(text) == n


def _m(name: str, price=199, chain="t", loyalty=None) -> Offer:
    return Offer(chain=chain, product_name=name, price_cents=price, loyalty_price_cents=loyalty,
                 source="feed", price_check="feed")


def test_merge_drops_same_price_similar_name_and_keeps_the_objects():
    offers = [_m("Bio Butter"), _m("Bio-Butter"), _m("Milch", price=99)]
    v = np.array([[1.0, 0.0], [0.999, 0.045], [0.0, 1.0]])
    merged = merge_duplicates(offers, v)
    assert [o.product_name for o in merged] == ["Bio Butter", "Milch"]
    assert merged[0] is offers[0] and merged[1] is offers[2]


@pytest.mark.parametrize(("offers", "v"), [
    ([_m("Butter", price=199), _m("Butter", price=249)], [[1.0, 0.0], [1.0, 0.0]]),
    ([_m("Butter"), _m("Milch")], [[1.0, 0.0], [0.0, 1.0]]),
    ([_m("Butter", chain="a"), _m("Butter", chain="b")], [[1.0, 0.0], [1.0, 0.0]]),
    ([_m("Milka", price=None, loyalty=79), _m("Milka", price=None, loyalty=129)],
     [[1.0, 0.0], [1.0, 0.0]]),
    ([_m("Milka", price=99, loyalty=79), _m("Milka", price=99)], [[1.0, 0.0], [1.0, 0.0]]),
])
def test_merge_keeps_different_price_loyalty_name_or_chain(offers, v):
    assert len(merge_duplicates(offers, np.array(v))) == 2


def test_merge_threshold_is_inclusive():
    v = np.array([[1.0, 0.0], [0.95, 0.31225]])
    assert len(merge_duplicates([_m("a"), _m("b")], v, threshold=0.95)) == 1


def test_merge_compares_with_kept_offers_only():
    # b is a duplicate of a (cos 15 deg); c is close to the dropped b but not to a (cos 30 deg).
    offers = [_m("a"), _m("b"), _m("c")]
    v = np.array([[1.0, 0.0], [0.9659, 0.2588], [0.866, 0.5]])
    assert [o.product_name for o in merge_duplicates(offers, v)] == ["a", "c"]
