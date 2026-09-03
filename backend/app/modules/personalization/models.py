"""Tables in the personalization database.

Every model here inherits :class:`SignalsBase`, not ``Base``. Getting that
wrong is silent in the worst way -- the table would be created in the *primary*
database by ``alembic upgrade head`` and the signals tree would never see it --
so ``test_every_personalization_model_uses_the_signals_base`` asserts it.

Two structural rules hold across this file, and both are tested rather than
trusted:

1. **No column is named ``user_id``.** There is no ``users`` table here and no
   cascade; borrowing the name would make the invariants that guard the ledger
   describe a database they cannot see. The owner is ``subject_id`` -- an
   opaque value, not a reference.
2. **Every table is reachable by the erasure sweep**: it either carries
   ``subject_id`` itself or cascades from a table that does.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import CONFIDENCE, CURRENCY, MONEY, TimestampMixin, UUIDMixin
from app.core.signals_base import SignalsBase, SubjectMixin

# There is deliberately no `rail` column.
#
# The obvious design carries how the money moved -- UPI, card, netbanking --
# because the SMS parser knows it. But signals are derived through
# `FinanceService`, which does not expose it (nor should it: `sms.models` is
# private behind `SmsService`, and reaching for it would break
# `sms-internals-are-private` for a field no recommendation currently reads).
#
# So the column could only ever hold "unknown". A column that is structurally
# constant is worse than an absent one: it reads as data, it survives into
# JSONB payloads and API responses, and the next person to look assumes it
# means something. If a recommendation ever needs the rail, the honest change
# is to widen `SmsService`, not to add a field here and hope.


class PurchaseSignal(UUIDMixin, SubjectMixin, TimestampMixin, SignalsBase):
    """One notable purchase, derived from a bank message or a ledger entry.

    Deliberately thin. This is not a second copy of the ledger and must never
    become one: it carries what a recommendation needs -- how much, roughly
    what, roughly where in the user's own distribution, and when -- and none of
    what an audit needs. There is no account, no reference number, no running
    balance, and no link back to the transaction it was derived from.

    ``merchant_normalized`` rather than the raw merchant string: the normaliser
    already collapses the payment-processor noise, and the raw string is the
    part most likely to carry something identifying.
    """

    __tablename__ = "purchase_signals"

    #: The originating row in the primary database, hashed rather than stored.
    #: Enough to make derivation idempotent -- re-running over the same
    #: transaction updates rather than duplicates -- and not enough to join.
    source_digest: Mapped[bytes] = mapped_column(LargeBinary(32), nullable=False)

    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False, server_default="INR")

    merchant_normalized: Mapped[str | None] = mapped_column(String(160))
    category_slug: Mapped[str | None] = mapped_column(String(80), index=True)

    #: Where this sits in *this user's own* spending distribution, 0..1. A
    #: ₹40,000 purchase is unremarkable for one person and the largest of the
    #: year for another; an absolute threshold would encode an assumption about
    #: income that this product exists to avoid making.
    amount_percentile: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)
    #: How sure the derivation is. Carried so a recommendation built on it can
    #: say so, per the global invariant in docs/01-srs.md section 7.
    confidence: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)

    __table_args__ = (
        # Idempotent derivation: the same source row yields the same signal.
        UniqueConstraint(
            "subject_id", "source_digest", name="uq_purchase_signals_subject_id_source_digest"
        ),
        Index("ix_purchase_signals_subject_id_occurred_on", "subject_id", text("occurred_on DESC")),
    )


class SignalProfile(UUIDMixin, SubjectMixin, TimestampMixin, SignalsBase):
    """The rolled-up view a recommendation actually reads.

    Recomputed from :class:`PurchaseSignal` rather than queried live, because
    the read path is a page render and the write path is a nightly sweep. One
    row per subject.

    ``factors`` is the ADR-002 envelope: a profile that shapes advice and cannot
    say why is exactly what that ADR exists to make unserialisable.
    """

    __tablename__ = "signal_profiles"

    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    observation_days: Mapped[int] = mapped_column(Integer, nullable=False)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    #: The amount above which a purchase is notable *for this person*.
    big_purchase_threshold: Mapped[Decimal | None] = mapped_column(MONEY)
    median_big_purchase: Mapped[Decimal | None] = mapped_column(MONEY)
    #: Mean days between notable purchases. NULL below two observations.
    cadence_days: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))

    #: category_slug -> share of notable spend, as strings (ADR-003: no floats
    #: in JSONB either, which `test_no_float_money` checks).
    category_affinities: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    factors: Mapped[list[dict[str, str]] | None] = mapped_column(JSONB)

    confidence: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)
    rubric_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="1")

    __table_args__ = (UniqueConstraint("subject_id", name="uq_signal_profiles_subject_id"),)


class ProfileArchetype(UUIDMixin, TimestampMixin, SignalsBase):
    """A named spending pattern a profile can match.

    Carries no ``subject_id``: it is shared reference data, the same for
    everyone, and it is reachable by the erasure sweep through the fact that it
    contains nothing about anybody. The sweep's structural test allows exactly
    this shape -- a table with no subject column and no row that belongs to
    one -- and the test names it explicitly rather than inferring it.
    """

    __tablename__ = "profile_archetypes"

    slug: Mapped[str] = mapped_column(String(60), nullable=False, unique=True)
    label: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str] = mapped_column(String(400), nullable=False)


class ProfileMatch(UUIDMixin, SubjectMixin, TimestampMixin, SignalsBase):
    """How strongly one subject matches one archetype."""

    __tablename__ = "profile_matches"

    archetype_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("profile_archetypes.id", ondelete="CASCADE"), nullable=False, index=True
    )
    score: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "subject_id", "archetype_id", name="uq_profile_matches_subject_id_archetype_id"
        ),
    )
