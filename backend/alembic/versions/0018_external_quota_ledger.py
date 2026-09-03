"""external quota ledger

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-17 12:00:00.000000

The mechanism behind the zero-recurring-cost constraint (ADR-008).

Two tables, scoped differently on purpose:

`external_quota_usage` has **no** `user_id`. A free-tier allowance belongs to
the deployment, not to a person. Giving it an owner would also pull it into the
account-deletion cascade, where removing a user would refund quota the provider
has already counted and spent.

`external_quota_user_usage` does have one, because a user's share of a shared
allowance genuinely is a fact about that user -- so it carries the cascading
foreign key every user-owned table carries, and
`test_every_user_owned_table_cascades` will check it.

Both unique constraints are what make the reservation atomic: the ledger takes
a call with a single `INSERT ... ON CONFLICT ... DO UPDATE ... WHERE used < cap
RETURNING used`, and no row returned means exhausted. Without the constraint
that statement has nothing to conflict on and every call inserts a fresh row
counting one.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "external_quota_usage",
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("period_kind", sa.String(length=10), nullable=False),
        sa.Column("period_key", sa.String(length=20), nullable=False),
        sa.Column("used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cap", sa.Integer(), nullable=False),
        sa.Column("first_call_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_call_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_quota_usage")),
        sa.UniqueConstraint(
            "provider", "period_kind", "period_key", name="uq_external_quota_usage_period"
        ),
    )

    op.create_table(
        "external_quota_user_usage",
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("period_key", sa.String(length=20), nullable=False),
        sa.Column("used", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_external_quota_user_usage")),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_external_quota_user_usage_user_id_users",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "user_id", "provider", "period_key", name="uq_external_quota_user_usage_period"
        ),
    )
    op.create_index(
        op.f("ix_external_quota_user_usage_user_id"),
        "external_quota_user_usage",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_external_quota_user_usage_user_id"), table_name="external_quota_user_usage"
    )
    op.drop_table("external_quota_user_usage")
    op.drop_table("external_quota_usage")
