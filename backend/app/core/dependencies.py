"""Shared FastAPI dependencies.

Authentication resolves to a *principal* -- an identity, not an ORM object --
so core never imports a domain module (ADR-001). Handlers that need the full
user row load it through their own module's repository.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import UnauthenticatedError
from app.core.logging import bind_user_id
from app.core.security import decode_access_token

# auto_error=False so a missing header raises our envelope rather than
# FastAPI's default 403 -- which is both the wrong status and the wrong shape.
_bearer = HTTPBearer(auto_error=False)


@dataclass(frozen=True, slots=True)
class CurrentUser:
    """The authenticated principal.

    Deliberately just an id. Anything more would mean a database read on every
    request, and would couple core to the auth module's model.
    """

    id: uuid.UUID


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser:
    if credentials is None or not credentials.credentials:
        raise UnauthenticatedError()

    user_id = decode_access_token(credentials.credentials)

    # Bind for structured logging so every subsequent line in this request --
    # and any Celery job it starts -- carries the user id (NFR-4).
    bind_user_id(str(user_id))
    return CurrentUser(id=user_id)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]


def client_ip(request: Request) -> str:
    """Best-effort client address for rate limiting.

    Behind Caddy on EC2 the real address arrives in X-Forwarded-For; the
    left-most entry is the client. This is only trusted because the app is not
    directly reachable -- a spoofed header would otherwise let an attacker
    evade per-IP limits by rotating the value.
    """
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> CurrentUser | None:
    """The principal, if there is one, without requiring one.

    For endpoints that are readable signed out but say more when signed in --
    the community reports list is the case that prompted it, where `mine` and
    `voted` were hardcoded false because the handler had no way to know who was
    asking.

    A malformed or expired token is treated as absent rather than raised: this
    is a public endpoint, and a stale token in an old tab should degrade to the
    anonymous view rather than break a page that never needed auth.
    """
    if credentials is None or not credentials.credentials:
        return None

    try:
        user_id = decode_access_token(credentials.credentials)
    except Exception:
        return None

    bind_user_id(str(user_id))
    return CurrentUser(id=user_id)


OptionalUserDep = Annotated[CurrentUser | None, Depends(get_optional_user)]
