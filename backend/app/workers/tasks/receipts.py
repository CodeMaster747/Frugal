"""Receipt OCR task.

Synchronous by design (ADR-006): OpenCV and Tesseract are CPU-bound C
libraries, and wrapping them in async would add a thread-pool layer, obscure
stack traces, and buy no concurrency.

Runs on the `ocr` queue at worker concurrency 1. Measured footprint is ~450 MB
during a 5 MP image, so a second concurrent worker on a 1 GB instance is an OOM
kill rather than throughput.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Any

from celery import Task

from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.database import sync_session
from app.core.jobs import Job, JobStatus
from app.core.logging import bind_request_id, bind_user_id, get_logger
from app.core.queue import celery_app

logger = get_logger(__name__)

MAX_RETRIES = 3


# Celery's decorator is untyped, so mypy cannot see through it.
@celery_app.task(  # type: ignore[untyped-decorator]
    bind=True,
    name="app.workers.tasks.receipts.process_receipt",
    max_retries=MAX_RETRIES,
    # Exponential backoff with jitter: a transient storage blip should not have
    # every retry land at the same instant.
    autoretry_for=(OSError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    acks_late=True,
)
def process_receipt(self: Task, job_id: str) -> dict[str, Any]:
    """Preprocess, OCR, extract, and persist one receipt.

    Every state transition is written to the `jobs` row in Postgres, so a
    Redis eviction cannot lose the work and the client always has something to
    poll.
    """
    started = time.perf_counter()

    with sync_session() as session:
        job = session.get(Job, uuid.UUID(job_id))
        if job is None:
            logger.error("job row missing", extra={"job_id": job_id})
            return {"status": "missing"}

        # Restore the request context so worker logs join up with the HTTP
        # request that started this (NFR-4).
        bind_request_id(job.request_id)
        bind_user_id(str(job.user_id) if job.user_id else None)

        if job.is_terminal:
            # acks_late means a task can be redelivered after it succeeded.
            logger.info("job already finished, skipping", extra={"job_id": job_id})
            return {"status": job.status}

        job.status = JobStatus.RUNNING.value
        job.attempts += 1
        job.started_at = job.started_at or utc_now()
        job.celery_task_id = self.request.id
        job.progress = {"stage": "fetching", "pct": 5}
        session.commit()

        payload = job.payload or {}
        receipt_id = uuid.UUID(str(payload["receipt_id"]))

    try:
        result = _run(receipt_id, job_id)
    except Exception as exc:
        _record_failure(job_id, exc, self.request.retries)
        # Retries are for transient faults. A malformed image will fail
        # identically forever, so it is dead-lettered rather than retried.
        if isinstance(exc, ValueError) or self.request.retries >= MAX_RETRIES:
            raise
        raise self.retry(exc=exc) from exc

    elapsed_ms = int((time.perf_counter() - started) * 1000)
    with sync_session() as session:
        job = session.get(Job, uuid.UUID(job_id))
        if job is not None:
            job.status = JobStatus.SUCCEEDED.value
            job.finished_at = utc_now()
            job.progress = {"stage": "done", "pct": 100}
            job.result = result | {"elapsed_ms": elapsed_ms}
            session.commit()

    logger.info("receipt processed", extra={"receipt_id": str(receipt_id), "ms": elapsed_ms})
    return result


def _run(receipt_id: uuid.UUID, job_id: str) -> dict[str, Any]:
    """The pipeline itself, isolated from job bookkeeping."""
    import asyncio

    from app.modules.receipts.pipeline import extract as extractor
    from app.modules.receipts.pipeline import preprocess as preprocessor

    settings = get_settings()
    store = _build_store(settings)
    engine = _build_engine(settings)

    with sync_session() as session:
        from app.modules.receipts.models import Receipt

        receipt = session.get(Receipt, receipt_id)
        if receipt is None:
            raise ValueError(f"Receipt {receipt_id} not found")
        key = receipt.s3_key
        receipt.status = "processing"
        session.commit()

    _progress(job_id, "preprocessing", 25)
    # The store port is async; this task is sync. One short bridge here beats
    # making the whole pipeline async for no concurrency gain.
    image_bytes = asyncio.run(store.get_bytes(key))

    processed, report = preprocessor.preprocess(image_bytes)

    _progress(job_id, "ocr", 60)
    ocr_started = time.perf_counter()
    ocr = engine.recognize(processed)
    ocr_ms = int((time.perf_counter() - ocr_started) * 1000)

    _progress(job_id, "extracting", 85)
    extraction = extractor.extract(ocr)

    with sync_session() as session:
        _persist(session, receipt_id, extraction, ocr.engine_version, report.as_dict(), ocr_ms)
        # Fingerprint the *preprocessed* image, not the original: deskewing and
        # binarising are what make the hash robust to the angle and lighting
        # differences between two photographs of one piece of paper (ADR-013).
        _fingerprint(session, receipt_id, processed)
        session.commit()

    return {
        "receipt_id": str(receipt_id),
        "fields_found": sum(1 for f in extraction.fields if f.found),
        "needs_review": any(
            f.confidence < Decimal(str(get_settings().ocr_confidence_threshold))
            for f in extraction.fields
            if f.name in {"merchant", "date", "total"}
        ),
        "overall_confidence": str(extraction.overall_confidence),
    }


def _persist(
    session: Any,
    receipt_id: uuid.UUID,
    extraction: Any,
    engine_version: str,
    report: dict[str, Any],
    processing_ms: int,
) -> None:
    """Write fields synchronously.

    Mirrors `ReceiptService.record_extraction`, which is async and therefore
    unusable from this sync task. The threshold rule is read from the same
    setting so the two cannot disagree about what needs review.
    """
    from app.modules.receipts.models import (
        REQUIRED_FIELDS,
        Receipt,
        ReceiptField,
        ReceiptLineItem,
        ReceiptStatus,
    )

    receipt = session.get(Receipt, receipt_id)
    if receipt is None:
        raise ValueError(f"Receipt {receipt_id} disappeared during processing")

    threshold = Decimal(str(get_settings().ocr_confidence_threshold))
    required = {f.value for f in REQUIRED_FIELDS}

    session.query(ReceiptField).filter_by(receipt_id=receipt_id).delete()
    session.query(ReceiptLineItem).filter_by(receipt_id=receipt_id).delete()

    for field in extraction.fields:
        needs_review = field.name in required and (
            field.parsed_value is None or field.confidence < threshold
        )
        session.add(
            ReceiptField(
                user_id=receipt.user_id,
                receipt_id=receipt_id,
                field_name=field.name,
                raw_text=field.raw_text,
                parsed_value=field.parsed_value,
                confidence=field.confidence,
                bbox=field.bbox,
                needs_review=needs_review,
            )
        )

    for item in extraction.line_items:
        session.add(
            ReceiptLineItem(
                user_id=receipt.user_id,
                receipt_id=receipt_id,
                line_number=item.line_number,
                description=item.description,
                quantity=item.quantity,
                total_price=item.total_price,
                confidence=item.confidence,
            )
        )

    values = {f.name: f.parsed_value for f in extraction.fields}
    receipt.merchant_extracted = values.get("merchant")
    receipt.total_extracted = _decimal(values.get("total"))
    receipt.tax_extracted = _decimal(values.get("tax"))
    receipt.date_extracted = _date(values.get("date"))
    receipt.overall_confidence = extraction.overall_confidence
    receipt.ocr_engine_version = engine_version
    receipt.preprocess_report = report
    receipt.processing_ms = processing_ms

    blocked = any(
        f.name in required and (f.parsed_value is None or f.confidence < threshold)
        for f in extraction.fields
    )
    receipt.status = ReceiptStatus.NEEDS_REVIEW.value if blocked else ReceiptStatus.READY.value


def _fingerprint(session: Any, receipt_id: uuid.UUID, image_bytes: bytes) -> None:
    """Record whether anyone, anywhere, has contributed this receipt before.

    Computed here because this is the one place that already holds the image
    bytes -- and they are discarded afterwards exactly as they are today. No
    retention changes, which is the constraint that made a bespoke DCT hash
    preferable to a dependency that wanted the file on disk.

    Failure is swallowed. A duplicate check is an anti-abuse control on an
    optional feature; a receipt the user photographed must not fail to process
    because a hash could not be taken.
    """
    from app.modules.pricegraph.models import ReceiptFingerprint
    from app.modules.receipts.models import FieldName, Receipt, ReceiptField
    from app.modules.receipts.pipeline import fingerprint as fp

    try:
        receipt = session.get(Receipt, receipt_id)
        if receipt is None or receipt.date_extracted is None or receipt.total_extracted is None:
            return

        gstin_row = (
            session.query(ReceiptField)
            .filter_by(receipt_id=receipt_id, field_name=FieldName.GSTIN.value)
            .one_or_none()
        )
        line_totals = [
            item.total_price for item in receipt.line_items if item.total_price is not None
        ]

        digest = fp.content_hash(
            gstin=gstin_row.parsed_value if gstin_row else None,
            merchant_normalized=(receipt.merchant_extracted or "").strip().lower() or None,
            observed_on=receipt.date_extracted,
            total=receipt.total_extracted,
            line_totals=line_totals,
        )
        phash = fp.perceptual_hash(image_bytes)

        existing = session.query(ReceiptFingerprint).filter_by(content_hash=digest).one_or_none()
        if existing is None:
            # No exact match. Look for the same paper photographed differently,
            # blocking on date and total first -- normally nought to three rows,
            # so the Hamming comparison never runs over the whole table.
            for candidate in (
                session.query(ReceiptFingerprint)
                .filter_by(observed_on=receipt.date_extracted)
                .limit(50)
                .all()
            ):
                if fp.looks_like_the_same_paper(
                    phash_a=phash,
                    phash_b=candidate.phash,
                    date_a=receipt.date_extracted,
                    date_b=candidate.observed_on,
                    total_a=receipt.total_extracted,
                    total_b=candidate.total,
                ):
                    existing = candidate
                    break

        if existing is None:
            existing = ReceiptFingerprint(
                content_hash=digest,
                phash=phash,
                observed_on=receipt.date_extracted,
                total=receipt.total_extracted,
                first_seen_on=receipt.date_extracted,
                sighting_count=1,
            )
            session.add(existing)
            session.flush()
        else:
            existing.sighting_count += 1

        receipt.fingerprint_id = existing.id
    except Exception as exc:
        logger.warning("could not fingerprint receipt", exc_info=exc)


def _record_failure(job_id: str, exc: Exception, retries: int) -> None:
    """Persist the failure, dead-lettering once retries are exhausted."""
    with sync_session() as session:
        job = session.get(Job, uuid.UUID(job_id))
        if job is None:
            return

        terminal = isinstance(exc, ValueError) or retries >= MAX_RETRIES
        job.status = JobStatus.DEAD_LETTERED.value if terminal else JobStatus.FAILED.value
        job.error_type = type(exc).__name__
        # The exception text is preserved, not just the type: "could not decode
        # the image" and "connection refused" need different responses.
        job.error_message = str(exc)[:2000]
        job.finished_at = utc_now() if terminal else None
        session.commit()

        if terminal and job.payload:
            from app.modules.receipts.models import Receipt, ReceiptStatus

            receipt = session.get(Receipt, uuid.UUID(str(job.payload["receipt_id"])))
            if receipt is not None:
                receipt.status = ReceiptStatus.FAILED.value
                receipt.error_message = str(exc)[:500]
                session.commit()

    logger.exception("receipt processing failed", extra={"job_id": job_id, "retries": retries})


def _progress(job_id: str, stage: str, pct: int) -> None:
    with sync_session() as session:
        job = session.get(Job, uuid.UUID(job_id))
        if job is not None:
            job.progress = {"stage": stage, "pct": pct}
            session.commit()


def _build_store(settings: Any) -> Any:
    """The worker's half of the composition root.

    Duplicated from `app.main` rather than shared: the worker is a separate
    entry point that must not import the FastAPI application, and the boundary
    contracts in .importlinter enforce that.
    """
    from app.workers.storage import build_object_store

    return build_object_store(settings)


def _build_engine(settings: Any) -> Any:
    from app.adapters.ocr.fake import FakeOCREngine
    from app.adapters.ocr.tesseract import TesseractEngine

    return TesseractEngine() if settings.ocr_engine == "tesseract" else FakeOCREngine()


def _decimal(value: str | None) -> Decimal | None:
    try:
        return Decimal(value) if value else None
    except Exception:
        return None


def _date(value: str | None) -> Any:
    from datetime import date

    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None
