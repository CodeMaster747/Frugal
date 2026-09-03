"""Liveness and readiness endpoints.

Deliberately unauthenticated and outside the versioned prefix: load balancers
and container orchestrators need them before any application concern applies.

Named ``system`` rather than ``health`` to avoid colliding with the financial
health module (app/modules/health), which is a different thing entirely.
"""

from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import check_database, get_db
from app.core.redis import check_redis

router = APIRouter(tags=["system"])

#: The oldest APK build this frontend still works with. Raise it in the same
#: commit that starts calling a native plugin method older builds do not have;
#: below it, the app shows a blocking "download the latest APK" screen instead
#: of failing silently on every device.
MIN_NATIVE_VERSION = 1


class HealthResponse(BaseModel):
    status: Literal["ok"]
    service: str
    environment: str


class DependencyStatus(BaseModel):
    database: bool
    redis: bool
    #: `None` when personalization is not configured for this deployment.
    #: Absent is not degraded -- reporting a dependency as down on a deployment
    #: that never turned the feature on is how a readiness probe becomes noise
    #: operators learn to ignore.
    personalization: bool | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "degraded"]
    dependencies: DependencyStatus


@router.get("/health", response_model=HealthResponse, summary="Liveness probe")
async def health() -> HealthResponse:
    """Is the process up? Checks nothing external, so a database outage does not
    cause the orchestrator to kill an otherwise-healthy container."""
    settings = get_settings()
    return HealthResponse(status="ok", service=settings.app_name, environment=settings.environment)


@router.get("/health/ready", response_model=ReadinessResponse, summary="Readiness probe")
async def readiness(response: Response) -> ReadinessResponse:
    """Can the process serve traffic? Checks Postgres and Redis concurrently.

    Returns 503 when degraded so a load balancer stops routing to it, while the
    body still reports which dependency is at fault.
    """
    database_ok, redis_ok = await asyncio.gather(check_database(), check_redis())
    signals_ok = await _personalization_ready()

    # A personalization outage is not a reason to take the process out of the
    # load balancer. It serves recommendations; the ledger keeps working
    # without it, and returning 503 here would take down a working product to
    # report a degraded extra.
    ready = database_ok and redis_ok

    if not ready:
        response.status_code = 503

    return ReadinessResponse(
        status="ready" if ready else "degraded",
        dependencies=DependencyStatus(
            database=database_ok, redis=redis_ok, personalization=signals_ok
        ),
    )


async def _personalization_ready() -> bool | None:
    """Whether the second database answers, or `None` if there is not one.

    Asked of the module, never of the engine: `core.signals_database` is off
    limits above this layer (`the-signals-database-has-one-owner`), and
    api -> modules is the permitted direction, so readiness needs no carve-out
    in the contract.
    """
    from app.modules.personalization.service import health_check

    return await health_check()


class ProviderStatusOut(BaseModel):
    """One metered dependency's remaining allowance."""

    name: str
    #: `ok` or `paused`.
    status: str
    used: int
    cap: int
    remaining: int
    period_key: str
    message: str


class ProvidersResponse(BaseModel):
    providers: list[ProviderStatusOut]
    #: How long the oldest unpaid erasure obligation has been outstanding, or
    #: `None` when there is none.
    #:
    #: Erasure across the database boundary cannot be atomic (ADR-011), and
    #: "we cannot make this atomic" obliges us to measure it: an unbounded
    #: queue here is a compliance failure that is otherwise entirely
    #: invisible. Alert above 24 hours.
    oldest_pending_erasure_seconds: int | None = None
    #: The oldest APK build the deployed frontend still supports.
    #:
    #: With Capacitor's `server.url`, the JavaScript comes from the server while
    #: the native plugins come from an APK that has no update channel. Deploy a
    #: frontend that calls a plugin method an installed build does not have and
    #: it fails on every device, silently. The app compares this at boot.
    min_native_version: int


@router.get("/system/providers", response_model=ProvidersResponse, summary="Metered dependencies")
async def providers(db: Annotated[AsyncSession, Depends(get_db)]) -> ProvidersResponse:
    """What is available, and what has spent its allowance.

    Unauthenticated and carrying no user data: it reports the state of the
    deployment's own free tiers, which is the same answer for everyone. That
    also means the paused banner still renders on a signed-out page.

    Never raises. An endpoint that exists to explain why something else is
    unavailable is the last thing that should fail.
    """
    from datetime import UTC, datetime

    from app.core.quota import PeriodKind, QuotaLedger, Window, period_keys

    settings = get_settings()
    out: list[ProviderStatusOut] = []

    if settings.offer_search_provider == "serpapi":
        _, month_key = period_keys(datetime.now(UTC))
        state = await QuotaLedger(db).status(
            "serpapi",
            window=Window(PeriodKind.MONTH, month_key, settings.serpapi_monthly_cap),
        )
        out.append(
            ProviderStatusOut(
                name="Live price comparison",
                status="ok" if state.available else "paused",
                used=state.used,
                cap=state.cap,
                remaining=state.remaining,
                period_key=state.period_key,
                message=state.message,
            )
        )

    oldest_erasure: int | None = None
    if settings.personalization_enabled:
        from app.core import erasure

        oldest_erasure = await erasure.oldest_pending_age_seconds(db)

    return ProvidersResponse(
        providers=out,
        min_native_version=MIN_NATIVE_VERSION,
        oldest_pending_erasure_seconds=oldest_erasure,
    )
