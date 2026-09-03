"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { keys } from "@/lib/api/query-keys";
import { RefreshCw } from "lucide-react";
import { useState } from "react";

import { ExplanationPanel } from "@/components/explanation-panel";
import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/empty";
import { Section } from "@/components/ui/section";
import {
  dismissInsight,
  getHealthScore,
  getInsights,
  getRubric,
  markInsightRead,
  refreshInsights,
  type HealthScore,
} from "@/features/health/api";
import { InsightCard } from "@/features/health/components/insight-card";

const RISK_COPY = {
  low: { label: "Low risk", tone: "text-good" },
  moderate: { label: "Moderate risk", tone: "text-warning" },
  elevated: { label: "Elevated risk", tone: "text-serious" },
  high: { label: "High risk", tone: "text-critical" },
} as const;

export default function HealthPage() {
  const queryClient = useQueryClient();
  const [showRubric, setShowRubric] = useState(false);

  const health = useQuery({ queryKey: keys.health.score(), queryFn: getHealthScore });
  const insights = useQuery({ queryKey: keys.health.insights(), queryFn: () => getInsights() });
  const rubric = useQuery({
    queryKey: keys.health.rubric(),
    queryFn: getRubric,
    enabled: showRubric,
  });

  const refresh = useMutation({
    mutationFn: refreshInsights,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.health.insights() }),
  });
  const dismiss = useMutation({
    mutationFn: dismissInsight,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.health.insights() }),
  });
  const read = useMutation({
    mutationFn: markInsightRead,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.health.insights() }),
  });

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="type-title">Financial health</h1>
        <Button
          variant="secondary"
          size="sm"
          onClick={() => refresh.mutate()}
          disabled={refresh.isPending}
          data-testid="refresh-insights"
        >
          <RefreshCw aria-hidden className={refresh.isPending ? "animate-spin" : ""} />
          {refresh.isPending ? "Checking…" : "Check for new insights"}
        </Button>
      </div>

      {health.isPending ? (
        <p className="type-body text-ink-muted">Loading…</p>
      ) : health.data ? (
        <ScoreCard
          score={health.data}
          onToggleRubric={() => setShowRubric((v) => !v)}
          rubricOpen={showRubric}
        />
      ) : null}

      {showRubric && rubric.data && (
        <Section
          variant="bordered"
          data-testid="rubric"
          title="How this score is calculated"
          description={`Rubric ${rubric.data.version}. Weights total ${rubric.data.total_weight}. Published in full so the score can be checked rather than trusted.`}
        >
          <div className="overflow-x-auto">
            <table className="w-full min-w-[34rem] text-left type-meta">
              <thead className="text-ink-muted">
                <tr className="border-b border-hairline">
                  <th scope="col" className="pr-4 pb-2 font-medium">
                    Metric
                  </th>
                  <th scope="col" className="pr-4 pb-2 font-medium">
                    Weight
                  </th>
                  <th scope="col" className="pb-2 font-medium">
                    Bands
                  </th>
                </tr>
              </thead>
              <tbody>
                {rubric.data.metrics.map((metric) => (
                  <tr key={metric.key} className="border-b border-hairline last:border-0">
                    <td className="py-2 pr-4 font-medium">{metric.name}</td>
                    <td className="tabular py-2 pr-4">{metric.weight}</td>
                    <td className="py-2 text-ink-secondary">
                      {metric.bands
                        .map(
                          (band) =>
                            `${band.label} (${
                              metric.higher_is_better ? "≥" : "≤"
                            }${band.at_least ?? band.at_most}${metric.unit} → ${band.points})`,
                        )
                        .join(", ")}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      <Section
        title={
          <>
            Insights
            {insights.data && insights.data.unread_count > 0 && (
              // Tinted, not filled. White on --series-1 is only 3.64:1 in dark
              // mode and fails AA for text this size; the accent as foreground on
              // a 12% wash of itself clears 4.9:1 in both modes.
              <span className="ml-2 rounded-full border border-series-1/30 bg-series-1/12 px-2 py-0.5 type-meta font-medium text-series-1">
                {insights.data.unread_count} new
              </span>
            )}
          </>
        }
        action={
          refresh.data && (
            <span className="type-meta text-ink-muted" role="status">
              {refresh.data.created > 0
                ? `Found ${refresh.data.created}`
                : "Nothing new — everything is already here"}
            </span>
          )
        }
      >
        {insights.isPending ? (
          <p className="type-body text-ink-muted">Loading…</p>
        ) : insights.data && insights.data.data.length > 0 ? (
          <div className="space-y-3" data-testid="insight-feed">
            {insights.data.data.map((insight) => (
              <InsightCard
                key={insight.id}
                insight={insight}
                busy={dismiss.isPending}
                onDismiss={() => dismiss.mutate(insight.id)}
                onRead={() => read.mutate(insight.id)}
              />
            ))}
          </div>
        ) : (
          <Empty>
            Nothing to flag right now. Insights appear when something changes materially — a
            budget overrun, a spending spike, a goal falling behind.
          </Empty>
        )}
      </Section>
    </div>
  );
}

function ScoreCard({
  score,
  onToggleRubric,
  rubricOpen,
}: {
  score: HealthScore;
  onToggleRubric: () => void;
  rubricOpen: boolean;
}) {
  // An absent score is a real state, not an error: too little history to say
  // anything honest. The caveats explain it, so the panel still renders.
  const unscored = score.score === null;
  const risk = score.risk_level ? RISK_COPY[score.risk_level] : null;

  return (
    <Section variant="bordered" data-testid="score-card">
      <div className="space-y-6">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            {unscored ? (
              <p className="type-section text-ink-secondary" data-testid="score-value">
                Not enough history yet
              </p>
            ) : (
              <>
                {/* The one figure in the app allowed past the type scale. A
                 * health score is the page's whole subject, and at 48px against
                 * a 24px h1 the hierarchy is unmistakable. `leading-none` because
                 * a lone numeral needs no line box around it. */}
                <p
                  className="tabular text-5xl leading-none font-semibold tracking-[-0.03em]"
                  data-testid="score-value"
                >
                  {Math.round(Number(score.score))}
                  <span className="type-title text-ink-muted">/100</span>
                </p>
                {risk && (
                  <p className={`mt-2 type-body font-medium ${risk.tone}`}>{risk.label}</p>
                )}
              </>
            )}
          </div>

          <Button variant="ghost" size="sm" onClick={onToggleRubric} aria-expanded={rubricOpen}>
            {rubricOpen ? "Hide the rubric" : "How is this calculated?"}
          </Button>
        </div>

        <ExplanationPanel explanation={score.explanation} />
      </div>
    </Section>
  );
}
