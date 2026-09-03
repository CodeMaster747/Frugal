"""The scraper's safety properties (ADR-012).

Nothing here makes a network call. The properties worth testing are not "does it
fetch" but "does it refuse to", and every one of those is decidable without a
socket.

The two source-scan tests at the bottom look unusual and are the most valuable
in the file: the gate *order* is the whole safety argument, and an ordering is
not something a behavioural test can pin without an actual host to be rude to.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from app.adapters.offers.scraper import ScraperOfferSearch, parse
from app.adapters.offers.scraper.hosts import (
    ALLOWED_HOSTS,
    HOST_RULES_VERSION,
    UA_TOKEN,
    HostRule,
    enabled_hosts,
    path_is_allowed,
    user_agent,
)
from app.adapters.offers.scraper.robots import Policy, _parse

RULE = HostRule(
    host="example.test",
    allowed_path_prefixes=("/api/prices",),
    kind="json",
    search_url="https://example.test/api/prices?q={query}",
    extractor="example",
)


class TestItShipsDisabled:
    def test_no_host_is_enabled_out_of_the_box(self, settings):
        """A fresh deployment is incapable of crawling anyone, exactly as it is
        incapable of spending money (ADR-008's default).

        `ALLOWED_HOSTS` is no longer empty -- it names what the code knows how
        to read -- so the promise now lives in `scraper_enabled_hosts`, which
        does ship empty.
        """
        assert settings.scraper_enabled_hosts == []
        assert enabled_hosts(settings) == ()

    def test_configuration_cannot_enable_an_unknown_host(self, settings):
        """A host allowed by configuration but unknown to the code is inert.

        The permission and the extractor have to move together: an operator who
        could point the crawler at an arbitrary host by setting one variable
        could point it at anything. `enabled_hosts` intersects the two, so the
        variable can only ever *narrow* what the code already knows how to read.
        """
        pretend = settings.model_copy(
            update={"scraper_enabled_hosts": ["somewhere-we-never-taught-it.test"]}
        )
        assert enabled_hosts(pretend) == ()


class TestPathNarrowing:
    def test_only_listed_prefixes_are_requested(self):
        """A narrowing of robots.txt, never a substitute: a rule that permits a
        host does not permit every path on it."""
        assert path_is_allowed(RULE, "/api/prices?q=milk")
        assert not path_is_allowed(RULE, "/account")
        assert not path_is_allowed(RULE, "/")


class TestRobots:
    def test_a_disallowed_path_is_refused(self):
        policy = Policy(
            parser=_parse("User-agent: *\nDisallow: /api/"),
            fetch_failed=False,
            crawl_delay_seconds=None,
        )
        assert not policy.allows("https://example.test/api/prices", UA_TOKEN)

    def test_an_allowed_path_is_permitted(self):
        policy = Policy(
            parser=_parse("User-agent: *\nDisallow: /account"),
            fetch_failed=False,
            crawl_delay_seconds=None,
        )
        assert policy.allows("https://example.test/api/prices", UA_TOKEN)

    def test_no_robots_file_means_no_restrictions(self):
        """RFC 9309. A 404 is not a failure, and conflating the two would
        either block every host without a robots.txt or crawl every host we
        could not reach."""
        policy = Policy(parser=None, fetch_failed=False, crawl_delay_seconds=None)
        assert policy.allows("https://example.test/anything", UA_TOKEN)

    def test_an_unreachable_robots_file_denies_everything(self):
        """Fail closed. Getting this backwards is the whole risk of the
        feature."""
        policy = Policy(parser=None, fetch_failed=True, crawl_delay_seconds=None)
        assert not policy.allows("https://example.test/anything", UA_TOKEN)

    def test_our_token_is_honoured_specifically(self):
        policy = Policy(
            parser=_parse(f"User-agent: {UA_TOKEN}\nDisallow: /\n\nUser-agent: *\nAllow: /"),
            fetch_failed=False,
            crawl_delay_seconds=None,
        )
        assert not policy.allows("https://example.test/api/prices", UA_TOKEN)


class TestTheUserAgent:
    def test_the_robots_token_matches_the_user_agent(self, settings):
        """A mismatch means honouring a different set of rules from the ones we
        claim to obey -- a silent violation, and invisible in every other test."""
        assert user_agent(settings).startswith(UA_TOKEN)

    def test_it_is_contactable(self, settings):
        """A compliance requirement rather than a courtesy: a host that wants us
        to stop needs somewhere to say so."""
        agent = user_agent(settings)
        assert settings.support_contact in agent
        assert "+http" in agent


class TestPlausibility:
    def _offer(self, price: str, title: str = "Amul Taaza Milk 500ml"):
        from datetime import UTC, datetime

        from app.adapters.ports import RetailOffer

        return RetailOffer(
            title=title,
            price=Decimal(price),
            seller="example.test",
            link="",
            as_of=datetime.now(UTC),
            provider="scraper",
        )

    def test_a_free_item_is_a_misparse(self):
        assert not parse.plausible(self._offer("0"), peers=[])

    def test_an_absurd_price_is_a_misparse(self):
        assert not parse.plausible(self._offer("99999999999"), peers=[])

    def test_an_untitled_offer_is_refused(self):
        assert not parse.plausible(self._offer("32", title="  "), peers=[])

    def test_the_emi_instalment_is_caught(self):
        """One of the two misparses that actually happen. An instalment is an
        order of magnitude below the peers and looks like a perfectly good
        number to a schema."""
        peers = [Decimal("32"), Decimal("34"), Decimal("30")]
        assert not parse.plausible(self._offer("2.50"), peers=peers)

    def test_the_star_rating_is_caught(self):
        peers = [Decimal("32000"), Decimal("34000"), Decimal("31000")]
        assert not parse.plausible(self._offer("4.5"), peers=peers)

    def test_a_genuine_discount_survives(self):
        """A tighter bound would start rejecting real clearance prices."""
        peers = [Decimal("100"), Decimal("110"), Decimal("105")]
        assert parse.plausible(self._offer("60"), peers=peers)


class TestPriceParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("₹1,299.00", Decimal("1299.00")),
            ("Rs. 45", Decimal("45")),
            ("1299", Decimal("1299")),
            (1299, Decimal("1299")),
            ("", None),
            (None, None),
            ("out of stock", None),
        ],
    )
    def test_it_reads_what_endpoints_actually_return(self, raw, expected):
        assert parse.to_decimal(raw) == expected

    def test_it_never_produces_a_float(self):
        """ADR-003 at the boundary where it is easiest to miss."""
        assert isinstance(parse.to_decimal("1299.99"), Decimal)


class TestTheGateOrder:
    """The whole safety argument, and not testable behaviourally without a real
    host to be impolite to."""

    def test_robots_is_checked_before_the_budget_is_spent(self):
        """A path we were told not to fetch must not even consume a slot.

        Reversing these two lines would spend the day's allowance discovering
        we were not allowed to ask.
        """
        source = inspect.getsource(ScraperOfferSearch.fetch_one)
        assert source.index("robots.policy_for") < source.index("budget.acquire")

    def test_the_http_call_is_below_the_gates_not_beside_them(self):
        source = inspect.getsource(ScraperOfferSearch.fetch_one)
        assert "httpx" not in source, (
            "the HTTP call belongs in _get, below both gates -- inlining it here "
            "is how a gate gets bypassed by the next edit"
        )
        assert "budget.acquire" in source
        assert "if not await" in source

    def test_the_path_narrowing_precedes_both(self):
        """Our own rule is checked before anyone else's, because it is free."""
        source = inspect.getsource(ScraperOfferSearch.fetch_one)
        assert source.index("path_is_allowed") < source.index("robots.policy_for")


class TestTheRulesVersion:
    def test_it_is_recorded_so_bad_output_is_retractable(self):
        """Scraped results are keyed on this. When a rule or an extractor
        changes it changes, and every result those rules produced is retired
        without a migration."""
        assert HOST_RULES_VERSION
        assert len(HOST_RULES_VERSION) >= 8


class TestRelevance:
    """The guard that exists because a filter was silently ignored.

    `prices.openfoodfacts.org` accepts unknown query parameters and drops them.
    A first attempt used `?product_name__like=milk`, got all 301,369 rows back,
    and produced French pasta at entirely plausible prices as "offers for milk".
    Nothing downstream could have caught it: the plausibility gate checks that a
    number is sane, never that a row is relevant.
    """

    def test_an_unfiltered_page_yields_nothing(self):
        payload = {
            "items": [
                {"product_name": "Fusilli", "price": 1.2, "currency": "EUR"},
                {"product_name": "Coudes Rayes", "price": 1.1, "currency": "EUR"},
            ]
        }
        assert parse.open_prices(payload, "milk") == []

    def test_a_matching_row_survives(self):
        payload = {
            "items": [{"product_name": "Amul Taaza Milk 500ml", "price": 32, "currency": "INR"}]
        }
        assert len(parse.open_prices(payload, "milk")) == 1

    def test_every_query_term_must_appear(self):
        """All of them, not any: "amul milk" must not match somebody else's
        milk."""
        assert parse.relevant("Amul Taaza Milk 500ml", "amul milk")
        assert not parse.relevant("Nandini Milk 500ml", "amul milk")

    def test_short_tokens_are_ignored(self):
        """`1l`, `of` and `g` match everything and would wave an unfiltered page
        straight through."""
        assert parse.relevant("Amul Taaza Milk 1L", "milk 1l")

    def test_an_empty_query_disables_the_guard(self):
        """There is nothing to be irrelevant to."""
        assert parse.relevant("anything at all", "")


class TestOpenPricesExtraction:
    def test_it_reads_the_documented_shape(self):
        payload = {
            "items": [
                {
                    "product_code": "8901262010115",
                    "product_name": None,
                    "price": 32.5,
                    "currency": "INR",
                    "date": "2026-08-01",
                    "product": {"product_name": "Amul Taaza Milk", "image_url": "http://x/y.jpg"},
                    "location": {"osm_name": "Nilgiris Adyar"},
                }
            ]
        }
        [offer] = parse.open_prices(payload, "amul")
        assert offer["title"] == "Amul Taaza Milk"
        assert offer["seller"] == "Nilgiris Adyar"
        assert offer["currency"] == "INR"

    def test_a_row_with_no_currency_is_dropped(self):
        """Defaulting a foreign price to rupees would render 0.63 EUR as
        Rs.0.63 -- a silent, confident misstatement, and the worst possible
        failure for a product about comparing prices."""
        payload = {"items": [{"product_name": "Milk", "price": 0.63}]}
        assert parse.open_prices(payload, "milk") == []

    def test_it_survives_junk(self):
        """Somebody else's JSON, which changes without notice."""
        for payload in ({}, {"items": None}, {"items": [None, 3, "x"]}, []):
            assert parse.open_prices(payload, "milk") == []


class TestTheConfiguredHost:
    def test_open_prices_is_known_but_not_enabled(self, settings):
        """Ships disabled. ADR-012's central promise is that a fresh deployment
        cannot fetch anything."""
        assert "prices.openfoodfacts.org" in ALLOWED_HOSTS
        assert enabled_hosts(settings) == ()

    def test_its_extractor_exists(self):
        """A host rule naming an extractor that does not exist is a host that
        fetches and then discards everything."""
        for rule in ALLOWED_HOSTS.values():
            assert parse.resolve(rule.extractor) is not None, rule.extractor

    def test_its_search_url_stays_under_its_allowed_prefixes(self):
        """The rule's own narrowing, checked against itself."""
        from urllib.parse import urlparse

        for rule in ALLOWED_HOSTS.values():
            parsed = urlparse(rule.search_url.format(query="milk"))
            assert parsed.hostname == rule.host
            assert path_is_allowed(rule, parsed.path), rule.search_url
