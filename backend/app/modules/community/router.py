"""Community reports over HTTP.

Reading is open; writing needs an account. A signed-out visitor lands on a map
and should be able to see what is on it -- a map with pins they cannot inspect
teaches nothing about the product.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep, OptionalUserDep
from app.modules.community.models import StoreReport, Vote
from app.modules.community.schemas import (
    CommentCreate,
    CommentOut,
    ReportCreate,
    ReportOut,
    TrustOut,
    VerificationIn,
)
from app.modules.community.service import CommunityService

router = APIRouter(prefix="/community", tags=["community"])

SessionDep = Annotated[AsyncSession, Depends(get_db)]


async def _voted_targets(
    session: AsyncSession, user_id: uuid.UUID | None, target_type: str, ids: list[uuid.UUID]
) -> set[uuid.UUID]:
    """Which of these the requesting user has already upvoted.

    One query for the whole page rather than one per row: a store detail screen
    renders a report and its comments together, and N+1 here would be the
    slowest thing on it.
    """
    if user_id is None or not ids:
        return set()
    from sqlalchemy import select

    rows = await session.execute(
        select(Vote.target_id).where(
            Vote.user_id == user_id,
            Vote.target_type == target_type,
            Vote.target_id.in_(ids),
        )
    )
    return set(rows.scalars().all())


async def _store_names(session: AsyncSession, store_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """One query for the page, not one per row."""
    if not store_ids:
        return {}
    from sqlalchemy import select

    from app.modules.pricegraph.models import Store

    rows = await session.execute(select(Store.id, Store.name).where(Store.id.in_(store_ids)))
    return {row.id: row.name for row in rows}


async def _verified_by(
    session: AsyncSession, user_id: uuid.UUID | None, report_ids: list[uuid.UUID]
) -> dict[uuid.UUID, bool]:
    """Which of these the caller has already confirmed or disputed, and how.

    `None` for a report they have not voted on, which is what lets the UI show
    an unanswered question differently from an answered one.
    """
    if user_id is None or not report_ids:
        return {}
    from sqlalchemy import select

    from app.modules.community.models import ReportVerification

    rows = await session.execute(
        select(ReportVerification.report_id, ReportVerification.agrees).where(
            ReportVerification.user_id == user_id,
            ReportVerification.report_id.in_(report_ids),
        )
    )
    return {row.report_id: row.agrees for row in rows}


def _to_report(
    report: StoreReport,
    *,
    user_id: uuid.UUID | None,
    voted: set[uuid.UUID],
    store_name: str | None = None,
    comments: list[CommentOut] | None = None,
    verified_by_me: bool | None = None,
) -> ReportOut:
    return ReportOut(
        id=report.id,
        store_id=report.store_id,
        store_name=store_name,
        kind=report.kind,
        item_text=report.item_text,
        price=format(report.price, "f") if report.price is not None else None,
        currency=report.currency,
        note=report.note,
        status=report.status,
        agree_count=report.agree_count,
        dispute_count=report.dispute_count,
        upvote_count=report.upvote_count,
        promoted=report.promoted_observation_id is not None,
        created_at=report.created_at,
        mine=user_id is not None and report.user_id == user_id,
        voted=report.id in voted,
        verified_by_me=verified_by_me,
        comments=comments or [],
    )


@router.get("/reports", response_model=list[ReportOut], summary="Reports for a store")
async def list_reports(
    session: SessionDep,
    user: OptionalUserDep,
    store_id: Annotated[uuid.UUID | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[ReportOut]:
    """Readable signed out, and more useful signed in.

    `mine` and `voted` were hardcoded false here, so a signed-in user reading
    the store sheet saw every control in the wrong state -- their own report
    offered them a confirm button, and an upvote they had already cast looked
    uncast. The endpoint stays open; it just resolves the caller when there is
    one.
    """
    service = CommunityService(session)
    reports = list(await service.list_reports(store_id=store_id, limit=limit))

    user_id = user.id if user is not None else None
    voted = await _voted_targets(session, user_id, "report", [r.id for r in reports])
    stores = await _store_names(session, [r.store_id for r in reports])
    verified = await _verified_by(session, user_id, [r.id for r in reports])

    return [
        _to_report(
            r,
            user_id=user_id,
            voted=voted,
            store_name=stores.get(r.store_id),
            verified_by_me=verified.get(r.id),
        )
        for r in reports
    ]


@router.post("/reports", response_model=ReportOut, status_code=201, summary="Add a report")
async def create_report(data: ReportCreate, user: CurrentUserDep, session: SessionDep) -> ReportOut:
    """Say what a shop has, or what it charges.

    This is the path for local stores that print nothing scannable -- which is
    most of them, and the reason the receipt path alone would leave the map
    empty outside chain retail.
    """
    report = await CommunityService(session).create_report(user.id, data)
    return _to_report(report, user_id=user.id, voted=set())


@router.post("/reports/{report_id}/verify", status_code=201, summary="Confirm or dispute a report")
async def verify(
    report_id: uuid.UUID, data: VerificationIn, user: CurrentUserDep, session: SessionDep
) -> dict[str, object]:
    """One verification per person, enforced by a unique constraint.

    Agreement is weighted by the verifier's trust, so three new accounts do not
    outweigh one established contributor -- which is the shape sockpuppeting
    takes.
    """
    verification = await CommunityService(session).verify(user.id, report_id, agrees=data.agrees)
    return {"id": str(verification.id), "agrees": verification.agrees}


@router.delete("/reports/{report_id}", summary="Retract a report")
async def retract_report(
    report_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> dict[str, object]:
    report = await CommunityService(session).retract_report(user.id, report_id)
    return {"id": str(report.id), "status": report.status}


@router.get("/reports/{report_id}/comments", response_model=list[CommentOut], summary="Comments")
async def comments(report_id: uuid.UUID, session: SessionDep) -> list[CommentOut]:
    rows = await CommunityService(session).comments_for(report_id)
    return [CommentOut.model_validate(row) for row in rows]


@router.post(
    "/reports/{report_id}/comments",
    response_model=CommentOut,
    status_code=201,
    summary="Add a comment",
)
async def add_comment(
    report_id: uuid.UUID, data: CommentCreate, user: CurrentUserDep, session: SessionDep
) -> CommentOut:
    comment = await CommunityService(session).comment(user.id, report_id, data)
    out = CommentOut.model_validate(comment)
    out.mine = True
    return out


@router.delete("/comments/{comment_id}", summary="Retract a comment")
async def retract_comment(
    comment_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> dict[str, object]:
    comment = await CommunityService(session).retract_comment(user.id, comment_id)
    return {"id": str(comment.id), "retracted": True}


@router.post("/{target_type}/{target_id}/vote", summary="Upvote, or take it back")
async def vote(
    target_type: str, target_id: uuid.UUID, user: CurrentUserDep, session: SessionDep
) -> dict[str, object]:
    """A toggle, because that is what the button does.

    The *author* is paid for a received upvote, never the voter: paying the
    voter would make clicking the cheapest way to farm, and there is no
    verification step on a click to gate it behind.
    """
    added = await CommunityService(session).vote(
        user.id, target_type=target_type, target_id=target_id
    )
    return {"upvoted": added}


@router.get("/me/trust", response_model=TrustOut, summary="My standing")
async def my_trust(user: CurrentUserDep, session: SessionDep) -> TrustOut:
    """The score, and the reasons for it.

    Published for the same reason `GET /health-score/rubric` is: a user whose
    contribution was not promoted can read exactly what would change that,
    rather than being told they are not trusted enough.
    """
    return await CommunityService(session).trust(user.id)


@router.get("/me/reports", response_model=list[ReportOut], summary="Reports I have made")
async def my_reports(user: CurrentUserDep, session: SessionDep) -> list[ReportOut]:
    service = CommunityService(session)
    reports = list(await service.my_reports(user.id))
    voted = await _voted_targets(session, user.id, "report", [r.id for r in reports])
    return [_to_report(r, user_id=user.id, voted=voted) for r in reports]


@router.get("/me/comments", response_model=list[CommentOut], summary="Comments I have made")
async def my_comments(user: CurrentUserDep, session: SessionDep) -> list[CommentOut]:
    rows = list(await CommunityService(session).my_comments(user.id))
    voted = await _voted_targets(session, user.id, "comment", [r.id for r in rows])
    out = []
    for row in rows:
        comment = CommentOut.model_validate(row)
        comment.mine = True
        comment.voted = row.id in voted
        out.append(comment)
    return out


@router.get("/trust/rubric", summary="How trust is calculated")
async def rubric() -> dict[str, object]:
    from app.modules.community import trust as rules

    return {
        "rubric_version": rules.RUBRIC_VERSION,
        "starting_trust": format(rules.STARTING_TRUST, "f"),
        "max_trust": format(rules.MAX_TRUST, "f"),
        "promotion_threshold": format(rules.PROMOTION_THRESHOLD, "f"),
        "weights": {
            "verified_contribution": format(rules.PROMOTION_WEIGHT, "f"),
            "confirmation": format(rules.AGREEMENT_WEIGHT, "f"),
            "disputed_report": format(-rules.DISPUTE_PENALTY, "f"),
            "retracted_contribution": format(-rules.RETRACTION_PENALTY, "f"),
        },
        "notes": [
            "An author's own trust counts for half towards promotion, so at least "
            "one other person must always agree.",
            "Agreement is weighted by the confirmer's trust, so several new "
            "accounts do not outweigh one established contributor.",
        ],
    }
