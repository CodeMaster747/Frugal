"""Generated insights.

The failure mode this schema is shaped against is **noise**. An insight feed
that re-announces the same finding every time it runs teaches users to ignore
it, and a feature people ignore is worse than one that does not exist -- it
still costs to build and maintain.

Two mechanisms prevent that:

* `dedup_key` under a unique constraint with `period_start`. The engine can run
  hourly and the same finding lands once per period, by construction rather than
  by the detector remembering what it said.
* `dismissed_at` plus a cooling period in the service. A user who dismisses
  "you spent more on dining out" should not see it again next Tuesday.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import (
    CONFIDENCE,
    MONEY,
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class InsightType(StrEnum):
    CATEGORY_SPIKE = "category_spike"
    BUDGET_BREACH = "budget_breach"
    NEW_RECURRING = "new_recurring"
    SUBSCRIPTION_CREEP = "subscription_creep"
    SAVINGS_RATE_CHANGE = "savings_rate_change"
    ANOMALOUS_TRANSACTION = "anomalous_transaction"
    EMERGENCY_FUND_LOW = "emergency_fund_low"
    GOAL_AT_RISK = "goal_at_risk"
    CASHFLOW_SHORTFALL = "cashflow_shortfall"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class Insight(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "insights"

    insight_type: Mapped[str] = mapped_column(String(32), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)

    #: Rupees at stake. Null when a finding is real but not quantifiable, which
    #: is honest -- a fabricated impact figure would corrupt the ranking.
    impact_amount: Mapped[Decimal | None] = mapped_column(MONEY)

    #: The ranking key: ₹impact × confidence. Precomputed rather than sorted in
    #: Python so the partial index below returns the ranked set in one scan.
    materiality: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)

    #: e.g. `category_spike:food-delivery:2026-08`. Stable across runs for the
    #: same finding in the same period -- that stability is the whole mechanism.
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False)

    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)

    explanation: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: What the insight is about, when it points at one row. Lets the UI deep
    #: link to the transaction rather than describe it and leave the user to
    #: search.
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "transactions.id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "dedup_key", "period_start", name="uq_insights_dedup"),
        # The feed query: active insights, most material first, in one scan.
        Index(
            "ix_insights_user_active",
            "user_id",
            "materiality",
            postgresql_where=(
                # Rendered by Alembic as a partial index predicate.
                "dismissed_at IS NULL AND deleted_at IS NULL"
            ),
        ),
        Index("ix_insights_user_created", "user_id", "created_at"),
    )
