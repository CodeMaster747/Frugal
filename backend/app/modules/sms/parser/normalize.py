"""Turning what a bank wrote into what the ledger stores.

Every function here is total: it returns None rather than raising, because the
caller's job is to lower confidence and route to review, not to crash on the
first bank that writes a date a new way.
"""

from __future__ import annotations

import calendar
import re
from datetime import date
from decimal import Decimal, InvalidOperation

# --- fragments templates embed ----------------------------------------------
#
# Bare fragments, not named groups: a named group may appear only once in a
# pattern, and templates need to name their own.

#: "450", "1,250.00", "1,23,456.78" -- Indian lakh grouping and Western
#: grouping both survive comma-stripping, so one fragment covers both.
AMOUNT = r"\d[\d,]*(?:\.\d{1,2})?"

#: "XX1234", "xx1234", "**1234", "X1234", "...1234", "1234"
TAIL = r"(?:[xX*.]{1,6})?\d{3,6}"

#: "15/08/26", "15-08-2026", "15-Aug-26", "15Aug26", "2026-08-15", "15 Aug 2026"
DATE = r"\d{1,4}[-/ ]?[A-Za-z]{3}[-/ ]?\d{2,4}|\d{1,4}[-/]\d{1,2}[-/]\d{2,4}"

#: "swiggy@ybl", "9876543210@paytm", "q73829183@okaxis"
VPA = r"[A-Za-z0-9._-]{2,64}@[A-Za-z]{2,32}"


_MONTHS = {name.lower(): num for num, name in enumerate(calendar.month_abbr) if name}

_AMBIGUOUS_DECIMAL_COMMA = re.compile(r"^\d{1,3},\d{2}$")

# A phone number as written in an Indian bank SMS: 10 digits starting 6-9,
# optionally with a country code.
_PHONE = re.compile(r"(?<!\d)(?:\+?91[-\s]?)?[6-9]\d{9}(?!\d)")

_OTP = re.compile(
    r"\b(?:otp|one[\s-]?time[\s-]?password|password|code)\b[^\d]{0,20}(\d{4,8})",
    re.IGNORECASE,
)

_BALANCE = re.compile(
    r"((?:avl|available|avlbl|a/c|closing|clsg)[\s.]*(?:bal|balance|lmt|limit)"
    r"[\s.:is]*(?:rs\.?|inr|₹)?\s*)(" + AMOUNT + r")",
    re.IGNORECASE,
)

#: A VPA local part that names nobody: a dynamic-QR handle, a phone number, a
#: customer id. Feeding one to `normalize_merchant` produces a merchant key
#: that matches nothing, pollutes the trigram index on `merchant_normalized`,
#: and gives the categoriser a label it can only learn noise from.
_OPAQUE_LOCAL = re.compile(r"^(?:\d+|[a-z]\d{5,}|[a-z]{0,2}\d{6,}|paytmqr\w*)$", re.IGNORECASE)


def parse_amount(raw: str) -> tuple[Decimal | None, bool]:
    """Read a figure. Returns (value, separator_was_ambiguous).

    Comma-stripping handles Indian and Western grouping identically --
    ``1,23,456.78`` and ``123,456.78`` are the same number either way. The one
    genuinely ambiguous shape is ``450,00``, where the comma may be a decimal
    separator; it is read as 45000 and flagged, so the caller can drop
    confidence rather than silently book a hundredfold error.
    """
    cleaned = raw.strip().replace("₹", "")
    cleaned = re.sub(r"(?i)^(?:rs\.?|inr)\s*", "", cleaned).strip()
    ambiguous = bool(_AMBIGUOUS_DECIMAL_COMMA.match(cleaned))
    cleaned = cleaned.replace(",", "")
    try:
        value = Decimal(cleaned)
    except (InvalidOperation, ValueError):
        return None, False
    if value <= 0:
        return None, ambiguous
    return value, ambiguous


def parse_date(raw: str, *, received_on: date | None = None) -> date | None:
    """Read a transaction date the way an Indian bank writes one.

    Numeric dates are read day-first, matching `DATE_FORMATS` in the CSV
    importer. That is a real choice with a real cost -- 05/08 is the 5th of
    August here and would be the 8th of May in a US-formatted statement -- but
    the two ingestion paths disagreeing about the same payment's date would
    break `content_hash` convergence, and every bank in scope writes day-first.

    A two-digit year in the future relative to `received_on` is rejected rather
    than shifted a century: it means the pattern read the wrong field.
    """
    text = raw.strip()

    parsed = _parse_alpha_month(text) or _parse_numeric(text)
    if parsed is None:
        return None
    # A transaction cannot post after the handset was told about it. One day of
    # slack absorbs timezone edges around midnight.
    if received_on is not None and (parsed - received_on).days > 1:
        return None
    return parsed


def _parse_alpha_month(text: str) -> date | None:
    match = re.match(r"^(\d{1,2})[-/ ]?([A-Za-z]{3})[A-Za-z]*[-/ ]?(\d{2,4})$", text)
    if not match:
        return None
    day, month_name, year = match.groups()
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    return _build(int(year), month, int(day))


def _parse_numeric(text: str) -> date | None:
    match = re.match(r"^(\d{1,4})[-/](\d{1,2})[-/](\d{2,4})$", text)
    if not match:
        return None
    first, second, third = (int(p) for p in match.groups())
    if len(match.group(1)) == 4:  # ISO: 2026-08-15
        return _build(first, second, third)
    return _build(third, second, first)  # day-first: 15-08-26


def _build(year: int, month: int, day: int) -> date | None:
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def account_tail(raw: str | None) -> str | None:
    """The digits a bank uses to name an account, without the masking noise.

    ``XX1234``, ``xx1234``, ``**1234`` and ``...1234`` are one account written
    four ways across a single bank's own templates. Storing the raw form would
    mean four identifiers, four mapping rows, and four chances to miss a match.
    """
    if not raw:
        return None
    digits = re.sub(r"\D", "", raw)
    return digits[-6:] if len(digits) >= 3 else None


def split_vpa(raw: str | None) -> tuple[str | None, str | None]:
    """A VPA into (merchant_name, vpa). Either may be None.

    ``swiggy@ybl`` yields ``("swiggy", "swiggy@ybl")`` -- and "swiggy" is
    exactly what `normalize_merchant` derives from the CSV narration
    ``UPI/SWIGGY/883012`` for the same payment, which is what makes the two
    ingestion paths converge on one `content_hash` instead of booking the
    purchase twice.

    Passing the raw handle through instead would not converge: the normaliser
    replaces ``@`` with a space, so ``swiggy@ybl`` becomes ``swiggy ybl``.

    An opaque local part yields ``(None, vpa)``. None is the honest answer --
    the handle names nobody, and the review queue is where it belongs.
    """
    if not raw:
        return None, None
    vpa = raw.strip()
    if "@" not in vpa:
        return None, None
    local = vpa.split("@", 1)[0]
    if _OPAQUE_LOCAL.match(local):
        return None, vpa
    name = re.sub(r"[._-]+", " ", local).strip()
    return (name or None), vpa


def clean_merchant(raw: str | None) -> str | None:
    """Trim a payee string to something worth storing.

    Deliberately light: `normalize_merchant` in the finance module is the
    canonical reducer and must stay the only one, or the two ingestion paths
    reduce the same name differently and stop converging.
    """
    if not raw:
        return None
    text = re.sub(r"\s+", " ", raw).strip(" .,-:;/")
    # Banks prefix the payee with the action ("Payment to SWIGGY"), and the
    # CSV narration for the same purchase does not. Leaving it on gives
    # `normalize_merchant` a different key for each path, so they stop
    # converging on one content hash and the purchase books twice.
    text = _LEADING_ACTION.sub("", text).strip(" .,-:;/")
    return text[:255] or None


_LEADING_ACTION = re.compile(
    r"^(?:(?:upi|imps|neft|rtgs|pos|ach|atw|mmt)[\s/-]+)?"
    r"(?:payment|paid|transfer|trf|sent|received|txn)?\s*"
    r"(?:to|at|from|for|towards|by)?\s+",
    re.IGNORECASE,
)


def redact(body: str) -> str:
    """What we keep once the message has been read.

    The bank has already masked the account number, so the sensitive residue is
    the things it did not mask: the user's phone number, any one-time code, and
    the running balance. The balance goes because no engine reads it and it is
    the single most revealing figure in the message -- knowing someone spent
    ₹450 is ordinary; knowing they have ₹1,240 left is not.

    The transaction amount, merchant, and reference stay. Removing them would
    leave a review queue that cannot show its work.
    """
    redacted = _OTP.sub(lambda m: m.group(0).replace(m.group(1), "*" * len(m.group(1))), body)
    redacted = _PHONE.sub("[phone]", redacted)
    return _BALANCE.sub(lambda m: f"{m.group(1)}[balance]", redacted)
