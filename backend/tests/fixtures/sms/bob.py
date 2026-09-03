"""Bank of Baroda fixtures.

BoB's UPI debit omits the date, which makes it the one issuer that exercises
date inference on every message rather than as an edge case.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.sms.parser.types import Direction
from tests.fixtures.sms import SmsCase

CASES: tuple[SmsCase, ...] = (
    SmsCase(
        template_id="bob.upi.debit.v1",
        sender="VM-BOBSMS",
        body=(
            "Rs.450.00 debited from A/c XX1234 and credited to swiggy@ybl "
            "(UPI Ref no 522398456712) -Bank of Baroda"
        ),
        direction=Direction.DEBIT,
        amount=Decimal("450.00"),
        # No date in the body, so the day it arrived stands in -- and the
        # reading says so rather than presenting a guess as a reading.
        occurred_on=date(2026, 8, 17),
        date_inferred=True,
        account_tail="1234",
        merchant="swiggy",
        vpa="swiggy@ybl",
        reference="522398456712",
        min_confidence=Decimal("0.80"),
    ),
    SmsCase(
        template_id="bob.account.credit.v1",
        sender="VM-BOBSMS",
        body="Rs.15000.00 credited to A/c XX1234 on 01-08-26 Ref no 521912345678 -Bank of Baroda",
        direction=Direction.CREDIT,
        amount=Decimal("15000.00"),
        occurred_on=date(2026, 8, 1),
        account_tail="1234",
        merchant=None,
        reference="521912345678",
    ),
)
