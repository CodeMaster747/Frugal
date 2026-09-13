"""Run background work as a one-shot process.

Usage: ``python -m scripts.run_jobs {sweeps-hourly,sweeps-nightly,receipts}``

**There is no Celery worker here, and there cannot be one.** Upstash's free tier
allows 500k commands a month while an idle worker ``BRPOP``s its queues about
once a second -- roughly 2.6M -- so the broker stops answering about a week into
every month, and the symptom is tasks silently not running
(docs/09-deployment.md). A process that starts, works, and exits spends nothing
waiting.

That is also the grain the task layer already has. Eleven of the twelve tasks
are thin sync shims around a plain ``async def``, and the integration suite has
always called those private functions directly, with no broker and no worker.
``worker_async_session`` builds and disposes its own engine per call precisely
because ``asyncio.run`` creates a fresh loop. This module is that same call,
given a command line and a cron schedule.

Invoked by Azure Container Apps Jobs -- see infra/azure/containerapps/jobs.tf.

Every ``app.*`` import is function-local, as in the task modules themselves: the
sweeps image carries no OpenCV, and a module-scope import of the receipts task
would work today and break the first time this file is imported by anything
that does not.
"""

from __future__ import annotations

import argparse
import sys

#: Sweeps that run every hour, in order.
#:
#: Folded from six beat entries into two commands. The ``:15``/``:45`` and
#: ``03:30``/``03:50``/``04:10``/``04:40`` offsets in `beat_schedule` exist
#: because "on a single-concurrency worker two schedules landing together is one
#: of them waiting" -- a one-shot process running them sequentially has no such
#: contention, and four fewer container cold starts per cycle is most of the
#: compute budget.
HOURLY: list[tuple[str, str]] = [
    ("notifications", "_run"),
    ("personalization", "_run_erasure"),
]

#: Sweeps that run once a night, in order.
NIGHTLY: list[tuple[str, str]] = [
    ("market", "_run"),
    ("scraping", "_refresh"),
    ("sms", "_purge"),
    ("personalization", "_refresh_profiles"),
]

#: How many receipts one execution will process.
#:
#: Bounded so a run cannot outlast the job's ``replica_timeout_in_seconds`` and
#: be killed part-way through. Being killed loses no work -- the row stays
#: ``running`` and the stale reaper below reclaims it -- but it costs a whole
#: cycle, and a batch that never finishes never gets to the next receipt either.
RECEIPT_BATCH = 5

#: How long a ``running`` row may sit before another execution reclaims it.
#:
#: A replica killed by the timeout leaves no process to finish the job and no
#: Celery redelivery to rescue it. Without this, one timeout strands a receipt
#: in ``running`` forever and the user watches it spin.
STALE_AFTER_MINUTES = 15


def _run_sweeps(sweeps: list[tuple[str, str]]) -> int:
    """Call each sweep in turn. Returns how many raised.

    Each gets its own ``asyncio.run``, exactly as Celery invokes them, because
    the session and Redis helpers they use are built for a loop that is created
    and destroyed around one call.

    Sequential, never ``gather``: these run against Neon's free tier alongside a
    live API holding its own pool, and the failure mode of exhausting it is
    requests that simply hang.

    One failure must not skip the rest -- the erasure sweep is a compliance
    obligation and does not deserve to be cancelled because the scraper was
    unreachable.
    """
    import asyncio
    import importlib

    failed = 0
    for module_name, function_name in sweeps:
        label = f"{module_name}.{function_name}"
        try:
            module = importlib.import_module(f"app.workers.tasks.{module_name}")
            result = asyncio.run(getattr(module, function_name)())
            print(f"{label}: {result}")
        except Exception as exc:
            # Broad on purpose: one failing sweep must not cancel the rest. The
            # erasure sweep is a compliance obligation and should not be skipped
            # because the scraper could not reach a host.
            failed += 1
            print(f"{label}: FAILED {exc!r}", file=sys.stderr)
    return failed


#: Claim exactly one receipt job, atomically, and return what is needed to run it.
#:
#: The claim lives in Postgres rather than Redis because job state already does
#: (app/core/jobs.py) and Redis is the thing being avoided. ``FOR UPDATE SKIP
#: LOCKED`` is what makes two overlapping executions safe: Container Apps does
#: not serialise scheduled runs, and ``parallelism`` only bounds replicas within
#: one execution.
#:
#: Three clauses, each earning its place:
#:
#: - ``queued`` -- the ordinary case. Note the bare ``'process_receipt'``: that
#:   is what `ReceiptService.enqueue_processing` writes. The dotted Celery name
#:   never reaches this column, and matching on it would return zero rows
#:   forever while looking entirely correct.
#: - ``failed`` with no ``finished_at`` -- how `_record_failure` marks a
#:   *retryable* fault; terminal ones get ``dead_lettered`` **and** a timestamp.
#:   This clause is what replaces Celery's retry path, which is itself already
#:   dead: ``self.retry()`` re-enters ``process_receipt``, whose guard sees
#:   ``is_terminal`` (which includes ``failed``) and returns having done nothing.
#: - ``running`` past the staleness window -- the reaper described above.
_CLAIM = """
    UPDATE jobs
       SET status = 'running',
           started_at = COALESCE(started_at, now()),
           attempts = attempts + 1
     WHERE id = (
           SELECT id FROM jobs
            WHERE task_name = 'process_receipt'
              AND (status = 'queued'
                   OR (status = 'failed'
                       AND finished_at IS NULL
                       AND attempts < :max_attempts)
                   OR (status = 'running'
                       AND started_at < now() - (:stale_minutes * INTERVAL '1 minute')))
            ORDER BY created_at
              FOR UPDATE SKIP LOCKED
            LIMIT 1
           )
    RETURNING id, payload, request_id, user_id, attempts
"""


def _drain_receipts() -> int:
    """Process queued receipts. Returns how many failed.

    Never inserts a ``jobs`` row, only updates. ``uq_jobs_idempotency_key`` is
    unique and the key is never cleared, so re-enqueuing
    ``process_receipt:{receipt_id}`` raises `IntegrityError` and surfaces as a
    409 -- the runner has no business creating work, only finishing it.
    """
    import uuid

    from sqlalchemy import text

    from app.core.clock import utc_now
    from app.core.config import get_settings
    from app.core.database import sync_session
    from app.core.jobs import Job, JobStatus
    from app.core.logging import bind_request_id, bind_user_id
    from app.workers.tasks import receipts

    if get_settings().ocr_engine != "tesseract":
        # The worst failure this job has: every execution succeeds, every
        # receipt reaches `ready`, and every one of them is empty. Loud, because
        # nothing downstream can tell the difference.
        print(
            "OCR_ENGINE is not 'tesseract' -- this run will use the fake engine "
            "and mark receipts processed having extracted nothing.",
            file=sys.stderr,
        )

    claim = text(_CLAIM)
    failed = 0

    for _ in range(RECEIPT_BATCH):
        with sync_session() as session:
            row = (
                session.execute(
                    claim,
                    {
                        "max_attempts": receipts.MAX_RETRIES,
                        "stale_minutes": STALE_AFTER_MINUTES,
                    },
                )
                .mappings()
                .first()
            )
            claimed = dict(row) if row is not None else None

        if claimed is None:
            break

        job_id = str(claimed["id"])
        payload = claimed["payload"] or {}
        receipt_id = uuid.UUID(str(payload["receipt_id"]))

        # Restore the request context so these logs join up with the HTTP
        # request that uploaded the receipt (NFR-4).
        bind_request_id(claimed["request_id"])
        bind_user_id(str(claimed["user_id"]) if claimed["user_id"] else None)

        try:
            result = receipts._run(receipt_id, job_id)
        except Exception as exc:
            # Broad on purpose: the failure belongs on the job row, not in a
            # traceback that abandons the rest of the batch.
            #
            # `_record_failure` reads its argument as "retries already made" and
            # dead-letters at MAX_RETRIES. `attempts` is post-increment, so
            # passing it straight through means the third attempt is terminal:
            # three tries, then a row that is genuinely finished. Passing
            # `attempts - 1` would instead leave a row stuck at `failed` that
            # the claim above can never pick up again and nothing ever
            # dead-lettered.
            receipts._record_failure(job_id, exc, claimed["attempts"])
            failed += 1
            print(f"receipt {receipt_id}: FAILED {exc!r}", file=sys.stderr)
            continue

        with sync_session() as session:
            job = session.get(Job, uuid.UUID(job_id))
            if job is not None:
                job.status = JobStatus.SUCCEEDED.value
                job.finished_at = utc_now()
                job.progress = {"stage": "done", "pct": 100}
                job.result = result

        print(f"receipt {receipt_id}: {result}")

    return failed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m scripts.run_jobs",
        description="Run Frugal's background work once and exit.",
    )
    parser.add_argument(
        "command",
        choices=("sweeps-hourly", "sweeps-nightly", "receipts"),
        help="which body of work to run",
    )
    args = parser.parse_args(argv)

    if args.command == "sweeps-hourly":
        failed = _run_sweeps(HOURLY)
    elif args.command == "sweeps-nightly":
        failed = _run_sweeps(NIGHTLY)
    else:
        failed = _drain_receipts()

    # Non-zero so Container Apps records a failed execution rather than a green
    # one that quietly did nothing. A job whose failures are invisible is the
    # same class of bug as the worker that was never deployed.
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
