"""Crawling allowlisted hosts, in the worker (ADR-012).

Never in the request path. Two independent reasons, and either would decide it:
a fetch takes seconds and a page render does not have seconds; and a crawl that
blocks an HTTP worker on a 1 GB instance is one slow host away from taking the
API down.

**Async, despite ADR-006.** That ADR makes workers synchronous *because OCR and
Prophet are CPU-bound* -- forcing async there would add a thread pool and buy no
concurrency. A crawler is the opposite: I/O over several hosts with enforced
spacing between requests. This follows `tasks/market.py` line for line, which
is the established shape for exactly this case.

**Bounded rather than isolated.** Routing to a `scrape` queue buys nothing while
one worker at concurrency 1 consumes every queue, and a second worker does not
fit in the memory budget. So the task is time-limited and per-run capped, which
is honest, free, and actually bounds the damage.
"""

from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.queue import celery_app

logger = get_logger(__name__)

#: Queries re-crawled by the nightly sweep. The same argument `refresh_prices`
#: makes: the value of a price observation is entirely in someone caring about
#: it.
REFRESH_WINDOW_DAYS = 7
REFRESH_BATCH = 25


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.scraping.scrape_offers",
    bind=True,
    acks_late=True,
    # Against the global 540/600. A crawl blocks receipt OCR on this worker, so
    # it gets a much shorter leash than the tasks that own it.
    soft_time_limit=120,
    time_limit=150,
)
def scrape_offers(self: object, query: str) -> dict[str, Any]:
    del self
    import asyncio

    if not get_settings().scraper_enabled_hosts:
        return {"status": "disabled"}

    try:
        return asyncio.run(_run(query))
    except Exception as exc:
        # The panel falls back to the rest of the chain; a failed crawl is
        # never a failed search.
        logger.warning("scrape failed", exc_info=exc)
        return {"status": "failed", "reason": type(exc).__name__}


async def _run(query: str) -> dict[str, Any]:
    from app.adapters.offers import get_scraper
    from app.core.database import worker_async_session
    from app.core.redis import reset_redis
    from app.modules.market.scrape_cache import write_cached

    # Both the engine and the Redis client cache against the loop that made
    # them, and `asyncio.run` gives this task a new one.
    await reset_redis()

    async with worker_async_session() as session:
        offers = await get_scraper(session).find_offers(query, limit=20)  # type: ignore[attr-defined]
        if offers:
            await write_cached(session, query, offers)
        await session.commit()

    return {"status": "ok", "query": query, "offers": len(offers)}


@celery_app.task(  # type: ignore[untyped-decorator]
    name="app.workers.tasks.scraping.refresh_scrapes", bind=True, soft_time_limit=300
)
def refresh_scrapes(self: object) -> dict[str, Any]:
    """Re-crawl queries somebody actually searched recently."""
    del self
    import asyncio

    if not get_settings().scraper_enabled_hosts:
        return {"status": "disabled"}

    try:
        return asyncio.run(_refresh())
    except Exception as exc:
        logger.warning("scrape refresh failed", exc_info=exc)
        return {"status": "failed", "reason": type(exc).__name__}


async def _refresh() -> dict[str, Any]:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import select

    from app.adapters.offers import get_scraper
    from app.core.database import worker_async_session
    from app.core.redis import reset_redis
    from app.modules.market.scrape_cache import ScrapedOfferSnapshot, write_cached

    await reset_redis()
    cutoff = datetime.now(UTC) - timedelta(days=REFRESH_WINDOW_DAYS)

    refreshed = 0
    async with worker_async_session() as session:
        rows = (
            (
                await session.execute(
                    select(ScrapedOfferSnapshot)
                    .where(ScrapedOfferSnapshot.fetched_at > cutoff)
                    .order_by(ScrapedOfferSnapshot.fetched_at)
                    .limit(REFRESH_BATCH)
                )
            )
            .scalars()
            .all()
        )

        search = get_scraper(session)
        for row in rows:
            offers = await search.find_offers(row.query_text, limit=20)  # type: ignore[attr-defined]
            if offers:
                await write_cached(session, row.query_text, offers)
                refreshed += 1
        await session.commit()

    return {"status": "ok", "refreshed": refreshed, "considered": len(rows)}
