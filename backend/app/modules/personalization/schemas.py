"""Request/response contracts for personalization.

Amounts are strings on the wire (ADR-003). Nothing here exposes an individual
signal: the module's whole contract with the rest of the system is that raw
purchase signals never leave it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ArchetypeMatchOut(BaseModel):
    slug: str
    label: str
    description: str
    score: str


class ProfileOut(BaseModel):
    """The aggregate view, and how far to trust it.

    `observation_days` and `confidence` are not decoration. A profile built
    from three weeks and one built from two years render identically, and the
    only thing stopping a user trusting them equally is this response saying
    which one they are looking at -- the same argument `ForecastResult` makes.
    """

    model_config = ConfigDict(from_attributes=True)

    available: bool = Field(
        description="False when there is not yet enough history to say anything."
    )
    computed_at: datetime | None = None
    observation_days: int = 0
    signal_count: int = 0

    big_purchase_threshold: str | None = None
    median_big_purchase: str | None = None
    cadence_days: str | None = None

    category_affinities: dict[str, str] = Field(default_factory=dict)
    archetypes: list[ArchetypeMatchOut] = Field(default_factory=list)

    confidence: str = "0.000"
    factors: list[dict[str, str]] = Field(default_factory=list)
    caveats: list[str] = Field(default_factory=list)


class RefreshResult(BaseModel):
    """What a derivation run did. Counts, not rows."""

    signals_written: int
    signals_skipped: int
    profile_recomputed: bool
