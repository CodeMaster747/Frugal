"""The gate on every metered external call.

This project runs on free tiers, and "free tier" is a promise the provider makes
about volume, not a wall they build. Exceeding one either bills the account or
kills the service, and the first is worse. So the constraint is enforced here,
locally, with a cap set *below* the provider's own -- our stop fires first and
theirs is a second line of defence rather than the plan.

Three rules that are not negotiable, each of which exists because the obvious
alternative fails:

**Postgres is the authority, not Redis.** Redis holds only regenerable state by
policy (`core/cache.py`, `core/jobs.py`). A quota counter is the one piece of
state whose loss costs money: an evicted key means the count restarts at zero
and the next thousand calls go through.

**Reserve before the call, not after.** A crash mid-call must still consume the
quota, because the provider counted it. Optimism here is how a free tier becomes
a bill.

**Enforce inside the adapter, above the HTTP client.** A gate the caller has to
remember is not a gate. `test_quota_is_enforced_inside_the_adapter` scans for
this, because the natural refactor -- lifting the check to the call site "so it
can be handled properly" -- silently removes it from every other call site.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.errors import ExternalServicePausedError
from app.core.logging import get_logger
from app.core.models import Base, TenantMixin, TimestampMixin, UUIDMixin

logger = get_logger(__name__)


class PeriodKind:
    DAY = "day"
    MONTH = "month"


@dataclass(frozen=True, slots=True)
class Window:
    """One cap over one period."""

    kind: str
    key: str
    cap: int


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """What the banner and `GET /system/providers` render."""

    provider: str
    available: bool
    used: int
    cap: int
    period_key: str
    message: str

    @property
    def remaining(self) -> int:
        return max(0, self.cap - self.used)


def period_keys(now: datetime) -> tuple[str, str]:
    """(day_key, month_key) for a moment. UTC, matching the providers' own."""
    return now.date().isoformat(), f"{now.year:04d}-{now.month:02d}"


class QuotaLedger:
    """Counts calls to metered providers, and refuses when a cap is reached."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def reserve(
        self,
        provider: str,
        *,
        windows: list[Window],
        user_id: uuid.UUID | None = None,
        per_user_cap: int | None = None,
        today: str | None = None,
    ) -> bool:
        """Take one call from every window, or take none and return False.

        Windows are checked tightest-first, so a refusal costs the fewest
        increments. A refusal at any level refuses the call.

        Note what is *not* done here: a failure part-way through does not roll
        back the windows already incremented. That over-counts by at most one
        call per window per refusal, which is the safe direction -- it makes us
        stop marginally early rather than marginally late.
        """
        wants_user_cap = user_id is not None and per_user_cap is not None and today is not None
        if wants_user_cap and not await self._reserve_user(
            provider,
            user_id,  # type: ignore[arg-type]
            today,  # type: ignore[arg-type]
            per_user_cap,  # type: ignore[arg-type]
        ):
            return False

        for window in windows:
            if not await self._reserve_window(provider, window):
                logger.warning(
                    "external quota exhausted",
                    extra={
                        "provider": provider,
                        "period_kind": window.kind,
                        "period_key": window.key,
                        "cap": window.cap,
                    },
                )
                return False
        return True

    async def _reserve_window(self, provider: str, window: Window) -> bool:
        """One atomic statement. No read-modify-write, no race.

        `RETURNING` yields no row when the WHERE on the DO UPDATE fails, which
        is exactly the exhausted case. Correct under concurrency by
        construction -- the same reasoning as the Lua script in
        `core/rate_limit.py`, which notes the read-modify-write must be atomic
        anyway.
        """
        stmt = text(
            """
            INSERT INTO external_quota_usage
                (id, provider, period_kind, period_key, used, cap, first_call_at, last_call_at,
                 created_at, updated_at)
            VALUES (gen_random_uuid(), :provider, :kind, :key, 1, :cap, now(), now(), now(), now())
            ON CONFLICT (provider, period_kind, period_key) DO UPDATE
               SET used = external_quota_usage.used + 1,
                   last_call_at = now(),
                   updated_at = now()
             WHERE external_quota_usage.used < external_quota_usage.cap
            RETURNING used
            """
        )
        result = await self.session.execute(
            stmt, {"provider": provider, "kind": window.kind, "key": window.key, "cap": window.cap}
        )
        return result.scalar_one_or_none() is not None

    async def _reserve_user(
        self, provider: str, user_id: uuid.UUID, period_key: str, cap: int
    ) -> bool:
        """The per-user sub-cap, so one user cannot drain a shared allowance.

        Without it, the first person to open the offers panel on the first of
        the month spends the whole deployment's month, and everyone else sees a
        paused banner forever.
        """
        stmt = text(
            """
            INSERT INTO external_quota_user_usage
                (id, user_id, provider, period_key, used, created_at, updated_at)
            VALUES (gen_random_uuid(), :user_id, :provider, :key, 1, now(), now())
            ON CONFLICT (user_id, provider, period_key) DO UPDATE
               SET used = external_quota_user_usage.used + 1,
                   updated_at = now()
             WHERE external_quota_user_usage.used < :cap
            RETURNING used
            """
        )
        result = await self.session.execute(
            stmt, {"user_id": user_id, "provider": provider, "key": period_key, "cap": cap}
        )
        if result.scalar_one_or_none() is None:
            logger.info(
                "per-user external quota reached",
                extra={"provider": provider, "period_key": period_key, "cap": cap},
            )
            return False
        return True

    async def status(self, provider: str, *, window: Window) -> ProviderStatus:
        """For the banner and the providers endpoint. Never raises.

        A status call that failed would take down the page that exists to
        explain why something else is down.
        """
        try:
            row = (
                await self.session.execute(
                    select(ExternalQuotaUsage).where(
                        ExternalQuotaUsage.provider == provider,
                        ExternalQuotaUsage.period_kind == window.kind,
                        ExternalQuotaUsage.period_key == window.key,
                    )
                )
            ).scalar_one_or_none()
        except Exception:
            logger.exception("quota status lookup failed", extra={"provider": provider})
            return ProviderStatus(provider, True, 0, window.cap, window.key, "")

        used = row.used if row is not None else 0
        available = used < window.cap
        return ProviderStatus(
            provider=provider,
            available=available,
            used=used,
            cap=window.cap,
            period_key=window.key,
            message="" if available else _paused_message(provider),
        )


def _paused_message(provider: str) -> str:
    return (
        f"{provider} is paused: its free allowance for this period is used up. "
        "Please contact the developer if you need this restored."
    )


def paused(provider: str, *, contact: str | None = None) -> ExternalServicePausedError:
    """The error to raise where degrading is genuinely impossible.

    Prefer falling back to a fake and returning caveats. This exists for
    endpoints whose entire answer is the external call.
    """
    return ExternalServicePausedError(provider, contact=contact)


class ExternalQuotaUsage(UUIDMixin, TimestampMixin, Base):
    """Calls spent against one provider in one period.

    Deliberately *not* tenant-scoped. A free-tier allowance belongs to the
    deployment, not to a user, and giving it a `user_id` would both misdescribe
    it and pull it into the account-deletion cascade -- where deleting a user
    would refund quota the provider has already counted.
    """

    __tablename__ = "external_quota_usage"

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    period_kind: Mapped[str] = mapped_column(String(10), nullable=False)
    period_key: Mapped[str] = mapped_column(String(20), nullable=False)
    used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    #: Snapshotted at first use, so a later config change is visible in history
    #: rather than retroactively rewriting what the limit was.
    cap: Mapped[int] = mapped_column(Integer, nullable=False)
    first_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_call_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "provider", "period_kind", "period_key", name="uq_external_quota_usage_period"
        ),
    )


class ExternalQuotaUserUsage(UUIDMixin, TenantMixin, TimestampMixin, Base):
    """One user's share of a shared allowance.

    Tenant-scoped, unlike its sibling above, because this *is* a fact about a
    user -- and so it carries the cascading foreign key every user-owned table
    carries.
    """

    __tablename__ = "external_quota_user_usage"

    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    period_key: Mapped[str] = mapped_column(String(20), nullable=False)
    used: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        UniqueConstraint(
            "user_id", "provider", "period_key", name="uq_external_quota_user_usage_period"
        ),
    )
