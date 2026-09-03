"""Where offers come from, ordered by cost.

`find_offers` used to be cache -> adapter -> ask the ledger why nothing came
back -> simulate. That re-derivation is the thing that becomes a ladder of ifs
once there are four sources, so it is inverted here: **each source reports its
own reason**, and the driver is a loop over a tuple.

Two things the old shape got wrong, both of which its own comments record as
found the hard way, are structurally fixed by that inversion:

- `_provider_status()` asked the quota ledger a question the adapter already
  knew the answer to.
- the "these are not real listings" caveat was inferred from a *string*
  (`all(o.provider == "simulated")`), so a cached simulated result -- which
  reports `source="cache"` -- dropped it, leaving invented prices on screen
  labelled as though they were real listings about named retailers. Now the
  source that produced the offers attaches the caveat.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.ports import RetailOffer
from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SourceResult:
    """What one source produced, and how far to trust it."""

    offers: list[RetailOffer]
    source: str
    #: `ok` | `paused` | `warming` | `unavailable`.
    #:
    #: Not decoration: the panel says something different for each, and only
    #: one of them is the user's problem to solve. "Nothing found" and "the
    #: allowance is spent" look identical without this.
    status: str = "ok"
    caveats: tuple[str, ...] = ()
    #: False for answers that must not enter `offer_snapshots` -- the
    #: simulator's, and a `warming` miss. Caching either would serve invented
    #: or absent prices for the whole TTL.
    cacheable: bool = True


class OfferSource(Protocol):
    name: str

    async def fetch(
        self,
        session: AsyncSession,
        query: str,
        *,
        limit: int,
        user_id: uuid.UUID | None,
    ) -> SourceResult: ...


@dataclass
class CrowdsourcedSource:
    """Our own price graph. Free, local, and current (ADR-013).

    First in the chain because it is the cheapest and, for groceries and small
    retail, the only one that knows anything at all. A metered national search
    API has never heard of the shop on the corner.
    """

    name: str = "crowdsourced"

    async def fetch(
        self,
        session: AsyncSession,
        query: str,
        *,
        limit: int,
        user_id: uuid.UUID | None,
    ) -> SourceResult:
        from decimal import Decimal

        from sqlalchemy import func, select

        from app.modules.pricegraph.models import CanonicalItem
        from app.modules.pricegraph.service import PricegraphService

        similarity = func.similarity(CanonicalItem.normalized_key, query.lower())
        row = (
            await session.execute(
                select(CanonicalItem)
                .where(
                    CanonicalItem.is_provisional.is_(False),
                    CanonicalItem.normalized_key.op("%")(query.lower()),
                )
                .order_by(similarity.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if row is None:
            return SourceResult([], self.name, cacheable=False)

        prices = await PricegraphService(session).prices_for_item(row.id, limit=limit)
        if not prices:
            return SourceResult([], self.name, cacheable=False)

        now = datetime.now(UTC)
        offers = [
            RetailOffer(
                title=price.item_name,
                price=Decimal(price.median_price),
                seller=price.store.name,
                # No link: there is no page to send anyone to. A shop is a
                # place, and inventing a URL would be worse than an empty one.
                link="",
                as_of=datetime.combine(price.last_seen_on, now.time(), tzinfo=UTC),
                provider=self.name,
                currency=price.currency,
            )
            for price in prices
        ]

        best = prices[0]
        return SourceResult(
            offers,
            self.name,
            caveats=(
                f"Reported by {best.contributors} shoppers, most recently on "
                f"{best.last_seen_on.isoformat()}.",
                "Shelf prices change. This is what people paid, not a quoted price.",
            ),
            # Not cached: the graph is a local table already, and caching it
            # would only serve a staler copy of something free to read.
            cacheable=False,
        )


@dataclass
class ScraperCacheSource:
    """Reads what the scraper has already fetched. Never fetches (ADR-012).

    Zero network in the request path. On a miss it enqueues a crawl and returns
    empty *with a reason*, which is the second reason the chain needs
    `SourceResult.status` rather than a bare list.
    """

    name: str = "scraper"

    async def fetch(
        self,
        session: AsyncSession,
        query: str,
        *,
        limit: int,
        user_id: uuid.UUID | None,
    ) -> SourceResult:
        from app.adapters.offers import scraper_enabled

        # Asked of the factory, never of the adapter: no module may name
        # `app.adapters.offers.scraper` (`offer-adapters-stay-behind-the-port`).
        #
        # False on a default deployment -- the scraper ships disabled, exactly as
        # the metered provider ships as the simulator.
        if not scraper_enabled():
            return SourceResult([], self.name, cacheable=False)

        from app.modules.market.scrape_cache import read_cached, request_crawl

        cached = await read_cached(session, query, limit=limit)
        if cached:
            return SourceResult(cached, self.name)

        await request_crawl(query)
        return SourceResult(
            [],
            self.name,
            status="warming",
            caveats=("Checking local retailers — refresh in a moment.",),
            cacheable=False,
        )


@dataclass
class MeteredSearchSource:
    """The configured metered provider, gated by its own quota (ADR-008).

    Third, not first: it is the only link in the chain that can cost money, and
    the ledger's whole purpose is that it is spent on requests a user actually
    asked for rather than on ones the system could have answered itself.
    """

    name: str = "metered"

    async def fetch(
        self,
        session: AsyncSession,
        query: str,
        *,
        limit: int,
        user_id: uuid.UUID | None,
    ) -> SourceResult:
        from app.adapters.offers import get_offer_search

        search = get_offer_search()
        if hasattr(search, "bind"):
            search = search.bind(session, user_id)

        offers = await search.find_offers(query, limit=limit)
        if offers:
            # The configured provider *is* the simulator on any deployment
            # without an API key, which is the default and therefore the common
            # case. Those offers are invented, and a panel that shows them
            # without saying so is making a false claim about named retailers.
            #
            # Asking the adapter for its name, rather than assuming this link
            # always returns real listings, is the same principle the rest of
            # this module rests on: the caveat belongs to whatever produced the
            # offers.
            if search.name == "simulated":
                # Still cacheable. Caching the simulator buys nothing on its
                # own -- it is deterministic and free to regenerate -- but on a
                # deployment without an API key it is the *only* thing that
                # exercises the cache path, and a cache nothing writes to is a
                # cache nothing tests. `_result` in `offers.py` re-attaches this
                # caveat on the way back out, so the disclaimer survives the
                # round trip that used to lose it.
                return SourceResult(
                    offers,
                    search.name,
                    caveats=("These are simulated prices for illustration, not real listings.",),
                )
            return SourceResult(offers, search.name)

        # Empty is ambiguous -- no results, or no allowance. The adapter knows
        # which; ask it rather than re-deriving from the ledger afterwards.
        paused = await _is_paused(session)
        if paused:
            return SourceResult(
                [],
                search.name,
                status="paused",
                cacheable=False,
                caveats=(
                    "Live price comparison is paused: the free allowance for the "
                    "external price service is used up for this period.",
                ),
            )
        return SourceResult([], search.name, cacheable=False)


@dataclass
class SimulatedSource:
    """The last resort, and the one that says so.

    A panel showing simulated prices that does not say they are simulated is a
    claim about named retailers that happens to be false. The caveat is
    attached *here*, by the thing that produced the offers, rather than
    inferred downstream from a provider string.
    """

    name: str = "simulated"
    #: Set by the chain when the reason we are here is an exhausted allowance,
    #: so the message can say which of the two it is.
    paused: bool = False

    async def fetch(
        self,
        session: AsyncSession,
        query: str,
        *,
        limit: int,
        user_id: uuid.UUID | None,
    ) -> SourceResult:
        from app.adapters.offers.simulated import SimulatedOfferSearch
        from app.core.config import get_settings

        offers = await SimulatedOfferSearch().find_offers(query, limit=limit)
        caveats = [
            "These are simulated prices for illustration, not real listings.",
        ]
        if self.paused:
            caveats.append(
                f"Please contact {get_settings().support_contact} if you need live "
                "comparison restored."
            )
        return SourceResult(
            offers,
            self.name,
            status="paused" if self.paused else "ok",
            caveats=tuple(caveats),
            cacheable=False,
        )


async def _is_paused(session: AsyncSession) -> bool:
    from datetime import datetime as _dt

    from app.core.config import get_settings
    from app.core.quota import PeriodKind, QuotaLedger, Window, period_keys

    settings = get_settings()
    if settings.offer_search_provider != "serpapi":
        return False

    _, month_key = period_keys(_dt.now(UTC))
    state = await QuotaLedger(session).status(
        "serpapi",
        window=Window(PeriodKind.MONTH, month_key, settings.serpapi_monthly_cap),
    )
    return not state.available


def offer_chain(settings: Settings) -> tuple[OfferSource, ...]:
    """The sources, cheapest first.

    **The order is a constant, not a setting.** An operator has no business
    reordering "free before metered": configuration enables and disables links,
    it does not rearrange them. Getting that wrong would spend a month's
    allowance answering questions the local graph already knew.
    """
    del settings  # every link is enabled/disabled by its own configuration
    return (CrowdsourcedSource(), ScraperCacheSource(), MeteredSearchSource())


#: Kept out of `offer_chain` because it is not a peer of the others: it is what
#: runs when every real source came back empty, and it produces nothing true.
FALLBACK = SimulatedSource
