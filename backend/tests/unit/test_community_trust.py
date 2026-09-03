"""The trust rubric, with no database (ADR-013 §7).

Trust decides whether one person's claim reaches everybody else, so the weights
need to be something you can reason about. Pure rules make that possible.
"""

from __future__ import annotations

from decimal import Decimal

from app.modules.community.trust import (
    MAX_TRUST,
    PROMOTION_THRESHOLD,
    STARTING_TRUST,
    TrustInputs,
    compute,
    promotable,
)


class TestTheScore:
    def test_a_new_contributor_starts_at_the_floor(self):
        """Below the floor would be tempting and wrong.

        A new user's first receipt would then never be promoted, and promotion
        is what earns the trust that promotion requires -- a loop nobody
        escapes.
        """
        result = compute(TrustInputs())
        assert result.score == STARTING_TRUST
        assert result.factors, "ADR-002: a score with no factors is a bug"

    def test_verified_contributions_raise_it(self):
        assert compute(TrustInputs(accepted_promotions=5)).score > STARTING_TRUST

    def test_disputes_cost_more_than_contributions_earn(self):
        """The cost of publishing a wrong price to everybody is much higher
        than the cost of not publishing a right one."""
        gained = compute(TrustInputs(accepted_promotions=1)).score - STARTING_TRUST
        lost = STARTING_TRUST - compute(TrustInputs(disputed_reports=1)).score
        assert lost > gained

    def test_retracting_costs_most_of_all(self):
        """Otherwise the cheapest strategy is to post freely and withdraw
        whatever gets challenged."""
        disputed = compute(TrustInputs(disputed_reports=1)).score
        retracted = compute(TrustInputs(retracted_contributions=1)).score
        assert retracted < disputed

    def test_it_never_reaches_certainty(self):
        assert compute(TrustInputs(accepted_promotions=10_000)).score == MAX_TRUST

    def test_it_never_goes_below_zero(self):
        assert compute(TrustInputs(retracted_contributions=10_000)).score == Decimal("0.000")

    def test_every_factor_names_a_cause_and_an_effect(self):
        result = compute(
            TrustInputs(accepted_promotions=3, agreed_verifications=2, disputed_reports=1)
        )
        for factor in result.factors:
            assert set(factor) == {"name", "detail", "effect"}
            assert all(factor.values())


class TestPromotion:
    def test_an_author_can_never_promote_their_own_report(self):
        """The halving is what makes self-promotion arithmetically impossible.

        Even at MAX_TRUST an author contributes 0.475, below the 0.600
        threshold. This property lives in the relationship between three
        constants and would break silently if any one of them moved.
        """
        assert not promotable(Decimal(0), MAX_TRUST)

    def test_one_new_confirmer_is_not_enough(self):
        assert not promotable(STARTING_TRUST, STARTING_TRUST)

    def test_several_new_confirmers_are(self):
        assert promotable(STARTING_TRUST * 3, STARTING_TRUST)

    def test_one_established_confirmer_counts_for_several_new_ones(self):
        """Three fresh accounts agreeing is the shape sockpuppeting takes.

        Weighting by trust rather than counting heads is what makes creating
        accounts a poor way to publish a price.
        """
        established = Decimal("0.500")
        assert promotable(established, STARTING_TRUST)
        assert not promotable(STARTING_TRUST * 2, STARTING_TRUST)

    def test_the_threshold_is_reachable_at_all(self):
        """A guard against a future weight change making promotion impossible."""
        assert promotable(MAX_TRUST, MAX_TRUST)
        assert PROMOTION_THRESHOLD < MAX_TRUST + MAX_TRUST / 2
