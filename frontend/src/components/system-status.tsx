"use client";

import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, Loader2, XCircle } from "lucide-react";

import { ApiError, getReadiness } from "@/lib/api/client";
import { keys } from "@/lib/api/query-keys";

/**
 * Live backend readiness.
 *
 * Exists in M0 to prove the frontend-to-backend path end to end, and doubles as
 * the first implementation of the four required states: loading, error, and
 * two success variants. Every data surface implements these from the start
 * rather than retrofitting them (docs/05-ui-wireframes.md section 6).
 */
export function SystemStatus() {
  const { data, isPending, error } = useQuery({
    queryKey: keys.system.readiness(),
    queryFn: getReadiness,
    refetchInterval: 15_000,
  });

  if (isPending) {
    return (
      <Row icon={<Loader2 className="size-4 animate-spin" aria-hidden />} tone="muted">
        Checking backend…
      </Row>
    );
  }

  if (error) {
    const requestId = error instanceof ApiError ? error.requestId : null;
    return (
      <Row icon={<XCircle className="size-4" aria-hidden />} tone="critical">
        Backend unreachable — is the stack running?
        {requestId && <span className="ml-2 type-meta text-ink-muted">({requestId})</span>}
      </Row>
    );
  }

  const degraded = data.status !== "ready";

  return (
    <div className="space-y-2">
      <Row
        icon={
          degraded ? (
            <AlertTriangle className="size-4" aria-hidden />
          ) : (
            <CheckCircle2 className="size-4" aria-hidden />
          )
        }
        tone={degraded ? "warning" : "good"}
      >
        {degraded ? "Backend degraded" : "Backend ready"}
      </Row>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1 pl-6 type-body text-ink-secondary">
        {Object.entries(data.dependencies).map(([name, ok]) => (
          <div key={name} className="flex items-center justify-between gap-4">
            <dt className="capitalize">{name}</dt>
            {/* Status carries an icon and a word, never colour alone (NFR-6). */}
            <dd className={ok ? "text-good" : "text-critical"}>{ok ? "✓ up" : "✕ down"}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

const TONES = {
  good: "text-good",
  warning: "text-warning",
  critical: "text-critical",
  muted: "text-ink-muted",
} as const;

function Row({
  icon,
  tone,
  children,
}: {
  icon: React.ReactNode;
  tone: keyof typeof TONES;
  children: React.ReactNode;
}) {
  return (
    <p className={`flex items-center gap-2 type-body font-medium ${TONES[tone]}`}>
      {icon}
      {children}
    </p>
  );
}
