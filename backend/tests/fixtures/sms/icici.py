"""ICICI Bank fixtures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.sms.parser.types import Direction
from tests.fixtures.sms import SmsCase

CASES: tuple[SmsCase, ...] = (
    SmsCase(
        template_id="icici.account.debit.v1",
        sender="JD-ICICIB",
        body=(
            "Dear Customer, Acct XX123 is debited with INR 500.00 on 15-Aug-26. "
            "Info: UPI/522398456712/Payment to swiggy. "
            "Available Balance is INR 12345.67."
        ),
        direction=Direction.DEBIT,
        amount=Decimal("500.00"),
        occurred_on=date(2026, 8, 15),
        account_tail="123",
        # "Payment to swiggy" reduces to "swiggy", which is what the CSV
        # narration UPI/SWIGGY/883012 reduces to as well. That equality is what
        # stops the same purchase booking twice.
        merchant="swiggy",
        reference="522398456712",
    ),
    SmsCase(
        template_id="icici.account.credit.v1",
        sender="JD-ICICIB",
        body=(
            "ICICI Bank Acct XX123 credited with Rs 25000.00 on 01-Aug-26. "
            "Info INF/NEFT/ACME PAYROLL."
        ),
        direction=Direction.CREDIT,
        amount=Decimal("25000.00"),
        occurred_on=date(2026, 8, 1),
        account_tail="123",
        merchant="INF/NEFT/ACME PAYROLL",
    ),
    SmsCase(
        template_id="icici.card.spend.v1",
        sender="JD-ICICIB",
        body=(
            "INR 1,250.00 spent using ICICI Bank Card XX5678 on 15-Aug-26 on AMAZON. "
            "Avl Lmt: INR 45,000."
        ),
        direction=Direction.DEBIT,
        amount=Decimal("1250.00"),
        occurred_on=date(2026, 8, 15),
        account_tail="5678",
        merchant="AMAZON",
        min_confidence=Decimal("0.90"),
    ),
)
