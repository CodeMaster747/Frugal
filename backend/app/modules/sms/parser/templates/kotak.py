"""Kotak Mahindra Bank message shapes.

Kotak names the counterparty by VPA rather than trade name, so `merchant` here
is usually a handle. `split_vpa` decides whether it names anyone.
"""

from __future__ import annotations

import re

from app.modules.sms.parser.normalize import AMOUNT, DATE, TAIL
from app.modules.sms.parser.types import Direction, Rail, Template

_SENDERS = ("KOTAKB",)
_FLAGS = re.IGNORECASE

TEMPLATES: tuple[Template, ...] = (
    Template(
        id="kotak.upi.debit.v1",
        issuer="KOTAK",
        senders=_SENDERS,
        rail=Rail.UPI,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"sent\s+(?:rs\.?|inr)\s*(?P<amount>{AMOUNT})\s+from\s+"
            rf"kotak\s+bank\s+ac\s+(?P<tail>{TAIL})\s+to\s+(?P<merchant>\S+?)\s+"
            rf"on\s+(?P<date>{DATE})"
            rf"(?:\s*\.?\s*upi\s*ref[:\s]*(?P<ref>\d{{6,}}))?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "merchant", "date", "ref"}),
    ),
    Template(
        id="kotak.upi.credit.v1",
        issuer="KOTAK",
        senders=_SENDERS,
        rail=Rail.UPI,
        direction=Direction.CREDIT,
        pattern=re.compile(
            rf"received\s+(?:rs\.?|inr)\s*(?P<amount>{AMOUNT})\s+in\s+your\s+"
            rf"kotak\s+bank\s+ac\s+(?P<tail>{TAIL})\s+from\s+(?P<merchant>\S+?)\s+"
            rf"on\s+(?P<date>{DATE})"
            rf"(?:\s*\.?\s*upi\s*ref[:\s]*(?P<ref>\d{{6,}}))?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "merchant", "date", "ref"}),
    ),
)
