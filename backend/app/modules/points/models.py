"""The points ledger, and the rewards it will one day buy.

**Append-only.** An award is reversed by a compensating row, never by updating
or deleting the original. Three reasons, and each independently decides it:

1. Points are earned for crowdsourced contributions, which is exactly the shape
   of thing people farm. A reversal has to be possible without destroying the
   evidence of what was reversed.
2. A balance that is a `SUM` over immutable rows cannot drift out of step with
   its history, because there is no second place to keep it.
3. `audit_log` already established the pattern here: a row that could be updated
   would not be evidence of anything.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TenantMixin, TimestampMixin, UUIDMixin


class PointsReason(StrEnum):
    """Why points were awarded, in descending order of how much work it is."""

    RECEIPT_VERIFIED = "receipt_verified"
    PRICE_PROMOTED = "price_promoted"
    STORE_REPORT = "store_report"
    REPORT_VERIFIED = "report_verified"
    REPORT_UPVOTED = "report_upvoted"
    ITEM_MERGE_CONFIRMED = "item_merge_confirmed"
    STORE_PIN_CONFIRMED = "store_pin_confirmed"
    #: The compensating side of any of the above.
    REVERSAL = "reversal"


#: What each contribution is worth.
#:
#: Flat integers rather than a formula, deliberately. A points economy that
#: nobody can predict is one nobody trusts, and these numbers are going to be
#: read by users long before they are spent on anything.
POINTS: dict[PointsReason, int] = {
    PointsReason.RECEIPT_VERIFIED: 10,
    PointsReason.PRICE_PROMOTED: 2,
    PointsReason.STORE_REPORT: 5,
    PointsReason.REPORT_VERIFIED: 3,
    PointsReason.REPORT_UPVOTED: 1,
    PointsReason.ITEM_MERGE_CONFIRMED: 2,
    PointsReason.STORE_PIN_CONFIRMED: 5,
}


class PointsEntry(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """One award or reversal. Never updated, never deleted."""

    __tablename__ = "points_ledger"

    reason: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Signed. A reversal is negative, which is what makes the balance a plain
    #: SUM with no special cases.
    points: Mapped[int] = mapped_column(Integer, nullable=False)

    #: What earned it, as a loose reference rather than a foreign key: the
    #: subject may be a receipt, a report, a comment or a merge, and a column
    #: per kind would grow with every new way to contribute.
    subject_type: Mapped[str | None] = mapped_column(String(40))
    subject_id: Mapped[uuid.UUID | None] = mapped_column(index=True)

    #: The row this one reverses, when it is a reversal.
    reverses_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("points_ledger.id", ondelete="SET NULL"), index=True
    )

    #: Makes awarding idempotent. A worker that retries after a broker timeout
    #: must not pay twice for one contribution, and this is enforced by the
    #: database rather than by the task remembering.
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False)

    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_points_ledger_idempotency_key"),
        Index("ix_points_ledger_user_id_created_at", "user_id", text("created_at DESC")),
        CheckConstraint("points <> 0", name="an_award_of_nothing_is_not_an_award"),
    )


class Reward(UUIDMixin, TimestampMixin, Base):
    """Something points will buy, once redemption exists.

    Shared reference data, so no `user_id`.

    Nothing is fulfilled yet and the API says so: `POST /rewards/{id}/redeem`
    returns 501. The full shape exists now so that adding fulfilment later is a
    change to one handler rather than a redesign -- and so a user earning points
    can see what they are for, which is the difference between a reward and a
    number that goes up.
    """

    __tablename__ = "rewards"

    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(400), nullable=False)
    cost_points: Mapped[int] = mapped_column(Integer, nullable=False)
    #: False for everything today. When one becomes real, this is the flag that
    #: says so, and the handler stops returning 501 for it.
    is_available: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    available_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (CheckConstraint("cost_points > 0", name="a_reward_costs_something"),)
