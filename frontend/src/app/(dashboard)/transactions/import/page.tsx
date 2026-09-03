"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Section } from "@/components/ui/section";
import { FormError } from "@/features/auth/components/form-error";
import {
  analyzeCsv,
  commitCsv,
  createAccount,
  listAccounts,
  type BulkResponse,
  type ImportAnalysis,
} from "@/features/finance/api";
import { getAccessToken } from "@/lib/api/client";
import { keys } from "@/lib/api/query-keys";
import { formatDate, formatMoney } from "@/lib/format";

/**
 * CSV import wizard.
 *
 * Two phases, matching the API: analyse then commit. The analysis step exists
 * so the user sees the detected mapping *and* how many rows already exist
 * before anything is written -- being told "12 of these are already here" is
 * what makes re-importing feel safe rather than risky.
 */
export default function ImportPage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const [file, setFile] = useState<File | null>(null);
  const [accountId, setAccountId] = useState("");
  const [analysis, setAnalysis] = useState<ImportAnalysis | null>(null);
  const [result, setResult] = useState<BulkResponse | null>(null);
  const [error, setError] = useState<unknown>(null);

  const accounts = useQuery({ queryKey: keys.finance.accounts(), queryFn: listAccounts });

  const ensureAccount = async () => {
    if (accountId) return accountId;
    if (accounts.data?.length) {
      setAccountId(accounts.data[0].id);
      return accounts.data[0].id;
    }
    const created = await createAccount({ name: "Imported Account", type: "bank" });
    setAccountId(created.id);
    return created.id;
  };

  const analyze = useMutation({
    mutationFn: async () => {
      const id = await ensureAccount();
      return analyzeCsv(file!, id, getAccessToken());
    },
    onError: setError,
    onSuccess: setAnalysis,
  });

  const commit = useMutation({
    mutationFn: async () => {
      const mapping = analysis?.detected_mapping ?? {};
      return commitCsv(
        file!,
        accountId,
        {
          date: mapping.date!,
          merchant: mapping.merchant ?? undefined,
          debit: mapping.debit ?? undefined,
          credit: mapping.credit ?? undefined,
          amount: mapping.amount ?? undefined,
        },
        getAccessToken(),
      );
    },
    onError: setError,
    onSuccess: (data) => {
      setResult(data);
      void queryClient.invalidateQueries();
    },
  });

  return (
    <div className="max-w-2xl space-y-8">
      <h1 className="type-title">Import a statement</h1>
      <FormError error={error} />

      {!result && (
        <Section variant="bordered">
          <div className="space-y-4">
            <div className="space-y-2">
              <label htmlFor="csv" className="block type-body font-medium">
                CSV file
              </label>
              {/* The native control is left alone. A styled file input has to
               * re-implement the filename display and the drag target, and gets
               * the keyboard path wrong more often than not. */}
              <input
                id="csv"
                type="file"
                accept=".csv,text/csv"
                className="block w-full type-body text-ink-secondary file:mr-3 file:rounded-control file:border file:border-gridline file:bg-transparent file:px-3 file:py-1.5 file:type-body file:font-medium file:text-ink"
                onChange={(e) => {
                  setFile(e.target.files?.[0] ?? null);
                  setAnalysis(null);
                  setError(null);
                }}
              />
              <p className="type-meta text-ink-muted">
                Most bank exports work. Nothing is saved until you confirm.
              </p>
            </div>

            <Button
              onClick={() => analyze.mutate()}
              disabled={!file || analyze.isPending}
              data-testid="analyze"
            >
              {analyze.isPending ? "Reading…" : "Preview import"}
            </Button>
          </div>
        </Section>
      )}

      {analysis && !result && (
        <Section
          variant="bordered"
          title={`${analysis.row_count} rows found`}
          description={`Columns detected with ${Math.round(Number(analysis.confidence) * 100)}% confidence.`}
          action={
            analysis.duplicate_estimate > 0 && (
              <p className="flex items-center gap-1.5 type-body text-warning">
                <AlertTriangle className="size-4 shrink-0" aria-hidden />
                {analysis.duplicate_estimate} already imported
              </p>
            )
          }
        >
          <div className="space-y-4">
            {analysis.warnings.map((w) => (
              <p key={w} className="type-body text-serious">
                {w}
              </p>
            ))}

            <div className="overflow-x-auto">
              <table className="w-full text-left type-body">
                <thead className="type-eyebrow text-ink-muted">
                  <tr>
                    <th className="py-2 pr-4">Date</th>
                    <th className="py-2 pr-4">Merchant</th>
                    <th className="py-2 pr-4 text-right" data-numeric>
                      Amount
                    </th>
                    <th className="py-2">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-hairline">
                  {analysis.preview.map((row) => (
                    <tr key={row.index}>
                      <td className="py-2 pr-4">
                        {row.occurred_on ? formatDate(row.occurred_on) : "—"}
                      </td>
                      <td className="max-w-[16ch] truncate py-2 pr-4">{row.merchant ?? "—"}</td>
                      <td className="tabular py-2 pr-4 text-right" data-numeric>
                        {row.amount ? formatMoney(row.amount) : "—"}
                      </td>
                      <td className="py-2 type-meta">
                        {row.error ? (
                          <span className="text-critical">{row.error}</span>
                        ) : row.is_duplicate ? (
                          <span className="text-warning">Already imported</span>
                        ) : (
                          <span className="text-good">New</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <Button
              onClick={() => commit.mutate()}
              disabled={commit.isPending}
              data-testid="commit"
            >
              {commit.isPending ? "Importing…" : `Import ${analysis.row_count} rows`}
            </Button>
          </div>
        </Section>
      )}

      {result && (
        <Section variant="bordered" data-testid="import-result">
          <div className="space-y-4">
            <p className="flex items-center gap-2 type-body font-medium text-good">
              <CheckCircle2 className="size-4 shrink-0" aria-hidden />
              Import complete
            </p>
            <dl className="grid grid-cols-3 gap-4">
              {[
                ["Created", result.created],
                ["Already there", result.duplicates],
                ["Skipped", result.errors],
              ].map(([label, value]) => (
                <div key={label as string}>
                  <dt className="type-eyebrow text-ink-muted">{label}</dt>
                  <dd className="tabular type-title">{value}</dd>
                </div>
              ))}
            </dl>
            {result.duplicates > 0 && (
              <p className="type-body text-ink-secondary">
                Rows already in your ledger were skipped rather than duplicated — re-importing
                the same file is always safe.
              </p>
            )}
            <Button onClick={() => router.push("/transactions")}>View transactions</Button>
          </div>
        </Section>
      )}
    </div>
  );
}
