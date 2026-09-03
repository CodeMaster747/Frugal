"""The personalization rubric, exercised with no database (ADR-011).

`derive.py` and `archetypes.py` are pure, which is what makes this file
possible: a thousand synthetic histories run in milliseconds, with no fixtures
and no session. The same property `sms/parser` and `market/reliability` have,
and the same payoff.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from app.modules.personalization.archetypes import MATCH_THRESHOLD, match
from app.modules.personalization.derive import (
    BIG_PURCHASE_QUANTILE,
    BIG_PURCHASE_SEPARATION,
    MAX_CONFIDENCE,
    MIN_OBSERVATIONS,
    Observation,
    derive_profile,
    derive_signals,
    quantile,
    source_digest,
)


def _history(amounts: list[str], *, start: date = date(2026, 1, 1)) -> list[Observation]:
    return [
        Observation(
            key=f"txn-{i}",
            occurred_on=start + timedelta(days=i * 7),
            amount=Decimal(a),
            merchant_normalized=f"merchant {i % 3}",
            category_slug=["groceries", "electronics", "dining"][i % 3],
        )
        for i, a in enumerate(amounts)
    ]


class TestQuantile:
    def test_it_interpolates(self):
        values = [Decimal(x) for x in ("10", "20", "30", "40")]
        assert quantile(values, Decimal("0.5")) == Decimal("25.00")

    def test_a_single_value_is_its_own_quantile(self):
        assert quantile([Decimal("42")], Decimal("0.9")) == Decimal("42")

    def test_empty_has_none(self):
        assert quantile([], Decimal("0.5")) is None


class TestDerivingSignals:
    def test_it_refuses_below_the_minimum(self):
        """Not a degraded answer -- the absence of one.

        A threshold computed from four purchases is arithmetic dressed up as a
        finding, and the product's whole thesis is that a number you cannot act
        on with confidence is worse than no number.
        """
        assert derive_signals(_history(["100"] * (MIN_OBSERVATIONS - 1))) == []

    def test_it_selects_the_top_decile(self):
        signals = derive_signals(_history([str(n) for n in range(100, 100 + 20 * 50, 50)]))
        assert signals, "expected some notable purchases"
        assert len(signals) <= 4, "the 90th percentile of 20 should not be most of them"

    def test_the_threshold_scales_with_the_person(self):
        """The single most important property in this module.

        The same purchase is notable for one person and unremarkable for
        another. Any absolute constant here would encode an assumption about
        income that this product exists to avoid making, so the whole rubric
        must be scale-invariant: multiply every amount by a hundred and exactly
        the same purchases are selected.
        """
        spread = [str(n) for n in range(100, 100 + 20 * 200, 200)]
        modest = derive_signals(_history(spread))
        wealthy = derive_signals(_history([str(int(a) * 100) for a in spread]))

        assert modest, "expected notable purchases in a spread history"
        assert [s.amount * 100 for s in modest] == [s.amount for s in wealthy]

    def test_a_flat_history_has_no_notable_purchases(self):
        """Nothing stands out, so nothing is reported.

        This is the case that made the rubric wrong before
        `BIG_PURCHASE_SEPARATION` existed. Fifteen identical purchases put the
        90th percentile *at* the ordinary spend, so every purchase cleared the
        threshold and the profile confidently reported a user's everyday
        spending as their big-purchase line -- arithmetically correct, and
        nonsense.
        """
        assert derive_signals(_history(["1000"] * 20)) == []

    def test_a_near_flat_history_with_one_outlier_still_says_nothing(self):
        """The outlier is real but the *decile* is not.

        Fifteen purchases at Rs.500 and one at Rs.5,000 put the 90th percentile
        at Rs.500. There is an unusual purchase here, but no top decile to
        describe, and inventing one from a single point is the failure mode this
        guard exists for. It is found by the spending-outlier detector in
        `insights`, which is the engine whose job that is.
        """
        assert derive_signals(_history(["500"] * 15 + ["5000"])) == []

    def test_the_digest_is_stable_and_is_not_the_key(self):
        assert source_digest("txn-1") == source_digest("txn-1")
        assert source_digest("txn-1") != source_digest("txn-2")
        assert b"txn-1" not in source_digest("txn-1")


class TestDerivingAProfile:
    def test_no_signals_gives_an_honest_empty_state(self):
        profile = derive_profile([], observation_days=10, total_observations=3)

        assert profile.signal_count == 0
        assert profile.big_purchase_threshold is None
        assert profile.confidence == Decimal("0.000")
        assert profile.caveats, "an empty profile must say why it is empty"
        assert profile.factors, "ADR-002: a profile with no factors is a bug"

    def test_confidence_grows_with_sample_and_window(self):
        spread = derive_signals(_history([str(500 + n * 300) for n in range(40)]))
        thin = derive_profile(spread[:1], observation_days=30, total_observations=13)
        thick = derive_profile(spread, observation_days=400, total_observations=72)
        assert thin.confidence < thick.confidence
        assert thick.confidence <= MAX_CONFIDENCE

    def test_a_long_window_alone_is_not_enough(self):
        """Two ratios rather than one, on purpose.

        A hundred purchases in a fortnight and four over two years are both
        weak, for different reasons, and a single term would let either hide
        behind the other.
        """
        signals = derive_signals(_history([str(500 + n * 300) for n in range(20)]))
        few_over_years = derive_profile(signals[:2], observation_days=700, total_observations=20)
        assert few_over_years.confidence < MAX_CONFIDENCE

    def test_a_large_sample_in_a_short_window_is_not_enough_either(self):
        signals = derive_signals(_history([str(500 + n * 300) for n in range(40)]))
        crammed = derive_profile(signals, observation_days=20, total_observations=40)
        assert crammed.confidence < MAX_CONFIDENCE

    def test_affinities_sum_to_about_one(self):
        profile = derive_profile(
            derive_signals(_history([str(500 + n * 100) for n in range(30)])),
            observation_days=210,
            total_observations=30,
        )
        total = sum(Decimal(v) for v in profile.category_affinities.values())
        assert Decimal("0.99") <= total <= Decimal("1.01")

    def test_affinities_are_strings_not_floats(self):
        """ADR-003 applies inside JSONB too, and `test_no_float_money` cannot
        see a value that only exists at runtime."""
        profile = derive_profile(
            derive_signals(_history([str(500 + n * 300) for n in range(20)])),
            observation_days=90,
            total_observations=20,
        )
        assert all(isinstance(v, str) for v in profile.category_affinities.values())

    def test_every_factor_names_a_cause_and_an_effect(self):
        profile = derive_profile(
            derive_signals(_history([str(500 + n * 100) for n in range(30)])),
            observation_days=210,
            total_observations=30,
        )
        for factor in profile.factors:
            assert set(factor) == {"name", "detail", "effect"}
            assert all(factor.values())

    def test_a_short_window_says_so(self):
        profile = derive_profile(
            derive_signals(_history([str(500 + n * 300) for n in range(20)])),
            observation_days=60,
            total_observations=20,
        )
        assert any("six months" in c for c in profile.caveats)


class TestArchetypes:
    def test_an_empty_profile_matches_nothing(self):
        assert match(derive_profile([], observation_days=0, total_observations=0)) == []

    def test_widely_spaced_purchases_read_as_considered(self):
        signals = derive_signals(_history([str(500 + n * 300) for n in range(30)]))
        profile = derive_profile(signals, observation_days=200, total_observations=30)

        slugs = [a.slug for a, _ in match(profile)]
        assert slugs, "a real profile should match something"

    def test_no_match_falls_below_the_threshold(self):
        profile = derive_profile(
            derive_signals(_history([str(500 + n * 300) for n in range(20)])),
            observation_days=90,
            total_observations=20,
        )
        for _, score in match(profile):
            assert score >= MATCH_THRESHOLD

    def test_the_constants_stay_in_a_defensible_range(self):
        """A constant nobody can find is a magic number with extra steps."""
        assert Decimal("0.5") < BIG_PURCHASE_QUANTILE < Decimal("1")
        assert Decimal("1") < BIG_PURCHASE_SEPARATION
