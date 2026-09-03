"""Rail-shaped fallbacks for account movement, matched on body alone.

Reached only when no issuer template matched. India has far more banks than
this package has modules, and most of them write one of two orderings:
amount-first ("INR 450 debited from A/c XX1234") or account-first ("A/c XX1234
debited by Rs 450"). Covering both catches a long tail of issuers for two
patterns.

The cost is precision, which is why a generic match never reaches issuer
confidence and a generic match from an *unknown* sender is always reviewed.
"""

from __future__ import annotations

import re

from app.modules.sms.parser.normalize import AMOUNT, DATE, TAIL
from app.modules.sms.parser.types import Rail, Template

_FLAGS = re.IGNORECASE

TEMPLATES: tuple[Template, ...] = (
    Template(
        id="generic.amount_first.v1",
        issuer="",
        senders=(),
        rail=Rail.UNKNOWN,
        pattern=re.compile(
            rf"(?:inr|rs\.?|₹)\s*(?P<amount>{AMOUNT})\s+"
            rf"(?:has\s+been\s+|was\s+|is\s+)?(?P<dir>debited|credited)\s+"
            rf"(?:from|to|in)\s+(?:your\s+)?a/?c\s+(?:no\.?\s*)?(?P<tail>{TAIL})"
            rf"(?:.*?\bon\s+(?P<date>{DATE}))?"
            rf"(?:.*?\b(?:at|to|from|trf\s+to)\s+(?P<merchant>[^.]{{2,60}}?)\s*(?:\.|$))?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "dir", "tail", "date", "merchant"}),
    ),
    Template(
        id="generic.account_first.v1",
        issuer="",
        senders=(),
        rail=Rail.UNKNOWN,
        pattern=re.compile(
            rf"a/?c\s+(?:no\.?\s*)?(?P<tail>{TAIL})\s+"
            rf"(?:is\s+|has\s+been\s+|was\s+)?(?P<dir>debited|credited)\s+"
            rf"(?:with|by|for)?\s*(?:inr|rs\.?|₹)?\s*(?P<amount>{AMOUNT})"
            rf"(?:.*?\bon\s+(?:date\s+)?(?P<date>{DATE}))?"
            rf"(?:.*?\b(?:at|to|from|trf\s+to)\s+(?P<merchant>[^.]{{2,60}}?)\s*(?:\.|$))?",
            _FLAGS,
        ),
        provides=frozenset({"tail", "dir", "amount", "date", "merchant"}),
    ),
)
