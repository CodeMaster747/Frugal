"""Application configuration.

Every setting is validated at import time. The process fails fast on a missing
or malformed variable rather than surfacing it later as a confusing runtime
error in a request handler.
"""

from __future__ import annotations

from decimal import Decimal
from functools import lru_cache
from typing import Annotated, Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import Field, PostgresDsn, RedisDsn, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

Environment = Literal["local", "ci", "production"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # -- environment -----------------------------------------------------
    environment: Environment = "local"
    debug: bool = False
    app_name: str = "Frugal"
    api_v1_prefix: str = "/api/v1"

    # -- database --------------------------------------------------------
    database_url: PostgresDsn = Field(
        description="Application connection. On Neon this is the *pooled* endpoint."
    )
    database_direct_url: PostgresDsn | None = Field(
        default=None,
        description=(
            "Direct (non-pooled) endpoint used by Alembic. DDL through a "
            "connection pooler is unreliable. Falls back to database_url locally."
        ),
    )
    # 10 + 10. A single advisor request holds **three** connections at its peak:
    # its own session, plus one each for the health and forecast engines it
    # gathers concurrently. At the previous 5 + 5 that was three concurrent
    # advisor users before the pool blocked, and SQLAlchemy's 30-second
    # `pool_timeout` then surfaced as a request that simply hung — which is how
    # this was found, as intermittent end-to-end timeouts that looked like load.
    #
    # Twenty is still conservative against Postgres's default 100 and Neon's
    # free-tier allowance, and leaves room for the worker's own pool.
    db_pool_size: int = Field(default=10, ge=1)
    db_max_overflow: int = Field(default=10, ge=0)
    db_echo: bool = False

    # -- personalization signals (ADR-011) -------------------------------
    #
    # A genuinely separate database: separate URL, separate engine, separate
    # Alembic history, separate declarative base. It holds purchase signals
    # derived from bank messages, used only to personalise budgets and
    # recommendations, and no foreign key crosses between it and the ledger --
    # Postgres cannot express one, and that impossibility is the point.
    #
    # Unset means the feature does not exist: no routes, no worker task, no
    # readiness entry. Not a broken one.
    signals_database_url: PostgresDsn | None = Field(
        default=None,
        description="Second database for personalization signals. Unset disables the feature.",
    )
    signals_database_direct_url: PostgresDsn | None = Field(
        default=None,
        description="Direct endpoint for the second Alembic tree, as database_direct_url is.",
    )
    # 2 + 2, not 10 + 10. This serves recommendations, never the critical path,
    # and both pools draw on the same Neon allowance.
    signals_db_pool_size: int = Field(default=2, ge=1)
    signals_db_max_overflow: int = Field(default=2, ge=0)

    # -- crowdsourced price graph (ADR-013) ------------------------------
    #: The bar to *publish* a line item into the shared graph, deliberately
    #: above `ocr_confidence_threshold` (0.75): showing you your own reading is
    #: a lower-stakes act than telling other people what a shop charges.
    price_promotion_confidence_threshold: Decimal = Field(default=Decimal("0.80"), ge=0, le=1)
    #: How far the sum of line items may differ from the receipt total before
    #: none of its lines are trusted. A receipt whose lines do not add up had a
    #: bad read, and this is the strongest filter in the promotion rule.
    price_promotion_total_tolerance: Decimal = Field(default=Decimal("0.05"), ge=0, le=1)
    #: Distinct contributors required before a store-level price is shown to
    #: anyone else. A single observation says "exactly one person shopped here
    #: and bought this", which is a sentence about a person.
    min_contributors_for_display: int = Field(default=2, ge=1)
    #: Trust below which a user's contributions are recorded but not published.
    #:
    #: Read by the promotion task through `CommunityService.trust_score`. It was
    #: documented in ADR-013 and read by nothing for a milestone, which meant a
    #: user whose trust had been driven to zero published exactly as freely as
    #: anyone else.
    min_contributor_trust: Decimal = Field(default=Decimal("0.20"), ge=0, le=1)
    #: Price observations one user may publish per day.
    #:
    #: Separate from `points_awards_per_user_daily_cap`: this gates whether a
    #: price reaches other people, which is a different question from whether
    #: the contributor is paid for it. A large weekly shop is a few dozen lines,
    #: so this is generous for a real user and tight for a script.
    price_contributions_per_user_daily_cap: int = Field(default=120, ge=1)
    #: Keys the contributor HMAC. Never stored in the database, so a dump alone
    #: links nothing. Falls back to the JWT secret locally; set it explicitly in
    #: production. **Never rotate it casually** -- rotation invalidates the
    #: one-per-day uniqueness constraint, which is why rows carry a
    #: `pepper_version`.
    contribution_pepper: SecretStr | None = None

    # -- scraper (ADR-012) -----------------------------------------------
    #: Hosts this deployment may fetch, comma-separated.
    #:
    #: **Empty by default, and that is the shipped configuration.** A fresh
    #: deployment is incapable of crawling anyone, exactly as it is incapable of
    #: spending money. This can only turn *off* a host that
    #: `app/adapters/offers/scraper/hosts.py` already knows how to read -- a
    #: host allowed here without an extractor there is inert, because the code
    #: and the permission have to move together.
    scraper_enabled_hosts: Annotated[list[str], NoDecode] = Field(default_factory=list)
    #: Per-run ceiling, so one crawl cannot starve receipt OCR on a
    #: single-concurrency worker.
    scraper_requests_per_run: int = Field(default=20, ge=1)
    #: How long a scraped result is served before it is refetched.
    scrape_cache_ttl_hours: int = Field(default=24, ge=1)

    # -- points ----------------------------------------------------------
    #: Awards per user per day. A cap on *awards* rather than on points: a cap
    #: on points would make the cheapest contribution the most efficient way to
    #: farm, which is the opposite of the intent.
    points_awards_per_user_daily_cap: int = Field(default=60, ge=1)
    #: A deployment-wide ceiling, so a botnet cannot spend the ledger's
    #: credibility across many accounts at once.
    points_awards_daily_cap_global: int = Field(default=20000, ge=1)

    # -- redis -----------------------------------------------------------
    redis_url: RedisDsn

    # -- security --------------------------------------------------------
    jwt_secret: SecretStr
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = Field(default=900, ge=60)
    refresh_token_ttl_days: int = Field(default=30, ge=1)
    # NoDecode: pydantic-settings JSON-decodes complex types from env before
    # validators run, so a plain comma-separated string would fail to parse.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    frontend_url: str = "http://localhost:3000"

    # --- oauth (FR-1.5) --------------------------------------------------
    # Optional: the OAuth routes are registered only when a client id is set,
    # so a deployment without credentials has no OAuth endpoints rather than
    # endpoints that fail at runtime.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    google_redirect_uri: str = "http://localhost:8000/api/v1/auth/oauth/google/callback"
    # Signs the short-lived session used to carry OAuth state; falls back to
    # the JWT secret when unset.
    session_secret: SecretStr | None = None

    # -- adapters (ADR-004) ----------------------------------------------
    storage_backend: Literal["s3", "minio", "azure_blob", "memory"] = "minio"
    ocr_engine: Literal["tesseract", "fake"] = "fake"
    #: Which `PriceProvider` the advisor and the market module both use.
    #:
    #: They must agree. `seed_catalog` prices are static, `simulated_market`
    #: prices move — and running one of each meant the advisor quoted ₹89,900
    #: for a laptop the wishlist was tracking at ₹70,283 on the same day. The
    #: setting exists so there is one answer to "what does this cost".
    price_provider: Literal["seed_catalog", "simulated_market", "manual"] = "simulated_market"

    # -- object storage --------------------------------------------------
    s3_bucket: str = "frugal-receipts"
    s3_region: str = "ap-south-1"
    s3_endpoint_url: str | None = Field(
        default=None, description="Set for MinIO; leave unset for real S3."
    )
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None
    presigned_url_ttl_seconds: int = Field(default=300, ge=60)

    # -- azure blob storage ------------------------------------------------
    # Read only when storage_backend is `azure_blob`. Kept separate from the
    # S3 block rather than overloaded onto it: Azure is not S3-compatible, and
    # a shared `s3_bucket` that sometimes means a container is the kind of
    # ambiguity that survives review and fails in production.
    azure_storage_account: str = "frugalreceipts"
    azure_blob_container: str = "receipts"
    #: Overrides the derived `https://<account>.blob.core.windows.net`. Set for
    #: Azurite in local development; leave unset against real Azure.
    azure_blob_endpoint: str | None = None
    #: Escape hatch and local-development credential. **Unset in production**:
    #: the VM authenticates as its managed identity, so there is no durable
    #: secret on the box to leak. An account key here is a full-control
    #: credential for the whole storage account, which is strictly worse than
    #: the AWS access key this migration was partly meant to avoid.
    azure_storage_key: SecretStr | None = None

    # -- rate limits (FR-1.7) ---------------------------------------------
    # Defaults are the production policy. They are configurable so local and
    # E2E environments can raise the registration ceiling -- an end-to-end
    # suite legitimately creates many accounts from one IP. The limiting
    # *behaviour* is pinned by tests that set their own limits explicitly, so
    # relaxing these here cannot silently disable the protection.
    login_attempts_per_ip: int = Field(default=10, ge=1)
    login_attempts_per_account: int = Field(default=5, ge=1)
    registrations_per_ip_per_hour: int = Field(default=5, ge=1)
    refreshes_per_hour: int = Field(default=60, ge=1)

    # -- engine thresholds -----------------------------------------------
    ocr_confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    #: Calibrated, not guessed. `tests/eval/test_categorization_accuracy.py`
    #: sweeps this against hand-labelled unseen merchants; 0.60 was the original
    #: value and it accepted only 8% of predictions -- correct on all of them,
    #: and useless. 0.30 accepts 28% at 91% precision. The trade is favourable
    #: because a model suggestion lands unreviewed in the review queue: a wrong
    #: one costs a click to fix, a missing one costs picking from 23 categories.
    categorization_confidence_threshold: float = Field(default=0.30, ge=0, le=1)
    forecast_min_observation_days: int = Field(default=14, ge=1)
    forecast_ewma_min_days: int = Field(default=60, ge=1)
    forecast_prophet_min_days: int = Field(default=180, ge=1)

    # -- sms ingestion ----------------------------------------------------
    #: Below this, a parsed message goes to review instead of the ledger.
    #: Set at the OCR threshold rather than the categoriser's 0.30, because the
    #: trade is the opposite one: a wrong category costs a click to fix, while a
    #: wrong transaction is money in the ledger that the user has no reason to
    #: go looking for. Every issuer template clears 0.90 on a complete message,
    #: so this rejects incomplete readings rather than whole banks.
    sms_confidence_threshold: float = Field(default=0.75, ge=0, le=1)
    #: How long the original message text is kept. After this the row keeps only
    #: its redacted form -- once the transaction exists, holding the raw text
    #: buys nothing and is the part of this feature a user would most object to.
    sms_raw_body_retention_days: int = Field(default=30, ge=1)

    # -- offer search ------------------------------------------------------
    #: Deliberately *not* added to `price_provider`. The advisor and the
    #: wishlist share that setting and both need a catalogue with history; a
    #: live search API has neither, and wiring it there would also spend quota
    #: on every `/advisor/evaluate` call rather than on an explicit search.
    offer_search_provider: Literal["simulated", "serpapi"] = "simulated"
    serpapi_api_key: SecretStr | None = None
    #: Caps set *below* the provider's own free allowance, so our stop fires
    #: first and theirs is a second line of defence rather than the plan. The
    #: whole zero-recurring-cost constraint rests on these three numbers and the
    #: ledger in `app/core/quota.py` that enforces them.
    serpapi_monthly_cap: int = Field(default=200, ge=0)
    #: So one bad day cannot eat the month.
    serpapi_daily_cap: int = Field(default=10, ge=0)
    #: So one user cannot drain a shared allowance and leave everyone else
    #: looking at a paused banner.
    serpapi_per_user_daily_cap: int = Field(default=2, ge=0)
    #: A cache hit costs no quota, which makes this the single largest saving
    #: available. Long enough that repeated searches are free, short enough that
    #: a quoted price is not stale enough to mislead.
    offer_cache_ttl_hours: int = Field(default=24, ge=1)
    #: Shown to users when a metered service is paused, so the message names
    #: someone rather than trailing off.
    support_contact: str = "the developer"

    # -- observability ---------------------------------------------------
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["json", "console"] = "json"

    @field_validator("jwt_secret")
    @classmethod
    def _secret_is_long_enough(cls, value: SecretStr) -> SecretStr:
        """HS256 keys shorter than the 256-bit hash output weaken the MAC
        (RFC 7518 §3.2). Enforced at boot so a weak secret cannot reach
        production quietly -- generate with `openssl rand -hex 32`.
        """
        if len(value.get_secret_value()) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return value

    @field_validator(
        "signals_database_url",
        "signals_database_direct_url",
        "contribution_pepper",
        mode="before",
    )
    @classmethod
    def _blank_is_unset(cls, value: object) -> object:
        """Treat an empty variable as absent.

        Compose enumerates every optional variable with `${VAR:-}`, so an
        unconfigured feature arrives as `""` rather than not arriving at all.
        Without this, "the feature is off" fails validation, and a blank
        pepper would read as a real one because `SecretStr("")` is truthy.
        """
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("scraper_enabled_hosts", mode="before")
    @classmethod
    def _split_hosts(cls, value: object) -> object:
        """Same comma-splitting as `cors_origins`: env vars cannot hold lists."""
        if isinstance(value, str):
            return [host.strip().lower() for host in value.split(",") if host.strip()]
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        """Accept a comma-separated string, since env vars cannot hold lists."""
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _the_signals_database_is_a_different_database(self) -> Settings:
        """Refuse the one misconfiguration the whole design rests on.

        Pointing both URLs at one database would leave every import-linter
        contract kept, every test passing, and the isolation entirely absent --
        a separation that exists only in the names of two settings. Failing at
        boot is the difference between a property of the deployment and a
        promise in a comment.
        """
        if self.signals_database_url is None:
            return self

        def endpoint(dsn: PostgresDsn) -> tuple[str | None, int | None, str | None]:
            host = dsn.hosts()[0]
            return (host.get("host"), host.get("port"), dsn.path)

        if endpoint(self.signals_database_url) == endpoint(self.database_url):
            raise ValueError(
                "SIGNALS_DATABASE_URL points at the primary database. The separation is "
                "the entire feature: it is what makes 'no cross-database foreign key' a "
                "property of the deployment rather than a promise in a comment. Create a "
                "second database (locally: infra/docker/init-test-db.sql makes one) or "
                "leave SIGNALS_DATABASE_URL unset to disable personalization."
            )
        return self

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def personalization_enabled(self) -> bool:
        """Whether the second database is configured.

        Callers check this before reaching for a signals session. The accessors
        in `core/signals_database.py` raise rather than return None, so a
        missing check is a stack trace at the call site instead of a None
        dereference three frames away.
        """
        return self.signals_database_url is not None

    @property
    def oauth_enabled(self) -> bool:
        return bool(self.google_client_id and self.google_client_secret)

    @property
    def contribution_pepper_key(self) -> str:
        """The HMAC key for contributor hashes.

        Falls back to the JWT secret so local development works without a
        second secret to manage. In production they should differ: the JWT
        secret is rotated when a token is compromised, and rotating the pepper
        breaks the one-contribution-per-day constraint retroactively.
        """
        key = self.contribution_pepper or self.jwt_secret
        return key.get_secret_value()

    @property
    def session_signing_key(self) -> str:
        key = self.session_secret or self.jwt_secret
        return key.get_secret_value()

    @property
    def alembic_url(self) -> str:
        """Direct endpoint for DDL, falling back to the pooled one locally."""
        return str(self.database_direct_url or self.database_url)

    @property
    def sync_database_url(self) -> str:
        """psycopg URL for Celery workers (ADR-006).

        Both spellings are named, as alembic/env.py does: a bare
        `postgresql://` otherwise resolves to psycopg2, which this project
        does not install, and the worker dies on its first query.
        """
        return (
            str(self.database_url)
            .replace("postgresql+asyncpg://", "postgresql+psycopg://")
            .replace("postgresql://", "postgresql+psycopg://")
        )

    @staticmethod
    def _to_asyncpg(url: str) -> str:
        """Rewrite a libpq URL for SQLAlchemy's asyncpg dialect.

        Two dialects read the same variable. psycopg speaks libpq and wants
        `sslmode=require`, which Neon needs and rejects the connection
        without; asyncpg has no such parameter and raises TypeError on it,
        wanting `ssl=require` instead. Translating here keeps one URL in the
        environment rather than two that can drift apart.
        """
        if "+asyncpg" not in url:
            url = url.replace("postgresql://", "postgresql+asyncpg://")
        split = urlsplit(url)
        params = [
            ("ssl", value) if key == "sslmode" else (key, value)
            for key, value in parse_qsl(split.query, keep_blank_values=True)
            # asyncpg negotiates channel binding itself and rejects the flag.
            if key != "channel_binding"
        ]
        return urlunsplit(split._replace(query=urlencode(params)))

    @property
    def async_database_url(self) -> str:
        """asyncpg URL for the API."""
        return self._to_asyncpg(str(self.database_url))

    @property
    def signals_alembic_url(self) -> str:
        """Direct endpoint for the second Alembic tree."""
        return str(self.signals_database_direct_url or self.signals_database_url)

    @property
    def signals_async_url(self) -> str:
        """asyncpg URL for the personalization database."""
        return self._to_asyncpg(str(self.signals_database_url))

    @property
    def signals_sync_url(self) -> str:
        """psycopg URL for Celery workers (ADR-006)."""
        return str(self.signals_database_url).replace(
            "postgresql+asyncpg://", "postgresql+psycopg://"
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached accessor. Import this rather than instantiating Settings directly."""
    return Settings()
