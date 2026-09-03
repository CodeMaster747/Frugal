"""Turning a receipt line into something comparable across receipts.

"AMUL TAAZA 500ML", "Amul Taaza Milk 500 ml" and "AMUL TAAZA MILK 0.5L" are the
same product at three shops, and a price graph that cannot see that compares
nothing. This module is the first and cheapest layer of making them one thing.

**Pure**: no session, no models, no sibling modules, held by the
`the-pricegraph-normalizer-is-pure` contract. That is what lets a thousand real
grocery strings be exercised in one fast test, which is the only way to have any
confidence in rules like these.

**No LLM** (ADR-005). And deliberately not the TF-IDF classifier either: that
maps a string onto one of 23 *fixed* categories, while this is open-vocabulary
clustering over a label set that grows with every new product. The classifier is
the right tool for assigning a category to a canonical item once one exists, and
the wrong tool for deciding whether two strings are the same item.

The rule that matters most is at the bottom: **pack size is part of identity.**
"AMUL TAAZA 500ML" and "AMUL TAAZA 1L" have trigram similarity around 0.93 and
are a different price. Blocking on (brand, size, unit) before any similarity is
computed is what stops similarity alone from corrupting every comparison.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal

#: Bumped when the rules below change, so a stored alias records which version
#: produced it and a bad rule is retractable without guessing at dates.
NORMALIZER_VERSION = 1

#: Canonical bases. Everything reduces to one of these three, so `0.5 L` and
#: `500 ML` become the same magnitude on the same unit.
UNIT_BASES: dict[str, tuple[str, Decimal]] = {
    "ml": ("ml", Decimal(1)),
    "l": ("ml", Decimal(1000)),
    "ltr": ("ml", Decimal(1000)),
    "litre": ("ml", Decimal(1000)),
    "liter": ("ml", Decimal(1000)),
    "g": ("g", Decimal(1)),
    "gm": ("g", Decimal(1)),
    "gms": ("g", Decimal(1)),
    "gram": ("g", Decimal(1)),
    "grams": ("g", Decimal(1)),
    "kg": ("g", Decimal(1000)),
    "kgs": ("g", Decimal(1000)),
    "pc": ("pc", Decimal(1)),
    "pcs": ("pc", Decimal(1)),
    "piece": ("pc", Decimal(1)),
    "pieces": ("pc", Decimal(1)),
    "pkt": ("pc", Decimal(1)),
    "pack": ("pc", Decimal(1)),
    "n": ("pc", Decimal(1)),
    "no": ("pc", Decimal(1)),
    "nos": ("pc", Decimal(1)),
}

PACK = re.compile(
    r"(?<![a-z0-9.])(\d+(?:\.\d+)?)\s*("
    + "|".join(sorted(UNIT_BASES, key=len, reverse=True))
    + r")(?![a-z])",
    re.I,
)

#: HSN/SAC codes and long numeric runs are SKU/barcode noise, never identity.
HSN = re.compile(r"\bhsn\s*:?\s*\d+\b|\bsac\s*:?\s*\d+\b", re.I)
LONG_DIGITS = re.compile(r"\b\d{4,}\b")

#: Words that appear on receipts and say nothing about what the item is.
STOPWORDS = frozenset(
    (
        "fresh",
        "new",
        "offer",
        "mrp",
        "combo",
        "save",
        "rs",
        "inr",
        "qty",
        "each",
        "item",
        "product",
        "special",
        "value",
        "pack",
        "packet",
        "free",
        "discount",
        "off",
        "total",
        "price",
        "amt",
        "amount",
    )
)

#: Indian FMCG brands, longest-match-first at use. Abbreviations map onto the
#: canonical form, because "parle g", "parle-g" and "parleg" are one brand and
#: trigram similarity will not reliably say so.
#: Spelling variants of *the same brand*, and nothing else.
#:
#: An earlier version mapped "amul taaza" -> "amul", which looked like a
#: tidy-up and was a bug: Amul Taaza and Amul Gold are different milks at
#: different prices, and folding the variant into the brand made them one
#: product. A brand alias may only ever collapse spelling; a variant name
#: belongs in the head noun, where it stays part of the identity.
BRAND_ALIASES: dict[str, str] = {
    "parle g": "parle-g",
    "parleg": "parle-g",
    "parle-g": "parle-g",
    "mother dairy": "mother-dairy",
    "motherdairy": "mother-dairy",
    "coca cola": "coca-cola",
    "thums up": "thums-up",
    "red label": "red-label",
    "taj mahal": "taj-mahal",
    "wagh bakri": "wagh-bakri",
    "dairy milk": "dairymilk",
}

BRANDS: frozenset[str] = frozenset(
    (
        "amul",
        "britannia",
        "parle",
        "parle-g",
        "nestle",
        "maggi",
        "tata",
        "aashirvaad",
        "fortune",
        "saffola",
        "dabur",
        "patanjali",
        "himalaya",
        "colgate",
        "dettol",
        "lifebuoy",
        "lux",
        "surf",
        "ariel",
        "rin",
        "vim",
        "harpic",
        "lizol",
        "mother-dairy",
        "nandini",
        "heritage",
        "haldiram",
        "bikano",
        "everest",
        "mdh",
        "catch",
        "kissan",
        "sunfeast",
        "bingo",
        "lays",
        "kurkure",
        "bourbon",
        "oreo",
        "cadbury",
        "dairymilk",
        "horlicks",
        "bournvita",
        "boost",
        "complan",
        "pediasure",
        "red-label",
        "taj-mahal",
        "society",
        "wagh-bakri",
        "bru",
        "nescafe",
        "tropicana",
        "real",
        "paperboat",
        "frooti",
        "maaza",
        "slice",
        "thums-up",
        "sprite",
        "limca",
        "pepsi",
        "coca-cola",
        "bisleri",
        "kinley",
        "aquafina",
    )
)

_WHITESPACE = re.compile(r"\s+")
_PUNCT = re.compile(r"[^\w\s.-]")


@dataclass(frozen=True, slots=True)
class NormalizedItem:
    """One receipt line, reduced to an identity.

    `key` is the blocking key -- two lines with the same key are the same
    product, full stop, with no similarity computation involved. It is
    deliberately coarse enough to collapse spelling and spacing and strict
    enough that pack size never collapses.
    """

    key: str
    display: str
    brand: str | None
    pack_size: Decimal | None
    pack_unit: str | None
    raw: str


def _strip_noise(text: str) -> str:
    text = text.lower()
    text = HSN.sub(" ", text)
    text = LONG_DIGITS.sub(" ", text)
    text = _PUNCT.sub(" ", text)
    return _WHITESPACE.sub(" ", text).strip()


def parse_pack(text: str) -> tuple[Decimal | None, str | None, str]:
    """Pull the pack size out, returning the remaining description.

    Returns the magnitude on a canonical base, so `1 L` and `1000 ml` are
    indistinguishable afterwards -- which is the point.
    """
    match = PACK.search(text)
    if match is None:
        return None, None, text

    magnitude = Decimal(match.group(1))
    base, factor = UNIT_BASES[match.group(2).lower()]
    remainder = (text[: match.start()] + " " + text[match.end() :]).strip()
    return _plain(magnitude * factor), base, _WHITESPACE.sub(" ", remainder)


def _plain(value: Decimal) -> Decimal:
    """Trailing zeros trimmed, without scientific notation.

    `Decimal.normalize()` alone turns 500 into `5E+2`, which then goes straight
    into the blocking key -- so `500ml` and `5E+2ml` would be two products, and
    every key in the table would be unreadable.
    """
    trimmed = value.normalize()
    if trimmed == trimmed.to_integral_value():
        return trimmed.quantize(Decimal(1))
    return trimmed


def find_brand(text: str) -> tuple[str | None, str]:
    """Longest match first, so "mother dairy" never resolves to "dairy"."""
    for alias in sorted(BRAND_ALIASES, key=len, reverse=True):
        if re.search(rf"\b{re.escape(alias)}\b", text):
            canonical = BRAND_ALIASES[alias]
            return canonical, re.sub(rf"\b{re.escape(alias)}\b", " ", text, count=1)

    for brand in sorted(BRANDS, key=len, reverse=True):
        spaced = brand.replace("-", "[ -]?")
        if re.search(rf"\b{spaced}\b", text):
            return brand, re.sub(rf"\b{spaced}\b", " ", text, count=1)

    return None, text


def normalize_item(description: str) -> NormalizedItem | None:
    """Reduce a receipt line to a comparable identity.

    Returns `None` when nothing usable survives -- a line of digits, a
    separator, an OCR smear. That is not a failure to be logged: most receipts
    have several such lines and treating them as items would fill the graph
    with noise.
    """
    if not description or not description.strip():
        return None

    cleaned = _strip_noise(description)
    if not cleaned:
        return None

    size, unit, remainder = parse_pack(cleaned)
    brand, remainder = find_brand(remainder)

    # `len(w) > 1` alone let a separator line like "---" through as an item,
    # because punctuation survives `_strip_noise`. A token has to say something.
    words = [
        w
        for w in remainder.split()
        if w not in STOPWORDS and len(w) > 1 and any(c.isalnum() for c in w)
    ]
    if not words and brand is None:
        return None

    head = " ".join(words).strip()
    if not head and brand is None:
        return None

    pack_part = f"{size}{unit}" if size is not None and unit else ""
    key = "|".join((brand or "", head, pack_part))

    display_parts = [p for p in (brand, head) if p]
    display = " ".join(display_parts).title()
    if pack_part:
        display = f"{display} {pack_part}"

    return NormalizedItem(
        key=key,
        display=display.strip(),
        brand=brand,
        pack_size=size,
        pack_unit=unit,
        raw=description.strip(),
    )


def same_block(left: NormalizedItem, right: NormalizedItem) -> bool:
    """Whether two items may even be compared for similarity.

    **The rule that keeps the graph honest.** Trigram similarity between
    "amul taaza 500ml" and "amul taaza 1l" is around 0.93; they are a different
    product at a different price. Blocking on (brand, size, unit) before
    similarity is computed is what prevents similarity alone from merging every
    pack size of everything.
    """
    return (
        left.brand == right.brand
        and left.pack_size == right.pack_size
        and left.pack_unit == right.pack_unit
    )
