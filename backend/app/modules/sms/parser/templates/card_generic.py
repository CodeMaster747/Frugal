"""Rail-shaped fallbacks for card spending.

Separate from `upi_generic` because a card message names a *card* tail, not an
account tail, and the two resolve through different identifier kinds. Reading a
card tail as an account tail maps the spend to the wrong account whenever a user
holds both with the same bank -- which is the common case, not the edge.
"""

from __future__ import annotations

import re

from app.modules.sms.parser.normalize import AMOUNT, DATE, TAIL
from app.modules.sms.parser.types import Direction, Rail, Template

_FLAGS = re.IGNORECASE

TEMPLATES: tuple[Template, ...] = (
    Template(
        id="generic.card_spend.v1",
        issuer="",
        senders=(),
        rail=Rail.CARD,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"(?:inr|rs\.?|₹)\s*(?P<amount>{AMOUNT})\s+"
            rf"(?:spent|charged|debited)\s+(?:on|using|from)\s+"
            rf"(?:.{{0,40}}?\b)?card\s+(?:no\.?\s*|ending\s+|xx)?(?P<tail>{TAIL})"
            # Same lookahead as sbi.account.credit, for the same reason: a lazy
            # quantifier before an optional group stops at its minimum.
            rf"(?:.*?\bat\s+(?P<merchant>[^.]{{2,60}}?)(?=\s+on\s+|\s*\.|\s*$))?"
            rf"(?:.*?\bon\s+(?P<date>{DATE}))?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "merchant", "date"}),
    ),
    Template(
        id="generic.atm_withdrawal.v1",
        issuer="",
        senders=(),
        rail=Rail.ATM,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"(?:inr|rs\.?|₹)\s*(?P<amount>{AMOUNT})\s+withdrawn"
            rf"(?:.{{0,60}}?\b(?:card|a/?c)\s+(?:no\.?\s*|ending\s+)?(?P<tail>{TAIL}))?"
            rf"(?:.*?\bon\s+(?P<date>{DATE}))?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date"}),
        notes="No merchant by nature, so its absence costs no confidence here.",
    ),
)
