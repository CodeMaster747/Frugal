"""Turning transactions into purchase signals, and signals into a profile.

Pure: no session, no models, no sibling modules. That is what lets the whole
percentile-and-cadence rubric be exercised over a matrix of synthetic histories
in one fast test, the same property `sms/parser` and `market/reliability` have,
and it is held by the `personalization-derivation-is-pure` contract.

**Percentiles rather than an absolute threshold.** "A big purchase" is
₹5,000 for one person and ₹80,000 for another, and any constant here would
encode an assumption about income that this product exists to avoid making.
The threshold is the user's own distribution.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

#: Bumped when the rubric below changes, so a stored profile says which rules
#: produced it. Same discipline as `health/rubric.py`.
RUBRIC_VERSION = 1

#: A purchase must sit at or above this quantile of the user's own spending to
#: count as notable. 0.90 rather than 0.95: at 0.95 a user with 40 purchases a
#: year yields two signals, which is not enough to say anything from.
BIG_PURCHASE_QUANTILE = Decimal("0.90")

#: Below this many observations the distribution is not a distribution.
MIN_OBSERVATIONS = 12

#: Below this many days, seasonality swamps everything and cadence is noise.
MIN_OBSERVATION_DAYS = 30

#: How far the notable threshold must sit above ordinary spending before
#: "notable" means anything.
#:
#: Without this, a flat history breaks the model in a way that looks like it is
#: working. Fifteen purchases of Rs.500 put the 90th percentile at Rs.500, so
#: every purchase clears the threshold, `big_purchase_threshold` reports the
#: user's *ordinary* spend as their big-purchase line, and the profile arrives
#: confident because it has fifteen "signals". Every number is arithmetically
#: correct and the conclusion is nonsense.
#:
#: 1.25 rather than something larger: the question is whether a top decile
#: exists at all, not whether it is dramatic.
BIG_PURCHASE_SEPARATION = Decimal("1.25")

#: A profile never claims more than this, whatever the sample size. It
#: describes a habit inferred from bank alerts, which is a genuinely uncertain
#: thing to infer.
MAX_CONFIDENCE = Decimal("0.85")

_CENT = Decimal("0.01")
_MILLI = Decimal("0.001")


@dataclass(frozen=True, slots=True)
class Observation:
    """One spend, already measured. Deliberately plain data.

    `key` is whatever the caller uses to recognise the same source row again --
    a transaction id, an SMS message hash. It is hashed on the way in and never
    stored in the clear, so this module cannot be used to join back.
    """

    key: str
    occurred_on: date
    amount: Decimal
    merchant_normalized: str | None = None
    category_slug: str | None = None
    confidence: Decimal = Decimal("1.000")


@dataclass(frozen=True, slots=True)
class DerivedSignal:
    source_digest: bytes
    occurred_on: date
    amount: Decimal
    merchant_normalized: str | None
    category_slug: str | None
    amount_percentile: Decimal
    confidence: Decimal


@dataclass(frozen=True, slots=True)
class DerivedProfile:
    observation_days: int
    signal_count: int
    big_purchase_threshold: Decimal | None
    median_big_purchase: Decimal | None
    cadence_days: Decimal | None
    category_affinities: dict[str, str]
    confidence: Decimal
    factors: list[dict[str, str]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def source_digest(key: str) -> bytes:
    """A stable handle for a source row that is not the row's identifier."""
    return hashlib.sha256(key.encode()).digest()


def quantile(values: list[Decimal], q: Decimal) -> Decimal | None:
    """Linear-interpolated quantile over a sorted copy.

    Written out rather than pulled from numpy: this module is imported by the
    API process, and the whole point of keeping it pure is that it costs
    nothing to import.
    """
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]

    position = (Decimal(len(ordered)) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    weight = position - Decimal(lower)
    interpolated = ordered[lower] + (ordered[upper] - ordered[lower]) * weight
    return interpolated.quantize(_CENT, rounding=ROUND_HALF_UP)


def _percentile_of(value: Decimal, ordered: list[Decimal]) -> Decimal:
    """Where `value` sits in `ordered`, 0..1."""
    if not ordered:
        return Decimal("0.000")
    below = sum(1 for v in ordered if v < value)
    return (Decimal(below) / Decimal(len(ordered))).quantize(_MILLI, rounding=ROUND_HALF_UP)


def derive_signals(observations: list[Observation]) -> list[DerivedSignal]:
    """The notable purchases among these, with where each sits in the whole.

    Returns empty below `MIN_OBSERVATIONS`. That is not a degraded answer, it
    is the absence of one: a threshold computed from four purchases would be
    arithmetic dressed up as a finding.
    """
    if len(observations) < MIN_OBSERVATIONS:
        return []

    amounts = sorted(o.amount for o in observations)
    threshold = quantile(amounts, BIG_PURCHASE_QUANTILE)
    median = quantile(amounts, Decimal("0.5"))
    if threshold is None or median is None:
        return []

    # Spending too even to have a top decile. Returning nothing is the honest
    # answer: there is no "big purchase" habit here to describe, and reporting
    # the ordinary spend as the threshold would be a confident wrong answer
    # rather than an absent one.
    if median > 0 and threshold < median * BIG_PURCHASE_SEPARATION:
        return []

    return [
        DerivedSignal(
            source_digest=source_digest(o.key),
            occurred_on=o.occurred_on,
            amount=o.amount,
            merchant_normalized=o.merchant_normalized,
            category_slug=o.category_slug,
            amount_percentile=_percentile_of(o.amount, amounts),
            confidence=o.confidence,
        )
        for o in observations
        if o.amount >= threshold
    ]


def derive_profile(
    signals: list[DerivedSignal],
    *,
    observation_days: int,
    total_observations: int,
) -> DerivedProfile:
    """Roll signals up into the thing a recommendation reads.

    Always returns a profile. When there is not enough to say, it returns one
    whose `signal_count` is zero and whose caveats say why -- an honest empty
    state rather than a fabricated median, which is the same rule
    `forecasting` follows when it refuses rather than inventing a series.
    """
    factors: list[dict[str, str]] = []
    caveats: list[str] = []

    if observation_days < MIN_OBSERVATION_DAYS:
        caveats.append(
            f"Only {observation_days} days of history; a spending habit is not "
            f"visible in less than {MIN_OBSERVATION_DAYS}."
        )
    if total_observations < MIN_OBSERVATIONS:
        caveats.append(
            f"Only {total_observations} purchases seen; at least {MIN_OBSERVATIONS} are "
            "needed before one can be called unusually large."
        )

    if not signals:
        return DerivedProfile(
            observation_days=observation_days,
            signal_count=0,
            big_purchase_threshold=None,
            median_big_purchase=None,
            cadence_days=None,
            category_affinities={},
            confidence=Decimal("0.000"),
            factors=[
                {
                    "name": "observations",
                    "detail": f"{total_observations} purchases over {observation_days} days",
                    "effect": "not enough to describe a habit",
                }
            ],
            caveats=caveats or ["No notable purchases identified yet."],
        )

    amounts = sorted(s.amount for s in signals)
    threshold = min(amounts)
    median = quantile(amounts, Decimal("0.5"))

    dates = sorted(s.occurred_on for s in signals)
    cadence: Decimal | None = None
    if len(dates) >= 2:
        span_days = (dates[-1] - dates[0]).days
        cadence = (Decimal(span_days) / Decimal(len(dates) - 1)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

    total = sum(amounts)
    affinities: dict[str, Decimal] = {}
    for signal in signals:
        slug = signal.category_slug or "uncategorized"
        affinities[slug] = affinities.get(slug, Decimal(0)) + signal.amount
    shares = {
        slug: (value / total).quantize(_MILLI, rounding=ROUND_HALF_UP)
        for slug, value in sorted(affinities.items(), key=lambda kv: -kv[1])
    }

    factors.append(
        {
            "name": "sample",
            "detail": f"{len(signals)} notable purchases over {observation_days} days",
            "effect": "sets the threshold and the cadence",
        }
    )
    factors.append(
        {
            "name": "threshold",
            "detail": f"at or above the {int(BIG_PURCHASE_QUANTILE * 100)}th percentile "
            f"of your own spending ({threshold})",
            "effect": "what counts as a big purchase for you",
        }
    )
    if cadence is not None:
        factors.append(
            {
                "name": "cadence",
                "detail": f"one about every {cadence} days",
                "effect": "when to expect the next",
            }
        )
    top = next(iter(shares), None)
    if top is not None:
        factors.append(
            {
                "name": "concentration",
                "detail": f"{shares[top]} of notable spend is {top}",
                "effect": "where a recommendation is most likely to help",
            }
        )

    # Confidence grows with sample and window, and is capped. Two independent
    # ratios rather than one: a hundred purchases in a fortnight and four over
    # two years are both weak, for different reasons, and a single term would
    # let either hide behind the other.
    sample_ratio = min(Decimal(len(signals)) / Decimal(MIN_OBSERVATIONS), Decimal(1))
    window_ratio = min(Decimal(observation_days) / Decimal(180), Decimal(1))
    confidence = (MAX_CONFIDENCE * sample_ratio * window_ratio).quantize(
        _MILLI, rounding=ROUND_HALF_UP
    )

    if observation_days < 180:
        caveats.append(
            "Under six months of history, so seasonal spending is not yet separable "
            "from a change in habit."
        )

    return DerivedProfile(
        observation_days=observation_days,
        signal_count=len(signals),
        big_purchase_threshold=threshold,
        median_big_purchase=median,
        cadence_days=cadence,
        category_affinities={k: format(v, "f") for k, v in shares.items()},
        confidence=confidence,
        factors=factors,
        caveats=caveats,
    )
