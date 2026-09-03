"""Labelled bank-alert fixtures.

**Every message here is a real one with the digits changed.** Invented messages
test the author's idea of how a bank writes, which is exactly the thing the
parser must not depend on. Amounts, account tails, reference numbers and names
are altered; the wording, spacing, punctuation and field order are not.

`test_every_template_has_a_fixture` enumerates the registry against `CASES` and
fails when a template has no case. That is what keeps the template directory
from rotting into patterns nobody can safely change: you cannot add a bank
message shape without also recording what it should mean.

The negative corpus matters as much as the positive one. An OTP message parsed
as a debit is worse than one not parsed at all -- it books money the user never
spent -- and OTP messages contain amounts, merchant names, and the word
"debited".
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from app.modules.sms.parser.types import Direction


@dataclass(frozen=True, slots=True)
class SmsCase:
    """One message and everything it should be read as.

    Fields left None are asserted to be None -- "we expect no merchant here" is
    a claim worth testing, because a template that starts inventing one has
    regressed.
    """

    template_id: str
    sender: str
    body: str
    direction: Direction
    amount: Decimal
    occurred_on: date | None
    account_tail: str | None = None
    merchant: str | None = None
    reference: str | None = None
    vpa: str | None = None
    date_inferred: bool = False
    reversal: bool = False
    #: Minimum confidence this reading must reach. Set below the threshold for
    #: cases that should land in review rather than auto-commit.
    min_confidence: Decimal = Decimal("0.75")


#: When every fixture is treated as having arrived, unless a case needs
#: otherwise. Fixed so a date-inferring template is deterministic.
RECEIVED_AT = datetime.fromisoformat("2026-08-17T10:00:00+00:00")


def _load() -> tuple[SmsCase, ...]:
    from tests.fixtures.sms import axis, bob, generic, hdfc, icici, kotak, pnb, sbi

    modules = (hdfc, icici, sbi, axis, kotak, pnb, bob, generic)
    return tuple(case for module in modules for case in module.CASES)


CASES: tuple[SmsCase, ...] = _load()


def _load_negatives() -> tuple[tuple[str, str, str], ...]:
    from tests.fixtures.sms import negative

    return negative.CASES


#: (sender, body, why_it_must_not_parse)
NEGATIVE_CASES: tuple[tuple[str, str, str], ...] = _load_negatives()
