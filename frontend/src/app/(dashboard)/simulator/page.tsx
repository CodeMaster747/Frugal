"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { AlertTriangle, Check, Play, Shield, TriangleAlert } from "lucide-react";
import { useState } from "react";

import { ChartContainer } from "@/components/charts/chart-container";
import { TrendLine } from "@/components/charts/primitives";
import { ExplanationPanel } from "@/components/explanation-panel";
import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Section } from "@/components/ui/section";
import {
  compareScenarios,
  getTemplates,
  runScenario,
  type Outlook,
  type ScenarioResult,
  type ScenarioTemplate,
} from "@/features/simulator/api";
import { formatDate, formatMoney } from "@/lib/format";
import { keys } from "@/lib/api/query-keys";

/**
 * Outlook presentation.
 *
 * A scenario is not scored — the user is asking "what happens", not "how well
 * did I do". So there is no number here, only a shape and the figures behind
 * it, and each carries an icon and a sentence rather than a colour alone.
 */
const OUTLOOK: Record<
  Outlook,
  { label: string; blurb: string; tone: string; Icon: typeof Check }
> = {
  comfortable: {
    label: "Comfortable",
    blurb: "Your savings stay above three months of cover throughout.",
    tone: "text-good",
    Icon: Check,
  },
  tight: {
    label: "Tight",
    blurb: "You get through it, but with less cushion than is comfortable.",
    tone: "text-warning",
    Icon: TriangleAlert,
  },
  unsustainable: {
    label: "Runs out",
    blurb: "Your savings hit zero before the horizon ends.",
    tone: "text-critical",
    Icon: AlertTriangle,
  },
};

export default function SimulatorPage() {
  const templates = useQuery({ queryKey: keys.simulator.templates(), queryFn: getTemplates });
  const [chosen, setChosen] = useState<string>("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [horizon, setHorizon] = useState(24);
  const [pinned, setPinned] = useState<ScenarioResult[]>([]);

  const template = templates.data?.templates.find((t) => t.key === chosen);

  /**
   * Switch template and reset its inputs together.
   *
   * Done here rather than in an effect keyed on the template: an effect that
   * calls setState during render is a cascading render, and the reset is a
   * consequence of a user action, not a synchronisation with anything external.
   * The reset itself matters — a leftover "months" from one scenario would
   * otherwise be silently applied to another that also has one.
   */
  const pick = (next: ScenarioTemplate) => {
    setChosen(next.key);
    setValues(Object.fromEntries(next.inputs.map((i) => [i.name, i.default])));
    run.reset();
  };

  const run = useMutation({
    mutationFn: () => runScenario({ template: chosen, values, horizon_months: horizon }),
  });

  const comparison = useMutation({
    mutationFn: (keys: string[]) =>
      compareScenarios(keys.map((key) => ({ template: key, horizon_months: horizon }))),
  });

  return (
    <div className="space-y-8">
      <div>
        <h1 className="type-title">What if?</h1>
        <p className="mt-1 type-body text-ink-secondary">
          Try a decision against your real finances before you make it. Nothing is saved.
        </p>
      </div>

      <Section variant="bordered">
        <div className="space-y-4">
          <div className="flex flex-wrap gap-2" role="group" aria-label="Scenario">
            {templates.data?.templates.map((t: ScenarioTemplate) => (
              <Button
                key={t.key}
                size="sm"
                variant={chosen === t.key ? "primary" : "secondary"}
                aria-pressed={chosen === t.key}
                onClick={() => pick(t)}
              >
                {t.name}
              </Button>
            ))}
          </div>

          {template && (
            <form
              className="space-y-4"
              onSubmit={(e) => {
                e.preventDefault();
                run.mutate();
              }}
            >
              <p className="type-meta text-ink-muted">{template.description}</p>
              <div className="grid gap-4 sm:grid-cols-3">
                {template.inputs.map((input) => (
                  <Field
                    key={input.name}
                    label={input.label}
                    inputMode="decimal"
                    value={values[input.name] ?? input.default}
                    onChange={(e) => setValues({ ...values, [input.name]: e.target.value })}
                  />
                ))}
                <Field
                  label="Look ahead (months)"
                  inputMode="numeric"
                  value={String(horizon)}
                  onChange={(e) => setHorizon(Number(e.target.value) || 24)}
                />
              </div>
              <Button
                type="submit"
                size="sm"
                disabled={run.isPending}
                data-testid="run-scenario"
              >
                <Play aria-hidden />
                {run.isPending ? "Working…" : "See what happens"}
              </Button>
            </form>
          )}

          {!chosen && (
            <p className="type-body text-ink-secondary">
              Pick a scenario above. Each one is a set of changes to what you earn and spend —
              you can adjust every number.
            </p>
          )}
        </div>
      </Section>

      {run.isError && (
        <p role="alert" className="type-body text-critical">
          {(run.error as Error).message}
        </p>
      )}

      {run.data && (
        <ScenarioCard
          result={run.data}
          onPin={() =>
            setPinned((current) =>
              current.some((r) => r.name === run.data.name) ? current : [...current, run.data],
            )
          }
          pinned={pinned.some((r) => r.name === run.data.name)}
        />
      )}

      <Section
        title="Compare"
        action={
          <Button
            variant="secondary"
            size="sm"
            disabled={comparison.isPending || !templates.data}
            data-testid="compare-scenarios"
            onClick={() => comparison.mutate(["holiday", "vehicle", "income_loss"])}
          >
            Compare three common decisions
          </Button>
        }
      >
        {comparison.data && (
          <div className="overflow-x-auto rounded-card border border-hairline bg-surface">
            <table
              className="w-full min-w-[36rem] text-left type-body"
              data-testid="comparison"
            >
              <thead className="type-meta text-ink-muted">
                <tr className="border-b border-hairline">
                  <th scope="col" className="px-4 py-2 font-medium">
                    Scenario
                  </th>
                  <th scope="col" className="px-4 py-2 font-medium">
                    Outlook
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    Lowest cover
                  </th>
                  <th scope="col" className="px-4 py-2 text-right font-medium">
                    Ends with
                  </th>
                </tr>
              </thead>
              <tbody>
                {comparison.data.results.map((result) => {
                  const outlook = OUTLOOK[result.outlook];
                  const safest = comparison.data.safest === result.name;
                  return (
                    <tr key={result.name} className="border-b border-hairline last:border-0">
                      <td className="px-4 py-2">
                        {result.name}
                        {/* "Safest", not "best" — which is best depends on what
                            the user wants, and the software does not know. */}
                        {safest && (
                          <span className="ml-2 inline-flex items-center gap-1 type-meta text-good">
                            <Shield className="size-3" aria-hidden />
                            leaves the most room
                          </span>
                        )}
                      </td>
                      <td className={`px-4 py-2 ${outlook.tone}`}>{outlook.label}</td>
                      <td className="tabular px-4 py-2 text-right">
                        {Number(result.trough_months_of_cover).toFixed(1)} months
                      </td>
                      <td className="tabular px-4 py-2 text-right">
                        {formatMoney(result.after.liquid_reserves, "INR")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </div>
  );
}

function ScenarioCard({
  result,
  onPin,
  pinned,
}: {
  result: ScenarioResult;
  onPin: () => void;
  pinned: boolean;
}) {
  const outlook = OUTLOOK[result.outlook];
  const series = result.series.map((p) => ({
    on: formatDate(p.on),
    reserves: Number(p.reserves),
  }));

  return (
    <section className="space-y-6" data-testid="scenario-result" data-outlook={result.outlook}>
      <Section variant="bordered">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex min-w-0 items-start gap-3">
            <outlook.Icon className={`mt-0.5 size-5 shrink-0 ${outlook.tone}`} aria-hidden />
            <div className="min-w-0 space-y-1">
              <h2
                className={`type-section font-semibold ${outlook.tone}`}
                data-testid="outlook"
              >
                {outlook.label}
              </h2>
              <p className="type-body text-ink-secondary">{outlook.blurb}</p>
              <p className="type-body text-ink-muted">{result.name}</p>
            </div>
          </div>
          <Button variant="ghost" size="sm" onClick={onPin} disabled={pinned}>
            {pinned ? "Kept" : "Keep for comparison"}
          </Button>
        </div>

        {result.months_until_shortfall !== null && (
          <p className="mt-4 flex items-start gap-2 rounded-control bg-surface-raised px-3 py-3 type-body text-critical">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden />
            Your savings run out in month {result.months_until_shortfall}.
          </p>
        )}

        <dl className="mt-6 grid gap-4 sm:grid-cols-3">
          {[
            {
              label: "Savings now",
              before: result.before.liquid_reserves,
              after: result.after.liquid_reserves,
              money: true,
            },
            {
              label: "Monthly surplus",
              before: result.before.monthly_surplus,
              after: result.after.monthly_surplus,
              money: true,
            },
            {
              label: "Cover at the worst point",
              before: `${Number(result.before.emergency_fund_months).toFixed(1)} months`,
              after: `${Number(result.trough_months_of_cover).toFixed(1)} months`,
              money: false,
            },
          ].map((row) => (
            <div key={row.label}>
              <dt className="type-eyebrow text-ink-muted">{row.label}</dt>
              <dd className="tabular mt-0.5 type-body">
                <span className="text-ink-muted">
                  {row.money ? formatMoney(String(row.before), "INR") : row.before}
                </span>
                <span className="mx-1.5 text-ink-muted" aria-hidden>
                  →
                </span>
                <span className="font-medium">
                  {row.money ? formatMoney(String(row.after), "INR") : row.after}
                </span>
              </dd>
            </div>
          ))}
        </dl>
      </Section>

      <ChartContainer
        title="Savings over time"
        summary={`Projected savings over ${result.series.length - 1} months under this scenario.`}
        slots={[1]}
        rows={result.series}
        columns={[
          { header: "Month", cell: (row) => formatDate(row.on) },
          { header: "Savings", numeric: true, cell: (row) => formatMoney(row.reserves, "INR") },
          {
            header: "Monthly surplus",
            numeric: true,
            cell: (row) => formatMoney(row.monthly_surplus, "INR"),
          },
        ]}
      >
        <TrendLine data={series} xKey="on" yKey="reserves" slot={1} height={200} fitDomain />
      </ChartContainer>

      <Section headingLevel={3} title="How this was worked out">
        <ExplanationPanel explanation={result.explanation} />
      </Section>
    </section>
  );
}
