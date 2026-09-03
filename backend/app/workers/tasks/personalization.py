"""Personalization tasks: the erasure sweep, and nightly profile derivation.

Both go through `PersonalizationService`, exactly as the SMS import task goes
through `SmsService`. A task reaching into the repository would also escape the
subject-scoping sweep, which only sees repositories reachable through the
module -- and would break `the-signals-database-has-one-owner`.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.queue import celery_app

logger = get_logger(__name__)

#: How many subjects one nightly run re-derives. Bounded because the worker
#: runs at concurrency 1 on a 1 GB instance and this must not become the task
#: that starves receipt OCR for an hour.
PROFILE_BATCH = 200


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.personalization.run_erasure", bind=True
)
def run_erasure(self: object) -> dict[str, Any]:
    """Drain the erasure outbox into the personalization database (FR-1.8)."""
    if not get_settings().personalization_enabled:
        return {"status": "disabled"}
    return asyncio.run(_run_erasure())


async def _run_erasure() -> dict[str, Any]:
    from app.core import erasure
    from app.core.erasure import ErasureKind
    from app.core.redis import reset_redis
    from app.core.signals_database import worker_both_sessions
    from app.modules.personalization.service import PersonalizationService

    # The Redis client caches against the loop that made it, the same way the
    # engine does. `asyncio.run` gives this task a new loop.
    await reset_redis()

    erased = failed = 0
    async with worker_both_sessions() as (primary, signals):
        # The price-graph half lives in the primary database and needs no
        # second session, so it is drained first and separately. Doing both in
        # one loop would tie an obligation that *can* be transactional to one
        # that cannot.
        anonymised = await _anonymise_contributions(primary)

        service = PersonalizationService(signals)
        for row in await erasure.pending(primary, kind=ErasureKind.SIGNALS):
            try:
                # The personalization database FIRST, always.
                #
                # There is no transaction spanning the two, so this ordering is
                # the correctness argument rather than a preference. A crash
                # between the two commits leaves the debt outstanding and the
                # sweep runs again -- and deleting zero rows twice is free. The
                # opposite order marks the obligation paid and leaves the data,
                # which is the one outcome that is not recoverable by retrying.
                count = await service.erase(row.subject_id)
                await signals.commit()

                await erasure.complete(primary, row.id)
                await primary.commit()
                erased += count
            except Exception as exc:
                await signals.rollback()
                logger.warning(
                    "erasure failed; debt left outstanding",
                    extra={"subject_id": str(row.subject_id), "error": type(exc).__name__},
                )
                await erasure.fail(primary, row.id, repr(exc))
                await primary.commit()
                failed += 1

    return {
        "status": "ok",
        "rows_erased": erased,
        "contributions_anonymised": anonymised,
        "failed": failed,
    }


async def _anonymise_contributions(primary: AsyncSession) -> int:
    """Sever deleted accounts from their price contributions.

    Same database as the ledger, so this *is* transactional -- unlike the
    signals half. Kept in the same sweep because it is the same obligation
    arriving from the same event, and a second beat entry for it would be one
    more thing to notice has stopped running.
    """
    from app.core import erasure
    from app.core.erasure import ErasureKind
    from app.modules.pricegraph.service import PricegraphService

    total = 0
    service = PricegraphService(primary)
    for row in await erasure.pending(primary, kind=ErasureKind.PRICE_CONTRIBUTIONS):
        try:
            total += await service.anonymise(row.subject_id)
            await erasure.complete(primary, row.id)
            await primary.commit()
        except Exception as exc:
            await primary.rollback()
            logger.warning(
                "contribution anonymisation failed; debt left outstanding",
                extra={"error": type(exc).__name__},
            )
            await erasure.fail(primary, row.id, repr(exc))
            await primary.commit()
    return total


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.personalization.refresh_profiles", bind=True
)
def refresh_profiles(self: object) -> dict[str, Any]:
    """Re-derive spending profiles for active users."""
    if not get_settings().personalization_enabled:
        return {"status": "disabled"}
    return asyncio.run(_refresh_profiles())


async def _refresh_profiles() -> dict[str, Any]:
    from app.core.redis import reset_redis
    from app.core.signals_database import worker_both_sessions
    from app.modules.auth.service import AuthService
    from app.modules.personalization.service import PersonalizationService

    await reset_redis()

    refreshed = failed = 0
    async with worker_both_sessions() as (primary, signals):
        # Sliced here rather than widening `active_user_ids`, which the
        # notification sweep also calls and deliberately wants in full.
        user_ids = list(await AuthService(primary).active_user_ids())[:PROFILE_BATCH]
        service = PersonalizationService(signals, primary)

        for user_id in user_ids:
            try:
                await service.refresh(user_id)
                await signals.commit()
                refreshed += 1
            except Exception as exc:
                await signals.rollback()
                logger.warning(
                    "profile refresh failed",
                    extra={"error": type(exc).__name__},
                )
                failed += 1

    return {"status": "ok", "refreshed": refreshed, "failed": failed}
