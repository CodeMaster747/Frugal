"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink, Search } from "lucide-react";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { searchOffers, type RetailOffer } from "@/features/market/api";
import { formatMoney } from "@/lib/format";
import { keys } from "@/lib/api/query-keys";

/**
 * Where a product is cheapest, next to the advice about whether to buy it.
 *
 * Two things this deliberately does not do.
 *
 * It does not search automatically. The free allowance is a couple of hundred
 * searches a month for the whole deployment, so a search fired on page load
 * would spend the budget on people who never asked (ADR-008). It waits for a
 * click.
 *
 * It does not rank the offers by anything other than price. Ascending price is
 * arithmetic over a column — no score, no verdict, nothing asserted. The one
 * judgement on the page is each seller's reliability, and that comes from the
 * published rubric the watchlist already uses, whose weights sum to 1.00 and
 * whose bands are worded about the *offer* rather than the seller.
 */
export function OffersPanel({
  query,
  askingPrice,
  currency = "INR",
}: {
  query: string;
  /** The price the advice was computed against, for the saving comparison. */
  askingPrice: string;
  currency?: string;
}) {
  const [searched, setSearched] = useState(false);

  const offers = useQuery({
    queryKey: keys.market.offers(query, askingPrice),
    queryFn: () => searchOffers(query, askingPrice),
    enabled: searched,
    // A price is worth re-fetching rarely. The server caches for 24 hours
    // anyway, so a refetch here would usually be a cache hit -- but not always,
    // and "usually free" is not a budget.
    staleTime: 60 * 60 * 1000,
    refetchOnWindowFocus: false,
  });

  if (!searched) {
    return (
      <Section variant="bordered" headingLevel={3} title="Is it cheaper elsewhere?">
        <div className="space-y-3">
          <p className="type-body text-ink-secondary">
            The advice above is about whether you can afford{" "}
            {formatMoney(askingPrice, currency)}. This is a different question: whether anyone
            is selling it for less.
          </p>
          <Button onClick={() => setSearched(true)} size="sm" variant="secondary">
            <Search className="size-4" aria-hidden />
            Look for a better price
          </Button>
        </div>
      </Section>
    );
  }

  return (
    <Section variant="bordered" headingLevel={3} title="Is it cheaper elsewhere?">
      <div className="space-y-4" data-testid="offers-panel">
        {offers.isPending && (
          // Named as slow rather than left silent. A live shopping search takes
          // ten to thirty seconds, and an unqualified "Looking…" for that long
          // reads as broken -- people reload, which spends the quota twice.
          <p className="type-body text-ink-muted">
            Checking prices across retailers — this can take up to half a minute.
          </p>
        )}

        {offers.isError && (
          <p className="type-body text-ink-secondary">
            Could not reach the price service. The advice above is unaffected — it never
            depended on this.
          </p>
        )}

        {offers.data && (
          <>
            {offers.data.saving && (
              <p className="type-body" data-testid="offer-saving">
                Cheapest found is <strong>{formatMoney(offers.data.saving, currency)}</strong>
                {offers.data.saving_percent && ` (${offers.data.saving_percent}%)`} below the
                price this advice used.
              </p>
            )}

            {offers.data.offers.length === 0 && (
              <p className="type-body text-ink-secondary">
                Nothing matched. That is an answer, not a failure — the advice above stands.
              </p>
            )}

            {offers.data.offers.length > 0 && (
              <ul className="divide-y divide-hairline" data-testid="offer-list">
                {offers.data.offers.map((offer, i) => (
                  <OfferRow key={`${offer.seller}-${i}`} offer={offer} currency={currency} />
                ))}
              </ul>
            )}

            {/* Never decoration. A panel of simulated prices that does not say so
             * is a false claim about named retailers; one showing cached prices
             * without a date implies a currency it does not have. */}
            {offers.data.caveats.length > 0 && (
              <div
                className="space-y-1.5 rounded-card bg-surface-raised p-4"
                data-testid="offer-caveats"
              >
                {offers.data.caveats.map((caveat) => (
                  <p className="type-meta text-ink-secondary" key={caveat}>
                    {caveat}
                  </p>
                ))}
              </div>
            )}
          </>
        )}
      </div>
    </Section>
  );
}

function OfferRow({ offer, currency }: { offer: RetailOffer; currency: string }) {
  return (
    <li className="flex flex-wrap items-baseline justify-between gap-3 py-3">
      <div className="min-w-0 space-y-0.5">
        <p className="type-body">
          {offer.link ? (
            <a
              className="inline-flex items-center gap-1"
              href={offer.link}
              rel="noreferrer noopener nofollow"
              target="_blank"
            >
              {offer.seller}
              <ExternalLink className="size-3.5" aria-hidden />
            </a>
          ) : (
            offer.seller
          )}
        </p>
        <p className="type-meta text-ink-muted">
          {offer.reliability.band}
          {offer.seller_rating && ` · rated ${offer.seller_rating}`}
          {offer.rating_count !== null &&
            ` from ${offer.rating_count.toLocaleString()} reviews`}
          {offer.delivery_note && ` · ${offer.delivery_note}`}
        </p>
      </div>
      <p className="tabular type-body font-medium">{formatMoney(offer.price, currency)}</p>
    </li>
  );
}
