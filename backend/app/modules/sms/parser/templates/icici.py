"""ICICI Bank message shapes."""

from __future__ import annotations

import re

from app.modules.sms.parser.normalize import AMOUNT, DATE, TAIL
from app.modules.sms.parser.types import Direction, Rail, Template

_SENDERS = ("ICICIB", "ICICIT", "ICCICI")
_FLAGS = re.IGNORECASE

TEMPLATES: tuple[Template, ...] = (
    Template(
        id="icici.account.debit.v1",
        issuer="ICICI",
        senders=_SENDERS,
        rail=Rail.UPI,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"acc?t\.?\s*(?P<tail>{TAIL})\s+is\s+debited\s+with\s+"
            rf"(?:inr|rs\.?)\s*(?P<amount>{AMOUNT})\s+on\s+(?P<date>{DATE})"
            rf"(?:.*?\binfo[:\s]+(?:upi/)?(?P<ref>\d{{6,}})?/?(?P<merchant>[^.]+?)\s*\.)?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date", "ref", "merchant"}),
        notes=(
            "The Info segment carries rail, reference and payee slash-separated, "
            "and is absent on some rails -- hence the whole tail being optional."
        ),
    ),
    Template(
        id="icici.account.credit.v1",
        issuer="ICICI",
        senders=_SENDERS,
        rail=Rail.NETBANKING,
        direction=Direction.CREDIT,
        pattern=re.compile(
            rf"acc?t\.?\s*(?P<tail>{TAIL})\s+(?:is\s+)?credited\s+with\s+"
            rf"(?:inr|rs\.?)\s*(?P<amount>{AMOUNT})\s+on\s+(?P<date>{DATE})"
            rf"(?:.*?\binfo[:\s]+(?P<merchant>[^.]+?)\s*\.)?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date", "merchant"}),
    ),
    Template(
        id="icici.card.spend.v1",
        issuer="ICICI",
        senders=_SENDERS,
        rail=Rail.CARD,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"(?:inr|rs\.?)\s*(?P<amount>{AMOUNT})\s+spent\s+using\s+"
            rf"icici\s+bank\s+card\s+(?P<tail>{TAIL})\s+on\s+(?P<date>{DATE})\s+"
            rf"on\s+(?P<merchant>.+?)\s*\.",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date", "merchant"}),
        notes="Note the repeated 'on': first the date, then the merchant.",
    ),
)
