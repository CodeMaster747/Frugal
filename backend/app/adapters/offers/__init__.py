"""Offer-search adapter selection.

Mirrors `adapters/pricing/__init__.py`. The default is the simulator, and that
is not a placeholder: it is what CI, local development, and any deployment
without an API key run against, so the entire offers feature is demonstrable
with no external call and no possibility of a bill.

Switching to SerpAPI is a config change and nothing else -- which is the
property ADR-004 argues for, and the reason the quota ledger can be the only
thing standing between a free tier and an invoice.
"""

from __future__ import annotations

from app.adapters.ports import OfferSearch


def get_offer_search() -> OfferSearch:
    from app.core.config import get_settings

    settings = get_settings()

    if settings.offer_search_provider == "serpapi" and settings.serpapi_api_key:
        from app.adapters.offers.serpapi import SerpApiOfferSearch

        return SerpApiOfferSearch()

    from app.adapters.offers.simulated import SimulatedOfferSearch

    return SimulatedOfferSearch()


def scraper_enabled() -> bool:
    """Whether this deployment may fetch anything at all (ADR-012).

    Asked of the factory, never of the concrete adapter: the
    `offer-adapters-stay-behind-the-port` contract forbids a module from naming
    `app.adapters.offers.scraper`, and that rule is the reason a module can ask
    *whether* the capability exists without learning how it is implemented.

    False on a default deployment -- `scraper_enabled_hosts` is empty and
    `ALLOWED_HOSTS` ships with no entries, so the answer is no twice over.
    """
    from app.adapters.offers.scraper.hosts import enabled_hosts
    from app.core.config import get_settings

    return bool(enabled_hosts(get_settings()))


def rules_version() -> str:
    """The host-rules version scraped results are keyed on.

    Exposed here rather than imported from the adapter, for the contract reason
    above. When a host rule or an extractor changes, this changes, and every
    result those rules produced is retired without a migration.
    """
    from app.adapters.offers.scraper.hosts import HOST_RULES_VERSION

    return HOST_RULES_VERSION


def get_scraper(session: object, user_id: object = None) -> object:
    """The scraper, for the worker task that owns it.

    Behind the factory for the same reason `get_offer_search` is: a caller asks
    for the configured capability and never names an implementation.
    """
    from app.adapters.offers.scraper import ScraperOfferSearch

    return ScraperOfferSearch(session, user_id)  # type: ignore[arg-type]
