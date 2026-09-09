"use client";

import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquare, Plus, Sparkles, Trash2, Upload } from "lucide-react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { Suspense, useState } from "react";

import { Button } from "@/components/ui/button";
import { Empty } from "@/components/ui/empty";
import { Field } from "@/components/ui/field";
import { Section } from "@/components/ui/section";
import { Select } from "@/components/ui/select";
import { FormError } from "@/features/auth/components/form-error";
import {
  confirmCategory,
  createTransaction,
  deleteTransaction,
  listAccounts,
  listCategories,
  listTransactions,
  updateTransaction,
  type Transaction,
} from "@/features/finance/api";
import { CategoryCell } from "@/features/finance/components/category-cell";
import { formatDate, formatMoney, todayISO } from "@/lib/format";
import { keys } from "@/lib/api/query-keys";

function TransactionsView() {
  const params = useSearchParams();
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(params.get("new") === "1");
  const [search, setSearch] = useState("");
  // Deep-linkable, because the review queue is somewhere the dashboard sends
  // people ("12 need review") rather than a mode they toggle into by hand.
  const [reviewOnly, setReviewOnly] = useState(params.get("review") === "1");

  const accounts = useQuery({ queryKey: keys.finance.accounts(), queryFn: listAccounts });
  const categories = useQuery({ queryKey: keys.finance.categories(), queryFn: listCategories });

  // Cursor pagination: the backend returns an opaque keyset cursor, so
  // inserting a transaction mid-scroll can never skip or duplicate a row.
  const ledger = useInfiniteQuery({
    queryKey: keys.finance.transactions.list({ q: search, reviewOnly }),
    queryFn: ({ pageParam }) =>
      listTransactions({
        cursor: pageParam,
        limit: 25,
        q: search || undefined,
        needs_review: reviewOnly || undefined,
      }),
    initialPageParam: null as string | null,
    getNextPageParam: (last) => last.pagination.next_cursor,
  });

  // Returns the promise so a mutation stays pending until the refetch lands.
  // Without awaiting it the select snaps back to its stale value for a beat,
  // which reads as the edit having failed.
  const refresh = () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: keys.finance.transactions.all() }),
      queryClient.invalidateQueries({ queryKey: keys.finance.accounts() }),
    ]);

  const rows = ledger.data?.pages.flatMap((p) => p.data) ?? [];

  return (
    <div className="space-y-8">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h1 className="type-title">Transactions</h1>
        <div className="flex gap-2">
          {/* Both ways into this ledger sit on the ledger, which is where you
           * look for them. Bank messages had a sidebar row of its own until
           * M20; it is a source of transactions, not a place to be. */}
          <Button variant="secondary" size="sm" asChild>
            <Link href="/transactions/sms">
              <MessageSquare aria-hidden />
              Bank messages
            </Link>
          </Button>
          <Button variant="secondary" size="sm" asChild>
            <Link href="/transactions/import">
              <Upload aria-hidden />
              Import
            </Link>
          </Button>
          <Button size="sm" onClick={() => setAdding((v) => !v)} data-testid="toggle-add">
            <Plus aria-hidden />
            Add transaction
          </Button>
        </div>
      </div>

      {adding && (
        <QuickAdd
          accounts={accounts.data ?? []}
          categories={categories.data ?? []}
          onDone={() => {
            setAdding(false);
            refresh();
          }}
        />
      )}

      <div className="flex flex-wrap items-end gap-3">
        <div className="min-w-56 flex-1">
          <Field
            label="Search merchants"
            placeholder="e.g. swiggy"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <Button
          variant={reviewOnly ? "primary" : "secondary"}
          aria-pressed={reviewOnly}
          data-testid="toggle-review"
          onClick={() => setReviewOnly((v) => !v)}
        >
          <Sparkles aria-hidden />
          {reviewOnly ? "Showing suggestions" : "Review suggestions"}
        </Button>
      </div>

      {reviewOnly && (
        <p className="rounded-control bg-surface-raised px-4 py-3 type-body text-ink-secondary">
          These were categorised automatically and nobody has checked them yet. Confirm the ones
          that look right — each answer, yes or no, teaches the categoriser.
        </p>
      )}

      {ledger.isPending ? (
        <p className="type-body text-ink-muted">Loading…</p>
      ) : rows.length === 0 ? (
        <Empty>No transactions yet. Add one, or import a statement.</Empty>
      ) : (
        <>
          <ul
            className="divide-y divide-hairline rounded-card border border-hairline bg-surface"
            data-testid="txn-list"
          >
            {rows.map((txn) => (
              <Row
                key={txn.id}
                txn={txn}
                categories={categories.data ?? []}
                onChanged={refresh}
              />
            ))}
          </ul>

          {ledger.hasNextPage && (
            <Button
              variant="secondary"
              onClick={() => void ledger.fetchNextPage()}
              disabled={ledger.isFetchingNextPage}
            >
              {ledger.isFetchingNextPage ? "Loading…" : "Load more"}
            </Button>
          )}
        </>
      )}
    </div>
  );
}

function Row({
  txn,
  categories,
  onChanged,
}: {
  txn: Transaction;
  categories: { id: string; name: string; kind: string }[];
  onChanged: () => Promise<unknown>;
}) {
  const [error, setError] = useState<string | null>(null);

  const recategorize = useMutation({
    mutationFn: (categoryId: string) =>
      updateTransaction(txn.id, { category_id: categoryId || null }),
    // A silently reverting dropdown is worse than an error message: the user
    // sees their edit undo itself with no explanation.
    onError: (e: Error) => setError(e.message),
    onSuccess: async () => {
      setError(null);
      await onChanged();
    },
  });
  const confirm = useMutation({
    mutationFn: () => confirmCategory(txn.id),
    onError: (e: Error) => setError(e.message),
    onSuccess: async () => {
      setError(null);
      await onChanged();
    },
  });
  const remove = useMutation({
    mutationFn: () => deleteTransaction(txn.id),
    onError: (e: Error) => setError(e.message),
    onSuccess: onChanged,
  });

  return (
    <li className="flex flex-wrap items-center gap-3 px-4 py-3" data-testid="txn-row">
      <span className="min-w-0 flex-1">
        <span className="block truncate type-body font-medium">
          {txn.merchant_raw ?? "Unknown"}
        </span>
        <span className="block type-meta text-ink-muted">
          {formatDate(txn.occurred_on)}
          {txn.transfer_pair_id && " · transfer"}
        </span>
      </span>

      <CategoryCell
        txn={txn}
        categories={categories}
        onRecategorize={(id) => recategorize.mutate(id)}
        onConfirm={() => confirm.mutate()}
        busy={recategorize.isPending || confirm.isPending}
      />

      <span
        className={`tabular w-28 shrink-0 text-right type-body font-medium ${
          txn.kind === "income" ? "text-delta-up" : ""
        }`}
      >
        {txn.kind === "income" ? "+" : "−"}
        {formatMoney(txn.amount, txn.currency)}
      </span>

      <Button
        variant="ghost"
        size="sm"
        aria-label={`Delete ${txn.merchant_raw ?? "transaction"}`}
        onClick={() => remove.mutate()}
        disabled={remove.isPending}
      >
        <Trash2 aria-hidden />
      </Button>

      {error && (
        <p role="alert" className="basis-full type-meta text-critical">
          {error}
        </p>
      )}
    </li>
  );
}

function QuickAdd({
  accounts,
  categories,
  onDone,
}: {
  accounts: { id: string; name: string }[];
  categories: { id: string; name: string; kind: string }[];
  onDone: () => void;
}) {
  const [error, setError] = useState<unknown>(null);
  const [form, setForm] = useState({
    account_id: accounts[0]?.id ?? "",
    kind: "expense" as "expense" | "income",
    amount: "",
    occurred_on: todayISO(),
    merchant_raw: "",
    category_id: "",
  });

  const create = useMutation({
    mutationFn: () =>
      createTransaction(
        {
          account_id: form.account_id,
          kind: form.kind,
          amount: form.amount,
          occurred_on: form.occurred_on,
          merchant_raw: form.merchant_raw || null,
          category_id: form.category_id || null,
        },
        // An idempotency key makes a double-submit or a retried request safe.
        crypto.randomUUID(),
      ),
    onError: setError,
    onSuccess: onDone,
  });

  if (accounts.length === 0) {
    return (
      <p className="rounded-card border border-dashed border-hairline p-4 type-body">
        Create an account first — a transaction has to belong somewhere.
      </p>
    );
  }

  return (
    <Section
      as="form"
      variant="bordered"
      title="Add a transaction"
      onSubmit={(e) => {
        e.preventDefault();
        setError(null);
        create.mutate();
      }}
    >
      <div className="space-y-4">
        <FormError error={error} />

        <div className="grid gap-4 sm:grid-cols-2">
          <Select
            id="qa-account"
            label="Account"
            value={form.account_id}
            onChange={(e) => setForm({ ...form, account_id: e.target.value })}
          >
            {accounts.map((a) => (
              <option key={a.id} value={a.id}>
                {a.name}
              </option>
            ))}
          </Select>

          <Select
            id="qa-kind"
            label="Type"
            value={form.kind}
            onChange={(e) =>
              setForm({
                ...form,
                kind: e.target.value as "expense" | "income",
                category_id: "",
              })
            }
          >
            <option value="expense">Expense</option>
            <option value="income">Income</option>
          </Select>

          <Field
            label="Amount"
            inputMode="decimal"
            placeholder="1250.00"
            value={form.amount}
            onChange={(e) => setForm({ ...form, amount: e.target.value })}
          />
          <Field
            label="Date"
            type="date"
            value={form.occurred_on}
            onChange={(e) => setForm({ ...form, occurred_on: e.target.value })}
          />
          <Field
            label="Merchant"
            placeholder="Reliance Fresh"
            value={form.merchant_raw}
            onChange={(e) => setForm({ ...form, merchant_raw: e.target.value })}
          />

          <Select
            id="qa-category"
            label="Category"
            value={form.category_id}
            onChange={(e) => setForm({ ...form, category_id: e.target.value })}
          >
            <option value="">Uncategorised</option>
            {categories
              .filter((c) => c.kind === form.kind)
              .map((c) => (
                <option key={c.id} value={c.id}>
                  {c.name}
                </option>
              ))}
          </Select>
        </div>

        <Button type="submit" disabled={create.isPending || !form.amount}>
          {create.isPending ? "Saving…" : "Save transaction"}
        </Button>
      </div>
    </Section>
  );
}

export default function TransactionsPage() {
  return (
    <Suspense>
      <TransactionsView />
    </Suspense>
  );
}
