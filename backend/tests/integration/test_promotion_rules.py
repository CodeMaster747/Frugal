"""The promotion conditions that were documented and not implemented.

ADR-013 lists eight conditions. Six were coded; two were prose. `min_contributor
_trust` was read nowhere in the codebase, and the daily cap gated only the
*payout* while the price was published regardless -- the opposite of what a
volume control is for.

Also here: the three values the audit found written by nobody or read by nobody
(`Store.confirmed_at`, `Store.gstin`, `receipt_promotions.points_awarded`) and
the re-promotion path that did not exist.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.modules.pricegraph.models import Store, StoreSource
from app.modules.receipts.models import Receipt, ReceiptField, ReceiptLineItem, ReceiptStatus

pytestmark = pytest.mark.integration

TODAY = date.today()
#: A checksum-valid GSTIN. `store_identity.gstin_checksum_ok` rejects anything
#: shaped right but not real, so a made-up one would silently never be stored.
GSTIN = "27AAPFU0939F1ZV"


async def _store(db_session, **overrides) -> Store:
    store = Store(
        **{
            "name": "Corner Store",
            "normalized_name": "corner store",
            "pincode": "600001",
            "latitude": Decimal("13.0827"),
            "longitude": Decimal("80.2707"),
            "source": StoreSource.USER_PIN.value,
        }
        | overrides
    )
    db_session.add(store)
    await db_session.flush()
    return store


async def _receipt(
    db_session, user: uuid.UUID, *, gstin: str | None = GSTIN, pincode: str | None = "600001"
) -> Receipt:
    receipt = Receipt(
        user_id=user,
        s3_key=f"receipts/{user}/{uuid.uuid4().hex}",
        content_type="image/jpeg",
        file_size_bytes=2048,
        status=ReceiptStatus.COMMITTED.value,
        merchant_extracted="Corner Store",
        total_extracted=Decimal("32.00"),
        date_extracted=TODAY,
    )
    db_session.add(receipt)
    await db_session.flush()

    db_session.add(
        ReceiptLineItem(
            user_id=user,
            receipt_id=receipt.id,
            line_number=1,
            description="AMUL TAAZA MILK 500ML",
            total_price=Decimal("32.00"),
            confidence=Decimal("0.900"),
        )
    )
    for name, value in (("gstin", gstin), ("store_pincode", pincode)):
        if not value:
            continue
        db_session.add(
            ReceiptField(
                user_id=user,
                receipt_id=receipt.id,
                field_name=name,
                raw_text=value,
                parsed_value=value,
                confidence=Decimal("1.000"),
                needs_review=False,
            )
        )
    await db_session.flush()
    return receipt


class TestTheTrustFloor:
    async def test_an_untrusted_contributor_is_refused(self, registered, db_session):
        """Condition 6, which was prose for a milestone.

        A user whose trust has been driven to zero by disputes and retractions
        published to the shared graph exactly as freely as anyone else.
        """
        from app.modules.community.models import UserTrust
        from app.workers.tasks.pricegraph import _promote

        user = uuid.UUID(registered["user"]["id"])
        await _store(db_session, gstin=GSTIN)
        receipt = await _receipt(db_session, user)

        db_session.add(UserTrust(user_id=user, score=Decimal("0.000"), retracted_contributions=20))
        await db_session.commit()

        assert (await _promote(receipt.id, user))["reason"] == "contributor_untrusted"

    async def test_a_new_contributor_clears_it(self, registered, db_session, settings):
        """The floor must not lock out the people it needs most.

        `STARTING_TRUST` sits at the floor on purpose: a new user's first
        receipt has to be promotable, because promotion is what earns the trust
        that promotion would otherwise require.
        """
        from app.modules.community.trust import STARTING_TRUST
        from app.workers.tasks.pricegraph import _promote

        user = uuid.UUID(registered["user"]["id"])
        await _store(db_session, gstin=GSTIN)
        receipt = await _receipt(db_session, user)
        await db_session.commit()

        assert settings.min_contributor_trust <= STARTING_TRUST
        assert (await _promote(receipt.id, user))["status"] == "ok"


class TestTheDailyCap:
    async def test_it_stops_the_observation_not_only_the_payout(
        self, registered, db_session, settings
    ):
        """Condition 8.

        The cap lived inside `PointsService.award`, so a user past their
        allowance still published prices to everybody and merely stopped being
        paid for them.

        The allowance is spent for real rather than by patching a setting:
        `get_settings` is `lru_cache`d and the promotion task runs on its own
        session, so a patched instance does not reach it -- and spending it
        exercises the ledger, which is the thing under test.
        """
        from app.modules.pricegraph.service import PricegraphService
        from app.workers.tasks.pricegraph import _promote

        user = uuid.UUID(registered["user"]["id"])
        await _store(db_session, gstin=GSTIN)
        receipt = await _receipt(db_session, user)
        await db_session.commit()

        graph = PricegraphService(db_session)
        for _ in range(settings.price_contributions_per_user_daily_cap):
            assert await graph.reserve_contribution(user)
        assert not await graph.reserve_contribution(user), "the cap should now be spent"
        await db_session.commit()

        result = await _promote(receipt.id, user)
        assert result["promoted"] == 0

        written = (
            await db_session.execute(text("SELECT count(*) FROM price_observations"))
        ).scalar_one()
        assert written == 0, "the cap must refuse the price, not merely the points"


class TestGstinCapture:
    async def test_a_receipt_teaches_the_store_its_registration(self, registered, db_session):
        """`Store.gstin` is `resolve_store`'s strongest evidence and was written
        by nothing, so that branch could never match."""
        from app.workers.tasks.pricegraph import _promote

        user = uuid.UUID(registered["user"]["id"])
        store = await _store(db_session, gstin=None)
        receipt = await _receipt(db_session, user)
        await db_session.commit()

        await _promote(receipt.id, user)

        await db_session.refresh(store)
        assert store.gstin == GSTIN

    async def test_it_never_steals_a_registration(self, registered, db_session):
        """Two shops cannot share one registration, and choosing between them
        from here would be guessing."""
        from app.modules.pricegraph.service import PricegraphService

        first = await _store(db_session, gstin=GSTIN)
        second = await _store(db_session, name="Other Store", gstin=None)
        await db_session.flush()

        await PricegraphService(db_session).claim_gstin(second, GSTIN)
        await db_session.flush()

        assert second.gstin is None
        assert first.gstin == GSTIN


class TestPointsAwardedIsRecorded:
    async def test_a_contribution_reports_what_it_earned(self, registered, db_session):
        """`receipt_promotions.points_awarded` was written by nobody and read by
        the API and the frontend, so every contribution reported 0 to its own
        contributor."""
        from app.workers.tasks.pricegraph import _promote

        user = uuid.UUID(registered["user"]["id"])
        await _store(db_session, gstin=GSTIN)
        receipt = await _receipt(db_session, user)
        await db_session.commit()

        assert (await _promote(receipt.id, user))["promoted"] == 1

        awarded = (
            await db_session.execute(text("SELECT points_awarded FROM receipt_promotions LIMIT 1"))
        ).scalar_one()
        assert awarded > 0


class TestStoreConfirmation:
    async def test_two_confirmers_clear_the_banner_and_pay_the_pin(
        self, client, auth_headers, second_user_headers, registered, db_session
    ):
        from app.modules.points.service import PointsService

        pinned = await client.post(
            "/api/v1/pricegraph/stores/pin",
            headers=auth_headers,
            json={"name": "Sri Lakshmi Stores", "latitude": "13.0827", "longitude": "80.2707"},
        )
        assert pinned.status_code == 201, pinned.text
        store_id = pinned.json()["id"]
        assert pinned.json()["confirmed"] is False

        third = await client.post(
            "/api/v1/auth/register",
            json={
                "email": "confirmer@example.com",
                "password": "CorrectHorse9Battery",
                "display_name": "Confirmer",
                "base_currency": "INR",
            },
        )
        third_headers = {"Authorization": f"Bearer {third.json()['access_token']}"}

        first = await client.post(
            f"/api/v1/pricegraph/stores/{store_id}/confirm", headers=second_user_headers
        )
        assert first.json()["confirmed"] is False, "one voucher is one person's word"

        second = await client.post(
            f"/api/v1/pricegraph/stores/{store_id}/confirm", headers=third_headers
        )
        assert second.json()["confirmed"] is True

        detail = (await client.get(f"/api/v1/pricegraph/stores/{store_id}")).json()
        assert detail["confirmed"] is True

        # The pin's author is paid, never the confirmer: clicking "yes" would
        # otherwise be the cheapest contribution in the system.
        author = uuid.UUID(registered["user"]["id"])
        assert await PointsService(db_session).balance(author) > 0

    async def test_one_confirmation_per_person(self, client, auth_headers, second_user_headers):
        pinned = await client.post(
            "/api/v1/pricegraph/stores/pin",
            headers=auth_headers,
            json={"name": "Sri Lakshmi Stores", "latitude": "13.0827", "longitude": "80.2707"},
        )
        store_id = pinned.json()["id"]

        assert (
            await client.post(
                f"/api/v1/pricegraph/stores/{store_id}/confirm", headers=second_user_headers
            )
        ).status_code == 200
        assert (
            await client.post(
                f"/api/v1/pricegraph/stores/{store_id}/confirm", headers=second_user_headers
            )
        ).status_code == 409


class TestRePromotion:
    async def test_pinning_a_shop_reconsiders_an_orphaned_receipt(self, registered, db_session):
        """The obvious sequence: upload a bill, notice the shop is missing, pin
        it. Without a retry the bill stays permanently unpromoted with nothing
        to say why."""
        from app.workers.tasks.pricegraph import _promote, _retry

        user = uuid.UUID(registered["user"]["id"])
        receipt = await _receipt(db_session, user)
        await db_session.commit()

        # No store exists yet.
        assert (await _promote(receipt.id, user))["reason"] == "store_unresolved"

        await _store(db_session, gstin=GSTIN)
        await db_session.commit()

        result = await _retry(user)
        assert result["promoted"] == 1, result

        written = (
            await db_session.execute(text("SELECT count(*) FROM price_observations"))
        ).scalar_one()
        assert written == 1

    async def test_it_ignores_receipts_that_already_contributed(self, registered, db_session):
        from app.workers.tasks.pricegraph import _promote, _retry

        user = uuid.UUID(registered["user"]["id"])
        await _store(db_session, gstin=GSTIN)
        receipt = await _receipt(db_session, user)
        await db_session.commit()

        await _promote(receipt.id, user)
        assert (await _retry(user))["considered"] == 0
