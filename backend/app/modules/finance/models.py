"""Financial core tables.

The data foundation every engine reads. Conventions and index rationale are in
docs/03-data-model.md; the decisions worth restating at the point of use are
commented inline.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import (
    CONFIDENCE,
    CURRENCY,
    MONEY,
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)

# Sentinel used inside partial unique indexes so a NULL column still
# participates in uniqueness -- NULL never equals NULL in SQL, so without this
# a user could create two "overall" budgets for the same month.
NIL_UUID = "00000000-0000-0000-0000-000000000000"


class AccountType(StrEnum):
    BANK = "bank"
    CASH = "cash"
    CREDIT_CARD = "credit_card"
    WALLET = "wallet"
    LOAN = "loan"
    INVESTMENT = "investment"


class TransactionKind(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"
    TRANSFER = "transfer"


class TransactionSource(StrEnum):
    MANUAL = "manual"
    CSV_IMPORT = "csv_import"
    RECEIPT = "receipt"
    RECURRING = "recurring"
    DEMO_SEED = "demo_seed"
    # A bank alert, parsed from the user's SMS. Stored in the same `String(20)`
    # column as every other source -- docs/03-data-model.md calls this an ENUM,
    # but no migration ever created one, so adding a source is a code change
    # rather than a schema change.
    SMS = "sms"


class GoalStatus(StrEnum):
    ACTIVE = "active"
    ACHIEVED = "achieved"
    PAUSED = "paused"
    ABANDONED = "abandoned"


class Cadence(StrEnum):
    WEEKLY = "weekly"
    FORTNIGHTLY = "fortnightly"
    MONTHLY = "monthly"
    QUARTERLY = "quarterly"
    YEARLY = "yearly"


class RecurringType(StrEnum):
    SALARY = "salary"
    RENT = "rent"
    EMI = "emi"
    SUBSCRIPTION = "subscription"
    UTILITY = "utility"
    OTHER = "other"


class Account(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "accounts"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False)

    opening_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False, server_default="0")

    # Materialised, updated transactionally on every write. Recomputing from
    # the full ledger on each dashboard load is O(n) per request; a nightly
    # reconciliation job verifies this against the ledger sum and logs drift.
    current_balance: Mapped[Decimal] = mapped_column(MONEY, nullable=False, server_default="0")

    credit_limit: Mapped[Decimal | None] = mapped_column(MONEY)

    # Distinguishes spendable savings from a locked deposit or an investment.
    # The advisor and the emergency-fund metric depend on this; getting it
    # wrong inflates affordability.
    is_liquid: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    institution: Mapped[str | None] = mapped_column(String(120))
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="account", passive_deletes=True
    )

    __table_args__ = (
        Index("ix_accounts_user_id_active", "user_id", postgresql_where=text("deleted_at IS NULL")),
        Index(
            "uq_accounts_user_id_name",
            "user_id",
            "name",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint(
            "type <> 'credit_card' OR credit_limit IS NOT NULL", name="credit_limit_required"
        ),
    )


class Category(UUIDMixin, TimestampMixin, Base):
    """Two-level taxonomy.

    ``user_id`` is NULL for the shared system taxonomy, which is also the label
    space the categoriser is trained against (M5). Because of that NULL this is
    not a TenantMixin table, and repositories reach it through the explicit
    shared-data path rather than the tenant-scoped one.

    It still cascades: a user's own categories go when the user does, and the
    NULL-owned system rows are untouched because NULL matches no user. Declared
    here rather than only in migration 0016 -- the mixin cannot carry it for a
    nullable column, and without this line `alembic check` would propose
    dropping the constraint.
    """

    __tablename__ = "categories"

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "categories.id",
            ondelete="CASCADE",
        ),
        index=True,
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    icon: Mapped[str | None] = mapped_column(String(40))
    color: Mapped[str | None] = mapped_column(String(7))
    is_system: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    sort_order: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="0")

    children: Mapped[list[Category]] = relationship(
        back_populates="parent", cascade="all, delete-orphan"
    )
    parent: Mapped[Category | None] = relationship(
        back_populates="children", remote_side="Category.id"
    )

    __table_args__ = (
        Index(
            "uq_categories_scope_slug",
            text(f"COALESCE(user_id, '{NIL_UUID}'::uuid)"),
            "slug",
            unique=True,
        ),
        CheckConstraint("parent_id IS NULL OR parent_id <> id", name="no_self_parent"),
    )


class Transaction(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """The busiest table in the system.

    Every index here answers a specific query; see docs/03-data-model.md 3.5.
    """

    __tablename__ = "transactions"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "categories.id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    # Always positive; direction is carried by `kind`. Signed amounts invite
    # double-negation bugs where a caller re-negates an already-negative
    # expense, and every aggregate then has to know the convention.
    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False)

    # A calendar date, not an instant: a transaction happens on a day in the
    # user's locale. Storing a timestamp creates off-by-one-day bugs at
    # midnight boundaries.
    occurred_on: Mapped[date] = mapped_column(Date, nullable=False)

    merchant_raw: Mapped[str | None] = mapped_column(String(255))
    merchant_normalized: Mapped[str | None] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text)

    transfer_pair_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "transactions.id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    recurring_item_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "recurring_items.id",
            ondelete="SET NULL",
        ),
        index=True,
    )
    # Set when a transaction came from a scanned receipt (M4). Deliberately not
    # a hard dependency: deleting the image must not delete the expense.
    receipt_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "receipts.id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    source: Mapped[str] = mapped_column(String(20), nullable=False)

    # The mechanism behind idempotent import (FR-2.6). Enforced by the database
    # so no ingestion path -- present or future -- can bypass it.
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    category_confidence: Mapped[Decimal | None] = mapped_column(CONFIDENCE)
    categorizer_version: Mapped[str | None] = mapped_column(String(32))
    is_reviewed: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    excluded_from_analytics: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )

    account: Mapped[Account] = relationship(back_populates="transactions")

    # lazy="selectin" rather than the default: an async session cannot lazy-load
    # on attribute access, so the response schema would silently serialise
    # `category: null` for every row. One extra query per page beats a join
    # that multiplies rows on the busiest table in the system.
    category: Mapped[Category | None] = relationship(lazy="selectin")

    __table_args__ = (
        Index(
            "uq_transactions_user_id_content_hash",
            "user_id",
            "content_hash",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_transactions_user_id_occurred_on",
            "user_id",
            text("occurred_on DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_transactions_user_id_category_id_occurred_on",
            "user_id",
            "category_id",
            text("occurred_on DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index(
            "ix_transactions_user_id_account_id_occurred_on",
            "user_id",
            "account_id",
            text("occurred_on DESC"),
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Fuzzy merchant search, and the similarity lookup the categoriser's
        # rules layer uses (M5).
        Index(
            "ix_transactions_merchant_trgm",
            "merchant_normalized",
            postgresql_using="gin",
            postgresql_ops={"merchant_normalized": "gin_trgm_ops"},
        ),
        # Small partial index serving the review queue directly. Without the
        # WHERE it would duplicate the whole table for a query that touches a
        # handful of rows.
        Index(
            "ix_transactions_uncategorized",
            "user_id",
            text("occurred_on DESC"),
            postgresql_where=text("category_id IS NULL AND deleted_at IS NULL"),
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    @staticmethod
    def compute_hash(
        user_id: uuid.UUID,
        occurred_on: date,
        amount: Decimal,
        merchant: str | None,
        account_id: uuid.UUID,
        discriminator: str = "",
    ) -> str:
        """Content hash used for deduplication.

        ``discriminator`` exists for the legitimate-duplicate case -- two ₹50
        coffees at the same shop on the same day from the same account. The
        user marks the second as distinct and it gets a discriminator, which is
        better than silently merging or silently duplicating.
        """
        parts = [
            str(user_id),
            occurred_on.isoformat(),
            f"{amount:.2f}",
            (merchant or "").strip().lower(),
            str(account_id),
            discriminator,
        ]
        return hashlib.sha256("|".join(parts).encode()).hexdigest()


class Budget(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """One row per category per period.

    Deliberately not a template: changing October's limit must not retroactively
    alter March's "over budget" insight, which a single mutable row would do.
    """

    __tablename__ = "budgets"

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "categories.id",
            ondelete="CASCADE",
        ),
        index=True,
    )
    period: Mapped[str] = mapped_column(String(20), nullable=False, server_default="monthly")
    period_start: Mapped[date] = mapped_column(Date, nullable=False)

    amount_limit: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False)
    rollover_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    rollover_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, server_default="0")

    __table_args__ = (
        Index(
            "uq_budgets_user_id_category_id_period_start",
            "user_id",
            text(f"COALESCE(category_id, '{NIL_UUID}'::uuid)"),
            "period_start",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_budgets_user_id_period_start", "user_id", text("period_start DESC")),
        CheckConstraint("amount_limit > 0", name="limit_positive"),
    )


class Goal(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "goals"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    target_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    current_amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False, server_default="0")
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False)
    target_date: Mapped[date | None] = mapped_column(Date)

    linked_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "accounts.id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    # Consumed by the advisor's opportunity-cost calculation (FR-8.10):
    # delaying a priority-1 goal weighs more than delaying a priority-3 one.
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="active")

    __table_args__ = (
        Index(
            "ix_goals_user_id_status",
            "user_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        CheckConstraint("target_amount > 0", name="target_positive"),
        CheckConstraint("priority BETWEEN 1 AND 5", name="priority_range"),
    )


class RecurringItem(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """Known future inflows and outflows.

    Feeds forecasting tier 1 (FR-7.4) and bill reminders (M10).
    """

    __tablename__ = "recurring_items"

    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "accounts.id",
            ondelete="CASCADE",
        ),
        index=True,
    )
    category_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "categories.id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    item_type: Mapped[str] = mapped_column(String(20), nullable=False)

    amount: Mapped[Decimal] = mapped_column(MONEY, nullable=False)
    currency: Mapped[str] = mapped_column(CURRENCY, nullable=False)
    cadence: Mapped[str] = mapped_column(String(20), nullable=False)
    next_due_on: Mapped[date] = mapped_column(Date, nullable=False)
    end_on: Mapped[date | None] = mapped_column(Date)

    # Distinguishes a fixed EMI (variance ~0, forecastable exactly) from a
    # utility bill (variance ~0.3, forecastable as a distribution). The
    # forecaster widens or tightens its interval per item instead of applying
    # one blanket assumption.
    amount_variance: Mapped[Decimal | None] = mapped_column(Numeric(5, 4))
    detection_confidence: Mapped[Decimal | None] = mapped_column(CONFIDENCE)

    #: The earliest observed occurrence of this pattern. Distinct from
    #: `created_at`, which is when detection first noticed it -- without the
    #: distinction, a commitment held for two years is reported as new on the
    #: day the feature ships.
    first_seen_on: Mapped[date | None] = mapped_column(Date)
    is_auto_detected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))

    __table_args__ = (
        Index(
            "ix_recurring_items_user_id_next_due_on",
            "user_id",
            "next_due_on",
            postgresql_where=text("is_active AND deleted_at IS NULL"),
        ),
        Index("ix_recurring_items_user_id_item_type", "user_id", "item_type"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )
