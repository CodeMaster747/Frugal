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
    """Drain the erasure outbox (FR-1.8).

    No `disabled` guard, deliberately. `delete_account` records the
    price-contribution debt *unconditionally* -- the price graph lives in the
    primary database, so that obligation exists in every deployment -- and
    returning early because the **second** database was absent left it with
    nothing able to pay it. Production runs without `SIGNALS_DATABASE_URL`, so
    that was every production deletion.
    """
    return asyncio.run(_run_erasure())


async def _run_erasure() -> dict[str, Any]:
    from app.core import erasure
    from app.core.database import worker_async_session
    from app.core.erasure import ErasureKind
    from app.core.redis import reset_redis
    from app.core.signals_database import worker_both_sessions
    from app.modules.personalization.service import PersonalizationService

    # The Redis client caches against the loop that made it, the same way the
    # engine does. `asyncio.run` gives this task a new loop.
    await reset_redis()

    # The price-graph half first, in a session of its own.
    #
    # It "lives in the primary database and needs no second session" -- which
    # this comment always claimed, while the code ran it inside
    # `worker_both_sessions()`, a context that *raises* when there is no second
    # database. So where personalization was unconfigured it was unreachable
    # twice over, and the debt accumulated with the gauge switched off. Its own
    # session is what finally makes the claim true.
    async with worker_async_session() as primary:
        anonymised = await _anonymise_contributions(primary)
        blobs = await _delete_blobs(primary)

    if not get_settings().personalization_enabled:
        return {
            "status": "ok",
            "personalization": "disabled",
            "rows_erased": 0,
            "contributions_anonymised": anonymised,
            "blobs_deleted": blobs,
            "failed": 0,
        }

    erased = failed = 0
    async with worker_both_sessions() as (primary, signals):
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
        "blobs_deleted": blobs,
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


async def _delete_blobs(primary: AsyncSession) -> int:
    """Remove a deleted account's receipt images from object storage.

    The obligation with nothing left to drive it: `receipts` cascades away with
    the account and takes the only copy of `s3_key`, so there is no row to read
    the keys from and no list of them anywhere. They are rebuilt from the
    subject id instead, which is sound because `s3_key` is
    `receipts/{user_id}/{uuid4}` -- the prefix is a complete index of one
    user's objects, with no bookkeeping that could drift out of step.

    The trailing slash is load-bearing, and `list_prefix` matches literally, so
    `receipts/{a}/` cannot reach `receipts/{a}b/`. Deleting one account's
    images is not recoverable by re-running anything.

    Delete before marking paid -- the same ordering as the signals half, for
    the same reason. A crash between the two leaves the debt outstanding and
    the sweep runs again, and deleting an absent blob is free; the opposite
    order marks the obligation paid and leaves the objects.
    """
    from app.core import erasure
    from app.core.erasure import ErasureKind
    from app.workers.storage import build_object_store

    store = build_object_store(get_settings())

    total = 0
    for row in await erasure.pending(primary, kind=ErasureKind.BLOBS):
        try:
            for key in await store.list_prefix(f"receipts/{row.subject_id}/"):
                await store.delete(key)
                total += 1
            await erasure.complete(primary, row.id)
            await primary.commit()
        except Exception as exc:
            await primary.rollback()
            logger.warning(
                "blob erasure failed; debt left outstanding",
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
