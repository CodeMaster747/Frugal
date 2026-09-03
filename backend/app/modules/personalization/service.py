"""PersonalizationService -- the only way into the second database (ADR-011).

Two sessions, deliberately passed in rather than opened here: `signals` for the
personalization database and an optional `primary` for reading the ledger. A
service that opened its own connections would put the engine choice inside the
domain, and the whole point of `core/signals_database.py` living apart is that
exactly one module reaches it.

**No raw signal ever leaves this module.** The contract with the rest of the
system is `profile()` -- aggregates, factors, caveats -- and nothing else.
`purchase_signals` rows are derived here, read here, and erased here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.signals_base import SignalsBase
from app.modules.finance.schemas import TransactionFilters, TransactionKind
from app.modules.finance.service import FinanceService
from app.modules.personalization.archetypes import ARCHETYPES, match
from app.modules.personalization.derive import (
    RUBRIC_VERSION,
    DerivedProfile,
    DerivedSignal,
    Observation,
    derive_profile,
    derive_signals,
)
from app.modules.personalization.models import (
    ProfileArchetype,
    ProfileMatch,
    PurchaseSignal,
    SignalProfile,
)
from app.modules.personalization.repository import (
    ProfileMatchRepository,
    PurchaseSignalRepository,
    SignalProfileRepository,
)
from app.modules.personalization.schemas import ArchetypeMatchOut, ProfileOut, RefreshResult

logger = get_logger(__name__)

#: How far back a derivation run looks. Two years is the longest window any
#: engine in this system claims to reason over, and reading more would cost a
#: large scan for observations too old to describe a current habit.
LOOKBACK_DAYS = 730


class PersonalizationService:
    def __init__(self, signals: AsyncSession, primary: AsyncSession | None = None) -> None:
        self.signals = signals
        self.primary = primary
        self.purchase_signals = PurchaseSignalRepository(signals)
        self.profiles = SignalProfileRepository(signals)
        self.matches = ProfileMatchRepository(signals)

    # -- health ------------------------------------------------------------

    async def health(self) -> bool:
        """Whether the second database answers.

        Exists so `app/api/system.py` can report readiness without importing
        `core/signals_database` -- api -> modules is a permitted direction and
        api -> the engine module is not, which is the whole shape of the
        `the-signals-database-has-one-owner` contract.
        """
        try:
            await self.signals.execute(select(1))
        except Exception:
            return False
        return True

    # -- derivation --------------------------------------------------------

    async def refresh(self, subject_id: uuid.UUID, *, today: date | None = None) -> RefreshResult:
        """Re-derive this subject's signals and profile from the ledger.

        Requires a primary session: it reads through `FinanceService`, never
        through finance's models or repository, so the boundary contracts hold
        exactly as they do for every other consumer.
        """
        if self.primary is None:
            raise RuntimeError(
                "refresh() needs a primary session to read the ledger. Construct "
                "PersonalizationService(signals, primary) -- see worker_both_sessions()."
            )

        today = today or datetime.now(UTC).date()
        since = today - timedelta(days=LOOKBACK_DAYS)

        observations = await self._observations(subject_id, since=since)
        derived = derive_signals(observations)

        written, skipped = await self._store_signals(subject_id, derived)

        span_days = (today - min((o.occurred_on for o in observations), default=today)).days
        profile = derive_profile(
            derived, observation_days=span_days, total_observations=len(observations)
        )
        await self._store_profile(subject_id, profile)

        return RefreshResult(
            signals_written=written, signals_skipped=skipped, profile_recomputed=True
        )

    async def _observations(self, subject_id: uuid.UUID, *, since: date) -> list[Observation]:
        """Spend the ledger has recorded, in the shape the pure derivation wants.

        Expenses only: a salary credit is not a purchase, and including it would
        put the "big purchase" threshold above every actual purchase.
        """
        assert self.primary is not None
        finance = FinanceService(self.primary)

        rows, _ = await finance.list_transactions(
            subject_id,
            cursor=None,
            limit=5000,
            filters=TransactionFilters(from_date=since, kind=TransactionKind.EXPENSE),
        )

        return [
            Observation(
                key=str(txn.id),
                occurred_on=txn.occurred_on,
                amount=abs(txn.amount),
                merchant_normalized=txn.merchant_normalized,
                category_slug=getattr(txn.category, "slug", None),
                confidence=txn.category_confidence or Decimal("1.000"),
            )
            for txn in rows
            if not txn.excluded_from_analytics
        ]

    async def _store_signals(
        self, subject_id: uuid.UUID, derived: list[DerivedSignal]
    ) -> tuple[int, int]:
        written = skipped = 0
        for signal in derived:
            existing = await self.purchase_signals.by_digest(subject_id, signal.source_digest)
            if existing is not None:
                # Idempotent: the same source row updates rather than duplicates.
                existing.amount = signal.amount
                existing.amount_percentile = signal.amount_percentile
                existing.category_slug = signal.category_slug
                existing.confidence = signal.confidence
                skipped += 1
                continue
            await self.purchase_signals.add(
                PurchaseSignal(
                    subject_id=subject_id,
                    source_digest=signal.source_digest,
                    occurred_on=signal.occurred_on,
                    amount=signal.amount,
                    merchant_normalized=signal.merchant_normalized,
                    category_slug=signal.category_slug,
                    amount_percentile=signal.amount_percentile,
                    confidence=signal.confidence,
                )
            )
            written += 1
        return written, skipped

    async def _store_profile(self, subject_id: uuid.UUID, profile: DerivedProfile) -> None:
        row = await self.profiles.for_subject(subject_id)
        if row is None:
            row = SignalProfile(subject_id=subject_id)
            self.signals.add(row)

        row.computed_at = datetime.now(UTC)
        row.observation_days = profile.observation_days
        row.signal_count = profile.signal_count
        row.big_purchase_threshold = profile.big_purchase_threshold
        row.median_big_purchase = profile.median_big_purchase
        row.cadence_days = profile.cadence_days
        row.category_affinities = profile.category_affinities
        row.factors = profile.factors
        row.confidence = profile.confidence
        row.rubric_version = RUBRIC_VERSION
        await self.signals.flush()

        await self._store_matches(subject_id, profile)

    async def _store_matches(self, subject_id: uuid.UUID, profile: DerivedProfile) -> None:
        await self._ensure_archetypes()
        by_slug = {
            row.slug: row
            for row in (await self.signals.execute(select(ProfileArchetype))).scalars()
        }

        await self.signals.execute(
            delete(ProfileMatch).where(ProfileMatch.subject_id == subject_id)
        )
        for archetype, score in match(profile):
            self.signals.add(
                ProfileMatch(
                    subject_id=subject_id,
                    archetype_id=by_slug[archetype.slug].id,
                    score=score,
                )
            )
        await self.signals.flush()

    async def _ensure_archetypes(self) -> None:
        """Sync the reference table from the code that defines it.

        Same shape as `MarketService.sync_catalogue`: the rules live in
        `archetypes.py` where they can be read and tested, and the table exists
        so a match can carry a foreign key rather than a string.
        """
        existing = {
            row.slug for row in (await self.signals.execute(select(ProfileArchetype))).scalars()
        }
        for archetype in ARCHETYPES:
            if archetype.slug in existing:
                continue
            self.signals.add(
                ProfileArchetype(
                    slug=archetype.slug,
                    label=archetype.label,
                    description=archetype.description,
                )
            )
        await self.signals.flush()

    # -- read --------------------------------------------------------------

    async def profile(self, subject_id: uuid.UUID) -> ProfileOut:
        """The aggregate view. The only thing this module exposes."""
        row = await self.profiles.for_subject(subject_id)
        if row is None:
            return ProfileOut(
                available=False,
                caveats=[
                    "No spending profile yet. It is built from your own transaction "
                    "history and refreshed nightly."
                ],
            )

        archetypes = await self.matches.for_subject(subject_id)
        by_id = {
            row_.id: row_
            for row_ in (await self.signals.execute(select(ProfileArchetype))).scalars()
        }

        return ProfileOut(
            available=row.signal_count > 0,
            computed_at=row.computed_at,
            observation_days=row.observation_days,
            signal_count=row.signal_count,
            big_purchase_threshold=_money(row.big_purchase_threshold),
            median_big_purchase=_money(row.median_big_purchase),
            cadence_days=format(row.cadence_days, "f") if row.cadence_days is not None else None,
            category_affinities=row.category_affinities or {},
            archetypes=[
                ArchetypeMatchOut(
                    slug=by_id[m.archetype_id].slug,
                    label=by_id[m.archetype_id].label,
                    description=by_id[m.archetype_id].description,
                    score=format(m.score, "f"),
                )
                for m in archetypes
                if m.archetype_id in by_id
            ],
            confidence=format(row.confidence, "f"),
            factors=row.factors or [],
            caveats=[] if row.signal_count else ["No notable purchases identified yet."],
        )

    # -- erasure -----------------------------------------------------------

    async def erase(self, subject_id: uuid.UUID) -> int:
        """Delete everything this subject owns in the personalization database.

        Tables are discovered from the metadata in reverse dependency order
        rather than listed, so a table added later is erased without anyone
        remembering to add it here -- the same discipline as the truncate in
        `conftest.isolated_test` and `_USER_OWNED_TABLES`. A hand-maintained
        list is how a right-to-erasure obligation quietly stops being met.
        """
        deleted = 0
        for table in reversed(SignalsBase.metadata.sorted_tables):
            if "subject_id" not in table.columns:
                continue
            result = await self.signals.execute(
                delete(table).where(table.c.subject_id == subject_id)
            )
            deleted += result.rowcount if hasattr(result, "rowcount") else 0
        return deleted


def _money(value: Decimal | None) -> str | None:
    return format(value, "f") if value is not None else None


async def health_check() -> bool | None:
    """Whether the personalization database answers, or `None` if unconfigured.

    A free function rather than a method, because the caller that needs it --
    `app/api/system.py` -- has no session and may not open one: acquiring a
    signals session means naming `core.signals_database`, which the
    `the-signals-database-has-one-owner` contract forbids above this layer.
    Owning the session here keeps that import inside the module, which is the
    whole point of the contract rather than an obstacle to it.
    """
    from app.core.config import get_settings

    if not get_settings().personalization_enabled:
        return None

    from app.core.signals_database import get_signals_session_factory

    try:
        async with get_signals_session_factory()() as session:
            return await PersonalizationService(session).health()
    except Exception:
        return False
