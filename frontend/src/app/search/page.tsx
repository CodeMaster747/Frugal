"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft } from "lucide-react";

import { Button } from "@/components/ui/button";
import { fetchCheaper, fetchItemPrices, searchItems } from "@/features/map/api";
import { keys } from "@/lib/api/query-keys";
import { formatDate, formatMoney } from "@/lib/format";

/**
 * Find an item, then see where it is cheapest.
 *
 * Outside the dashboard group on purpose: it is reachable from the public map,
 * and everything it shows is an aggregate over the k-anonymity floor (ADR-013)
 * that names nobody. Asking someone to sign in before they can see whether the
 * product knows anything about their area is asking them to buy before looking.
 */
export default function SearchPage() {
  const [draft, setDraft] = useState("");
  const [query, setQuery] = useState("");
  const [itemId, setItemId] = useState<string | null>(null);
  const [paid, setPaid] = useState("");

  const items = useQuery({
    queryKey: keys.map.itemSearch(query),
    queryFn: () => searchItems(query),
    enabled: query.length >= 2,
  });

  const prices = useQuery({
    queryKey: keys.map.itemPrices(itemId ?? ""),
    queryFn: () => fetchItemPrices(itemId!),
    enabled: itemId !== null,
  });

  const cheaper = useQuery({
    queryKey: ["cheaper", itemId ?? "", paid],
    queryFn: () => fetchCheaper(itemId!, paid),
    enabled: itemId !== null && paid.trim().length > 0,
  });

  return (
    <main className="mx-auto w-full px-4 py-6 sm:px-6 lg:px-8 xl:max-w-[75rem]">
      <Link href="/" className="inline-flex items-center gap-1 type-meta text-ink-muted">
        <ArrowLeft className="size-3.5" aria-hidden />
        Back to the map
      </Link>

      <h1 className="mt-3 type-title">Find something cheaper</h1>

      <form
        className="mt-4 flex gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          setItemId(null);
          setQuery(draft.trim());
        }}
      >
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder="Amul Taaza 500ml"
          className="h-11 flex-1 rounded-control border border-gridline bg-transparent px-3 type-body"
          aria-label="Item to search for"
        />
        <Button type="submit" disabled={draft.trim().length < 2}>
          Search
        </Button>
      </form>

      {items.data?.length === 0 && (
        <p className="mt-6 type-body text-ink-muted">
          Nothing matches yet. The catalogue is built from bills people upload, so an item
          appears once somebody has bought it and scanned the receipt.
        </p>
      )}

      <ul className="mt-6 space-y-2">
        {items.data?.map((item) => (
          <li key={item.id}>
            <button
              type="button"
              onClick={() => setItemId(item.id)}
              aria-current={itemId === item.id ? "true" : undefined}
              className="w-full rounded-control border border-hairline p-3 text-left"
            >
              <p className="type-body">{item.name}</p>
              <p className="type-meta text-ink-muted">seen {item.observations} times</p>
            </button>
          </li>
        ))}
      </ul>

      {itemId && (
        <section className="mt-8">
          <h2 className="type-section">Where it is cheapest</h2>

          {prices.data?.length === 0 && (
            <p className="mt-2 type-body text-ink-muted">
              Not enough reports yet. A shop&rsquo;s price is shown once a few different people
              have reported the same item there — one person&rsquo;s receipt is a fact about
              that person, not about the shop.
            </p>
          )}

          {/* What you paid, against what other people did. Ascending price is
           * arithmetic over a column -- no score and no verdict -- so there is
           * nothing here ADR-002 would want factors for. The caveats carry the
           * honesty instead: how many people said so, and how long ago. */}
          <label className="mt-3 block max-w-xs">
            <span className="type-meta text-ink-secondary">What did you pay?</span>
            <input
              value={paid}
              onChange={(event) => setPaid(event.target.value)}
              inputMode="decimal"
              placeholder="32.00"
              className="tabular mt-1 h-10 w-full rounded-control border border-gridline bg-transparent px-2 type-body"
            />
          </label>

          {cheaper.data && (
            <div className="mt-3 rounded-card border border-hairline bg-surface-raised p-3">
              <p className="type-body">
                {formatMoney(cheaper.data.saving)} cheaper at{" "}
                <strong>{cheaper.data.store.name}</strong> ({cheaper.data.saving_percent}% less)
              </p>
              <ul className="mt-1 space-y-0.5">
                {cheaper.data.caveats.map((caveat) => (
                  <li key={caveat} className="type-meta text-ink-muted">
                    {caveat}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {cheaper.isFetched && cheaper.data === null && paid.trim() && (
            <p className="mt-3 type-meta text-ink-muted">
              Nowhere reported cheaper than that. That is a real answer, not a missing one.
            </p>
          )}

          <ul className="mt-2 divide-y divide-hairline">
            {prices.data?.map((price) => (
              <li
                key={price.store.id}
                className="flex items-baseline justify-between gap-4 py-3"
              >
                <div className="min-w-0">
                  <p className="type-body">{price.store.name}</p>
                  <p className="type-meta text-ink-muted">
                    {price.contributors} shoppers · last seen {formatDate(price.last_seen_on)}
                  </p>
                </div>
                <span className="tabular type-body">{formatMoney(price.median_price)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
