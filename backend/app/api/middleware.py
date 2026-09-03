"""Request-scoped middleware and exception handlers."""

from __future__ import annotations

import time

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from starlette.datastructures import Headers
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import (
    AppError,
    ErrorBody,
    ErrorCode,
    ErrorDetail,
    ErrorResponse,
)
from app.core.logging import bind_request_id, bind_user_id, get_logger, get_request_id

logger = get_logger(__name__)

REQUEST_ID_HEADER = "X-Request-ID"

# Framework-raised HTTP errors (unmatched route, wrong method, malformed auth
# header) never reach our domain exceptions, so they are mapped explicitly.
# Without this they leave as Starlette's {"detail": ...}, and the client would
# need a second error path for exactly the responses it can least predict.
_STATUS_TO_CODE = {
    400: ErrorCode.VALIDATION_ERROR,
    401: ErrorCode.UNAUTHENTICATED,
    403: ErrorCode.FORBIDDEN,
    404: ErrorCode.NOT_FOUND,
    405: ErrorCode.NOT_FOUND,  # a method that does not exist on a path is a miss
    409: ErrorCode.CONFLICT,
    422: ErrorCode.UNPROCESSABLE,
    429: ErrorCode.RATE_LIMITED,
    503: ErrorCode.INSUFFICIENT_DATA,
}


# `geolocation=(self)` and `camera=(self)` are grants, and narrow ones: `self`
# is this origin only, so a third-party frame still cannot ask. Both are load
# bearing -- the map at `/` centres on the user, and receipt capture is a photo
# of a piece of paper. Before M14 this header denied all three outright, which
# made those two features impossible to build without anyone noticing why.
#
# `microphone` stays fully denied. Nothing in the product records audio, and a
# permission nothing needs is pure attack surface.
_SECURITY_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy", b"geolocation=(self), camera=(self), microphone=()"),
]


class RequestContextMiddleware:
    """Binds a request ID, adds security headers, times the request, and logs it.

    Written as raw ASGI rather than Starlette's ``BaseHTTPMiddleware``, for
    three reasons that all point the same way:

    1. ``BaseHTTPMiddleware`` runs the downstream app in a *child anyio task*.
       ContextVars set here would then be set in a different task from the one
       handling the request -- and request-ID propagation is the whole point of
       this middleware (NFR-4).
    2. That child task also defeats coverage tracing, so everything past the
       first ``await`` in a handler measured as unexecuted. Untrustworthy
       coverage is worse than none: it hides what is genuinely untested.
    3. It allocates a task and a memory object stream per request. On a 1 GB
       instance that overhead is real and buys nothing here.

    Both concerns live in one class because each additional middleware is
    another wrapper on the hot path, and these two always run together.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        request_id = bind_request_id(headers.get(REQUEST_ID_HEADER))
        bind_user_id(None)
        scope.setdefault("state", {})["request_id"] = request_id

        start = time.perf_counter()
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                raw = list(message.get("headers", []))
                present = {name.lower() for name, _ in raw}

                raw.append((b"x-request-id", request_id.encode()))
                elapsed = (time.perf_counter() - start) * 1000
                raw.append((b"x-response-time-ms", f"{elapsed:.2f}".encode()))
                raw.extend((n, v) for n, v in _SECURITY_HEADERS if n not in present)

                message = {**message, "headers": raw}
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            logger.exception(
                "request failed",
                extra={
                    "method": scope.get("method"),
                    "path": scope.get("path"),
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )
            raise

        path = scope.get("path", "")
        # Health probes run constantly and would drown the log at INFO.
        if not path.startswith("/health"):
            logger.info(
                "request completed",
                extra={
                    "method": scope.get("method"),
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 2),
                },
            )


def _envelope(
    status_code: int, body: ErrorBody, headers: dict[str, str] | None = None
) -> JSONResponse:
    payload = ErrorResponse(error=body).model_dump(mode="json")
    response = JSONResponse(status_code=status_code, content=payload, headers=headers)
    if request_id := get_request_id():
        response.headers[REQUEST_ID_HEADER] = request_id
    return response


def register_exception_handlers(app: FastAPI) -> None:
    """Install handlers so every error leaves as the same envelope."""

    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        response = exc.to_response(get_request_id())
        if exc.status_code >= 500:
            logger.exception("domain error", extra={"error_code": exc.code.value})
        else:
            logger.warning(
                "domain error",
                # Never `message` here: it is a reserved LogRecord attribute,
                # and passing it in `extra` makes logging raise KeyError --
                # which would turn every 4xx into a 500.
                extra={"error_code": exc.code.value, "error_message": exc.message},
            )
        return _envelope(exc.status_code, response.error, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            ErrorDetail(
                field=".".join(str(p) for p in err["loc"][1:]) or None,
                issue=err["msg"],
                value=str(err.get("input"))[:200] if err.get("input") is not None else None,
            )
            for err in exc.errors()
        ]
        return _envelope(
            400,
            ErrorBody(
                code=ErrorCode.VALIDATION_ERROR,
                message="Request validation failed",
                details=details,
                request_id=get_request_id(),
                docs_url="https://docs.frugal.app/errors/VALIDATION_ERROR",
            ),
        )

    @app.exception_handler(IntegrityError)
    async def handle_integrity_error(_: Request, exc: IntegrityError) -> JSONResponse:
        """Database constraint violations become 409, never 500.

        Constraints are the authority on data integrity, so reaching one is not
        a server fault -- it is the database refusing a write the caller asked
        for. Services validate ahead of time for a clearer message; this is the
        backstop that keeps any path we have not anticipated from leaking a
        stack trace as a 500.
        """
        logger.warning("integrity constraint violated", extra={"constraint": str(exc.orig)[:200]})
        return _envelope(
            409,
            ErrorBody(
                code=ErrorCode.CONFLICT,
                message="This change conflicts with existing data",
                request_id=get_request_id(),
                docs_url="https://docs.frugal.app/errors/CONFLICT",
            ),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_TO_CODE.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        return _envelope(
            exc.status_code,
            ErrorBody(
                code=code,
                message=str(exc.detail),
                request_id=get_request_id(),
                docs_url=f"https://docs.frugal.app/errors/{code.value}",
            ),
            dict(exc.headers) if exc.headers else None,
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Never leak internals. The request ID is the support handle.
        logger.exception("unhandled exception", extra={"error_type": type(exc).__name__})
        return _envelope(
            500,
            ErrorBody(
                code=ErrorCode.INTERNAL_ERROR,
                message="An unexpected error occurred",
                request_id=get_request_id(),
                docs_url="https://docs.frugal.app/errors/INTERNAL_ERROR",
            ),
        )


def register_middleware(app: FastAPI) -> None:
    app.add_middleware(RequestContextMiddleware)
