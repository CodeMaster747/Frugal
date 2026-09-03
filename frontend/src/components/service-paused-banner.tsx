"use client";

import { useQuery } from "@tanstack/react-query";
import { PauseCircle } from "lucide-react";

import { API_BASE_URL } from "@/lib/api/client";
import { keys } from "@/lib/api/query-keys";

interface ProviderStatus {
  name: string;
  status: "ok" | "paused";
  used: number;
  cap: number;
  remaining: number;
  period_key: string;
  message: string;
}

/**
 * Says out loud when a metered dependency has spent its free allowance.
 *
 * A banner rather than a notification, deliberately. Quota exhaustion is a
 * property of the deployment, not of a person: writing one notification row per
 * user would fan a fact about nobody across the whole user base, and quiet
 * hours and digest batching are both meaningless for it. The notification that
 * *is* useful goes to the developer, as a structured log line the CloudWatch
 * alarms already watch.
 *
 * Renders nothing in the ordinary case, which is most of the time and all of
 * the time on a deployment running the simulator.
 */
export function ServicePausedBanner() {
  const { data } = useQuery({
    queryKey: keys.system.providers(),
    queryFn: async (): Promise<{ providers: ProviderStatus[] }> => {
      // Through API_BASE_URL, not a bare relative path. Locally that points
      // straight at the API; in production it is this origin, where the
      // rewrite in next.config.ts forwards it. A relative fetch works only in
      // the second case and 404s in the first -- which is not merely untidy,
      // it puts a console error on every dashboard page in development.
      const response = await fetch(`${API_BASE_URL}/system/providers`, {
        credentials: "include",
      });
      if (!response.ok) return { providers: [] };
      return response.json();
    },
    // Nothing here changes minute to minute, and a failed poll must never
    // become noise on a page that is otherwise working.
    refetchInterval: 5 * 60 * 1000,
    retry: false,
  });

  const paused = (data?.providers ?? []).filter((provider) => provider.status === "paused");
  if (paused.length === 0) return null;

  return (
    <div
      // The margin lives here rather than on a wrapper in the layout,
      // because this returns null in the ordinary case and a wrapper
      // would leave a gap above every page for nothing.
      className="mb-6 space-y-1 rounded-card bg-surface-raised p-4"
      data-testid="service-paused"
      role="status"
    >
      {paused.map((provider) => (
        <p className="flex items-start gap-2 type-body text-ink-secondary" key={provider.name}>
          <PauseCircle aria-hidden className="mt-0.5 size-4 shrink-0" />
          <span>
            <strong className="text-ink">{provider.name} is paused.</strong> {provider.message}
          </span>
        </p>
      ))}
    </div>
  );
}
