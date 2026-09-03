"""Read a bank alert. One public function, no side effects.

`parse` is total: an unrecognised message returns None rather than raising, and
a partially-read one returns a `ParsedSms` with reduced confidence and the
reasons attached. Nothing here decides what happens next -- the service decides
whether a reading is good enough to commit, because that threshold is
configuration and this module is not.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from app.modules.sms.parser.normalize import (
    account_tail,
    clean_merchant,
    parse_amount,
    parse_date,
    redact,
    split_vpa,
)
from app.modules.sms.parser.registry import TEMPLATES, candidates
from app.modules.sms.parser.senders import (
    is_capturable,
    issuer_for,
    looks_transactional,
    sender_core,
)
from app.modules.sms.parser.types import (
    PARSER_VERSION,
    PRECEDENCE_BASE,
    Direction,
    ParsedSms,
    Precedence,
    Rail,
    Template,
)

__all__ = [
    "PARSER_VERSION",
    "TEMPLATES",
    "Direction",
    "ParsedSms",
    "Rail",
    "is_capturable",
    "looks_transactional",
    "parse",
    "redact",
]

# --- confidence -------------------------------------------------------------
#
# Deductions are for what the *message* failed to yield, not for what the
# template usually manages. A template that normally reads a date still
# produces a doubtful result on the one message that omitted it, and it is the
# message that gets committed. The same reasoning is written out at length in
# ReceiptService.record_extraction, which grades per field for the same reason.

_NO_DATE = Decimal("0.15")
_NO_MERCHANT = Decimal("0.20")
_NO_TAIL = Decimal("0.25")
_AMBIGUOUS_AMOUNT = Decimal("0.10")
_HAS_REFERENCE = Decimal("0.03")

_CREDIT_WORDS = frozenset({"credited", "received", "credit"})


def parse(body: str, *, sender: str, received_at: datetime) -> ParsedSms | None:
    """Read one message, or return None if it is not a transaction.

    `received_at` is when the handset got it, and is used two ways: as the
    fallback date when the bank omitted one, and as an upper bound so a
    misread field cannot produce a transaction dated in the future.

    An empty `sender` is allowed, and is the normal case for a pasted or shared
    message: someone copying an alert out of their inbox copies the text, not
    the header above it. The body gate still applies, so a personal message is
    still refused -- but with no header to recognise, the reading can only reach
    unknown-sender precedence and therefore always goes to review rather than
    into the ledger. That is the honest handling: we cannot verify a bank sent
    this, so a human confirms it.

    A *non-empty* sender that is not a commercial header -- a phone number --
    is refused outright. Nothing on the device path produces one, so its
    presence means the caller is passing something this was not built to read.
    """
    supplied_sender = sender.strip()
    if supplied_sender and sender_core(supplied_sender) is None:
        return None
    if not looks_transactional(body):
        return None

    received_on = received_at.date()

    for template, precedence in candidates(sender):
        match = template.pattern.search(body)
        if match is None:
            continue
        parsed = _build(match.groupdict(), template, precedence, received_on, sender)
        if parsed is not None:
            return parsed
    return None


def _build(
    groups: dict[str, str | None],
    template: Template,
    precedence: Precedence,
    received_on: date,
    sender: str,
) -> ParsedSms | None:
    amount, ambiguous = parse_amount(groups.get("amount") or "")
    if amount is None:
        # The pattern matched but the figure did not read. Treat as no match so
        # a later, looser template gets its turn -- returning a ParsedSms with
        # no amount would be a transaction with no value.
        return None

    direction = _direction(groups, template)
    if direction is None:
        return None

    caveats: list[str] = []
    confidence = PRECEDENCE_BASE[precedence]

    occurred_on = parse_date(groups.get("date") or "", received_on=received_on)
    date_inferred = occurred_on is None
    if date_inferred:
        occurred_on = received_on
        confidence -= _NO_DATE
        caveats.append("No date in the message; using the day it arrived.")

    merchant, vpa = split_vpa(groups.get("merchant"))
    if merchant is None:
        merchant = clean_merchant(groups.get("merchant")) if vpa is None else None
    if merchant is None and template.rail is not Rail.ATM:
        confidence -= _NO_MERCHANT
        caveats.append(
            "No usable payee: the handle names nobody." if vpa else "No payee in the message."
        )

    tail = account_tail(groups.get("tail"))
    if tail is None:
        confidence -= _NO_TAIL
        caveats.append("No account or card number in the message.")

    if ambiguous:
        confidence -= _AMBIGUOUS_AMOUNT
        caveats.append("The amount's comma may be a decimal separator.")

    reference = groups.get("ref") or None
    if reference:
        confidence += _HAS_REFERENCE

    if template.reversal:
        caveats.append("This looks like a refund or reversal, not new income.")

    return ParsedSms(
        template_id=template.id,
        # A generic template carries no issuer of its own, so fall back to
        # whichever bank the sender header belongs to. Empty when that is
        # unknown too, which is exactly the case the review queue exists for.
        issuer=template.issuer or issuer_for(sender) or "",
        rail=template.rail,
        direction=direction,
        amount=amount,
        confidence=max(Decimal("0"), min(Decimal("1"), confidence)),
        occurred_on=occurred_on,
        date_inferred=date_inferred,
        merchant=merchant,
        vpa=vpa,
        account_tail=tail,
        reference=reference,
        reversal=template.reversal,
        caveats=tuple(caveats),
    )


def _direction(groups: dict[str, str | None], template: Template) -> Direction | None:
    if template.direction is not None:
        return template.direction
    word = (groups.get("dir") or "").strip().lower()
    if not word:
        return None
    return Direction.CREDIT if word in _CREDIT_WORDS else Direction.DEBIT
