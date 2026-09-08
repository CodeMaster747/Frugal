"use client";

import { Coins, MapPin } from "lucide-react";
import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { apiFetch } from "@/lib/api/client";
import { keys } from "@/lib/api/query-keys";
import { formatDate, formatMoney } from "@/lib/format";

interface Contribution {
  id: string;
  receipt_id: string;
  item_name: string;
  store_name: string;
  unit_price: string;
  observed_on: string;
  points_awarded: number;
  retracted: boolean;
}

interface Report {
  id: string;
  item_text: string;
  price: string | null;
  note: string | null;
  status: string;
  agree_count: number;
  dispute_count: number;
  upvote_count: number;
  promoted: boolean;
  created_at: string;
}

interface Comment {
  id: string;
  body: string;
  upvote_count: number;
  created_at: string;
}

interface Trust {
  score: string;
  can_promote: boolean;
  threshold: string;
  factors: { name: string; detail: string; effect: string }[];
}

interface MergeCandidate {
  id: string;
  left_name: string;
  right_name: string;
  similarity: string;
}

type Tab = "prices" | "reports" | "comments" | "merges" | "trust";

const TABS: { id: Tab; label: string }[] = [
  { id: "prices", label: "Prices from bills" },
  { id: "reports", label: "Shop reports" },
  { id: "comments", label: "Comments" },
  { id: "merges", label: "Same product?" },
  { id: "trust", label: "Standing" },
];

/**
 * Everything a user has contributed, in one place, with a way to take it back.
 *
 * Two different rights live on this page and the wording keeps them apart,
 * because they are not the same thing and the difference matters to whoever is
 * exercising them:
 *
 * - **Retract** removes a contribution from the shared graph.
 * - **Deleting your account** anonymises it instead — the price survives as a
 *   fact about the shop, because deleting it would silently degrade what every
 *   other user sees and make the graph a function of churn (ADR-013).
 */
export default function ContributionsPage() {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("prices");

  const prices = useQuery({
    queryKey: keys.pricegraph.contributions(),
    queryFn: () => apiFetch<Contribution[]>("/api/v1/pricegraph/me/contributions"),
  });
  const reports = useQuery({
    queryKey: keys.community.myReports(),
    queryFn: () => apiFetch<Report[]>("/api/v1/community/me/reports"),
  });
  const comments = useQuery({
    queryKey: keys.community.myComments(),
    queryFn: () => apiFetch<Comment[]>("/api/v1/community/me/comments"),
  });
  const trust = useQuery({
    queryKey: keys.community.trust(),
    queryFn: () => apiFetch<Trust>("/api/v1/community/me/trust"),
  });

  const merges = useQuery({
    queryKey: keys.community.mergeCandidates(),
    queryFn: () => apiFetch<MergeCandidate[]>("/api/v1/pricegraph/merge-candidates"),
  });

  const resolveMerge = useMutation({
    mutationFn: ({ id, merge }: { id: string; merge: boolean }) =>
      apiFetch(`/api/v1/pricegraph/merge-candidates/${id}/${merge ? "confirm" : "reject"}`, {
        method: "POST",
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: keys.community.mergeCandidates() }),
  });

  const retractAll = useMutation({
    mutationFn: () =>
      apiFetch<{ retracted: number }>("/api/v1/pricegraph/me/contributions", {
        method: "DELETE",
      }),
    onSuccess: () =>
      queryClient.invalidateQueries({ queryKey: keys.pricegraph.contributions() }),
  });

  const retractReport = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/api/v1/community/reports/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.community.myReports() }),
  });

  const retractComment = useMutation({
    mutationFn: (id: string) =>
      apiFetch(`/api/v1/community/comments/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: keys.community.myComments() }),
  });

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="type-title">My contributions</h1>
        {/* The community group, collapsed onto the page that is about it.
         * Adding a shop is also the map's own button -- this is the second
         * way in, for somebody who is already looking at what they have
         * given. */}
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" size="sm" asChild>
            <Link href="/contribute">
              <MapPin aria-hidden />
              Add a shop
            </Link>
          </Button>
          <Button variant="secondary" size="sm" asChild>
            <Link href="/points">
              <Coins aria-hidden />
              Points
            </Link>
          </Button>
        </div>
      </div>
      <p className="mt-1 type-body text-ink-secondary">
        Everything you have added to the shared price graph, and how to take any of it back.
      </p>

      <nav className="mt-6 flex gap-1 overflow-x-auto" aria-label="Contribution types">
        {TABS.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => setTab(entry.id)}
            aria-current={tab === entry.id ? "page" : undefined}
            className={
              tab === entry.id
                ? "rounded-control border border-hairline bg-surface px-3 py-2 type-body font-medium whitespace-nowrap"
                : "rounded-control px-3 py-2 type-body whitespace-nowrap text-ink-muted"
            }
          >
            {entry.label}
          </button>
        ))}
      </nav>

      {tab === "prices" && (
        <Section title="Prices from your bills" variant="bordered" className="mt-4">
          {prices.data?.length === 0 && (
            <p className="type-body text-ink-muted">
              Nothing yet. A bill contributes prices once it is committed and its line items add
              up to its total.
            </p>
          )}
          <ul className="divide-y divide-hairline">
            {prices.data?.map((row) => (
              <li key={row.id} className="flex items-baseline justify-between gap-4 py-2">
                <div className="min-w-0">
                  <p className="type-body">{row.item_name}</p>
                  <p className="type-meta text-ink-muted">
                    {row.store_name} · {formatDate(row.observed_on)}
                    {row.retracted && " · retracted"}
                  </p>
                </div>
                <span className="tabular type-body">{formatMoney(row.unit_price)}</span>
              </li>
            ))}
          </ul>

          {(prices.data?.length ?? 0) > 0 && (
            <div className="mt-4 border-t border-hairline pt-4">
              <Button
                variant="danger"
                onClick={() => retractAll.mutate()}
                disabled={retractAll.isPending}
              >
                {retractAll.isPending ? "Retracting…" : "Retract all my prices"}
              </Button>
              <p className="mt-2 type-meta text-ink-muted">
                Removes them from the shared graph for everyone. This is different from deleting
                your account, which <em>anonymises</em> your contributions instead — the prices
                stay, because they are facts about shops rather than about you.
              </p>
            </div>
          )}
        </Section>
      )}

      {tab === "reports" && (
        <Section title="Shop reports" variant="bordered" className="mt-4">
          {reports.data?.length === 0 && (
            <p className="type-body text-ink-muted">
              You have not reported anything yet. Open the map, tap a shop, and add what you
              know.
            </p>
          )}
          <ul className="divide-y divide-hairline">
            {reports.data?.map((report) => (
              <li key={report.id} className="py-3">
                <div className="flex items-baseline justify-between gap-4">
                  <p className="type-body">{report.item_text}</p>
                  {report.price && (
                    <span className="tabular type-body">{formatMoney(report.price)}</span>
                  )}
                </div>
                <p className="mt-1 type-meta text-ink-muted">
                  {report.agree_count} confirmed · {report.dispute_count} disputed ·{" "}
                  {report.upvote_count} upvotes
                  {report.promoted && " · became a verified price"}
                  {report.status !== "active" && ` · ${report.status}`}
                </p>
                {report.status === "active" && (
                  <Button
                    variant="ghost"
                    size="sm"
                    className="mt-2"
                    onClick={() => retractReport.mutate(report.id)}
                  >
                    Retract
                  </Button>
                )}
              </li>
            ))}
          </ul>
        </Section>
      )}

      {tab === "comments" && (
        <Section title="Comments" variant="bordered" className="mt-4">
          {comments.data?.length === 0 && (
            <p className="type-body text-ink-muted">No comments yet.</p>
          )}
          <ul className="divide-y divide-hairline">
            {comments.data?.map((comment) => (
              <li key={comment.id} className="py-3">
                <p className="type-body">{comment.body}</p>
                <p className="mt-1 type-meta text-ink-muted">
                  {formatDate(comment.created_at)} · {comment.upvote_count} upvotes
                </p>
                <Button
                  variant="ghost"
                  size="sm"
                  className="mt-2"
                  onClick={() => retractComment.mutate(comment.id)}
                >
                  Retract
                </Button>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {tab === "merges" && (
        <Section
          title="Are these the same product?"
          description="Two spellings the matcher will not merge on its own."
          variant="bordered"
          className="mt-4"
        >
          {/* Layer 3 of item matching. Trigram similarity is not transitive --
           * chaining A~B and B~C produces groups where A and C are unrelated --
           * so anything canonical-to-canonical waits for a person. Until M19
           * it waited forever: rows went in and nothing read them. */}
          {merges.data?.length === 0 && (
            <p className="type-body text-ink-muted">
              Nothing waiting. These appear when two receipt spellings look like one product but
              are too different to merge automatically.
            </p>
          )}
          <ul className="divide-y divide-hairline">
            {merges.data?.map((candidate) => (
              <li key={candidate.id} className="py-3">
                <p className="type-body">{candidate.left_name}</p>
                <p className="type-body">{candidate.right_name}</p>
                <p className="mt-1 type-meta text-ink-muted">
                  {Math.round(Number(candidate.similarity) * 100)}% similar
                </p>
                <div className="mt-2 flex gap-2">
                  <Button
                    size="sm"
                    variant="secondary"
                    onClick={() => resolveMerge.mutate({ id: candidate.id, merge: true })}
                  >
                    Same product
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    onClick={() => resolveMerge.mutate({ id: candidate.id, merge: false })}
                  >
                    Different
                  </Button>
                </div>
              </li>
            ))}
          </ul>
          <p className="mt-4 type-meta text-ink-muted">
            Saying &ldquo;different&rdquo; is permanent and as useful as saying
            &ldquo;same&rdquo; &mdash; it stops the pair being asked about again.
          </p>
        </Section>
      )}

      {tab === "trust" && (
        <Section
          title="Your standing"
          description="How much weight your contributions carry, and why."
          variant="bordered"
          className="mt-4"
        >
          {trust.data && (
            <>
              <p className="tabular type-display">{trust.data.score}</p>
              <p className="type-meta text-ink-muted">
                A report needs {trust.data.threshold} of combined agreement before it becomes a
                shared price. Your own trust counts for half of that, so at least one other
                person always has to agree.
              </p>

              {/* ADR-002: a score that gates whether somebody's contribution
               * reaches other people has no business being unexplainable. */}
              <ul className="mt-4 space-y-2">
                {trust.data.factors.map((factor) => (
                  <li key={factor.name} className="flex items-baseline justify-between gap-4">
                    <div className="min-w-0">
                      <p className="type-body">{factor.name}</p>
                      <p className="type-meta text-ink-muted">{factor.detail}</p>
                    </div>
                    <span className="tabular type-body">{factor.effect}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Section>
      )}
    </>
  );
}
