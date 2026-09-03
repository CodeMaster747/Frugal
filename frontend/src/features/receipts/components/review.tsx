"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Select } from "@/components/ui/select";
import { FormError } from "@/features/auth/components/form-error";
import { listAccounts, listCategories } from "@/features/finance/api";
import {
  commitReceipt,
  correctFields,
  getImageUrl,
  getReceipt,
  type ReceiptField,
} from "@/features/receipts/api";
import { formatDate, formatMoney } from "@/lib/format";
import { keys } from "@/lib/api/query-keys";
import { cn } from "@/lib/utils";

const LABELS: Record<string, string> = {
  merchant: "Merchant",
  date: "Date",
  total: "Total",
  tax: "Tax",
  subtotal: "Subtotal",
};

/**
 * Receipt review.
 *
 * The screen that makes ~65%-accurate OCR into a usable feature. Three
 * decisions carry it:
 *
 * 1. **Only doubtful fields are flagged.** Merchant and date usually read
 *    cleanly, so the UI asks about the total alone. Demanding wholesale
 *    re-verification is how human-in-the-loop flows get abandoned.
 * 2. **The raw reading is shown for a failed field.** "We read '1,2S0.00'"
 *    turns a correction request into an explanation.
 * 3. **The bounding box links each field to its region** on the image, so
 *    verifying is a glance rather than a hunt.
 */
export function ReceiptReview({ receiptId }: { receiptId: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [error, setError] = useState<unknown>(null);
  const [edits, setEdits] = useState<Record<string, string>>({});
  const [focused, setFocused] = useState<string | null>(null);
  const [accountId, setAccountId] = useState("");
  const [allowDuplicate, setAllowDuplicate] = useState(false);

  const receipt = useQuery({
    queryKey: keys.receipts.one(receiptId),
    queryFn: () => getReceipt(receiptId),
    // Poll only while the worker is still on it.
    refetchInterval: (query) =>
      ["queued", "processing"].includes(query.state.data?.status ?? "") ? 1500 : false,
  });
  const image = useQuery({
    queryKey: keys.receipts.image(receiptId),
    queryFn: () => getImageUrl(receiptId),
  });
  const accounts = useQuery({ queryKey: keys.finance.accounts(), queryFn: listAccounts });
  const categories = useQuery({ queryKey: keys.finance.categories(), queryFn: listCategories });

  const save = useMutation({
    mutationFn: () => correctFields(receiptId, edits),
    onError: setError,
    onSuccess: async (data) => {
      setError(null);
      setEdits({});
      queryClient.setQueryData(["receipt", receiptId], data);
    },
  });

  const commit = useMutation({
    mutationFn: () =>
      commitReceipt(receiptId, { account_id: accountId, allow_duplicate: allowDuplicate }),
    onError: setError,
    onSuccess: () => {
      void queryClient.invalidateQueries();
      router.push("/transactions");
    },
  });

  if (receipt.isPending) return <p className="type-body text-ink-muted">Loading…</p>;
  if (receipt.isError) return <FormError error={receipt.error} />;

  const data = receipt.data;

  if (data.status === "queued" || data.status === "processing") {
    return <Processing status={data.status} />;
  }
  if (data.status === "failed") {
    return (
      <div role="alert" className="rounded-card border border-critical/40 p-6 type-body">
        <p className="font-medium text-critical">We could not read this receipt.</p>
        <p className="mt-1 text-ink-secondary">{data.error_message}</p>
        <Button className="mt-4" variant="secondary" onClick={() => router.push("/receipts")}>
          Back to receipts
        </Button>
      </div>
    );
  }

  const flagged = data.fields.filter((f) => f.needs_review);
  const activeAccount = accountId || accounts.data?.[0]?.id || "";

  return (
    <div className="space-y-6">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="type-title">Review extraction</h1>
        <Confidence value={data.overall_confidence} flaggedCount={flagged.length} />
      </header>

      <FormError error={error} />

      <div className="grid gap-6 lg:grid-cols-2">
        <ImagePane url={image.data?.url} fields={data.fields} focused={focused} />

        <section className="space-y-4" data-testid="review-fields">
          {flagged.length > 0 ? (
            <p className="type-body">
              We read this receipt.{" "}
              <strong>
                {flagged.length} field{flagged.length > 1 ? "s need" : " needs"} your eyes.
              </strong>
            </p>
          ) : (
            <p className="flex items-center gap-2 type-body text-good">
              <CheckCircle2 className="size-4 shrink-0" aria-hidden />
              Everything read clearly. Check it and save.
            </p>
          )}

          {data.fields
            .filter((f) => f.parsed_value !== null || f.needs_review)
            .map((field) => (
              <FieldRow
                key={field.field_name}
                field={field}
                value={edits[field.field_name] ?? field.effective_value ?? ""}
                onChange={(v) => setEdits({ ...edits, [field.field_name]: v })}
                onFocus={() => setFocused(field.field_name)}
                onBlur={() => setFocused(null)}
              />
            ))}

          {Object.keys(edits).length > 0 && (
            <Button variant="secondary" onClick={() => save.mutate()} disabled={save.isPending}>
              {save.isPending ? "Saving…" : "Confirm corrections"}
            </Button>
          )}

          {data.duplicate_candidates.length > 0 && (
            <DuplicateWarning
              candidates={data.duplicate_candidates}
              acknowledged={allowDuplicate}
              onAcknowledge={() => setAllowDuplicate(true)}
            />
          )}

          <div className="space-y-4 border-t border-hairline pt-4">
            <Select
              id="account"
              label="Save to account"
              value={activeAccount}
              onChange={(e) => setAccountId(e.target.value)}
            >
              {accounts.data?.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </Select>

            <Button
              className="w-full"
              data-testid="commit-receipt"
              disabled={
                commit.isPending ||
                data.blocking_fields.length > 0 ||
                !activeAccount ||
                (data.duplicate_candidates.length > 0 && !allowDuplicate)
              }
              onClick={() => commit.mutate()}
            >
              {commit.isPending ? "Saving…" : "Save as transaction"}
            </Button>

            {data.blocking_fields.length > 0 && (
              <p className="type-meta text-ink-muted">
                Confirm {data.blocking_fields.map((f) => LABELS[f] ?? f).join(", ")} first.
              </p>
            )}
          </div>
        </section>
      </div>

      {categories.isSuccess && data.line_items.length > 0 && (
        <details className="rounded-card border border-hairline bg-surface p-4">
          <summary className="cursor-pointer type-body font-medium">
            {data.line_items.length} line items
          </summary>
          <ul className="mt-3 space-y-1 type-body">
            {data.line_items.map((item) => (
              <li key={item.line_number} className="flex justify-between gap-4">
                <span className="truncate text-ink-secondary">{item.description}</span>
                <span className="tabular">
                  {item.total_price ? formatMoney(item.total_price) : "—"}
                </span>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

function Processing({ status }: { status: string }) {
  return (
    <div className="rounded-card border border-dashed border-hairline p-6 text-center">
      <p className="font-medium">Reading your receipt…</p>
      <p className="mt-1 type-body text-ink-secondary">
        {status === "queued" ? "Queued" : "Straightening, cleaning up, and recognising text"}
      </p>
      <div className="mx-auto mt-4 h-1 w-48 overflow-hidden rounded-full bg-gridline">
        <div className="h-full w-1/3 animate-pulse rounded-full bg-series-1" />
      </div>
    </div>
  );
}

function Confidence({ value, flaggedCount }: { value: string | null; flaggedCount: number }) {
  if (value === null) return null;
  const pct = Math.round(Number(value) * 100);

  return (
    <p
      className={cn(
        "flex items-center gap-1.5 type-body",
        flaggedCount > 0 ? "text-warning" : "text-good",
      )}
    >
      {flaggedCount > 0 ? (
        <AlertTriangle className="size-4 shrink-0" aria-hidden />
      ) : (
        <CheckCircle2 className="size-4 shrink-0" aria-hidden />
      )}
      Confidence {pct}%
    </p>
  );
}

function FieldRow({
  field,
  value,
  onChange,
  onFocus,
  onBlur,
}: {
  field: ReceiptField;
  value: string;
  onChange: (v: string) => void;
  onFocus: () => void;
  onBlur: () => void;
}) {
  const pct = Math.round(Number(field.confidence) * 100);
  const label = LABELS[field.field_name] ?? field.field_name;

  return (
    <div data-testid={`field-${field.field_name}`}>
      <div className="mb-1 flex items-center justify-between">
        <span className="type-body font-medium">{label}</span>
        {/* Icon plus a percentage: confidence is never colour alone. */}
        <span
          className={cn("type-meta", field.needs_review ? "text-warning" : "text-ink-muted")}
          data-testid={`confidence-${field.field_name}`}
        >
          {field.needs_review ? "⚠ " : "✓ "}
          {pct}%{field.needs_review && " · needs review"}
        </span>
      </div>

      <Field
        label={label}
        className="sr-only-label"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        onFocus={onFocus}
        onBlur={onBlur}
        type={field.field_name === "date" ? "date" : "text"}
      />

      {field.needs_review && field.raw_text && (
        // Showing the machine's actual reading turns a correction request into
        // an explanation of what went wrong.
        <p className="mt-1 type-meta text-ink-secondary">
          We read <span className="font-mono">{field.raw_text}</span> — please confirm.
        </p>
      )}
    </div>
  );
}

function ImagePane({
  url,
  fields,
  focused,
}: {
  url: string | undefined;
  fields: ReceiptField[];
  focused: string | null;
}) {
  const active = fields.find((f) => f.field_name === focused && f.bbox);

  return (
    <figure className="relative overflow-hidden rounded-card border border-hairline bg-surface">
      {url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={url} alt="The uploaded receipt" className="w-full" />
      ) : (
        <div className="h-80 animate-pulse bg-page" />
      )}

      {/* The bbox links each field to where its value came from, so checking a
          flagged value is a glance rather than a hunt. */}
      {active?.bbox && (
        <span
          aria-hidden
          // Scoped to the four properties that actually change, not `all`. The
          // box slides between fields, which is the point of it, but `all` also
          // put border and background on the compositor's list for no reason.
          // Geometry transitions are not cheap; four named properties on one
          // element, only while a field is focused, is the affordable version.
          className="pointer-events-none absolute border-2 border-warning bg-warning/20 transition-[left,top,width,height]"
          style={{
            left: `${(active.bbox.x / 720) * 100}%`,
            top: `${(active.bbox.y / 520) * 100}%`,
            width: `${(active.bbox.w / 720) * 100}%`,
            height: `${(active.bbox.h / 520) * 100}%`,
          }}
          data-testid="bbox-highlight"
        />
      )}

      <figcaption className="border-t border-hairline px-3 py-2 type-meta text-ink-muted">
        Focus a field to highlight where it was read from.
      </figcaption>
    </figure>
  );
}

function DuplicateWarning({
  candidates,
  acknowledged,
  onAcknowledge,
}: {
  candidates: {
    transaction_id: string;
    occurred_on: string;
    amount: string;
    merchant: string | null;
  }[];
  acknowledged: boolean;
  onAcknowledge: () => void;
}) {
  return (
    <div
      role="alert"
      className="space-y-2 rounded-control border border-warning/40 bg-warning/5 p-4 type-body"
      data-testid="duplicate-warning"
    >
      <p className="flex items-center gap-2 font-medium">
        <AlertTriangle className="size-4 shrink-0" aria-hidden />
        Possible duplicate
      </p>
      {candidates.map((c) => (
        <p key={c.transaction_id} className="text-ink-secondary">
          {formatMoney(c.amount)} at {c.merchant ?? "unknown"} on {formatDate(c.occurred_on)}{" "}
          already exists.
        </p>
      ))}
      {acknowledged ? (
        <p className="type-meta text-ink-muted">Marked as a separate purchase.</p>
      ) : (
        <Button size="sm" variant="secondary" onClick={onAcknowledge}>
          It&apos;s a separate purchase
        </Button>
      )}
    </div>
  );
}
