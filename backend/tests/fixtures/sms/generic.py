"""Rail-generic fixtures, from banks with no issuer module.

These exist to prove the long tail is covered at *reduced* confidence. A generic
read from a known bank header commits; the same shape from a header we do not
recognise does not, and the last case here asserts exactly that.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.sms.parser.types import Direction
from tests.fixtures.sms import SmsCase

CASES: tuple[SmsCase, ...] = (
    SmsCase(
        template_id="generic.amount_first.v1",
        sender="AD-YESBNK",
        body="INR 799.00 debited from A/c no. XX9012 on 15-08-26 at NETFLIX.",
        direction=Direction.DEBIT,
        amount=Decimal("799.00"),
        occurred_on=date(2026, 8, 15),
        account_tail="9012",
        merchant="NETFLIX",
        min_confidence=Decimal("0.80"),
    ),
    SmsCase(
        template_id="generic.account_first.v1",
        sender="VM-IDFCFB",
        body="A/c XX3456 is debited by Rs 1200.00 on 14-08-26 to BLINKIT.",
        direction=Direction.DEBIT,
        amount=Decimal("1200.00"),
        occurred_on=date(2026, 8, 14),
        account_tail="3456",
        merchant="BLINKIT",
        min_confidence=Decimal("0.80"),
    ),
    SmsCase(
        template_id="generic.card_spend.v1",
        sender="AD-RBLBNK",
        body="Rs 349.00 spent on card ending 3344 at BIGBASKET on 16-08-26",
        direction=Direction.DEBIT,
        amount=Decimal("349.00"),
        occurred_on=date(2026, 8, 16),
        account_tail="3344",
        merchant="BIGBASKET",
        min_confidence=Decimal("0.80"),
    ),
    SmsCase(
        template_id="generic.atm_withdrawal.v1",
        sender="VM-CANBNK",
        body="Rs 3000.00 withdrawn from A/c XX7788 on 13-08-26 at CANARA ATM",
        direction=Direction.DEBIT,
        amount=Decimal("3000.00"),
        occurred_on=date(2026, 8, 13),
        account_tail="7788",
        merchant=None,
        min_confidence=Decimal("0.80"),
    ),
    SmsCase(
        template_id="generic.amount_first.v1",
        sender="VM-ZZZBNK",
        body="INR 560.00 debited from A/c no. XX2211 on 15-08-26 at DUNZO.",
        direction=Direction.DEBIT,
        amount=Decimal("560.00"),
        occurred_on=date(2026, 8, 15),
        account_tail="2211",
        merchant="DUNZO",
        # An unrecognised header tops out below the commit threshold, on
        # purpose. We do not know this sender is a bank, so the reading goes to
        # a human rather than into the ledger.
        min_confidence=Decimal("0.60"),
    ),
)
