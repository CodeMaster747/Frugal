"""Market intelligence HTTP layer."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field, field_serializer
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.errors import NotFoundError
from app.modules.market import offers as market_offers
from app.modules.market import reliability as reliability_module
from app.modules.market.models import WishlistItem
from app.modules.market.service import MarketService, summarise_item

router = APIRouter(prefix="/market", tags=["market"])


def get_service(db: Annotated[AsyncSession, Depends(get_db)]) -> MarketService:
    return MarketService(db)


ServiceDep = Annotated[MarketService, Depends(get_service)]


def _money(value: Decimal | None) -> str | None:
    return None if value is None else format(value, "f")


class WishlistItemOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    name: str
    category: str
    price_when_added: Decimal
    current_price: Decimal | None
    change_since_added: Decimal | None
    lowest_recorded: Decimal | None
    lowest_recorded_on: date | None
    is_at_lowest: bool
    target_price: Decimal | None
    notes: str | None
    purchased_on: date | None
    created_at: datetime

    @field_serializer(
        "price_when_added",
        "current_price",
        "change_since_added",
        "lowest_recorded",
        "target_price",
        when_used="json",
    )
    def _decimals(self, value: Decimal | None) -> str | None:
        return _money(value)


class AddToWishlistIn(BaseModel):
    external_id: Annotated[str, Field(min_length=1, max_length=160)]
    target_price: Annotated[Decimal, Field(gt=0)] | None = None
    notes: Annotated[str, Field(max_length=500)] | None = None


@router.post("/wishlist", response_model=WishlistItemOut, status_code=status.HTTP_201_CREATED)
async def add_to_wishlist(
    payload: AddToWishlistIn, current: CurrentUserDep, service: ServiceDep
) -> WishlistItemOut:
    """Track a product's price.

    Backfills 90 days of history on the way in, so the chart is useful
    immediately rather than after three months of waiting.
    """
    item = await service.add_to_wishlist(
        current.id,
        external_id=payload.external_id,
        target_price=payload.target_price,
        notes=payload.notes,
    )
    return await _summarise(service, item)


async def _summarise(service: MarketService, item: WishlistItem) -> WishlistItemOut:
    from app.modules.market.models import Product

    product = await service.session.get(Product, item.product_id)
    assert product is not None
    offers = await service.current_offers(item.product_id)
    current_price = min((Decimal(o.price) for o in offers), default=None)
    lowest = await service.lowest_recorded(item.product_id)

    return WishlistItemOut.model_validate(
        summarise_item(item, product, current=current_price, lowest=lowest)
    )


@router.get("/wishlist", response_model=list[WishlistItemOut])
async def list_wishlist(current: CurrentUserDep, service: ServiceDep) -> list[WishlistItemOut]:
    return [await _summarise(service, item) for item in await service.wishlist(current.id)]


@router.delete("/wishlist/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_from_wishlist(
    item_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep
) -> None:
    if not await service.remove_from_wishlist(current.id, item_id):
        raise NotFoundError("Wishlist item")


class HistoryPointOut(BaseModel):
    date: date
    price: Decimal
    sellers: int

    @field_serializer("price", when_used="json")
    def _price(self, value: Decimal) -> str:
        return format(value, "f")


class SignalOut(BaseModel):
    key: str
    name: str
    value: str
    weight: Decimal
    contribution: Decimal
    detail: str

    @field_serializer("weight", "contribution", when_used="json")
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


class ReliabilityOut(BaseModel):
    score: Decimal
    band: str
    confidence: str
    rubric_version: str
    signals: list[SignalOut]
    caveats: list[str]

    @field_serializer("score", when_used="json")
    def _score(self, value: Decimal) -> str:
        return format(value, "f")


class OfferOut(BaseModel):
    seller_name: str
    price: Decimal
    in_stock: bool
    return_window_days: int | None
    warranty_months: int | None
    fulfillment_type: str | None
    observed_at: datetime
    reliability: ReliabilityOut

    @field_serializer("price", when_used="json")
    def _price(self, value: Decimal) -> str:
        return format(value, "f")


class ProductDetailOut(BaseModel):
    product_id: uuid.UUID
    name: str
    category: str
    current_best: Decimal | None
    lowest_recorded: Decimal | None
    lowest_recorded_on: date | None
    market_median: Decimal | None
    history: list[HistoryPointOut]
    offers: list[OfferOut]

    @field_serializer("current_best", "lowest_recorded", "market_median", when_used="json")
    def _decimals(self, value: Decimal | None) -> str | None:
        return _money(value)


@router.get("/products/{product_id}", response_model=ProductDetailOut)
async def product_detail(
    product_id: uuid.UUID,
    current: CurrentUserDep,
    service: ServiceDep,
    days: Annotated[int, Query(ge=7, le=365)] = 90,
) -> ProductDetailOut:
    """Price history and current offers, each offer scored for reliability."""
    del current
    from app.modules.market.models import Product

    product = await service.session.get(Product, product_id)
    if product is None:
        raise NotFoundError("Product")

    offers = await service.current_offers(product_id)
    scored: list[OfferOut] = []
    for offer in offers:
        result = await service.reliability_for(offer)
        scored.append(
            OfferOut(
                seller_name=offer.seller_name,
                price=Decimal(offer.price),
                in_stock=offer.in_stock,
                return_window_days=offer.return_window_days,
                warranty_months=offer.warranty_months,
                fulfillment_type=offer.fulfillment_type,
                observed_at=offer.observed_at,
                reliability=ReliabilityOut(
                    score=result.score,
                    band=result.band,
                    confidence=result.confidence.value,
                    rubric_version=result.rubric_version,
                    signals=[
                        SignalOut(
                            key=s.key.value,
                            name=s.name,
                            value=s.value,
                            weight=s.weight,
                            contribution=s.contribution,
                            detail=s.detail,
                        )
                        for s in result.signals
                    ],
                    caveats=list(result.caveats),
                ),
            )
        )

    lowest = await service.lowest_recorded(product_id)
    return ProductDetailOut(
        product_id=product.id,
        name=product.canonical_name,
        category=product.category,
        current_best=min((o.price for o in scored), default=None),
        lowest_recorded=lowest[0] if lowest else None,
        lowest_recorded_on=lowest[1] if lowest else None,
        market_median=await service.market_median(product_id),
        history=[
            HistoryPointOut(**point) for point in await service.history(product_id, days=days)
        ],
        offers=scored,
    )


class PriceAlertOut(BaseModel):
    id: uuid.UUID
    product_id: uuid.UUID
    previous_price: Decimal
    new_price: Decimal
    drop_fraction: Decimal
    seller_name: str
    is_lowest_recorded: bool
    read_at: datetime | None
    created_at: datetime

    @field_serializer("previous_price", "new_price", "drop_fraction", when_used="json")
    def _decimals(self, value: Decimal) -> str:
        return format(value, "f")


@router.get("/alerts", response_model=list[PriceAlertOut])
async def list_alerts(current: CurrentUserDep, service: ServiceDep) -> list[PriceAlertOut]:
    return [
        PriceAlertOut.model_validate(a, from_attributes=True)
        for a in await service.alerts(current.id)
    ]


@router.post("/alerts/check", response_model=list[PriceAlertOut])
async def check_alerts(current: CurrentUserDep, service: ServiceDep) -> list[PriceAlertOut]:
    """Look for drops now.

    The scheduler does this daily; the endpoint exists so the loop is
    demonstrable without waiting for Beat, and so a user who just added
    something can see it work.
    """
    alerts = await service.check_drops(current.id)
    return [PriceAlertOut.model_validate(a, from_attributes=True) for a in alerts]


@router.post("/alerts/{alert_id}/read", status_code=status.HTTP_204_NO_CONTENT)
async def mark_read(alert_id: uuid.UUID, current: CurrentUserDep, service: ServiceDep) -> None:
    if not await service.mark_alert_read(current.id, alert_id):
        raise NotFoundError("Alert")


@router.get("/reliability/rubric")
async def published_rubric(current: CurrentUserDep) -> dict[str, Any]:
    """The reliability rubric, published in-product (FR-9.2).

    Carries `what_this_is_not` as prominently as `what_this_is`. A score about a
    named commercial seller needs to be explicit about the claim it is *not*
    making.
    """
    del current
    return reliability_module.published()


class RetailOfferOut(BaseModel):
    title: str
    price: Decimal
    currency: str
    seller: str
    #: Empty for simulated offers, which have nowhere real to link to.
    link: str
    as_of: datetime
    provider: str
    seller_rating: Decimal | None = None
    rating_count: int | None = None
    delivery_note: str | None = None
    thumbnail_url: str | None = None
    reliability: ReliabilityOut

    @field_serializer("price", when_used="json")
    def _price(self, value: Decimal) -> str:
        return format(value, "f")

    @field_serializer("seller_rating", when_used="json")
    def _rating(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")


class OfferSearchOut(BaseModel):
    """Offers for a query, ordered cheapest first.

    Ordering by price asserts nothing -- it is arithmetic over a column, so
    there is no score to decompose and ADR-002's validator has nothing to fire
    on. What *is* a judgement, each offer's reliability, carries the published
    rubric's full factor list exactly as the wishlist does.
    """

    query: str
    offers: list[RetailOfferOut]
    #: `serpapi`, `simulated`, or `cache`. Named so the UI can say where a
    #: price came from rather than implying they are all live listings.
    source: str
    #: `ok` or `paused`.
    service_status: str
    caveats: list[str]
    saving: Decimal | None = None
    saving_percent: Decimal | None = None

    @field_serializer("saving", "saving_percent", when_used="json")
    def _decimals(self, value: Decimal | None) -> str | None:
        return None if value is None else format(value, "f")


@router.get("/offers", response_model=OfferSearchOut)
async def search_offers(
    current: CurrentUserDep,
    db: Annotated[AsyncSession, Depends(get_db)],
    q: Annotated[str, Query(min_length=2, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=20)] = 10,
    compare_to: Decimal | None = None,
) -> OfferSearchOut:
    """Where a product is cheapest right now.

    Behind an explicit user action, and never called from a scheduled sweep.
    At a couple of hundred searches a month for the whole deployment, an
    automatic call is the entire allowance.

    Degrades rather than failing: when the allowance is spent this returns
    simulated prices with `service_status: "paused"` and a caveat naming the
    contact, because a paused price comparison must not take down the
    affordability answer that was the point.
    """
    result = await market_offers.find_offers(
        db, query=q, user_id=current.id, limit=limit, compare_to=compare_to
    )
    await db.commit()

    return OfferSearchOut(
        query=q,
        offers=[
            RetailOfferOut(
                title=s.offer.title,
                price=s.offer.price,
                currency=s.offer.currency,
                seller=s.offer.seller,
                link=s.offer.link,
                as_of=s.offer.as_of,
                provider=s.offer.provider,
                seller_rating=s.offer.seller_rating,
                rating_count=s.offer.rating_count,
                delivery_note=s.offer.delivery_note,
                thumbnail_url=s.offer.thumbnail_url,
                reliability=ReliabilityOut(
                    score=s.reliability.score,
                    band=s.reliability.band,
                    confidence=s.reliability.confidence.value,
                    rubric_version=s.reliability.rubric_version,
                    signals=[
                        SignalOut(
                            key=sig.key.value,
                            name=sig.name,
                            value=sig.value,
                            weight=sig.weight,
                            contribution=sig.contribution,
                            detail=sig.detail,
                        )
                        for sig in s.reliability.signals
                    ],
                    caveats=list(s.reliability.caveats),
                ),
            )
            for s in result.offers
        ],
        source=result.source,
        service_status=result.service_status,
        caveats=result.caveats,
        saving=result.saving,
        saving_percent=result.saving_percent,
    )
