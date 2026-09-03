"""Punjab National Bank message shapes."""

from __future__ import annotations

import re

from app.modules.sms.parser.normalize import AMOUNT, DATE, TAIL
from app.modules.sms.parser.types import Rail, Template

_SENDERS = ("PNBSMS", "PUNBNK")
_FLAGS = re.IGNORECASE

TEMPLATES: tuple[Template, ...] = (
    Template(
        id="pnb.account.movement.v1",
        issuer="PNB",
        senders=_SENDERS,
        rail=Rail.UPI,
        pattern=re.compile(
            rf"a/?c\s+(?P<tail>{TAIL})\s+(?P<dir>debited|credited)\s+"
            rf"(?:with\s+|by\s+|for\s+)?(?:inr|rs\.?)\s*(?P<amount>{AMOUNT})\s+"
            rf"on\s+(?P<date>{DATE})"
            rf"(?:.*?\binfo[:\s]+(?:upi/)?(?P<ref>\d{{6,}})?/?(?P<merchant>[^.]*?)\s*(?:\.|bal))?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "dir", "date", "ref", "merchant"}),
        notes=(
            "One template for both directions -- PNB's debit and credit bodies "
            "are identical apart from the verb, so splitting them would be two "
            "patterns to keep in step for no gain."
        ),
    ),
)
