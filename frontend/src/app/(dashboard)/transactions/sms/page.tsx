"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Link2, Upload, X } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/empty";
import { listAccounts } from "@/features/finance/api";
import {
  commitMessage,
  dismissMessage,
  fetchQueue,
  importBackup,
  mapAccount,
  parseSms,
  type ParsedSms,
  type SmsMessage,
} from "@/features/sms/api";
import { consumeSharedText } from "@/features/sms/native";
import { getAccessToken } from "@/lib/api/client";
import { keys } from "@/lib/api/query-keys";
import { formatDate, formatMoney } from "@/lib/format";

/**
 * The bank-message review queue.
 *
 * Three things reach a user here, in descending order of how often they matter:
 * messages held because no account is mapped to their number, messages read too
 * doubtfully to book unattended, and a paste box for trying one message without
 * storing it.
 *
 * Nothing on this page books a transaction without an explicit click. That is
 * the same rule the service enforces, restated in the UI rather than relied on
 * from it -- a disabled button is not a safety property.
 */
export default function SmsReviewPage() {
  const queryClient = useQueryClient();
  const [draft, setDraft] = useState("");
  const [preview, setPreview] = useState<Awaited<ReturnType<typeof parseSms>> | null>(null);

  const queue = useQuery({ queryKey: keys.sms.queue(), queryFn: fetchQueue });
  const accounts = useQuery({ queryKey: keys.finance.accounts(), queryFn: listAccounts });

  // Text handed over by the Android share sheet, on both APK flavours. The
  // plugin hands each item over exactly once -- `consumeSharedText` clears it
  // -- so this fills the box and then behaves like any other paste: nothing is
  // stored until the user asks for it.
  //
  // Resolves to null in every browser and in the standard build, where the
  // plugin is absent. That is the ShareIntentPlugin contract, not a fallback.
  useEffect(() => {
    let cancelled = false;
    void consumeSharedText().then((text) => {
      if (!cancelled && text) setDraft(text);
    });
    return () => {
      cancelled = true;
    };
  }, []);

  const invalidate = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.sms.queue() }),
      queryClient.invalidateQueries({ queryKey: keys.finance.transactions.all() }),
    ]);

  const tryMessage = useMutation({
    mutationFn: () => parseSms(draft),
    onSuccess: setPreview,
  });

  const commit = useMutation({
    mutationFn: ({ id, accountId }: { id: string; accountId?: string }) =>
      commitMessage(id, { account_id: accountId }),
    onSuccess: invalidate,
  });

  const dismiss = useMutation({
    mutationFn: (id: string) => dismissMessage(id),
    onSuccess: invalidate,
  });

  const bind = useMutation({
    mutationFn: ({ tail, accountId }: { tail: string; accountId: string }) =>
      mapAccount("account_tail", tail, accountId),
    onSuccess: invalidate,
  });

  const upload = useMutation({
    mutationFn: (file: File) => importBackup(file, getAccessToken()),
    onSuccess: invalidate,
  });

  const messages = queue.data?.messages ?? [];
  const unmapped = Object.entries(queue.data?.unmapped_tails ?? {}).sort((a, b) => b[1] - a[1]);
  const accountOptions = accounts.data ?? [];

  return (
    <div className="space-y-8">
      <header className="space-y-1">
        <h1 className="type-title">Bank messages</h1>
        <p className="type-body text-ink-secondary">
          Transactions read from your bank&apos;s SMS alerts. Anything Frugal was not sure about
          waits here rather than going into your accounts.
        </p>
      </header>

      {/* Mapping first: the number on ninety messages is the main account, and
          binding it clears most of the queue in one action. */}
      {unmapped.length > 0 && (
        <section className="space-y-3 rounded-card border border-hairline p-4">
          <h2 className="flex items-center gap-2 type-section">
            <Link2 className="size-4" aria-hidden />
            Which account is this?
          </h2>
          <p className="type-body text-ink-secondary">
            Your bank refers to accounts by their last few digits. Tell Frugal which is which
            and these messages can be recorded. Nothing is guessed — an unmatched number never
            creates an account.
          </p>
          <ul className="space-y-2">
            {unmapped.map(([tail, count]) => (
              <li key={tail} className="flex flex-wrap items-center gap-3">
                <span className="font-mono">•••• {tail}</span>
                <span className="type-meta text-ink-muted">
                  {count} message{count === 1 ? "" : "s"}
                </span>
                <select
                  aria-label={`Account for ${tail}`}
                  className="rounded-control border border-hairline bg-surface px-2 py-1"
                  defaultValue=""
                  onChange={(event) =>
                    event.target.value && bind.mutate({ tail, accountId: event.target.value })
                  }
                >
                  <option value="" disabled>
                    Choose an account…
                  </option>
                  {accountOptions.map((account) => (
                    <option key={account.id} value={account.id}>
                      {account.name}
                    </option>
                  ))}
                </select>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="space-y-3 rounded-card border border-hairline p-4">
        <h2 className="flex items-center gap-2 type-section">
          <Upload className="size-4" aria-hidden />
          Import a message backup
        </h2>
        <p className="type-body text-ink-secondary">
          Export your SMS with any backup app and upload the XML here. Frugal reads years of
          transactions in one go, and needs no permission on your phone to do it.
        </p>
        <input
          aria-label="SMS backup file"
          accept=".xml,text/xml"
          type="file"
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) upload.mutate(file);
          }}
        />
        {upload.isPending && <p className="type-meta text-ink-muted">Uploading…</p>}
        {upload.isSuccess && (
          <p className="type-meta text-ink-muted">
            Queued. Large backups take a few minutes; this page updates as they land.
          </p>
        )}
        {upload.isError && (
          <p className="type-meta text-ink">{(upload.error as Error).message}</p>
        )}
      </section>

      <section className="space-y-3">
        <h2 className="type-section">Waiting for you</h2>
        {messages.length === 0 ? (
          <Empty>
            Nothing waiting. Messages Frugal could not read confidently, or could not match to
            one of your accounts, appear here.
          </Empty>
        ) : (
          <ul className="space-y-3" data-testid="sms-queue">
            {messages.map((message) => (
              <MessageRow
                key={message.id}
                message={message}
                accounts={accountOptions}
                onCommit={(accountId) => commit.mutate({ id: message.id, accountId })}
                onDismiss={() => dismiss.mutate(message.id)}
              />
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-3 rounded-card border border-hairline p-4">
        <h2 className="type-section">Try a message</h2>
        <p className="type-body text-ink-secondary">
          Paste a bank message to see exactly what Frugal reads from it.{" "}
          <strong>Nothing is stored.</strong>
        </p>
        <label className="block space-y-1">
          <span className="type-meta text-ink-secondary">Message</span>
          <textarea
            className="min-h-24 w-full rounded-control border border-hairline bg-surface p-2"
            onChange={(event) => setDraft(event.target.value)}
            placeholder="Sent Rs.450.00 From HDFC Bank A/C x1234 To SWIGGY On 15/08/26…"
            value={draft}
          />
        </label>
        <Button
          disabled={!draft.trim() || tryMessage.isPending}
          onClick={() => tryMessage.mutate()}
        >
          Read it
        </Button>
        {preview && <PreviewPanel result={preview} />}
      </section>
    </div>
  );
}

function PreviewPanel({ result }: { result: Awaited<ReturnType<typeof parseSms>> }) {
  if (!result.parsed) {
    return (
      <div className="rounded-card bg-surface-raised p-4" data-testid="sms-preview">
        <p className="type-body">{result.reason}</p>
      </div>
    );
  }
  return (
    <div className="space-y-2 rounded-card bg-surface-raised p-4" data-testid="sms-preview">
      <ParsedSummary parsed={result.parsed} />
      <p className="type-meta text-ink-muted">{result.reason}</p>
    </div>
  );
}

function ParsedSummary({ parsed }: { parsed: ParsedSms }) {
  return (
    <div className="space-y-1">
      <p className="type-body">
        <strong>
          {parsed.direction === "credit" ? "+" : "−"}
          {formatMoney(parsed.amount)}
        </strong>{" "}
        {parsed.merchant ?? "no payee named"}
        {parsed.account_tail && (
          <span className="text-ink-muted"> · •••• {parsed.account_tail}</span>
        )}
      </p>
      <p className="type-meta text-ink-muted">
        {parsed.occurred_on ? formatDate(parsed.occurred_on) : "no date"}
        {parsed.date_inferred && " (assumed)"} · read with{" "}
        {Math.round(Number(parsed.confidence) * 100)}% confidence
      </p>
      {parsed.caveats.length > 0 && (
        <ul className="ml-4 list-disc type-meta text-ink-muted">
          {parsed.caveats.map((caveat) => (
            <li key={caveat}>{caveat}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

function MessageRow({
  message,
  accounts,
  onCommit,
  onDismiss,
}: {
  message: SmsMessage;
  accounts: { id: string; name: string }[];
  onCommit: (accountId?: string) => void;
  onDismiss: () => void;
}) {
  const [chosen, setChosen] = useState("");
  const needsAccount = message.status === "needs_account";

  return (
    <li className="space-y-3 rounded-card border border-hairline p-4">
      <div className="flex items-start justify-between gap-3">
        <div className="space-y-1">
          {message.parsed ? (
            <ParsedSummary parsed={message.parsed} />
          ) : (
            <p className="type-body">{message.body_redacted}</p>
          )}
          <p className="type-meta text-ink-muted">
            {message.sender} · {formatDate(message.received_at)}
          </p>
        </div>
        {needsAccount && (
          <span className="flex items-center gap-1 type-meta text-ink-muted">
            <AlertTriangle className="size-3.5" aria-hidden />
            No account matched
          </span>
        )}
      </div>

      <p className="type-meta text-ink-muted">{message.body_redacted}</p>

      <div className="flex flex-wrap items-center gap-2">
        {needsAccount && (
          <select
            aria-label="Account for this message"
            className="rounded-control border border-hairline bg-surface px-2 py-1"
            onChange={(event) => setChosen(event.target.value)}
            value={chosen}
          >
            <option value="">Choose an account…</option>
            {accounts.map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        )}
        <Button
          disabled={needsAccount && !chosen}
          onClick={() => onCommit(chosen || undefined)}
          size="sm"
        >
          <Check className="size-4" aria-hidden />
          Record it
        </Button>
        <Button onClick={onDismiss} size="sm" variant="ghost">
          <X className="size-4" aria-hidden />
          Not a transaction
        </Button>
      </div>
    </li>
  );
}
