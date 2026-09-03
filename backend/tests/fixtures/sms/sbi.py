"""State Bank of India fixtures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.sms.parser.types import Direction
from tests.fixtures.sms import SmsCase

CASES: tuple[SmsCase, ...] = (
    SmsCase(
        template_id="sbi.upi.debit.v1",
        sender="AX-SBIINB",
        body=(
            "Dear UPI user A/C X1234 debited by 450.0 on date 15Aug26 trf to SWIGGY "
            "Refno 522398456712. If not u? call 1800111109. -SBI"
        ),
        direction=Direction.DEBIT,
        amount=Decimal("450.0"),
        occurred_on=date(2026, 8, 15),
        account_tail="1234",
        merchant="SWIGGY",
        reference="522398456712",
        min_confidence=Decimal("0.95"),
    ),
    SmsCase(
        template_id="sbi.account.credit.v1",
        sender="AX-SBIINB",
        body=(
            "Dear Customer, Your A/c XX1234-credited by Rs.50000 on 01Aug26 "
            "transfer from MR EMPLOYER Ref No 521912345678 -SBI"
        ),
        direction=Direction.CREDIT,
        amount=Decimal("50000"),
        occurred_on=date(2026, 8, 1),
        account_tail="1234",
        # The whole name, not "M". A lazy quantifier before an optional group
        # stops at its minimum, which is what this case exists to catch.
        merchant="MR EMPLOYER",
        reference="521912345678",
        min_confidence=Decimal("0.95"),
    ),
    SmsCase(
        template_id="sbi.account.debit.v1",
        sender="AX-SBIINB",
        body="Your a/c no. XX1234 is debited for Rs.1500.00 on 12-08-26 -SBI",
        direction=Direction.DEBIT,
        amount=Decimal("1500.00"),
        occurred_on=date(2026, 8, 12),
        account_tail="1234",
        merchant=None,
    ),
)
