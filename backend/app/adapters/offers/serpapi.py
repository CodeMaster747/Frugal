"""Live Google Shopping results, behind a hard local quota.

The only source found that returns real Indian retail prices on a permanent
free plan with no credit card attached. Google's own Custom Search API closed to
new signups and sunsets on 2027-01-01; Brave withdrew its free tier in February
2026; the Bing Search API retired in August 2025. Scraping retailers directly is
rejected by ADR-004 and scraping Google is worse on every axis.

**The quota gate is inside `find_offers`, above the HTTP client, and it must
stay there.** A gate at the call site is a gate the next call site forgets. The
counter is Postgres, not Redis, because an evicted key restarts the count at
zero and the next thousand calls go through -- see `app/core/quota.py`.

Caps are set below SerpAPI's own free allowance so our stop fires first. Their
hard stop is the second line of defence, not the plan.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from app.adapters.ports import RetailOffer
from app.core.logging import get_logger
from app.core.quota import PeriodKind, QuotaLedger, Window, period_keys

logger = get_logger(__name__)

PROVIDER = "serpapi"
_ENDPOINT = "https://serpapi.com/search.json"

#: Seconds.
#:
#: Was 8, chosen on the reasoning that a user is waiting behind a button and a
#: slow comparison is worth less than a fast "we could not reach it". That
#: reasoning was right and the number was wrong: google_shopping regularly takes
#: longer, and every timeout still *spends the quota* -- the provider ran the
#: search whether or not we waited for it. An 8-second cap therefore bought a
#: faster failure at the price of the entire monthly allowance.
#:
#: 30 is chosen to fit the observed latency with headroom. Confirm against your
#: own account with `python scripts/verify_serpapi.py`, which prints the elapsed
#: time and warns when it exceeds this value.
_TIMEOUT = 30.0


class SerpApiOfferSearch:
    """Google Shopping via SerpAPI, metered locally.

    Constructed with a session so the ledger and the search share one
    transaction: a reservation that committed while the response was discarded
    would over-count, and one that did not commit while the call went out would
    under-count. The second is the direction that costs money.
    """

    name = PROVIDER

    def __init__(self, session: object | None = None, user_id: uuid.UUID | None = None) -> None:
        self._session = session
        self._user_id = user_id

    def bind(self, session: object, user_id: uuid.UUID | None) -> SerpApiOfferSearch:
        """Attach a request's session and caller.

        The factory builds this adapter without either -- it is selected from
        configuration at import time, before any request exists.
        """
        return SerpApiOfferSearch(session, user_id)

    async def find_offers(self, query: str, *, limit: int = 10) -> list[RetailOffer]:
        """Search, if there is allowance left. Returns [] when there is not.

        Empty is a normal result for this port, and the caller distinguishes
        "nothing matched" from "we are paused" through the ledger's status
        rather than through an exception -- because the advisor's verdict must
        not fail just because a price comparison is unavailable.
        """
        from app.core.config import get_settings

        settings = get_settings()
        api_key = settings.serpapi_api_key
        if api_key is None or self._session is None:
            return []

        now = datetime.now(UTC)
        day_key, month_key = period_keys(now)

        ledger = QuotaLedger(self._session)  # type: ignore[arg-type]
        allowed = await ledger.reserve(
            PROVIDER,
            # Tightest first. The daily cap exists so one bad day cannot eat
            # the month; the per-user cap so one person cannot drain a shared
            # allowance and leave everyone else looking at a paused banner.
            windows=[
                Window(PeriodKind.DAY, day_key, settings.serpapi_daily_cap),
                Window(PeriodKind.MONTH, month_key, settings.serpapi_monthly_cap),
            ],
            user_id=self._user_id,
            per_user_cap=settings.serpapi_per_user_daily_cap,
            today=day_key,
        )
        if not allowed:
            return []

        return await self._fetch(query, api_key.get_secret_value(), limit=limit, now=now)

    async def _fetch(
        self, query: str, api_key: str, *, limit: int, now: datetime
    ) -> list[RetailOffer]:
        import httpx

        params = {
            "engine": "google_shopping",
            "q": query,
            "api_key": api_key,
            "gl": "in",
            "hl": "en",
            "location": "India",
            "num": str(min(limit, 20)),
        }

        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                response = await client.get(_ENDPOINT, params=params)
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            # The quota is already spent, deliberately: the provider counted the
            # call whether or not we could read the answer. Refunding it here
            # would let a flapping endpoint spend the month twice over.
            logger.warning("serpapi request failed", exc_info=exc, extra={"query": query})
            return []

        return _parse(payload, now=now, limit=limit)


def _parse(payload: dict[str, object], *, now: datetime, limit: int) -> list[RetailOffer]:
    """Map a response to offers, skipping anything unpriced.

    Tolerant by design. A shopping result with no readable price is not an
    error, it is a listing we cannot rank -- and dropping it is better than
    either guessing or failing the whole search.
    """
    raw = payload.get("shopping_results")
    if not isinstance(raw, list):
        return []

    offers: list[RetailOffer] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        price = _price(entry)
        if price is None:
            continue
        offers.append(
            RetailOffer(
                title=str(entry.get("title") or "").strip()[:255],
                price=price,
                seller=str(entry.get("source") or "").strip()[:120],
                link=str(entry.get("product_link") or entry.get("link") or ""),
                as_of=now,
                provider=PROVIDER,
                seller_rating=_decimal(entry.get("rating")),
                rating_count=_int(entry.get("reviews")),
                delivery_note=str(entry.get("delivery") or "") or None,
                thumbnail_url=str(entry.get("thumbnail") or "") or None,
            )
        )
    return sorted(offers, key=lambda o: o.price)[:limit]


def _price(entry: dict[str, object]) -> Decimal | None:
    """The numeric price, preferring the parsed field over the display string.

    `extracted_price` is a float in the response. It is converted through `str`
    rather than `Decimal(float)`, because the binary value of 1299.99 is not
    1299.99 and ADR-003 bans that everywhere, not only in columns.
    """
    extracted = entry.get("extracted_price")
    if isinstance(extracted, int | float):
        try:
            value = Decimal(str(extracted))
        except InvalidOperation:
            return None
        return value if value > 0 else None
    return None


def _decimal(value: object) -> Decimal | None:
    if isinstance(value, int | float):
        try:
            return Decimal(str(value))
        except InvalidOperation:
            return None
    return None


def _int(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None
