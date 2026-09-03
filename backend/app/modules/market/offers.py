"""Finding where a product is cheapest, and saying how sure we are.

Two decisions shape this file, and both are about staying on the right side of
a line the codebase already drew.

**Offers are ordered by price, and that ordering asserts nothing.** Ascending
price is arithmetic over a column -- no score, no verdict -- so ADR-002's
validator has nothing to fire on. "₹4,200 (5.2%) below the price you entered"
is a subtraction, not a judgement.

**Each offer's reliability comes from the rubric that already exists.**
`market/reliability.py` takes exactly the fields a shopping search returns, its
weights already sum to 1.00, and it already redistributes missing signals and
caps the band at `Confidence.LOW` when most are absent -- which is precisely
this case. Writing a composite "deal score" instead would mean inventing
weights *and* publishing a judgement about a named commercial seller, walking
straight back into the defamation exposure that file spends four paragraphs
explaining was deliberately removed.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ports import RetailOffer
from app.core.config import get_settings
from app.core.logging import get_logger
from app.modules.market.models import OfferSnapshot
from app.modules.market.reliability import Reliability, score_offer
from app.modules.market.sources import FALLBACK, offer_chain

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ScoredOffer:
    offer: RetailOffer
    #: The same `Reliability` the wishlist renders, so `ReliabilityOut` and the
    #: frontend's panel serve both without a second shape to keep in step.
    reliability: Reliability


@dataclass(frozen=True, slots=True)
class OfferResult:
    """What a search produced, and how far to trust it.

    `source` and `caveats` are not decoration. A panel showing simulated prices
    that does not say so is a claim about named retailers that happens to be
    false, and one showing stale cached prices without a timestamp implies a
    currency it does not have.
    """

    offers: list[ScoredOffer]
    source: str
    service_status: str
    caveats: list[str]
    cached: bool = False
    #: Set when the caller supplied a price to compare against.
    saving: Decimal | None = None
    saving_percent: Decimal | None = None


def query_hash(query: str) -> str:
    return hashlib.sha256(query.strip().lower().encode()).hexdigest()


async def find_offers(
    session: AsyncSession,
    *,
    query: str,
    user_id: uuid.UUID | None = None,
    limit: int = 10,
    compare_to: Decimal | None = None,
) -> OfferResult:
    """Live offers for a query, degrading rather than failing.

    Cache first -- a hit costs no quota and no network, and repeated searches
    for the same product are the common case. Then a loop over the chain in
    `market/sources.py`, cheapest source first, stopping at the first one with
    anything to say. Then the simulator, with the reason said out loud.

    A loop over a tuple, not a ladder of ifs. Two things the previous shape got
    wrong went with it, and this module's own comments recorded both as found
    the hard way:

    - the provider status was re-derived from the quota ledger *after* an empty
      result, asking it a question the adapter already knew the answer to.
    - the "not real listings" caveat was inferred from a provider string, so a
      cached simulated result -- which reports `source="cache"` -- silently
      dropped it. Each source now attaches its own caveats, so the disclaimer
      travels with the offers instead of being re-derived downstream.

    The advisor's verdict never depends on any of this. A paused price
    comparison must not take down the affordability answer that was the point.
    """
    settings = get_settings()
    digest = query_hash(query)

    cached = await _from_cache(session, digest)
    if cached is not None:
        return _result(cached, source="cache", status="ok", cached=True, compare_to=compare_to)

    caveats: list[str] = []
    status = "ok"

    for source in offer_chain(settings):
        outcome = await source.fetch(session, query, limit=limit, user_id=user_id)
        caveats.extend(outcome.caveats)
        if outcome.status != "ok":
            status = outcome.status

        if outcome.offers:
            if outcome.cacheable:
                await _to_cache(session, digest, query, outcome.offers, provider=outcome.source)
            return _result(
                outcome.offers,
                source=outcome.source,
                status=outcome.status,
                compare_to=compare_to,
                extra_caveats=caveats,
            )

    # Nothing real came back. The simulator is not a peer of the sources above
    # -- it produces nothing true -- so it runs here rather than in the chain,
    # and it is told why it was reached, so the message can distinguish "no
    # results" from "the allowance is spent". Only one of those is something
    # the user can act on.
    fallback = await FALLBACK(paused=status == "paused").fetch(
        session, query, limit=limit, user_id=user_id
    )
    return _result(
        fallback.offers,
        source=fallback.source,
        status=fallback.status,
        compare_to=compare_to,
        extra_caveats=caveats + list(fallback.caveats),
    )


async def _from_cache(session: AsyncSession, digest: str) -> list[RetailOffer] | None:
    """A previous result, if it is still fresh.

    Postgres rather than Redis, against the usual preference, because an
    evicted entry here costs *quota* -- and quota is the one piece of state in
    this system whose loss is not regenerable.
    """
    row = (
        await session.execute(
            select(OfferSnapshot).where(
                OfferSnapshot.query_hash == digest,
                OfferSnapshot.expires_at > datetime.now(UTC),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return [_from_dict(o) for o in row.offers]


async def _to_cache(
    session: AsyncSession,
    digest: str,
    query: str,
    offers: list[RetailOffer],
    *,
    provider: str,
) -> None:
    settings = get_settings()
    now = datetime.now(UTC)
    existing = (
        await session.execute(select(OfferSnapshot).where(OfferSnapshot.query_hash == digest))
    ).scalar_one_or_none()

    payload = [_to_dict(o) for o in offers]
    expires = now + timedelta(hours=settings.offer_cache_ttl_hours)

    if existing is not None:
        existing.offers = payload
        existing.provider = provider
        existing.fetched_at = now
        existing.expires_at = expires
    else:
        session.add(
            OfferSnapshot(
                query_hash=digest,
                query_text=query[:255],
                provider=provider,
                offers=payload,
                fetched_at=now,
                expires_at=expires,
            )
        )
    await session.flush()


def _result(
    offers: list[RetailOffer],
    *,
    source: str,
    status: str,
    cached: bool = False,
    compare_to: Decimal | None = None,
    extra_caveats: list[str] | None = None,
) -> OfferResult:
    from app.adapters.offers.simulated import median_price

    median = median_price(offers)
    scored = [
        ScoredOffer(
            offer=offer,
            reliability=score_offer(
                seller_rating=offer.seller_rating,
                rating_count=offer.rating_count,
                # A shopping search publishes none of these. The rubric
                # excludes a missing signal and redistributes its weight rather
                # than scoring it zero -- silence is not a failing grade.
                return_window_days=None,
                warranty_months=None,
                fulfillment_type=None,
                price=offer.price,
                market_median=median,
            ),
        )
        for offer in offers
    ]

    caveats = list(extra_caveats or [])
    # No longer inferred from a provider string in the general case: each
    # source attaches its own caveats in `market/sources.py`, so the disclaimer
    # travels with the offers. That inference is what dropped it for cached
    # simulated results -- leaving invented prices on screen labelled as though
    # they were real listings about named retailers, which `m13-offers.spec.ts`
    # caught the first time two tests searched the same product.
    #
    # A cache hit is the one case no source can speak for, because by then the
    # source is gone. Hence this check, and only this one.
    if cached and offers and all(o.provider == "simulated" for o in offers):
        caveats.append("These are simulated prices for illustration, not real listings.")
    if cached and offers:
        caveats.append(f"Prices as last seen on {offers[0].as_of.date().isoformat()}.")

    saving = percent = None
    if compare_to is not None and scored:
        best = min(o.offer.price for o in scored)
        if best < compare_to:
            saving = compare_to - best
            percent = (saving / compare_to * 100).quantize(Decimal("0.1"))

    return OfferResult(
        offers=scored,
        source=source,
        service_status=status,
        caveats=caveats,
        cached=cached,
        saving=saving,
        saving_percent=percent,
    )


def _to_dict(offer: RetailOffer) -> dict[str, object]:
    return {
        "title": offer.title,
        "price": format(offer.price, "f"),
        "seller": offer.seller,
        "link": offer.link,
        "as_of": offer.as_of.isoformat(),
        "provider": offer.provider,
        "currency": offer.currency,
        "condition": offer.condition,
        "seller_rating": format(offer.seller_rating, "f") if offer.seller_rating else None,
        "rating_count": offer.rating_count,
        "delivery_note": offer.delivery_note,
        "thumbnail_url": offer.thumbnail_url,
    }


def _from_dict(raw: dict[str, object]) -> RetailOffer:
    rating = raw.get("seller_rating")
    return RetailOffer(
        title=str(raw["title"]),
        price=Decimal(str(raw["price"])),
        seller=str(raw["seller"]),
        link=str(raw.get("link") or ""),
        as_of=datetime.fromisoformat(str(raw["as_of"])),
        provider=str(raw["provider"]),
        currency=str(raw.get("currency") or "INR"),
        condition=str(raw.get("condition") or "new"),
        seller_rating=Decimal(str(rating)) if rating else None,
        rating_count=int(str(raw["rating_count"])) if raw.get("rating_count") else None,
        delivery_note=str(raw["delivery_note"]) if raw.get("delivery_note") else None,
        thumbnail_url=str(raw["thumbnail_url"]) if raw.get("thumbnail_url") else None,
    )
