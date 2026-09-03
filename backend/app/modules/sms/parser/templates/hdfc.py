"""HDFC Bank message shapes."""

from __future__ import annotations

import re

from app.modules.sms.parser.normalize import AMOUNT, DATE, TAIL
from app.modules.sms.parser.types import Direction, Rail, Template

_SENDERS = ("HDFCBK", "HDFCBN")
_FLAGS = re.IGNORECASE

TEMPLATES: tuple[Template, ...] = (
    Template(
        id="hdfc.upi.debit.v1",
        issuer="HDFC",
        senders=_SENDERS,
        rail=Rail.UPI,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"sent\s+(?:rs\.?|inr)\s*(?P<amount>{AMOUNT})\s+"
            rf"from\s+hdfc\s+bank\s+a/?c\s+(?P<tail>{TAIL})\s+"
            rf"to\s+(?P<merchant>.+?)\s+"
            rf"on\s+(?P<date>{DATE})"
            rf"(?:.*?\bref\s*(?:no\.?)?\s*(?P<ref>\d{{6,}}))?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "merchant", "date", "ref"}),
        notes="The dominant UPI shape since 2023. 'To' may hold a name or a VPA.",
    ),
    Template(
        id="hdfc.card.spend.v1",
        issuer="HDFC",
        senders=_SENDERS,
        rail=Rail.CARD,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"(?:rs\.?|inr)\s*(?P<amount>{AMOUNT})\s+spent\s+on\s+"
            rf"hdfc\s+bank\s+(?:credit\s+|debit\s+)?card\s+(?P<tail>{TAIL})\s+"
            rf"at\s+(?P<merchant>.+?)\s+on\s+(?P<date>{DATE})",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "merchant", "date"}),
        notes="Carries no reference number; the timestamp follows the date after a colon.",
    ),
    Template(
        id="hdfc.credit.v1",
        issuer="HDFC",
        senders=_SENDERS,
        rail=Rail.NETBANKING,
        direction=Direction.CREDIT,
        pattern=re.compile(
            rf"(?:rs\.?|inr)\s*(?P<amount>{AMOUNT})\s+credited\s+to\s+"
            rf"(?:your\s+)?a/?c\s+(?P<tail>{TAIL})\s+on\s+(?P<date>{DATE})"
            rf"(?:.*?\bref\s*(?:no\.?)?\s*(?P<ref>\d{{6,}}))?",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date", "ref"}),
        notes="Salary and IMPS credits. No merchant -- the sender is in free text.",
    ),
    Template(
        id="hdfc.atm.withdrawal.v1",
        issuer="HDFC",
        senders=_SENDERS,
        rail=Rail.ATM,
        direction=Direction.DEBIT,
        pattern=re.compile(
            rf"(?:rs\.?|inr)\s*(?P<amount>{AMOUNT})\s+withdrawn\s+from\s+"
            rf"hdfc\s+bank\s+(?:atm\s+)?card\s+(?P<tail>{TAIL})"
            rf".*?\bon\s+(?P<date>{DATE})",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date"}),
        notes="A cash withdrawal has no merchant by nature, so none is expected.",
    ),
    Template(
        id="hdfc.upi.reversal.v1",
        issuer="HDFC",
        senders=_SENDERS,
        rail=Rail.UPI,
        direction=Direction.CREDIT,
        reversal=True,
        pattern=re.compile(
            rf"(?:rs\.?|inr)\s*(?P<amount>{AMOUNT})\s+"
            rf"(?:reversed|refunded)\s+(?:to|in)\s+(?:your\s+)?a/?c\s+(?P<tail>{TAIL})"
            rf".*?\bon\s+(?P<date>{DATE})",
            _FLAGS,
        ),
        provides=frozenset({"amount", "tail", "date"}),
        notes=(
            "Parses as a credit, which is arithmetically right and semantically "
            "wrong -- a refund reduces an expense rather than being income. "
            "Always reviewed; see Template.reversal."
        ),
    ),
)
