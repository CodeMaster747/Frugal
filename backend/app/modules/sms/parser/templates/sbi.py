"""State Bank of India message shapes.

SBI writes dates without separators ("15Aug26") and amounts without a currency
marker ("debited by 450.0"), which is why the shared fragments allow both.
"""

from __future__ import annotations

import re

from app.modules.sms.parser.normalize import AMOUNT, DATE, TAIL
from app.modules.sms.parser.types import Direction, Rail, Template

_SENDERS = ("SBIINB", "SBIUPI", "SBIPSG", "SBICRD", "CBSSBI", "ATMSBI")
_FLAGS = re.IGNORECASE

TEMPLATES: tuple[Template, ...] = (
    Template(
        id="sbi.upi.debit.v1",
        issuer="SBI",
        senders=_SENDERS,
        rail=Rail.UPI,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"a/?c\s+(?P<tail>{TAIL})\s+debited\s+by\s+(?:rs\.?|inr)?\s*(?P<amount>{AMOUNT})\s+"
            rf"on\s+date\s+(?P<date>{DATE})\s+"
            rf"trf\s+to\s+(?P<merchant>.+?)\s+ref\s*no\.?\s*(?P<ref>\d{{6,}})",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date", "merchant", "ref"}),
        notes="'Dear UPI user' prefix. Amount often written 450.0, one decimal.",
    ),
    Template(
        id="sbi.account.credit.v1",
        issuer="SBI",
        senders=_SENDERS,
        rail=Rail.NETBANKING,
        direction=Direction.CREDIT,
        pattern=re.compile(
            rf"a/?c\s+(?P<tail>{TAIL})[-\s]+credited\s+by\s+(?:rs\.?|inr)\s*(?P<amount>{AMOUNT})\s+"
            rf"on\s+(?P<date>{DATE})"
            # The lookahead is load-bearing. A lazy `.+?` followed by an
            # optional group matches one character and stops -- "MR EMPLOYER"
            # became "M". Anchoring on what ends the payee makes it consume the
            # whole name.
            rf"(?:\s+transfer\s+from\s+(?P<merchant>.+?)(?=\s+ref\s*no|\s*-[A-Z]|\s*$))?"
            rf"(?:\s+ref\s*no\.?\s*(?P<ref>\d{{6,}}))?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date", "merchant", "ref"}),
        notes="Hyphen rather than space between the tail and 'credited'.",
    ),
    Template(
        id="sbi.account.debit.v1",
        issuer="SBI",
        senders=_SENDERS,
        rail=Rail.NETBANKING,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"a/?c\s+(?:no\.?\s*)?(?P<tail>{TAIL})\s+is\s+debited\s+for\s+"
            rf"(?:rs\.?|inr)\s*(?P<amount>{AMOUNT})\s+on\s+(?P<date>{DATE})",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date"}),
    ),
)
