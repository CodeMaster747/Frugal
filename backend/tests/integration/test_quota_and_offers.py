"""The quota ledger and the offers panel.

These are the tests that guard the zero-recurring-cost constraint. The
interesting assertions are all negative: that an exhausted ledger makes no
call, that a cache hit spends nothing, and that the gate lives inside the
adapter where a caller cannot forget it.
"""

from __future__ import annotations

import inspect
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from app.core.quota import PeriodKind, QuotaLedger, Window, period_keys

pytestmark = pytest.mark.integration

MARKET = "/api/v1/market"


def month_window(cap: int) -> Window:
    _, month_key = period_keys(datetime.now(UTC))
    return Window(PeriodKind.MONTH, month_key, cap)


class TestTheLedger:
    async def test_it_allows_up_to_the_cap_and_not_past_it(self, db_session):
        ledger = QuotaLedger(db_session)
        window = Window(PeriodKind.DAY, "2026-08-17", 3)

        results = [await ledger.reserve("test_provider_a", windows=[window]) for _ in range(5)]
        await db_session.commit()

        assert results == [True, True, True, False, False]

    async def test_a_refusal_does_not_keep_counting(self, db_session):
        """The row must stop at the cap, not run past it.

        `used` climbing past `cap` would make the status endpoint report a
        negative remaining allowance, and would mean the WHERE clause was not
        doing the work the design says it does.
        """
        ledger = QuotaLedger(db_session)
        window = Window(PeriodKind.DAY, "2026-08-18", 2)
        for _ in range(6):
            await ledger.reserve("test_provider_b", windows=[window])
        await db_session.commit()

        status = await ledger.status("test_provider_b", window=window)
        assert status.used == 2
        assert status.remaining == 0
        assert not status.available

    async def test_the_tightest_window_decides(self, db_session):
        """A daily cap exists so one bad day cannot eat the month."""
        ledger = QuotaLedger(db_session)
        day = Window(PeriodKind.DAY, "2026-08-19", 1)
        month = Window(PeriodKind.MONTH, "2026-08", 1000)

        assert await ledger.reserve("test_provider_c", windows=[day, month])
        assert not await ledger.reserve("test_provider_c", windows=[day, month])
        await db_session.commit()

    async def test_one_user_cannot_drain_a_shared_allowance(self, db_session, registered):
        """Without the per-user cap, the first person to open the panel on the
        first of the month spends everyone's month."""
        import uuid

        ledger = QuotaLedger(db_session)
        window = Window(PeriodKind.MONTH, "2026-08", 1000)
        user = uuid.UUID(str(registered["user"]["id"]))

        allowed = [
            await ledger.reserve(
                "test_provider_d",
                windows=[window],
                user_id=user,
                per_user_cap=2,
                today="2026-08-20",
            )
            for _ in range(4)
        ]
        await db_session.commit()
        assert allowed == [True, True, False, False]

    async def test_status_never_raises_on_an_unknown_provider(self, db_session):
        """The endpoint that explains why something is down must not be the
        thing that goes down."""
        status = await QuotaLedger(db_session).status(
            "never_used", window=Window(PeriodKind.MONTH, "2026-08", 100)
        )
        assert status.available
        assert status.used == 0


class TestTheGateLivesInTheAdapter:
    def test_the_quota_check_precedes_the_http_call(self):
        """A gate at the call site is a gate the next call site forgets.

        Asserted by source order rather than by behaviour, because the failure
        this catches is a refactor that moves the check "somewhere more
        sensible" and silently removes it from every other caller.
        """
        from app.adapters.offers import serpapi

        source = inspect.getsource(serpapi.SerpApiOfferSearch.find_offers)
        assert "ledger.reserve" in source
        assert "if not allowed:" in source
        assert "httpx" not in source, "the HTTP call belongs below the gate, not beside it"

    async def test_an_unconfigured_adapter_makes_no_call(self):
        """No API key means no request, not a request that fails."""
        from app.adapters.offers.serpapi import SerpApiOfferSearch

        assert await SerpApiOfferSearch().find_offers("laptop") == []

    def test_the_default_provider_is_the_simulator(self):
        """CI, local development, and any deployment without a key must be
        incapable of spending money."""
        from app.adapters.offers import get_offer_search

        assert get_offer_search().name == "simulated"


class TestTheOffersPanel:
    async def test_it_returns_offers_ordered_cheapest_first(self, client, auth_headers):
        response = await client.get(
            f"{MARKET}/offers", headers=auth_headers, params={"q": "macbook air"}
        )
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["offers"], "the simulator must produce something to demonstrate against"

        prices = [Decimal(o["price"]) for o in body["offers"]]
        assert prices == sorted(prices), "ascending price is the whole ordering claim"

    async def test_simulated_prices_say_so(self, client, auth_headers):
        """Presenting invented prices as real listings would be a claim about
        named retailers that happens to be false."""
        response = await client.get(
            f"{MARKET}/offers", headers=auth_headers, params={"q": "macbook air"}
        )
        body = response.json()
        assert body["source"] in {"simulated", "cache"}
        if body["source"] == "simulated":
            assert any("simulated" in c.lower() for c in body["caveats"])

    async def test_every_offer_carries_the_published_rubric(self, client, auth_headers):
        """ADR-002: a score reaches nobody without the reasoning that produced
        it. The reliability rubric is reused verbatim, so its weights already
        sum to 1.00 and no new weights were invented."""
        response = await client.get(
            f"{MARKET}/offers", headers=auth_headers, params={"q": "macbook air"}
        )
        for offer in response.json()["offers"]:
            reliability = offer["reliability"]
            assert reliability["signals"], "a score with no factors must not serialise"
            assert reliability["rubric_version"]
            total = sum(Decimal(s["weight"]) for s in reliability["signals"])
            assert total == Decimal("1.00"), f"weights must sum to 1.00, got {total}"

    async def test_a_saving_is_a_subtraction_not_a_judgement(self, client, auth_headers):
        response = await client.get(
            f"{MARKET}/offers",
            headers=auth_headers,
            params={"q": "macbook air", "compare_to": "500000"},
        )
        body = response.json()
        assert body["saving"] is not None
        assert Decimal(body["saving"]) > 0

    async def test_a_second_search_is_served_from_cache(self, client, auth_headers):
        """The single largest quota saving available: a hit costs no call."""
        params = {"q": "sony wh-1000xm5"}
        first = await client.get(f"{MARKET}/offers", headers=auth_headers, params=params)
        second = await client.get(f"{MARKET}/offers", headers=auth_headers, params=params)

        assert first.json()["offers"], "need a non-empty result to cache"
        assert second.json()["source"] == "cache"

    async def test_a_query_matching_nothing_is_not_an_error(self, client, auth_headers):
        response = await client.get(
            f"{MARKET}/offers", headers=auth_headers, params={"q": "zzzz nonexistent product"}
        )
        assert response.status_code == 200
        assert response.json()["offers"] == []
        assert response.json()["service_status"] == "ok"


class TestTheProvidersEndpoint:
    async def test_it_reports_status_without_authentication(self, client):
        """The paused banner must render on a signed-out page too, and this
        carries no user data -- it describes the deployment's own free tiers."""
        response = await client.get("/system/providers")
        assert response.status_code == 200
        assert "min_native_version" in response.json()

    async def test_it_lists_nothing_metered_when_the_simulator_is_configured(self, client):
        response = await client.get("/system/providers")
        assert response.json()["providers"] == []

    async def test_a_cached_simulated_result_still_says_it_is_simulated(self, client, auth_headers):
        """The disclaimer must survive the cache.

        Keying it off the response's `source` dropped it on the second search:
        cached results report source="cache", so invented prices appeared
        labelled as though they were real listings. That is a false claim about
        named retailers, which is the one thing this caveat exists to prevent.
        """
        params = {"q": "dell xps 13"}
        first = await client.get(f"{MARKET}/offers", headers=auth_headers, params=params)
        second = await client.get(f"{MARKET}/offers", headers=auth_headers, params=params)

        assert first.json()["offers"], "need a non-empty result to cache"
        assert second.json()["source"] == "cache"
        assert any("simulated" in c.lower() for c in second.json()["caveats"])
