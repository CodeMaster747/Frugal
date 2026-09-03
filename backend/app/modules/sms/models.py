"""SMS intake and the account identities that make it resolvable.

`account_identifiers` lives here rather than in `finance` on purpose. An account
tail is not a fact the ledger needs -- nothing in `finance` asks "which account
ends 1234". It exists because an SMS says `A/c XX1234` and something has to turn
that into a UUID, so it belongs to the module that has the problem.

Conventions and index rationale follow docs/03-data-model.md.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import (
    CONFIDENCE,
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)


class IdentifierKind(StrEnum):
    """What kind of handle a bank used to name the account in a message."""

    ACCOUNT_TAIL = "account_tail"  # "A/c XX1234"
    CARD_TAIL = "card_tail"  # "Card ending 5678"
    VPA = "vpa"  # "priya@okhdfcbank"
    WALLET_HANDLE = "wallet_handle"


class IdentifierSource(StrEnum):
    USER = "user"
    SMS_RECONCILIATION = "sms_reconciliation"
    INSTITUTION_MATCH = "institution_match"


class SmsIntake(StrEnum):
    """How the message reached us. Recorded because the paths fail differently."""

    PASTE = "paste"
    SHARE = "share"  # Android share sheet
    XML_IMPORT = "xml_import"  # SMS Backup & Restore
    DEVICE_LIVE = "device_live"  # buffered by the receiver, drained on foreground


class SmsStatus(StrEnum):
    PARSED = "parsed"  # read confidently, awaiting commit
    NEEDS_ACCOUNT = "needs_account"  # read fine, but the tail maps to nothing
    NEEDS_REVIEW = "needs_review"  # read doubtfully
    COMMITTED = "committed"
    IGNORED = "ignored"  # user dismissed it
    UNPARSED = "unparsed"  # no template matched


class AccountIdentifier(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """A handle a bank uses for one of the user's accounts.

    A table rather than a column on `accounts` because the relationship is
    genuinely one-to-many: a single bank account has an account tail *and* a
    debit-card tail *and* one or more VPAs, and messages arrive quoting any of
    them. A column would have forced a choice between them on the first day a
    user added a second card.

    `confirmed_at` is the load-bearing field. A NULL means "we think this tail
    belongs to that account" -- a suggestion the reconciliation screen shows and
    a human accepts. Only a confirmed identifier may auto-commit a transaction.
    Guessing here writes money to the wrong account, which is a silent
    corruption of every balance, budget, and forecast that reads it.
    """

    __tablename__ = "account_identifiers"

    account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("accounts.id", ondelete="CASCADE"), nullable=False
    )

    kind: Mapped[str] = mapped_column(String(20), nullable=False)

    #: Normalised at write time -- lowercased, and for tails stripped to digits.
    #: Banks write the same account as "XX1234", "xx1234", "**1234" and
    #: "...1234" across message templates, so storing the raw form would mean
    #: four identifiers for one account and four chances to miss a match.
    value: Mapped[str] = mapped_column(String(64), nullable=False)

    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))

    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    source: Mapped[str] = mapped_column(String(20), nullable=False)

    __table_args__ = (
        # One handle names one account per user. Partial so a soft-deleted
        # mapping does not block re-adding the same tail later -- the same
        # shape as uq_wishlist_user_product.
        Index(
            "uq_account_identifiers_user_id_kind_value",
            "user_id",
            "kind",
            "value",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Every foreign-key child column carries an index; migration 0015
        # established this after an unindexed cascade ran for ten minutes.
        Index("ix_account_identifiers_account_id", "account_id"),
    )

    @property
    def is_confirmed(self) -> bool:
        return self.confirmed_at is not None


class SmsMessage(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    """One bank alert, and what we made of it.

    Kept as a row even after it becomes a transaction, because the review queue
    has to be able to show its work: which template matched, how confident that
    was, and what the transaction was built from. A parse the user cannot
    inspect is the same failure ADR-002 exists to prevent, one layer lower.
    """

    __tablename__ = "sms_messages"

    intake: Mapped[str] = mapped_column(String(20), nullable=False)

    #: The DLT header, e.g. "VM-HDFCBK". Not a phone number: Indian commercial
    #: senders are alphanumeric header IDs, which is exactly what makes a
    #: sender allowlist workable.
    sender: Mapped[str] = mapped_column(String(32), nullable=False)

    #: When the handset received it -- from the message, never from us. An XML
    #: backup imported today is full of messages from three years ago.
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: What we keep. OTP-shaped and long digit runs masked.
    body_redacted: Mapped[str] = mapped_column(Text, nullable=False)

    #: What we parsed from. Nulled once the message is resolved, or after
    #: `sms_raw_body_retention_days`, whichever comes first. Holding the
    #: original text indefinitely buys nothing after the transaction exists and
    #: is the part of this feature a user would most object to.
    raw_body: Mapped[str | None] = mapped_column(Text)

    #: sha256(user_id | sender | received_at | body). Intake-level dedup, and
    #: distinct from Transaction.content_hash: this stops the *same message*
    #: arriving twice (live capture, then an XML backup covering the same day),
    #: which the transaction hash would only catch afterwards and only when
    #: every component happened to agree.
    message_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[str] = mapped_column(String(24), nullable=False)

    template_id: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(16))
    confidence: Mapped[Decimal | None] = mapped_column(CONFIDENCE)

    #: The parse result: amount, direction, occurred_on, merchant, tail, ref_id.
    #: JSONB rather than columns because it is evidence for the review screen,
    #: not something any engine queries -- the moment it is committed the
    #: authoritative copy is the transaction row.
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    resolved_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("accounts.id", ondelete="SET NULL")
    )
    transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("transactions.id", ondelete="SET NULL")
    )

    __table_args__ = (
        Index(
            "uq_sms_messages_user_id_message_hash",
            "user_id",
            "message_hash",
            unique=True,
            postgresql_where=text("deleted_at IS NULL"),
        ),
        # Serves the review queue, which is the only list view over this table.
        # Partial for the same reason ix_transactions_uncategorized is: the
        # rows anyone queries for are a small and shrinking fraction.
        Index(
            "ix_sms_messages_user_id_status",
            "user_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_sms_messages_resolved_account_id", "resolved_account_id"),
        Index("ix_sms_messages_transaction_id", "transaction_id"),
    )
