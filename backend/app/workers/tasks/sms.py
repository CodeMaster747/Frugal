"""SMS backup import and raw-body retention.

Both tasks go through `SmsService` rather than reaching into the repository, for
the reason `workers/tasks/receipts.py` goes through `ReceiptService`: the rules
about what may be committed unattended live in the service, and a worker that
bypassed them would be a second, quieter ingestion path with different
guarantees. The `sms-internals-are-private` contract makes that a build failure.

Follows the `notifications` task's shape rather than the `receipts` one -- a
sync entry point wrapping one `asyncio.run`, because the work here is ordinary
async ORM traffic rather than a CPU-bound C library.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.clock import utc_now
from app.core.logging import get_logger
from app.core.queue import celery_app

logger = get_logger(__name__)

#: Messages per transaction.
#:
#: The same reasoning as BATCH_SIZE in the notifications task, which recorded
#: what happens without it: one long transaction over the whole input holds a
#: connection from the shared pool and degrades the API while it runs. A batch
#: is also the unit of durability -- a failure at message 40,000 must not
#: discard the 39,999 already read.
BATCH_SIZE = 500


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.sms.import_sms_backup", bind=True, acks_late=True
)
def import_sms_backup(self: object, job_id: str, user_id: str, xml_text: str) -> dict[str, Any]:
    """Read an SMS Backup & Restore export and ingest what it holds.

    The file travels in the task payload rather than through object storage.
    That is a deliberate limit, not an oversight: it caps a backup at the
    broker's message size, which is what `MAX_XML_BYTES` is sized against.
    Routing it through S3 would lift the cap and add a storage round-trip, an
    object to expire, and a second failure mode -- for a file that is read once
    and never needed again.
    """
    del self

    import asyncio

    try:
        return asyncio.run(_run(job_id, user_id, xml_text))
    except Exception as exc:
        logger.warning("sms import failed", exc_info=exc)
        return {"status": "failed", "reason": type(exc).__name__}


async def _run(job_id: str, user_id: str, xml_text: str) -> dict[str, Any]:
    import io

    from app.core.database import worker_async_session
    from app.core.jobs import Job, JobStatus
    from app.core.redis import reset_redis
    from app.modules.sms.service import SmsIntake, SmsService
    from app.modules.sms.xml_import import iter_messages

    # Both async globals bind connections to the loop that opened them, and this
    # task creates a fresh one. See `worker_async_session`.
    await reset_redis()

    owner = uuid.UUID(user_id)
    job_uuid = uuid.UUID(job_id)
    totals = {"read": 0, "created": 0, "already_held": 0, "filtered": 0, "committed": 0}

    async with worker_async_session() as session:
        job = await session.get(Job, job_uuid)
        if job is None:
            logger.error("sms import job missing", extra={"job_id": job_id})
            return {"status": "failed", "reason": "job_missing"}
        job.status = JobStatus.RUNNING.value
        job.started_at = utc_now()
        job.attempts += 1
        await session.commit()

    async def flush(batch: list[tuple[str, str, Any]]) -> None:
        async with worker_async_session() as session:
            counts = await SmsService(session).ingest_batch(
                owner, batch, intake=SmsIntake.XML_IMPORT
            )
            for key, value in counts.items():
                totals[key] += value
            # Progress in the same transaction as the work it describes, so a
            # crash cannot leave the job claiming rows it never wrote.
            job = await session.get(Job, job_uuid)
            if job is not None:
                job.progress = dict(totals)
            await session.commit()

    try:
        batch: list[tuple[str, str, Any]] = []
        for message in iter_messages(io.BytesIO(xml_text.encode())):
            batch.append((message.body, message.sender, message.received_at))
            totals["read"] += 1
            if len(batch) >= BATCH_SIZE:
                await flush(batch)
                batch = []
        if batch:
            await flush(batch)
    except Exception as exc:
        async with worker_async_session() as session:
            job = await session.get(Job, job_uuid)
            if job is not None:
                job.status = JobStatus.FAILED.value
                job.finished_at = utc_now()
                job.error_type = type(exc).__name__
                job.error_message = str(exc)[:2000]
                await session.commit()
        raise

    async with worker_async_session() as session:
        job = await session.get(Job, job_uuid)
        if job is not None:
            job.status = JobStatus.SUCCEEDED.value
            job.finished_at = utc_now()
            job.result = dict(totals)
            await session.commit()

    await reset_redis()
    logger.info("sms import complete", extra=totals)
    return {"status": "ok", **totals}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.sms.purge_raw_bodies", bind=True
)
def purge_raw_bodies(self: object) -> dict[str, object]:
    """Drop the original text of messages past the retention window.

    A message that was committed or dismissed already had its raw body cleared
    at that moment. This catches the rest -- the ones sitting unresolved in a
    review queue nobody came back to, which is the population most likely to
    hold something personal that slipped the sender filter.
    """
    del self

    import asyncio

    try:
        return asyncio.run(_purge())
    except Exception as exc:
        logger.warning("sms retention purge failed", exc_info=exc)
        return {"status": "failed", "reason": type(exc).__name__}


async def _purge() -> dict[str, object]:
    from app.core.database import worker_async_session
    from app.core.redis import reset_redis
    from app.modules.sms.service import SmsService

    await reset_redis()

    async with worker_async_session() as session:
        # Through the service, like every other cross-module call from a
        # worker. The retention rule is policy and belongs where the rest of
        # this module's policy lives.
        purged = await SmsService(session).purge_raw_bodies()
        await session.commit()

    await reset_redis()
    logger.info("sms raw bodies purged", extra={"count": purged})
    return {"status": "ok", "purged": purged}
