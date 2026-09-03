"""Shared test fixtures.

Integration tests run against a real Postgres and Redis rather than SQLite:
behaviour must match production, and SQLite silently accepts things Postgres
rejects (partial indexes, CITEXT, NUMERIC semantics).

Locally those come from `docker compose up`; in CI from GitHub Actions
`services:`. Both are reached through the same TEST_DATABASE_URL, so the test
command is identical in either place.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

# Settings validate at import time, so the environment must be populated before
# anything under app.* is imported. Point the app at the *test* database so a
# test run can never touch local development data.
os.environ["DATABASE_URL"] = os.environ.get(
    "TEST_DATABASE_URL", "postgresql+asyncpg://frugal:frugal@localhost:5432/frugal_test"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/1")
os.environ.setdefault("JWT_SECRET", "test-secret-not-used-in-production-0123456789abcdef")
os.environ.setdefault("ENVIRONMENT", "ci")
os.environ.setdefault("LOG_FORMAT", "console")
os.environ.setdefault("LOG_LEVEL", "WARNING")
# Fakes for every external port (ADR-004): no network, no credentials, no
# Tesseract binary needed to run the suite. Assigned, not setdefault -- the
# container's own env sets these to the local MinIO stack, and a test run must
# not depend on it being up.
os.environ["STORAGE_BACKEND"] = "memory"
os.environ["OCR_ENGINE"] = "fake"
# The personalization database (ADR-011). Assigned rather than setdefault, for
# the same reason as the two above: the api container's own environment points
# at the local *development* signals database, and a test run must not touch
# it. Settings refuses to boot if this resolves to the same endpoint as
# DATABASE_URL, so the two names below must stay different.
os.environ["SIGNALS_DATABASE_URL"] = os.environ.get(
    "TEST_SIGNALS_DATABASE_URL",
    "postgresql+asyncpg://frugal:frugal@localhost:5432/frugal_signals_test",
)

from app.core.config import get_settings
from app.main import create_app

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def settings():
    return get_settings()


@pytest.fixture(scope="session", autouse=True)
def migrated_database(request):
    """Bring the test database to head before integration tests run.

    Migrations are applied rather than ``metadata.create_all`` so the schema
    under test is the schema migrations actually produce -- the two drift, and
    the difference only surfaces in production.
    """
    if not request.config.getoption("-m", default="") or "not integration" not in str(
        request.config.getoption("-m")
    ):
        from alembic.command import upgrade
        from alembic.config import Config

        cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
        cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
        upgrade(cfg, "head")

        # The second tree, in its own database with its own history and its own
        # version table (ADR-011). Primary first: nothing here depends on that
        # order, but every other runner in this repo uses it and a test fixture
        # that disagreed would be one more thing to hold in mind.
        signals_cfg = Config(str(BACKEND_ROOT / "alembic_signals.ini"))
        signals_cfg.set_main_option("script_location", str(BACKEND_ROOT / "alembic_signals"))
        upgrade(signals_cfg, "head")

        # The product catalogue is shared reference data that the application
        # syncs on startup -- and `ASGITransport` does not run the lifespan, so
        # the market tests would otherwise face an empty `products` table and
        # every one of them would 404. Synced here for the same reason
        # migrations are: the tests need the world the app runs in.
        import asyncio

        asyncio.run(_sync_catalogue())
        asyncio.run(_assert_reference_data())


async def _assert_reference_data() -> None:
    """Fail loudly if the seeded taxonomy is missing.

    It is seeded by migration 0004, so a database already at head never gets it
    back -- and a fixture that destroys it leaves every later run broken with no
    hint as to why. That happened: a `TRUNCATE ... CASCADE` over a hand-listed
    set of tables started reaching `categories` the moment it gained a foreign
    key to `users`, and the symptom was a dozen unrelated assertions comparing
    against an empty set.

    The remedy is to drop and recreate the test database. Saying so here costs
    one query and saves the next person the hour it cost to find.
    """
    from sqlalchemy import text

    from app.core.database import worker_async_session

    async with worker_async_session() as session:
        seeded = await session.scalar(text("SELECT count(*) FROM categories WHERE user_id IS NULL"))

    if not seeded:
        raise RuntimeError(
            "The system category taxonomy is missing from the test database. It is "
            "seeded by migration 0004 and cannot be restored by re-running migrations. "
            "Drop and recreate the test database to fix it."
        )


async def _sync_catalogue() -> None:
    """Load the catalogue on an engine this call owns.

    `worker_async_session` rather than the cached factory, for the same reason
    Celery tasks use it: `asyncio.run` creates a loop and destroys it, while the
    cached engine's asyncpg connections stay bound to whichever loop opened
    them. pytest-asyncio then runs the tests on a different loop and the first
    query fails with "attached to a different loop".
    """
    from app.core.database import worker_async_session
    from app.modules.market.service import MarketService

    async with worker_async_session() as session:
        await MarketService(session).sync_catalogue()
        await session.commit()


@pytest.fixture(scope="session")
def app(settings):
    return create_app(settings)


@pytest.fixture(autouse=True)
async def isolated_test() -> AsyncIterator[None]:
    """Reset all cross-test state after every test.

    Two concerns, deliberately in one fixture rather than two: pytest-asyncio
    gives each test its own event loop, and both asyncpg and redis connections
    are bound to the loop that opened them. Cleanup must therefore happen
    *before* those connections are released, and splitting this across two
    autouse fixtures makes that ordering implicit and fragile.

    Order matters: clean data while connections are alive, then release them.
    """
    yield

    from sqlalchemy import text

    from app.core.database import get_async_engine
    from app.core.redis import get_redis

    # 1. Truncate tenant data. Rate limiting is stateful in Redis too, so a
    #    test that exhausts a limit would otherwise fail the next one -- and
    #    the failure would look like a bug in the code under test.
    async with get_async_engine().begin() as conn:
        # One statement, because every user-owned table now cascades from
        # `users` (migration 0016). This used to be a hand-maintained list of
        # twelve table names passed to TRUNCATE ... CASCADE, which had gone
        # wrong in both directions: it never cleared insights, forecasts,
        # health_snapshots, notifications, or wishlist_items -- so those leaked
        # between tests -- and once the cascades existed, CASCADE reached
        # `categories` and truncated the system taxonomy along with it.
        #
        # DELETE rather than TRUNCATE for the same reason the cascade is
        # correct: it removes rows whose `user_id` matches a deleted user and
        # leaves the shared taxonomy, which has no owner, alone.
        await conn.execute(text("DELETE FROM users"))
        # No foreign key by design, so nothing cascades into them.
        #
        # The quota ledger and the offer cache are deployment-scoped, not
        # user-scoped -- a free-tier allowance belongs to the install, not to a
        # person -- so `DELETE FROM users` does not reach them. Exactly the leak
        # the comment above describes: without these two lines, a test that
        # exhausts a cap leaves it exhausted for every test after it, and the
        # failure looks like a bug in the code under test.
        await conn.execute(
            text(
                "TRUNCATE audit_log, external_quota_usage, offer_snapshots, erasure_requests, "
                # The crowdsourced price graph is deployment-scoped, not
                # user-scoped: a price at a shop is a fact about the shop, so
                # these tables carry no `user_id` and `DELETE FROM users` does
                # not reach them. Exactly the leak the comment above describes --
                # without these lines a canonical item created by one test
                # collides with the same item in the next, and the failure looks
                # like a bug in the code under test.
                #
                # CASCADE because `price_observations` and the alias tables hang
                # off `canonical_items` and `stores`. `rewards` is absent on
                # purpose: it is seeded reference data, like the category
                # taxonomy, and truncating it would leave every later run broken
                # with no hint as to why.
                "stores, canonical_items, price_observations, receipt_fingerprints, "
                # Scraper state is deployment-scoped too: a host budget spent
                # by one test would refuse the next one's fetch, and the
                # failure would look like a bug in the adapter.
                "scraper_host_budget, robots_policies, scraped_offer_snapshots, "
                "store_confirmations CASCADE"
            )
        )

    # The personalization database has no `users` table and therefore no
    # cascade -- which is the entire design, and also exactly the leak the
    # comment above describes. `DELETE FROM users` reaches nothing over here.
    #
    # Discovered from the metadata rather than listed, so a table added later is
    # cleaned without anyone remembering to add it here. A hand-maintained list
    # is how the twelve-table TRUNCATE above went wrong in both directions.
    if get_settings().personalization_enabled:
        from app.core.signals_base import SignalsBase
        from app.core.signals_database import get_signals_engine

        names = ", ".join(t.name for t in SignalsBase.metadata.sorted_tables)
        if names:
            async with get_signals_engine().begin() as conn:
                # Interpolated, and safe: `names` comes from the declarative
                # metadata, never from input.
                await conn.execute(text(f"TRUNCATE {names}"))

    redis = get_redis()
    # Rate-limit counters, cached aggregates, data versions, and idempotency
    # keys are all cross-test state.
    for pattern in ("rl:*", "agg:*", "dv:*", "idem:*"):
        if keys := await redis.keys(pattern):
            await redis.delete(*keys)

    # 2. Release loop-bound connections and drop the cached clients, so the
    #    next test builds its own against its own loop.
    await get_async_engine().dispose()
    if get_settings().personalization_enabled:
        from app.core.signals_database import get_signals_engine

        await get_signals_engine().dispose()
    await redis.aclose()
    get_redis.cache_clear()


@pytest.fixture(autouse=True)
def _clear_object_store(app):
    """The in-memory store lives on the session-scoped app, so objects would
    otherwise leak between tests."""
    yield
    store = getattr(app.state, "object_store", None)
    if hasattr(store, "objects"):
        store.objects.clear()


@pytest.fixture
async def client(app) -> AsyncIterator[AsyncClient]:
    """HTTP client bound to the ASGI app, with no network involved."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# --- database & auth helpers -----------------------------------------------


@pytest.fixture
async def db_session() -> AsyncIterator[AsyncSession]:  # noqa: F821
    """Direct session for tests that assert on rows rather than responses."""
    from app.core.database import get_async_session_factory

    async with get_async_session_factory()() as session:
        yield session


@pytest.fixture
async def signals_session() -> AsyncIterator[AsyncSession]:  # noqa: F821
    """Direct session on the personalization database (ADR-011)."""
    from app.core.signals_database import get_signals_session_factory

    async with get_signals_session_factory()() as session:
        yield session


# --- auth helpers ----------------------------------------------------------

VALID_PASSWORD = "CorrectHorse9Battery"


def registration_payload(**overrides: object) -> dict[str, object]:
    return {
        "email": "priya@example.com",
        "password": VALID_PASSWORD,
        "display_name": "Priya",
        "base_currency": "INR",
    } | overrides


@pytest.fixture
async def registered(client: AsyncClient) -> dict[str, object]:
    """A registered, signed-in user.

    Returns the token payload and leaves the refresh cookie on the client, so
    tests exercise the same session the browser would hold.
    """
    response = await client.post("/api/v1/auth/register", json=registration_payload())
    assert response.status_code == 201, response.text
    return response.json()


@pytest.fixture
def auth_headers(registered: dict[str, object]) -> dict[str, str]:
    return {"Authorization": f"Bearer {registered['access_token']}"}


@pytest.fixture
async def second_user_headers(client: AsyncClient, registered: dict[str, object]) -> dict[str, str]:
    """A second, unrelated tenant.

    Depends on `registered` so the ordering is deterministic: the first user
    exists before this one, which is what makes "did my data leak into theirs"
    a meaningful question.
    """
    response = await client.post(
        "/api/v1/auth/register",
        json=registration_payload(email="second@example.com", display_name="Arjun"),
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}
