"""Request and response contracts for the financial core.

Monetary amounts cross the wire as **strings** (ADR-003): `JSON.parse` produces
an IEEE-754 double, so a numeric `1250.10` would not survive a round trip.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
    model_validator,
)
from pydantic_core.core_schema import ValidationInfo

from app.modules.finance.models import (
    AccountType,
    Cadence,
    GoalStatus,
    RecurringType,
    TransactionKind,
    TransactionSource,
)

# Re-exported deliberately. `TransactionFilters.kind` is typed as this enum, so
# a consumer module cannot build a filter without it -- and reaching into
# `finance.models` for it is what `finance-models-stay-behind-the-service`
# forbids. Naming them here makes the enums part of finance's published
# contract rather than an internal a caller has to smuggle out. (mypy runs with
# `no_implicit_reexport`, so the redundant-looking alias is what publishes it.)
__all__ = [
    "AccountType",
    "Cadence",
    "GoalStatus",
    "RecurringType",
    "TransactionKind",
    "TransactionSource",
]

Amount = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)]
Currency = Annotated[str, Field(min_length=3, max_length=3)]


class MoneyOut(BaseModel):
    """Wire representation of a monetary value."""

    amount: Decimal
    currency: str

    @field_serializer("amount")
    def _as_string(self, value: Decimal) -> str:
        return f"{value:.2f}"


class _Base(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def _decimals_as_strings(self, value: object) -> object:
        return f"{value:.2f}" if isinstance(value, Decimal) else value


# --- accounts --------------------------------------------------------------


class AccountCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    type: AccountType
    currency: Currency = "INR"
    opening_balance: Annotated[Decimal, Field(max_digits=18, decimal_places=2)] = Decimal("0")
    credit_limit: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)] | None = None
    is_liquid: bool = True
    institution: Annotated[str, Field(max_length=120)] | None = None

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @model_validator(mode="after")
    def _credit_card_needs_a_limit(self) -> AccountCreate:
        """Mirrors the database check constraint.

        The constraint is the authority, but reaching it means the caller gets
        a 500 from a raw IntegrityError. Validating here turns it into a clear
        400 that says what to fix.
        """
        if self.type == AccountType.CREDIT_CARD and self.credit_limit is None:
            raise ValueError("A credit card account needs a credit_limit")
        if self.type != AccountType.CREDIT_CARD and self.credit_limit is not None:
            raise ValueError("credit_limit only applies to a credit card account")
        return self


class AccountUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    credit_limit: Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=2)] | None = None
    is_liquid: bool | None = None
    institution: Annotated[str, Field(max_length=120)] | None = None


class AccountOut(_Base):
    id: uuid.UUID
    name: str
    type: str
    currency: str
    opening_balance: Decimal
    current_balance: Decimal
    credit_limit: Decimal | None
    is_liquid: bool
    institution: str | None
    archived_at: datetime | None
    created_at: datetime


# --- categories ------------------------------------------------------------


class CategoryCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=80)]
    kind: Literal["income", "expense", "transfer"]
    parent_id: uuid.UUID | None = None
    icon: Annotated[str, Field(max_length=40)] | None = None
    color: Annotated[str, Field(pattern=r"^#[0-9a-fA-F]{6}$")] | None = None


class CategoryOut(_Base):
    id: uuid.UUID
    name: str
    slug: str
    kind: str
    parent_id: uuid.UUID | None
    icon: str | None
    color: str | None
    is_system: bool
    sort_order: int


class CategoryTreeOut(CategoryOut):
    children: list[CategoryOut] = Field(default_factory=list)


# --- transactions ----------------------------------------------------------


class TransactionCreate(BaseModel):
    account_id: uuid.UUID
    kind: TransactionKind
    amount: Amount
    occurred_on: date
    currency: Currency = "INR"
    category_id: uuid.UUID | None = None
    merchant_raw: Annotated[str, Field(max_length=255)] | None = None
    description: str | None = None

    # For a transfer, the account the money lands in. Required for transfers,
    # rejected otherwise -- see the validator below.
    to_account_id: uuid.UUID | None = None

    # Marks a legitimate repeat (two identical coffees on one day) so the
    # content hash gets a discriminator instead of colliding.
    allow_duplicate: bool = False

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("to_account_id")
    @classmethod
    def _transfer_needs_destination(
        cls, v: uuid.UUID | None, info: ValidationInfo
    ) -> uuid.UUID | None:
        kind = info.data.get("kind")
        if kind == TransactionKind.TRANSFER and v is None:
            raise ValueError("A transfer requires to_account_id")
        if kind != TransactionKind.TRANSFER and v is not None:
            raise ValueError("to_account_id is only valid for a transfer")
        return v


class TransactionUpdate(BaseModel):
    amount: Amount | None = None
    occurred_on: date | None = None
    category_id: uuid.UUID | None = None
    merchant_raw: Annotated[str, Field(max_length=255)] | None = None
    description: str | None = None
    is_reviewed: bool | None = None
    excluded_from_analytics: bool | None = None


class CategoryRef(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    icon: str | None = None


class TransactionOut(_Base):
    id: uuid.UUID
    account_id: uuid.UUID
    kind: str
    amount: Decimal
    currency: str
    occurred_on: date
    merchant_raw: str | None
    merchant_normalized: str | None
    description: str | None
    category: CategoryRef | None = None
    category_confidence: Decimal | None
    categorizer_version: str | None
    transfer_pair_id: uuid.UUID | None
    source: str
    is_reviewed: bool
    excluded_from_analytics: bool
    created_at: datetime


class TransactionFilters(BaseModel):
    from_date: date | None = None
    to_date: date | None = None
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    kind: TransactionKind | None = None
    source: TransactionSource | None = None
    q: str | None = None
    uncategorized_only: bool = False
    #: The review queue: categorised by a machine, not yet confirmed by a person.
    #: Distinct from `uncategorized_only`, which is the opposite problem -- one
    #: asks "is this guess right?", the other "what is this?".
    needs_review: bool = False


class BulkResult(BaseModel):
    """Per-row outcome for a bulk insert.

    A partial CSV import must not be all-or-nothing at 500 rows, and the user
    needs to know exactly which rows were rejected and why.
    """

    index: int
    status: Literal["created", "duplicate", "error"]
    id: uuid.UUID | None = None
    error: str | None = None


class BulkResponse(BaseModel):
    created: int
    duplicates: int
    errors: int
    results: list[BulkResult]


# --- budgets ---------------------------------------------------------------


class BudgetCreate(BaseModel):
    category_id: uuid.UUID | None = None
    period_start: date
    amount_limit: Amount
    currency: Currency = "INR"
    rollover_enabled: bool = False

    @field_validator("period_start")
    @classmethod
    def _first_of_month(cls, v: date) -> date:
        # Budgets are monthly instances; normalising here means every uniqueness
        # check and lookup agrees on what "this period" means.
        return v.replace(day=1)


class BudgetUpdate(BaseModel):
    amount_limit: Amount | None = None
    rollover_enabled: bool | None = None


class BudgetOut(_Base):
    id: uuid.UUID
    category: CategoryRef | None = None
    period: str
    period_start: date
    amount_limit: Decimal
    currency: str
    rollover_enabled: bool
    rollover_amount: Decimal

    # Computed, not stored.
    spent: Decimal = Decimal("0")
    remaining: Decimal = Decimal("0")
    pace: Literal["on_track", "ahead", "over"] = "on_track"


# --- goals -----------------------------------------------------------------


class GoalCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    target_amount: Amount
    currency: Currency = "INR"
    target_date: date | None = None
    linked_account_id: uuid.UUID | None = None
    priority: Annotated[int, Field(ge=1, le=5)] = 3


class GoalUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    target_amount: Amount | None = None
    target_date: date | None = None
    priority: Annotated[int, Field(ge=1, le=5)] | None = None
    status: GoalStatus | None = None


class GoalContribution(BaseModel):
    amount: Amount


class GoalOut(_Base):
    id: uuid.UUID
    name: str
    target_amount: Decimal
    current_amount: Decimal
    currency: str
    target_date: date | None
    linked_account_id: uuid.UUID | None
    priority: int
    status: str
    progress_pct: Decimal = Decimal("0")


# --- recurring items -------------------------------------------------------


class RecurringCreate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)]
    kind: Literal["income", "expense"]
    item_type: RecurringType
    amount: Amount
    cadence: Cadence
    next_due_on: date
    currency: Currency = "INR"
    account_id: uuid.UUID | None = None
    category_id: uuid.UUID | None = None
    end_on: date | None = None


class RecurringUpdate(BaseModel):
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    amount: Amount | None = None
    next_due_on: date | None = None
    end_on: date | None = None
    is_active: bool | None = None


class RecurringOut(_Base):
    id: uuid.UUID
    name: str
    kind: str
    item_type: str
    amount: Decimal
    currency: str
    cadence: str
    next_due_on: date
    end_on: date | None
    account_id: uuid.UUID | None
    category_id: uuid.UUID | None
    amount_variance: Decimal | None
    detection_confidence: Decimal | None
    is_auto_detected: bool
    is_active: bool


# --- import ----------------------------------------------------------------


class ColumnMapping(BaseModel):
    date: str
    amount: str | None = None
    debit: str | None = None
    credit: str | None = None
    merchant: str | None = None
    description: str | None = None

    @field_validator("credit")
    @classmethod
    def _needs_an_amount_source(cls, v: str | None, info: ValidationInfo) -> str | None:
        if not (info.data.get("amount") or info.data.get("debit") or v):
            raise ValueError("Map either an amount column, or debit and/or credit columns")
        return v


class ImportPreviewRow(BaseModel):
    index: int
    occurred_on: date | None
    amount: Decimal | None
    kind: str | None
    merchant: str | None
    is_duplicate: bool = False
    error: str | None = None


class ImportAnalysis(BaseModel):
    import_id: uuid.UUID
    columns: list[str]
    detected_mapping: ColumnMapping | None
    confidence: Decimal
    row_count: int
    preview: list[ImportPreviewRow]
    warnings: list[str] = Field(default_factory=list)
    duplicate_estimate: int = 0


class ImportCommit(BaseModel):
    account_id: uuid.UUID
    mapping: ColumnMapping
    currency: Currency = "INR"
    date_format: str | None = None
