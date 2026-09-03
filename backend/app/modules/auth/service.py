"""Authentication service — the auth module's public interface.

Other modules import this file and nothing else from `app.modules.auth`
(ADR-001), which is what keeps the module extractable.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import audit, erasure
from app.core.audit import AuditAction
from app.core.config import get_settings
from app.core.erasure import ErasureKind
from app.core.errors import (
    ConflictError,
    NotFoundError,
    TokenReuseError,
    UnauthenticatedError,
)
from app.core.logging import get_logger
from app.core.queue import RUN_ERASURE, dispatch
from app.core.rate_limit import (
    login_per_account,
    login_per_ip,
    rate_limiter,
    register_per_ip,
)
from app.core.security import (
    generate_refresh_token,
    hash_password,
    hash_refresh_token,
    needs_rehash,
    verify_password,
)
from app.modules.auth.models import OAuthProvider, RefreshToken, User
from app.modules.auth.repository import RefreshTokenRepository, UserRepository
from app.modules.auth.schemas import RegisterRequest, UpdateProfileRequest

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Where a request came from, for rate limiting and audit."""

    ip_address: str
    user_agent: str | None


@dataclass(frozen=True, slots=True)
class IssuedSession:
    """A newly issued session.

    `refresh_token` is the raw value, returned so the router can set the
    cookie. It is never persisted or logged -- only its digest is stored.
    """

    user: User
    refresh_token: str
    refresh_expires_at: datetime


class AuthService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UserRepository(session)
        self.tokens = RefreshTokenRepository(session)
        self.settings = get_settings()

    # --- registration & login ---------------------------------------------

    async def register(self, data: RegisterRequest, ctx: RequestContext) -> IssuedSession:
        await rate_limiter.check("register:ip", ctx.ip_address, register_per_ip())

        if await self.users.email_exists(data.email):
            # Registration necessarily reveals whether an email is taken --
            # there is no way to create a unique account without it. Login and
            # password reset stay silent, which is where it actually matters.
            raise ConflictError("An account with this email already exists")

        user = await self.users.add(
            User(
                email=data.email,
                password_hash=hash_password(data.password),
                display_name=data.display_name,
                base_currency=data.base_currency,
                timezone=data.timezone,
            )
        )

        await audit.record(
            self.session,
            AuditAction.USER_REGISTERED,
            user_id=user.id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return await self._issue_session(user, ctx)

    async def login(self, email: str, password: str, ctx: RequestContext) -> IssuedSession:
        # Both limits apply: per-IP alone is defeated by a botnet, per-account
        # alone by spraying one attempt across many accounts (FR-1.7).
        await rate_limiter.check("login:ip", ctx.ip_address, login_per_ip())
        await rate_limiter.check("login:account", email.lower(), login_per_account())

        user = await self.users.get_by_email(email)

        if user is None or user.password_hash is None:
            # Hash a dummy value so a missing account and a wrong password take
            # the same time. Otherwise response latency is an account oracle.
            verify_password(password, _DUMMY_HASH)
            await self._record_failed_login(email, ctx)
            raise UnauthenticatedError("Incorrect email or password")

        if not verify_password(password, user.password_hash):
            await self._record_failed_login(email, ctx, user_id=user.id)
            raise UnauthenticatedError("Incorrect email or password")

        # Transparently upgrade the hash if cost parameters have been raised.
        if needs_rehash(user.password_hash):
            user.password_hash = hash_password(password)

        await rate_limiter.reset("login:account", email.lower(), login_per_account())
        await audit.record(
            self.session,
            AuditAction.USER_LOGGED_IN,
            user_id=user.id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return await self._issue_session(user, ctx)

    async def _record_failed_login(
        self, email: str, ctx: RequestContext, user_id: uuid.UUID | None = None
    ) -> None:
        await audit.record(
            self.session,
            AuditAction.USER_LOGIN_FAILED,
            user_id=user_id,
            changes={"email": email},
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        await self._commit_security_event()

    async def _commit_security_event(self) -> None:
        """Persist a security action that is about to be followed by an error.

        The request-scoped session rolls back when a handler raises, which would
        discard exactly the writes that matter most here -- a revoked token
        family, or the record of a failed login. Committing first means the
        security effect survives the 401. The subsequent rollback in `get_db`
        is then a no-op on an already-committed transaction.
        """
        await self.session.commit()

    # --- refresh rotation --------------------------------------------------

    async def refresh(self, raw_token: str, ctx: RequestContext) -> IssuedSession:
        """Rotate a refresh token, detecting replay.

        Every branch that fails returns the same 401 to the client except
        reuse, which is surfaced distinctly so the frontend can force a clean
        re-login rather than retrying a doomed refresh loop.
        """
        stored = await self.tokens.get_by_hash(hash_refresh_token(raw_token))

        if stored is None:
            raise UnauthenticatedError("Invalid refresh token")

        if stored.revoked_at is not None:
            raise UnauthenticatedError("Session has been revoked")

        if stored.used_at is not None:
            # The token was already exchanged. Either an attacker replayed a
            # stolen token, or the legitimate holder is replaying one -- and
            # they cannot be distinguished. Revoke the whole chain.
            revoked = await self.tokens.revoke_family(stored.family_id)
            await audit.record(
                self.session,
                AuditAction.TOKEN_REUSE_DETECTED,
                user_id=stored.user_id,
                entity_type="refresh_token",
                entity_id=stored.id,
                changes={"family_id": str(stored.family_id), "revoked_count": revoked},
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
            )
            logger.warning(
                "refresh token reuse detected; family revoked",
                extra={"family_id": str(stored.family_id), "revoked_count": revoked},
            )
            # The revocation must outlive the 401 this raises -- otherwise the
            # rollback would hand the attacker back a working token family.
            await self._commit_security_event()
            raise TokenReuseError()

        if stored.expires_at <= datetime.now(UTC):
            raise UnauthenticatedError("Session has expired")

        user = await self.users.get_by_id(stored.user_id)
        if user is None:
            raise UnauthenticatedError("Session is no longer valid")

        stored.used_at = datetime.now(UTC)
        await audit.record(
            self.session,
            AuditAction.TOKEN_REFRESHED,
            user_id=user.id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        # Same family: the chain is what makes replay detectable.
        return await self._issue_session(user, ctx, family_id=stored.family_id)

    async def logout(self, raw_token: str | None, ctx: RequestContext) -> None:
        """Revoke the presented session.

        Always succeeds. A logout that errors on an already-invalid token would
        strand the client holding a cookie it cannot clear.
        """
        if not raw_token:
            return

        stored = await self.tokens.get_by_hash(hash_refresh_token(raw_token))
        if stored is None:
            return

        await self.tokens.revoke_family(stored.family_id)
        await audit.record(
            self.session,
            AuditAction.USER_LOGGED_OUT,
            user_id=stored.user_id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

    async def _issue_session(
        self, user: User, ctx: RequestContext, family_id: uuid.UUID | None = None
    ) -> IssuedSession:
        raw = generate_refresh_token()
        expires_at = datetime.now(UTC) + timedelta(days=self.settings.refresh_token_ttl_days)

        await self.tokens.add(
            RefreshToken(
                user_id=user.id,
                family_id=family_id or uuid.uuid4(),
                token_hash=hash_refresh_token(raw),
                expires_at=expires_at,
                user_agent=ctx.user_agent,
                ip_address=ctx.ip_address if ctx.ip_address != "unknown" else None,
            )
        )
        return IssuedSession(user=user, refresh_token=raw, refresh_expires_at=expires_at)

    # --- profile -----------------------------------------------------------

    async def active_user_ids(self) -> Sequence[uuid.UUID]:
        """The roster for scheduled jobs that sweep every user.

        Exists so the notification sweep can enumerate users without importing
        this module's models -- the boundary the import-linter enforces, and the
        reason this is a service method rather than a `select` in the worker.
        """
        return await self.users.active_ids()

    async def get_user(self, user_id: uuid.UUID) -> User:
        user = await self.users.get_by_id(user_id)
        if user is None:
            raise NotFoundError("User")
        return user

    async def update_profile(
        self, user_id: uuid.UUID, data: UpdateProfileRequest, ctx: RequestContext
    ) -> User:
        user = await self.get_user(user_id)
        changes = data.model_dump(exclude_unset=True, exclude_none=True)

        for field, value in changes.items():
            setattr(user, field, value)

        if changes:
            await audit.record(
                self.session,
                AuditAction.USER_UPDATED,
                user_id=user.id,
                changes=changes,
                ip_address=ctx.ip_address,
                user_agent=ctx.user_agent,
            )
        await self.session.flush()
        return user

    async def delete_account(self, user_id: uuid.UUID, ctx: RequestContext) -> None:
        """Remove the account and everything owned by it (FR-1.8).

        The audit entry is written *before* the delete and survives it, holding
        only an opaque id -- which is what makes the deletion provable without
        retaining the data it removed.

        Inside the primary database this is a cascade, and cascades are exact.
        Across the boundary to the personalization database (ADR-011) it cannot
        be: Postgres expresses no foreign key between databases and no
        transaction spans them. So the debt is *recorded* here, in this
        transaction, and paid by an idempotent sweep. There is no window in
        which the user is gone and the obligation is unwritten.

        The dispatch below is for latency only. If Redis is down, or the worker
        is not running, or the task dies -- the hourly sweep still finds the
        row. The guarantee is the outbox, never the message.
        """
        user = await self.get_user(user_id)

        await audit.record(
            self.session,
            AuditAction.USER_DELETED,
            user_id=user.id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )

        settings = get_settings()
        if settings.personalization_enabled:
            # Only when there *is* a second database. Recording a debt no
            # deployment can pay would accumulate pending rows and set off the
            # staleness alarm forever, for data that never existed.
            await erasure.request(self.session, user.id, kind=ErasureKind.SIGNALS)

        # The shared price graph, always -- it lives in this database, so there
        # is no configuration under which the obligation does not exist.
        #
        # This one *anonymises* rather than deletes, and the distinction is a
        # decision rather than a shortcut. A price at a shop on a day is a fact
        # about the shop; deleting it would silently degrade what every other
        # user sees and make the graph a function of churn. Nulling the
        # contributor hash -- the last value derived from this user's id, with
        # `receipt_promotions` already gone by cascade -- leaves a row that is
        # genuinely anonymous.
        #
        # A user who wants their contributions actually removed has a separate,
        # explicit action for it: DELETE /pricegraph/contributions. Erasure
        # anonymises; retraction removes.
        await erasure.request(self.session, user.id, kind=ErasureKind.PRICE_CONTRIBUTIONS)

        await self.users.delete(user)

        dispatch(RUN_ERASURE, countdown=5)

    # --- oauth -------------------------------------------------------------

    async def link_or_create_oauth_user(
        self,
        *,
        provider: OAuthProvider,
        subject: str,
        email: str,
        display_name: str,
        email_verified: bool,
        ctx: RequestContext,
    ) -> IssuedSession:
        """Resolve an OAuth profile to a session (FR-1.5).

        Three cases: known subject, existing local account with the same email,
        or a new user.
        """
        user = await self.users.get_by_oauth(provider.value, subject)

        if user is None:
            existing = await self.users.get_by_email(email)
            if existing is not None:
                # Link only when the provider asserts the email is verified.
                # Without that check, anyone able to set an unverified address
                # at the provider could take over a local account.
                if not email_verified:
                    raise ConflictError(
                        "An account with this email already exists. "
                        "Sign in with your password to link this provider."
                    )
                existing.oauth_provider = provider.value
                existing.oauth_subject = subject
                existing.email_verified_at = existing.email_verified_at or datetime.now(UTC)
                user = existing
                await audit.record(
                    self.session,
                    AuditAction.OAUTH_LINKED,
                    user_id=user.id,
                    changes={"provider": provider.value},
                    ip_address=ctx.ip_address,
                    user_agent=ctx.user_agent,
                )
            else:
                user = await self.users.add(
                    User(
                        email=email,
                        password_hash=None,  # OAuth-only; check constraint allows it
                        display_name=display_name,
                        oauth_provider=provider.value,
                        oauth_subject=subject,
                        email_verified_at=datetime.now(UTC) if email_verified else None,
                    )
                )
                await audit.record(
                    self.session,
                    AuditAction.USER_REGISTERED,
                    user_id=user.id,
                    changes={"provider": provider.value},
                    ip_address=ctx.ip_address,
                    user_agent=ctx.user_agent,
                )

        await audit.record(
            self.session,
            AuditAction.USER_LOGGED_IN,
            user_id=user.id,
            ip_address=ctx.ip_address,
            user_agent=ctx.user_agent,
        )
        return await self._issue_session(user, ctx)


# Precomputed so the timing-equalisation path in login() does not pay a hashing
# cost at import time on every call.
_DUMMY_HASH = hash_password("frugal-timing-equalisation-placeholder")
