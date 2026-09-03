"""sms ingestion and account identifiers

Revision ID: 0017
Revises: 0016
Create Date: 2026-08-17 10:00:00.000000

Two tables for the SMS ingestion path.

`account_identifiers` answers the question every bank alert raises and no other
ingestion path does: "A/c XX1234" names an account, and nothing in the schema
could turn that into a UUID. Every existing path -- manual, CSV, receipt,
recurring -- receives an explicit `account_id` from the client.

`sms_messages` is the intake row and the review queue. Its unique index on
`(user_id, message_hash)` is what stops one message being ingested twice when a
user captures it live and then imports an SMS backup covering the same day.
That is a different question from `transactions.content_hash`, which asks
whether two *transactions* are the same; this asks whether two *messages* are.

Both tables declare `fk_<table>_user_id_users ... ON DELETE CASCADE` explicitly.
`TenantMixin` does not carry a ForeignKey -- migration 0016 added them to all
nineteen existing tables by hand after discovering that account deletion left
1,416,254 transactions orphaned. `test_every_user_owned_table_cascades` queries
the live catalogue and fails the build if a new table forgets.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "account_identifiers",
        sa.Column("account_id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("value", sa.String(length=64), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("source", sa.String(length=20), nullable=False),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_account_identifiers")),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_account_identifiers_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_account_identifiers_user_id_users",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        op.f("ix_account_identifiers_user_id"), "account_identifiers", ["user_id"], unique=False
    )
    op.create_index(
        "ix_account_identifiers_account_id", "account_identifiers", ["account_id"], unique=False
    )
    op.create_index(
        "uq_account_identifiers_user_id_kind_value",
        "account_identifiers",
        ["user_id", "kind", "value"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )

    op.create_table(
        "sms_messages",
        sa.Column("intake", sa.String(length=20), nullable=False),
        sa.Column("sender", sa.String(length=32), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body_redacted", sa.Text(), nullable=False),
        sa.Column("raw_body", sa.Text(), nullable=True),
        sa.Column("message_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=24), nullable=False),
        sa.Column("template_id", sa.String(length=64), nullable=True),
        sa.Column("parser_version", sa.String(length=16), nullable=True),
        sa.Column("confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("parsed", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resolved_account_id", sa.UUID(), nullable=True),
        sa.Column("transaction_id", sa.UUID(), nullable=True),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sms_messages")),
        # SET NULL, not CASCADE: deleting an account should not silently erase
        # the evidence of what was read from the user's messages. The row stays
        # and returns to the review queue as unresolved.
        sa.ForeignKeyConstraint(
            ["resolved_account_id"],
            ["accounts.id"],
            name=op.f("fk_sms_messages_resolved_account_id_accounts"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"],
            ["transactions.id"],
            name=op.f("fk_sms_messages_transaction_id_transactions"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_sms_messages_user_id_users", ondelete="CASCADE"
        ),
    )
    op.create_index(op.f("ix_sms_messages_user_id"), "sms_messages", ["user_id"], unique=False)
    op.create_index(
        "ix_sms_messages_resolved_account_id", "sms_messages", ["resolved_account_id"], unique=False
    )
    op.create_index(
        "ix_sms_messages_transaction_id", "sms_messages", ["transaction_id"], unique=False
    )
    op.create_index(
        "uq_sms_messages_user_id_message_hash",
        "sms_messages",
        ["user_id", "message_hash"],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "ix_sms_messages_user_id_status",
        "sms_messages",
        ["user_id", "status"],
        unique=False,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_sms_messages_user_id_status",
        table_name="sms_messages",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index(
        "uq_sms_messages_user_id_message_hash",
        table_name="sms_messages",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_sms_messages_transaction_id", table_name="sms_messages")
    op.drop_index("ix_sms_messages_resolved_account_id", table_name="sms_messages")
    op.drop_index(op.f("ix_sms_messages_user_id"), table_name="sms_messages")
    op.drop_table("sms_messages")

    op.drop_index(
        "uq_account_identifiers_user_id_kind_value",
        table_name="account_identifiers",
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.drop_index("ix_account_identifiers_account_id", table_name="account_identifiers")
    op.drop_index(op.f("ix_account_identifiers_user_id"), table_name="account_identifiers")
    op.drop_table("account_identifiers")
