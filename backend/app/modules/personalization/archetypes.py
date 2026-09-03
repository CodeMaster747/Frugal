"""Named spending patterns, and how a profile matches one.

Deterministic and published, per ADR-005: no LLM computes any of this, and
`GET /personalization/archetypes` serves the rules themselves so a user can see
why they were matched rather than being told they were.

Each rule is a plain predicate over `DerivedProfile`. A profile can match
several -- these are descriptions, not a partition, and forcing one label would
be a claim about a person that the evidence does not support.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal

from app.modules.personalization.derive import DerivedProfile


@dataclass(frozen=True, slots=True)
class Archetype:
    slug: str
    label: str
    description: str
    #: Returns 0..1. Zero means "not this", not "no data".
    score: Callable[[DerivedProfile], Decimal]


def _share(profile: DerivedProfile, *slugs: str) -> Decimal:
    return sum(
        (Decimal(profile.category_affinities.get(slug, "0")) for slug in slugs),
        Decimal(0),
    )


def _infrequent_large(profile: DerivedProfile) -> Decimal:
    """Few, large purchases far apart -- someone who saves up and then buys."""
    if profile.cadence_days is None:
        return Decimal("0.000")
    if profile.cadence_days < 45:
        return Decimal("0.000")
    return min(profile.cadence_days / Decimal(180), Decimal(1)).quantize(Decimal("0.001"))


def _frequent_moderate(profile: DerivedProfile) -> Decimal:
    """Notable purchases arriving often -- steadier, more recurring commitments."""
    if profile.cadence_days is None or profile.cadence_days <= 0:
        return Decimal("0.000")
    if profile.cadence_days > 30:
        return Decimal("0.000")
    return min(Decimal(30) / profile.cadence_days, Decimal(1)).quantize(Decimal("0.001"))


def _concentrated(profile: DerivedProfile) -> Decimal:
    """Most notable spend in one category."""
    if not profile.category_affinities:
        return Decimal("0.000")
    return max(Decimal(v) for v in profile.category_affinities.values())


ARCHETYPES: tuple[Archetype, ...] = (
    Archetype(
        slug="considered_buyer",
        label="Considered buyer",
        description=(
            "Your larger purchases are spaced well apart. Advice that helps you is "
            "mostly about timing and price, not about frequency."
        ),
        score=_infrequent_large,
    ),
    Archetype(
        slug="steady_spender",
        label="Steady spender",
        description=(
            "Larger purchases arrive regularly. The useful question for you is "
            "usually whether the next one fits, not whether this one does."
        ),
        score=_frequent_moderate,
    ),
    Archetype(
        slug="category_focused",
        label="Category focused",
        description=(
            "Most of your notable spending falls in one category, so a better price "
            "there is worth more to you than a better price anywhere else."
        ),
        score=_concentrated,
    ),
)

#: Below this a match is not worth showing. A label attached at 0.1 is noise
#: presented as insight.
MATCH_THRESHOLD = Decimal("0.35")


def match(profile: DerivedProfile) -> list[tuple[Archetype, Decimal]]:
    """Archetypes this profile matches, strongest first."""
    if profile.signal_count == 0:
        return []
    scored = [(a, a.score(profile)) for a in ARCHETYPES]
    return sorted(
        ((a, s) for a, s in scored if s >= MATCH_THRESHOLD),
        key=lambda pair: pair[1],
        reverse=True,
    )
