"""offer snapshots

Revision ID: 0019
Revises: 0018
Create Date: 2026-08-17 12:30:00.000000

A cache for offer-search results, and the largest single saving against the
metered provider's allowance: a hit costs no quota and no network, and repeated
searches for the same product are the common case rather than the exception.

Shared reference data, so no `user_id` -- two users searching the same product
get the same listings, and scoping this per user would multiply the quota cost
by the number of people who asked.

`expires_at` is indexed because it serves the purge, and the purge is what keeps
this a cache. A table of retailer prices that grows forever is a republished
catalogue, which is a different thing with different terms attached to it.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "offer_snapshots",
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("query_text", sa.String(length=255), nullable=False),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("offers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_offer_snapshots")),
        sa.UniqueConstraint("provider", "query_hash", name="uq_offer_snapshots_provider_query"),
    )
    op.create_index("ix_offer_snapshots_expires_at", "offer_snapshots", ["expires_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_offer_snapshots_expires_at", table_name="offer_snapshots")
    op.drop_table("offer_snapshots")
