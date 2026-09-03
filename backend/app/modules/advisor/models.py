"""Purchase evaluations.

Intensely personal, and persisted rather than computed-and-discarded so a user
can revisit a decision, and so the rubric can eventually be checked against what
people actually did.

The catalogue tables (`products`, `price_points`) started here in M8 and moved
to `market` in M9, which is where they are actually queried — M8 only ever read
the in-memory catalogue. `product_id` below still references `products.id`; a
foreign key by table name needs no import, which is what keeps this module free
of a dependency on the other's internals (ADR-001).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
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


class PurchaseEvaluation(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "purchase_evaluations"

    product_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "products.id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    #: What the user actually asked about. Kept even when a product matched, so
    #: the history reads back in their words rather than the catalogue's.
    product_query: Mapped[str] = mapped_column(String(255), nullable=False)

    price: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False)

    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    affordability_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)
    rubric_version: Mapped[str] = mapped_column(String(16), nullable=False)

    affordable_from: Mapped[date | None] = mapped_column(Date)
    goal_delay_days: Mapped[int | None] = mapped_column(Integer)
    emergency_fund_delta_months: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    health_score_delta: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))

    simulation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    emi_options: Mapped[list[Any] | None] = mapped_column(JSONB)
    alternatives: Mapped[list[Any] | None] = mapped_column(JSONB)
    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        # FR-8.6 at the schema level. A `wait` with no date is not advice, it is
        # a refusal wearing advice's clothes — and the database will not store
        # one, so no code path can produce one by accident.
        CheckConstraint(
            "verdict <> 'wait' OR affordable_from IS NOT NULL",
            name="ck_purchase_eval_wait_date",
        ),
        CheckConstraint(
            "affordability_score >= 0 AND affordability_score <= 100",
            name="ck_purchase_eval_score_range",
        ),
        Index("ix_purchase_eval_user_created", "user_id", text("created_at DESC")),
    )
