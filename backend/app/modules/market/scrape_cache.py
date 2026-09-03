"""What the scraper found, and how a miss asks for more (ADR-012).

Keyed on `(host_rules_version, query_hash)` rather than the query alone: when a
host rule or an extractor changes, every result it produced becomes suspect,
and a version in the key retires them all without a migration.
"""

from __future__ import annotations

import contextlib
import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, Index, String, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.adapters.ports import RetailOffer
from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.models import Base, TimestampMixin, UUIDMixin

logger = get_logger(__name__)

#: How long one query's enqueue is suppressed. Ten users searching the same
#: thing enqueue one crawl.
#:
#: Redis is right here, unlike the host budget: losing this costs one duplicate
#: job, and a duplicate job is caught by the budget anyway.
ENQUEUE_TTL_SECONDS = 300


class ScrapedOfferSnapshot(UUIDMixin, TimestampMixin, Base):
    """Global, like `offer_snapshots`. A price is not owned by whoever asked."""

    __tablename__ = "scraped_offer_snapshots"

    rules_version: Mapped[str] = mapped_column(String(20), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    query_text: Mapped[str] = mapped_column(String(255), nullable=False)
    offers: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        Index(
            "uq_scraped_offer_snapshots_version_query",
            "rules_version",
            "query_hash",
            unique=True,
        ),
        Index("ix_scraped_offer_snapshots_expires_at", "expires_at"),
    )


def query_hash(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode()).hexdigest()


async def read_cached(session: AsyncSession, query: str, *, limit: int = 10) -> list[RetailOffer]:
    from app.adapters.offers import rules_version

    row = (
        await session.execute(
            select(ScrapedOfferSnapshot).where(
                ScrapedOfferSnapshot.rules_version == rules_version(),
                ScrapedOfferSnapshot.query_hash == query_hash(query),
                ScrapedOfferSnapshot.expires_at > datetime.now(UTC),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return []
    return [_from_dict(entry) for entry in row.offers][:limit]


async def write_cached(session: AsyncSession, query: str, offers: list[RetailOffer]) -> None:
    from app.adapters.offers import rules_version

    settings = get_settings()
    now = datetime.now(UTC)
    digest = query_hash(query)

    row = (
        await session.execute(
            select(ScrapedOfferSnapshot).where(
                ScrapedOfferSnapshot.rules_version == rules_version(),
                ScrapedOfferSnapshot.query_hash == digest,
            )
        )
    ).scalar_one_or_none()

    payload = [_to_dict(offer) for offer in offers]
    expires = now + timedelta(hours=settings.scrape_cache_ttl_hours)

    if row is None:
        session.add(
            ScrapedOfferSnapshot(
                rules_version=rules_version(),
                query_hash=digest,
                query_text=query[:255],
                offers=payload,
                fetched_at=now,
                expires_at=expires,
            )
        )
    else:
        row.offers = payload
        row.fetched_at = now
        row.expires_at = expires
    await session.flush()


async def request_crawl(query: str) -> bool:
    """Enqueue a crawl, at most once per query per five minutes.

    Returns whether a job was dispatched. Failures are swallowed: a warming
    miss that could not enqueue is still a warming miss, and the panel says the
    same thing either way.
    """
    from app.core.queue import SCRAPE_OFFERS, dispatch
    from app.core.redis import get_redis

    key = f"scrape:{query_hash(query)}"

    try:
        redis = get_redis()
        if not await redis.set(key, "1", ex=ENQUEUE_TTL_SECONDS, nx=True):
            # Somebody already asked for this query inside the window.
            return False
    except Exception as exc:
        # Redis is unavailable. Dispatch anyway rather than refusing: the
        # de-duplication is an optimisation, and losing it costs a duplicate
        # job that the host budget will refuse anyway.
        logger.info("could not de-duplicate the crawl request", exc_info=exc)

    try:
        dispatch(SCRAPE_OFFERS, query=query)
    except Exception as exc:
        # The suppression key is released on failure. Setting it *before* the
        # dispatch -- which is how this was written first -- meant a failed
        # dispatch suppressed every retry for the next five minutes, so a
        # transient broker outage turned into a query that silently never
        # warmed.
        logger.info("could not enqueue a crawl", exc_info=exc)
        with contextlib.suppress(Exception):
            await get_redis().delete(key)
        return False

    return True


def _to_dict(offer: RetailOffer) -> dict[str, object]:
    return {
        "title": offer.title,
        # A string, never a float: ADR-003 applies inside JSONB, and
        # `test_no_float_money` checks payloads as well as columns.
        "price": format(offer.price, "f"),
        "seller": offer.seller,
        "link": offer.link,
        "as_of": offer.as_of.isoformat(),
        "provider": offer.provider,
        "currency": offer.currency,
        "delivery_note": offer.delivery_note,
        "thumbnail_url": offer.thumbnail_url,
    }


def _from_dict(raw: dict[str, object]) -> RetailOffer:
    from decimal import Decimal

    return RetailOffer(
        title=str(raw["title"]),
        price=Decimal(str(raw["price"])),
        seller=str(raw["seller"]),
        link=str(raw.get("link") or ""),
        as_of=datetime.fromisoformat(str(raw["as_of"])),
        provider=str(raw["provider"]),
        currency=str(raw.get("currency") or "INR"),
        delivery_note=str(raw["delivery_note"]) if raw.get("delivery_note") else None,
        thumbnail_url=str(raw["thumbnail_url"]) if raw.get("thumbnail_url") else None,
    )
