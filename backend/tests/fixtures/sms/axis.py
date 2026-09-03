"""Axis Bank fixtures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.sms.parser.types import Direction
from tests.fixtures.sms import SmsCase

CASES: tuple[SmsCase, ...] = (
    SmsCase(
        template_id="axis.account.debit.v1",
        sender="VK-AXISBK",
        body=(
            "INR 450.00 debited from A/c no. XX1234 on 15-08-26, 13:45:12 IST at SWIGGY. "
            "Avl Bal INR 12345.67. Not you? SMS BLOCKAXIS to 9876543210"
        ),
        direction=Direction.DEBIT,
        amount=Decimal("450.00"),
        occurred_on=date(2026, 8, 15),
        account_tail="1234",
        merchant="SWIGGY",
        min_confidence=Decimal("0.90"),
    ),
    SmsCase(
        template_id="axis.account.credit.v1",
        sender="VK-AXISBK",
        body="INR 25000.00 credited to A/c no. XX1234 on 01-08-26. Info- NEFT/ACME PAYROLL.",
        direction=Direction.CREDIT,
        amount=Decimal("25000.00"),
        occurred_on=date(2026, 8, 1),
        account_tail="1234",
        merchant="NEFT/ACME PAYROLL",
        min_confidence=Decimal("0.90"),
    ),
    SmsCase(
        template_id="axis.card.spend.v1",
        sender="VK-AXISBK",
        body="Spent Card no. XX5678 INR 1250 15-08-26 13:45:12 AMAZON Avl Lmt INR 45000",
        direction=Direction.DEBIT,
        amount=Decimal("1250"),
        occurred_on=date(2026, 8, 15),
        account_tail="5678",
        merchant="AMAZON",
        min_confidence=Decimal("0.90"),
    ),
)
