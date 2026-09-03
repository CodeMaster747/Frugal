"use client";

import { useQuery } from "@tanstack/react-query";

import { StatTile } from "@/features/analytics/components/stat-tile";
import { Section } from "@/components/ui/section";
import { apiFetch } from "@/lib/api/client";
import { keys } from "@/lib/api/query-keys";
import { formatDate } from "@/lib/format";

interface Balance {
  balance: number;
  redeemable: boolean;
  message: string;
}

interface Entry {
  id: string;
  reason: string;
  points: number;
  subject_type: string | null;
  note: string | null;
  created_at: string;
}

interface Reward {
  id: string;
  slug: string;
  title: string;
  description: string;
  cost_points: number;
  is_available: boolean;
}

const REASON_LABELS: Record<string, string> = {
  receipt_verified: "Bill verified",
  price_promoted: "Price added to the shared graph",
  store_report: "Shop report",
  report_verified: "Confirmed someone's report",
  report_upvoted: "Your contribution was upvoted",
  item_merge_confirmed: "Confirmed an item match",
  store_pin_confirmed: "Confirmed a shop",
  reversal: "Reversed",
};

/**
 * Points earned, and what they are for.
 *
 * The catalogue is shown even though nothing can be redeemed yet, and the page
 * says so plainly rather than leaving it to be discovered. A number that goes
 * up and buys nothing, with no indication of what it might ever buy, is a
 * counter rather than a reward.
 */
export default function PointsPage() {
  const balance = useQuery({
    queryKey: keys.points.balance(),
    queryFn: () => apiFetch<Balance>("/api/v1/points/balance"),
  });
  const ledger = useQuery({
    queryKey: keys.points.ledger(),
    queryFn: () => apiFetch<Entry[]>("/api/v1/points/ledger"),
  });
  const rewards = useQuery({
    queryKey: keys.points.rewards(),
    queryFn: () => apiFetch<Reward[]>("/api/v1/rewards"),
  });

  return (
    <>
      <h1 className="type-title">Points</h1>
      <p className="mt-1 type-body text-ink-secondary">
        Earned for contributing prices other people can use.
      </p>

      <div className="mt-6 grid gap-4 sm:grid-cols-2">
        <StatTile label="Balance" value={String(balance.data?.balance ?? 0)} />
        <StatTile label="Contributions" value={String(ledger.data?.length ?? 0)} />
      </div>

      {balance.data && !balance.data.redeemable && (
        <p className="mt-4 rounded-card border border-hairline bg-surface-raised p-3 type-body text-ink-secondary">
          {balance.data.message}
        </p>
      )}

      <Section title="What points will buy" variant="bordered" className="mt-6">
        <ul className="space-y-3">
          {rewards.data?.map((reward) => (
            <li key={reward.id} className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <p className="type-body font-medium">{reward.title}</p>
                <p className="type-meta text-ink-muted">{reward.description}</p>
              </div>
              <div className="shrink-0 text-right">
                <p className="tabular type-body">{reward.cost_points}</p>
                <p className="type-meta text-ink-muted">
                  {reward.is_available ? "available" : "coming soon"}
                </p>
              </div>
            </li>
          ))}
        </ul>
      </Section>

      <Section title="History" variant="bordered" className="mt-6">
        {ledger.data?.length === 0 && (
          <p className="type-body text-ink-muted">
            Nothing yet. Scan a bill, or add a shop to the map.
          </p>
        )}
        <ul className="divide-y divide-hairline">
          {ledger.data?.map((entry) => (
            <li key={entry.id} className="flex items-baseline justify-between gap-4 py-2">
              <div className="min-w-0">
                <p className="type-body">{REASON_LABELS[entry.reason] ?? entry.reason}</p>
                {entry.note && <p className="type-meta text-ink-muted">{entry.note}</p>}
                <p className="type-meta text-ink-muted">{formatDate(entry.created_at)}</p>
              </div>
              {/* Reversals are shown as reversals rather than quietly netted
               * out: the ledger is append-only precisely so a user can see why
               * their balance moved. */}
              <span className="tabular type-body">
                {entry.points > 0 ? `+${entry.points}` : entry.points}
              </span>
            </li>
          ))}
        </ul>
      </Section>
    </>
  );
}
