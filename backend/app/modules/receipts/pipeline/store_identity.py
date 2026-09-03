"""Who printed this receipt, and where.

The receipts pipeline extracts *what was bought*. This extracts *where from*,
which is what turns a private receipt into a row in a shared price graph: a
price with no located seller compares nothing.

Pure, like the rest of the pipeline -- regexes over grouped lines, no session,
no models. Every function returns a `Field` with its own confidence, so the
existing review UI handles these exactly as it handles a doubtful total, and a
human correction wins through `effective_value`.

None of these fields can block a commit. `REQUIRED_FIELDS` stays
(merchant, date, total): a user photographing a receipt to record a purchase
should never be stopped because the shop's GSTIN was smudged.
"""

from __future__ import annotations

import re
from decimal import Decimal

from app.modules.receipts.pipeline.extract import (
    PARSE_CLEAN,
    PARSE_GUESSED,
    PARSE_REPAIRED,
    Field,
)

#: 2 digits state code · 10 char PAN · 1 entity digit · 'Z' · 1 check char.
#:
#: The single highest-value field on an Indian receipt. It is a government
#: identifier for a *specific business at a specific state registration*, which
#: is what turns "AMUL PARLOUR" -- a name thousands of shops share -- into an
#: entity key that can be matched across receipts from different users.
GSTIN = re.compile(r"\b(\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z])\b")

#: Indian PIN codes never start with zero.
PINCODE = re.compile(r"\b([1-9]\d{5})\b")

#: Indian mobile numbers begin 6-9; landlines are matched with their STD code.
PHONE = re.compile(r"(?:\+?91[\s-]?)?([6-9]\d{9})\b|\b(0\d{2,4}[\s-]?\d{6,8})\b")

#: A line that marks the end of the letterhead and the start of the
#: transaction. The address, if there is one, is above it.
TRANSACTION_PREAMBLE = re.compile(
    r"\b(bill|invoice|receipt|tax\s*invoice|order|token|date|time|cashier|counter)\b"
    r"|^\s*\d{1,2}[/-]\d{1,2}[/-]\d{2,4}",
    re.I,
)

#: Corroboration for an address guess. A block of text between the merchant
#: line and the preamble is only an address if it *looks* like one -- otherwise
#: it is a tagline, a GST notice, or a slogan.
STREET_TOKEN = re.compile(
    r"\b(road|rd|street|st|nagar|layout|cross|main|sector|phase|block|marg|"
    r"colony|market|complex|plaza|mall|floor|shop|opp|near|behind)\b",
    re.I,
)

#: Distinctive words from Indian state names, one per state where one word is
#: unambiguous. Corroboration only -- a state name is evidence that a block of
#: text is an address, never the address itself.
STATES = frozenset(
    (
        "andhra",
        "arunachal",
        "assam",
        "bihar",
        "chhattisgarh",
        "goa",
        "gujarat",
        "haryana",
        "himachal",
        "jharkhand",
        "karnataka",
        "kerala",
        "madhya",
        "maharashtra",
        "manipur",
        "meghalaya",
        "mizoram",
        "nagaland",
        "odisha",
        "punjab",
        "rajasthan",
        "sikkim",
        "tamil",
        "telangana",
        "tripura",
        "uttarakhand",
        "uttar",
        "bengal",
        "delhi",
        "puducherry",
        "chandigarh",
    )
)

#: The GSTIN check character alphabet: 0-9 then A-Z, indexed 0..35.
_ALPHABET = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: Substitutions OCR actually makes on this alphabet, tried once when the
#: checksum fails. Bidirectional, because the error runs either way depending
#: on the font.
_CONFUSABLE = {
    "0": "O",
    "O": "0",
    "1": "I",
    "I": "1",
    "5": "S",
    "S": "5",
    "8": "B",
    "B": "8",
    "2": "Z",
    "Z": "2",
}

MAX_ADDRESS_CHARS = 255


def gstin_checksum_ok(value: str) -> bool:
    """Validate the mod-36 weighted check character.

    This is what separates a real identifier from fifteen characters OCR
    happened to read in the right shape. Without it a mis-read GSTIN becomes a
    confident wrong entity key -- and the price graph would merge two different
    shops, which is worse than failing to match them at all.
    """
    if len(value) != 15 or any(c not in _ALPHABET for c in value):
        return False

    total = 0
    for index, char in enumerate(value[:14]):
        # Alternating weights 1, 2, 1, 2, ... over base-36 values; each
        # product's digits are folded before summing.
        product = _ALPHABET.index(char) * (2 if index % 2 else 1)
        total += product // 36 + product % 36

    checksum = (36 - total % 36) % 36
    return _ALPHABET[checksum] == value[14]


def _repair_gstin(value: str) -> str | None:
    """One round of confusable substitution, kept only if it validates.

    Deliberately not a search over every combination: a checksum has a 1-in-36
    chance of passing by accident, so trying thousands of variants would
    manufacture a plausible-looking identifier out of noise. One substitution
    at a time is strong evidence; brute force is not evidence at all.
    """
    for index, char in enumerate(value):
        replacement = _CONFUSABLE.get(char)
        if replacement is None:
            continue
        candidate = value[:index] + replacement + value[index + 1 :]
        if gstin_checksum_ok(candidate):
            return candidate
    return None


def extract_gstin(text: str) -> Field:
    """The shop's tax registration, validated rather than merely matched."""
    for match in GSTIN.finditer(text.upper()):
        candidate = match.group(1)
        if gstin_checksum_ok(candidate):
            return Field(
                name="gstin",
                raw_text=match.group(0),
                parsed_value=candidate,
                confidence=PARSE_CLEAN,
            )
        repaired = _repair_gstin(candidate)
        if repaired is not None:
            return Field(
                name="gstin",
                raw_text=match.group(0),
                parsed_value=repaired,
                confidence=PARSE_REPAIRED,
            )

    # Emit nothing rather than an unvalidated identifier. A wrong GSTIN is not
    # a low-confidence GSTIN; it is a different shop.
    return Field(name="gstin", raw_text=None, parsed_value=None, confidence=Decimal("0"))


def extract_pincode(text: str) -> Field:
    """Clean, low-cardinality, and geographic.

    Worth more for store matching than the free-text address: it narrows a
    trigram name match from the whole country to one postal area, which is the
    difference between "a Reliance Fresh" and "this Reliance Fresh".
    """
    claimed = {m.group(1) for m in GSTIN.finditer(text.upper())}
    for match in PINCODE.finditer(text):
        # A PIN inside a GSTIN or a phone number is not a PIN.
        span = text[max(0, match.start() - 6) : match.end() + 6]
        if any(g[:6] in span for g in claimed):
            continue
        if PHONE.search(span):
            continue
        return Field(
            name="store_pincode",
            raw_text=match.group(0),
            parsed_value=match.group(1),
            confidence=PARSE_CLEAN,
        )
    return Field(name="store_pincode", raw_text=None, parsed_value=None, confidence=Decimal("0"))


def extract_phone(text: str) -> Field:
    for match in PHONE.finditer(text):
        number = match.group(1) or match.group(2)
        if number is None:
            continue
        digits = re.sub(r"\D", "", number)
        return Field(
            name="store_phone",
            raw_text=match.group(0),
            parsed_value=digits,
            confidence=PARSE_CLEAN,
        )
    return Field(name="store_phone", raw_text=None, parsed_value=None, confidence=Decimal("0"))


def extract_address(line_texts: list[str], merchant_line: int | None) -> Field:
    """The block between the merchant name and the transaction preamble.

    The hardest and lowest-confidence of the four, and the one most likely to be
    corrected by a human -- which is fine, because that is what the review UI is
    for. It requires corroboration (a PIN, a state name, or a street token)
    rather than trusting position alone: without that, a shop's tagline lands in
    the address field and two branches of one chain look like different places.
    """
    start = (merchant_line + 1) if merchant_line is not None else 0
    collected: list[str] = []

    for text in line_texts[start:]:
        if TRANSACTION_PREAMBLE.search(text):
            break
        stripped = text.strip()
        if stripped:
            collected.append(stripped)
        if len(" ".join(collected)) > MAX_ADDRESS_CHARS:
            break

    joined = " ".join(collected)[:MAX_ADDRESS_CHARS].strip(" ,-")
    if not joined:
        return Field(
            name="store_address", raw_text=None, parsed_value=None, confidence=Decimal("0")
        )

    corroborated = (
        PINCODE.search(joined) is not None
        or STREET_TOKEN.search(joined) is not None
        or any(word in STATES for word in joined.lower().split())
    )
    if not corroborated:
        return Field(
            name="store_address", raw_text=joined, parsed_value=None, confidence=Decimal("0")
        )

    return Field(
        name="store_address",
        raw_text=joined,
        parsed_value=joined,
        confidence=PARSE_GUESSED,
    )
