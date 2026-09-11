"""The task queue, as infrastructure.

The Celery app lives in `core` for the same reason the database engine does: it
is a connection to something external, not domain logic. Putting it in
`app.workers` forced every dispatcher to import a task module, which meant
`modules -> workers` -- backwards through the layering, and caught by the
import-linter contract rather than by review.

Dispatch is **by task name**, so a caller never imports the task it is
triggering. That keeps the dependency pointing inward and means a module can be
extracted later without dragging the worker's dependencies along.
"""

from __future__ import annotations

import ssl
from typing import Any
from urllib.parse import parse_qs, urlsplit

from celery import Celery
from celery.schedules import crontab

from app.core.config import get_settings
from app.core.logging import get_logger

settings = get_settings()
logger = get_logger(__name__)


def _redis_tls_options(url: str) -> dict[str, Any] | None:
    """TLS options Celery needs for a `rediss://` URL, or None when it needs none.

    Celery refuses a `rediss://` URL that does not say how to verify the server:
    "A rediss:// URL must have parameter ssl_cert_reqs and this must be set to
    CERT_REQUIRED, CERT_OPTIONAL, or CERT_NONE". Plain redis-py -- which the
    readiness check uses -- accepts the same URL without complaint. So a managed
    Redis that only speaks TLS, which is every free tier worth using (Upstash
    included), passes `/health/ready` and then fails on the first `send_task`.

    That is what took down every endpoint that queues work on the Container Apps
    deployment: account deletion, receipt processing and commit, SMS import,
    forecast refinement, promotion retries. The error came from the result
    backend, which `send_task` touches, so both halves get the options.

    CERT_REQUIRED, never CERT_NONE: an unverified TLS connection to the queue
    carries user ids in task payloads to whoever can answer on that address. A
    URL that states its own `ssl_cert_reqs` is left alone -- kombu reads it.
    """
    parts = urlsplit(url)
    if parts.scheme != "rediss" or "ssl_cert_reqs" in parse_qs(parts.query):
        return None
    return {"ssl_cert_reqs": ssl.CERT_REQUIRED}


celery_app = Celery(
    "frugal",
    broker=str(settings.redis_url),
    backend=str(settings.redis_url),
    # Task modules are registered by the worker entrypoint, not here -- the API
    # process must never import OpenCV or Tesseract.
    include=[],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Acknowledge only after completion, so a worker killed mid-task requeues
    # it rather than silently dropping a user's receipt.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=540,
    result_expires=3600,
    task_default_queue="default",
    task_routes={
        "app.workers.tasks.receipts.*": {"queue": "ocr"},
        "app.workers.tasks.forecasting.*": {"queue": "ml"},
        "app.workers.tasks.market.*": {"queue": "default"},
        "app.workers.tasks.notifications.*": {"queue": "default"},
        "app.workers.tasks.sms.*": {"queue": "default"},
        "app.workers.tasks.personalization.*": {"queue": "default"},
        "app.workers.tasks.pricegraph.*": {"queue": "default"},
        "app.workers.tasks.scraping.*": {"queue": "scrape"},
    },
    beat_schedule={
        # Daily price refresh for tracked products (M9). Early morning UTC is
        # mid-morning in India, after overnight sale prices have settled and
        # before most people shop.
        "refresh-tracked-prices": {
            "task": "app.workers.tasks.market.refresh_prices",
            "schedule": crontab(hour="3", minute="30"),
        },
        # Hourly, which sounds frequent for a daily digest and is not: users
        # pick their own digest hour, so the task must wake often enough to
        # catch each of them. A run with nothing to do costs one query per user.
        "run-notifications": {
            "task": "app.workers.tasks.notifications.run_notifications",
            "schedule": crontab(minute="15"),
        },
        # Nightly, and deliberately not more often. This drops the original
        # text of messages nobody came back to review; the window is a
        # retention promise, not a deadline, and running it hourly would write
        # far more than it cleared.
        "purge-sms-raw-bodies": {
            "task": "app.workers.tasks.sms.purge_raw_bodies",
            "schedule": crontab(hour="4", minute="10"),
        },
        # The erasure sweep (ADR-011). Hourly, at :45 to stay clear of the
        # notification run at :15 -- on a single-concurrency worker two
        # schedules landing together is one of them waiting.
        #
        # Account deletion dispatches this directly, so the ordinary case
        # completes in seconds. The schedule is the guarantee: it is what makes
        # the obligation independent of Redis being up, the worker being
        # alive, and the task not having died.
        "run-erasure": {
            "task": "app.workers.tasks.personalization.run_erasure",
            "schedule": crontab(minute="45"),
        },
        # Nightly re-derivation of spending profiles. Reads only the user's own
        # ledger, so it touches no external service and no quota.
        "refresh-personalization": {
            "task": "app.workers.tasks.personalization.refresh_profiles",
            "schedule": crontab(hour="4", minute="40"),
        },
        # Re-crawl only queries somebody searched in the last week -- the same
        # argument `refresh_prices` makes for polling only tracked products:
        # the value of a price observation is entirely in someone caring about
        # it. A nightly sweep over everything ever searched would spend a
        # host's goodwill on questions nobody is asking any more.
        "refresh-tracked-scrapes": {
            "task": "app.workers.tasks.scraping.refresh_scrapes",
            "schedule": crontab(hour="3", minute="50"),
        },
    },
)


_tls = _redis_tls_options(str(settings.redis_url))
if _tls is not None:
    celery_app.conf.update(broker_use_ssl=_tls, redis_backend_use_ssl=_tls)


# Task names, declared here so a dispatcher never imports the task module.
PROCESS_RECEIPT = "app.workers.tasks.receipts.process_receipt"
#: Tier-3 forecasting. Runs in the worker because the API image does not
#: install Prophet -- see `app.modules.forecasting.tiers`.
GENERATE_FORECAST = "app.workers.tasks.forecasting.generate_forecast"
#: Daily price refresh and drop detection (M9).
REFRESH_PRICES = "app.workers.tasks.market.refresh_prices"
#: Hourly notification generation and delivery (M10).
RUN_NOTIFICATIONS = "app.workers.tasks.notifications.run_notifications"
#: An uploaded SMS Backup & Restore export (M12).
IMPORT_SMS_BACKUP = "app.workers.tasks.sms.import_sms_backup"
#: Nightly retention sweep over unresolved messages' original text (M12).
PURGE_SMS_RAW_BODIES = "app.workers.tasks.sms.purge_raw_bodies"
#: Drains the erasure outbox into the personalization database (ADR-011).
#: Dispatched on account deletion for latency, and scheduled hourly for the
#: guarantee.
RUN_ERASURE = "app.workers.tasks.personalization.run_erasure"
#: Nightly re-derivation of spending profiles (ADR-011).
REFRESH_PROFILES = "app.workers.tasks.personalization.refresh_profiles"
#: Considers a committed receipt for the shared price graph (ADR-013).
PROMOTE_RECEIPT = "app.workers.tasks.pricegraph.promote_receipt"
#: Reconsiders a user's unpromoted receipts after they pin a shop. Without it,
#: a receipt refused for `store_unresolved` is never looked at again.
RETRY_PROMOTIONS = "app.workers.tasks.pricegraph.retry_promotions"
#: Fetches offers from allowlisted hosts (ADR-012). Worker only -- there is
#: no network in the request path.
SCRAPE_OFFERS = "app.workers.tasks.scraping.scrape_offers"
#: Nightly re-crawl of queries somebody actually searched recently.
REFRESH_SCRAPES = "app.workers.tasks.scraping.refresh_scrapes"


def dispatch(task_name: str, *, countdown: int = 0, **kwargs: Any) -> str:
    """Queue a task by name and return its id.

    `countdown` gives the API's transaction time to commit before a worker
    picks the job up -- otherwise the worker can read a row that does not exist
    yet.
    """
    result = celery_app.send_task(task_name, kwargs=kwargs, countdown=countdown)
    return str(result.id)


def dispatch_best_effort(task_name: str, *, countdown: int = 0, **kwargs: Any) -> str | None:
    """Queue a task if the queue will take it; log and carry on if it will not.

    For the call sites whose own contract already says the task is an
    optimisation rather than an obligation -- account deletion, whose erasure is
    guaranteed by the hourly sweep over its outbox, and receipt commit, where "a
    failed promotion must never surface as a failed commit". Before this helper
    existed, both said so in a comment and then called `dispatch`, so a queue
    outage rolled back the very transaction the comment promised it could not
    touch.

    Everything that genuinely needs the task -- anything that stores or returns
    the id -- keeps calling `dispatch` and keeps failing loudly.
    """
    try:
        return dispatch(task_name, countdown=countdown, **kwargs)
    except Exception as exc:
        logger.warning("could not queue %s; relying on its sweep or retry", task_name, exc_info=exc)
        return None
