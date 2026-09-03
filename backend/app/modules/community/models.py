"""Reports, verifications, comments, votes, and the trust they add up to.

For local stores there is no bill to scan -- the shop prints nothing, or prints
something OCR cannot read. So a user says what they found, other users confirm
or dispute it, and a report that enough trusted people agree with is promoted
into the same `price_observations` table a receipt feeds.

**Every vote is one row per user, enforced by a unique constraint.** Not by a
check in a service, which a new call site can bypass, and not by a counter,
which double-counts the moment two requests race.

Reports are authored by a user and read by everyone, which makes them the one
place in this system where a tenant-scoped row is deliberately world-readable.
The author is shown; nothing else about them is.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import (
    CONFIDENCE,
    CURRENCY,
    MONEY,
    Base,
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class ReportKind(StrEnum):
    #: Something this shop stocks that is hard to find elsewhere.
    UNIQUE_ITEM = "unique_item"
    #: A price notably better than usual.
    GOOD_PRICE = "good_price"


class ReportStatus(StrEnum):
    ACTIVE = "active"
    #: Enough disagreement that it is no longer shown. Not deleted: the author
    #: can see what happened, and a hidden report that is later confirmed can
    #: come back.
    HIDDEN = "hidden"
    #: The author withdrew it.
    RETRACTED = "retracted"


class StoreReport(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """One person's claim about one shop."""

    __tablename__ = "store_reports"

    store_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("stores.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)

    #: Resolved through `pricegraph.normalize` when the free text matches
    #: something known. NULL is fine and common: a shop's specialty often has
    #: no canonical entry until somebody buys it and uploads the receipt.
    canonical_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("canonical_items.id", ondelete="SET NULL"), index=True
    )
    item_text: Mapped[str] = mapped_column(String(200), nullable=False)

    price: Mapped[Decimal | None] = mapped_column(MONEY)
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False, server_default="INR")
    note: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default=ReportStatus.ACTIVE.value
    )

    #: Denormalised counters, kept in step by the service. They exist because
    #: the map renders hundreds of pins and counting votes per pin would be the
    #: slowest thing on the screen; the vote tables remain the source of truth.
    agree_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    dispute_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    upvote_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    promoted_observation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("price_observations.id", ondelete="SET NULL"), index=True
    )

    __table_args__ = (
        Index("ix_store_reports_store_id_status", "store_id", "status"),
        Index("ix_store_reports_user_id_created_at", "user_id", text("created_at DESC")),
        CheckConstraint(
            "kind <> 'good_price' OR price IS NOT NULL",
            name="a_good_price_report_needs_a_price",
        ),
        CheckConstraint("price IS NULL OR price > 0", name="price_positive"),
    )


class ReportVerification(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """One user's confirmation or dispute of one report.

    `UNIQUE (user_id, report_id)` is the whole anti-abuse mechanism here. A
    service-level check would be bypassed by the next call site somebody adds;
    a constraint is not.
    """

    __tablename__ = "report_verifications"

    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("store_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    agrees: Mapped[bool] = mapped_column(Boolean, nullable=False)
    #: The verifier's trust at the moment they voted, snapshotted.
    #:
    #: Trust changes as people contribute, and a promotion decision has to be
    #: reconstructible from what was known when it was made -- not recomputed
    #: later against numbers that have since moved.
    weight: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "report_id", name="uq_report_verifications_user_id_report_id"),
    )


class ReportComment(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "report_comments"

    report_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("store_reports.id", ondelete="CASCADE"), nullable=False, index=True
    )
    body: Mapped[str] = mapped_column(String(1000), nullable=False)
    upvote_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    #: The author withdrew it. Soft, so replies keep their context.
    retracted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_report_comments_user_id_created_at", "user_id", text("created_at DESC")),
    )


class Vote(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """An upvote on a report or a comment.

    One table for both, keyed by `(user_id, target_type, target_id)`. A table
    per target would duplicate the constraint that is the entire point, and the
    two would drift.
    """

    __tablename__ = "community_votes"

    target_type: Mapped[str] = mapped_column(String(16), nullable=False)
    target_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    __table_args__ = (
        UniqueConstraint(
            "user_id", "target_type", "target_id", name="uq_community_votes_one_per_user"
        ),
        CheckConstraint(
            "target_type IN ('report', 'comment')", name="target_is_a_report_or_a_comment"
        ),
    )


class UserTrust(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """How much weight this user's contributions carry.

    **Derived, never typed in.** It is a function of what a user has
    contributed and how it held up -- accepted promotions and agreed
    verifications raise it, retractions and disputes lower it. A settable trust
    score would be a permission system with a misleading name.
    """

    __tablename__ = "user_trust"

    score: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False, server_default="0.200")

    accepted_promotions: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    agreed_verifications: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    disputed_reports: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    retracted_contributions: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )

    rubric_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")

    __table_args__ = (UniqueConstraint("user_id", name="uq_user_trust_user_id"),)
