"""Receipt -> shared price graph, and the eight rules that gate it (ADR-013).

The promotion rule is where a private receipt becomes a public price, so each
gate is asserted individually rather than through one happy path. The fourth is
the strongest and the cheapest: if the line items do not sum to the total, the
read was bad and *none* of the lines are trustworthy.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.modules.pricegraph.models import PriceObservation, Store, StoreSource
from app.modules.pricegraph.service import PricegraphService
from app.modules.receipts.models import Receipt, ReceiptLineItem, ReceiptStatus

pytestmark = pytest.mark.integration

TODAY = date.today()


async def _committed_receipt(
    session, user: uuid.UUID, *, total: str, lines: list[tuple[str, str]], confidence="0.900"
) -> Receipt:
    receipt = Receipt(
        user_id=user,
        s3_key=f"receipts/{user}/{uuid.uuid4().hex}",
        content_type="image/jpeg",
        file_size_bytes=2048,
        status=ReceiptStatus.COMMITTED.value,
        merchant_extracted="Corner Store",
        total_extracted=Decimal(total),
        date_extracted=TODAY,
    )
    session.add(receipt)
    await session.flush()

    for index, (description, price) in enumerate(lines, start=1):
        session.add(
            ReceiptLineItem(
                user_id=user,
                receipt_id=receipt.id,
                line_number=index,
                description=description,
                total_price=Decimal(price),
                confidence=Decimal(confidence),
            )
        )
    await session.flush()
    return receipt


class TestTheArithmeticGate:
    async def test_lines_that_sum_are_accepted(self, db_session, settings):
        service = PricegraphService(db_session)
        assert service.lines_add_up(Decimal("100.00"), Decimal("100.00"))
        assert service.lines_add_up(Decimal("98.00"), Decimal("100.00"))

    async def test_lines_that_do_not_sum_are_refused(self, db_session):
        """A receipt whose arithmetic does not work had a bad read, and which
        line was misread is exactly what we do not know -- so the whole receipt
        is refused rather than the one line that looks wrong."""
        service = PricegraphService(db_session)
        assert not service.lines_add_up(Decimal("50.00"), Decimal("100.00"))

    async def test_a_missing_total_is_refused(self, db_session):
        assert not PricegraphService(db_session).lines_add_up(Decimal("100.00"), None)


class TestPromotion:
    async def test_a_clean_receipt_reaches_the_graph_and_pays_points(self, registered, db_session):
        from app.workers.tasks.pricegraph import _promote

        user = uuid.UUID(registered["user"]["id"])
        db_session.add(
            Store(
                name="Corner Store",
                normalized_name="corner store",
                pincode="560001",
                latitude=Decimal("12.9716"),
                longitude=Decimal("77.5946"),
                gstin="27AAPFU0939F1ZV",
                source=StoreSource.USER_PIN.value,
            )
        )
        receipt = await _committed_receipt(
            db_session,
            user,
            total="82.00",
            lines=[("AMUL TAAZA MILK 500ML", "32.00"), ("PARLE-G BISCUIT 800G", "50.00")],
        )
        # The GSTIN field is what resolves the store; a receipt without one
        # falls back to name-plus-PIN, which is tested separately.
        from app.modules.receipts.models import ReceiptField

        db_session.add(
            ReceiptField(
                user_id=user,
                receipt_id=receipt.id,
                field_name="gstin",
                raw_text="27AAPFU0939F1ZV",
                parsed_value="27AAPFU0939F1ZV",
                confidence=Decimal("1.000"),
                needs_review=False,
            )
        )
        await db_session.commit()

        result = await _promote(receipt.id, user)

        assert result["status"] == "ok", result
        assert result["promoted"] == 2, result

        observations = (
            await db_session.execute(text("SELECT count(*) FROM price_observations"))
        ).scalar_one()
        assert observations == 2

        from app.modules.points.service import PointsService

        assert await PointsService(db_session).balance(user) > 0

    async def test_a_receipt_that_is_not_committed_is_refused(self, registered, db_session):
        """Never promote from NEEDS_REVIEW.

        The whole basis of the rule is that a human confirmed the merchant, the
        date and the total.
        """
        from app.workers.tasks.pricegraph import _promote

        user = uuid.UUID(registered["user"]["id"])
        receipt = await _committed_receipt(
            db_session, user, total="32.00", lines=[("AMUL TAAZA MILK 500ML", "32.00")]
        )
        receipt.status = ReceiptStatus.NEEDS_REVIEW.value
        await db_session.commit()

        assert (await _promote(receipt.id, user))["reason"] == "not_committed"

    async def test_a_receipt_whose_lines_do_not_sum_is_refused(self, registered, db_session):
        from app.workers.tasks.pricegraph import _promote

        user = uuid.UUID(registered["user"]["id"])
        receipt = await _committed_receipt(
            db_session, user, total="500.00", lines=[("AMUL TAAZA MILK 500ML", "32.00")]
        )
        await db_session.commit()

        assert (await _promote(receipt.id, user))["reason"] == "lines_do_not_sum"

    async def test_an_unresolvable_store_is_refused(self, registered, db_session):
        """A price with no located seller compares nothing."""
        from app.workers.tasks.pricegraph import _promote

        user = uuid.UUID(registered["user"]["id"])
        receipt = await _committed_receipt(
            db_session, user, total="32.00", lines=[("AMUL TAAZA MILK 500ML", "32.00")]
        )
        await db_session.commit()

        assert (await _promote(receipt.id, user))["reason"] == "store_unresolved"

    async def test_a_low_confidence_line_is_skipped(self, registered, db_session, settings):
        """The bar to *publish* is higher than the bar to show you your own
        data: 0.80 against OCR's 0.75."""
        from app.workers.tasks.pricegraph import _promote

        user = uuid.UUID(registered["user"]["id"])
        db_session.add(
            Store(
                name="Corner Store",
                normalized_name="corner store",
                gstin="29AAGCB7383J1Z4",
                source=StoreSource.USER_PIN.value,
            )
        )
        receipt = await _committed_receipt(
            db_session,
            user,
            total="32.00",
            lines=[("AMUL TAAZA MILK 500ML", "32.00")],
            confidence="0.500",
        )
        from app.modules.receipts.models import ReceiptField

        db_session.add(
            ReceiptField(
                user_id=user,
                receipt_id=receipt.id,
                field_name="gstin",
                parsed_value="29AAGCB7383J1Z4",
                raw_text="29AAGCB7383J1Z4",
                confidence=Decimal("1.000"),
                needs_review=False,
            )
        )
        await db_session.commit()

        result = await _promote(receipt.id, user)
        assert result["promoted"] == 0
        assert result["skipped"] == 1


class TestTheSharedRowCarriesNoBasket:
    async def test_two_items_from_one_receipt_do_not_reference_each_other(
        self, registered, db_session
    ):
        """Item co-occurrence on one receipt is a fingerprint that
        re-identifies people with no name attached. The shared row records one
        item, never the shopping trip it came from."""
        from app.modules.receipts.models import ReceiptField
        from app.workers.tasks.pricegraph import _promote

        user = uuid.UUID(registered["user"]["id"])
        db_session.add(
            Store(
                name="Corner Store",
                normalized_name="corner store",
                gstin="24AAACC1206D1ZM",
                source=StoreSource.USER_PIN.value,
            )
        )
        receipt = await _committed_receipt(
            db_session,
            user,
            total="82.00",
            lines=[("AMUL TAAZA MILK 500ML", "32.00"), ("PARLE-G BISCUIT 800G", "50.00")],
        )
        db_session.add(
            ReceiptField(
                user_id=user,
                receipt_id=receipt.id,
                field_name="gstin",
                parsed_value="24AAACC1206D1ZM",
                raw_text="24AAACC1206D1ZM",
                confidence=Decimal("1.000"),
                needs_review=False,
            )
        )
        await db_session.commit()
        await _promote(receipt.id, user)

        rows = (
            await db_session.execute(
                text("SELECT contributor_hash, observed_on FROM price_observations")
            )
        ).all()
        assert len(rows) == 2
        # Same contributor, same store, same day -- so the hashes match, which
        # is what the one-per-day constraint is built on. Nothing else links
        # them: no receipt id, no line number, no timestamp.
        assert rows[0].contributor_hash == rows[1].contributor_hash

        columns = set(PriceObservation.__table__.columns.keys())
        assert not {"receipt_id", "receipt_line_item_id", "basket_id"} & columns
