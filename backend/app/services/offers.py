"""Pure offer math shared by the structured feeds and the VLM tier.

Money is integer cents and packs are integer base units (g, ml, stk); no floats are stored.
"""
from __future__ import annotations

import math
import re
import unicodedata
from collections import defaultdict
from decimal import ROUND_HALF_UP, Decimal
from typing import TYPE_CHECKING

from app.schemas.offer import Offer, PackUnit

if TYPE_CHECKING:
    import numpy as np

# Applied after NFKC, which already turns NBSP (and its narrow and figure variants) into a space.
_CLEAN = str.maketrans({"\N{SOFT HYPHEN}": None, "\N{EN DASH}": "-", "\N{EM DASH}": "-"})
# Overprinted prices (outline plus fill) come out of a PDF text layer glued, e.g. "0.990.99".
_TWICE = re.compile(r"(?<!\d)(\d+[.,]\d{2})(?=\1(?![\d.,]))")
# Never a bare "199"; dates like 13.10. or 13.10.2026 are not prices.
_PRICE = re.compile(
    r"(?<![\w.,])(?:"
    r"\d{1,3}\.\d{3},(?:\d{2}(?![\d.,])|-(?!\d))"  # 1.299,00 1.299,-
    r"|(?:\d+|-)[.,]\d{2}(?![\d.,])"  # 1,99 1.99 -.99
    r"|\d+[.,]-(?!\d)"  # whole euros 5,- 1.-
    # 1 99 or 5.\n49: superscript cents split off as a separate 2-digit token
    r"|\d+\.?\s+\d{2}(?![\w.,])"
    r")"
)
_UNIT = r"(kg|g|ml|liter|l|stück|stk|st)\b"
_UNITS: dict[str, tuple[int, PackUnit]] = {
    "kg": (1000, "g"), "g": (1, "g"), "liter": (1000, "ml"), "l": (1000, "ml"), "ml": (1, "ml"),
    "stück": (1, "stk"), "stk": (1, "stk"), "st": (1, "stk"),
}
# "6 x 1,5 l", "2 x 12 x 1 Liter", "500-g-Packg.", "3-St.-Packg.", "4 x 57 - 59-g-Becher"; not the
# unit of a printed reference like "1 kg = 3,98". A range counts with its upper bound.
_PACK = re.compile(
    r"((?:\d+\s*[x\u00d7]\s*)*)(?:\d+(?:[.,]\d+)?\s*-\s*)?(\d+(?:[.,]\d+)?)\s*-?\s*" + _UNIT
    + r"(?!\.?\s*=)",
    re.IGNORECASE,
)
# Loose goods "je kg", "pro Stück": only when no numeric pack is given anywhere in the text.
_PER = re.compile(r"\b(?:je|pro)\s+" + _UNIT + r"(?!\.?\s*=)", re.IGNORECASE)
# The unit of a printed reference: "1 kg = 3,98", "100 g = 0,40", "(3.33 / kg)", "kg-Preis 9,99".
_REF_UNIT = re.compile(r"(?:(\d+(?:[.,]\d+)?)\s*-?\s*|\b)" + _UNIT, re.IGNORECASE)


def _norm(text: str) -> str:
    return _TWICE.sub(r"\1 ", unicodedata.normalize("NFKC", text).translate(_CLEAN))


def _cents(token: str) -> int:
    """Cents of one _PRICE match: its last two characters are the cents or ",-"."""
    euros = int(re.sub(r"\D", "", token[:-2]) or 0)
    return euros * 100 + (0 if token.endswith("-") else int(token[-2:]))


def _amount(number: str, unit: str, times: int = 1) -> tuple[int, PackUnit] | None:
    """Integer base units of a printed amount, e.g. ("1,5", "l") -> (1500, "ml").

    The comma is the decimal mark, and so is a dot, except in thousands like "1.000 g".
    """
    factor, base = _UNITS[unit.lower()]
    if factor == 1 and re.fullmatch(r"\d+\.\d{3}", number):
        number = number.replace(".", "")
    n = int((Decimal(number.replace(",", ".")) * factor * times).to_integral_value(ROUND_HALF_UP))
    return (n, base) if n > 0 else None


def parse_price_cents(value: float | str | None) -> int | None:
    """Euros as a number or a German price string ("1,99 €", "-.99", "1,-") in cents, else None."""
    if value is None:
        return None
    if isinstance(value, str):
        s = _norm(value).replace("€", "").strip()
        m = _PRICE.fullmatch("-" + s if s.startswith((".", ",")) else s)
        cents = _cents(m.group()) if m else 0
    else:
        cents = int((Decimal(str(value)) * 100).to_integral_value(ROUND_HALF_UP))
    return cents if cents > 0 else None


def parse_pack(text: str) -> tuple[int, PackUnit] | None:
    """First pack size in text as integer base units, e.g. "6 x 1,5 l" -> (9000, "ml")."""
    t = _norm(text)
    if m := _PACK.search(t):
        return _amount(m[2], m[3], math.prod(int(n) for n in re.findall(r"\d+", m[1])))
    m = _PER.search(t)
    return _amount("1", m[1]) if m else None


def reference_cents(
    price_cents: int | None, pack_qty: int | None, pack_unit: str | None
) -> int | None:
    """Cents per kg (g), per l (ml) or per piece (stk), rounded half up."""
    if not price_cents or not pack_qty or not pack_unit:
        return None
    n = price_cents * (1 if pack_unit == "stk" else 1000)
    return (2 * n + pack_qty) // (2 * pack_qty)


def _reference_off(printed: str | None, ref: int | None, unit: str | None) -> bool:
    """A printed reference (one price or a range) is more than 5% away from the computed one.

    Unreadable references, drained-weight ones and ones in another unit than the pack never count
    as off.
    """
    if not printed or ref is None or "abtropf" in printed.lower():
        return False
    text = _norm(printed)
    m = _REF_UNIT.search(text)
    amount = _amount(m[1] or "1", m[2]) if m else None
    if amount is None or amount[1] != unit:
        return False
    refs = [r for t in _PRICE.findall(text) if (r := reference_cents(_cents(t), *amount))]
    return bool(refs) and (20 * min(refs) > 21 * ref or 20 * max(refs) < 19 * ref)


def normalize(offer: Offer) -> Offer:
    """Fill pack and reference price, drop a non-higher old price, flag implausible VLM prices."""
    qty, unit = offer.pack_qty, offer.pack_unit
    if not (qty and unit):
        # The description only without a pack text: for nonfood it holds capacities and loads.
        qty, unit = parse_pack(offer.pack_text or offer.description or "") or (None, None)
    p, old, lp = offer.price_cents, offer.old_price_cents, offer.loyalty_price_cents
    ref = reference_cents(p, qty, unit)
    stale_old = p is not None and old is not None and old <= p
    update: dict[str, object] = {"pack_qty": qty, "pack_unit": unit, "reference_price_cents": ref}
    if stale_old:
        update["old_price_cents"] = None
    if offer.source == "vlm" and p is not None and (
        stale_old
        or (lp is not None and lp >= p)
        or _reference_off(offer.reference_printed, ref, unit)
    ):
        update["price_check"] = "mismatch"
    return offer.model_copy(update=update)


def price_tokens(text: str) -> int:
    """Number of price-like tokens in a page text (fewer than 3 means the page is image_only)."""
    return len(_PRICE.findall(_norm(text)))


def price_in_text(price_cents: int, text: str) -> bool:
    """The price is printed in the page text in one of the accepted forms."""
    return price_cents in {_cents(t) for t in _PRICE.findall(_norm(text))}


def merge_duplicates(
    offers: list[Offer], vectors: np.ndarray, threshold: float = 0.95
) -> list[Offer]:
    """Drop offers whose name embedding matches an earlier kept offer of the same chain and prices.

    vectors are L2-normalized rows aligned with offers, so a dot product is the cosine.
    """
    kept: dict[tuple[str, int | None, int | None], list[int]] = defaultdict(list)
    out: list[Offer] = []
    for i, o in enumerate(offers):
        same = kept[(o.chain, o.price_cents, o.loyalty_price_cents)]
        if all(float(vectors[i] @ vectors[j]) < threshold for j in same):
            same.append(i)
            out.append(o)
    return out
