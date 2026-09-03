"""HDFC Bank fixtures."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.sms.parser.types import Direction
from tests.fixtures.sms import SmsCase

CASES: tuple[SmsCase, ...] = (
    SmsCase(
        template_id="hdfc.upi.debit.v1",
        sender="VM-HDFCBK",
        body=(
            "Sent Rs.450.00 From HDFC Bank A/C x1234 To SWIGGY On 15/08/26 "
            "Ref 522398456712 Not You? Call 18002586161/SMS BLOCK UPI to 7308080808"
        ),
        direction=Direction.DEBIT,
        amount=Decimal("450.00"),
        occurred_on=date(2026, 8, 15),
        account_tail="1234",
        merchant="SWIGGY",
        reference="522398456712",
        min_confidence=Decimal("0.95"),
    ),
    SmsCase(
        template_id="hdfc.card.spend.v1",
        sender="AD-HDFCBK",
        body=(
            "Rs.1250.00 spent on HDFC Bank Card x5678 at AMAZON on 15-08-26. "
            "Not you? Call 18002586161"
        ),
        direction=Direction.DEBIT,
        amount=Decimal("1250.00"),
        occurred_on=date(2026, 8, 15),
        account_tail="5678",
        merchant="AMAZON",
        min_confidence=Decimal("0.90"),
    ),
    SmsCase(
        template_id="hdfc.credit.v1",
        sender="VM-HDFCBK",
        body=(
            "HDFC Bank: Rs. 50000.00 credited to A/c XX1234 on 01-08-26 by a/c "
            "linked to mobile 9876543210 (IMPS Ref 521912345678)"
        ),
        direction=Direction.CREDIT,
        amount=Decimal("50000.00"),
        occurred_on=date(2026, 8, 1),
        account_tail="1234",
        # A salary credit names no merchant. Its absence is correct here, and
        # the confidence deduction for it is what keeps this above the commit
        # threshold rather than comfortably clear of it.
        merchant=None,
        reference="521912345678",
    ),
    SmsCase(
        template_id="hdfc.atm.withdrawal.v1",
        sender="VM-HDFCBK",
        body="Rs.2000 withdrawn from HDFC Bank Card x5678 at HDFC ATM on 15-08-26",
        direction=Direction.DEBIT,
        amount=Decimal("2000"),
        occurred_on=date(2026, 8, 15),
        account_tail="5678",
        # No merchant, and no deduction for it: cash has no payee, so an ATM
        # withdrawal missing one is complete rather than doubtful.
        merchant=None,
        min_confidence=Decimal("0.90"),
    ),
    SmsCase(
        template_id="hdfc.upi.reversal.v1",
        sender="VM-HDFCBK",
        body="Rs.450.00 reversed to your A/c XX1234 on 16-08-26 for failed UPI txn",
        direction=Direction.CREDIT,
        amount=Decimal("450.00"),
        occurred_on=date(2026, 8, 16),
        account_tail="1234",
        merchant=None,
        reversal=True,
    ),
)
