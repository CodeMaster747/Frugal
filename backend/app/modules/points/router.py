"""Points and rewards.

Redemption returns **501**, deliberately and visibly. The full shape exists so
that adding fulfilment later is a change to one handler rather than a redesign,
and so a user earning points can see what they are for -- a number that goes up
and buys nothing, with no indication of what it might ever buy, is a counter
rather than a reward.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.modules.points.service import PointsService

router = APIRouter(tags=["points"])

SessionDep = Annotated[AsyncSession, Depends(get_db)]


class PointsEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    reason: str
    points: int
    subject_type: str | None
    note: str | None
    created_at: datetime


class BalanceOut(BaseModel):
    balance: int
    #: Said out loud rather than left to be discovered. Points are earned now
    #: and spendable later; a product that implies otherwise is lying by
    #: omission.
    redeemable: bool = False
    message: str


class RewardOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    title: str
    description: str
    cost_points: int
    is_available: bool


@router.get("/points/balance", response_model=BalanceOut, summary="Points balance")
async def balance(user: CurrentUserDep, session: SessionDep) -> BalanceOut:
    total = await PointsService(session).balance(user.id)
    return BalanceOut(
        balance=total,
        redeemable=False,
        message=(
            "Redemption is not available yet. Points keep accruing and nothing "
            "expires; the catalogue shows what they are intended to buy."
        ),
    )


@router.get("/points/ledger", response_model=list[PointsEntryOut], summary="Points history")
async def ledger(
    user: CurrentUserDep,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[PointsEntryOut]:
    """Every award and every reversal.

    Append-only, so this is the whole history rather than a summary of it --
    including reversals, which a user is entitled to see rather than merely
    noticing their balance dropped.
    """
    entries = await PointsService(session).history(user.id, limit=limit, offset=offset)
    return [PointsEntryOut.model_validate(entry) for entry in entries]


@router.get("/rewards", response_model=list[RewardOut], summary="Rewards catalogue")
async def rewards(session: SessionDep) -> list[RewardOut]:
    return [RewardOut.model_validate(reward) for reward in await PointsService(session).rewards()]


@router.post("/rewards/{reward_id}/redeem", status_code=501, summary="Redeem points")
async def redeem(
    reward_id: uuid.UUID, user: CurrentUserDep, session: SessionDep, response: Response
) -> dict[str, object]:
    """Not implemented, and it says so.

    501 rather than 404 or a silent success: the endpoint exists, the shape is
    settled, and the thing that is missing is fulfilment. A user who tries this
    should learn that, not that they typed the wrong URL.
    """
    del reward_id, user, session
    response.status_code = 501
    return {
        "status": "not_yet_available",
        "message": (
            "Redemption is not available yet. Your points are safe, nothing "
            "expires, and this endpoint will start working without you having "
            "to do anything."
        ),
    }
