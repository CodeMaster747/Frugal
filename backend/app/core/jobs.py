"""Durable background-job state.

Job state lives in **Postgres, not Redis**. Redis is the broker and holds only
regenerable state; an Upstash eviction must never lose a user's receipt. The
row is also what makes a task idempotent on retry and what gives the frontend
something to poll.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, SmallInteger, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDMixin


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEAD_LETTERED = "dead_lettered"


class Job(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "jobs"

    # Nullable: a scheduled sweep belongs to the deployment, not to a person.
    # Cascading anyway, so deleting an account takes its job rows with it --
    # added by migration 0016 and declared here so `alembic check` can see it.
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    task_name: Mapped[str] = mapped_column(String(120), nullable=False)
    celery_task_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=JobStatus.QUEUED.value)

    idempotency_key: Mapped[str | None] = mapped_column(String(200))
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    progress: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    attempts: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    error_type: Mapped[str | None] = mapped_column(String(120))
    error_message: Mapped[str | None] = mapped_column(Text)

    # Closes the observability loop (NFR-4): a failed receipt in production
    # traces back to the HTTP request that started it.
    request_id: Mapped[str | None] = mapped_column(String(64))

    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("ix_jobs_status_created_at", "status", "created_at"),
        Index("ix_jobs_user_id_created_at", "user_id", "created_at"),
        Index(
            "uq_jobs_idempotency_key",
            "idempotency_key",
            unique=True,
            postgresql_where=None,
        ),
    )

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            JobStatus.SUCCEEDED.value,
            JobStatus.FAILED.value,
            JobStatus.DEAD_LETTERED.value,
        }
