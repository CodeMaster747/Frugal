import { API_BASE_URL, apiFetch, getAccessToken } from "@/lib/api/client";

export interface ReceiptField {
  field_name: "merchant" | "date" | "total" | "tax" | "subtotal" | "payment_method";
  raw_text: string | null;
  parsed_value: string | null;
  corrected_value: string | null;
  effective_value: string | null;
  confidence: string;
  bbox: { x: number; y: number; w: number; h: number } | null;
  needs_review: boolean;
  corrected_at: string | null;
}

export interface DuplicateCandidate {
  transaction_id: string;
  occurred_on: string;
  amount: string;
  merchant: string | null;
  similarity: string;
}

export interface Receipt {
  id: string;
  status:
    | "pending_upload"
    | "queued"
    | "processing"
    | "needs_review"
    | "ready"
    | "committed"
    | "failed";
  merchant_extracted: string | null;
  total_extracted: string | null;
  date_extracted: string | null;
  overall_confidence: string | null;
  processing_ms: number | null;
  error_message: string | null;
  committed_transaction_id: string | null;
  created_at: string;
  fields: ReceiptField[];
  line_items: { line_number: number; description: string | null; total_price: string | null }[];
  duplicate_candidates: DuplicateCandidate[];
  /** Required fields still standing in the way of committing. */
  blocking_fields: string[];
}

export interface JobStatus {
  id: string;
  status: "queued" | "running" | "succeeded" | "failed" | "dead_lettered";
  progress: { stage: string; pct: number } | null;
  error_message: string | null;
}

export const listReceipts = () => apiFetch<Receipt[]>("/api/v1/receipts");
export const getReceipt = (id: string) => apiFetch<Receipt>(`/api/v1/receipts/${id}`);
export const getJob = (id: string) => apiFetch<JobStatus>(`/api/v1/jobs/${id}`);

export const getImageUrl = (id: string) =>
  apiFetch<{ url: string; expires_in: number }>(`/api/v1/receipts/${id}/image-url`);

export const correctFields = (id: string, corrections: Record<string, string>) =>
  apiFetch<Receipt>(`/api/v1/receipts/${id}/fields`, {
    method: "PATCH",
    body: JSON.stringify({ corrections }),
  });

export const commitReceipt = (
  id: string,
  input: { account_id: string; category_id?: string | null; allow_duplicate?: boolean },
) =>
  apiFetch<{ transaction_id: string; amount: string; occurred_on: string }>(
    `/api/v1/receipts/${id}/commit`,
    { method: "POST", body: JSON.stringify(input) },
  );

export const deleteReceipt = (id: string) =>
  apiFetch<void>(`/api/v1/receipts/${id}`, { method: "DELETE" });

/**
 * Upload a receipt image.
 *
 * Three steps, and the middle one is the point: the browser PUTs the bytes
 * **straight to object storage** using a presigned URL. They never pass through
 * the API, which on a 1 GB instance would consume request workers and stall the
 * event loop (FR-4.1).
 */
export async function uploadReceipt(file: File): Promise<{ receiptId: string; jobId: string }> {
  const ticket = await apiFetch<{
    receipt_id: string;
    upload_url: string;
    upload_headers?: Record<string, string>;
  }>("/api/v1/receipts/upload-url", {
    method: "POST",
    body: JSON.stringify({ content_type: file.type, size_bytes: file.size }),
  });

  // `upload_headers` comes from whichever storage adapter issued the URL:
  // empty on S3, `x-ms-blob-type: BlockBlob` on Azure, which rejects a PUT
  // without it. Spread rather than branched on, so this file never learns
  // which backend the deployment runs (ADR-010).
  const put = await fetch(ticket.upload_url, {
    method: "PUT",
    body: file,
    headers: { "Content-Type": file.type, ...(ticket.upload_headers ?? {}) },
  });
  if (!put.ok) throw new Error(`Upload failed (${put.status})`);

  const job = await apiFetch<{ job_id: string }>(
    `/api/v1/receipts/${ticket.receipt_id}/process`,
    { method: "POST" },
  );

  return { receiptId: ticket.receipt_id, jobId: job.job_id };
}

/** Kept for parity with the finance uploader; unused here but same auth path. */
export const authHeader = () => {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : undefined;
};

export { API_BASE_URL };
