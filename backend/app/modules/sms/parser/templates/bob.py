"""Bank of Baroda message shapes."""

from __future__ import annotations

import re

from app.modules.sms.parser.normalize import AMOUNT, DATE, TAIL, VPA
from app.modules.sms.parser.types import Direction, Rail, Template

_SENDERS = ("BOBSMS", "BOBTXN", "BOBIBK")
_FLAGS = re.IGNORECASE

TEMPLATES: tuple[Template, ...] = (
    Template(
        id="bob.upi.debit.v1",
        issuer="BOB",
        senders=_SENDERS,
        rail=Rail.UPI,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"(?:rs\.?|inr)\s*(?P<amount>{AMOUNT})\s+debited\s+from\s+"
            rf"a/?c\s+(?P<tail>{TAIL})\s+and\s+credited\s+to\s+(?P<merchant>{VPA})"
            rf"(?:\s*\(upi\s+ref\s*(?:no\.?)?\s*(?P<ref>\d{{6,}})\))?"
            rf"(?:\s*on\s+(?P<date>{DATE}))?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "merchant", "ref", "date"}),
        notes=(
            "Often omits the date entirely, on the assumption the message "
            "arrives the moment it happens. `date_inferred` covers that, at a "
            "confidence cost -- which is right, because an XML backfill of a "
            "dateless message has only the receipt time to go on."
        ),
    ),
    Template(
        id="bob.account.credit.v1",
        issuer="BOB",
        senders=_SENDERS,
        rail=Rail.NETBANKING,
        direction=Direction.CREDIT,
        pattern=re.compile(
            rf"(?:rs\.?|inr)\s*(?P<amount>{AMOUNT})\s+credited\s+to\s+"
            rf"a/?c\s+(?P<tail>{TAIL})"
            rf"(?:.*?\bon\s+(?P<date>{DATE}))?"
            rf"(?:.*?\bref\s*(?:no\.?)?\s*(?P<ref>\d{{6,}}))?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date", "ref"}),
    ),
)
