"""robots.txt, fetched, cached, and obeyed (ADR-012).

Two decisions here matter more than the rest of the file.

**Never call `RobotFileParser.read()`.** It performs its own
`urllib.request.urlopen` with no timeout and follows redirects. The bytes are
fetched here, with a timeout, and handed to `parse()`.

**Fail closed.** Getting this backwards is the whole risk of the feature:

| outcome | decision |
|---|---|
| 404 | **allow** -- RFC 9309: no robots.txt means no restrictions |
| 2xx | obey what it says |
| timeout, 5xx, connection error | **deny**, 24h -- unreachable is a full disallow |
| 403 or 429 on robots.txt itself | **deny**, 7 days |

Cached in Postgres rather than Redis, against the usual preference and for a
stronger reason than the offer cache's: an evicted *allow* costs one wasted
fetch, but an evicted *deny* means crawling a path we were told not to. That is
a compliance failure, not a performance one, so it belongs in the store that
does not evict.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.robotparser import RobotFileParser

from sqlalchemy import Boolean, DateTime, Index, SmallInteger, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.models import Base, TimestampMixin, UUIDMixin

CACHE_TTL = timedelta(hours=24)
FAILURE_TTL = timedelta(hours=24)
REFUSAL_TTL = timedelta(days=7)

_TIMEOUT_SECONDS = 10.0


class RobotsPolicy(UUIDMixin, TimestampMixin, Base):
    """One host's robots.txt, as last read. Global: no `user_id`."""

    __tablename__ = "robots_policies"

    host: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    body: Mapped[str | None] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: True when the fetch failed. Deny is the default on ignorance, so this
    #: flag is load-bearing rather than diagnostic.
    fetch_failed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    status_code: Mapped[int | None] = mapped_column(SmallInteger)
    crawl_delay_seconds: Mapped[int | None] = mapped_column(SmallInteger)

    __table_args__ = (Index("ix_robots_policies_expires_at", "expires_at"),)


@dataclass(frozen=True, slots=True)
class Policy:
    parser: RobotFileParser | None
    fetch_failed: bool
    crawl_delay_seconds: int | None

    def allows(self, url: str, token: str) -> bool:
        if self.fetch_failed:
            return False
        if self.parser is None:
            # 404: no robots.txt, no restrictions (RFC 9309).
            return True
        return self.parser.can_fetch(token, url)


def _parse(body: str) -> RobotFileParser:
    parser = RobotFileParser()
    # `parse`, never `read`: see the module docstring.
    parser.parse(body.splitlines())
    return parser


async def policy_for(session: AsyncSession, host: str, *, token: str) -> Policy:
    """The cached policy for a host, fetching it if stale."""
    now = datetime.now(UTC)
    row = (
        await session.execute(select(RobotsPolicy).where(RobotsPolicy.host == host))
    ).scalar_one_or_none()

    if row is not None and row.expires_at > now:
        return _to_policy(row, token)

    body, status, failed = await _fetch(host)

    ttl = CACHE_TTL
    if failed:
        ttl = REFUSAL_TTL if status in (403, 429) else FAILURE_TTL

    parser = _parse(body) if body is not None else None
    delay = None
    if parser is not None:
        raw_delay = parser.crawl_delay(token)
        delay = int(raw_delay) if raw_delay is not None else None

    if row is None:
        row = RobotsPolicy(host=host)
        session.add(row)

    row.body = body
    row.fetched_at = now
    row.expires_at = now + ttl
    row.fetch_failed = failed
    row.status_code = status
    row.crawl_delay_seconds = delay
    await session.flush()

    return Policy(parser=parser, fetch_failed=failed, crawl_delay_seconds=delay)


def _to_policy(row: RobotsPolicy, token: str) -> Policy:
    parser = _parse(row.body) if row.body else None
    return Policy(
        parser=parser,
        fetch_failed=row.fetch_failed,
        crawl_delay_seconds=row.crawl_delay_seconds,
    )


async def _fetch(host: str) -> tuple[str | None, int | None, bool]:
    """Returns (body, status, failed).

    A 404 is `(None, 404, False)` -- *not* a failure. That distinction is the
    difference between "this host has no rules" and "we could not learn this
    host's rules", and treating them alike would either block every host
    without a robots.txt or crawl every host we could not reach.
    """
    import httpx

    url = f"https://{host}/robots.txt"
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS, follow_redirects=True) as client:
            response = await client.get(url)
    except Exception:
        # Unreachable is treated as a full disallow.
        return None, None, True

    if response.status_code == 404:
        return None, 404, False
    if response.status_code >= 400:
        return None, response.status_code, True
    return response.text, response.status_code, False
