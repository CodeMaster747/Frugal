"""The parser's vocabulary.

Pure data. Nothing here imports a session, a model, or a sibling module -- the
`the-sms-parser-is-pure` import-linter contract enforces it, so a template can
be exercised against a fixture in microseconds with no database anywhere near.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

#: Bumped when a change alters what the parser produces for input it already
#: handled. Stored on every `sms_messages` row, so a bad release can be found
#: and reparsed rather than guessed at.
PARSER_VERSION = "1"


class Direction(StrEnum):
    DEBIT = "debit"
    CREDIT = "credit"


class Rail(StrEnum):
    """How the money moved. Not the same as the account type.

    Recorded because it changes what a missing field *means*: an ATM withdrawal
    legitimately has no merchant, while a card purchase missing one is a parse
    that went wrong.
    """

    UPI = "upi"
    CARD = "card"
    NETBANKING = "netbanking"
    ATM = "atm"
    WALLET = "wallet"
    UNKNOWN = "unknown"


class Precedence(StrEnum):
    """How the template was matched, which is the base for confidence."""

    #: An issuer's own template, matched against that issuer's sender header.
    ISSUER = "issuer"
    #: A rail-shaped fallback, but the sender is a bank we recognise.
    RAIL_KNOWN_SENDER = "rail_known_sender"
    #: A rail-shaped fallback from a sender we do not know. Always reviewed.
    RAIL_UNKNOWN_SENDER = "rail_unknown_sender"


#: The confidence a match starts from, before deductions for what it failed to
#: extract. Deliberately not 1.0 even for an issuer template: matching a regex
#: proves the shape, not that the reading is right.
PRECEDENCE_BASE: dict[Precedence, Decimal] = {
    Precedence.ISSUER: Decimal("0.95"),
    Precedence.RAIL_KNOWN_SENDER: Decimal("0.85"),
    Precedence.RAIL_UNKNOWN_SENDER: Decimal("0.65"),
}


class TemplateError(Exception):
    """A template is malformed. Raised at import time, never at request time."""


@dataclass(frozen=True, slots=True)
class Template:
    """One bank message shape.

    Pure data holding a compiled pattern, rather than a class with behaviour.
    The point is that adding support for a new bank message is writing a regex
    and a fixture -- no subclassing, no method to override, nothing to read
    beyond the neighbouring entries.

    Named groups the parser understands:

    ``amount``   required. The figure, with or without separators and symbol.
    ``dir``      a direction word ("debited", "credited", "sent", "spent").
                 Omit when the template only ever matches one direction and set
                 `direction` instead.
    ``tail``     the masked account or card identifier, e.g. "XX1234".
    ``date``     the transaction date as the bank wrote it.
    ``merchant`` payee text or a VPA.
    ``ref``      the bank's reference number.
    """

    #: Stable and versioned, e.g. "hdfc.upi.debit.v1". Written to every row it
    #: matched, so a template that turns out to misread something can be found
    #: in the data rather than reasoned about.
    id: str
    issuer: str
    #: DLT header cores this template applies to, e.g. ("HDFCBK", "HDFCBN").
    #: Empty means the template is rail-generic and matched on body alone.
    senders: tuple[str, ...]
    pattern: re.Pattern[str]
    rail: Rail
    #: Set when the shape only ever means one thing; otherwise read from `dir`.
    direction: Direction | None = None
    #: A refund or failed-payment reversal. Always routed to review, whatever
    #: the confidence, because the sign is right but the meaning is not.
    reversal: bool = False
    #: Groups this template guarantees. Verified against the pattern at import
    #: time, so a renamed group is an ImportError rather than a None at 3am.
    provides: frozenset[str] = field(default_factory=frozenset)
    notes: str = ""

    def __post_init__(self) -> None:
        groups = set(self.pattern.groupindex)
        if "amount" not in groups:
            raise TemplateError(f"{self.id}: pattern has no 'amount' group")
        if self.direction is None and "dir" not in groups:
            raise TemplateError(
                f"{self.id}: template sets no direction and pattern has no 'dir' group"
            )
        missing = self.provides - groups
        if missing:
            raise TemplateError(
                f"{self.id}: declares {sorted(missing)} in `provides` "
                f"but the pattern has no such group(s)"
            )


@dataclass(frozen=True, slots=True)
class ParsedSms:
    """What one message was read as.

    `confidence` is about this reading of this message, not about the template.
    A template that usually works can still produce a doubtful result when the
    particular message omits a date, and it is the message that gets committed.
    """

    template_id: str
    issuer: str
    rail: Rail
    direction: Direction
    amount: Decimal
    confidence: Decimal
    parser_version: str = PARSER_VERSION
    occurred_on: date | None = None
    #: True when `occurred_on` came from the handset receipt time rather than
    #: from the message body. Costs confidence, and the review screen says so.
    date_inferred: bool = False
    merchant: str | None = None
    #: The VPA exactly as written, kept for display even when `merchant` is
    #: None because the handle was opaque.
    vpa: str | None = None
    account_tail: str | None = None
    reference: str | None = None
    reversal: bool = False
    #: Why confidence is not the template's base. Rendered in the review queue.
    caveats: tuple[str, ...] = ()

    @property
    def is_complete(self) -> bool:
        """Whether every field a commit requires was read.

        The account is deliberately absent: resolving a tail to an account is a
        lookup against the user's mappings, not something the parser can know.
        """
        return self.occurred_on is not None and not self.date_inferred
