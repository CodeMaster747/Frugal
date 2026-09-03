"""Data access for the personalization database.

Private to this module, exactly as `finance/repository.py` and
`sms/repository.py` are, and held there by the
`personalization-internals-are-private` contract. Everything crosses at
`service.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.signals_repository import SignalsRepository
from app.modules.personalization.models import (
    ProfileArchetype,
    ProfileMatch,
    PurchaseSignal,
    SignalProfile,
)


class PurchaseSignalRepository(SignalsRepository[PurchaseSignal]):
    model = PurchaseSignal

    async def since(self, subject_id: uuid.UUID, on_or_after: date) -> Sequence[PurchaseSignal]:
        stmt = (
            self.scoped_select(subject_id)
            .where(PurchaseSignal.occurred_on >= on_or_after)
            .order_by(PurchaseSignal.occurred_on)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def by_digest(self, subject_id: uuid.UUID, digest: bytes) -> PurchaseSignal | None:
        stmt = self.scoped_select(subject_id).where(PurchaseSignal.source_digest == digest)
        return (await self.session.execute(stmt)).scalar_one_or_none()


class SignalProfileRepository(SignalsRepository[SignalProfile]):
    model = SignalProfile

    async def for_subject(self, subject_id: uuid.UUID) -> SignalProfile | None:
        return (await self.session.execute(self.scoped_select(subject_id))).scalar_one_or_none()


class ProfileMatchRepository(SignalsRepository[ProfileMatch]):
    model = ProfileMatch

    async def for_subject(self, subject_id: uuid.UUID) -> Sequence[ProfileMatch]:
        stmt = self.scoped_select(subject_id).order_by(ProfileMatch.score.desc())
        return (await self.session.execute(stmt)).scalars().all()


class ArchetypeRepository:
    """Shared reference data, so not a `SignalsRepository`.

    `profile_archetypes` has no `subject_id` by design, and
    `SignalsRepository.scoped_select` raises on a model without one -- which is
    the mechanism working, not an obstacle to route around. Reference data gets
    a plain class, the same way `categories` does on the other side.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def all(self) -> Sequence[ProfileArchetype]:
        result = await self.session.execute(
            select(ProfileArchetype).order_by(ProfileArchetype.slug)
        )
        return result.scalars().all()
