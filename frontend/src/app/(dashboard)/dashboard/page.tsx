"use client";

import { useQuery } from "@tanstack/react-query";
import { Bell } from "lucide-react";
import Link from "next/link";

import { ChartContainer, Legend } from "@/components/charts/chart-container";
import { GroupedBars, StackedShare, TrendLine } from "@/components/charts/primitives";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/empty";
import { useAuth } from "@/features/auth/auth-provider";
import { getDashboard, getSavingsRate, type CategorySlice } from "@/features/analytics/api";
import { StatTile } from "@/features/analytics/components/stat-tile";
import { SpendingProfile } from "@/features/personalization/spending-profile";
import { listAccounts } from "@/features/finance/api";
import { EmptyState } from "@/features/finance/components/empty-state";
import { formatMoney } from "@/lib/format";
import { keys } from "@/lib/api/query-keys";

/** "2026-08" -> "Aug" */
const monthLabel = (period: string) =>
  new Date(`${period}-01T00:00:00`).toLocaleDateString("en-IN", { month: "short" });

function pctChange(current: string, previous: string): number | null {
  const before = Number(previous);
  if (!Number.isFinite(before) || before === 0) return null;
  return ((Number(current) - before) / before) * 100;
}

export default function Dashboard() {
  const { user } = useAuth();
  const currency = user?.base_currency ?? "INR";

  const accounts = useQuery({ queryKey: keys.finance.accounts(), queryFn: listAccounts });
  const dashboard = useQuery({
    queryKey: keys.analytics.dashboard(),
    queryFn: () => getDashboard(),
  });
  const savings = useQuery({
    queryKey: keys.analytics.savingsRate(),
    queryFn: () => getSavingsRate(12),
  });

  if (accounts.isPending || dashboard.isPending) return <Skeleton />;

  const data = dashboard.data!;
  if (data.transaction_count === 0 && (accounts.data?.length ?? 0) === 0) {
    return <EmptyState name={user?.display_name ?? "there"} />;
  }

  const cashflow = data.cashflow.map((p) => ({
    month: monthLabel(p.period),
    income: Number(p.income),
    expense: Number(p.expense),
  }));

  const netWorthTrend = data.net_worth_trend.map((p) => ({
    month: monthLabel(p.period),
    value: p.value === null ? null : Number(p.value),
  }));

  const savingsTrend = (savings.data ?? []).map((p) => ({
    month: monthLabel(p.period),
    // null stays null: a month with no income has an undefined rate, and
    // plotting zero would draw a cliff the user never experienced.
    value: p.value === null ? null : Number(p.value) * 100,
  }));

  const rate = data.totals.savings_rate;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="type-title">Overview</h1>
        <div className="flex flex-wrap gap-2">
          {/* Alerts is the notification feed -- things worth interrupting you
           * for -- which is a fact about the whole account rather than about
           * any one screen. So it hangs off the account's summary. */}
          <Button variant="secondary" size="sm" asChild>
            <Link href="/alerts">
              <Bell aria-hidden />
              Alerts
            </Link>
          </Button>
          <Button variant="secondary" size="sm" asChild>
            <Link href="/transactions">All transactions</Link>
          </Button>
        </div>
      </div>

      {/* KPI row of stat tiles, not a grouped bar chart: these are four
          unrelated headline numbers, and a chart would invite comparisons
          between them that mean nothing. */}
      <section className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4" data-testid="kpi-row">
        <StatTile
          label="Net worth"
          value={formatMoney(data.net_worth, currency)}
          trend={netWorthTrend.filter((p) => p.value !== null)}
          trendKey="value"
          slot={3}
        />
        <StatTile
          label="Income this month"
          value={formatMoney(data.totals.income, currency)}
          delta={pctChange(data.totals.income, data.previous_totals.income)}
          deltaLabel="vs same period last month"
          slot={1}
        />
        <StatTile
          label="Spent this month"
          value={formatMoney(data.totals.expense, currency)}
          delta={pctChange(data.totals.expense, data.previous_totals.expense)}
          deltaLabel="vs same period last month"
          slot={2}
        />
        <StatTile
          label="Savings rate"
          value={rate === null ? "—" : `${(Number(rate) * 100).toFixed(1)}%`}
          caveat={rate === null ? "No income recorded this month" : undefined}
          trend={savingsTrend.filter((p) => p.value !== null)}
          trendKey="value"
          slot={3}
        />
      </section>

      <div className="grid gap-4 lg:grid-cols-2">
        <ChartContainer
          title="Income vs expense"
          caveat="Last 6 months"
          summary={cashflowSummary(data.cashflow, currency)}
          slots={[1, 2]}
          rows={data.cashflow}
          columns={[
            { header: "Month", cell: (r) => monthLabel(r.period) },
            { header: "Income", numeric: true, cell: (r) => formatMoney(r.income, currency) },
            { header: "Expense", numeric: true, cell: (r) => formatMoney(r.expense, currency) },
            { header: "Net", numeric: true, cell: (r) => formatMoney(r.net, currency) },
          ]}
        >
          <GroupedBars
            data={cashflow}
            xKey="month"
            series={[
              { key: "income", label: "Income", slot: 1 },
              { key: "expense", label: "Expense", slot: 2 },
            ]}
          />
          <Legend
            items={[
              { label: "Income", slot: 1 },
              { label: "Expense", slot: 2 },
            ]}
          />
        </ChartContainer>

        <ChartContainer
          title="Net worth"
          caveat="Last 12 months"
          summary={trendSummary(netWorthTrend, currency)}
          slots={[3]}
          // Slot 3 is below 3:1 on the light surface, so the table view is not
          // optional here — the container would flag its absence.
          rows={data.net_worth_trend}
          columns={[
            { header: "Month", cell: (r) => monthLabel(r.period) },
            {
              header: "Net worth",
              numeric: true,
              cell: (r) => (r.value === null ? "—" : formatMoney(r.value, currency)),
            },
          ]}
        >
          <TrendLine data={netWorthTrend} xKey="month" yKey="value" slot={3} />
        </ChartContainer>
      </div>

      <CategoryBreakdown categories={data.top_categories} currency={currency} />

      {/* Renders nothing when there is no profile -- which covers the default
       * deployment, where no personalization database is configured at all. */}
      <SpendingProfile />
    </div>
  );
}

function CategoryBreakdown({
  categories,
  currency,
}: {
  categories: CategorySlice[];
  currency: string;
}) {
  if (categories.length === 0) {
    return <Empty>No spending recorded this month yet.</Empty>;
  }

  // Slots are assigned in fixed order and never cycled. Past four categories
  // the share bar stops colouring and the table carries the tail -- generating
  // a ninth hue would produce something indistinguishable under CVD.
  const segments = categories.slice(0, 4).map((c, i) => ({
    label: c.name,
    pct: Number(c.share_pct),
    slot: (i + 1) as 1 | 2 | 3 | 4,
  }));

  return (
    <ChartContainer
      title="Where it went"
      caveat="This month, by category"
      summary={categorySummary(categories, currency)}
      slots={[1, 2, 3, 4]}
      rows={categories}
      columns={[
        { header: "Category", cell: (c) => c.name },
        { header: "Amount", numeric: true, cell: (c) => formatMoney(c.amount, currency) },
        { header: "Share", numeric: true, cell: (c) => `${Number(c.share_pct).toFixed(1)}%` },
        {
          header: "vs last month",
          numeric: true,
          cell: (c) =>
            c.change_pct === null ? (
              <span className="text-ink-muted">new</span>
            ) : (
              <span className={Number(c.change_pct) > 0 ? "text-serious" : "text-delta-up"}>
                {Number(c.change_pct) > 0 ? "▲" : "▼"}{" "}
                {Math.abs(Number(c.change_pct)).toFixed(0)}%
              </span>
            ),
        },
      ]}
    >
      <div className="space-y-4">
        <StackedShare segments={segments} />
        {/* Direct labels, so the low-contrast slots are never the only cue. */}
        <ul className="grid gap-x-10 gap-y-2 sm:grid-cols-2" data-testid="category-list">
          {categories.map((c, i) => (
            <li key={c.slug} className="flex items-center gap-2 type-body">
              {i < 4 && (
                <span
                  className="size-2.5 shrink-0 rounded-swatch"
                  style={{ backgroundColor: `var(--series-${i + 1})` }}
                  aria-hidden
                />
              )}
              <span className="min-w-0 flex-1 truncate">{c.name}</span>
              <span className="tabular text-ink-secondary">
                {formatMoney(c.amount, currency)}
              </span>
              <span className="w-12 text-right type-meta text-ink-muted">
                {Number(c.share_pct).toFixed(0)}%
              </span>
            </li>
          ))}
        </ul>
      </div>
    </ChartContainer>
  );
}

// --- screen-reader summaries ------------------------------------------------
// Every chart owes a non-sighted user the same information the picture carries.

function cashflowSummary(
  points: { period: string; income: string; expense: string }[],
  c: string,
) {
  if (points.length === 0) return "No cash-flow data yet.";
  const last = points[points.length - 1];
  return `Income versus expense over ${points.length} months. Most recently, income ${formatMoney(last.income, c)} against expense ${formatMoney(last.expense, c)}.`;
}

function trendSummary(points: { month: string; value: number | null }[], c: string) {
  const values = points.filter((p) => p.value !== null);
  if (values.length < 2) return "Not enough history to show a trend yet.";
  const first = values[0].value!;
  const last = values[values.length - 1].value!;
  const direction = last > first ? "rose" : last < first ? "fell" : "was flat";
  return `Net worth ${direction} from ${formatMoney(String(first), c)} to ${formatMoney(String(last), c)} over ${values.length} months.`;
}

function categorySummary(categories: CategorySlice[], c: string) {
  const top = categories[0];
  return `Spending across ${categories.length} categories. Largest is ${top.name} at ${formatMoney(top.amount, c)}, ${Number(top.share_pct).toFixed(0)} percent of the total.`;
}

function Skeleton() {
  return (
    <div className="space-y-8" aria-busy="true">
      <div className="h-8 w-40 animate-pulse rounded-control bg-surface" />
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-28 animate-pulse rounded-card bg-surface" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        {[0, 1].map((i) => (
          <div key={i} className="h-64 animate-pulse rounded-card bg-surface" />
        ))}
      </div>
    </div>
  );
}
