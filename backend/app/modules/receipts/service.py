"""Receipt intelligence service — the module's public interface.

Two rules define this module, and both exist because Tesseract on a thermal
receipt reads at roughly 60--70%:

* **Nothing is committed below the confidence threshold.** Enforced here, not
  in the UI, so no client can bypass it (FR-4.5).
* **A human correction always wins.** Once a person edits a field, the machine's
  reading is history.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ports import ObjectStore
from app.core.clock import utc_now, utc_today
from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError, UnprocessableError, ValidationError
from app.core.jobs import Job, JobStatus
from app.core.logging import get_logger
from app.core.queue import PROMOTE_RECEIPT, dispatch
from app.core.repository import BaseRepository
from app.modules.finance.models import Transaction, TransactionKind, TransactionSource
from app.modules.finance.schemas import TransactionCreate
from app.modules.finance.service import FinanceService, normalize_merchant
from app.modules.receipts.models import (
    MAX_UPLOAD_BYTES,
    REQUIRED_FIELDS,
    FieldName,
    Receipt,
    ReceiptField,
    ReceiptLineItem,
    ReceiptStatus,
)

logger = get_logger(__name__)

ALLOWED_CONTENT_TYPES = {
    "image/jpeg",
    "image/png",
    "image/heic",
    "image/heif",
    "image/webp",
    "application/pdf",
}

#: A same-day transaction within this fraction of the receipt total is offered
#: as a possible duplicate. Loose on purpose -- surfacing a near-match the user
#: can dismiss beats silently double-counting an expense.
DUPLICATE_AMOUNT_TOLERANCE = Decimal("0.02")


class ReceiptRepository(BaseRepository[Receipt]):
    model = Receipt


class ReceiptFieldRepository(BaseRepository[ReceiptField]):
    model = ReceiptField


@dataclass(frozen=True, slots=True)
class UploadTicket:
    receipt_id: uuid.UUID
    upload_url: str
    expires_in: int
    #: Headers the browser must send with the PUT, supplied by the adapter.
    #: Empty on S3; Azure requires `x-ms-blob-type`. Carried on the ticket so
    #: the frontend spreads whatever it is given rather than branching on which
    #: storage backend the deployment happens to run (ADR-004, ADR-010).
    upload_headers: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DuplicateCandidate:
    transaction_id: uuid.UUID
    occurred_on: date
    amount: Decimal
    merchant: str | None
    similarity: Decimal


class ReceiptService:
    def __init__(self, session: AsyncSession, store: ObjectStore) -> None:
        self.session = session
        self.store = store
        self.receipts = ReceiptRepository(session)
        self.fields = ReceiptFieldRepository(session)
        self.settings = get_settings()

    # --- upload -----------------------------------------------------------

    async def create_upload_ticket(
        self, user_id: uuid.UUID, *, content_type: str, size_bytes: int
    ) -> UploadTicket:
        """Register a receipt and hand back a presigned PUT URL.

        The image never transits the API: routing 10 MB through a 250 MB
        process would consume request workers and stall the event loop. The
        API only ever holds the object key.
        """
        if content_type not in ALLOWED_CONTENT_TYPES:
            raise ValidationError(f"Unsupported file type: {content_type}")
        if size_bytes <= 0 or size_bytes > MAX_UPLOAD_BYTES:
            raise ValidationError(
                f"File must be between 1 byte and {MAX_UPLOAD_BYTES // 1024**2} MB"
            )

        receipt = Receipt(
            user_id=user_id,
            s3_key=f"receipts/{user_id}/{uuid.uuid4().hex}",
            content_type=content_type,
            file_size_bytes=size_bytes,
            status=ReceiptStatus.PENDING_UPLOAD.value,
        )
        await self.receipts.add(receipt)

        ttl = self.settings.presigned_url_ttl_seconds
        url = await self.store.presign_put(receipt.s3_key, content_type, ttl)
        return UploadTicket(
            receipt_id=receipt.id,
            upload_url=url,
            expires_in=ttl,
            upload_headers=dict(self.store.upload_headers),
        )

    async def image_url(self, user_id: uuid.UUID, receipt_id: uuid.UUID) -> str:
        """A read URL for the stored image.

        The recorded content type is passed through so the storage service
        serves the object under the type we *accepted*, not the type the bytes
        claim. On Azure a SAS cannot constrain the upload itself, so this is
        what stops a payload smuggled past the ticket check from being served
        as a document from the storage origin.
        """
        receipt = await self.receipts.get_or_404(user_id, receipt_id)
        return await self.store.presign_get(
            receipt.s3_key,
            self.settings.presigned_url_ttl_seconds,
            content_type=receipt.content_type,
        )

    # --- processing -------------------------------------------------------

    async def enqueue_processing(self, user_id: uuid.UUID, receipt_id: uuid.UUID) -> Job:
        """Queue OCR and return a job the client can poll."""
        receipt = await self.receipts.get_or_404(user_id, receipt_id)

        if receipt.status not in {
            ReceiptStatus.PENDING_UPLOAD.value,
            ReceiptStatus.FAILED.value,
        }:
            raise UnprocessableError(f"This receipt is already {receipt.status}")

        if not await self.store.exists(receipt.s3_key):
            raise UnprocessableError("The image has not finished uploading yet")

        receipt.status = ReceiptStatus.QUEUED.value
        job = Job(
            user_id=user_id,
            task_name="process_receipt",
            status=JobStatus.QUEUED.value,
            payload={"receipt_id": str(receipt_id)},
            # One in-flight job per receipt: a double-click must not run OCR
            # twice and pay for it twice.
            idempotency_key=f"process_receipt:{receipt_id}",
        )
        self.session.add(job)
        await self.session.flush()
        return job

    async def record_extraction(
        self,
        receipt_id: uuid.UUID,
        *,
        fields: list[tuple[str, str | None, str | None, Decimal, dict[str, int] | None]],
        line_items: list[tuple[int, str | None, Decimal | None, Decimal]],
        engine_version: str,
        preprocess_report: dict[str, object],
        processing_ms: int,
    ) -> Receipt:
        """Persist what OCR found, and decide whether a human is needed.

        Called from the worker. The threshold decision happens here rather than
        in the task, so it is the same rule regardless of what triggered the
        extraction.
        """
        receipt = await self.session.get(Receipt, receipt_id)
        if receipt is None:
            raise NotFoundError("Receipt")

        threshold = Decimal(str(self.settings.ocr_confidence_threshold))
        receipt.fields.clear()
        receipt.line_items.clear()

        for name, raw_text, parsed, confidence, bbox in fields:
            # A field that is required, missing, or doubtful goes to review.
            # Optional fields that simply were not present do not.
            required = name in {f.value for f in REQUIRED_FIELDS}
            needs_review = required and (parsed is None or confidence < threshold)

            receipt.fields.append(
                ReceiptField(
                    user_id=receipt.user_id,
                    field_name=name,
                    raw_text=raw_text,
                    parsed_value=parsed,
                    confidence=confidence,
                    bbox=bbox,
                    needs_review=needs_review,
                )
            )

        for line_number, description, price, confidence in line_items:
            receipt.line_items.append(
                ReceiptLineItem(
                    user_id=receipt.user_id,
                    line_number=line_number,
                    description=description,
                    total_price=price,
                    confidence=confidence,
                )
            )

        self._project_summary(receipt)
        receipt.ocr_engine_version = engine_version
        receipt.preprocess_report = preprocess_report
        receipt.processing_ms = processing_ms
        receipt.error_message = None

        required_fields = [
            f for f in receipt.fields if f.field_name in {r.value for r in REQUIRED_FIELDS}
        ]
        receipt.overall_confidence = (
            min(f.confidence for f in required_fields) if required_fields else Decimal("0")
        )
        receipt.status = (
            ReceiptStatus.NEEDS_REVIEW.value
            if any(f.needs_review for f in receipt.fields)
            else ReceiptStatus.READY.value
        )

        await self.session.flush()
        return receipt

    @staticmethod
    def _project_summary(receipt: Receipt) -> None:
        """Copy resolved field values onto the receipt for cheap listing."""
        values = {f.field_name: f.effective_value for f in receipt.fields}

        receipt.merchant_extracted = values.get(FieldName.MERCHANT.value)
        receipt.total_extracted = _as_decimal(values.get(FieldName.TOTAL.value))
        receipt.tax_extracted = _as_decimal(values.get(FieldName.TAX.value))
        receipt.date_extracted = _as_date(values.get(FieldName.DATE.value))

    async def mark_failed(self, receipt_id: uuid.UUID, message: str) -> None:
        receipt = await self.session.get(Receipt, receipt_id)
        if receipt is None:
            return
        receipt.status = ReceiptStatus.FAILED.value
        receipt.error_message = message[:500]
        await self.session.flush()

    # --- review -----------------------------------------------------------

    async def apply_corrections(
        self, user_id: uuid.UUID, receipt_id: uuid.UUID, corrections: dict[str, str]
    ) -> Receipt:
        """Record human corrections.

        A corrected field is resolved regardless of what the engine thought:
        the person looking at the image is the authority.
        """
        receipt = await self.receipts.get_or_404(user_id, receipt_id)
        by_name = {f.field_name: f for f in receipt.fields}

        for name, value in corrections.items():
            field = by_name.get(name)
            if field is None:
                raise ValidationError(f"Unknown field: {name}")

            _validate_field_value(name, value)
            field.corrected_value = value
            field.corrected_at = utc_now()
            field.needs_review = False

        self._project_summary(receipt)
        receipt.status = (
            ReceiptStatus.NEEDS_REVIEW.value
            if any(not f.is_resolved for f in receipt.fields)
            else ReceiptStatus.READY.value
        )
        await self.session.flush()
        return receipt

    def blocking_fields(self, receipt: Receipt) -> list[str]:
        """Required fields that still stand in the way of committing."""
        required = {f.value for f in REQUIRED_FIELDS}
        return [
            f.field_name
            for f in receipt.fields
            if f.field_name in required and (not f.is_resolved or f.effective_value is None)
        ]

    # --- duplicates -------------------------------------------------------

    async def duplicate_candidates(
        self, user_id: uuid.UUID, receipt: Receipt
    ) -> list[DuplicateCandidate]:
        """Existing transactions this receipt might already be.

        Surfaced *before* commit (FR-4.7). Discovering a double-counted expense
        weeks later is far worse than one dismissible prompt now.
        """
        if receipt.total_extracted is None or receipt.date_extracted is None:
            return []

        total = Decimal(receipt.total_extracted)
        tolerance = total * DUPLICATE_AMOUNT_TOLERANCE

        stmt = select(Transaction).where(
            Transaction.user_id == user_id,
            Transaction.deleted_at.is_(None),
            Transaction.occurred_on == receipt.date_extracted,
            Transaction.amount >= total - tolerance,
            Transaction.amount <= total + tolerance,
        )
        rows = (await self.session.execute(stmt)).scalars().all()

        merchant = normalize_merchant(receipt.merchant_extracted)
        candidates: list[DuplicateCandidate] = []

        for row in rows:
            # Same day and near-identical amount is already strong. A matching
            # merchant makes it near-certain.
            similarity = Decimal("0.80")
            if merchant and row.merchant_normalized:
                if merchant == row.merchant_normalized:
                    similarity = Decimal("0.99")
                elif merchant in row.merchant_normalized or row.merchant_normalized in merchant:
                    similarity = Decimal("0.90")

            candidates.append(
                DuplicateCandidate(
                    transaction_id=row.id,
                    occurred_on=row.occurred_on,
                    amount=Decimal(row.amount),
                    merchant=row.merchant_raw,
                    similarity=similarity,
                )
            )

        candidates.sort(key=lambda c: c.similarity, reverse=True)
        return candidates

    # --- commit -----------------------------------------------------------

    async def commit(
        self,
        user_id: uuid.UUID,
        receipt_id: uuid.UUID,
        *,
        account_id: uuid.UUID,
        category_id: uuid.UUID | None = None,
        allow_duplicate: bool = False,
    ) -> Transaction:
        """Turn a reviewed receipt into a transaction.

        Refuses while any required field is unresolved. Enforced in the service
        so the rule holds for every caller, not just the UI that happens to
        disable a button (FR-4.5).
        """
        receipt = await self.receipts.get_or_404(user_id, receipt_id)

        if receipt.status == ReceiptStatus.COMMITTED.value:
            raise ConflictError("This receipt has already been committed")

        blocking = self.blocking_fields(receipt)
        if blocking:
            raise ConflictError(
                "Confirm these fields before saving: " + ", ".join(sorted(blocking))
            )

        if not allow_duplicate and await self.duplicate_candidates(user_id, receipt):
            raise ConflictError(
                "A matching transaction already exists. "
                "Review it, or set allow_duplicate=true to save anyway."
            )

        finance = FinanceService(self.session)
        outcome = await finance.create_transaction(
            user_id,
            TransactionCreate(
                account_id=account_id,
                kind=TransactionKind.EXPENSE,
                amount=Decimal(receipt.total_extracted or 0),
                occurred_on=receipt.date_extracted or utc_today(),
                category_id=category_id,
                merchant_raw=receipt.merchant_extracted,
                allow_duplicate=allow_duplicate,
            ),
            source=TransactionSource.RECEIPT,
        )

        if outcome.transaction is None:
            raise ConflictError("An identical transaction already exists")

        outcome.transaction.receipt_id = receipt.id
        receipt.committed_transaction_id = outcome.transaction.id
        receipt.status = ReceiptStatus.COMMITTED.value
        await self.session.flush()

        # Consider the receipt for the shared price graph (ADR-013), in the
        # worker. Dispatched by name, so nothing here imports the task.
        #
        # Deliberately after the flush and deliberately not awaited: the user is
        # recording their own purchase, and they are not waiting on a decision
        # about whether other people get to see the price. A failed promotion
        # must never surface as a failed commit.
        dispatch(PROMOTE_RECEIPT, receipt_id=str(receipt.id), user_id=str(user_id))

        return outcome.transaction

    async def promotion_input(
        self, user_id: uuid.UUID, receipt_id: uuid.UUID
    ) -> PromotionInput | None:
        """What the price graph may see about a committed receipt.

        Returns `None` for anything not committed. Never promote from
        NEEDS_REVIEW: the whole basis of the rule is that a human confirmed the
        merchant, the date and the total.
        """
        from app.modules.pricegraph.models import ReceiptFingerprint

        receipt = await self.receipts.get(user_id, receipt_id)
        if receipt is None or receipt.status != ReceiptStatus.COMMITTED.value:
            return None

        already = False
        if receipt.fingerprint_id is not None:
            fingerprint = await self.session.get(ReceiptFingerprint, receipt.fingerprint_id)
            already = fingerprint is not None and fingerprint.sighting_count > 1

        lines = [
            PromotionLine(
                id=item.id,
                description=item.description,
                quantity=item.quantity,
                unit_price=item.unit_price,
                total_price=item.total_price,
                confidence=item.confidence,
            )
            for item in receipt.line_items
        ]
        line_total = sum(
            (item.total_price for item in receipt.line_items if item.total_price is not None),
            Decimal(0),
        )

        return PromotionInput(
            observed_on=receipt.date_extracted or utc_today(),
            total=receipt.total_extracted,
            line_total=line_total,
            merchant_normalized=normalize_merchant(receipt.merchant_extracted),
            gstin=self._field_value(receipt, FieldName.GSTIN),
            pincode=self._field_value(receipt, FieldName.STORE_PINCODE),
            lines=lines,
            already_contributed=already,
        )

    @staticmethod
    async def unpromoted_receipt_ids(
        session: AsyncSession, user_id: uuid.UUID, *, since: date, limit: int = 25
    ) -> list[uuid.UUID]:
        """Committed receipts this user has that contributed nothing.

        A `LEFT JOIN ... IS NULL` rather than a status column: "was this
        promoted" is a question about whether a `receipt_promotions` row exists,
        and adding a second place to record it would be a second place for it to
        be wrong.

        Static because the retry task has no `ObjectStore` to build a service
        with and needs none -- it is choosing candidates, not reading blobs.
        """
        from app.modules.pricegraph.models import ReceiptPromotion

        stmt = (
            select(Receipt.id)
            .outerjoin(ReceiptPromotion, ReceiptPromotion.receipt_id == Receipt.id)
            .where(
                Receipt.user_id == user_id,
                Receipt.status == ReceiptStatus.COMMITTED.value,
                Receipt.deleted_at.is_(None),
                Receipt.date_extracted >= since,
                ReceiptPromotion.id.is_(None),
            )
            .order_by(Receipt.date_extracted.desc())
            .limit(limit)
        )
        return list((await session.execute(stmt)).scalars().all())

    @staticmethod
    def _field_value(receipt: Receipt, name: FieldName) -> str | None:
        """A human correction always wins, through `effective_value`."""
        field = next((f for f in receipt.fields if f.field_name == name.value), None)
        return field.effective_value if field is not None else None

    async def record_promotion(
        self,
        *,
        user_id: uuid.UUID,
        receipt_id: uuid.UUID,
        line_item_id: uuid.UUID,
        observation_id: uuid.UUID,
        points_awarded: int = 0,
    ) -> None:
        """The uploader's private record of what they contributed.

        This is the join `price_observations` deliberately does not carry.

        `points_awarded` is passed in rather than looked up: the award may have
        been refused by the daily cap or already granted, and only the caller
        knows which. It shipped unset for a milestone, so every contribution
        reported 0 points to its own contributor -- a column written by nobody
        and read by the API and the frontend.
        """
        from app.modules.pricegraph.models import ReceiptPromotion

        self.session.add(
            ReceiptPromotion(
                user_id=user_id,
                receipt_id=receipt_id,
                receipt_line_item_id=line_item_id,
                price_observation_id=observation_id,
                points_awarded=points_awarded,
            )
        )
        await self.session.flush()

    async def delete(self, user_id: uuid.UUID, receipt_id: uuid.UUID) -> None:
        """Remove the receipt and its image.

        Receipts are PII, so the object goes with the row.
        """
        receipt = await self.receipts.get_or_404(user_id, receipt_id)
        try:
            await self.store.delete(receipt.s3_key)
        except Exception as exc:
            logger.warning("could not delete object", exc_info=exc, extra={"key": receipt.s3_key})

        receipt.soft_delete()
        await self.session.flush()


@dataclass(frozen=True, slots=True)
class PromotionLine:
    """One receipt line, as the promotion rule needs it."""

    id: uuid.UUID
    description: str | None
    quantity: Decimal | None
    unit_price: Decimal | None
    total_price: Decimal | None
    confidence: Decimal


@dataclass(frozen=True, slots=True)
class PromotionInput:
    """Everything the price graph may see about one committed receipt.

    Deliberately a projection rather than the ORM object. What is *absent* is
    the point: no `s3_key`, no `raw_text`, no bounding boxes, no payment method.
    A caller cannot leak a field it was never handed, which is a stronger
    guarantee than a caller choosing not to read one.
    """

    observed_on: date
    total: Decimal | None
    line_total: Decimal
    merchant_normalized: str | None
    gstin: str | None
    pincode: str | None
    lines: list[PromotionLine]
    already_contributed: bool


# --- value helpers ---------------------------------------------------------


def _as_decimal(value: str | None) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _validate_field_value(name: str, value: str) -> None:
    """Reject a correction that could not be true.

    A human is authoritative about what the receipt says, not about what a date
    or an amount is -- a typo here would otherwise write a bad transaction.
    """
    if name in {FieldName.TOTAL.value, FieldName.TAX.value, FieldName.SUBTOTAL.value}:
        amount = _as_decimal(value)
        if amount is None or amount < 0:
            raise ValidationError(f"{name} must be a positive amount")

    if name == FieldName.DATE.value:
        parsed = _as_date(value)
        if parsed is None:
            raise ValidationError("date must be in YYYY-MM-DD form")
        if parsed > utc_today():
            raise ValidationError("A receipt cannot be dated in the future")

    if name == FieldName.MERCHANT.value and not value.strip():
        raise ValidationError("merchant cannot be empty")
