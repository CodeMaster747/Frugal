"""PointsService -- the only way to move the ledger.

Two rules the rest of the system relies on:

**Awards happen after verification, never on submission.** Every call site is a
worker, past the point where the contribution has been checked. Awarding on
submission would pay for anything a scripted client could POST.

**Awarding is idempotent.** Every award carries a key derived from what earned
it, under a unique constraint. A task retried after a broker timeout pays once.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.quota import PeriodKind, QuotaLedger, Window, period_keys
from app.modules.points.models import POINTS, PointsEntry, PointsReason, Reward

logger = get_logger(__name__)

#: The provider name under which daily earn caps are counted.
#:
#: Reusing `core/quota.py` rather than writing a second counter: the shape is
#: identical (one atomic increment against a cap, in Postgres because losing it
#: matters) and ADR-008 already argued that case. The honest caveat is that its
#: no-refund rule applies here too -- a rejected award still spends the cap.
#: Over-counting is the safe direction for an abuse control.
QUOTA_PROVIDER = "points"


class PointsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    async def award(
        self,
        user_id: uuid.UUID,
        reason: PointsReason,
        *,
        subject_type: str | None = None,
        subject_id: uuid.UUID | None = None,
        idempotency_key: str | None = None,
        note: str | None = None,
    ) -> PointsEntry | None:
        """Credit a verified contribution.

        Returns `None` when nothing was written -- already awarded, or the
        user's daily cap is spent. Neither is an error: both are the ordinary
        outcome of the controls working, and raising would make every caller
        handle a non-exception.
        """
        points = POINTS.get(reason)
        if points is None:
            raise ValueError(f"{reason} has no value in POINTS; awards must be predictable")

        key = idempotency_key or self._key(user_id, reason, subject_id)

        if await self._already_awarded(key):
            return None

        if not await self._within_daily_cap(user_id):
            logger.info(
                "points not awarded: daily cap reached",
                extra={"reason": reason.value},
            )
            return None

        entry = PointsEntry(
            user_id=user_id,
            reason=reason.value,
            points=points,
            subject_type=subject_type,
            subject_id=subject_id,
            idempotency_key=key,
            note=note,
        )

        # A SAVEPOINT, not a bare flush.
        #
        # This method is called *mid-transaction* by callers that have already
        # written something -- `CommunityService.create_report` has flushed a
        # report, `::vote` has incremented a counter. The first version caught
        # `IntegrityError` and called `session.rollback()`, which rolls back the
        # **whole** transaction: on the duplicate-key race it silently discarded
        # the report or the vote that had just been created, while the handler
        # went on to return 201 with a detached object.
        #
        # `begin_nested` scopes the rollback to this insert. The caller's work
        # survives, and losing the race still returns None -- which is what
        # idempotency promises.
        try:
            async with self.session.begin_nested():
                self.session.add(entry)
                await self.session.flush()
        except IntegrityError:
            return None
        return entry

    async def reverse(
        self, user_id: uuid.UUID, entry_id: uuid.UUID, *, note: str
    ) -> PointsEntry | None:
        """Undo an award with a compensating row.

        Never updates or deletes the original: the point of an append-only
        ledger is that a reversal is visible as a reversal, both to an operator
        investigating abuse and to the user who is about to ask why their
        balance dropped.
        """
        original = await self.session.get(PointsEntry, entry_id)
        if original is None or original.user_id != user_id:
            return None
        if original.reason == PointsReason.REVERSAL.value:
            return None

        key = f"reversal:{entry_id}"
        if await self._already_awarded(key):
            return None

        entry = PointsEntry(
            user_id=user_id,
            reason=PointsReason.REVERSAL.value,
            points=-original.points,
            subject_type=original.subject_type,
            subject_id=original.subject_id,
            reverses_id=original.id,
            idempotency_key=key,
            note=note,
        )
        self.session.add(entry)
        await self.session.flush()
        return entry

    async def balance(self, user_id: uuid.UUID) -> int:
        """A SUM over immutable rows, computed rather than stored.

        A cached balance is a second source of truth, and the two drift the
        first time a write fails between them.
        """
        total = await self.session.scalar(
            select(func.coalesce(func.sum(PointsEntry.points), 0)).where(
                PointsEntry.user_id == user_id
            )
        )
        return int(total or 0)

    async def history(
        self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0
    ) -> Sequence[PointsEntry]:
        stmt = (
            select(PointsEntry)
            .where(PointsEntry.user_id == user_id)
            .order_by(PointsEntry.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def rewards(self) -> Sequence[Reward]:
        return (
            (await self.session.execute(select(Reward).order_by(Reward.cost_points)))
            .scalars()
            .all()
        )

    async def sync_rewards(self) -> int:
        """Seed the catalogue from the code that defines it.

        Same shape as `MarketService.sync_catalogue`: the list lives where it
        can be read and reviewed, and the table exists so a redemption can carry
        a foreign key rather than a string.
        """
        from app.modules.points.catalogue import REWARDS

        existing = {row.slug for row in (await self.session.execute(select(Reward))).scalars()}
        added = 0
        for reward in REWARDS:
            if reward["slug"] in existing:
                continue
            self.session.add(Reward(**reward))
            added += 1
        await self.session.flush()
        return added

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _key(user_id: uuid.UUID, reason: PointsReason, subject_id: uuid.UUID | None) -> str:
        return f"{user_id}:{reason.value}:{subject_id or 'none'}"

    async def _already_awarded(self, key: str) -> bool:
        found = await self.session.scalar(
            select(PointsEntry.id).where(PointsEntry.idempotency_key == key)
        )
        return found is not None

    async def _within_daily_cap(self, user_id: uuid.UUID) -> bool:
        """One atomic reservation against the user's daily allowance.

        The cap is on *awards per day*, not points per day: a cap on points
        would make the cheapest contribution the most efficient way to farm,
        which is the opposite of what it is for.
        """
        day_key, _ = period_keys(datetime.now(UTC))
        return await QuotaLedger(self.session).reserve(
            QUOTA_PROVIDER,
            windows=[Window(PeriodKind.DAY, day_key, self.settings.points_awards_daily_cap_global)],
            user_id=user_id,
            per_user_cap=self.settings.points_awards_per_user_daily_cap,
            today=day_key,
        )
