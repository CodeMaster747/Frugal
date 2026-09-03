"""Domain exceptions and the single error envelope.

Every non-2xx response has an identical shape, so the client has exactly one
error path. See docs/04-api-design.md section 2.3.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    TOKEN_REUSE_DETECTED = "TOKEN_REUSE_DETECTED"  # noqa: S105 — an error code, not a credential
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    UNPROCESSABLE = "UNPROCESSABLE"
    RATE_LIMITED = "RATE_LIMITED"
    INTERNAL_ERROR = "INTERNAL_ERROR"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"
    SERVICE_PAUSED = "SERVICE_PAUSED"


class ErrorDetail(BaseModel):
    field: str | None = None
    issue: str
    value: str | None = None


class ErrorBody(BaseModel):
    code: ErrorCode
    message: str
    details: list[ErrorDetail] = Field(default_factory=list)
    request_id: str | None = None
    docs_url: str | None = None

    #: Why the answer is absent, in prose, when refusing *is* the answer.
    #:
    #: Distinct from `details`, which is field-level ("amount: must be
    #: positive"). A caveat is about the response as a whole: an engine that
    #: declined for want of history, or a metered dependency that has stopped.
    #: The frontend has read `error.caveats` since M7 -- see
    #: `frontend/src/lib/api/client.ts` -- but nothing ever sent it, so the
    #: forecast page's declined panel has been rendering an empty reason list.
    caveats: list[str] = Field(default_factory=list)


class ErrorResponse(BaseModel):
    """The only error shape the API emits."""

    error: ErrorBody


class AppError(Exception):
    """Base for all domain errors.

    Carries its own HTTP status so handlers never map exceptions to codes by
    hand -- that mapping drifts, and drifted status codes break clients silently.
    """

    status_code: int = 500
    code: ErrorCode = ErrorCode.INTERNAL_ERROR

    def __init__(
        self,
        message: str,
        *,
        details: list[ErrorDetail] | None = None,
        caveats: list[str] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or []
        self.caveats = caveats or []
        self.headers = headers or {}

    def to_response(self, request_id: str | None = None) -> ErrorResponse:
        return ErrorResponse(
            error=ErrorBody(
                code=self.code,
                message=self.message,
                details=self.details,
                caveats=self.caveats,
                request_id=request_id,
                docs_url=f"https://docs.frugal.app/errors/{self.code.value}",
            )
        )


class ValidationError(AppError):
    status_code = 400
    code = ErrorCode.VALIDATION_ERROR


class UnauthenticatedError(AppError):
    status_code = 401
    code = ErrorCode.UNAUTHENTICATED

    def __init__(self, message: str = "Authentication required", **kw: Any) -> None:
        super().__init__(message, **kw)


class TokenReuseError(AppError):
    """A refresh token was replayed, so the whole family is revoked (FR-1.3)."""

    status_code = 401
    code = ErrorCode.TOKEN_REUSE_DETECTED

    def __init__(
        self, message: str = "Session invalidated; please sign in again", **kw: Any
    ) -> None:
        super().__init__(message, **kw)


class ForbiddenError(AppError):
    status_code = 403
    code = ErrorCode.FORBIDDEN


class NotFoundError(AppError):
    """Also returned when a resource belongs to another user.

    Returning 403 there would confirm the ID exists, which is an enumeration
    oracle across the whole system.
    """

    status_code = 404
    code = ErrorCode.NOT_FOUND

    def __init__(self, resource: str = "Resource", **kw: Any) -> None:
        super().__init__(f"{resource} not found", **kw)


class ConflictError(AppError):
    status_code = 409
    code = ErrorCode.CONFLICT


class UnprocessableError(AppError):
    """Well-formed input rejected by a domain rule."""

    status_code = 422
    code = ErrorCode.UNPROCESSABLE


class RateLimitedError(AppError):
    status_code = 429
    code = ErrorCode.RATE_LIMITED

    def __init__(self, retry_after_seconds: int, **kw: Any) -> None:
        headers = {"Retry-After": str(retry_after_seconds), **kw.pop("headers", {})}
        super().__init__("Too many requests", headers=headers, **kw)


class InsufficientDataError(AppError):
    """An engine declines rather than fabricating a result.

    The API-level expression of the product thesis: a number that would be
    meaningless is worse than no number, so the engine refuses and the client
    renders the caveats instead.
    """

    status_code = 503
    code = ErrorCode.INSUFFICIENT_DATA

    def __init__(self, message: str, *, caveats: list[str] | None = None, **kw: Any) -> None:
        caveats = caveats or []
        # `details` is populated too, and stays populated. Caveats lived there
        # alone until the envelope grew a field for them, and a client that
        # learned to read them from `details` should not break on the day the
        # right field appears.
        super().__init__(
            message,
            details=[ErrorDetail(issue=c) for c in caveats],
            caveats=caveats,
            **kw,
        )


class ExternalServicePausedError(AppError):
    """A metered dependency has spent its free allowance and stopped.

    Distinct from INSUFFICIENT_DATA, which is about the user's own history
    being too thin. This is about *us*: a capability the product paid nothing
    for is unavailable until the period rolls over, and no amount of user data
    changes that. The distinction matters because the remedies differ -- one
    says "come back when you have more transactions", the other says "contact
    the developer".

    Prefer degrading to a fallback over raising this. Reserve it for endpoints
    whose entire answer depends on the paused service; see
    `docs/adr/008-external-quota-and-degradation.md`.
    """

    status_code = 503
    code = ErrorCode.SERVICE_PAUSED

    def __init__(self, service: str, *, contact: str | None = None, **kw: Any) -> None:
        caveats = [f"{service} is paused: its free allowance for this period is used up."]
        if contact:
            caveats.append(f"Please contact the developer at {contact} if you need this restored.")
        super().__init__(f"{service} is temporarily unavailable", caveats=caveats, **kw)
