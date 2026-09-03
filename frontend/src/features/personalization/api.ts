import { apiFetch } from "@/lib/api/client";

/**
 * The aggregate view of a user's own spending habits.
 *
 * Everything here is derived in a separate database (ADR-011) and only ever
 * leaves it as aggregates — no individual purchase signal is exposed by the
 * API, which is the whole reason it lives apart from the ledger.
 */
export interface Archetype {
  slug: string;
  label: string;
  description: string;
  score: string;
}

export interface Profile {
  available: boolean;
  computed_at: string | null;
  observation_days: number;
  signal_count: number;
  big_purchase_threshold: string | null;
  median_big_purchase: string | null;
  cadence_days: string | null;
  category_affinities: Record<string, string>;
  archetypes: Archetype[];
  confidence: string;
  factors: { name: string; detail: string; effect: string }[];
  caveats: string[];
}

/**
 * `404` when the deployment has no personalization database configured — which
 * is the default, and a supported state rather than an error. Callers treat a
 * failure as "no profile" and render nothing.
 */
export function fetchProfile(): Promise<Profile> {
  return apiFetch("/api/v1/personalization/profile");
}
