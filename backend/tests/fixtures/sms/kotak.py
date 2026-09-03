"""Kotak Mahindra Bank fixtures.

Kotak names counterparties by VPA, which makes it the issuer where the opaque-
handle rule actually bites: half of real traffic resolves to a merchant and half
resolves to nobody.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.modules.sms.parser.types import Direction
from tests.fixtures.sms import SmsCase

CASES: tuple[SmsCase, ...] = (
    SmsCase(
        template_id="kotak.upi.debit.v1",
        sender="BP-KOTAKB",
        body="Sent Rs.450.00 from Kotak Bank AC X1234 to swiggy@ybl on 15-08-26.UPI Ref 522398456712.",
        direction=Direction.DEBIT,
        amount=Decimal("450.00"),
        occurred_on=date(2026, 8, 15),
        account_tail="1234",
        merchant="swiggy",
        vpa="swiggy@ybl",
        reference="522398456712",
        min_confidence=Decimal("0.95"),
    ),
    SmsCase(
        template_id="kotak.upi.credit.v1",
        sender="BP-KOTAKB",
        body=(
            "Received Rs.5000.00 in your Kotak Bank AC X1234 from 9876543210@ybl "
            "on 15-08-26.UPI Ref:522398456712."
        ),
        direction=Direction.CREDIT,
        amount=Decimal("5000.00"),
        occurred_on=date(2026, 8, 15),
        account_tail="1234",
        # A bare phone number names nobody. None is the honest reading --
        # storing "9876543210" as a merchant would pollute the trigram index
        # and train the categoriser on a label it can never generalise.
        merchant=None,
        vpa="9876543210@ybl",
        reference="522398456712",
    ),
)
