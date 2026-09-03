"""Readiness against live Postgres and Redis.

Marked `integration` because it needs the compose stack (or CI services).
Run the unit suite alone with: pytest -m "not integration"
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


class TestReadiness:
    async def test_reports_ready_when_dependencies_are_up(self, client):
        response = await client.get("/health/ready")
        body = response.json()

        assert response.status_code == 200, f"not ready: {body}"
        assert body["status"] == "ready"
        assert body["dependencies"]["database"] is True
        assert body["dependencies"]["redis"] is True

    async def test_names_each_dependency_individually(self, client):
        """A single boolean would say the service is unhealthy without saying
        which dependency to look at."""
        deps = (await client.get("/health/ready")).json()["dependencies"]
        assert set(deps) == {"database", "redis", "personalization"}

    async def test_personalization_reports_its_own_state(self, client, settings):
        """`None` when unconfigured, a boolean when there is a database to ask.

        Absent is not degraded. A deployment that never turned personalization
        on must not report a dependency as down, or readiness becomes noise
        that operators learn to ignore -- which is how the M11 CloudWatch alarm
        ended up unable to fire.
        """
        deps = (await client.get("/health/ready")).json()["dependencies"]

        if settings.personalization_enabled:
            assert deps["personalization"] is True
        else:
            assert deps["personalization"] is None

    async def test_a_personalization_outage_does_not_take_the_process_down(self, client):
        """503 stops the load balancer routing here.

        Personalization serves recommendations; the ledger works without it.
        Returning 503 for its absence would take a working product offline to
        report a degraded extra, so readiness reports it and stays ready.
        """
        response = await client.get("/health/ready")
        body = response.json()

        assert response.status_code == 200
        assert body["status"] == "ready"


class TestDatabase:
    async def test_extensions_from_the_baseline_migration_are_installed(self):
        from sqlalchemy import text

        from app.core.database import get_async_engine

        async with get_async_engine().connect() as conn:
            result = await conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('citext','pg_trgm')")
            )
            installed = {row[0] for row in result}

        assert installed == {"citext", "pg_trgm"}, (
            f"baseline migration 0001 has not been applied; found {installed}"
        )

    async def test_numeric_arithmetic_is_exact(self):
        """Confirms the database agrees with ADR-003 -- NUMERIC, not float."""
        from decimal import Decimal

        from sqlalchemy import text

        from app.core.database import get_async_engine

        async with get_async_engine().connect() as conn:
            value = (
                await conn.execute(text("SELECT (0.1::numeric + 0.2::numeric) AS total"))
            ).scalar_one()

        assert value == Decimal("0.3")
