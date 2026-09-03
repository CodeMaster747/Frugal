"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileUp, Receipt, Sparkles } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { seedDemoData } from "@/features/finance/api";
import { FormError } from "@/features/auth/components/form-error";

/**
 * First-run experience.
 *
 * Every engine in Frugal is meaningless on an empty database, so this screen's
 * only job is to get data in. "Try sample data" carries equal visual weight
 * deliberately: it is the one path that produces a fully populated product in
 * a single click, which is what makes the platform demonstrable at all.
 */
export function EmptyState({ name }: { name: string }) {
  const queryClient = useQueryClient();
  const [error, setError] = useState<unknown>(null);

  const seed = useMutation({
    mutationFn: seedDemoData,
    onError: setError,
    onSuccess: () => queryClient.invalidateQueries(),
  });

  return (
    <div className="space-y-8">
      <div className="space-y-2">
        <h1 className="type-editorial text-balance">Welcome to Frugal, {name}</h1>
        <p className="max-w-prose type-body text-ink-secondary">
          Frugal needs some financial history before it can advise you. Start with any of these
          — you can change everything later.
        </p>
      </div>

      <FormError error={error} />

      <div className="grid gap-4 sm:grid-cols-3">
        <Option
          icon={<FileUp aria-hidden />}
          title="Import a statement"
          body="Upload a bank CSV. We detect the columns and skip anything already imported."
          action={
            <Button variant="secondary" className="w-full" asChild>
              <Link href="/transactions/import">Choose file</Link>
            </Button>
          }
        />
        <Option
          icon={<Receipt aria-hidden />}
          title="Add a transaction"
          body="Enter one by hand to get started. Takes about ten seconds."
          action={
            <Button variant="secondary" className="w-full" asChild>
              <Link href="/transactions?new=1">Add transaction</Link>
            </Button>
          }
        />
        <Option
          icon={<Sparkles aria-hidden />}
          title="Try sample data"
          body="Twelve months of realistic demo history, so every feature has something to show."
          action={
            <Button
              className="w-full"
              onClick={() => seed.mutate()}
              disabled={seed.isPending}
              data-testid="load-demo"
            >
              {seed.isPending ? "Loading…" : "Load demo data"}
            </Button>
          }
        />
      </div>
    </div>
  );
}

/**
 * One onboarding route. A bordered Section because each of these is a discrete
 * choice, not a passage of the page.
 */
function Option({
  icon,
  title,
  body,
  action,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
  action: React.ReactNode;
}) {
  return (
    <Section variant="bordered" className="flex flex-col gap-4">
      {/* 20px: the one step up from the 16px default, for a lone icon that is
       * carrying a whole card rather than labelling a line of text. */}
      <span className="text-ink-muted [&_svg]:size-5" aria-hidden>
        {icon}
      </span>
      <div className="flex-1 space-y-1">
        <h2 className="type-section">{title}</h2>
        <p className="type-body text-ink-secondary">{body}</p>
      </div>
      {action}
    </Section>
  );
}
