"""How often we may talk to one host (ADR-012).

A deliberate sibling of `core/quota.py` rather than an extension of it. The
structures read alike on purpose; the invariants differ, and merging them would
make ADR-008's rules -- reserve before the call, no refund, never from a sweep --
false of half the rows in that table.

`quota.py` is per-provider-per-period, where a period key is a day or a month
string. It can express "200 calls this month". It cannot express either of the
two things politeness actually needs:

- **spacing** -- "at least ten seconds since the last request to this host" is
  an *instant*, not a count;
- **cooldown** -- "this host returned 429, stay away until T".

Postgres rather than Redis, and for a stronger reason than the offer cache's:
an evicted counter there costs money, but an evicted *spacing* value here means
hammering a host we promised not to -- which costs the deployment its access and
the project its good faith.

Not tenant-scoped. Politeness to a host is owed by the deployment, never by the
user who happened to type the query.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import DateTime, Integer, SmallInteger, String, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDMixin

#: How long a host is left alone after refusing us, when it does not say.
DEFAULT_COOLDOWN = timedelta(hours=24)
#: A 403, or a robots.txt we could not read, is a longer no.
HARD_COOLDOWN = timedelta(days=7)
#: Consecutive parse failures before an extractor is assumed broken.
#:
#: A silently-broken extractor returning garbage prices is worse than one
#: returning nothing, because the garbage lands in a graph users trust.
MAX_PARSE_FAILURES = 5


class HostBudget(UUIDMixin, TimestampMixin, Base):
    __tablename__ = "scraper_host_budget"

    host: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)

    #: The spacing constraint: a request may go out only when now() >= this.
    next_allowed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    min_interval_seconds: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default="10"
    )

    #: The volume constraint -- the same shape as a quota Window.
    day_key: Mapped[str] = mapped_column(String(10), nullable=False, server_default="")
    requests_today: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    daily_cap: Mapped[int] = mapped_column(Integer, nullable=False, server_default="200")

    #: Set when a host says no. Overrides both constraints above.
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_parse_failures: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )
    last_status: Mapped[int | None] = mapped_column(SmallInteger)


@dataclass(frozen=True, slots=True)
class HostStatus:
    host: str
    available: bool
    requests_today: int
    daily_cap: int
    cooldown_until: datetime | None
    message: str


class HostBudgetLedger:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def ensure(self, host: str, *, min_interval_seconds: int, daily_cap: int) -> None:
        """Create or update the row from the checked-in host rule.

        A configuration change takes effect; the counters do not reset. Letting
        a redeploy zero `requests_today` would make the daily cap a function of
        deploy frequency.
        """
        await self.session.execute(
            text("""
                INSERT INTO scraper_host_budget
                       (id, host, next_allowed_at, min_interval_seconds,
                        day_key, requests_today, daily_cap, created_at, updated_at)
                VALUES (gen_random_uuid(), :host, now(), :interval, '', 0, :cap, now(), now())
                ON CONFLICT (host) DO UPDATE
                   SET min_interval_seconds = :interval,
                       daily_cap = :cap,
                       updated_at = now()
            """),
            {"host": host, "interval": min_interval_seconds, "cap": daily_cap},
        )

    async def acquire(self, host: str, *, today: str) -> bool:
        """Take one request slot, or take none and return False.

        One atomic statement, no read-modify-write, no race -- the same
        reasoning as `QuotaLedger._reserve_window`. `RETURNING` yields no row
        exactly when we must not fetch.
        """
        result = await self.session.execute(
            text("""
                UPDATE scraper_host_budget
                   SET next_allowed_at = now() + (min_interval_seconds * interval '1 second'),
                       requests_today  = CASE WHEN day_key = :today
                                              THEN requests_today + 1 ELSE 1 END,
                       day_key         = :today,
                       updated_at      = now()
                 WHERE host = :host
                   AND (cooldown_until IS NULL OR cooldown_until <= now())
                   AND next_allowed_at <= now()
                   AND (day_key <> :today OR requests_today < daily_cap)
             RETURNING requests_today
            """),
            {"host": host, "today": today},
        )
        return result.first() is not None

    async def penalize(
        self, host: str, *, status: int | None, retry_after_seconds: int | None = None
    ) -> None:
        """Back off after a host refuses us.

        `Retry-After` is honoured when given: a host that says how long to wait
        has told us the answer, and substituting our own guess is exactly the
        rudeness this table exists to prevent.
        """
        if retry_after_seconds is not None:
            cooldown = timedelta(seconds=min(retry_after_seconds, 86_400))
        elif status == 403:
            cooldown = HARD_COOLDOWN
        else:
            cooldown = DEFAULT_COOLDOWN

        await self.session.execute(
            text("""
                UPDATE scraper_host_budget
                   SET cooldown_until = now() + (:seconds * interval '1 second'),
                       last_status = :status,
                       updated_at = now()
                 WHERE host = :host
            """),
            {"host": host, "seconds": int(cooldown.total_seconds()), "status": status},
        )

    async def note_parse_failure(self, host: str) -> None:
        """Count a broken extractor, and stop asking once it is clearly broken."""
        await self.session.execute(
            text("""
                UPDATE scraper_host_budget
                   SET consecutive_parse_failures = consecutive_parse_failures + 1,
                       cooldown_until = CASE
                           WHEN consecutive_parse_failures + 1 >= :max
                           THEN now() + interval '7 days' ELSE cooldown_until END,
                       updated_at = now()
                 WHERE host = :host
            """),
            {"host": host, "max": MAX_PARSE_FAILURES},
        )

    async def note_success(self, host: str) -> None:
        await self.session.execute(
            text("""
                UPDATE scraper_host_budget
                   SET consecutive_parse_failures = 0, last_status = 200, updated_at = now()
                 WHERE host = :host
            """),
            {"host": host},
        )

    async def status(self, host: str) -> HostStatus:
        """Never raises -- the same contract as `QuotaLedger.status`."""
        try:
            row = (
                await self.session.execute(
                    text("""
                        SELECT host, requests_today, daily_cap, cooldown_until, day_key
                          FROM scraper_host_budget WHERE host = :host
                    """),
                    {"host": host},
                )
            ).first()
        except Exception:
            return HostStatus(host, False, 0, 0, None, "budget unavailable")

        if row is None:
            return HostStatus(host, False, 0, 0, None, "host not configured")

        now = datetime.now(UTC)
        cooling = row.cooldown_until is not None and row.cooldown_until > now
        available = not cooling and row.requests_today < row.daily_cap
        return HostStatus(
            host=row.host,
            available=available,
            requests_today=row.requests_today,
            daily_cap=row.daily_cap,
            cooldown_until=row.cooldown_until,
            message=(
                f"cooling down until {row.cooldown_until.isoformat()}"
                if cooling
                else f"{row.requests_today}/{row.daily_cap} requests today"
            ),
        )
