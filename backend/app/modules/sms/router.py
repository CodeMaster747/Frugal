"""SMS ingestion HTTP layer."""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, File, UploadFile, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.errors import ConflictError, UnprocessableError
from app.core.jobs import Job, JobStatus
from app.core.queue import IMPORT_SMS_BACKUP, dispatch
from app.modules.finance.schemas import TransactionOut
from app.modules.sms.schemas import (
    AccountMappingIn,
    AccountMappingOut,
    DuplicateCandidateOut,
    ParsedSmsOut,
    SmsBatchIn,
    SmsBatchOut,
    SmsCommitIn,
    SmsImportJobOut,
    SmsIngestIn,
    SmsMessageOut,
    SmsParseIn,
    SmsParseOut,
    SmsQueueOut,
)
from app.modules.sms.service import SmsService
from app.modules.sms.xml_import import check_size

router = APIRouter(prefix="/sms", tags=["sms"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> SmsService:
    return SmsService(db)


ServiceDep = Annotated[SmsService, Depends(get_service)]


@router.post("/parse", response_model=SmsParseOut)
async def parse_message(
    payload: SmsParseIn,
    current: CurrentUserDep,
    service: ServiceDep,
) -> SmsParseOut:
    """Read one message and report what would happen to it. Stores nothing.

    Deliberately the first endpoint this feature ships. Granting an app
    permission to read bank messages is a large ask, and the honest way to make
    it answerable is to let someone paste a message in and see exactly what is
    extracted, what is not, and whether it would reach the ledger unattended --
    all before anything is stored.

    A message that is not a transaction returns `parsed: null` with a
    disposition, which is an answer rather than an error: a promotional message
    from a real bank header is *correctly* read as not a transaction.
    """
    parsed, disposition, reason, account_id, unmapped = await service.preview(
        current.id,
        body=payload.body,
        sender=payload.sender,
        received_at=payload.received_at or datetime.now(UTC),
    )
    return SmsParseOut(
        parsed=ParsedSmsOut.model_validate(parsed) if parsed else None,
        disposition=disposition,
        reason=reason,
        resolved_account_id=account_id,
        unmapped_tail=unmapped,
    )


@router.post("/messages", response_model=SmsMessageOut, status_code=status.HTTP_201_CREATED)
async def ingest_message(
    payload: SmsIngestIn,
    current: CurrentUserDep,
    service: ServiceDep,
) -> SmsMessageOut:
    """Store one message, and book it when that is safe.

    Returns 409 when the message is already held -- re-sending the same alert
    is the normal case for a share-sheet user who taps twice, and the intake
    hash catches it before any parsing happens.

    Returns 422 when the message is not a transaction. Nothing is stored in
    that case: a row would be a permanent copy of a personal or promotional
    message kept for no purpose, which is what this feature promises not to do.
    """
    outcome = await service.ingest(
        current.id,
        body=payload.body,
        sender=payload.sender,
        received_at=payload.received_at or datetime.now(UTC),
        intake=payload.intake,
    )
    if outcome.already_held:
        raise ConflictError("This message has already been read")
    if outcome.message is None:
        raise UnprocessableError("This does not look like a transaction alert")
    return SmsMessageOut.model_validate(outcome.message)


@router.post("/messages/batch", response_model=SmsBatchOut)
async def ingest_batch(
    payload: SmsBatchIn,
    current: CurrentUserDep,
    service: ServiceDep,
) -> SmsBatchOut:
    """Store many messages -- the device buffer drain.

    Reports counts rather than per-item errors. A drain is not a user action
    they are watching: it happens when the app comes to the foreground, and the
    only interesting outcome is how many new transactions appeared.
    """
    counts = await service.ingest_batch(
        current.id,
        [(m.body, m.sender, m.received_at or datetime.now(UTC)) for m in payload.messages],
        intake=payload.messages[0].intake,
    )
    return SmsBatchOut(**counts)


@router.get("/messages", response_model=SmsQueueOut)
async def review_queue(current: CurrentUserDep, service: ServiceDep) -> SmsQueueOut:
    """Everything awaiting a human, with the unmapped handles behind it."""
    messages = await service.queue(current.id)
    return SmsQueueOut(
        messages=[SmsMessageOut.model_validate(m) for m in messages],
        unmapped_tails=await service.suggest_identifiers(current.id),
    )


@router.get("/messages/{message_id}/duplicates", response_model=list[DuplicateCandidateOut])
async def duplicates(
    message_id: uuid.UUID,
    current: CurrentUserDep,
    service: ServiceDep,
) -> list[DuplicateCandidateOut]:
    """Transactions this message might already be.

    Surfaced before commit, never merged automatically. Discovering a
    double-counted expense weeks later is far worse than one dismissible
    prompt now.
    """
    return [
        DuplicateCandidateOut.model_validate(c)
        for c in await service.candidates_for(current.id, message_id)
    ]


@router.post("/messages/{message_id}/commit", response_model=TransactionOut)
async def commit_message(
    message_id: uuid.UUID,
    payload: SmsCommitIn,
    current: CurrentUserDep,
    service: ServiceDep,
) -> TransactionOut:
    """Turn a read message into a transaction."""
    txn = await service.commit(
        current.id,
        message_id,
        account_id=payload.account_id,
        category_id=payload.category_id,
        allow_duplicate=payload.allow_duplicate,
    )
    return TransactionOut.model_validate(txn)


@router.post("/messages/{message_id}/dismiss", response_model=SmsMessageOut)
async def dismiss_message(
    message_id: uuid.UUID,
    current: CurrentUserDep,
    service: ServiceDep,
) -> SmsMessageOut:
    """Mark a message as not worth booking, keeping the row as evidence."""
    return SmsMessageOut.model_validate(await service.dismiss(current.id, message_id))


@router.post("/mappings", response_model=AccountMappingOut, status_code=status.HTTP_201_CREATED)
async def map_account(
    payload: AccountMappingIn,
    current: CurrentUserDep,
    service: ServiceDep,
) -> AccountMappingOut:
    """Bind a bank's account handle to one of the user's accounts.

    Every message held for want of this mapping is attached to the account as a
    side effect, but none of them are booked. The user mapped an account; they
    did not ask for a month of held transactions to appear unreviewed.
    """
    identifier = await service.map_account(
        current.id,
        kind=payload.kind,
        value=payload.value,
        account_id=payload.account_id,
    )
    return AccountMappingOut.model_validate(identifier)


@router.get("/mappings", response_model=list[AccountMappingOut])
async def list_mappings(current: CurrentUserDep, service: ServiceDep) -> list[AccountMappingOut]:
    return [AccountMappingOut.model_validate(i) for i in await service.list_mappings(current.id)]


@router.post(
    "/imports/backup", response_model=SmsImportJobOut, status_code=status.HTTP_202_ACCEPTED
)
async def import_backup(
    current: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    file: Annotated[UploadFile, File()],
) -> SmsImportJobOut:
    """Queue an "SMS Backup & Restore" export for import.

    202, not 200: a three-year backup is tens of thousands of messages, and
    parsing that inside a request would hold a worker until it timed out. The
    response carries a job id the client polls.

    This is the path that makes the feature useful with no permission at all --
    it works in a browser, on iOS, and in the APK build that never asks for SMS
    access. It is the primary ingestion route, not a fallback.
    """
    content = await file.read()
    check_size(len(content))

    # Keyed on the file's own digest, so a double-submitted upload -- a
    # refreshed tab, an impatient second tap -- returns the running job rather
    # than starting a second pass over the same fifty thousand messages.
    digest = hashlib.sha256(content).hexdigest()
    key = f"import_sms_backup:{current.id}:{digest}"

    existing = (
        await db.execute(select(Job).where(Job.idempotency_key == key).limit(1))
    ).scalar_one_or_none()
    if existing is not None:
        return SmsImportJobOut.model_validate(existing)

    job = Job(
        user_id=current.id,
        task_name=IMPORT_SMS_BACKUP,
        status=JobStatus.QUEUED.value,
        idempotency_key=key,
        payload={"bytes": len(content)},
    )
    db.add(job)
    await db.commit()

    job.celery_task_id = dispatch(
        IMPORT_SMS_BACKUP,
        # The worker must be able to read the row, so give the commit above
        # time to land before it looks.
        countdown=2,
        job_id=str(job.id),
        user_id=str(current.id),
        xml_text=content.decode("utf-8", errors="replace"),
    )
    await db.commit()
    return SmsImportJobOut.model_validate(job)
