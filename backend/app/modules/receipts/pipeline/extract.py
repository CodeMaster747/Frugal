"""Field extraction from OCR output.

Turns a bag of recognised words into merchant, date, total, tax and line items
-- each with **its own confidence and bounding box** (FR-4.3).

Two ideas carry the design:

1. **Confidence is per field, not per document.** A receipt whose total scanned
   badly but whose merchant is obvious should prompt about the total alone.
   Asking a user to re-verify everything is how human-in-the-loop UX gets
   abandoned.

2. **Confidence combines recognition and interpretation.** Tesseract's score
   says how sure it is of the *characters*. It says nothing about whether
   `1,2S0.00` is a plausible amount. Both matter, so the field score is the OCR
   confidence discounted by how well the value parses.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from app.adapters.ports import OcrResult, Word

# Keywords that mark the grand total. Ordered by how strongly they mean it --
# "grand total" beats a bare "total", which beats "amount".
TOTAL_KEYWORDS = (
    ("grand total", 1.00),
    ("net payable", 1.00),
    ("net amount", 0.95),
    ("total", 0.90),
    ("amount due", 0.90),
    ("balance due", 0.85),
    ("amount", 0.70),
)
SUBTOTAL_KEYWORDS = ("subtotal", "sub total", "sub-total")
TAX_KEYWORDS = ("cgst", "sgst", "igst", "gst", "vat", "tax", "service charge")

# Lines that are never the merchant name.
NOT_MERCHANT = re.compile(
    r"invoice|receipt|bill|tax|gst|tel|phone|www\.|http|thank|welcome|order|table|cashier",
    re.IGNORECASE,
)

AMOUNT = re.compile(r"(?<![\d.])(\d{1,3}(?:[,\s]\d{2,3})*(?:\.\d{1,2})?|\d+\.\d{1,2})(?![\d])")

DATE_PATTERNS = (
    (re.compile(r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b"), "ymd"),
    (re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{4})\b"), "dmy"),
    (re.compile(r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2})\b"), "dmy2"),
    (re.compile(r"\b(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})\b"), "dMy"),
)

# Weights for how confident we are in a parsed value, relative to how confident
# the engine was in the characters.
PARSE_CLEAN = Decimal("1.00")
PARSE_REPAIRED = Decimal("0.70")  # needed character substitution
PARSE_GUESSED = Decimal("0.50")  # positional fallback, no keyword


@dataclass(slots=True)
class Field:
    name: str
    raw_text: str | None
    parsed_value: str | None
    confidence: Decimal
    bbox: dict[str, int] | None = None

    @property
    def found(self) -> bool:
        return self.parsed_value is not None


@dataclass(slots=True)
class LineItem:
    line_number: int
    description: str | None
    total_price: Decimal | None
    confidence: Decimal
    #: How many were bought, when the receipt says so.
    #:
    #: `None` means the receipt did not state it, which is the common case and
    #: is *not* the same as 1. The price graph treats an absent quantity as one
    #: unit -- a documented assumption rather than a silent one, and a robust
    #: enough guess given that a store price is a median over several
    #: contributors, so one shopper who bought two is an outlier the median
    #: absorbs. Without this field there is nothing to be wrong about, and a
    #: "2 x 32.00 = 64.00" line would enter the graph as a 64-rupee carton.
    quantity: Decimal | None = None


@dataclass(slots=True)
class Extraction:
    fields: list[Field]
    line_items: list[LineItem]

    def by_name(self, name: str) -> Field | None:
        return next((f for f in self.fields if f.name == name), None)

    @property
    def overall_confidence(self) -> Decimal:
        """Lowest confidence among the fields that must be right.

        The minimum, not the mean: a receipt is only as trustworthy as its
        weakest required field, and averaging lets a confident merchant name
        hide an unreadable total.
        """
        required = [f for f in self.fields if f.name in {"merchant", "date", "total"}]
        if not required:
            return Decimal("0")
        return min(f.confidence for f in required)


def extract(result: OcrResult) -> Extraction:
    lines = _group_lines(result)
    merchant = _extract_merchant(lines)
    fields = [
        merchant,
        _extract_date(lines),
        _extract_total(lines),
        _extract_tax(lines),
        _extract_subtotal(lines),
        *_extract_store_identity(lines, merchant),
    ]
    return Extraction(fields=fields, line_items=_extract_line_items(lines, fields))


def _extract_store_identity(lines: list[tuple[int, list[Word]]], merchant: Field) -> list[Field]:
    """Where the receipt came from (M16).

    Imported inside the function to keep the module cycle-free: `store_identity`
    reuses `Field` and the three confidence constants from here.
    """
    from app.modules.receipts.pipeline import store_identity

    line_texts = [_text_of(words) for _, words in lines]
    document = "\n".join(line_texts)

    merchant_line: int | None = None
    if merchant.raw_text:
        merchant_line = next(
            (i for i, text in enumerate(line_texts) if merchant.raw_text in text), None
        )

    return [
        store_identity.extract_gstin(document),
        store_identity.extract_pincode(document),
        store_identity.extract_phone(document),
        store_identity.extract_address(line_texts, merchant_line),
    ]


# --- helpers ---------------------------------------------------------------


def _group_lines(result: OcrResult) -> list[tuple[int, list[Word]]]:
    grouped: dict[int, list[Word]] = {}
    for word in result.words:
        grouped.setdefault(word.line, []).append(word)
    return [
        (key, sorted(words, key=lambda w: w.left))
        for key, words in sorted(grouped.items(), key=lambda kv: min(w.top for w in kv[1]))
    ]


def _text_of(words: list[Word]) -> str:
    return " ".join(w.text for w in words)


def _bbox(words: list[Word]) -> dict[str, int]:
    left = min(w.left for w in words)
    top = min(w.top for w in words)
    return {
        "x": left,
        "y": top,
        "w": max(w.right for w in words) - left,
        "h": max(w.bottom for w in words) - top,
    }


def _mean_confidence(words: list[Word]) -> Decimal:
    if not words:
        return Decimal("0")
    total = sum((w.confidence for w in words), Decimal("0"))
    return (total / len(words)).quantize(Decimal("0.001"))


def _repair_digits(text: str) -> tuple[str, bool]:
    """Fix the substitutions Tesseract makes on thermal receipts.

    `O`→`0`, `S`→`5`, `l`/`I`→`1`, `B`→`8`. Applied only where a digit is
    expected, and the caller is told a repair happened so the confidence can be
    discounted -- a repaired value is a guess, however plausible.
    """
    table: dict[str, str | int | None] = {
        "O": "0", "o": "0", "S": "5", "s": "5",
        "l": "1", "I": "1", "B": "8", "Z": "2",
    }  # fmt: skip
    repaired = text.translate(str.maketrans(table))
    return repaired, repaired != text


def _parse_amount(text: str) -> tuple[Decimal | None, Decimal]:
    """Parse the monetary value from a receipt line.

    Two rules, both learned from the eval harness:

    1. **Rightmost, not first.** Receipts put the amount in a right-hand
       column, and the left of the line is full of numbers that are not money:
       `CGST 9% 52.83`, `Milk 1L 62.00`. Reading left-to-right returns the rate
       or the pack size every time.

    2. **Longest well-formed token wins.** A misread like `1,2S0.00` still
       yields a *valid* match on the raw text -- the fragment `0.00` -- so
       returning the first successful parse silently reads twelve hundred and
       fifty rupees as zero. Repairing the digits gives `1,250.00`, a longer
       token spanning the same run, and that is the one to trust.

    The repaired reading is returned at reduced confidence, because a repaired
    value is a guess however plausible -- which is what routes it to review.
    """
    candidates: list[tuple[str, Decimal]] = []

    raw_matches = AMOUNT.findall(text)
    if raw_matches:
        candidates.append((raw_matches[-1], PARSE_CLEAN))

    repaired, changed = _repair_digits(text)
    if changed:
        repaired_matches = AMOUNT.findall(repaired)
        if repaired_matches:
            candidates.append((repaired_matches[-1], PARSE_REPAIRED))

    best: tuple[Decimal, Decimal] | None = None
    for token, quality in candidates:
        try:
            value = Decimal(token.replace(",", "").replace(" ", ""))
        except InvalidOperation:
            continue
        # Compare by token length: the reading that accounts for more of the
        # numeric run is the one that read the whole amount.
        if best is None or len(token) > len(str(best[0])):
            best = (value, quality)

    return best if best else (None, Decimal("0"))


# --- fields ----------------------------------------------------------------


def _extract_merchant(lines: list[tuple[int, list[Word]]]) -> Field:
    """The merchant is almost always the first substantial line.

    Receipts put the shop name at the top in the largest type, so position and
    glyph height are better signals than any keyword. Lines that look like
    headers ("TAX INVOICE") are skipped explicitly.
    """
    for _, words in lines[:5]:
        text = _text_of(words).strip()
        if len(text) < 3 or NOT_MERCHANT.search(text):
            continue
        if sum(c.isdigit() for c in text) > len(text) * 0.4:
            continue  # mostly digits: a phone number or a GST id

        confidence = _mean_confidence(words)
        # Receipts set the merchant larger than the body; taller glyphs are
        # corroborating evidence that this is the name.
        if max(w.height for w in words) > 24:
            confidence = min(confidence * Decimal("1.05"), Decimal("1"))

        return Field(
            name="merchant",
            raw_text=text,
            parsed_value=_titlecase(text),
            confidence=confidence.quantize(Decimal("0.001")),
            bbox=_bbox(words),
        )

    return Field(name="merchant", raw_text=None, parsed_value=None, confidence=Decimal("0"))


def _titlecase(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    return cleaned.title() if cleaned.isupper() else cleaned


def _extract_date(lines: list[tuple[int, list[Word]]]) -> Field:
    for _, words in lines:
        text = _text_of(words)
        for pattern, order in DATE_PATTERNS:
            match = pattern.search(text)
            if not match:
                continue

            parsed = _build_date(match.groups(), order)
            if parsed is None:
                continue

            matched = [w for w in words if any(g in w.text for g in match.groups() if g)]
            source = matched or words
            return Field(
                name="date",
                raw_text=match.group(0),
                parsed_value=parsed.isoformat(),
                confidence=_mean_confidence(source),
                bbox=_bbox(source),
            )

    return Field(name="date", raw_text=None, parsed_value=None, confidence=Decimal("0"))


def _build_date(groups: tuple[str, ...], order: str) -> date | None:
    try:
        if order == "ymd":
            year, month, day = (int(g) for g in groups)
        elif order == "dmy":
            day, month, year = (int(g) for g in groups)
        elif order == "dmy2":
            day, month, short = (int(g) for g in groups)
            year = 2000 + short
        else:  # dMy
            day = int(groups[0])
            # A month name carries no zone; a naive parse is the right result.
            month = datetime.strptime(groups[1][:3], "%b").month  # noqa: DTZ007
            year = int(groups[2])

        parsed = date(year, month, day)
    except (ValueError, TypeError):
        return None

    # A receipt from 1970 or 2087 is a misread, not a date.
    today = date.today()  # noqa: DTZ011 — a sanity window, not a business date
    if not (date(today.year - 5, 1, 1) <= parsed <= date(today.year + 1, 12, 31)):
        return None
    return parsed


def _extract_total(lines: list[tuple[int, list[Word]]]) -> Field:
    """The grand total.

    Scanned bottom-up: totals sit at the end, and a "TOTAL" appearing earlier is
    usually a section subtotal. The keyword's own strength weights the result,
    so "GRAND TOTAL" outranks a bare "AMOUNT".
    """
    best: tuple[Decimal, Field] | None = None

    for _, words in reversed(lines):
        text = _text_of(words)
        lowered = text.lower()

        if any(k in lowered for k in SUBTOTAL_KEYWORDS):
            continue

        for keyword, strength in TOTAL_KEYWORDS:
            if keyword not in lowered:
                continue

            amount, parse_quality = _parse_amount(text)
            if amount is None or amount <= 0:
                continue

            confidence = (
                _mean_confidence(words) * parse_quality * Decimal(str(strength))
            ).quantize(Decimal("0.001"))
            candidate = Field(
                name="total",
                raw_text=text,
                parsed_value=f"{amount:.2f}",
                confidence=confidence,
                bbox=_bbox(words),
            )
            score = Decimal(str(strength)) * confidence
            if best is None or score > best[0]:
                best = (score, candidate)
            break

    if best:
        return best[1]

    # No keyword anywhere. The largest amount on the receipt is usually the
    # total, but that is a guess and is scored as one -- which routes it to a
    # human rather than committing it silently.
    fallback = _largest_amount(lines)
    if fallback:
        amount, words = fallback
        return Field(
            name="total",
            raw_text=_text_of(words),
            parsed_value=f"{amount:.2f}",
            confidence=(_mean_confidence(words) * PARSE_GUESSED).quantize(Decimal("0.001")),
            bbox=_bbox(words),
        )

    return Field(name="total", raw_text=None, parsed_value=None, confidence=Decimal("0"))


def _largest_amount(
    lines: list[tuple[int, list[Word]]],
) -> tuple[Decimal, list[Word]] | None:
    best: tuple[Decimal, list[Word]] | None = None
    for _, words in lines:
        amount, quality = _parse_amount(_text_of(words))
        if amount and quality > 0 and (best is None or amount > best[0]):
            best = (amount, words)
    return best


def _extract_tax(lines: list[tuple[int, list[Word]]]) -> Field:
    """Total tax, summing the components a GST receipt splits (CGST + SGST)."""
    total = Decimal("0")
    confidences: list[Decimal] = []
    source: list[Word] = []

    for _, words in lines:
        lowered = _text_of(words).lower()
        if not any(k in lowered for k in TAX_KEYWORDS):
            continue

        amount, quality = _parse_amount(_text_of(words))
        if amount is None or quality == 0:
            continue

        total += amount
        confidences.append(_mean_confidence(words) * quality)
        source.extend(words)

    if not confidences:
        return Field(name="tax", raw_text=None, parsed_value=None, confidence=Decimal("0"))

    return Field(
        name="tax",
        raw_text=_text_of(source),
        parsed_value=f"{total:.2f}",
        confidence=(sum(confidences, Decimal("0")) / len(confidences)).quantize(Decimal("0.001")),
        bbox=_bbox(source),
    )


def _extract_subtotal(lines: list[tuple[int, list[Word]]]) -> Field:
    for _, words in lines:
        lowered = _text_of(words).lower()
        if not any(k in lowered for k in SUBTOTAL_KEYWORDS):
            continue

        amount, quality = _parse_amount(_text_of(words))
        if amount is None:
            continue

        return Field(
            name="subtotal",
            raw_text=_text_of(words),
            parsed_value=f"{amount:.2f}",
            confidence=(_mean_confidence(words) * quality).quantize(Decimal("0.001")),
            bbox=_bbox(words),
        )

    return Field(name="subtotal", raw_text=None, parsed_value=None, confidence=Decimal("0"))


def _extract_line_items(lines: list[tuple[int, list[Word]]], fields: list[Field]) -> list[LineItem]:
    """Lines that look like "description ... price".

    Deliberately conservative: line items are a nice-to-have, and a wrong item
    list is worse than none. Anything matching a known field is skipped.
    """
    claimed = {f.raw_text for f in fields if f.raw_text}
    items: list[LineItem] = []

    for index, (_, words) in enumerate(lines):
        text = _text_of(words)
        if text in claimed:
            continue

        lowered = text.lower()
        if any(k in lowered for k, _ in TOTAL_KEYWORDS) or any(
            k in lowered for k in (*TAX_KEYWORDS, *SUBTOTAL_KEYWORDS)
        ):
            continue

        amount, quality = _parse_amount(text)
        if amount is None or quality == 0 or amount <= 0:
            continue

        quantity, text_without_quantity = _parse_quantity(text)

        description = AMOUNT.sub("", text_without_quantity).strip(" .-x*")
        if len(description) < 2:
            continue

        items.append(
            LineItem(
                line_number=index,
                description=description[:255],
                total_price=amount,
                confidence=(_mean_confidence(words) * quality).quantize(Decimal("0.001")),
                quantity=quantity,
            )
        )

    return items[:50]


#: "2 x", "2 X", "2 @", "2 *" -- the forms an Indian receipt actually prints.
#:
#: Anchored to the start of the line and capped at two digits: a bare number
#: mid-line is far more often part of a product name ("Maggi 2 Minute") than a
#: count, and reading one as a quantity would halve a price rather than fail to
#: find one.
QUANTITY = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,3})?)\s*[xX@*]\s+")


def _parse_quantity(text: str) -> tuple[Decimal | None, str]:
    """A stated quantity, and the line with it removed."""
    match = QUANTITY.match(text)
    if match is None:
        return None, text
    try:
        quantity = Decimal(match.group(1))
    except Exception:
        return None, text
    if quantity <= 0:
        return None, text
    return quantity, text[match.end() :]
