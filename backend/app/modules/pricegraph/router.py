"""The price graph over HTTP.

Two audiences, and the split matters:

- **anyone** can read the map and store-level prices. They are aggregates over
  a k-anonymity floor and name nobody, which is what makes a public landing map
  possible at all.
- **only the owner** can read or retract their own contributions, through the
  tenant-scoped `receipt_promotions` join that the shared observation
  deliberately does not carry.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.errors import ValidationError
from app.modules.pricegraph.models import CanonicalItem, PriceObservation, Store
from app.modules.pricegraph.schemas import (
    CheaperElsewhereOut,
    ContributionOut,
    ItemPriceOut,
    MapPinOut,
    MergeCandidateOut,
    StoreOut,
    StorePinIn,
)
from app.modules.pricegraph.service import PricegraphService

router = APIRouter(prefix="/pricegraph", tags=["pricegraph"])

SessionDep = Annotated[AsyncSession, Depends(get_db)]

#: The largest viewport served in one request.
#:
#: Not a performance limit -- an honesty one. A query spanning the country
#: returns pins too sparse to mean anything and invites the client to render a
#: map that implies coverage the graph does not have.
MAX_SPAN_DEGREES = Decimal("2.0")


def _decimal(name: str, raw: str) -> Decimal:
    try:
        return Decimal(raw)
    except InvalidOperation as exc:
        raise ValidationError(f"{name} is not a number") from exc


@router.get("/map", response_model=list[MapPinOut], summary="Stores in a viewport")
async def map_pins(
    session: SessionDep,
    min_lat: Annotated[str, Query()],
    max_lat: Annotated[str, Query()],
    min_lon: Annotated[str, Query()],
    max_lon: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[MapPinOut]:
    """Pins for a map viewport.

    Unauthenticated on purpose: the landing screen is a map, and a signed-out
    visitor seeing an empty one would learn nothing about what the product is.
    Everything here is an aggregate above the contributor floor.

    Coordinates are strings on the wire for the same reason amounts are
    (ADR-003): a float latitude compared by equality is the same class of bug as
    a float rupee.
    """
    lo_lat, hi_lat = _decimal("min_lat", min_lat), _decimal("max_lat", max_lat)
    lo_lon, hi_lon = _decimal("min_lon", min_lon), _decimal("max_lon", max_lon)

    if lo_lat > hi_lat or lo_lon > hi_lon:
        raise ValidationError("The bounding box is inverted")
    if (hi_lat - lo_lat) > MAX_SPAN_DEGREES or (hi_lon - lo_lon) > MAX_SPAN_DEGREES:
        raise ValidationError(
            f"Zoom in: a viewport wider than {MAX_SPAN_DEGREES} degrees returns pins "
            "too sparse to be useful."
        )

    return await PricegraphService(session).map_pins(
        min_lat=lo_lat, max_lat=hi_lat, min_lon=lo_lon, max_lon=hi_lon, limit=limit
    )


@router.get("/items/{item_id}/prices", response_model=list[ItemPriceOut], summary="Where to buy it")
async def item_prices(
    item_id: uuid.UUID,
    session: SessionDep,
    pincode: Annotated[str | None, Query(max_length=6)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[ItemPriceOut]:
    return await PricegraphService(session).prices_for_item(item_id, pincode=pincode, limit=limit)


@router.get(
    "/items/{item_id}/cheaper",
    response_model=CheaperElsewhereOut | None,
    summary="Cheaper than what you paid",
)
async def cheaper(
    item_id: uuid.UUID,
    session: SessionDep,
    paid: Annotated[str, Query()],
    pincode: Annotated[str | None, Query(max_length=6)] = None,
) -> CheaperElsewhereOut | None:
    """`null` when nowhere is cheaper, which is a real and common answer."""
    return await PricegraphService(session).cheaper_elsewhere(
        item_id, paid=_decimal("paid", paid), pincode=pincode
    )


@router.get("/items/search", response_model=list[dict[str, object]], summary="Find an item")
async def search_items(
    session: SessionDep,
    q: Annotated[str, Query(min_length=2, max_length=120)],
    limit: Annotated[int, Query(ge=1, le=25)] = 10,
) -> list[dict[str, object]]:
    """Trigram search over canonical items.

    Provisional items are excluded: a first sighting is one person's OCR, and
    offering it as a search result would let a single mis-read become the entry
    everyone else then matches against.
    """
    from sqlalchemy import func, select

    similarity = func.similarity(CanonicalItem.normalized_key, q.lower())
    stmt = (
        select(CanonicalItem, similarity)
        .where(
            CanonicalItem.is_provisional.is_(False),
            CanonicalItem.normalized_key.op("%")(q.lower()),
        )
        .order_by(similarity.desc())
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": str(item.id),
            "name": item.canonical_name,
            "brand": item.brand,
            "pack": f"{item.pack_size}{item.pack_unit}" if item.pack_size else None,
            "observations": item.observation_count,
        }
        for item, _ in rows
    ]


@router.get("/stores/{store_id}", response_model=StoreOut, summary="One store")
async def store_detail(store_id: uuid.UUID, session: SessionDep) -> StoreOut:
    from app.core.errors import NotFoundError

    store = await session.get(Store, store_id)
    if store is None:
        raise NotFoundError("Store")
    return StoreOut(
        id=store.id,
        name=store.name,
        address_line=store.address_line,
        pincode=store.pincode,
        city=store.city,
        latitude=format(store.latitude, "f") if store.latitude is not None else None,
        longitude=format(store.longitude, "f") if store.longitude is not None else None,
        source=store.source,
        confirmed=store.confirmed_at is not None,
    )


@router.post("/stores/pin", response_model=StoreOut, status_code=201, summary="Add a store")
async def pin_store(data: StorePinIn, user: CurrentUserDep, session: SessionDep) -> StoreOut:
    """Place a shop the import has never heard of.

    For small local stores that is most of them. Geocoding a printed Indian
    address resolves poorly, and the person standing in the shop knows exactly
    where it is -- which is why this is a pin and not a lookup.
    """
    store = await PricegraphService(session).pin_store(user.id, data)
    return StoreOut(
        id=store.id,
        name=store.name,
        pincode=store.pincode,
        latitude=format(store.latitude, "f") if store.latitude is not None else None,
        longitude=format(store.longitude, "f") if store.longitude is not None else None,
        source=store.source,
        confirmed=False,
    )


@router.post("/stores/{store_id}/confirm", summary="Confirm a shop is really there")
async def confirm_store(
    store_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> dict[str, object]:
    """Vouch for a pinned shop.

    Two distinct confirmers clear its "not yet confirmed" banner and pay
    whoever placed the pin. The confirmer is not paid: clicking "yes" would
    otherwise be the cheapest contribution in the system, and there is nothing
    behind a click to verify.
    """
    store = await PricegraphService(session).confirm_store(user.id, store_id)
    return {"store_id": str(store.id), "confirmed": store.confirmed_at is not None}


@router.get(
    "/merge-candidates",
    response_model=list[MergeCandidateOut],
    summary="Item pairs that might be one product",
)
async def merge_candidates(
    user: CurrentUserDep, session: SessionDep, limit: Annotated[int, Query(ge=1, le=50)] = 20
) -> list[MergeCandidateOut]:
    """Layer 3 of item matching: the pairs the machine will not decide alone.

    Trigram similarity is not transitive, so auto-merge may only ever attach an
    alias to an existing canonical. Anything canonical-to-canonical waits here.
    Until now it waited forever -- rows went in and nothing read them.
    """
    return await PricegraphService(session).merge_candidates(user.id, limit=limit)


@router.post("/merge-candidates/{candidate_id}/confirm", summary="These are one product")
async def confirm_merge(
    candidate_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> dict[str, object]:
    merged = await PricegraphService(session).resolve_merge(user.id, candidate_id, merge=True)
    return {"id": str(candidate_id), "merged": merged}


@router.post("/merge-candidates/{candidate_id}/reject", summary="These are different products")
async def reject_merge(
    candidate_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> dict[str, object]:
    """Rejecting is as valuable as confirming, and it is permanent.

    `_propose_merge` skips a pair that already has a row, so a rejected pair is
    never proposed again -- which is what stops the queue re-asking the same
    question every time somebody buys the item.
    """
    await PricegraphService(session).resolve_merge(user.id, candidate_id, merge=False)
    return {"id": str(candidate_id), "merged": False}


@router.get("/me/contributions", response_model=list[ContributionOut], summary="What I contributed")
async def my_contributions(user: CurrentUserDep, session: SessionDep) -> list[ContributionOut]:
    """The tenant-scoped view of a user's own contributions.

    The shared observations carry none of this. `receipt_promotions` is the only
    place the link exists, and it cascades from `users`.
    """
    service = PricegraphService(session)
    out: list[ContributionOut] = []

    for promotion in await service.my_contributions(user.id):
        if promotion.price_observation_id is None:
            continue
        observation = await session.get(PriceObservation, promotion.price_observation_id)
        if observation is None:
            continue
        item = await session.get(CanonicalItem, observation.canonical_item_id)
        store = await session.get(Store, observation.store_id)
        out.append(
            ContributionOut(
                id=promotion.id,
                receipt_id=promotion.receipt_id,
                item_name=item.canonical_name if item else "Unknown item",
                store_name=store.name if store else "Unknown store",
                unit_price=format(observation.unit_price, "f"),
                observed_on=observation.observed_on,
                points_awarded=promotion.points_awarded,
                retracted=observation.retracted_at is not None,
            )
        )
    return out


@router.delete("/me/contributions", summary="Retract everything I contributed")
async def retract(user: CurrentUserDep, session: SessionDep) -> dict[str, object]:
    """Remove this user's contributions from the shared graph.

    **Retraction is not erasure**, and the difference is worth stating to the
    person using it. Deleting an account *anonymises* these rows -- the price
    survives as a fact about the shop, because deleting it would silently
    degrade what every other user sees and make the graph a function of churn.
    This removes them.
    """
    count = await PricegraphService(session).retract_all(user.id)
    return {
        "retracted": count,
        "message": (
            f"{count} contributions retracted. They no longer appear to anyone. "
            "This is separate from deleting your account, which anonymises "
            "contributions rather than removing them."
        ),
    }
