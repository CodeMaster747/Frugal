"""Personalization end to end: ledger in, profile out (ADR-011).

The property that matters most here is not that the numbers are right -- the
rubric is tested with no database in `tests/unit/test_personalization_derive.py`
-- but that the two databases stay separate while the feature works: signals are
derived by reading the ledger through `FinanceService`, written to the other
database, and only aggregates come back.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration


async def _account(client, headers) -> str:
    response = await client.post(
        "/api/v1/accounts",
        headers=headers,
        json={"name": "Everyday", "type": "bank", "currency": "INR", "opening_balance": "0.00"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _spend(client, headers, account_id: str, amounts: list[str]) -> None:
    """A spread history, so a top decile actually exists."""
    start = date.today() - timedelta(days=len(amounts) * 7)
    rows = [
        {
            "account_id": account_id,
            "kind": "expense",
            "amount": amount,
            "currency": "INR",
            "occurred_on": (start + timedelta(days=i * 7)).isoformat(),
            "merchant_raw": f"Shop {i % 4}",
        }
        for i, amount in enumerate(amounts)
    ]
    # A bare list: `bulk_create` takes `list[TransactionCreate]`, and 207 is a
    # success here -- it reports per-row outcomes rather than failing the batch.
    response = await client.post("/api/v1/transactions/bulk", headers=headers, json=rows)
    assert response.status_code in (200, 201, 207), response.text


class TestTheProfile:
    async def test_it_is_empty_before_anything_is_derived(self, client, auth_headers, settings):
        if not settings.personalization_enabled:
            pytest.skip("personalization is not configured")

        response = await client.get("/api/v1/personalization/profile", headers=auth_headers)
        assert response.status_code == 200, response.text
        body = response.json()

        assert body["available"] is False
        assert body["caveats"], "an empty profile must say why it is empty"

    async def test_it_builds_from_the_ledger(self, client, auth_headers, settings):
        if not settings.personalization_enabled:
            pytest.skip("personalization is not configured")

        account = await _account(client, auth_headers)
        await _spend(client, auth_headers, account, [str(500 + n * 400) for n in range(30)])

        refreshed = await client.post("/api/v1/personalization/refresh", headers=auth_headers)
        assert refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["profile_recomputed"] is True

        body = (await client.get("/api/v1/personalization/profile", headers=auth_headers)).json()

        assert body["available"] is True
        assert body["signal_count"] > 0
        assert body["big_purchase_threshold"] is not None
        assert body["factors"], "ADR-002: a profile with no factors is a bug"

    async def test_refreshing_twice_does_not_duplicate_signals(
        self, client, auth_headers, signals_session, settings
    ):
        if not settings.personalization_enabled:
            pytest.skip("personalization is not configured")

        account = await _account(client, auth_headers)
        await _spend(client, auth_headers, account, [str(500 + n * 400) for n in range(30)])

        first = (await client.post("/api/v1/personalization/refresh", headers=auth_headers)).json()
        second = (await client.post("/api/v1/personalization/refresh", headers=auth_headers)).json()

        assert first["signals_written"] > 0
        assert second["signals_written"] == 0, "re-derivation must update, not duplicate"
        assert second["signals_skipped"] == first["signals_written"]

    async def test_amounts_are_strings_on_the_wire(self, client, auth_headers, settings):
        """ADR-003. A float here would round somebody's threshold."""
        if not settings.personalization_enabled:
            pytest.skip("personalization is not configured")

        account = await _account(client, auth_headers)
        await _spend(client, auth_headers, account, [str(500 + n * 400) for n in range(30)])
        await client.post("/api/v1/personalization/refresh", headers=auth_headers)

        body = (await client.get("/api/v1/personalization/profile", headers=auth_headers)).json()
        assert isinstance(body["big_purchase_threshold"], str)
        assert isinstance(body["confidence"], str)
        assert all(isinstance(v, str) for v in body["category_affinities"].values())


class TestTenancy:
    async def test_one_users_profile_is_not_anothers(
        self, client, auth_headers, second_user_headers, settings
    ):
        if not settings.personalization_enabled:
            pytest.skip("personalization is not configured")

        account = await _account(client, auth_headers)
        await _spend(client, auth_headers, account, [str(500 + n * 400) for n in range(30)])
        await client.post("/api/v1/personalization/refresh", headers=auth_headers)

        theirs = (
            await client.get("/api/v1/personalization/profile", headers=second_user_headers)
        ).json()
        assert theirs["available"] is False
        assert theirs["signal_count"] == 0

    async def test_the_profile_never_returns_an_individual_signal(
        self, client, auth_headers, settings
    ):
        """The module's contract with the rest of the system.

        Raw purchase signals do not leave it -- that is the reason they are in a
        database of their own, and a response that leaked one would make the
        separation pointless.
        """
        if not settings.personalization_enabled:
            pytest.skip("personalization is not configured")

        account = await _account(client, auth_headers)
        await _spend(client, auth_headers, account, [str(500 + n * 400) for n in range(30)])
        await client.post("/api/v1/personalization/refresh", headers=auth_headers)

        body = (await client.get("/api/v1/personalization/profile", headers=auth_headers)).json()

        assert "signals" not in body
        assert "merchant_normalized" not in str(body)
        assert "source_digest" not in str(body)


class TestTheRubricIsPublished:
    async def test_archetypes_are_readable(self, client, auth_headers, settings):
        """Same commitment as `GET /health-score/rubric`: a user told they match
        a pattern can read what the pattern means."""
        if not settings.personalization_enabled:
            pytest.skip("personalization is not configured")

        body = (await client.get("/api/v1/personalization/archetypes", headers=auth_headers)).json()

        assert body["archetypes"]
        for archetype in body["archetypes"]:
            assert archetype["slug"] and archetype["label"] and archetype["description"]
        assert Decimal(body["match_threshold"]) > 0


class TestTheSignalsStayPut:
    async def test_derived_signals_are_in_the_other_database(
        self, client, auth_headers, db_session, signals_session, settings
    ):
        if not settings.personalization_enabled:
            pytest.skip("personalization is not configured")

        account = await _account(client, auth_headers)
        await _spend(client, auth_headers, account, [str(500 + n * 400) for n in range(30)])
        await client.post("/api/v1/personalization/refresh", headers=auth_headers)

        over_there = (
            await signals_session.execute(text("SELECT count(*) FROM purchase_signals"))
        ).scalar_one()
        assert over_there > 0

        # And nothing of the sort exists on this side.
        here = (
            await db_session.execute(
                text("SELECT to_regclass('public.purchase_signals') IS NOT NULL")
            )
        ).scalar_one()
        assert here is False
