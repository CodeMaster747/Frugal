"""Declarative base and the mixins every table composes from.

One schema serves both the async API engine and the sync worker engine
(ADR-006). Conventions here are applied uniformly; see docs/03-data-model.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, ClassVar

from sqlalchemy import DateTime, ForeignKey, MetaData, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID as PgUUID  # noqa: N811 — SQLAlchemy's own name
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Explicit naming so Alembic autogenerate produces stable, readable migrations
# instead of database-assigned names that differ between environments.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

MONEY = Numeric(18, 2)
"""Every monetary column. Never Float -- see ADR-003."""

CURRENCY = String(3)
CONFIDENCE = Numeric(4, 3)
"""0.000..1.000 -- confidence and probability columns."""


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_uuid() -> uuid.UUID:
    """UUID for primary keys.

    Generated application-side so an object has an identity before it is
    flushed, which lets services build object graphs without round-tripping.
    """
    return uuid.uuid4()


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)

    # Maps Python annotations to column types, so `Mapped[Decimal]` is
    # NUMERIC(18,2) by default and no model can accidentally get a float.
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        Decimal: MONEY,
        datetime: DateTime(timezone=True),
        uuid.UUID: PgUUID(as_uuid=True),
    }

    def __repr__(self) -> str:
        pk = getattr(self, "id", None)
        return f"<{type(self).__name__} id={pk}>"

    def to_dict(self) -> dict[str, Any]:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


class UUIDMixin:
    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_uuid)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SoftDeleteMixin:
    """30-day restore without breaking referential integrity (FR-2.11).

    Partial indexes on tables using this mixin carry ``WHERE deleted_at IS NULL``
    so deleted rows cost nothing at query time.
    """

    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=None, index=False
    )

    @property
    def is_deleted(self) -> bool:
        return self.deleted_at is not None

    def soft_delete(self) -> None:
        self.deleted_at = utcnow()

    def restore(self) -> None:
        self.deleted_at = None


class TenantMixin:
    """Marks a table as user-owned.

    ``user_id`` is stored directly even where it is reachable through a parent.
    The redundancy is deliberate: it lets BaseRepository scope every query with
    one predicate, making "forgot to check ownership" unrepresentable rather
    than merely discouraged. See docs/03-data-model.md section 8.1.

    **The foreign key belongs here, not in a migration.** Migration 0016 added
    ``ON DELETE CASCADE`` to nineteen tables after the M10 discovery that
    account deletion had never deleted anything -- but it added them in raw SQL,
    and the mixin kept declaring a bare column. Models and schema then disagreed
    about every one of those cascades, so ``alembic revision --autogenerate``
    would have emitted a migration *dropping* them and quietly reintroduced the
    original bug. Declaring it here makes the cascade a property of being
    user-owned: a new tenant table gets it by construction, and ``alembic
    check`` in CI fails if the schema ever drifts back.

    The referent is a string, so this creates no Python import from any module
    to ``auth`` -- the boundary contracts in .importlinter are unaffected.
    """

    user_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
