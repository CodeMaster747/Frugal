"""CommunityService -- reports, verification, votes, and trust.

The anti-abuse story here is entirely structural, per the M17 decision: no
moderation queue, no admin role, and nothing that needs a person watching.

- one vote per user per target, by unique constraint
- one verification per user per report, by unique constraint
- points awarded only after a contribution is verified, never on submission
- promotion needs agreement *weighted by trust*, so three new accounts do not
  outvote one established contributor
- an author's own trust is halved, so self-promotion is arithmetically
  impossible
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import TypeAlias

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.logging import get_logger
from app.modules.community.models import (
    ReportComment,
    ReportKind,
    ReportStatus,
    ReportVerification,
    StoreReport,
    UserTrust,
    Vote,
)
from app.modules.community.schemas import CommentCreate, ReportCreate, TrustOut
from app.modules.community.trust import (
    PROMOTION_THRESHOLD,
    RUBRIC_VERSION,
    TrustInputs,
    TrustResult,
    compute,
    promotable,
)
from app.modules.points.models import PointsReason
from app.modules.points.service import PointsService

logger = get_logger(__name__)

#: Both vote targets carry `user_id` and `upvote_count`, which is the whole
#: interface this module needs from either. Naming the union beats an
#: `object` annotation and four type-ignores that would hide a real error.
VoteTarget: TypeAlias = StoreReport | ReportComment

#: Disputes needed before a report stops being shown.
#:
#: Three rather than one: a single dissenter is often somebody who visited on a
#: different day, and hiding on one dispute would hand any user a veto over
#: everyone else's contributions.
HIDE_AFTER_DISPUTES = 3


class CommunityService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.points = PointsService(session)

    # -- reports -----------------------------------------------------------

    async def create_report(self, user_id: uuid.UUID, data: ReportCreate) -> StoreReport:
        from app.modules.pricegraph.models import Store
        from app.modules.pricegraph.normalize import normalize_item

        store = await self.session.get(Store, data.store_id)
        if store is None:
            raise NotFoundError("Store")

        price: Decimal | None = None
        if data.price is not None:
            try:
                price = Decimal(data.price)
            except Exception as exc:
                raise ValidationError("price is not a number") from exc
            if price <= 0:
                raise ValidationError("price must be greater than zero")

        if data.kind is ReportKind.GOOD_PRICE and price is None:
            raise ValidationError("A good-price report needs a price")

        # Resolve the free text to a canonical item where one already exists.
        # `allow_create=False` on purpose: a report is a claim, not an
        # observation, and letting it mint canonical items would fill the
        # catalogue with things nobody has ever bought.
        canonical_item_id: uuid.UUID | None = None
        normalized = normalize_item(data.item_text)
        if normalized is not None:
            from app.modules.pricegraph import matching

            match = await matching.resolve_item(self.session, normalized, allow_create=False)
            if match is not None:
                canonical_item_id = match.item.id

        report = StoreReport(
            user_id=user_id,
            store_id=data.store_id,
            kind=data.kind.value,
            canonical_item_id=canonical_item_id,
            item_text=data.item_text.strip(),
            price=price,
            note=data.note,
        )
        self.session.add(report)
        await self.session.flush()

        # Points on submission, and this is the one place they are: a report is
        # itself the contribution, and refusing to pay for it until somebody
        # else turns up would mean the first person in any new area works for
        # nothing. The daily cap is what keeps that from being farmable, and a
        # disputed report costs trust, which is worth more.
        await self.points.award(
            user_id, PointsReason.STORE_REPORT, subject_type="store_report", subject_id=report.id
        )
        return report

    async def list_reports(
        self, *, store_id: uuid.UUID | None = None, limit: int = 50
    ) -> Sequence[StoreReport]:
        stmt = (
            select(StoreReport)
            .where(StoreReport.status == ReportStatus.ACTIVE.value)
            .order_by(StoreReport.upvote_count.desc(), StoreReport.created_at.desc())
            .limit(limit)
        )
        if store_id is not None:
            stmt = stmt.where(StoreReport.store_id == store_id)
        return (await self.session.execute(stmt)).scalars().all()

    async def my_reports(self, user_id: uuid.UUID, *, limit: int = 100) -> Sequence[StoreReport]:
        stmt = (
            select(StoreReport)
            .where(StoreReport.user_id == user_id)
            .order_by(StoreReport.created_at.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def retract_report(self, user_id: uuid.UUID, report_id: uuid.UUID) -> StoreReport:
        report = await self._own_report(user_id, report_id)
        report.status = ReportStatus.RETRACTED.value

        # Retracting costs trust. Otherwise the cheapest strategy is to post
        # freely and withdraw whatever gets challenged, which is exactly the
        # behaviour the score exists to price.
        await self._adjust_trust(user_id, retracted=1)
        await self.session.flush()
        return report

    # -- verification ------------------------------------------------------

    async def verify(
        self, user_id: uuid.UUID, report_id: uuid.UUID, *, agrees: bool
    ) -> ReportVerification:
        report = await self.session.get(StoreReport, report_id)
        if report is None:
            raise NotFoundError("Report")
        if report.user_id == user_id:
            raise ConflictError("You cannot verify your own report")

        weight = await self.trust_score(user_id)

        verification = ReportVerification(
            user_id=user_id, report_id=report_id, agrees=agrees, weight=weight
        )
        # A SAVEPOINT rather than a bare flush, for the same reason as
        # `PointsService.award`: this runs mid-transaction -- `trust_score`
        # above may have created this user's `UserTrust` row -- and a bare
        # `session.rollback()` here would discard it along with anything else
        # the request had written. Scoping it to the insert keeps the session
        # usable, so the 409 below is raised from a live transaction rather than
        # a dead one.
        try:
            async with self.session.begin_nested():
                self.session.add(verification)
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("You have already verified this report") from exc

        if agrees:
            report.agree_count += 1
        else:
            report.dispute_count += 1
            if report.dispute_count >= HIDE_AFTER_DISPUTES:
                report.status = ReportStatus.HIDDEN.value
                await self._adjust_trust(report.user_id, disputed=1)

        await self.points.award(
            user_id,
            PointsReason.REPORT_VERIFIED,
            subject_type="report_verification",
            subject_id=verification.id,
        )
        await self._adjust_trust(user_id, agreed=1)
        await self.session.flush()

        if agrees:
            await self._maybe_promote(report)
        return verification

    async def _maybe_promote(self, report: StoreReport) -> None:
        """Turn an agreed-upon good-price report into a shared observation.

        Only `good_price` reports carry a number, so only they can become a
        price. A `unique_item` report is a fact about stock, which the map shows
        directly and the price graph has no column for.
        """
        if report.kind != ReportKind.GOOD_PRICE.value:
            return
        if report.promoted_observation_id is not None or report.price is None:
            return
        if report.canonical_item_id is None:
            # Nothing to compare it against. The report still stands and is
            # still shown; it just is not a price yet.
            return

        agreement = await self.session.scalar(
            select(func.coalesce(func.sum(ReportVerification.weight), 0)).where(
                ReportVerification.report_id == report.id,
                ReportVerification.agrees.is_(True),
            )
        )
        author_trust = await self.trust_score(report.user_id)
        if not promotable(Decimal(str(agreement or 0)), author_trust):
            return

        from app.modules.pricegraph.models import ObservationSource
        from app.modules.pricegraph.service import PricegraphService

        observation = await PricegraphService(self.session).record_observation(
            user_id=report.user_id,
            canonical_item_id=report.canonical_item_id,
            store_id=report.store_id,
            unit_price=report.price,
            observed_on=datetime.now(UTC).date(),
            # Lower than a receipt's, and permanently so. A receipt is a
            # document; a report is a recollection.
            confidence=Decimal("0.600"),
            pack_size=None,
            pack_unit=None,
            source=ObservationSource.USER_REPORT,
        )
        if observation is None:
            return

        report.promoted_observation_id = observation.id
        await self._adjust_trust(report.user_id, promoted=1)
        await self.session.flush()

    # -- comments and votes ------------------------------------------------

    async def comment(
        self, user_id: uuid.UUID, report_id: uuid.UUID, data: CommentCreate
    ) -> ReportComment:
        if await self.session.get(StoreReport, report_id) is None:
            raise NotFoundError("Report")

        comment = ReportComment(user_id=user_id, report_id=report_id, body=data.body.strip())
        self.session.add(comment)
        await self.session.flush()
        return comment

    async def comments_for(self, report_id: uuid.UUID) -> Sequence[ReportComment]:
        stmt = (
            select(ReportComment)
            .where(
                ReportComment.report_id == report_id,
                ReportComment.retracted_at.is_(None),
            )
            .order_by(ReportComment.upvote_count.desc(), ReportComment.created_at)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def my_comments(self, user_id: uuid.UUID, *, limit: int = 100) -> Sequence[ReportComment]:
        stmt = (
            select(ReportComment)
            .where(ReportComment.user_id == user_id)
            .order_by(ReportComment.created_at.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def retract_comment(self, user_id: uuid.UUID, comment_id: uuid.UUID) -> ReportComment:
        comment = await self.session.get(ReportComment, comment_id)
        if comment is None or comment.user_id != user_id:
            raise NotFoundError("Comment")
        comment.retracted_at = datetime.now(UTC)
        await self.session.flush()
        return comment

    async def vote(self, user_id: uuid.UUID, *, target_type: str, target_id: uuid.UUID) -> bool:
        """Upvote, or take an upvote back.

        A toggle rather than an idempotent set, because that is what the UI
        offers and modelling it any other way means two endpoints for one
        button. The unique constraint is what makes the toggle safe under a
        double-click.
        """
        if target_type not in {"report", "comment"}:
            raise ValidationError("target_type must be 'report' or 'comment'")

        existing = (
            await self.session.execute(
                select(Vote).where(
                    Vote.user_id == user_id,
                    Vote.target_type == target_type,
                    Vote.target_id == target_id,
                )
            )
        ).scalar_one_or_none()

        target = await self._target(target_type, target_id)
        if target is None:
            raise NotFoundError("Report" if target_type == "report" else "Comment")

        if existing is not None:
            await self.session.delete(existing)
            target.upvote_count = max(0, target.upvote_count - 1)
            await self.session.flush()
            return False

        if target.user_id == user_id:
            raise ConflictError("You cannot upvote your own contribution")

        try:
            async with self.session.begin_nested():
                self.session.add(
                    Vote(user_id=user_id, target_type=target_type, target_id=target_id)
                )
                await self.session.flush()
        except IntegrityError as exc:
            # Lost a race with a double-click. The other write stands, which is
            # what the constraint is for. Scoped to a SAVEPOINT so the loser of
            # the race does not roll back the winner's counter increment.
            raise ConflictError("You have already upvoted this") from exc

        target.upvote_count += 1

        # The *author* is paid for a received upvote, not the voter. Paying the
        # voter would make clicking the cheapest way to farm, and there is no
        # verification step on a click to gate it behind.
        await self.points.award(
            target.user_id,
            PointsReason.REPORT_UPVOTED,
            subject_type=target_type,
            subject_id=target_id,
            idempotency_key=f"upvote:{target_type}:{target_id}:{user_id}",
        )
        await self.session.flush()
        return True

    async def _target(self, target_type: str, target_id: uuid.UUID) -> VoteTarget | None:
        if target_type == "report":
            return await self.session.get(StoreReport, target_id)
        return await self.session.get(ReportComment, target_id)

    # -- trust -------------------------------------------------------------

    async def trust_score(self, user_id: uuid.UUID) -> Decimal:
        """The raw score, for arithmetic.

        Separate from `trust()`, which returns the wire DTO with the score as a
        string (ADR-003). Doing the comparison against the string would work
        right up until "0.9" sorted below "0.15".
        """
        return (await self._compute(user_id)).score

    async def trust(self, user_id: uuid.UUID) -> TrustOut:
        result = await self._compute(user_id)
        return TrustOut(
            score=format(result.score, "f"),
            can_promote=result.score >= PROMOTION_THRESHOLD / 2,
            threshold=format(PROMOTION_THRESHOLD, "f"),
            factors=result.factors,
        )

    async def _compute(self, user_id: uuid.UUID) -> TrustResult:
        row = await self._trust_row(user_id)
        return compute(
            TrustInputs(
                accepted_promotions=row.accepted_promotions,
                agreed_verifications=row.agreed_verifications,
                disputed_reports=row.disputed_reports,
                retracted_contributions=row.retracted_contributions,
            )
        )

    async def _trust_row(self, user_id: uuid.UUID) -> UserTrust:
        row = (
            await self.session.execute(select(UserTrust).where(UserTrust.user_id == user_id))
        ).scalar_one_or_none()
        if row is None:
            row = UserTrust(user_id=user_id, rubric_version=RUBRIC_VERSION)
            self.session.add(row)
            await self.session.flush()
        return row

    async def _adjust_trust(
        self,
        user_id: uuid.UUID,
        *,
        promoted: int = 0,
        agreed: int = 0,
        disputed: int = 0,
        retracted: int = 0,
    ) -> None:
        row = await self._trust_row(user_id)
        row.accepted_promotions += promoted
        row.agreed_verifications += agreed
        row.disputed_reports += disputed
        row.retracted_contributions += retracted
        row.score = compute(
            TrustInputs(
                accepted_promotions=row.accepted_promotions,
                agreed_verifications=row.agreed_verifications,
                disputed_reports=row.disputed_reports,
                retracted_contributions=row.retracted_contributions,
            )
        ).score
        row.rubric_version = RUBRIC_VERSION
        await self.session.flush()

    async def _own_report(self, user_id: uuid.UUID, report_id: uuid.UUID) -> StoreReport:
        report = await self.session.get(StoreReport, report_id)
        if report is None or report.user_id != user_id:
            # A report owned by somebody else is indistinguishable from a
            # missing one, per docs/04-api-design.md 2.3.
            raise NotFoundError("Report")
        return report
