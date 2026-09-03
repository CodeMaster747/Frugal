"""A narrow, allowlisted, robots-honouring offer fetcher (ADR-012).

A second implementation of `OfferSearch`, not a new port. Test it against the
three reasons `ports.py` gives for `OfferSearch` existing separately from
`PriceProvider`, and it fails all three in exactly the way SerpAPI does: it
supplies no catalogue, no history and no sellers list; it returns *the same*
product from other sellers rather than a cheaper different one; and it must
never sit on the advisor's `alternatives()` path. It returns `RetailOffer`
verbatim -- a listing seen once, at a moment, with a link out.

**The gate order is the whole safety property**, and it is asserted by a source
scan in the test suite:

    robots.txt  ->  host budget  ->  HTTP

A path we were told not to fetch must not even consume a request slot. Reversing
those two lines would spend the day's allowance discovering we were not allowed
to ask.
"""

from __future__ import annotations

import uuid
from urllib.parse import quote, urlparse

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.offers.scraper import parse, robots
from app.adapters.offers.scraper.hosts import (
    HOST_RULES_VERSION,
    UA_TOKEN,
    HostRule,
    enabled_hosts,
    path_is_allowed,
    user_agent,
)
from app.adapters.ports import RetailOffer
from app.core.config import get_settings
from app.core.host_budget import HostBudgetLedger
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Split rather than a single number: a host that is slow to connect is
#: probably down, while one that is slow to respond may just be busy.
_CONNECT_TIMEOUT = 5.0
_READ_TIMEOUT = 10.0
_MAX_RETRIES = 2


class ScraperOfferSearch:
    """Fetches offers from allowlisted hosts. Worker-side only."""

    name = "scraper"

    def __init__(self, session: AsyncSession, user_id: uuid.UUID | None = None) -> None:
        self._session = session
        self._user_id = user_id
        self._budget = HostBudgetLedger(session)

    async def find_offers(self, query: str, *, limit: int = 10) -> list[RetailOffer]:
        settings = get_settings()
        rules = enabled_hosts(settings)
        if not rules:
            # The shipped configuration. Not an error: a deployment that has
            # not opted into a host has nothing to fetch.
            return []

        offers: list[RetailOffer] = []
        budget_remaining = settings.scraper_requests_per_run

        for rule in rules:
            if budget_remaining <= 0:
                break
            await self._budget.ensure(
                rule.host,
                min_interval_seconds=rule.min_interval_seconds,
                daily_cap=rule.daily_request_cap,
            )
            fetched = await self.fetch_one(rule, self._url_for(rule, query), query=query)
            budget_remaining -= 1
            offers.extend(fetched)

        return sorted(offers, key=lambda offer: offer.price)[:limit]

    @staticmethod
    def _url_for(rule: HostRule, query: str) -> str:
        return rule.search_url.format(query=quote(query, safe=""))

    async def fetch_one(self, rule: HostRule, url: str, *, query: str = "") -> list[RetailOffer]:
        """One request to one host, if both gates allow it.

        Read the order of the next three blocks as the contract. It is scanned
        by `test_the_gate_precedes_the_http_call` and
        `test_robots_is_checked_before_the_budget_is_spent`, so it cannot drift
        without the build going red.
        """
        parsed = urlparse(url)
        if parsed.hostname != rule.host or not path_is_allowed(rule, parsed.path):
            # Our own narrowing, checked before anyone else's. A rule that
            # permits a host does not permit every path on it.
            return []

        policy = await robots.policy_for(self._session, rule.host, token=UA_TOKEN)
        if not policy.allows(url, UA_TOKEN):
            logger.info("robots.txt disallows this path", extra={"host": rule.host})
            return []

        from datetime import UTC, datetime

        today = datetime.now(UTC).date().isoformat()
        if not await self._budget.acquire(rule.host, today=today):
            return []

        return await self._get(url, rule, query)

    async def _get(self, url: str, rule: HostRule, query: str) -> list[RetailOffer]:
        """The HTTP call, below the gates and never beside them."""
        import asyncio
        import random

        import httpx

        settings = get_settings()
        headers = {"User-Agent": user_agent(settings), "Accept": "application/json"}
        timeout = httpx.Timeout(
            connect=_CONNECT_TIMEOUT, read=_READ_TIMEOUT, write=_CONNECT_TIMEOUT, pool=5.0
        )

        for attempt in range(_MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                    response = await client.get(url, headers=headers)
            except Exception as exc:
                if attempt == _MAX_RETRIES:
                    logger.warning("scrape request failed", extra={"host": rule.host}, exc_info=exc)
                    await self._budget.penalize(rule.host, status=None)
                    return []
                await asyncio.sleep(2**attempt + random.random())  # noqa: S311 — jitter
                continue

            if response.status_code in (429, 503):
                # A host that says how long to wait has told us the answer;
                # substituting our own guess is the rudeness this whole file
                # exists to avoid.
                retry_after = response.headers.get("Retry-After")
                await self._budget.penalize(
                    rule.host,
                    status=response.status_code,
                    retry_after_seconds=int(retry_after) if _is_int(retry_after) else None,
                )
                return []

            if response.status_code >= 400:
                # A 403 is the host saying no, and it is not retried.
                await self._budget.penalize(rule.host, status=response.status_code)
                return []

            return await self._extract(response, rule, query)

        return []

    async def _extract(self, response: object, rule: HostRule, query: str) -> list[RetailOffer]:
        extractor = parse.resolve(rule.extractor)
        if extractor is None:
            logger.error("no extractor named %s", rule.extractor)
            return []

        try:
            payload = response.json()  # type: ignore[attr-defined]
            raw = extractor(payload, query)
        except Exception as exc:
            logger.warning("could not read response", extra={"host": rule.host}, exc_info=exc)
            await self._budget.note_parse_failure(rule.host)
            return []

        offers = parse.build(raw, provider=f"{self.name}:{HOST_RULES_VERSION}", host=rule.host)
        if not offers and raw:
            # Fields were found and every one failed validation, which is what a
            # changed page looks like from here.
            await self._budget.note_parse_failure(rule.host)
        else:
            await self._budget.note_success(rule.host)
        return offers


def _is_int(value: str | None) -> bool:
    return value is not None and value.strip().isdigit()
