"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Info, Search, TrendingDown, X } from "lucide-react";
import { useState } from "react";

import { ChartContainer } from "@/components/charts/chart-container";
import { TrendLine } from "@/components/charts/primitives";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/empty";
import { Field } from "@/components/ui/field";
import { searchProducts, type Offer as CatalogOffer } from "@/features/advisor/api";
import {
  addToWishlist,
  checkAlerts,
  getAlerts,
  getProductDetail,
  getReliabilityRubric,
  getWishlist,
  removeFromWishlist,
  type Offer,
  type WishlistItem,
} from "@/features/market/api";
import { formatDate, formatMoney } from "@/lib/format";
import { keys } from "@/lib/api/query-keys";

export default function WatchlistPage() {
  const queryClient = useQueryClient();
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const [showRubric, setShowRubric] = useState(false);

  const wishlist = useQuery({ queryKey: keys.market.wishlist(), queryFn: getWishlist });
  const alerts = useQuery({ queryKey: keys.market.alerts(), queryFn: getAlerts });
  const results = useQuery({
    queryKey: keys.market.search(submitted),
    queryFn: () => searchProducts(submitted),
    enabled: submitted.length > 0,
  });
  const rubric = useQuery({
    queryKey: keys.market.reliabilityRubric(),
    queryFn: getReliabilityRubric,
    enabled: showRubric,
  });

  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.market.wishlist() }),
      queryClient.invalidateQueries({ queryKey: keys.market.alerts() }),
    ]);

  const track = useMutation({
    mutationFn: (offer: CatalogOffer) => addToWishlist({ external_id: offer.external_id }),
    onSuccess: invalidate,
  });
  const untrack = useMutation({ mutationFn: removeFromWishlist, onSuccess: invalidate });
  const check = useMutation({ mutationFn: checkAlerts, onSuccess: invalidate });

  const unread = (alerts.data ?? []).filter((a) => a.read_at === null);

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="type-title">Watchlist</h1>
          <p className="mt-1 type-body text-ink-secondary">
            Track a price and we&rsquo;ll tell you when it drops. Checked once a day.
          </p>
        </div>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => check.mutate()}
          disabled={check.isPending}
          data-testid="check-drops"
        >
          <TrendingDown aria-hidden />
          {check.isPending ? "Checking…" : "Check now"}
        </Button>
      </div>

      {unread.length > 0 && (
        <section className="space-y-2" data-testid="price-alerts">
          {unread.map((alert) => (
            <p
              key={alert.id}
              className="flex items-start gap-2 rounded-card border border-good/40 bg-surface px-4 py-3 type-body"
            >
              <BellRing className="mt-0.5 size-4 shrink-0 text-good" aria-hidden />
              <span>
                Price dropped {(Number(alert.drop_fraction) * 100).toFixed(1)}% at{" "}
                <strong>{alert.seller_name}</strong> —{" "}
                {formatMoney(alert.previous_price, "INR")} →{" "}
                <strong>{formatMoney(alert.new_price, "INR")}</strong>
                {alert.is_lowest_recorded && " · lowest price we have recorded"}
              </span>
            </p>
          ))}
        </section>
      )}

      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted(query.trim());
        }}
      >
        <div className="min-w-56 flex-1">
          <Field
            label="Track something"
            placeholder="e.g. iPhone 16"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
          />
        </div>
        <Button type="submit" disabled={!query.trim()} data-testid="search-track">
          <Search aria-hidden />
          Search
        </Button>
      </form>

      {submitted && results.data && results.data.length > 0 && (
        <ul
          className="divide-y divide-hairline rounded-card border border-hairline bg-surface"
          data-testid="track-results"
        >
          {results.data.map((offer) => (
            <li key={offer.external_id} className="flex flex-wrap items-center gap-3 px-4 py-3">
              <span className="min-w-0 flex-1 truncate type-body font-medium">
                {offer.name}
              </span>
              <span className="tabular type-body">{formatMoney(offer.price, "INR")}</span>
              <Button
                size="sm"
                variant="secondary"
                onClick={() => track.mutate(offer)}
                disabled={track.isPending}
              >
                Track
              </Button>
            </li>
          ))}
        </ul>
      )}

      {track.isError && (
        <p role="alert" className="type-body text-critical">
          {(track.error as Error).message}
        </p>
      )}

      <section className="space-y-3">
        <div className="flex items-baseline justify-between gap-3">
          <h2 className="type-section">Tracked</h2>
          <Button variant="ghost" size="sm" onClick={() => setShowRubric((v) => !v)}>
            How is reliability scored?
          </Button>
        </div>

        {showRubric && rubric.data && (
          <div
            className="space-y-2 rounded-card border border-hairline bg-surface-raised p-4 type-body"
            data-testid="reliability-rubric"
          >
            <p className="text-ink-secondary">{rubric.data.what_this_is}</p>
            {/* Displayed as prominently as the definition. A score about a
                named business has to be explicit about the claim it does not
                make (FR-9.2). */}
            <p className="flex items-start gap-2 text-ink-secondary">
              <Info className="mt-0.5 size-4 shrink-0" aria-hidden />
              {rubric.data.what_this_is_not}
            </p>
            <ul className="mt-2 grid gap-1 type-meta text-ink-muted sm:grid-cols-2">
              {rubric.data.signals.map((signal) => (
                <li key={signal.key} className="tabular">
                  {signal.name} · weight {signal.weight}
                </li>
              ))}
            </ul>
            <p className="type-meta text-ink-muted">{rubric.data.missing_signals}</p>
          </div>
        )}

        {wishlist.isPending ? (
          <p className="type-body text-ink-muted">Loading…</p>
        ) : wishlist.data && wishlist.data.length > 0 ? (
          <ul className="space-y-3" data-testid="wishlist">
            {wishlist.data.map((item) => (
              <TrackedItem
                key={item.id}
                item={item}
                expanded={open === item.id}
                onToggle={() => setOpen(open === item.id ? null : item.id)}
                onRemove={() => untrack.mutate(item.id)}
              />
            ))}
          </ul>
        ) : (
          <Empty>
            Nothing tracked yet. Search above for something you are thinking about, and
            we&rsquo;ll watch its price.
          </Empty>
        )}
      </section>
    </div>
  );
}

function TrackedItem({
  item,
  expanded,
  onToggle,
  onRemove,
}: {
  item: WishlistItem;
  expanded: boolean;
  onToggle: () => void;
  onRemove: () => void;
}) {
  const detail = useQuery({
    queryKey: keys.market.product(item.product_id),
    queryFn: () => getProductDetail(item.product_id),
    enabled: expanded,
  });

  const change = item.change_since_added === null ? null : Number(item.change_since_added);
  const cheaper = change !== null && change < 0;
  // Rounded to the precision actually displayed, so a change of -0.0004 does
  // not render as "▼ 0.0% cheaper" — the arrow claimed a direction the number
  // beside it did not show.
  const moved = change !== null && Math.abs(change * 100) >= 0.05;

  return (
    <li className="rounded-card border border-hairline bg-surface" data-testid="wishlist-item">
      <div className="flex flex-wrap items-center gap-3 p-4">
        <span className="min-w-0 flex-1">
          <span className="block truncate type-body font-medium">{item.name}</span>
          <span className="block type-meta text-ink-muted">
            Tracking since {formatDate(item.created_at)}
            {item.is_at_lowest && " · at its lowest recorded price"}
          </span>
        </span>

        <span className="text-right">
          <span className="tabular block type-body font-medium">
            {item.current_price ? formatMoney(item.current_price, "INR") : "—"}
          </span>
          {change !== null && (
            <span
              className={`block type-meta ${moved && cheaper ? "text-good" : "text-ink-muted"}`}
            >
              {moved
                ? `${cheaper ? "▼" : "▲"} ${Math.abs(change * 100).toFixed(1)}% since you added it`
                : "unchanged since you added it"}
            </span>
          )}
        </span>

        <Button variant="ghost" size="sm" onClick={onToggle} aria-expanded={expanded}>
          {expanded ? "Hide" : "Price history"}
        </Button>
        <button
          type="button"
          className="grid size-7 shrink-0 place-items-center rounded-control text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink"
          aria-label={`Stop tracking ${item.name}`}
          onClick={onRemove}
        >
          <X className="size-3.5" aria-hidden />
        </button>
      </div>

      {expanded && detail.data && (
        <div className="space-y-6 border-t border-hairline p-4">
          <ChartContainer
            title="Price over time"
            summary={`Best available price per day over ${detail.data.history.length} days. Lowest recorded ${detail.data.lowest_recorded ?? "unknown"}.`}
            slots={[1]}
            rows={detail.data.history}
            columns={[
              { header: "Date", cell: (row) => formatDate(row.date) },
              {
                header: "Best price",
                numeric: true,
                cell: (row) => formatMoney(row.price, "INR"),
              },
              { header: "Sellers", numeric: true, cell: (row) => String(row.sellers) },
            ]}
          >
            <TrendLine
              data={detail.data.history.map((p) => ({
                date: formatDate(p.date),
                price: Number(p.price),
              }))}
              xKey="date"
              yKey="price"
              slot={1}
              height={180}
              fitDomain
            />
          </ChartContainer>

          <p className="tabular type-meta text-ink-muted">
            Lowest recorded {formatMoney(detail.data.lowest_recorded ?? "0", "INR")}
            {detail.data.lowest_recorded_on &&
              ` on ${formatDate(detail.data.lowest_recorded_on)}`}
            {" · "}median across sellers {formatMoney(detail.data.market_median ?? "0", "INR")}
          </p>

          <div>
            <h3 className="mb-2 type-body font-medium">Where to buy it</h3>
            <ul className="divide-y divide-hairline" data-testid="offers">
              {detail.data.offers.map((offer) => (
                <OfferRow key={offer.seller_name} offer={offer} />
              ))}
            </ul>
          </div>
        </div>
      )}
    </li>
  );
}

function OfferRow({ offer }: { offer: Offer }) {
  const [open, setOpen] = useState(false);
  const score = Number(offer.reliability.score);

  return (
    <li className="py-2.5">
      <div className="flex flex-wrap items-center gap-3">
        <span className="min-w-0 flex-1 type-body">{offer.seller_name}</span>
        <span className="tabular type-body">{formatMoney(offer.price, "INR")}</span>
        <span className="type-meta text-ink-secondary">
          {/* The band, in words. Never a colour on its own — a claim about a
              named business must be legible in greyscale and to a reader. */}
          {offer.reliability.band}
          <span className="tabular ml-1.5 text-ink-muted">({Math.round(score)}/100)</span>
        </span>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
        >
          {open ? "Hide" : "Why?"}
        </Button>
      </div>

      {open && (
        <div className="mt-2 space-y-2 rounded-control bg-surface-raised p-3">
          <ul className="space-y-1.5 type-meta">
            {offer.reliability.signals.map((signal) => (
              <li key={signal.key}>
                <span className="font-medium">{signal.name}</span>{" "}
                <span className="tabular text-ink-secondary">{signal.value}</span>
                <span className="tabular ml-2 text-ink-muted">
                  {signal.contribution} pts · weight {signal.weight}
                </span>
                <span className="block text-ink-muted">{signal.detail}</span>
              </li>
            ))}
          </ul>
          <ul className="list-disc space-y-1 pl-4 type-meta text-ink-muted">
            {offer.reliability.caveats.map((caveat) => (
              <li key={caveat}>{caveat}</li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}
