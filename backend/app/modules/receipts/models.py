"""Receipt tables.

The per-field structure is what makes human-in-the-loop review workable: a
receipt with a confident merchant and an ambiguous total should ask about the
total alone (FR-4.4).
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.models import (
    CONFIDENCE,
    MONEY,
    Base,
    SoftDeleteMixin,
    TenantMixin,
    TimestampMixin,
    UUIDMixin,
)

MAX_UPLOAD_BYTES = 10 * 1024 * 1024


class ReceiptStatus(StrEnum):
    PENDING_UPLOAD = "pending_upload"
    QUEUED = "queued"
    PROCESSING = "processing"
    NEEDS_REVIEW = "needs_review"
    READY = "ready"
    COMMITTED = "committed"
    FAILED = "failed"


class FieldName(StrEnum):
    MERCHANT = "merchant"
    DATE = "date"
    TOTAL = "total"
    TAX = "tax"
    SUBTOTAL = "subtotal"
    PAYMENT_METHOD = "payment_method"

    # Store identity (M16). These are what turn a private receipt into a row in
    # a shared price graph -- a price with no located seller compares nothing.
    #
    # Deliberately absent from REQUIRED_FIELDS: a user photographing a receipt
    # to record a purchase must never be blocked because the shop's GSTIN was
    # smudged. They gate *promotion*, not commit.
    GSTIN = "gstin"
    STORE_ADDRESS = "store_address"
    STORE_PINCODE = "store_pincode"
    STORE_PHONE = "store_phone"


#: Fields a receipt cannot be committed without.
REQUIRED_FIELDS = (FieldName.MERCHANT, FieldName.DATE, FieldName.TOTAL)


class Receipt(UUIDMixin, TenantMixin, TimestampMixin, SoftDeleteMixin, Base):
    __tablename__ = "receipts"

    # The object *key*, never a URL. A presigned URL expires, so a stored URL
    # is a stored expiry bug; the URL is generated on read.
    s3_key: Mapped[str] = mapped_column(Text, nullable=False)
    content_type: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=ReceiptStatus.PENDING_UPLOAD.value
    )

    merchant_extracted: Mapped[str | None] = mapped_column(String(255))
    total_extracted: Mapped[Decimal | None] = mapped_column(MONEY)
    date_extracted: Mapped[date | None] = mapped_column(Date)
    tax_extracted: Mapped[Decimal | None] = mapped_column(MONEY)

    overall_confidence: Mapped[Decimal | None] = mapped_column(CONFIDENCE)
    ocr_engine_version: Mapped[str | None] = mapped_column(String(32))
    preprocess_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    processing_ms: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

    committed_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "transactions.id",
            ondelete="SET NULL",
        ),
        index=True,
    )

    #: The cross-user duplicate check (ADR-013).
    #:
    #: The link runs this way and only this way. `receipt_fingerprints` names
    #: nobody, so "has anyone contributed this receipt" is answerable in one
    #: indexed lookup while "who" is not answerable from that table at all --
    #: the only path to a person is through this tenant-scoped column, which
    #: the asking user cannot read. That asymmetry is the privacy property.
    fingerprint_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("receipt_fingerprints.id", ondelete="SET NULL"), index=True
    )

    fields: Mapped[list[ReceiptField]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    line_items: Mapped[list[ReceiptLineItem]] = relationship(
        back_populates="receipt",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )

    __table_args__ = (
        Index(
            "ix_receipts_user_id_status",
            "user_id",
            "status",
            postgresql_where=text("deleted_at IS NULL"),
        ),
        Index("ix_receipts_user_id_created_at", "user_id", text("created_at DESC")),
        CheckConstraint(
            f"file_size_bytes > 0 AND file_size_bytes <= {MAX_UPLOAD_BYTES}",
            name="file_size_within_limit",
        ),
    )


class ReceiptField(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """One extracted field, with its own confidence and image region.

    `raw_text` is kept beside `parsed_value` so an OCR failure is debuggable:
    when a total parses wrong, the raw token shows whether the error was in
    recognition or in parsing. `bbox` lets the review UI highlight exactly where
    the value came from, which turns a correction request into an explanation.
    """

    __tablename__ = "receipt_fields"

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("receipts.id", ondelete="CASCADE"), nullable=False
    )
    field_name: Mapped[str] = mapped_column(String(20), nullable=False)

    raw_text: Mapped[str | None] = mapped_column(Text)
    parsed_value: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)
    bbox: Mapped[dict[str, int] | None] = mapped_column(JSONB)

    needs_review: Mapped[bool] = mapped_column(Boolean, nullable=False)
    corrected_value: Mapped[str | None] = mapped_column(Text)
    corrected_at: Mapped[Any | None] = mapped_column(DateTime(timezone=True))

    receipt: Mapped[Receipt] = relationship(back_populates="fields")

    __table_args__ = (
        Index("uq_receipt_fields_receipt_id_field_name", "receipt_id", "field_name", unique=True),
        Index(
            "ix_receipt_fields_review_queue",
            "user_id",
            postgresql_where=text("needs_review AND corrected_at IS NULL"),
        ),
    )

    @property
    def effective_value(self) -> str | None:
        """A human correction always wins over the machine's reading."""
        return self.corrected_value if self.corrected_value is not None else self.parsed_value

    @property
    def is_resolved(self) -> bool:
        return not self.needs_review or self.corrected_at is not None


class ReceiptLineItem(UUIDMixin, TenantMixin, TimestampMixin, Base):
    __tablename__ = "receipt_line_items"

    receipt_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("receipts.id", ondelete="CASCADE"), nullable=False
    )
    line_number: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    description: Mapped[str | None] = mapped_column(String(255))
    quantity: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    unit_price: Mapped[Decimal | None] = mapped_column(MONEY)
    total_price: Mapped[Decimal | None] = mapped_column(MONEY)
    confidence: Mapped[Decimal] = mapped_column(CONFIDENCE, nullable=False)

    receipt: Mapped[Receipt] = relationship(back_populates="line_items")

    __table_args__ = (
        Index(
            "uq_receipt_line_items_receipt_id_line_number",
            "receipt_id",
            "line_number",
            unique=True,
        ),
    )
