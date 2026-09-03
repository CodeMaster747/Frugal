"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { Button } from "@/components/ui/button";
import { keys } from "@/lib/api/query-keys";

import { createReport, type ReportKind } from "../api";

/**
 * Tell other people what a shop has, or what it charges.
 *
 * The path for shops that print nothing scannable — which is most small retail,
 * and the reason the receipt path alone would leave the map empty outside chain
 * stores. The backend for this shipped in M17 with no caller.
 */
export function ReportForm({ storeId, onDone }: { storeId: string; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<ReportKind>("good_price");
  const [itemText, setItemText] = useState("");
  const [price, setPrice] = useState("");
  const [note, setNote] = useState("");

  const submit = useMutation({
    mutationFn: () =>
      createReport({
        store_id: storeId,
        kind,
        item_text: itemText.trim(),
        // The server refuses a good-price report with no price, so the form
        // does not offer to send one.
        price: kind === "good_price" ? price.trim() : undefined,
        note: note.trim() || undefined,
      }),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: keys.community.reports(storeId) }),
        queryClient.invalidateQueries({ queryKey: keys.points.balance() }),
        queryClient.invalidateQueries({ queryKey: ["map-pins"] }),
      ]);
      setItemText("");
      setPrice("");
      setNote("");
      onDone();
    },
  });

  const ready =
    itemText.trim().length > 1 && (kind !== "good_price" || price.trim().length > 0);

  return (
    <form
      className="mt-3 space-y-3 rounded-control border border-hairline p-3"
      onSubmit={(event) => {
        event.preventDefault();
        submit.mutate();
      }}
    >
      <fieldset>
        <legend className="type-meta text-ink-muted">What are you reporting?</legend>
        <div className="mt-1 flex gap-2">
          {(
            [
              ["good_price", "A good price"],
              ["unique_item", "Something they stock"],
            ] as const
          ).map(([value, label]) => (
            <button
              key={value}
              type="button"
              onClick={() => setKind(value)}
              aria-pressed={kind === value}
              className={
                kind === value
                  ? "rounded-control border border-hairline bg-surface-raised px-2 py-1 type-meta font-medium"
                  : "rounded-control border border-hairline px-2 py-1 type-meta text-ink-muted"
              }
            >
              {label}
            </button>
          ))}
        </div>
      </fieldset>

      <label className="block">
        <span className="type-meta text-ink-secondary">Item</span>
        <input
          value={itemText}
          onChange={(event) => setItemText(event.target.value)}
          placeholder="Amul Taaza Milk 500ml"
          maxLength={200}
          className="mt-1 h-10 w-full rounded-control border border-gridline bg-transparent px-2 type-body"
        />
      </label>

      {kind === "good_price" && (
        <label className="block">
          <span className="type-meta text-ink-secondary">Price</span>
          <input
            value={price}
            onChange={(event) => setPrice(event.target.value)}
            inputMode="decimal"
            placeholder="28.00"
            className="tabular mt-1 h-10 w-full rounded-control border border-gridline bg-transparent px-2 type-body"
          />
        </label>
      )}

      <label className="block">
        <span className="type-meta text-ink-secondary">Anything else</span>
        <input
          value={note}
          onChange={(event) => setNote(event.target.value)}
          maxLength={400}
          className="mt-1 h-10 w-full rounded-control border border-gridline bg-transparent px-2 type-body"
        />
      </label>

      <div className="flex items-center gap-2">
        <Button type="submit" size="sm" disabled={!ready || submit.isPending}>
          {submit.isPending ? "Posting…" : "Post"}
        </Button>
        <Button type="button" size="sm" variant="ghost" onClick={onDone}>
          Cancel
        </Button>
      </div>

      {submit.isError && (
        <p className="type-meta text-critical">{(submit.error as Error).message}</p>
      )}

      <p className="type-meta text-ink-muted">
        A price becomes visible to everyone once enough people confirm it. Yours counts for
        half, so somebody else always has to agree.
      </p>
    </form>
  );
}
