"""How much a user's word is worth, and why.

Pure, so the rubric can be exercised across a matrix of contribution histories
with no database -- the same property `personalization/derive.py` and
`market/reliability.py` have, and the same reason.

**Published in-product**, per the commitment `GET /health-score/rubric` and
`GET /market/reliability/rubric` already make: a user whose contribution was not
promoted can read exactly what would change that, rather than being told they
are not trusted enough.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

#: Bumped when the weights below change, so a stored score records which rules
#: produced it.
RUBRIC_VERSION = 1

#: Where everybody starts. Deliberately *below* `min_contributor_trust`
#: (default 0.20) is tempting and wrong: a new user's first receipt would then
#: never be promoted, and they would have no way to earn the trust that
#: promotion is what grants. Starting at the floor means the first contribution
#: counts and a bad one costs.
STARTING_TRUST = Decimal("0.200")

#: Nobody reaches certainty. A trust score is an inference from a handful of
#: contributions, and 1.000 would claim otherwise.
MAX_TRUST = Decimal("0.950")

#: What each signal is worth. Positive signals are cheap and negative ones are
#: expensive, on purpose: the cost of publishing a wrong price to everybody is
#: much higher than the cost of not publishing a right one.
PROMOTION_WEIGHT = Decimal("0.030")
AGREEMENT_WEIGHT = Decimal("0.010")
DISPUTE_PENALTY = Decimal("0.080")
RETRACTION_PENALTY = Decimal("0.120")

_MILLI = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class TrustInputs:
    accepted_promotions: int = 0
    agreed_verifications: int = 0
    disputed_reports: int = 0
    retracted_contributions: int = 0


@dataclass(frozen=True, slots=True)
class TrustResult:
    score: Decimal
    factors: list[dict[str, str]]


def compute(inputs: TrustInputs) -> TrustResult:
    """Trust, and the reasons for it.

    ADR-002's shape: a score that cannot say why it is what it is has no
    business gating whether someone's contribution reaches other people.
    """
    score = STARTING_TRUST
    factors: list[dict[str, str]] = []

    if inputs.accepted_promotions:
        gain = PROMOTION_WEIGHT * inputs.accepted_promotions
        score += gain
        factors.append(
            {
                "name": "verified contributions",
                "detail": f"{inputs.accepted_promotions} prices reached the shared graph",
                "effect": f"+{gain.quantize(_MILLI)}",
            }
        )

    if inputs.agreed_verifications:
        gain = AGREEMENT_WEIGHT * inputs.agreed_verifications
        score += gain
        factors.append(
            {
                "name": "confirmations",
                "detail": f"{inputs.agreed_verifications} of your confirmations matched others",
                "effect": f"+{gain.quantize(_MILLI)}",
            }
        )

    if inputs.disputed_reports:
        loss = DISPUTE_PENALTY * inputs.disputed_reports
        score -= loss
        factors.append(
            {
                "name": "disputed reports",
                "detail": f"{inputs.disputed_reports} of your reports were disputed by others",
                "effect": f"-{loss.quantize(_MILLI)}",
            }
        )

    if inputs.retracted_contributions:
        loss = RETRACTION_PENALTY * inputs.retracted_contributions
        score -= loss
        factors.append(
            {
                "name": "retracted contributions",
                "detail": f"{inputs.retracted_contributions} contributions were taken back",
                "effect": f"-{loss.quantize(_MILLI)}",
            }
        )

    if not factors:
        factors.append(
            {
                "name": "new contributor",
                "detail": "no contributions yet",
                "effect": f"starts at {STARTING_TRUST}",
            }
        )

    clamped = min(max(score, Decimal(0)), MAX_TRUST).quantize(_MILLI, rounding=ROUND_HALF_UP)
    return TrustResult(score=clamped, factors=factors)


#: Agreement weight needed before a `good_price` report is promoted into the
#: shared graph.
#:
#: A sum of verifier trust rather than a count: three brand-new accounts
#: agreeing is the shape sockpuppeting takes, and it should not outweigh one
#: established contributor. At the starting trust of 0.200 this needs three
#: confirmations, and fewer as the confirmers' own records improve.
PROMOTION_THRESHOLD = Decimal("0.600")


def promotable(agreement_weight: Decimal, author_trust: Decimal) -> bool:
    """Whether a report has enough behind it to become a shared price.

    The author's own trust counts for half, and that halving is what makes
    self-promotion impossible: even a maximally trusted author contributes
    `MAX_TRUST / 2` = 0.475, which is below the 0.600 threshold. **At least one
    other person must always agree.** One person, however reliable, does not get
    to publish an unverified price to everybody -- that is what the receipt path
    is for, and it has a paper trail this does not.

    `test_an_author_can_never_promote_their_own_report` pins it, because the
    property lives in the relationship between three constants and would break
    silently if any one of them moved.
    """
    return (agreement_weight + min(author_trust, MAX_TRUST) / 2) >= PROMOTION_THRESHOLD
