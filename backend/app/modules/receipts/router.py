"""Receipt HTTP layer."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_serializer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ports import ObjectStore
from app.core import queue
from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.errors import NotFoundError
from app.core.jobs import Job
from app.modules.receipts.models import MAX_UPLOAD_BYTES, Receipt
from app.modules.receipts.service import ALLOWED_CONTENT_TYPES, ReceiptService

router = APIRouter(prefix="/receipts", tags=["receipts"])


def get_store(request: Request) -> ObjectStore:
    """The adapter chosen at composition time (ADR-004)."""
    return request.app.state.object_store  # type: ignore[no-any-return]


def get_service(
    db: Annotated[AsyncSession, Depends(get_db)],
    store: Annotated[ObjectStore, Depends(get_store)],
) -> ReceiptService:
    return ReceiptService(db, store)


ServiceDep = Annotated[ReceiptService, Depends(get_service)]


class _Out(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    @field_serializer("*", when_used="json", check_fields=False)
    def _decimals(self, value: object) -> object:
        return format(value, "f") if isinstance(value, Decimal) else value


# --- schemas ---------------------------------------------------------------


class UploadRequest(BaseModel):
    content_type: str
    size_bytes: Annotated[int, Field(gt=0, le=MAX_UPLOAD_BYTES)]


class UploadResponse(BaseModel):
    receipt_id: uuid.UUID
    upload_url: str
    expires_in: int
    accepted_types: list[str]
    #: Headers the client must send with the PUT. Empty on S3; Azure needs
    #: `x-ms-blob-type`. The client spreads these rather than knowing which
    #: backend it is uploading to.
    upload_headers: dict[str, str] = Field(default_factory=dict)


class JobResponse(BaseModel):
    job_id: uuid.UUID
    status: str
    poll_url: str
    estimated_seconds: int


class FieldOut(_Out):
    field_name: str
    raw_text: str | None
    parsed_value: str | None
    corrected_value: str | None
    effective_value: str | None
    confidence: Decimal
    bbox: dict[str, int] | None
    needs_review: bool
    corrected_at: datetime | None


class LineItemOut(_Out):
    line_number: int
    description: str | None
    total_price: Decimal | None
    confidence: Decimal


class DuplicateOut(_Out):
    transaction_id: uuid.UUID
    occurred_on: date
    amount: Decimal
    merchant: str | None
    similarity: Decimal


class ReceiptOut(_Out):
    id: uuid.UUID
    status: str
    content_type: str
    merchant_extracted: str | None
    total_extracted: Decimal | None
    date_extracted: date | None
    tax_extracted: Decimal | None
    overall_confidence: Decimal | None
    ocr_engine_version: str | None
    processing_ms: int | None
    error_message: str | None
    committed_transaction_id: uuid.UUID | None
    created_at: datetime

    fields: list[FieldOut] = Field(default_factory=list)
    line_items: list[LineItemOut] = Field(default_factory=list)
    duplicate_candidates: list[DuplicateOut] = Field(default_factory=list)
    blocking_fields: list[str] = Field(default_factory=list)


class CorrectionsRequest(BaseModel):
    corrections: dict[str, str]


class CommitRequest(BaseModel):
    account_id: uuid.UUID
    category_id: uuid.UUID | None = None
    allow_duplicate: bool = False


# --- routes ----------------------------------------------------------------


@router.post("/upload-url", response_model=UploadResponse, status_code=status.HTTP_201_CREATED)
async def create_upload_url(
    data: UploadRequest, current: CurrentUserDep, service: ServiceDep
) -> UploadResponse:
    """Register a receipt and return a presigned PUT URL.

    The browser uploads straight to object storage; the image never passes
    through the API (FR-4.1).
    """
    ticket = await service.create_upload_ticket(
        current.id, content_type=data.content_type, size_bytes=data.size_bytes
    )
    return UploadResponse(
        receipt_id=ticket.receipt_id,
        upload_url=ticket.upload_url,
        expires_in=ticket.expires_in,
        accepted_types=sorted(ALLOWED_CONTENT_TYPES),
        upload_headers=ticket.upload_headers,
    )


@router.post(
    "/{receipt_id}/process", response_model=JobResponse, status_code=status.HTTP_202_ACCEPTED
)
async def process(
    receipt_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep
) -> JobResponse:
    """Queue OCR.

    202 with a job handle rather than a blocking call: preprocessing plus
    Tesseract on a 5 MP image takes seconds, and holding a request open for it
    would occupy a worker on a 1 GB instance.
    """
    job = await service.enqueue_processing(current.id, receipt_id)

    # Dispatched by name, so this module never imports the worker package --
    # that would point the dependency backwards through the layering (ADR-001).
    # The countdown lets this request's transaction commit before a worker
    # picks the job up.
    queue.dispatch(queue.PROCESS_RECEIPT, countdown=1, job_id=str(job.id))

    return JobResponse(
        job_id=job.id,
        status=job.status,
        poll_url=f"/api/v1/jobs/{job.id}",
        estimated_seconds=15,
    )


@router.get("", response_model=list[ReceiptOut])
async def list_receipts(
    current: CurrentUserDep,
    service: ServiceDep,
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ReceiptOut]:
    stmt = (
        service.receipts.scoped_select(current.id).order_by(Receipt.created_at.desc()).limit(limit)
    )
    if status_filter:
        stmt = stmt.where(Receipt.status == status_filter)

    rows = (await service.session.execute(stmt)).scalars().all()
    return [_shape(r, service, duplicates=[]) for r in rows]


@router.get("/{receipt_id}", response_model=ReceiptOut)
async def get_receipt(
    receipt_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep
) -> ReceiptOut:
    """A receipt with its per-field confidence and any duplicate candidates.

    Duplicates are surfaced here, before commit, so the user resolves the
    collision rather than discovering a double-counted expense later.
    """
    receipt = await service.receipts.get_or_404(current.id, receipt_id)
    duplicates = await service.duplicate_candidates(current.id, receipt)
    return _shape(receipt, service, duplicates)


@router.get("/{receipt_id}/image-url")
async def image_url(
    receipt_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep
) -> dict[str, object]:
    """Short-lived read URL. Generated per request, never stored."""
    return {
        "url": await service.image_url(current.id, receipt_id),
        "expires_in": service.settings.presigned_url_ttl_seconds,
    }


@router.patch("/{receipt_id}/fields", response_model=ReceiptOut)
async def correct_fields(
    receipt_id: uuid.UUID,
    data: CorrectionsRequest,
    current: CurrentUserDep,
    service: ServiceDep,
) -> ReceiptOut:
    """Record human corrections. A corrected field is resolved."""
    receipt = await service.apply_corrections(current.id, receipt_id, data.corrections)
    duplicates = await service.duplicate_candidates(current.id, receipt)
    return _shape(receipt, service, duplicates)


@router.post("/{receipt_id}/commit", status_code=status.HTTP_201_CREATED)
async def commit(
    receipt_id: uuid.UUID,
    data: CommitRequest,
    current: CurrentUserDep,
    service: ServiceDep,
) -> dict[str, object]:
    """Create a transaction from a reviewed receipt.

    Returns 409 while any required field is unresolved — the same rule the UI
    enforces, applied server-side so no client can bypass it.
    """
    transaction = await service.commit(
        current.id,
        receipt_id,
        account_id=data.account_id,
        category_id=data.category_id,
        allow_duplicate=data.allow_duplicate,
    )
    return {
        "transaction_id": str(transaction.id),
        "amount": format(Decimal(transaction.amount), "f"),
        "occurred_on": transaction.occurred_on.isoformat(),
    }


@router.delete("/{receipt_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_receipt(
    receipt_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep
) -> None:
    """Delete the receipt and its stored image — receipts are PII."""
    await service.delete(current.id, receipt_id)


def _shape(receipt: Receipt, service: ReceiptService, duplicates: list[Any]) -> ReceiptOut:
    out = ReceiptOut.model_validate(receipt)
    out.fields = [FieldOut.model_validate(f) for f in receipt.fields]
    out.line_items = [LineItemOut.model_validate(i) for i in receipt.line_items]
    out.duplicate_candidates = [DuplicateOut.model_validate(d) for d in duplicates]
    out.blocking_fields = service.blocking_fields(receipt)
    return out


# --- jobs ------------------------------------------------------------------

jobs_router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobStatusOut(_Out):
    id: uuid.UUID
    task_name: str
    status: str
    attempts: int
    progress: dict[str, Any] | None
    result: dict[str, Any] | None
    error_type: str | None
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None


@jobs_router.get("/{job_id}", response_model=JobStatusOut)
async def job_status(
    job_id: uuid.UUID,
    current: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> JobStatusOut:
    """Poll a background job.

    Scoped to the caller: a job id must not expose another user's work.
    """
    stmt = select(Job).where(Job.id == job_id, Job.user_id == current.id)
    job = (await db.execute(stmt)).scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job")
    return JobStatusOut.model_validate(job)
