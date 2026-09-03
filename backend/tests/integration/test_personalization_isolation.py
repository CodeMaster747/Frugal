"""The guarantees the second database exists for (ADR-011).

These are structural tests. Not "does erasure work for the tables that exist
today", but "is every table that will ever exist reachable by the sweep" -- the
same shape as `test_every_user_owned_table_cascades`, and for the same reason:
the M10 finding was not that one table failed to cascade, it was that nineteen
did and nothing could see it.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import text

from app.core.models import Base
from app.core.signals_base import SignalsBase

pytestmark = pytest.mark.integration

#: Tables in the personalization database that hold nothing about anybody, and
#: are therefore allowed to carry no `subject_id`.
#:
#: A list somebody has to edit, deliberately, rather than a predicate that
#: infers it. "This is shared reference data" is a judgement, and a judgement
#: that a test makes on your behalf is one nobody ever revisits.
SHARED_REFERENCE_DATA = {"profile_archetypes"}


def _cascades_to_a_subject_table(table: object) -> bool:
    """Whether deleting a subject's rows reaches this table by cascade."""
    for fk in table.foreign_keys:  # type: ignore[attr-defined]
        if fk.ondelete != "CASCADE":
            continue
        parent = fk.column.table
        if "subject_id" in parent.columns or _cascades_to_a_subject_table(parent):
            return True
    return False


class TestTheTwoDatabasesAreSeparate:
    def test_the_two_metadatas_are_disjoint(self):
        """A table in both would be created twice and erased once."""
        overlap = set(Base.metadata.tables) & set(SignalsBase.metadata.tables)
        assert not overlap, f"tables declared on both bases: {sorted(overlap)}"

    def test_no_signals_table_carries_a_user_id(self):
        """`user_id` means three things here, none of which is true over there.

        In this codebase the name implies a cascading foreign key to `users`, a
        place in the account-deletion cascade, and a `BaseRepository` predicate.
        Borrowing it would make `test_every_user_owned_table_cascades` describe
        a database it cannot see, and would let a signals model slip into the
        tenant-repository sweep and be silently skipped.
        """
        offenders = [t.name for t in SignalsBase.metadata.tables.values() if "user_id" in t.columns]
        assert offenders == [], (
            f"signals tables carrying `user_id`: {offenders}. The owner column here "
            "is `subject_id` -- an opaque value, not a foreign key."
        )

    def test_every_personalization_model_uses_the_signals_base(self):
        """Inheriting `Base` by mistake is silent in the worst way.

        The table would be created in the *primary* database by `alembic
        upgrade head`, the signals tree would never see it, and every query
        against it would work in development where both databases are on one
        instance.
        """
        import app.modules.personalization.models as models

        for name, obj in vars(models).items():
            if isinstance(obj, type) and hasattr(obj, "__tablename__"):
                assert issubclass(obj, SignalsBase), (
                    f"{name} inherits Base, so `alembic upgrade head` will create it in "
                    "the primary database and the signals tree will never see it"
                )

    async def test_the_signals_database_references_nothing_outside_itself(self, signals_session):
        """Postgres cannot express a cross-database foreign key.

        This asserts we did not simulate one -- every foreign key over there
        points at a table over there.
        """
        rows = (
            await signals_session.execute(
                text("""
                SELECT c.relname AS child, f.relname AS parent
                  FROM pg_constraint con
                  JOIN pg_class c ON c.oid = con.conrelid
                  JOIN pg_class f ON f.oid = con.confrelid
                 WHERE con.contype = 'f'
                """)
            )
        ).all()

        signals_tables = set(SignalsBase.metadata.tables)
        for row in rows:
            assert row.parent in signals_tables, (
                f"{row.child} references {row.parent}, which is not a signals table"
            )

    async def test_the_signals_database_has_no_users_table(self, signals_session):
        """The absence is the feature, so assert it rather than assume it."""
        found = (
            await signals_session.execute(text("SELECT to_regclass('public.users') IS NOT NULL"))
        ).scalar_one()
        assert found is False


class TestErasureCanReachEverything:
    def test_every_signals_table_is_reachable_by_the_erasure_sweep(self):
        """The most important test in this milestone.

        Right to erasure over the boundary is carried by a sweep, not a
        cascade, and a sweep only reaches what it knows about. Every table must
        either carry `subject_id` itself or hang off one by ON DELETE CASCADE --
        so a table added in six months is still legally deletable, or the build
        fails.

        A table with neither is allowed only if it holds nothing about anybody;
        `profile_archetypes` is the one such table and it is named explicitly,
        because "shared reference data" is a judgement and judgements belong in
        a list somebody has to edit, not in a predicate.
        """
        for table in SignalsBase.metadata.sorted_tables:
            if table.name in SHARED_REFERENCE_DATA:
                continue
            reachable = "subject_id" in table.columns or _cascades_to_a_subject_table(table)
            assert reachable, (
                f"{table.name} carries no `subject_id` and does not cascade from a table "
                "that does, so the erasure sweep cannot reach it. Either add the column, "
                "add a cascading foreign key, or add it to SHARED_REFERENCE_DATA above "
                "and be able to defend that."
            )

    async def test_the_sweep_deletes_every_kind_of_row(self, signals_session):
        """`erase()` discovers tables from the metadata, so this covers ones
        added after it was written."""
        from app.modules.personalization.models import PurchaseSignal, SignalProfile
        from app.modules.personalization.service import PersonalizationService

        subject = uuid.uuid4()
        signals_session.add(
            PurchaseSignal(
                subject_id=subject,
                source_digest=b"0" * 32,
                occurred_on=__import__("datetime").date(2026, 1, 1),
                amount=Decimal("1000.00"),
                currency="INR",
                amount_percentile=Decimal("0.950"),
                confidence=Decimal("0.900"),
            )
        )
        signals_session.add(
            SignalProfile(
                subject_id=subject,
                observation_days=90,
                signal_count=1,
                confidence=Decimal("0.500"),
            )
        )
        await signals_session.commit()

        deleted = await PersonalizationService(signals_session).erase(subject)
        await signals_session.commit()

        assert deleted >= 2
        assert await PersonalizationService(signals_session).profile(subject) is not None
        remaining = (
            await signals_session.execute(
                text("SELECT count(*) FROM purchase_signals WHERE subject_id = :s"),
                {"s": subject},
            )
        ).scalar_one()
        assert remaining == 0

    async def test_the_sweep_is_idempotent(self, signals_session):
        """A crash between the two commits re-runs it, and deleting zero rows
        twice must be free."""
        from app.modules.personalization.service import PersonalizationService

        service = PersonalizationService(signals_session)
        subject = uuid.uuid4()

        assert await service.erase(subject) == 0
        assert await service.erase(subject) == 0

    async def test_erase_touches_only_the_named_subject(self, signals_session):
        from app.modules.personalization.models import SignalProfile
        from app.modules.personalization.service import PersonalizationService

        mine, theirs = uuid.uuid4(), uuid.uuid4()
        for subject in (mine, theirs):
            signals_session.add(
                SignalProfile(
                    subject_id=subject,
                    observation_days=30,
                    signal_count=0,
                    confidence=Decimal("0.100"),
                )
            )
        await signals_session.commit()

        await PersonalizationService(signals_session).erase(mine)
        await signals_session.commit()

        survivors = (
            await signals_session.execute(
                text("SELECT count(*) FROM signal_profiles WHERE subject_id = :s"),
                {"s": theirs},
            )
        ).scalar_one()
        assert survivors == 1
