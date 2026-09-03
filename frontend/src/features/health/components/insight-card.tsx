"use client";

/**
 * One insight, with its reasoning one click away.
 *
 * The reasoning is collapsed by default and the summary is not. A feed where
 * every card is fully expanded is a wall of text; a feed where the reasoning is
 * unreachable is a black box making claims about someone's money. Collapsed but
 * always available is the only version that is both readable and accountable.
 */

import { AlertTriangle, ChevronDown, Info, OctagonAlert, X } from "lucide-react";
import { useState } from "react";

import { ExplanationPanel } from "@/components/explanation-panel";

import type { Insight, Severity } from "../api";

const SEVERITY: Record<Severity, { Icon: typeof Info; tone: string; label: string }> = {
  // Status colours are reserved and always ship with an icon and a word, never
  // colour alone.
  info: { Icon: Info, tone: "text-ink-muted", label: "For information" },
  warning: { Icon: AlertTriangle, tone: "text-warning", label: "Worth attention" },
  critical: { Icon: OctagonAlert, tone: "text-critical", label: "Needs action" },
};

export function InsightCard({
  insight,
  onDismiss,
  onRead,
  busy,
}: {
  insight: Insight;
  onDismiss: () => void;
  onRead: () => void;
  busy?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const { Icon, tone, label } = SEVERITY[insight.severity];

  const toggle = () => {
    setOpen((wasOpen) => {
      // Opening the reasoning is the clearest signal the user has read it.
      // Marking read on render would mark everything read on first paint.
      if (!wasOpen && insight.read_at === null) onRead();
      return !wasOpen;
    });
  };

  return (
    <article
      className={`rounded-card border border-hairline bg-surface ${
        insight.read_at === null ? "border-l-2 border-l-brand" : ""
      }`}
      data-testid="insight-card"
      data-severity={insight.severity}
    >
      <div className="flex items-start gap-3 p-4">
        <Icon className={`mt-0.5 size-4 shrink-0 ${tone}`} aria-hidden />

        <div className="min-w-0 flex-1 space-y-1">
          <h3 className="type-body font-medium">
            {insight.title}
            <span className="sr-only"> — {label}</span>
          </h3>
          <p className="type-body text-ink-secondary">{insight.body}</p>

          <button
            type="button"
            className="mt-2 flex items-center gap-1.5 type-meta text-ink-muted underline-offset-2 transition-colors hover:text-ink hover:underline"
            aria-expanded={open}
            onClick={toggle}
          >
            <ChevronDown
              className={`size-3 shrink-0 transition-transform ${open ? "rotate-180" : ""}`}
              aria-hidden
            />
            {open ? "Hide the reasoning" : "Why am I seeing this?"}
          </button>
        </div>

        <button
          type="button"
          className="grid size-7 shrink-0 place-items-center rounded-control text-ink-muted transition-colors hover:bg-surface-raised hover:text-ink disabled:opacity-50"
          aria-label={`Dismiss: ${insight.title}`}
          title="Dismiss"
          disabled={busy}
          onClick={onDismiss}
        >
          <X className="size-3.5" aria-hidden />
        </button>
      </div>

      {open && (
        <div className="border-t border-hairline p-4">
          <ExplanationPanel explanation={insight.explanation} />
        </div>
      )}
    </article>
  );
}
