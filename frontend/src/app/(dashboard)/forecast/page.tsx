"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Loader2 } from "lucide-react";
import { useState } from "react";

import { ChartContainer } from "@/components/charts/chart-container";
import { ForecastBand } from "@/components/charts/primitives";
import { ExplanationPanel } from "@/components/explanation-panel";
import { Button } from "@/components/ui/button";
import { StatTile } from "@/features/analytics/components/stat-tile";
import { Field } from "@/components/ui/field";
import { Section } from "@/components/ui/section";
import {
  getForecast,
  getRecurring,
  isDeclined,
  runScenario,
  type Declined,
  type Forecast,
} from "@/features/forecast/api";
import { formatDate, formatMoney, todayISO } from "@/lib/format";
import { keys } from "@/lib/api/query-keys";

const HORIZONS = [30, 60, 90] as const;

/** What each tier is, in the user's terms rather than the model's. */
const METHOD_COPY: Record<Forecast["method"], string> = {
  recurring_projection: "Known commitments only",
  ewma_seasonal: "Recent averages and weekly pattern",
  prophet: "Full trend and seasonality model",
};

export default function ForecastPage() {
  const [horizon, setHorizon] = useState<number>(90);
  const [event, setEvent] = useState({ on: todayISO(), amount: "", label: "" });

  const forecast = useQuery({
    queryKey: keys.forecast.horizon(horizon),
    queryFn: () => getForecast(horizon),
    // The worker may be computing a better tier; the response says when.
    refetchInterval: (query) => {
      const data = query.state.data;
      return data && !isDeclined(data) && data.refining ? 4000 : false;
    },
  });

  const recurring = useQuery({ queryKey: keys.finance.recurring(), queryFn: getRecurring });

  const scenario = useMutation({
    mutationFn: () =>
      runScenario(horizon, [
        { on: event.on, amount: `-${event.amount}`, label: event.label || "Purchase" },
      ]),
  });

  const result = scenario.data ?? forecast.data;

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="type-title">Cash-flow forecast</h1>
        <div className="flex gap-1" role="group" aria-label="Forecast horizon">
          {HORIZONS.map((days) => (
            <Button
              key={days}
              variant={horizon === days ? "primary" : "secondary"}
              size="sm"
              aria-pressed={horizon === days}
              onClick={() => {
                setHorizon(days);
                scenario.reset();
              }}
            >
              {days}d
            </Button>
          ))}
        </div>
      </div>

      {forecast.isPending ? (
        <p className="type-body text-ink-muted">Loading…</p>
      ) : result && isDeclined(result) ? (
        <DeclinedCard declined={result} />
      ) : result ? (
        <>
          <ForecastCard
            forecast={result}
            hypothetical={Boolean(scenario.data)}
            onClear={() => scenario.reset()}
          />

          <ScenarioForm
            event={event}
            onChange={setEvent}
            onRun={() => scenario.mutate()}
            busy={scenario.isPending}
          />
        </>
      ) : null}

      {recurring.data && recurring.data.length > 0 && (
        <Section
          title="What you&rsquo;re committed to"
          description="Detected from repeating charges. These are projected on their own dates rather than averaged into the baseline."
        >
          <div className="overflow-x-auto rounded-card border border-hairline bg-surface">
            <table className="w-full min-w-[36rem] text-left type-body">
              <thead className="type-meta text-ink-muted">
                <tr className="border-b border-hairline">
                  <th scope="col" className="px-4 py-2 font-medium">
                    Merchant
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Every
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    Amount
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    Per month
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Next
                  </th>
                </tr>
              </thead>
              <tbody data-testid="recurring-table">
                {recurring.data.map((pattern) => (
                  <tr
                    key={`${pattern.merchant}-${pattern.cadence}`}
                    className="border-b border-hairline last:border-0"
                  >
                    <td className="px-4 py-2">
                      <span className="capitalize">{pattern.merchant}</span>
                      {/* Variance is the honest part: a bill that swings is
                          still a commitment, and saying so beats implying the
                          amount is fixed. */}
                      {Number(pattern.amount_variance) > 0.1 && (
                        <span className="ml-2 type-meta text-ink-muted">varies</span>
                      )}
                    </td>
                    <td className="px-4 py-2 text-ink-secondary">{pattern.cadence}</td>
                    <td className="tabular px-4 py-2 text-right">
                      {pattern.kind === "income" ? "+" : "−"}
                      {formatMoney(pattern.amount, "INR")}
                    </td>
                    <td className="tabular px-4 py-2 text-right text-ink-secondary">
                      {formatMoney(pattern.monthly_equivalent, "INR")}
                    </td>
                    <td className="px-4 py-2 text-ink-secondary">
                      {formatDate(pattern.next_due_on)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}
    </div>
  );
}

function DeclinedCard({ declined }: { declined: Declined }) {
  return (
    <div
      className="space-y-2 rounded-card border border-dashed border-hairline p-6"
      data-testid="forecast-declined"
    >
      <h2 className="type-section">Not enough history to forecast yet</h2>
      <ul className="list-disc space-y-1 pl-4 type-body text-ink-secondary">
        {declined.caveats.map((caveat) => (
          <li key={caveat}>{caveat}</li>
        ))}
      </ul>
    </div>
  );
}

function ForecastCard({
  forecast,
  hypothetical,
  onClear,
}: {
  forecast: Forecast;
  hypothetical: boolean;
  onClear: () => void;
}) {
  const series = forecast.series.map((point) => ({
    date: formatDate(point.date),
    p50: Number(point.p50),
    // Recharts has no band mark; a transparent base plus a visible span gives
    // one without introducing a second series.
    bandBase: Number(point.p10),
    bandSpan: Number(point.p90) - Number(point.p10),
  }));

  const shortfalls = forecast.shortfall_dates.length;

  // Bounds from the band's own extremes, with a little breathing room. Passed
  // explicitly because a stacked-area domain always includes zero, which would
  // squash the band to a hairline -- see the axis note in `ForecastBand`.
  const lows = forecast.series.map((p) => Number(p.p10));
  const highs = forecast.series.map((p) => Number(p.p90));
  const low = Math.min(...lows, shortfalls > 0 ? 0 : Infinity);
  const high = Math.max(...highs);
  const pad = Math.max((high - low) * 0.12, 1);
  const domain: [number, number] = [low - pad, high + pad];

  return (
    <section className="space-y-4" data-testid="forecast-card">
      <div className="grid gap-4 sm:grid-cols-3">
        <StatTile
          label={`Projected in ${forecast.horizon_days} days`}
          value={formatMoney(forecast.projected_balance_end.amount, "INR")}
        />
        {forecast.trough && (
          <StatTile
            label="Lowest point"
            value={formatMoney(forecast.trough.amount, "INR")}
            caveat={formatDate(forecast.trough.on)}
          />
        )}
        <StatTile
          label="Method"
          value={METHOD_COPY[forecast.method]}
          caveat={`${Math.round(Number(forecast.confidence) * 100)}% confidence · ${forecast.observation_days} days of history`}
        />
      </div>

      {hypothetical && (
        <div className="flex items-center justify-between gap-3 rounded-control bg-surface-raised px-4 py-3 type-body">
          <span>Showing a hypothetical. Your actual forecast is unchanged.</span>
          <Button variant="ghost" size="sm" onClick={onClear}>
            Clear
          </Button>
        </div>
      )}

      {forecast.refining && (
        <p
          className="flex items-center gap-2 type-meta text-ink-muted"
          role="status"
          data-testid="refining"
        >
          <Loader2 className="size-3 animate-spin" aria-hidden />A more detailed model is being
          fitted in the background. This will update itself.
        </p>
      )}

      {shortfalls > 0 && (
        <p className="flex items-center gap-2 rounded-control bg-surface-raised px-4 py-3 type-body text-critical">
          <AlertTriangle className="size-4 shrink-0" aria-hidden />
          On {shortfalls} {shortfalls === 1 ? "day" : "days"} in this window your balance could
          go negative. Shown on the pessimistic edge of the range, not the middle.
        </p>
      )}

      <ChartContainer
        title="Projected balance"
        summary={`Balance projected over ${forecast.horizon_days} days using ${METHOD_COPY[forecast.method].toLowerCase()}. The shaded band is the range between the pessimistic and optimistic paths.`}
        slots={[1]}
        // Caveats are rendered once, by the panel below. Repeating the first
        // one here made the same sentence appear twice on the screen.
        rows={forecast.series}
        columns={[
          { header: "Date", cell: (row) => formatDate(row.date) },
          { header: "Low", numeric: true, cell: (row) => formatMoney(row.p10, "INR") },
          { header: "Expected", numeric: true, cell: (row) => formatMoney(row.p50, "INR") },
          { header: "High", numeric: true, cell: (row) => formatMoney(row.p90, "INR") },
        ]}
      >
        <ForecastBand
          data={series}
          xKey="date"
          slot={1}
          zeroLine={shortfalls > 0}
          domain={domain}
        />
      </ChartContainer>

      <Section headingLevel={3} title="How this was projected">
        <ExplanationPanel explanation={forecast.explanation} />
      </Section>
    </section>
  );
}

function ScenarioForm({
  event,
  onChange,
  onRun,
  busy,
}: {
  event: { on: string; amount: string; label: string };
  onChange: (next: { on: string; amount: string; label: string }) => void;
  onRun: () => void;
  busy: boolean;
}) {
  return (
    <Section
      as="form"
      variant="bordered"
      title="What if I spent…"
      description="Lays a hypothetical purchase over the projection. Nothing is saved."
      onSubmit={(e) => {
        e.preventDefault();
        onRun();
      }}
    >
      <div className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-3">
          <Field
            label="Amount"
            inputMode="decimal"
            placeholder="150000"
            value={event.amount}
            onChange={(e) => onChange({ ...event, amount: e.target.value })}
          />
          <Field
            label="On"
            type="date"
            value={event.on}
            onChange={(e) => onChange({ ...event, on: e.target.value })}
          />
          <Field
            label="What for"
            placeholder="Laptop"
            value={event.label}
            onChange={(e) => onChange({ ...event, label: e.target.value })}
          />
        </div>

        <Button
          type="submit"
          size="sm"
          disabled={busy || !event.amount}
          data-testid="run-scenario"
        >
          {busy ? "Working…" : "See the impact"}
        </Button>
      </div>
    </Section>
  );
}
