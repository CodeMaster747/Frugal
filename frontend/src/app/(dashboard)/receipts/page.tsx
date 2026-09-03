"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Camera } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/empty";
import { FormError } from "@/features/auth/components/form-error";
import { listReceipts, uploadReceipt, type Receipt } from "@/features/receipts/api";
import { formatDate, formatMoney } from "@/lib/format";
import { keys } from "@/lib/api/query-keys";

const STATUS_LABEL: Record<Receipt["status"], string> = {
  pending_upload: "Waiting for upload",
  queued: "Queued",
  processing: "Reading…",
  needs_review: "Needs review",
  ready: "Ready to save",
  committed: "Saved",
  failed: "Could not read",
};

const STATUS_TONE: Record<Receipt["status"], string> = {
  pending_upload: "text-ink-muted",
  queued: "text-ink-muted",
  processing: "text-ink-secondary",
  needs_review: "text-warning",
  ready: "text-good",
  committed: "text-ink-muted",
  failed: "text-critical",
};

export default function ReceiptsPage() {
  const router = useRouter();
  const queryClient = useQueryClient();
  const fileInput = useRef<HTMLInputElement>(null);
  const [error, setError] = useState<unknown>(null);

  const receipts = useQuery({
    queryKey: keys.receipts.all(),
    queryFn: listReceipts,
    // Keep the list live while anything is still being read.
    refetchInterval: (query) =>
      query.state.data?.some((r) => ["queued", "processing"].includes(r.status)) ? 2000 : false,
  });

  const upload = useMutation({
    mutationFn: uploadReceipt,
    onError: setError,
    onSuccess: async ({ receiptId }) => {
      setError(null);
      await queryClient.invalidateQueries({ queryKey: keys.receipts.all() });
      router.push(`/receipts/${receiptId}`);
    },
  });

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="type-title">Receipts</h1>
        <Button
          onClick={() => fileInput.current?.click()}
          disabled={upload.isPending}
          data-testid="upload-receipt"
        >
          <Camera aria-hidden />
          {upload.isPending ? "Uploading…" : "Add receipt"}
        </Button>
        <input
          ref={fileInput}
          id="receipt-file"
          type="file"
          accept="image/jpeg,image/png,image/webp,image/heic"
          className="sr-only"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload.mutate(file);
          }}
        />
      </div>

      <FormError error={error} />

      <p className="max-w-prose type-body text-ink-secondary">
        Photograph a receipt and Frugal reads the merchant, date and total. It shows how
        confident it is in each one, and asks you to confirm anything it could not read clearly
        — so a bad scan never quietly becomes a wrong transaction.
      </p>

      {receipts.isPending ? (
        <p className="type-body text-ink-muted">Loading…</p>
      ) : receipts.data?.length === 0 ? (
        <Empty>No receipts yet. Add one to get started.</Empty>
      ) : (
        <ul
          className="divide-y divide-hairline rounded-card border border-hairline bg-surface"
          data-testid="receipt-list"
        >
          {receipts.data?.map((receipt) => (
            <li key={receipt.id}>
              <Link
                href={`/receipts/${receipt.id}`}
                className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-page"
              >
                <span className="min-w-0">
                  <span className="block truncate type-body font-medium">
                    {receipt.merchant_extracted ?? "Unread receipt"}
                  </span>
                  <span className="block type-meta text-ink-muted">
                    {receipt.date_extracted
                      ? formatDate(receipt.date_extracted)
                      : formatDate(receipt.created_at.slice(0, 10))}
                  </span>
                </span>

                <span className="flex shrink-0 items-center gap-4">
                  <span className={`type-meta ${STATUS_TONE[receipt.status]}`}>
                    {STATUS_LABEL[receipt.status]}
                  </span>
                  <span className="tabular w-24 text-right type-body font-medium">
                    {receipt.total_extracted ? formatMoney(receipt.total_extracted) : "—"}
                  </span>
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
