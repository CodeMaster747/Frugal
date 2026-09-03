"""Subject-scoped data access for the personalization database (ADR-011).

A deliberate sibling of ``BaseRepository`` rather than a subclass of it. The
mechanism is the same -- one statement builder that injects the owner predicate,
so "forgot to scope this query" is not something a caller can express -- but the
guarantee is different, and conflating the two would weaken both:

- ``BaseRepository.scoped_select(user_id)`` scopes to a row in ``users`` that a
  cascading foreign key will delete.
- ``SignalsRepository.scoped_select(subject_id)`` scopes to an opaque value in a
  database with no ``users`` table, whose deletion is carried by the sweep in
  ``app/core/erasure.py``.

Sharing a base class would let ``test_all_repositories_are_tenant_scoped``
enumerate a signals repository and check it for a mechanism that does not exist
over here -- and its ``if not hasattr(model, "user_id"): continue`` would skip
it silently. Two classes, two sweeps, neither able to pass for the other.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Generic, TypeVar

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.signals_base import SignalsBase

SignalModelT = TypeVar("SignalModelT", bound=SignalsBase)


class SignalsRepository(Generic[SignalModelT]):
    """Subject-scoped data access for a single personalization model."""

    model: type[SignalModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not hasattr(cls, "model"):
            raise TypeError(f"{cls.__name__} must declare a `model` attribute")

    def scoped_select(self, subject_id: uuid.UUID) -> Select[tuple[SignalModelT]]:
        """The only entry point for building a query on a signals model."""
        if not hasattr(self.model, "subject_id"):
            raise TypeError(
                f"{self.model.__name__} has no subject_id. Every table in the "
                "personalization database must carry one or cascade to one, or the "
                "erasure sweep cannot reach it -- see test_every_signals_table_is_"
                "reachable_by_the_erasure_sweep."
            )
        return select(self.model).where(self.model.subject_id == subject_id)  # type: ignore[attr-defined]

    async def list(
        self, subject_id: uuid.UUID, *, limit: int = 200, offset: int = 0
    ) -> Sequence[SignalModelT]:
        stmt = self.scoped_select(subject_id).limit(limit).offset(offset)
        return (await self.session.execute(stmt)).scalars().all()

    async def count(self, subject_id: uuid.UUID) -> int:
        stmt = select(func.count()).select_from(self.scoped_select(subject_id).subquery())
        return (await self.session.execute(stmt)).scalar_one()

    async def add(self, entity: SignalModelT) -> SignalModelT:
        self.session.add(entity)
        await self.session.flush()
        return entity

    async def add_all(self, entities: Sequence[SignalModelT]) -> Sequence[SignalModelT]:
        self.session.add_all(list(entities))
        await self.session.flush()
        return entities
