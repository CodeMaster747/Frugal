"""Punjab National Bank fixtures.

One template serves both directions, so both are exercised here -- a shared
pattern that silently stopped reading credits would otherwise still pass.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.sms.parser.types import Direction
from tests.fixtures.sms import SmsCase

CASES: tuple[SmsCase, ...] = (
    SmsCase(
        template_id="pnb.account.movement.v1",
        sender="AD-PNBSMS",
        body=(
            "Dear Customer, Your A/c XX1234 Debited INR 450.00 on 15/08/26 . "
            "Info: UPI/522398456712/SWIGGY. Bal INR 12345.67 -PNB"
        ),
        direction=Direction.DEBIT,
        amount=Decimal("450.00"),
        occurred_on=date(2026, 8, 15),
        account_tail="1234",
        merchant="SWIGGY",
        reference="522398456712",
    ),
    SmsCase(
        template_id="pnb.account.movement.v1",
        sender="AD-PNBSMS",
        body=(
            "Dear Customer, Your A/c XX1234 Credited INR 30000.00 on 01/08/26 . "
            "Info: NEFT/ACME PAYROLL. Bal INR 42345.67 -PNB"
        ),
        direction=Direction.CREDIT,
        amount=Decimal("30000.00"),
        occurred_on=date(2026, 8, 1),
        account_tail="1234",
        # The rail prefix survives here, and should. `clean_merchant` is
        # deliberately light -- `normalize_merchant` in the finance module is
        # the canonical reducer and strips "neft" itself, so both this and the
        # CSV narration for the same credit land on "acme payroll". Duplicating
        # that stripping here would be a second reducer to keep in step.
        merchant="NEFT/ACME PAYROLL",
    ),
)
