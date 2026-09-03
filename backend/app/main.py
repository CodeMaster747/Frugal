"""Application factory and composition root.

Adapters are selected here from configuration and injected as dependencies
(ADR-004), so services depend on protocols and never on concrete
implementations. That is what lets the whole suite run with fakes -- no
network, no credentials, no Tesseract binary.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.middleware import (
    CatchAllMiddleware,
    register_exception_handlers,
    register_middleware,
)
from app.api.system import router as system_router
from app.api.v1 import api_router
from app.core.config import Settings, get_settings
from app.core.database import dispose_engines
from app.core.logging import configure_logging, get_logger
from app.core.redis import close_redis
from app.core.signals_database import dispose_signals_engine

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    logger.info(
        "starting up",
        extra={
            "environment": settings.environment,
            "storage_backend": settings.storage_backend,
            "ocr_engine": settings.ocr_engine,
            "price_provider": settings.price_provider,
        },
    )
    # The product catalogue is shared reference data with no tenant, and every
    # market feature needs rows in `products` to exist. Syncing on startup is
    # idempotent (upsert on `external_id`, ~130 rows) and means a fresh database
    # is usable without a separate provisioning step — the alternative is a
    # feature that silently returns nothing until someone remembers to run a
    # command.
    try:
        from app.core.database import get_async_session_factory
        from app.modules.market.service import MarketService

        async with get_async_session_factory()() as session:
            summary = await MarketService(session).sync_catalogue()
            await session.commit()
        logger.info("catalogue synced", extra=summary)
    except Exception as exc:
        # Every other feature works without it; market pages will be empty and
        # say so, which beats refusing to boot.
        logger.warning("catalogue sync failed; market features will be empty", exc_info=exc)

    yield
    logger.info("shutting down")
    await dispose_engines()
    # The composition root is the one place outside the personalization
    # module that may name the second engine, and this is the reason:
    # somebody has to close the pool. It builds nothing on a deployment
    # that never used it.
    await dispose_signals_engine()
    await close_redis()


def _build_object_store(settings: Settings) -> object:
    """Choose the storage adapter.

    `s3` covers AWS S3, Cloudflare R2, Backblaze, and MinIO -- they differ only
    by endpoint. `azure_blob` is a separate adapter because Azure is not
    S3-compatible (ADR-010). `memory` is the fake that lets the suite run with
    no network.
    """
    if settings.storage_backend == "memory":
        from app.adapters.storage.memory import InMemoryObjectStore

        return InMemoryObjectStore()

    if settings.storage_backend == "azure_blob":
        from app.adapters.storage.azure_blob import AzureBlobObjectStore

        return AzureBlobObjectStore(settings)

    from app.adapters.storage.s3 import S3ObjectStore

    return S3ObjectStore(settings)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, fmt=settings.log_format)

    # Composition root (ADR-004): adapters are selected here from config and
    # injected as dependencies, so services depend on the protocol and never on
    # a concrete implementation.
    app_store = _build_object_store(settings)

    app = FastAPI(
        title=settings.app_name,
        description="The Intelligent Financial Decision Platform",
        version="0.1.0",
        lifespan=lifespan,
        # Docs are useful in production for a developer-facing API, but the
        # OpenAPI schema is what the frontend generates types from, so it is
        # always served.
        docs_url="/docs" if not settings.is_production else None,
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.settings = settings
    app.state.object_store = app_store

    # Registered before CORSMiddleware on purpose. `add_middleware` prepends, so
    # the first one registered ends up innermost -- which is where the catch-all
    # has to be for its 500 to travel back out through CORS and pick up the
    # headers a browser needs before it will show the body. See the class.
    app.add_middleware(CatchAllMiddleware)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # required for the httpOnly refresh cookie
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID", "Idempotency-Key"],
        expose_headers=["X-Request-ID", "X-Response-Time-Ms", "Idempotency-Replayed"],
    )
    # Authlib carries the OAuth nonce and PKCE verifier in a signed session
    # between the redirect and the callback, so this is mounted only when OAuth
    # is actually configured.
    if settings.oauth_enabled:
        from starlette.middleware.sessions import SessionMiddleware

        app.add_middleware(
            SessionMiddleware,
            secret_key=settings.session_signing_key,
            session_cookie="frugal_oauth_session",
            max_age=600,
            same_site="lax",
            https_only=settings.is_production,
        )

    register_middleware(app)
    register_exception_handlers(app)

    app.include_router(system_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
