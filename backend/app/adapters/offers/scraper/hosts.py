"""Which hosts may be fetched, and how politely (ADR-012).

**Checked-in code, not an environment variable.** A host allowed without an
extractor is useless, and the two have to move together. `scraper_enabled_hosts`
can turn a host *off*; it cannot turn on one that has no rule here.

The list ships **empty of enabled hosts**, matching the `simulated` default
elsewhere: a fresh deployment is incapable of crawling anyone, exactly as it is
incapable of spending money.

`allowed_path_prefixes` is a *narrowing* of robots.txt, never a substitute:
both must permit a path before it is requested.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from app.core.config import Settings

#: Recorded on every scraped row, so the output of a bad extractor is findable
#: and retractable by rule version rather than by guessing at dates.
HOST_RULES_VERSION = "2026-08-26"

#: The token sent as User-Agent *and* given to `RobotFileParser.can_fetch`.
#:
#: One constant used in both places, because a mismatch means honouring a
#: different set of rules from the ones we claim to obey -- a silent violation,
#: and the reason `test_the_robots_token_matches_the_user_agent` exists.
UA_TOKEN = "FrugalBot"  # noqa: S105 — a user-agent token, not a credential


@dataclass(frozen=True, slots=True)
class HostRule:
    host: str
    #: Only these prefixes are ever requested.
    allowed_path_prefixes: tuple[str, ...]
    kind: Literal["json", "html"]
    #: A format string taking `{query}`, already URL-encoded by the caller.
    search_url: str
    #: Dotted name of the extractor in `parse.py`.
    extractor: str
    min_interval_seconds: int = 10
    daily_request_cap: int = 200


#: Every host the code knows how to read.
#:
#: Empty in the shipped configuration. Adding one means writing an extractor,
#: checking that host's robots.txt and terms by hand, and deciding that the
#: answer is yes -- which is a judgement, and judgements belong in a file
#: somebody has to edit rather than in a variable somebody can set.
#:
#: ADR-012 narrows ADR-004 rather than reversing it: what was rejected was
#: unrestricted scraping of retail product pages behind bot detection. What is
#: permitted here is allowlisted, robots-permitted, rate-limited, fail-closed
#: fetching of public endpoints. Everything outside this mapping is still
#: rejected, and still for the reasons ADR-004 gave.
ALLOWED_HOSTS: Mapping[str, HostRule] = {
    # Open Food Facts' Open Prices: a crowdsourced open price database with a
    # documented public API, no key and no signup. Verified before adding:
    # `GET /api/v1/prices?size=2` answers unauthenticated over 300,000 prices.
    #
    # It is the only candidate that is unambiguously free, key-free and
    # permitted. The Indian retailers -- Amazon.in, Flipkart, BigBasket,
    # Blinkit, DMart -- forbid scraping in their terms and disallow product and
    # search paths in robots.txt. That is precisely what ADR-004 rejected and
    # ADR-012 does not permit, and no configuration flag makes it permitted.
    #
    # Two honest caveats, neither of which is a reason not to add it:
    #
    # - Coverage is heavily France and Europe today, so an Indian query returns
    #   little. It grows with the project, and the pipeline being real is worth
    #   more than the rows it returns this month.
    # - The data is ODbL. Attribution is required wherever these prices are
    #   shown, which `market/sources.py` carries as a caveat on every offer.
    #
    # The `location` object on each price references an OpenStreetMap node,
    # which is the same identity `stores.osm_id` already holds from the OSM
    # import -- so a fetched price can resolve to a shop we already know.
    "prices.openfoodfacts.org": HostRule(
        host="prices.openfoodfacts.org",
        allowed_path_prefixes=("/api/v1/prices",),
        kind="json",
        # `product_name`, not `product_name__like`.
        #
        # This endpoint silently ignores unknown filter parameters. A first
        # attempt used `product_name__like`, which returned all 301,369 rows
        # -- so a search for milk came back as French pasta at entirely
        # plausible prices. The API offers exact `product_name` or
        # `product_code`; there is no substring search over prices.
        #
        # The practical consequence, stated plainly: an exact match against
        # OCR-derived names like "Amul Taaza Milk 500ml" will almost never hit.
        # This host is a working, tested scaffold whose yield is close to zero
        # until receipts carry barcodes and we can query by `product_code`.
        # That is why it ships disabled and why the extractor checks relevance
        # rather than trusting the filter.
        search_url=(
            "https://prices.openfoodfacts.org/api/v1/prices"
            "?product_name={query}&size=20&order_by=-date"
        ),
        extractor="open_prices",
        # Deliberately gentle. This is a volunteer-run project, and the
        # difference between polite and rude here is measured in seconds we do
        # not need.
        min_interval_seconds=30,
        daily_request_cap=100,
    ),
}


def enabled_hosts(settings: Settings) -> tuple[HostRule, ...]:
    """The hosts this deployment may fetch.

    The intersection of what the code can read and what the operator has turned
    on -- so a typo in configuration disables a host rather than inventing one.
    """
    enabled = set(settings.scraper_enabled_hosts)
    return tuple(rule for host, rule in ALLOWED_HOSTS.items() if host in enabled)


def user_agent(settings: Settings) -> str:
    """A contactable identifier, which is a compliance requirement rather than
    a courtesy: a host that wants us to stop needs somewhere to say so."""
    return f"{UA_TOKEN}/0.1 (+{settings.frontend_url}/bot; {settings.support_contact})"


def path_is_allowed(rule: HostRule, path: str) -> bool:
    return any(path.startswith(prefix) for prefix in rule.allowed_path_prefixes)
