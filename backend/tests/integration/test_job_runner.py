"""The one-shot receipt drain (`scripts/run_jobs.py`).

What is new here is the *claiming*, not the pipeline: `test_receipt_worker.py`
already covers `_run` end to end. So `_run` is stubbed throughout. A test that
drove the real pipeline would need a blob in an object store the worker rebuilds
empty on every call, and would pass for the wrong reason.

The properties asserted are the ones a cron-triggered drain lives or dies by:

- a queued job is processed exactly once and marked succeeded,
- a second execution claims nothing that is already done,
- a job whose replica was killed is reclaimed rather than stranded,
- a failure ends somewhere terminal instead of looping forever,
- and the drain never creates work, only finishes it.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _queue_job(db_session, *, status: str = "queued", started_ago_minutes: int | None = None):
    """Insert a receipt job directly.

    Written as SQL rather than through `enqueue_processing`, which would need a
    real receipt row and an uploaded object. `user_id` is left null -- the
    column is nullable by design, and nothing in the claim path reads it.
    """
    job_id = uuid.uuid4()
    receipt_id = uuid.uuid4()
    await db_session.execute(
        text(
            "INSERT INTO jobs (id, task_name, status, payload, attempts, started_at, "
            "created_at, updated_at) "
            "VALUES (:id, 'process_receipt', :status, "
            "CAST(:payload AS jsonb), 0, "
            ":started_at, now(), now())"
        ),
        {
            "id": job_id,
            "status": status,
            "payload": f'{{"receipt_id": "{receipt_id}"}}',
            "started_at": (
                None
                if started_ago_minutes is None
                else datetime.now(UTC) - timedelta(minutes=started_ago_minutes)
            ),
        },
    )
    await db_session.commit()
    return job_id, receipt_id


async def _job_row(db_session, job_id):
    return (
        (
            await db_session.execute(
                text("SELECT status, attempts, finished_at, result FROM jobs WHERE id = :id"),
                {"id": job_id},
            )
        )
        .mappings()
        .first()
    )


async def _job_count(db_session) -> int:
    return (await db_session.execute(text("SELECT count(*) FROM jobs"))).scalar_one()


class TestTheReceiptDrain:
    async def test_it_processes_a_queued_job_and_marks_it_succeeded(self, db_session, monkeypatch):
        from app.workers.tasks import receipts
        from scripts import run_jobs

        job_id, receipt_id = await _queue_job(db_session)
        monkeypatch.setattr(receipts, "_run", lambda r, j: {"receipt_id": str(r)})

        assert run_jobs._drain_receipts() == 0

        row = await _job_row(db_session, job_id)
        assert row["status"] == "succeeded"
        assert row["attempts"] == 1
        assert row["finished_at"] is not None
        assert row["result"]["receipt_id"] == str(receipt_id)

    async def test_it_claims_each_job_once(self, db_session, monkeypatch):
        """Two executions overlap in production; neither may redo the other's work."""
        from app.workers.tasks import receipts
        from scripts import run_jobs

        first, _ = await _queue_job(db_session)
        second, _ = await _queue_job(db_session)

        seen: list[str] = []
        monkeypatch.setattr(receipts, "_run", lambda r, j: seen.append(j) or {"receipt_id": str(r)})

        assert run_jobs._drain_receipts() == 0
        assert sorted(seen) == sorted([str(first), str(second)])

        # A second pass has nothing left to claim -- succeeded is terminal and
        # matches none of the three clauses in the claim.
        seen.clear()
        assert run_jobs._drain_receipts() == 0
        assert seen == [], "the drain re-ran work that was already finished"

    async def test_it_reclaims_a_job_whose_replica_died(self, db_session, monkeypatch):
        """`replica_timeout_in_seconds` kills the container mid-receipt.

        There is no Celery redelivery to rescue it and no process left to finish
        it, so without the staleness clause the row sits in `running` forever
        and the user watches it spin.
        """
        from app.workers.tasks import receipts
        from scripts import run_jobs

        job_id, _ = await _queue_job(db_session, status="running", started_ago_minutes=20)
        monkeypatch.setattr(receipts, "_run", lambda r, j: {"receipt_id": str(r)})

        assert run_jobs._drain_receipts() == 0
        assert (await _job_row(db_session, job_id))["status"] == "succeeded"

    async def test_a_fresh_running_job_is_left_alone(self, db_session, monkeypatch):
        """The other side of the reaper: a job genuinely in flight elsewhere."""
        from app.workers.tasks import receipts
        from scripts import run_jobs

        job_id, _ = await _queue_job(db_session, status="running", started_ago_minutes=1)
        seen: list[str] = []
        monkeypatch.setattr(receipts, "_run", lambda r, j: seen.append(j) or {"receipt_id": str(r)})

        assert run_jobs._drain_receipts() == 0
        assert seen == [], "the drain stole a job another execution was still running"
        assert (await _job_row(db_session, job_id))["status"] == "running"

    async def test_a_failure_ends_somewhere_terminal(self, db_session, monkeypatch):
        """A malformed image fails identically forever.

        `_record_failure` dead-letters on ValueError, and the drain must hand it
        a retry count that lets that happen rather than leaving a row stuck at
        `failed` which the claim can never pick up again.
        """
        from app.workers.tasks import receipts
        from scripts import run_jobs

        job_id, _ = await _queue_job(db_session)

        def _boom(receipt_id, job):
            raise ValueError("could not decode the image")

        monkeypatch.setattr(receipts, "_run", _boom)

        assert run_jobs._drain_receipts() == 1

        row = await _job_row(db_session, job_id)
        assert row["status"] == "dead_lettered"
        assert row["finished_at"] is not None

    async def test_it_never_creates_a_job_row(self, db_session, monkeypatch):
        """`uq_jobs_idempotency_key` is unique and never cleared.

        Re-enqueuing `process_receipt:{receipt_id}` raises IntegrityError and
        surfaces as a 409. The runner finishes work; it must never make any.
        """
        from app.workers.tasks import receipts
        from scripts import run_jobs

        await _queue_job(db_session)
        monkeypatch.setattr(receipts, "_run", lambda r, j: {"receipt_id": str(r)})

        before = await _job_count(db_session)
        run_jobs._drain_receipts()
        assert await _job_count(db_session) == before

    async def test_an_empty_queue_is_not_a_failure(self, db_session):
        """Most executions find nothing. That must exit zero."""
        from scripts import run_jobs

        assert run_jobs._drain_receipts() == 0
