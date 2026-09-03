"""Tests for cross-cutting API behaviour: error envelope, headers, liveness.

These run against the ASGI app in-process, so they need no database.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.errors import (
    ConflictError,
    ErrorCode,
    InsufficientDataError,
    NotFoundError,
    RateLimitedError,
)


class TestLiveness:
    async def test_health_reports_ok(self, client):
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    async def test_health_does_not_touch_dependencies(self, client):
        """Liveness must not check Postgres or Redis -- otherwise a database
        outage causes the orchestrator to kill healthy containers."""
        assert (await client.get("/health")).status_code == 200


class TestRequestId:
    async def test_response_carries_a_request_id(self, client):
        response = await client.get("/health")
        assert response.headers.get("X-Request-ID")

    async def test_client_supplied_id_is_echoed(self, client):
        """Lets a trace span the frontend and backend."""
        response = await client.get("/health", headers={"X-Request-ID": "trace-abc-123"})
        assert response.headers["X-Request-ID"] == "trace-abc-123"

    async def test_response_time_is_reported(self, client):
        assert float((await client.get("/health")).headers["X-Response-Time-Ms"]) >= 0


class TestErrorEnvelope:
    """Every non-2xx response has the identical shape, so the client needs
    exactly one error path -- including for framework-raised errors, which are
    the ones it can least predict."""

    async def test_unknown_route_uses_the_standard_envelope(self, client):
        response = await client.get("/api/v1/does-not-exist")
        assert response.status_code == 404

        body = response.json()
        assert set(body) == {"error"}, f"leaked a non-envelope shape: {body}"
        assert body["error"]["code"] == "NOT_FOUND"
        assert body["error"]["request_id"]
        assert body["error"]["docs_url"].endswith("/NOT_FOUND")

    async def test_wrong_method_uses_the_standard_envelope(self, client):
        response = await client.post("/health")
        assert response.status_code == 405
        assert set(response.json()) == {"error"}

    async def test_envelope_never_uses_fastapis_detail_shape(self, client):
        """Guards the specific regression: Starlette's default handler emits
        {"detail": ...}, which bypasses the contract entirely."""
        body = (await client.get("/api/v1/does-not-exist")).json()
        assert "detail" not in body

    async def test_security_headers_are_present(self, client):
        headers = (await client.get("/health")).headers
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert headers["X-Frame-Options"] == "DENY"


class TestDomainErrors:
    """Each error carries its own status and code, so handlers never map
    exceptions to status codes by hand -- that mapping drifts."""

    @pytest.mark.parametrize(
        ("error", "status", "code"),
        [
            (NotFoundError("Transaction"), 404, ErrorCode.NOT_FOUND),
            (ConflictError("Duplicate"), 409, ErrorCode.CONFLICT),
            (RateLimitedError(60), 429, ErrorCode.RATE_LIMITED),
            (InsufficientDataError("Not enough history"), 503, ErrorCode.INSUFFICIENT_DATA),
        ],
    )
    def test_status_and_code_pairing(self, error, status, code):
        assert error.status_code == status
        assert error.code == code

    def test_not_found_hides_whether_the_resource_exists(self):
        """A row owned by another user must be indistinguishable from a missing
        one; 403 would confirm the ID exists."""
        assert NotFoundError("Transaction").message == "Transaction not found"

    def test_rate_limit_includes_retry_after(self):
        assert RateLimitedError(90).headers["Retry-After"] == "90"

    def test_insufficient_data_carries_caveats(self):
        error = InsufficientDataError(
            "Need more history", caveats=["Only 10 days recorded; 14 required."]
        )
        assert error.details[0].issue.startswith("Only 10 days")

    def test_envelope_includes_a_docs_url(self):
        body = NotFoundError("Goal").to_response("req-1").error
        assert body.docs_url == "https://docs.frugal.app/errors/NOT_FOUND"
        assert body.request_id == "req-1"


class TestUnhandledErrors:
    """An unexpected 500 has to reach a cross-origin browser intact.

    The envelope was never the problem -- it has always been correct in the
    body. The problem was that a browser could not read it. Starlette installs
    a handler registered for bare `Exception` on `ServerErrorMiddleware`, the
    outermost layer, so its response was built after `CORSMiddleware` had been
    unwound and left without an `Access-Control-Allow-Origin` header. Chrome
    discards such a response and reports a CORS violation, which is a message
    about the wrong subsystem entirely: six E2E specs failed for two milestones
    on what looked like a CORS misconfiguration and was an unmigrated database.

    The body assertions below would pass without the fix. The header assertion
    is the regression guard.
    """

    @staticmethod
    def _client(settings) -> AsyncClient:
        from app.main import create_app

        app = create_app(settings)

        @app.get("/api/v1/_explode", include_in_schema=False)
        async def explode() -> None:
            raise RuntimeError("nobody anticipated this")

        # `raise_app_exceptions=False` so a regression shows up as a failed
        # assertion on the response rather than a RuntimeError out of the
        # transport, which says much less about what broke.
        return AsyncClient(
            transport=ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://test",
        )

    async def test_unhandled_error_is_readable_cross_origin(self, settings):
        origin = settings.cors_origins[0]

        async with self._client(settings) as client:
            response = await client.get("/api/v1/_explode", headers={"Origin": origin})

        assert response.status_code == 500
        assert response.headers["access-control-allow-origin"] == origin

        body = response.json()["error"]
        assert body["code"] == ErrorCode.INTERNAL_ERROR.value
        # Internals never leak; the request id is what support is given instead.
        assert body["message"] == "An unexpected error occurred"
        assert body["request_id"]
        assert response.headers["x-request-id"] == body["request_id"]

    async def test_the_request_id_is_exposed_to_the_browser(self, settings):
        """A header the browser cannot read is not a support handle."""
        origin = settings.cors_origins[0]

        async with self._client(settings) as client:
            response = await client.get("/api/v1/_explode", headers={"Origin": origin})

        exposed = response.headers["access-control-expose-headers"]
        assert "X-Request-ID" in exposed
