"""Erasure that outlives the row it deletes (FR-1.8, ADR-011).

Right to erasure is carried by a cascading foreign key everywhere it can be:
migration 0016 gave nineteen user-owned tables ``ON DELETE CASCADE``, and
``TenantMixin`` now declares it so a new one gets it by construction.

**A foreign key cannot cross a database boundary, and there is no transaction
that spans two databases.** Neither is a limitation to work around; both are
facts about Postgres. So erasure over the boundary is an outbox: a row written
in the *same transaction* as the account deletion, drained by an idempotent
sweep. There is no window in which the user is gone and the debt is unrecorded.

The consequence must be said plainly rather than glossed, because it is the
kind of sentence a regulator may one day read: **erasure across the boundary is
eventually consistent.** It completes within the hour under normal operation,
`oldest_pending_erasure_seconds` is published so the delay is observable, and
the sweep is idempotent so a crash costs a retry and never a silent skip.

Placed in ``core`` beside ``audit.py``, and for the same reason: cross-cutting
infrastructure that several modules must reach without importing each other.
Like ``audit_log`` it carries no foreign key to ``users`` -- the row exists
precisely because the user does not.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 — SQLAlchemy's own name
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDMixin


class ErasureKind(StrEnum):
    """What kind of debt a request records.

    Two rights, deliberately distinguished, because they are not the same and
    the difference matters to the person exercising them:

    - ``SIGNALS`` **deletes**. Personalization signals are derived facts about
      one person and are worthless without them.
    - ``PRICE_CONTRIBUTIONS`` **anonymises**. A price at a shop on a day is a
      fact about the shop, and deleting it would silently degrade what every
      other user sees, making the shared graph a function of churn. Nulling the
      contributor hash makes the surviving row genuinely anonymous.

    A user who wants their contributions actually removed rather than
    anonymised has a separate, explicit action for it (retraction). Erasure
    anonymises; retraction removes.
    """

    SIGNALS = "signals"
    PRICE_CONTRIBUTIONS = "price_contributions"


class ErasureRequest(UUIDMixin, TimestampMixin, Base):
    """Work owed on behalf of a user who no longer exists.

    No foreign key to ``users``, for the same reason ``audit_log`` has none.
    """

    __tablename__ = "erasure_requests"

    subject_id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)

    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default=text("0"))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        # One outstanding debt per subject per kind. Deleting an account twice
        # is not a thing, but a retried request must not queue a second sweep.
        UniqueConstraint("subject_id", "kind", name="uq_erasure_requests_subject_id_kind"),
        Index(
            "ix_erasure_requests_pending",
            "requested_at",
            postgresql_where=text("completed_at IS NULL"),
        ),
    )


async def request(session: AsyncSession, subject_id: uuid.UUID, *, kind: ErasureKind) -> None:
    """Record a debt, in the caller's transaction.

    Deliberately does not commit. It must land or not land together with the
    account deletion that prompted it -- a committed request beside a
    rolled-back delete would erase a live user's data.
    """
    existing = (
        await session.execute(
            select(ErasureRequest).where(
                ErasureRequest.subject_id == subject_id,
                ErasureRequest.kind == kind.value,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.completed_at = None
        existing.last_error = None
        return
    session.add(ErasureRequest(subject_id=subject_id, kind=kind.value))


async def pending(
    session: AsyncSession, *, kind: ErasureKind | None = None, limit: int = 500
) -> Sequence[ErasureRequest]:
    stmt = (
        select(ErasureRequest)
        .where(ErasureRequest.completed_at.is_(None))
        .order_by(ErasureRequest.requested_at)
        .limit(limit)
    )
    if kind is not None:
        stmt = stmt.where(ErasureRequest.kind == kind.value)
    return (await session.execute(stmt)).scalars().all()


async def complete(session: AsyncSession, request_id: uuid.UUID) -> None:
    row = await session.get(ErasureRequest, request_id)
    if row is not None:
        row.completed_at = datetime.now(UTC)
        row.last_error = None


async def fail(session: AsyncSession, request_id: uuid.UUID, error: str) -> None:
    """Record an attempt that did not complete, leaving the debt outstanding."""
    row = await session.get(ErasureRequest, request_id)
    if row is not None:
        row.attempts += 1
        row.last_error = error[:2000]


async def oldest_pending_age_seconds(session: AsyncSession) -> int | None:
    """How long the oldest unpaid debt has been outstanding.

    Published on ``GET /system/providers``. "We cannot make this atomic"
    obliges us to measure it: an unbounded queue here is a compliance failure
    that is otherwise completely invisible.
    """
    oldest = (
        await session.execute(
            select(func.min(ErasureRequest.requested_at)).where(
                ErasureRequest.completed_at.is_(None)
            )
        )
    ).scalar_one_or_none()
    if oldest is None:
        return None
    return int((datetime.now(UTC) - oldest).total_seconds())
