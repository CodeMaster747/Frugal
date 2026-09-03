"""Celery worker entrypoint.

The app itself lives in `app.core.queue` -- a broker connection is
infrastructure, like the database engine, and keeping it there is what lets a
router dispatch a task without importing the task module (which would point the
dependency backwards through the layering).

This module's only job is to register the task packages the *worker* should
load. The API process never imports it, and so never pays for OpenCV,
Tesseract, or Prophet.

Workers run synchronously (ADR-006) at concurrency 1 on t3.micro: measured
footprint is ~450 MB during a Prophet fit or an OpenCV pipeline, so a second
concurrent worker is an OOM kill rather than throughput.
"""

from __future__ import annotations

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.core.queue import celery_app

settings = get_settings()
configure_logging(level=settings.log_level, fmt=settings.log_format)

# Registered per milestone. Importing here, in the worker entrypoint only,
# keeps the heavy dependencies out of the API image.
# One call per task module: `related_name` matches a single filename, so a new
# module that is not listed here is silently never registered -- the failure
# mode is a task that queues fine and never runs.
for _module in (
    "receipts",
    "forecasting",
    "market",
    "notifications",
    "sms",
    "personalization",
    "pricegraph",
    "scraping",
):
    celery_app.autodiscover_tasks(["app.workers.tasks"], related_name=_module, force=True)

__all__ = ["celery_app"]
