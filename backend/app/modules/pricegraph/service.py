"""PricegraphService -- the only entry into the shared price graph.

The promotion rule is the important part of this file. Everything else is
reads.

**What crosses the line, and what never does.** Receipts are PII and the docs
say so. These stay with the uploader, permanently: the image and its key,
`raw_text`, every bounding box, the payment method, the receipt total, tax and
subtotal, the receipt id, the time of day, and -- most importantly -- **the
basket**. Item co-occurrence on one receipt is a fingerprint that re-identifies
people with no name attached, which is why a shared observation records one item
and not the shopping trip it came from.

These cross: canonical item, store, unit price, currency, pack size and unit,
the *date* (never a timestamp), the source, a confidence, and a contributor
hash.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import ColumnElement, Row, and_, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.config import get_settings
from app.core.errors import ConflictError, NotFoundError
from app.core.logging import get_logger
from app.core.queue import RETRY_PROMOTIONS, dispatch
from app.core.quota import QuotaLedger, period_keys
from app.modules.pricegraph.models import (
    CanonicalItem,
    CanonicalItemAlias,
    ItemMergeCandidate,
    MergeStatus,
    ObservationSource,
    PriceObservation,
    ReceiptPromotion,
    Store,
    StoreConfirmation,
    StoreSource,
    UserStorePin,
)
from app.modules.pricegraph.schemas import (
    CheaperElsewhereOut,
    ItemPriceOut,
    MapPinOut,
    MergeCandidateOut,
    StoreOut,
    StorePinIn,
)

logger = get_logger(__name__)

#: The observation window for a "current" price. Older than this and a shelf
#: price is history, not guidance.
FRESH_DAYS = 30

#: Distinct people who must vouch for a pinned shop before its banner clears.
#:
#: Two, not three: a shopfront is a far easier thing to be right about than a
#: price, and a higher bar would leave the map full of permanently provisional
#: pins nobody trusts.
CONFIRMATIONS_REQUIRED = 2

#: Current pepper generation. Bumped only alongside a migration that
#: recomputes or nulls existing hashes -- rotating it casually silently breaks
#: the one-contribution-per-day constraint, which is why the column exists.
PEPPER_VERSION = 1

_CENT = Decimal("0.01")


def contributor_hash(user_id: uuid.UUID, store_id: uuid.UUID, observed_on: date) -> bytes:
    """A per-user, per-store, per-day pseudonym.

    The store and the date are *inside* the HMAC, so the value is not linkable
    across shops or across days: an attacker holding the whole table cannot
    assemble one person's purchase history from it. The pepper lives in Settings
    and never in the database, so a dump alone links nothing.

    Say plainly what this is not: it is **pseudonymisation, not
    anonymisation**. An operator holding the pepper can test "did user X buy
    item Y at store Z on date D". Calling it anonymous in the docs would be
    false. Erasure nulls the hash, and only then is the surviving row genuinely
    anonymous.
    """
    key = get_settings().contribution_pepper_key.encode()
    message = f"{user_id}|{store_id}|{observed_on.isoformat()}".encode()
    return hmac.new(key, message, hashlib.sha256).digest()[:16]


class PricegraphService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.settings = get_settings()

    # -- reads -------------------------------------------------------------

    async def prices_for_item(
        self, canonical_item_id: uuid.UUID, *, pincode: str | None = None, limit: int = 20
    ) -> list[ItemPriceOut]:
        """Store-level prices for one item, above the k-anonymity floor.

        A single observation says "exactly one person shopped here and bought
        this", which is a sentence about a person rather than about a shop. The
        `HAVING` clause is what stops the graph publishing it.

        The uploader always sees their own observation through their own
        tenant-scoped receipt, so the feature is useful on day one with an empty
        graph -- the floor costs nothing they had.
        """
        rows = await self._aggregate(
            where=and_(PriceObservation.canonical_item_id == canonical_item_id),
            pincode=pincode,
            limit=limit,
        )
        item = await self.session.get(CanonicalItem, canonical_item_id)
        name = item.canonical_name if item else "Unknown item"
        return [self._to_price_out(row, name) for row in rows]

    async def cheaper_elsewhere(
        self,
        canonical_item_id: uuid.UUID,
        *,
        paid: Decimal,
        pincode: str | None = None,
    ) -> CheaperElsewhereOut | None:
        """Where this item costs less than what the user paid.

        Ascending price is arithmetic over a column -- no score, no verdict --
        so ADR-002's validator has nothing to fire on, exactly as
        `market/offers.py` argues for its own ordering. The caveats carry the
        honesty: how many people said so, and how long ago.
        """
        prices = await self.prices_for_item(canonical_item_id, pincode=pincode, limit=1)
        if not prices:
            return None

        best = prices[0]
        best_price = Decimal(best.median_price)
        if best_price >= paid:
            return None

        saving = (paid - best_price).quantize(_CENT)
        percent = (saving / paid * 100).quantize(Decimal("0.1"))

        return CheaperElsewhereOut(
            item_name=best.item_name,
            your_price=format(paid, "f"),
            best_price=best.median_price,
            saving=format(saving, "f"),
            saving_percent=format(percent, "f"),
            store=best.store,
            contributors=best.contributors,
            last_seen_on=best.last_seen_on,
            caveats=[
                f"Reported by {best.contributors} shoppers, most recently on "
                f"{best.last_seen_on.isoformat()}.",
                "Shelf prices change. This is what people paid, not a quoted price.",
            ],
        )

    async def map_pins(
        self,
        *,
        min_lat: Decimal,
        max_lat: Decimal,
        min_lon: Decimal,
        max_lon: Decimal,
        limit: int = 200,
    ) -> list[MapPinOut]:
        """Stores inside a bounding box, with what each has to show.

        A bounding box on two indexed NUMERIC columns rather than PostGIS. The
        upgrade path is `cube` + `earthdistance` -- both contrib, both on Neon --
        and neither is needed at this size. A box is also what a map viewport
        actually is, so the extra precision of a radius would be discarded by
        the caller anyway.
        """
        floor = self.settings.min_contributors_for_display
        cutoff = datetime.now(UTC).date().toordinal() - FRESH_DAYS

        stmt = text("""
            SELECT s.id, s.name, s.address_line, s.pincode, s.city,
                   s.latitude, s.longitude, s.source, s.confirmed_at,
                   COALESCE(p.price_count, 0) AS price_count,
                   COALESCE(r.report_count, 0) AS report_count
              FROM stores s
              LEFT JOIN (
                    SELECT store_id, count(*) AS price_count
                      FROM (
                            SELECT store_id, canonical_item_id
                              FROM price_observations
                             WHERE retracted_at IS NULL
                               AND observed_on > :cutoff
                             GROUP BY store_id, canonical_item_id
                            HAVING count(DISTINCT contributor_hash) >= :floor
                           ) q
                     GROUP BY store_id
                   ) p ON p.store_id = s.id
              -- Counted here, rather than the hardcoded 0 this shipped with. A
              -- shop with no verified prices but three community reports is
              -- worth a pin, and the map had no way to say so.
              LEFT JOIN (
                    SELECT store_id, count(*) AS report_count
                      FROM store_reports
                     WHERE status = 'active'
                     GROUP BY store_id
                   ) r ON r.store_id = s.id
             WHERE s.latitude BETWEEN :min_lat AND :max_lat
               AND s.longitude BETWEEN :min_lon AND :max_lon
             ORDER BY COALESCE(p.price_count, 0) DESC,
                      COALESCE(r.report_count, 0) DESC, s.name
             LIMIT :limit
        """)
        rows = (
            await self.session.execute(
                stmt,
                {
                    "cutoff": date.fromordinal(cutoff),
                    "floor": floor,
                    "min_lat": min_lat,
                    "max_lat": max_lat,
                    "min_lon": min_lon,
                    "max_lon": max_lon,
                    "limit": limit,
                },
            )
        ).all()

        return [
            MapPinOut(
                store=StoreOut(
                    id=row.id,
                    name=row.name,
                    address_line=row.address_line,
                    pincode=row.pincode,
                    city=row.city,
                    latitude=format(row.latitude, "f") if row.latitude is not None else None,
                    longitude=format(row.longitude, "f") if row.longitude is not None else None,
                    source=row.source,
                    confirmed=row.confirmed_at is not None,
                ),
                price_count=row.price_count,
                report_count=row.report_count,
                headline=_headline(row.price_count, row.report_count),
            )
            for row in rows
        ]

    async def _aggregate(
        self,
        *,
        where: ColumnElement[bool],
        pincode: str | None,
        limit: int,
    ) -> Sequence[Row[Any]]:
        """Store-level aggregates above the k-anonymity floor.

        The store is joined rather than fetched per row: a price the caller
        cannot locate is not useful, and N+1 lookups for the store name would be
        the slowest part of a map render.
        """
        floor = self.settings.min_contributors_for_display
        cutoff = date.fromordinal(datetime.now(UTC).date().toordinal() - FRESH_DAYS)

        stmt = (
            select(
                PriceObservation.canonical_item_id,
                Store.id.label("store_id"),
                Store.name.label("store_name"),
                Store.address_line,
                Store.pincode.label("store_pincode"),
                Store.city,
                Store.latitude,
                Store.longitude,
                Store.source.label("store_source"),
                Store.confirmed_at,
                func.percentile_cont(0.5)
                .within_group(PriceObservation.unit_price)
                .label("median_price"),
                func.min(PriceObservation.unit_price).label("min_price"),
                func.max(PriceObservation.unit_price).label("max_price"),
                func.count(func.distinct(PriceObservation.contributor_hash)).label("contributors"),
                func.max(PriceObservation.observed_on).label("last_seen_on"),
                func.max(PriceObservation.currency).label("currency"),
            )
            .join(Store, Store.id == PriceObservation.store_id)
            .where(
                where,
                PriceObservation.retracted_at.is_(None),
                PriceObservation.observed_on > cutoff,
            )
            .group_by(
                PriceObservation.canonical_item_id,
                Store.id,
                Store.name,
                Store.address_line,
                Store.pincode,
                Store.city,
                Store.latitude,
                Store.longitude,
                Store.source,
                Store.confirmed_at,
            )
            .having(func.count(func.distinct(PriceObservation.contributor_hash)) >= floor)
            .order_by(text("median_price ASC"))
            .limit(limit)
        )
        if pincode:
            stmt = stmt.where(Store.pincode == pincode)
        return (await self.session.execute(stmt)).all()

    def _to_price_out(self, row: Row[Any], item_name: str) -> ItemPriceOut:
        return ItemPriceOut(
            canonical_item_id=row.canonical_item_id,
            item_name=item_name,
            store=StoreOut(
                id=row.store_id,
                name=row.store_name,
                address_line=row.address_line,
                pincode=row.store_pincode,
                city=row.city,
                latitude=format(row.latitude, "f") if row.latitude is not None else None,
                longitude=format(row.longitude, "f") if row.longitude is not None else None,
                source=row.store_source,
                confirmed=row.confirmed_at is not None,
            ),
            median_price=format(Decimal(str(row.median_price)).quantize(_CENT), "f"),
            min_price=format(row.min_price, "f"),
            max_price=format(row.max_price, "f"),
            contributors=row.contributors,
            last_seen_on=row.last_seen_on,
            currency=row.currency or "INR",
        )

    # -- store pins --------------------------------------------------------

    async def pin_store(self, user_id: uuid.UUID, data: StorePinIn) -> Store:
        """Place a shop the OSM import has never heard of.

        For small local stores that is most of them, which is why this exists
        rather than relying on geocoding: a printed address in India resolves
        poorly, and the person standing in the shop knows exactly where it is.
        """
        from app.modules.finance.service import normalize_merchant

        latitude = Decimal(data.latitude)
        longitude = Decimal(data.longitude)

        store = Store(
            name=data.name,
            normalized_name=normalize_merchant(data.name) or data.name.lower(),
            pincode=data.pincode,
            latitude=latitude,
            longitude=longitude,
            source=StoreSource.USER_PIN.value,
        )
        self.session.add(store)
        await self.session.flush()

        self.session.add(
            UserStorePin(
                user_id=user_id,
                store_id=store.id,
                name=data.name,
                latitude=latitude,
                longitude=longitude,
                pincode=data.pincode,
                note=data.note,
            )
        )
        await self.session.flush()

        # The obvious sequence is: upload a bill, notice the shop is missing,
        # pin it. Without this the bill stays permanently unpromoted with
        # nothing to say why, because promotion is dispatched once at commit and
        # never reconsidered. Dispatched by name, bounded to this user.
        dispatch(RETRY_PROMOTIONS, user_id=str(user_id))
        return store

    # -- store confirmation ------------------------------------------------

    async def confirm_store(self, user_id: uuid.UUID, store_id: uuid.UUID) -> Store:
        """Vouch that a pinned shop is really where somebody said it is.

        Two distinct confirmers clear the banner. Two rather than one because a
        pin plus its own author's word is one person's claim twice; two rather
        than three because a shopfront is a far easier thing to be right about
        than a price, and a higher bar would leave the map full of permanently
        provisional pins.

        Idempotent by constraint, not by check.
        """
        from app.modules.points.models import PointsReason
        from app.modules.points.service import PointsService

        store = await self.session.get(Store, store_id)
        if store is None:
            raise NotFoundError("Store")

        try:
            async with self.session.begin_nested():
                self.session.add(StoreConfirmation(user_id=user_id, store_id=store_id))
                await self.session.flush()
        except IntegrityError as exc:
            raise ConflictError("You have already confirmed this shop") from exc

        if store.confirmed_at is None:
            confirmations = await self.session.scalar(
                select(func.count()).select_from(
                    select(StoreConfirmation.id)
                    .where(StoreConfirmation.store_id == store_id)
                    .subquery()
                )
            )
            if int(confirmations or 0) >= CONFIRMATIONS_REQUIRED:
                store.confirmed_at = datetime.now(UTC)

                # The pin's author is paid, not the confirmer. Paying the
                # confirmer would make clicking "yes" the cheapest contribution
                # in the system, and there is nothing behind a click to verify.
                pin = (
                    (
                        await self.session.execute(
                            select(UserStorePin).where(
                                UserStorePin.store_id == store_id,
                                UserStorePin.deleted_at.is_(None),
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if pin is not None:
                    await PointsService(self.session).award(
                        pin.user_id,
                        PointsReason.STORE_PIN_CONFIRMED,
                        subject_type="store",
                        subject_id=store_id,
                    )

        await self.session.flush()
        return store

    async def claim_gstin(self, store: Store, gstin: str | None) -> None:
        """Attach a receipt's tax registration to the shop it named.

        `Store.gstin` is `resolve_store`'s strongest evidence and was written by
        nothing, so that branch could never match. A receipt already carries a
        checksum-validated GSTIN; this is where it gets remembered, so the
        *second* receipt from that shop matches on an exact government
        identifier instead of a fuzzy name.

        On conflict the existing claim wins and this does nothing. Two stores
        cannot share a registration, and choosing between them from here would
        be guessing -- name matching is the honest fallback.
        """
        if not gstin or store.gstin is not None:
            return

        taken = await self.session.scalar(select(Store.id).where(Store.gstin == gstin))
        if taken is not None:
            logger.info("GSTIN already claimed by another store", extra={"store_id": str(store.id)})
            return

        store.gstin = gstin
        await self.session.flush()

    # -- item merges -------------------------------------------------------

    async def merge_candidates(
        self, user_id: uuid.UUID, *, limit: int = 20
    ) -> list[MergeCandidateOut]:
        """Pending pairs, most similar first.

        Not tenant-scoped: an item merge is a fact about the catalogue, not
        about the person answering. Anyone signed in may see the queue; the
        trust floor applies to *resolving* one, because a wrong merge is a
        wrong price for everybody.
        """
        del user_id  # reading the queue is open; resolving is not

        left = aliased(CanonicalItem)
        right = aliased(CanonicalItem)
        rows = (
            await self.session.execute(
                select(ItemMergeCandidate, left.canonical_name, right.canonical_name)
                .join(left, left.id == ItemMergeCandidate.left_item_id)
                .join(right, right.id == ItemMergeCandidate.right_item_id)
                .where(ItemMergeCandidate.status == MergeStatus.PENDING.value)
                .order_by(ItemMergeCandidate.similarity.desc())
                .limit(limit)
            )
        ).all()

        return [
            MergeCandidateOut(
                id=candidate.id,
                left_name=left_name,
                right_name=right_name,
                similarity=format(candidate.similarity, "f"),
            )
            for candidate, left_name, right_name in rows
        ]

    async def resolve_merge(
        self, user_id: uuid.UUID, candidate_id: uuid.UUID, *, merge: bool
    ) -> bool:
        """Answer one merge question.

        Confirming writes an alias from the right item's key onto the left, so
        every future sighting of either spelling lands on one product. It does
        **not** rewrite existing observations: those carry their own pack size
        and price, and rewriting history to match a later judgement would make
        the graph unauditable.
        """
        from app.modules.points.models import PointsReason
        from app.modules.points.service import PointsService

        candidate = await self.session.get(ItemMergeCandidate, candidate_id)
        if candidate is None or candidate.status != MergeStatus.PENDING.value:
            raise NotFoundError("Merge candidate")

        candidate.status = (MergeStatus.MERGED if merge else MergeStatus.REJECTED).value
        candidate.resolved_at = datetime.now(UTC)

        if merge:
            right = await self.session.get(CanonicalItem, candidate.right_item_id)
            if right is not None:
                self.session.add(
                    CanonicalItemAlias(
                        canonical_item_id=candidate.left_item_id,
                        alias_key=right.normalized_key,
                        source="user_confirmed",
                    )
                )

        await PointsService(self.session).award(
            user_id,
            PointsReason.ITEM_MERGE_CONFIRMED,
            subject_type="item_merge_candidate",
            subject_id=candidate_id,
        )
        await self.session.flush()
        return merge

    # -- contributions -----------------------------------------------------

    async def my_contributions(
        self, user_id: uuid.UUID, *, limit: int = 100
    ) -> Sequence[ReceiptPromotion]:
        stmt = (
            select(ReceiptPromotion)
            .where(ReceiptPromotion.user_id == user_id)
            .order_by(ReceiptPromotion.created_at.desc())
            .limit(limit)
        )
        return (await self.session.execute(stmt)).scalars().all()

    async def retract_all(self, user_id: uuid.UUID) -> int:
        """Remove everything this user contributed to the shared graph.

        **Retraction is not erasure**, and the difference matters to the person
        exercising it. Erasure anonymises: it nulls the contributor hash and
        leaves the price, because a price at a shop on a day is a fact about the
        shop and deleting it would silently degrade what everyone else sees.
        Retraction says "take my contributions out", and does.

        Two rights, two actions, and the docs say which is which.
        """
        promotions = await self.my_contributions(user_id, limit=10_000)
        observation_ids = [p.price_observation_id for p in promotions if p.price_observation_id]
        if not observation_ids:
            return 0

        now = datetime.now(UTC)
        result = await self.session.execute(
            select(PriceObservation).where(PriceObservation.id.in_(observation_ids))
        )
        count = 0
        for observation in result.scalars():
            if observation.retracted_at is None:
                observation.retracted_at = now
                count += 1
        await self.session.flush()
        return count

    async def anonymise(self, user_id: uuid.UUID) -> int:
        """Sever the link between a deleted account and its observations.

        Called by the erasure sweep. After this the surviving row is genuinely
        anonymous: `receipt_promotions` is already gone by cascade, and the
        contributor hash -- the last value derived from the user id -- is null.

        It releases that user's one-per-day uniqueness slot for the (item,
        store, date) triple. Acceptable: they are gone.
        """
        promotions = await self.my_contributions(user_id, limit=10_000)
        observation_ids = [p.price_observation_id for p in promotions if p.price_observation_id]
        if not observation_ids:
            return 0

        result = await self.session.execute(
            select(PriceObservation).where(PriceObservation.id.in_(observation_ids))
        )
        count = 0
        for observation in result.scalars():
            observation.contributor_hash = None
            observation.pepper_version = None
            count += 1
        await self.session.flush()
        return count

    # -- promotion ---------------------------------------------------------

    def lines_add_up(self, line_total: Decimal, receipt_total: Decimal | None) -> bool:
        """The strongest filter in the promotion rule, and it is free.

        A receipt whose line items do not sum to its total had a bad read, and
        *none* of its lines are trustworthy -- not just the one that was
        misread. Rejecting the whole receipt is the correct granularity.
        """
        if receipt_total is None or receipt_total <= 0:
            return False
        difference = abs(line_total - receipt_total) / receipt_total
        return difference <= self.settings.price_promotion_total_tolerance

    async def reserve_contribution(self, user_id: uuid.UUID) -> bool:
        """Take one of this user's daily promotion slots, or refuse.

        The same ledger the metered providers use (ADR-008): one atomic
        increment against a cap, in Postgres, reserved *before* the write.
        Provider `"price_contributions"` rather than `"points"`, because this
        gates whether a price reaches other people and that is a different
        question from whether the contributor gets paid for it.

        No refund on a rejected line. Over-counting is the safe direction for a
        volume control, and reconciling would need a second write on the failure
        path -- which is where reservations get lost.
        """
        day_key, _ = period_keys(datetime.now(UTC))
        return await QuotaLedger(self.session).reserve(
            "price_contributions",
            windows=[],
            user_id=user_id,
            per_user_cap=self.settings.price_contributions_per_user_daily_cap,
            today=day_key,
        )

    async def refresh_item_reach(self, item: CanonicalItem) -> None:
        """Recount how many distinct people have reported this item.

        `observation_count` used to be incremented per write, which let one
        person publish an item alone by promoting it from two receipts at two
        shops. Counting `contributor_hash` values answers the question the
        `is_provisional` flag is actually asking -- has more than one person
        seen this -- and it does so without a `user_id` on the shared table,
        which deliberately has none.

        NULL hashes (machine sources, and erased contributors) are excluded by
        `count(DISTINCT ...)`, which is correct: neither is a person vouching
        for the item today.
        """
        contributors = await self.session.scalar(
            select(func.count(func.distinct(PriceObservation.contributor_hash))).where(
                PriceObservation.canonical_item_id == item.id,
                PriceObservation.retracted_at.is_(None),
            )
        )
        item.observation_count = int(contributors or 0)
        # A first sighting is one person's OCR. Publishing it as a product would
        # let a single mis-read become the entry everyone else then matches
        # against.
        item.is_provisional = item.observation_count < 2
        await self.session.flush()

    async def record_observation(
        self,
        *,
        user_id: uuid.UUID,
        canonical_item_id: uuid.UUID,
        store_id: uuid.UUID,
        unit_price: Decimal,
        observed_on: date,
        confidence: Decimal,
        pack_size: Decimal | None,
        pack_unit: str | None,
        source: ObservationSource = ObservationSource.RECEIPT,
        currency: str = "INR",
    ) -> PriceObservation | None:
        """Write one price into the shared graph.

        Returns `None` when the unique constraint refuses it -- one
        contribution per user per item per store per day, enforced by the
        database rather than by a check somebody can forget.
        """
        digest = contributor_hash(user_id, store_id, observed_on)

        existing = (
            await self.session.execute(
                select(PriceObservation).where(
                    PriceObservation.canonical_item_id == canonical_item_id,
                    PriceObservation.store_id == store_id,
                    PriceObservation.observed_on == observed_on,
                    PriceObservation.contributor_hash == digest,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            return None

        observation = PriceObservation(
            canonical_item_id=canonical_item_id,
            store_id=store_id,
            unit_price=unit_price.quantize(_CENT, rounding=ROUND_HALF_UP),
            currency=currency,
            pack_size=pack_size,
            pack_unit=pack_unit,
            observed_on=observed_on,
            source=source.value,
            confidence=confidence,
            contributor_hash=digest,
            pepper_version=PEPPER_VERSION,
        )
        self.session.add(observation)
        await self.session.flush()
        return observation


def _headline(price_count: int, report_count: int) -> str | None:
    """One line for a pin, or nothing.

    Prices first: a verified price is a stronger reason to walk somewhere than a
    report about one. `None` rather than "0 prices" -- a pin with nothing to say
    should say nothing.
    """
    if price_count:
        return f"{price_count} verified price{'s' if price_count != 1 else ''}"
    if report_count:
        return f"{report_count} report{'s' if report_count != 1 else ''}"
    return None
