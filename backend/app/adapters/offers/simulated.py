"""The mandatory fake, built on the seeded catalogue.

ADR-004 requires every port to ship one, and this is more than a test double:
it is the default in every environment without an API key, which means CI,
local development, and any deployment where the owner has not opted in.

**It says so everywhere it surfaces.** `provider` is `"simulated"`, and the
market service turns that into a caveat on the response. Presenting invented
prices as real listings would be the same failure ADR-002 exists to prevent --
a conclusion the user cannot interrogate -- and here it would also be a claim
about named retailers that happens not to be true.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.adapters.ports import RetailOffer


class SimulatedOfferSearch:
    """Offers derived from the seeded catalogue's competing sellers.

    Deterministic: the same query on the same day yields the same offers, which
    is what lets the offers panel be screenshotted, demonstrated and tested
    without a network.
    """

    name = "simulated"

    async def find_offers(self, query: str, *, limit: int = 10) -> list[RetailOffer]:
        from app.adapters.pricing.catalog import BY_ID
        from app.adapters.pricing.simulated_market import SimulatedMarketProvider

        provider = SimulatedMarketProvider()
        matches = await provider.search(query, limit=3)
        if not matches:
            return []

        today = datetime.now(UTC)
        item = BY_ID.get(matches[0].external_id)
        if item is None:
            return []

        offers = [
            RetailOffer(
                title=item.full_name,
                price=seller.price,
                seller=seller.seller_name,
                # A simulated listing has nowhere real to link to, and inventing
                # a retailer URL would be the one part of this a user could not
                # tell was fabricated.
                link="",
                as_of=today,
                provider=self.name,
                seller_rating=seller.seller_rating,
                rating_count=seller.rating_count,
                delivery_note=seller.fulfillment_type,
            )
            for seller in provider.sellers_for(item, today.date())
            if seller.in_stock
        ]
        return sorted(offers, key=lambda o: o.price)[:limit]


def median_price(offers: list[RetailOffer]) -> Decimal | None:
    """The market median across a result set.

    Computed locally rather than requested, because it is arithmetic over data
    already in hand -- and it is the one reliability signal a shopping API does
    not return but the rubric needs.
    """
    if not offers:
        return None
    prices = sorted(o.price for o in offers)
    middle = len(prices) // 2
    if len(prices) % 2 == 1:
        return prices[middle]
    return (prices[middle - 1] + prices[middle]) / 2
