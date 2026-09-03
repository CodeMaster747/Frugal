"""HTTP surface for personalization.

Registered only when `settings.personalization_enabled` -- the same pattern as
the OAuth routes. A deployment without a second database serves 404 here, which
is the truth: the feature does not exist, rather than existing and being broken.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db
from app.core.dependencies import CurrentUserDep
from app.core.signals_database import get_signals_db
from app.modules.personalization.archetypes import ARCHETYPES, MATCH_THRESHOLD
from app.modules.personalization.schemas import ProfileOut, RefreshResult
from app.modules.personalization.service import PersonalizationService

router = APIRouter(prefix="/personalization", tags=["personalization"])

SignalsDep = Annotated[AsyncSession, Depends(get_signals_db)]
PrimaryDep = Annotated[AsyncSession, Depends(get_db)]


@router.get("/profile", response_model=ProfileOut, summary="Spending profile")
async def profile(user: CurrentUserDep, signals: SignalsDep) -> ProfileOut:
    """The aggregate view derived from this user's own spending.

    Never returns an individual signal. Raw purchase signals do not leave the
    module, which is the whole reason they are in a database of their own.
    """
    return await PersonalizationService(signals).profile(user.id)


@router.post("/refresh", response_model=RefreshResult, summary="Re-derive the profile")
async def refresh(user: CurrentUserDep, signals: SignalsDep, primary: PrimaryDep) -> RefreshResult:
    """Recompute now rather than waiting for the nightly sweep.

    Bounded by the user's own history, so this is a read of at most
    `LOOKBACK_DAYS` of their transactions and a handful of writes -- small
    enough to run in the request, unlike the OCR and forecast paths.
    """
    return await PersonalizationService(signals, primary).refresh(user.id)


@router.get("/archetypes", summary="The archetype rubric")
async def archetypes() -> dict[str, object]:
    """The rules themselves, published in-product.

    Same commitment as `GET /health-score/rubric` and
    `GET /market/reliability/rubric`: a user who is told they match a pattern
    can read what the pattern means and what threshold it took to match it.
    """
    return {
        "match_threshold": format(MATCH_THRESHOLD, "f"),
        "archetypes": [
            {"slug": a.slug, "label": a.label, "description": a.description} for a in ARCHETYPES
        ],
    }
