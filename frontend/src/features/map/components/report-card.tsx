"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowBigUp, Check, MessageSquare, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { keys } from "@/lib/api/query-keys";
import { formatDate, formatMoney } from "@/lib/format";
import { cn } from "@/lib/utils";

import { addComment, fetchComments, toggleVote, verifyReport, type StoreReport } from "../api";

/**
 * One report, with the controls that act on it.
 *
 * Every control is state-aware: `mine`, `voted` and `verified_by_me` come from
 * the API and decide what is offered. They were hardcoded false on the public
 * list until M19, which meant a signed-in user was invited to confirm their own
 * report and shown an uncast vote they had already cast.
 */
export function ReportCard({
  report,
  storeId,
  signedIn,
}: {
  report: StoreReport;
  storeId: string;
  signedIn: boolean;
}) {
  const queryClient = useQueryClient();
  const [showComments, setShowComments] = useState(false);
  const [draft, setDraft] = useState("");

  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.community.reports(storeId) }),
      queryClient.invalidateQueries({ queryKey: keys.points.balance() }),
    ]);

  const verify = useMutation({
    mutationFn: (agrees: boolean) => verifyReport(report.id, agrees),
    onSuccess: refresh,
  });
  const vote = useMutation({
    mutationFn: () => toggleVote("report", report.id),
    onSuccess: refresh,
  });

  const comments = useQuery({
    queryKey: keys.community.comments(report.id),
    queryFn: () => fetchComments(report.id),
    enabled: showComments,
  });
  const postComment = useMutation({
    mutationFn: () => addComment(report.id, draft.trim()),
    onSuccess: async () => {
      setDraft("");
      await queryClient.invalidateQueries({ queryKey: keys.community.comments(report.id) });
    },
  });

  const answered = report.verified_by_me !== null;

  return (
    <li className="rounded-control border border-hairline p-3">
      <div className="flex items-baseline justify-between gap-2">
        <p className="type-body">{report.item_text}</p>
        {report.price && <span className="tabular type-body">{formatMoney(report.price)}</span>}
      </div>

      {report.note && <p className="mt-1 type-meta text-ink-secondary">{report.note}</p>}

      <p className="mt-2 type-meta text-ink-muted">
        {report.agree_count} confirmed
        {report.dispute_count > 0 && ` · ${report.dispute_count} disputed`}
        {report.promoted && " · verified price"}
        {" · "}
        {formatDate(report.created_at)}
      </p>

      {signedIn && (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {/* Your own report offers no confirm control: the server refuses it,
           * and a button that always errors is worse than no button. */}
          {!report.mine && (
            <>
              <Button
                size="sm"
                variant={report.verified_by_me === true ? "secondary" : "ghost"}
                disabled={answered || verify.isPending}
                onClick={() => verify.mutate(true)}
              >
                <Check className="mr-1 size-3.5" aria-hidden />
                {report.verified_by_me === true ? "Confirmed" : "Confirm"}
              </Button>
              <Button
                size="sm"
                variant={report.verified_by_me === false ? "secondary" : "ghost"}
                disabled={answered || verify.isPending}
                onClick={() => verify.mutate(false)}
              >
                <X className="mr-1 size-3.5" aria-hidden />
                {report.verified_by_me === false ? "Disputed" : "Dispute"}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => vote.mutate()}
                aria-pressed={report.voted}
              >
                <ArrowBigUp
                  className={cn("mr-1 size-4", report.voted && "fill-current")}
                  aria-hidden
                />
                {report.upvote_count}
              </Button>
            </>
          )}

          <Button size="sm" variant="ghost" onClick={() => setShowComments((v) => !v)}>
            <MessageSquare className="mr-1 size-3.5" aria-hidden />
            Comments
          </Button>
        </div>
      )}

      {showComments && (
        <div className="mt-3 border-t border-hairline pt-3">
          {comments.isPending && <p className="type-meta text-ink-muted">Loading…</p>}
          {comments.data?.length === 0 && (
            <p className="type-meta text-ink-muted">No comments yet.</p>
          )}
          <ul className="space-y-2">
            {comments.data?.map((comment) => (
              <li key={comment.id}>
                <p className="type-meta">{comment.body}</p>
                <p className="type-meta text-ink-muted">{formatDate(comment.created_at)}</p>
              </li>
            ))}
          </ul>

          {signedIn && (
            <form
              className="mt-2 flex gap-2"
              onSubmit={(event) => {
                event.preventDefault();
                postComment.mutate();
              }}
            >
              <input
                value={draft}
                onChange={(event) => setDraft(event.target.value)}
                placeholder="Still this price?"
                maxLength={1000}
                className="h-9 flex-1 rounded-control border border-gridline bg-transparent px-2 type-meta"
                aria-label="Add a comment"
              />
              <Button type="submit" size="sm" disabled={!draft.trim() || postComment.isPending}>
                Send
              </Button>
            </form>
          )}
        </div>
      )}
    </li>
  );
}
