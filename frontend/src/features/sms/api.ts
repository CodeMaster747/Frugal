import { API_BASE_URL, apiFetch } from "@/lib/api/client";

/** Money and confidence are strings on the wire (ADR-003). */
export interface ParsedSms {
  template_id: string;
  issuer: string;
  rail: string;
  direction: "debit" | "credit";
  amount: string;
  confidence: string;
  occurred_on: string | null;
  date_inferred: boolean;
  merchant: string | null;
  vpa: string | null;
  account_tail: string | null;
  reference: string | null;
  reversal: boolean;
  /** Why the confidence is not the template's ceiling. Rendered verbatim. */
  caveats: string[];
}

/**
 * What would happen to a message, reported before anything happens to it.
 *
 * `commit` — read confidently and the account is known.
 * `review` — read doubtfully, or a refund.
 * `needs_account` — read fine, but no account is mapped to its number.
 * `not_a_transaction` — a promotion, a passcode, a payment request.
 */
export type Disposition = "commit" | "review" | "needs_account" | "not_a_transaction";

export interface SmsParseResult {
  parsed: ParsedSms | null;
  disposition: Disposition;
  reason: string;
  resolved_account_id: string | null;
  unmapped_tail: string | null;
}

export interface SmsMessage {
  id: string;
  intake: string;
  sender: string;
  received_at: string;
  /** The original text is dropped once a message is resolved. */
  body_redacted: string;
  status: string;
  template_id: string | null;
  confidence: string | null;
  parsed: ParsedSms | null;
  resolved_account_id: string | null;
  transaction_id: string | null;
}

export interface SmsQueue {
  messages: SmsMessage[];
  /** Unmapped account numbers seen in the queue, and how often each appeared. */
  unmapped_tails: Record<string, number>;
}

export interface DuplicateCandidate {
  transaction_id: string;
  occurred_on: string;
  amount: string;
  merchant: string | null;
  similarity: string;
}

export interface AccountMapping {
  id: string;
  account_id: string;
  kind: string;
  value: string;
  is_primary: boolean;
  confirmed_at: string | null;
  source: string;
}

export interface ImportJob {
  id: string;
  status: string;
  progress: Record<string, number> | null;
  result: Record<string, number> | null;
  error_message: string | null;
}

const BASE = "/api/v1/sms";

/** Read one message without storing it. */
export function parseSms(body: string, sender = ""): Promise<SmsParseResult> {
  return apiFetch(`${BASE}/parse`, {
    method: "POST",
    body: JSON.stringify({ body, sender }),
  });
}

export function ingestSms(
  body: string,
  sender: string,
  intake: "paste" | "share" = "paste",
): Promise<SmsMessage> {
  return apiFetch(`${BASE}/messages`, {
    method: "POST",
    body: JSON.stringify({ body, sender, intake }),
  });
}

/** The device buffer drain. Counts, not per-item errors. */
export function ingestBatch(
  messages: { body: string; sender: string; received_at: string }[],
): Promise<{ created: number; already_held: number; filtered: number; committed: number }> {
  return apiFetch(`${BASE}/messages/batch`, {
    method: "POST",
    body: JSON.stringify({
      messages: messages.map((m) => ({ ...m, intake: "device_live" })),
    }),
  });
}

export function fetchQueue(): Promise<SmsQueue> {
  return apiFetch(`${BASE}/messages`);
}

export function fetchDuplicates(messageId: string): Promise<DuplicateCandidate[]> {
  return apiFetch(`${BASE}/messages/${messageId}/duplicates`);
}

export function commitMessage(
  messageId: string,
  options: { account_id?: string; category_id?: string; allow_duplicate?: boolean } = {},
): Promise<unknown> {
  return apiFetch(`${BASE}/messages/${messageId}/commit`, {
    method: "POST",
    body: JSON.stringify(options),
  });
}

export function dismissMessage(messageId: string): Promise<SmsMessage> {
  return apiFetch(`${BASE}/messages/${messageId}/dismiss`, { method: "POST" });
}

export function fetchMappings(): Promise<AccountMapping[]> {
  return apiFetch(`${BASE}/mappings`);
}

/**
 * Bind a bank's number to an account.
 *
 * Card and account numbers are separate kinds on purpose: the same digits often
 * name both a card and an account at one bank, and reading a card spend against
 * the savings account is a misattribution nobody would notice.
 */
export function mapAccount(
  kind: "account_tail" | "card_tail" | "vpa" | "wallet_handle",
  value: string,
  accountId: string,
): Promise<AccountMapping> {
  return apiFetch(`${BASE}/mappings`, {
    method: "POST",
    body: JSON.stringify({ kind, value, account_id: accountId }),
  });
}

/**
 * Queue a message-backup file for import.
 *
 * Multipart, so it bypasses `apiFetch` for the reason the CSV importer does:
 * `apiFetch` sets a JSON content-type, which would break the boundary header
 * the browser has to generate.
 */
export async function importBackup(file: File, token: string | null): Promise<ImportJob> {
  const form = new FormData();
  form.append("file", file);

  const response = await fetch(`${API_BASE_URL}${BASE}/imports/backup`, {
    method: "POST",
    body: form,
    credentials: "include",
    headers: token ? { Authorization: `Bearer ${token}` } : undefined,
  });

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `Upload failed (${response.status})`);
  }
  return (await response.json()) as ImportJob;
}

export function fetchJob(jobId: string): Promise<ImportJob> {
  return apiFetch(`/api/v1/jobs/${jobId}`);
}
