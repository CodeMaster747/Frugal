"use client";

import { useQuery } from "@tanstack/react-query";

import { Section } from "@/components/ui/section";
import { formatMoney } from "@/lib/format";

import { fetchProfile } from "./api";

/**
 * What the system has worked out about how this person spends.
 *
 * Renders nothing at all when there is no profile, which covers three cases
 * that mean the same thing to a reader: personalization is not configured for
 * this deployment (the default, a 404), there is not enough history yet, or the
 * request failed. None of them is worth a box on a dashboard saying so.
 *
 * `retry: false` because the 404 is the ordinary case, not a transient fault.
 */
export function SpendingProfile() {
  const profile = useQuery({
    queryKey: ["personalization-profile"],
    queryFn: fetchProfile,
    retry: false,
    staleTime: 5 * 60_000,
  });

  if (!profile.data?.available) return null;
  const data = profile.data;

  return (
    <Section
      title="How you spend"
      description={`From ${data.signal_count} notable purchases over ${data.observation_days} days.`}
      variant="bordered"
      className="mt-6"
    >
      <dl className="grid grid-cols-2 gap-4 sm:grid-cols-3">
        {data.big_purchase_threshold && (
          <div>
            <dt className="type-meta text-ink-muted">A big purchase, for you</dt>
            <dd className="tabular type-section">
              {formatMoney(data.big_purchase_threshold)}+
            </dd>
          </div>
        )}
        {data.median_big_purchase && (
          <div>
            <dt className="type-meta text-ink-muted">Typical</dt>
            <dd className="tabular type-section">{formatMoney(data.median_big_purchase)}</dd>
          </div>
        )}
        {data.cadence_days && (
          <div>
            <dt className="type-meta text-ink-muted">About every</dt>
            <dd className="tabular type-section">
              {Math.round(Number(data.cadence_days))} days
            </dd>
          </div>
        )}
      </dl>

      {data.archetypes.length > 0 && (
        <ul className="mt-4 space-y-2">
          {data.archetypes.map((archetype) => (
            <li key={archetype.slug}>
              <p className="type-body font-medium">{archetype.label}</p>
              <p className="type-meta text-ink-secondary">{archetype.description}</p>
            </li>
          ))}
        </ul>
      )}

      {/* ADR-002: a description of somebody's habits that cannot say what it is
       * based on has no business being on their dashboard. */}
      {data.factors.length > 0 && (
        <ul className="mt-4 space-y-1 border-t border-hairline pt-3">
          {data.factors.map((factor) => (
            <li key={factor.name} className="type-meta text-ink-muted">
              <span className="text-ink-secondary">{factor.name}:</span> {factor.detail} &mdash;{" "}
              {factor.effect}
            </li>
          ))}
        </ul>
      )}

      {data.caveats.map((caveat) => (
        <p key={caveat} className="mt-2 type-meta text-ink-muted">
          {caveat}
        </p>
      ))}
    </Section>
  );
}
