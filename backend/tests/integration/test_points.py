"""The points ledger (ADR-013).

Points on crowdsourced data invite farming, so the controls are the substance
of this feature rather than an afterthought. Four of them are asserted here, and
all four are structural rather than procedural:

- awarding is idempotent, enforced by a unique constraint
- a daily cap limits *awards*, not points
- reversal is a compensating row, never an update
- the balance is a SUM over immutable rows, so it cannot drift from its history
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from app.modules.points.models import POINTS, PointsReason
from app.modules.points.service import PointsService

pytestmark = pytest.mark.integration


class TestAwarding:
    async def test_an_award_credits_the_balance(self, registered, db_session):
        user = uuid.UUID(registered["user"]["id"])
        service = PointsService(db_session)

        await service.award(user, PointsReason.STORE_REPORT, subject_id=uuid.uuid4())
        await db_session.commit()

        assert await service.balance(user) == POINTS[PointsReason.STORE_REPORT]

    async def test_awarding_twice_for_one_thing_pays_once(self, registered, db_session):
        """A worker retried after a broker timeout must not pay twice.

        Enforced by the unique key rather than by the task remembering, because
        the task is exactly the thing that just failed.
        """
        user = uuid.UUID(registered["user"]["id"])
        subject = uuid.uuid4()
        service = PointsService(db_session)

        first = await service.award(user, PointsReason.STORE_REPORT, subject_id=subject)
        second = await service.award(user, PointsReason.STORE_REPORT, subject_id=subject)
        await db_session.commit()

        assert first is not None
        assert second is None
        assert await service.balance(user) == POINTS[PointsReason.STORE_REPORT]

    async def test_the_database_enforces_idempotency(self, db_session):
        constraints = (
            (
                await db_session.execute(
                    text("""
                    SELECT conname FROM pg_constraint
                     WHERE conrelid = 'points_ledger'::regclass AND contype = 'u'
                    """)
                )
            )
            .scalars()
            .all()
        )
        assert "uq_points_ledger_idempotency_key" in constraints

    async def test_an_unpriced_reason_is_refused_loudly(self, registered, db_session):
        """A points economy nobody can predict is one nobody trusts."""
        user = uuid.UUID(registered["user"]["id"])
        with pytest.raises(ValueError, match="no value in POINTS"):
            await PointsService(db_session).award(user, PointsReason.REVERSAL)


class TestTheDailyCap:
    async def test_it_stops_awarding_past_the_cap(self, registered, db_session, settings):
        """The cap is on *awards*, not points.

        A cap on points would make the cheapest contribution the most efficient
        way to farm, which is the opposite of the intent.
        """
        user = uuid.UUID(registered["user"]["id"])
        service = PointsService(db_session)
        cap = settings.points_awards_per_user_daily_cap

        awarded = 0
        for _ in range(cap + 5):
            if await service.award(user, PointsReason.REPORT_UPVOTED, subject_id=uuid.uuid4()):
                awarded += 1
        await db_session.commit()

        assert awarded == cap, f"awarded {awarded} against a cap of {cap}"

    async def test_reaching_the_cap_is_not_an_error(self, registered, db_session, settings):
        """It is the ordinary outcome of a control working. Raising would make
        every caller handle a non-exception."""
        user = uuid.UUID(registered["user"]["id"])
        service = PointsService(db_session)

        for _ in range(settings.points_awards_per_user_daily_cap + 1):
            result = await service.award(user, PointsReason.REPORT_UPVOTED, subject_id=uuid.uuid4())
        assert result is None


class TestReversal:
    async def test_it_writes_a_compensating_row(self, registered, db_session):
        """Never an update or a delete.

        The point of an append-only ledger is that a reversal is visible as a
        reversal -- to an operator investigating abuse, and to the user about to
        ask why their balance dropped.
        """
        user = uuid.UUID(registered["user"]["id"])
        service = PointsService(db_session)

        award = await service.award(user, PointsReason.STORE_REPORT, subject_id=uuid.uuid4())
        assert award is not None
        await service.reverse(user, award.id, note="duplicate report")
        await db_session.commit()

        assert await service.balance(user) == 0

        history = await service.history(user)
        assert len(history) == 2
        assert {e.reason for e in history} == {
            PointsReason.STORE_REPORT.value,
            PointsReason.REVERSAL.value,
        }
        assert any(e.reverses_id == award.id for e in history)

    async def test_reversing_twice_is_a_no_op(self, registered, db_session):
        user = uuid.UUID(registered["user"]["id"])
        service = PointsService(db_session)

        award = await service.award(user, PointsReason.STORE_REPORT, subject_id=uuid.uuid4())
        assert award is not None
        await service.reverse(user, award.id, note="first")
        await service.reverse(user, award.id, note="second")
        await db_session.commit()

        assert await service.balance(user) == 0

    async def test_one_user_cannot_reverse_anothers_award(self, registered, db_session):
        user = uuid.UUID(registered["user"]["id"])
        service = PointsService(db_session)

        award = await service.award(user, PointsReason.STORE_REPORT, subject_id=uuid.uuid4())
        assert award is not None
        assert await service.reverse(uuid.uuid4(), award.id, note="not mine") is None


class TestTheApi:
    async def test_the_balance_says_redemption_is_not_available(self, client, auth_headers):
        """Said out loud rather than left to be discovered. A product that
        implies points are spendable when they are not is lying by omission."""
        body = (await client.get("/api/v1/points/balance", headers=auth_headers)).json()

        assert body["balance"] == 0
        assert body["redeemable"] is False
        assert "not available yet" in body["message"]

    async def test_the_catalogue_is_seeded_and_visible(self, client, auth_headers):
        rewards = (await client.get("/api/v1/rewards", headers=auth_headers)).json()

        assert rewards, "the catalogue is seeded by migration 0021"
        assert all(r["is_available"] is False for r in rewards)
        assert all(r["cost_points"] > 0 for r in rewards)

    async def test_redeeming_returns_501_and_explains(self, client, auth_headers):
        """501, not 404: the endpoint exists and the shape is settled; what is
        missing is fulfilment. A user who tries this should learn that."""
        rewards = (await client.get("/api/v1/rewards", headers=auth_headers)).json()
        response = await client.post(
            f"/api/v1/rewards/{rewards[0]['id']}/redeem", headers=auth_headers
        )

        assert response.status_code == 501
        assert "not available yet" in response.text

    async def test_the_ledger_is_tenant_scoped(
        self, client, auth_headers, second_user_headers, registered, db_session
    ):
        user = uuid.UUID(registered["user"]["id"])
        await PointsService(db_session).award(
            user, PointsReason.STORE_REPORT, subject_id=uuid.uuid4()
        )
        await db_session.commit()

        mine = (await client.get("/api/v1/points/ledger", headers=auth_headers)).json()
        theirs = (await client.get("/api/v1/points/ledger", headers=second_user_headers)).json()

        assert len(mine) == 1
        assert theirs == []


class TestAwardingDoesNotDestroyItsCallersTransaction:
    """The bug this class exists for.

    `award` is called *mid-transaction* by `CommunityService.create_report`
    (after the report is flushed) and `::vote` (after a counter is
    incremented). It used to catch `IntegrityError` on the duplicate
    idempotency key and call `session.rollback()` -- which rolls back the
    **whole** transaction, silently discarding the caller's work while the
    handler still returned 201.

    Reaching that branch takes deliberate effort, which is exactly why the bug
    survived: `_already_awarded` short-circuits the ordinary duplicate, so the
    `IntegrityError` only fires when two transactions race -- one has inserted
    the key and not yet committed, so the other cannot see it and tries. The
    pre-check is disabled below to make that race deterministic rather than
    orchestrating two sessions for it.
    """

    @pytest.fixture
    def racing(self, monkeypatch):
        """Make `award` behave as it does when it loses a race.

        Patching the pre-check is not a shortcut around the test -- the branch
        under test is "the INSERT raised", and this is the only way to reach it
        without real concurrency.
        """

        async def blind(self, key: str) -> bool:
            return False

        monkeypatch.setattr(PointsService, "_already_awarded", blind)

    async def test_a_lost_race_leaves_earlier_work_intact(self, registered, db_session, racing):
        from app.modules.community.models import StoreReport
        from app.modules.pricegraph.models import Store, StoreSource

        user = uuid.UUID(registered["user"]["id"])
        service = PointsService(db_session)
        subject = uuid.uuid4()

        store = Store(
            name="Corner Store",
            normalized_name="corner store",
            source=StoreSource.USER_PIN.value,
        )
        db_session.add(store)
        await db_session.flush()

        # Something written before the award, exactly as the real callers do.
        report = StoreReport(
            user_id=user,
            store_id=store.id,
            kind="unique_item",
            item_text="Hand-ground filter coffee",
        )
        db_session.add(report)
        await db_session.flush()
        report_id = report.id

        assert await service.award(user, PointsReason.STORE_REPORT, subject_id=subject)
        # The lost race: the pre-check is blind, so this reaches the INSERT and
        # the unique constraint refuses it. It must return None and take
        # nothing else with it.
        assert await service.award(user, PointsReason.STORE_REPORT, subject_id=subject) is None

        await db_session.commit()

        survived = await db_session.get(StoreReport, report_id)
        assert survived is not None, (
            "losing the race rolled back the caller's transaction and discarded "
            "a report the handler had already returned as created"
        )
        assert await service.balance(user) == POINTS[PointsReason.STORE_REPORT]

    async def test_the_session_is_still_usable_afterwards(self, registered, db_session, racing):
        """A bare rollback leaves the transaction dead, so the next statement in
        the same request fails -- which is how this would have surfaced: as an
        unrelated error somewhere downstream."""
        user = uuid.UUID(registered["user"]["id"])
        service = PointsService(db_session)
        subject = uuid.uuid4()

        await service.award(user, PointsReason.STORE_REPORT, subject_id=subject)
        await service.award(user, PointsReason.STORE_REPORT, subject_id=subject)

        assert await service.balance(user) == POINTS[PointsReason.STORE_REPORT]

    async def test_an_ordinary_duplicate_still_short_circuits(self, registered, db_session):
        """Without the fixture, the pre-check does its job and the INSERT is
        never attempted. Both paths return None; only one of them used to be
        destructive."""
        user = uuid.UUID(registered["user"]["id"])
        service = PointsService(db_session)
        subject = uuid.uuid4()

        assert await service.award(user, PointsReason.STORE_REPORT, subject_id=subject)
        assert await service.award(user, PointsReason.STORE_REPORT, subject_id=subject) is None
        await db_session.commit()
        assert await service.balance(user) == POINTS[PointsReason.STORE_REPORT]
