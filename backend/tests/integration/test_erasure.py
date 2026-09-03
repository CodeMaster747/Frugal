"""Right to erasure across a boundary with no foreign key (FR-1.8, ADR-011).

Inside the primary database, deletion is a cascade and cascades are exact --
`test_every_user_owned_table_cascades` guards that. Across the boundary it
cannot be: Postgres expresses no foreign key between databases and no
transaction spans them.

So the guarantee is an outbox plus an idempotent sweep, and the properties that
make it a guarantee rather than a hope are each asserted here:

- the debt is written in the *same transaction* as the deletion,
- the sweep deletes the data before it marks the debt paid,
- a personalization outage does not block a user from deleting their account,
- running the sweep twice is free.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _signal_count(session, subject: uuid.UUID) -> int:
    return (
        await session.execute(
            text("SELECT count(*) FROM purchase_signals WHERE subject_id = :s"),
            {"s": subject},
        )
    ).scalar_one()


async def _seed_signal(session, subject: uuid.UUID) -> None:
    from datetime import date

    from app.modules.personalization.models import PurchaseSignal

    session.add(
        PurchaseSignal(
            subject_id=subject,
            source_digest=uuid.uuid4().bytes + uuid.uuid4().bytes,
            occurred_on=date(2026, 3, 1),
            amount=Decimal("25000.00"),
            currency="INR",
            amount_percentile=Decimal("0.980"),
            confidence=Decimal("0.900"),
        )
    )
    await session.commit()


class TestDeletingAnAccount:
    async def test_it_records_the_debt_and_the_sweep_pays_it(
        self, client, registered, auth_headers, db_session, signals_session, settings
    ):
        if not settings.personalization_enabled:
            pytest.skip("personalization is not configured")

        me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
        subject = uuid.UUID(me["id"])
        await _seed_signal(signals_session, subject)
        assert await _signal_count(signals_session, subject) == 1

        response = await client.delete("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 202, response.text

        # Two obligations, one per kind, and they are genuinely different
        # rights: SIGNALS deletes, PRICE_CONTRIBUTIONS anonymises.
        kinds = {
            row[0]
            for row in (
                await db_session.execute(
                    text(
                        "SELECT kind FROM erasure_requests "
                        "WHERE subject_id = :s AND completed_at IS NULL"
                    ),
                    {"s": subject},
                )
            ).all()
        }
        assert kinds == {"signals", "price_contributions"}, (
            f"the deletion did not record both obligations; got {kinds}"
        )

        from app.workers.tasks.personalization import _run_erasure

        result = await _run_erasure()
        assert result["status"] == "ok"

        assert await _signal_count(signals_session, subject) == 0
        outstanding = (
            await db_session.execute(
                text(
                    "SELECT count(*) FROM erasure_requests "
                    "WHERE subject_id = :s AND completed_at IS NULL"
                ),
                {"s": subject},
            )
        ).scalar_one()
        assert outstanding == 0, "the sweep left an obligation unpaid"

    async def test_the_debt_lands_or_the_deletion_does_not(self, db_session):
        """Written in the caller's transaction, never committed on its own.

        A committed request beside a rolled-back delete would erase a live
        user's data, which is worse than the bug it protects against.
        """
        from app.core import erasure
        from app.core.erasure import ErasureKind

        subject = uuid.uuid4()
        await erasure.request(db_session, subject, kind=ErasureKind.SIGNALS)
        await db_session.rollback()

        found = (
            await db_session.execute(
                text("SELECT count(*) FROM erasure_requests WHERE subject_id = :s"),
                {"s": subject},
            )
        ).scalar_one()
        assert found == 0

    async def test_a_signals_outage_does_not_block_deletion(
        self, client, auth_headers, db_session, monkeypatch, settings
    ):
        """The whole reason the debt is an outbox rather than a second write.

        If deleting an account required the personalization database to be
        reachable, an outage over there would block a user from exercising a
        legal right -- and the failure would be a 500 with no way for them to
        act on it. The obligation is recorded and the sweep pays it later.
        """
        if not settings.personalization_enabled:
            pytest.skip("personalization is not configured")

        me = (await client.get("/api/v1/auth/me", headers=auth_headers)).json()
        subject = uuid.UUID(me["id"])

        # There is no signals write on the deletion path at all -- which is the
        # property under test. Break the engine and the deletion must not care.
        from app.core import signals_database

        def _dead_engine() -> object:
            raise ConnectionError("personalization database is unreachable")

        monkeypatch.setattr(signals_database, "get_signals_engine", _dead_engine)

        response = await client.delete("/api/v1/auth/me", headers=auth_headers)
        assert response.status_code == 202, response.text

        pending = (
            await db_session.execute(
                text(
                    "SELECT count(*) FROM erasure_requests "
                    "WHERE subject_id = :s AND completed_at IS NULL"
                ),
                {"s": subject},
            )
        ).scalar_one()
        assert pending == 2, "both debts must outlive the outage"

    async def test_requesting_twice_records_one_debt(self, db_session):
        from app.core import erasure
        from app.core.erasure import ErasureKind

        subject = uuid.uuid4()
        await erasure.request(db_session, subject, kind=ErasureKind.SIGNALS)
        await db_session.flush()
        await erasure.request(db_session, subject, kind=ErasureKind.SIGNALS)
        await db_session.commit()

        found = (
            await db_session.execute(
                text("SELECT count(*) FROM erasure_requests WHERE subject_id = :s"),
                {"s": subject},
            )
        ).scalar_one()
        assert found == 1


class TestTheSweep:
    async def test_it_is_idempotent(self, settings):
        if not settings.personalization_enabled:
            pytest.skip("personalization is not configured")

        from app.workers.tasks.personalization import _run_erasure

        first = await _run_erasure()
        second = await _run_erasure()
        assert first["status"] == second["status"] == "ok"

    async def test_a_pending_debt_is_visible_to_operators(self, db_session, settings):
        """`oldest_pending_erasure_seconds` is the only way an unbounded queue
        here is visible at all. Erasure is eventually consistent, and that
        obliges us to measure the eventually."""
        from app.core import erasure
        from app.core.erasure import ErasureKind

        assert await erasure.oldest_pending_age_seconds(db_session) is None

        await erasure.request(db_session, uuid.uuid4(), kind=ErasureKind.SIGNALS)
        await db_session.commit()

        age = await erasure.oldest_pending_age_seconds(db_session)
        assert age is not None and age >= 0

    async def test_it_reports_disabled_rather_than_failing(self, monkeypatch, settings):
        """A deployment with no second database must not accumulate errors."""
        from app.core.config import get_settings
        from app.workers.tasks import personalization

        monkeypatch.setattr(
            personalization, "get_settings", lambda: _WithoutPersonalization(get_settings())
        )
        assert personalization.run_erasure() == {"status": "disabled"}


class _WithoutPersonalization:
    """A Settings stand-in with the feature off, for the disabled path."""

    def __init__(self, real: object) -> None:
        self._real = real

    @property
    def personalization_enabled(self) -> bool:
        return False

    def __getattr__(self, name: str) -> object:
        return getattr(self._real, name)
