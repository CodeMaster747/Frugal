"""Axis Bank message shapes."""

from __future__ import annotations

import re

from app.modules.sms.parser.normalize import AMOUNT, DATE, TAIL
from app.modules.sms.parser.types import Direction, Rail, Template

_SENDERS = ("AXISBK", "AXISBL")
_FLAGS = re.IGNORECASE

TEMPLATES: tuple[Template, ...] = (
    Template(
        id="axis.account.debit.v1",
        issuer="AXIS",
        senders=_SENDERS,
        rail=Rail.UPI,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"(?:inr|rs\.?)\s*(?P<amount>{AMOUNT})\s+debited\s+from\s+"
            rf"a/?c\s+(?:no\.?\s*)?(?P<tail>{TAIL})\s+on\s+(?P<date>{DATE})"
            rf"(?:[,\s]+[\d:]+\s*(?:ist)?)?"
            rf"(?:\s*(?:at|to)\s+(?P<merchant>[^.]+?)\s*\.)?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date", "merchant"}),
        notes="A time and 'IST' sit between the date and the payee.",
    ),
    Template(
        id="axis.account.credit.v1",
        issuer="AXIS",
        senders=_SENDERS,
        rail=Rail.NETBANKING,
        direction=Direction.CREDIT,
        pattern=re.compile(
            rf"(?:inr|rs\.?)\s*(?P<amount>{AMOUNT})\s+credited\s+to\s+"
            rf"a/?c\s+(?:no\.?\s*)?(?P<tail>{TAIL})\s+on\s+(?P<date>{DATE})"
            rf"(?:.*?\binfo[:\s-]+(?P<merchant>[^.]+?)\s*\.)?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date", "merchant"}),
    ),
    Template(
        id="axis.card.spend.v1",
        issuer="AXIS",
        senders=_SENDERS,
        rail=Rail.CARD,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"spent\s+card\s+(?:no\.?\s*)?(?P<tail>{TAIL})\s+"
            rf"(?:inr|rs\.?)\s*(?P<amount>{AMOUNT})\s+(?P<date>{DATE})"
            rf"(?:\s+[\d:]+)?\s+(?P<merchant>.+?)\s+avl",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date", "merchant"}),
        notes=(
            "Telegraphic, no prepositions: fields are positional and separated "
            "only by spaces, so 'Avl' is the merchant's right boundary."
        ),
    ),
)
