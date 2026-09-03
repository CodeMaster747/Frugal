"""Promoting a committed receipt into the shared price graph.

Runs in the worker, dispatched by name when a receipt is committed. The commit
response must never depend on it: a user recording their own purchase is not
waiting on a decision about whether other people get to see the price.

**The promotion rule.** All eight must hold, and the fourth is the strongest
filter and costs nothing:

1. the receipt is COMMITTED -- a human confirmed merchant, date and total
2. the line's confidence clears `price_promotion_confidence_threshold` (0.80,
   above OCR's 0.75: the bar to publish is higher than the bar to show you your
   own data)
3. a unit price exists, or is derivable from total / quantity
4. the line items sum to the receipt total within tolerance
5. the store resolved -- by GSTIN, or name-trigram plus PIN, or a confirmed pin
6. the uploader's trust clears the floor
7. this receipt has not already been contributed by anyone
8. the user is under their daily award cap

A receipt failing 4 has *none* of its lines promoted, not just the misread one:
if the arithmetic does not work, the read was bad, and which line was wrong is
exactly what we do not know.
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.logging import get_logger
from app.core.queue import celery_app

logger = get_logger(__name__)


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.pricegraph.promote_receipt", bind=True, acks_late=True
)
def promote_receipt(self: object, receipt_id: str, user_id: str) -> dict[str, Any]:
    """Consider one committed receipt for the shared graph."""
    del self
    import asyncio

    try:
        return asyncio.run(_promote(uuid.UUID(receipt_id), uuid.UUID(user_id)))
    except Exception as exc:
        # A failed promotion must never surface to the user: their transaction
        # is already recorded, and this is an optional extra on top of it.
        logger.warning("promotion failed", exc_info=exc)
        return {"status": "failed", "reason": type(exc).__name__}


async def _promote(receipt_id: uuid.UUID, user_id: uuid.UUID) -> dict[str, Any]:
    from decimal import Decimal

    from app.core.config import get_settings
    from app.core.database import worker_async_session
    from app.core.redis import reset_redis

    # Workers may import a module's `service.py` -- that is the permitted
    # direction, and it is how the trust floor is read without `pricegraph`
    # ever importing `community.models`.
    from app.modules.community.service import CommunityService
    from app.modules.points.models import PointsReason
    from app.modules.points.service import PointsService
    from app.modules.pricegraph import matching
    from app.modules.pricegraph.normalize import normalize_item
    from app.modules.pricegraph.service import PricegraphService
    from app.modules.receipts.service import ReceiptService
    from app.workers.storage import build_object_store

    await reset_redis()

    async with worker_async_session() as session:
        # Promotion touches no blob, but `ReceiptService` owns the object store
        # for the paths that do, and weakening its constructor to make this call
        # site tidier would weaken it for those too.
        receipts = ReceiptService(session, build_object_store(get_settings()))
        graph = PricegraphService(session)
        points = PointsService(session)

        detail = await receipts.promotion_input(user_id, receipt_id)
        if detail is None:
            return {"status": "skipped", "reason": "not_committed"}

        if detail.already_contributed:
            # Word this carefully wherever it reaches a user: "already counted",
            # never "someone else uploaded this". The second phrasing is both an
            # accusation and a leak.
            return {"status": "skipped", "reason": "already_counted"}

        if not graph.lines_add_up(detail.line_total, detail.total):
            return {"status": "skipped", "reason": "lines_do_not_sum"}

        store = await matching.resolve_store(
            session,
            gstin=detail.gstin,
            merchant_normalized=detail.merchant_normalized,
            pincode=detail.pincode,
        )
        if store is None:
            return {"status": "skipped", "reason": "store_unresolved"}

        # Condition 6 of the promotion rule (ADR-013). It was documented and
        # never implemented: `min_contributor_trust` was read nowhere in the
        # codebase, so a user whose trust had been driven to zero by disputes
        # and retractions published to the shared graph exactly as freely as
        # anyone else.
        # Remember the registration for next time. `resolve_store` prefers a
        # GSTIN over a fuzzy name match and had nothing to prefer, because
        # nothing ever wrote one.
        await graph.claim_gstin(store, detail.gstin)

        trust = await CommunityService(session).trust_score(user_id)
        if trust < graph.settings.min_contributor_trust:
            return {"status": "skipped", "reason": "contributor_untrusted"}

        threshold = graph.settings.price_promotion_confidence_threshold
        promoted = skipped = 0

        for line in detail.lines:
            if line.confidence < threshold:
                skipped += 1
                continue

            # Receipts state a unit price only rarely, so this is mostly a
            # division. An *absent* quantity is treated as one unit -- the
            # common case, and a documented assumption rather than a silent
            # one. It is robust enough because a published price is a median
            # over at least `min_contributors_for_display` people, so one
            # shopper who bought two is an outlier the median absorbs.
            #
            # Requiring a stated quantity instead would skip almost every line
            # on almost every receipt, which is how this rule was written the
            # first time and why nothing reached the graph.
            unit_price = line.unit_price
            if unit_price is None and line.total_price is not None:
                unit_price = (line.total_price / (line.quantity or Decimal(1))).quantize(
                    Decimal("0.01")
                )
            if unit_price is None or unit_price <= 0:
                skipped += 1
                continue

            normalized = normalize_item(line.description or "")
            if normalized is None:
                skipped += 1
                continue

            match = await matching.resolve_item(session, normalized)
            if match is None:
                skipped += 1
                continue

            # Condition 8, and it must be reserved *before* the write.
            #
            # The cap previously lived only inside `PointsService.award`, which
            # meant a user past their allowance still published prices to
            # everybody and merely stopped being paid for them -- the opposite
            # of what a volume control is for.
            #
            # ADR-008's no-refund rule applies: a reservation spent on a line
            # that is then refused by the one-per-day constraint is not
            # returned. Over-counting is the safe direction for an abuse
            # control.
            if not await graph.reserve_contribution(user_id):
                skipped += 1
                continue

            observation = await graph.record_observation(
                user_id=user_id,
                canonical_item_id=match.item.id,
                store_id=store.id,
                unit_price=unit_price,
                observed_on=detail.observed_on,
                confidence=line.confidence,
                pack_size=normalized.pack_size,
                pack_unit=normalized.pack_unit,
            )
            if observation is None:
                skipped += 1
                continue

            # Distinct contributors, not observations.
            #
            # Counting observations let one person flip an item to public alone,
            # by promoting it from two receipts at two shops -- which is exactly
            # what the comment below says must not happen.
            await graph.refresh_item_reach(match.item)

            entry = await points.award(
                user_id,
                PointsReason.PRICE_PROMOTED,
                subject_type="price_observation",
                subject_id=observation.id,
            )
            await receipts.record_promotion(
                user_id=user_id,
                receipt_id=receipt_id,
                line_item_id=line.id,
                observation_id=observation.id,
                points_awarded=entry.points if entry is not None else 0,
            )
            promoted += 1

        if promoted:
            await points.award(
                user_id,
                PointsReason.RECEIPT_VERIFIED,
                subject_type="receipt",
                subject_id=receipt_id,
            )

        await session.commit()

    return {"status": "ok", "promoted": promoted, "skipped": skipped}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.pricegraph.retry_promotions", bind=True, soft_time_limit=120
)
def retry_promotions(self: object, user_id: str) -> dict[str, Any]:
    """Reconsider one user's receipts after they put a shop on the map.

    A receipt whose shop could not be resolved was refused once and never looked
    at again, so the obvious sequence -- upload a bill, notice the shop is not on
    the map, pin it -- left that bill permanently unpromoted with nothing to
    tell the user why.

    Scoped to the one user who just pinned something, and to receipts recent
    enough that a price from them is still worth publishing. That bound is what
    makes it safe to run on a worker at concurrency 1.
    """
    del self
    import asyncio

    try:
        return asyncio.run(_retry(uuid.UUID(user_id)))
    except Exception as exc:
        logger.warning("promotion retry failed", exc_info=exc)
        return {"status": "failed", "reason": type(exc).__name__}


#: How far back a retry looks. Beyond this a shelf price is history, and the
#: `FRESH_DAYS` window in the read path would exclude it anyway.
RETRY_WINDOW_DAYS = 30
#: Receipts reconsidered in one run.
RETRY_BATCH = 25


async def _retry(user_id: uuid.UUID) -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta

    from app.core.database import worker_async_session
    from app.core.redis import reset_redis
    from app.modules.receipts.service import ReceiptService

    await reset_redis()
    cutoff = (datetime.now(UTC) - timedelta(days=RETRY_WINDOW_DAYS)).date()

    async with worker_async_session() as session:
        candidates = await ReceiptService.unpromoted_receipt_ids(
            session, user_id, since=cutoff, limit=RETRY_BATCH
        )

    promoted = 0
    for receipt_id in candidates:
        result = await _promote(receipt_id, user_id)
        promoted += int(result.get("promoted") or 0)

    return {"status": "ok", "considered": len(candidates), "promoted": promoted}
