"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-provider";
import { keys } from "@/lib/api/query-keys";

import { confirmStore, fetchStoreReports, type MapPin } from "../api";
import { ReportCard } from "./report-card";
import { ReportForm } from "./report-form";

interface Props {
  pin: MapPin;
  onClose: () => void;
}

/**
 * What one shop has to offer, opened by tapping its pin.
 *
 * A sheet rather than a page: the map is the context, and navigating away from
 * it to read one store loses the comparison that brought the user here.
 *
 * Readable signed out; writable signed in. The write controls shipped as a
 * complete backend with no caller in M17, while this component told users to
 * "add it" and gave them nothing to add it with.
 */
export function StoreSheet({ pin, onClose }: Props) {
  const queryClient = useQueryClient();
  const { status } = useAuth();
  const signedIn = status === "authenticated";
  const [adding, setAdding] = useState(false);

  const { store } = pin;

  const reports = useQuery({
    queryKey: keys.community.reports(store.id),
    queryFn: () => fetchStoreReports(store.id),
  });

  const confirm = useMutation({
    mutationFn: () => confirmStore(store.id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["map-pins"] }),
  });

  return (
    <aside
      className="absolute inset-x-0 bottom-0 z-20 max-h-[70vh] overflow-y-auto rounded-t-card border border-hairline bg-surface p-5 md:inset-y-0 md:right-0 md:left-auto md:max-h-none md:w-96 md:rounded-t-none"
      aria-label={`Details for ${store.name}`}
    >
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h2 className="type-title">{store.name}</h2>
          {(store.address_line || store.city || store.pincode) && (
            <p className="type-meta text-ink-muted">
              {[store.address_line, store.city, store.pincode].filter(Boolean).join(", ")}
            </p>
          )}
        </div>
        <Button variant="ghost" onClick={onClose} aria-label="Close">
          <X className="size-4" />
        </Button>
      </div>

      <dl className="mt-4 grid grid-cols-2 gap-3">
        <div>
          <dt className="type-meta text-ink-muted">Verified prices</dt>
          <dd className="tabular type-section">{pin.price_count}</dd>
        </div>
        <div>
          <dt className="type-meta text-ink-muted">Reports</dt>
          <dd className="tabular type-section">{reports.data?.length ?? pin.report_count}</dd>
        </div>
      </dl>

      {!store.confirmed && (
        // A pin somebody dropped and nobody has confirmed is a different kind
        // of thing from one that came out of the OSM import, and the map would
        // be lying by omission if both looked alike. Until M19 this banner was
        // permanent, because nothing ever set `confirmed_at`.
        <div className="mt-3 rounded-control border border-hairline bg-surface-raised p-2">
          <p className="type-meta text-ink-secondary">
            Added by a user and not yet confirmed by anyone else.
          </p>
          {signedIn && (
            <Button
              size="sm"
              variant="secondary"
              className="mt-2"
              disabled={confirm.isPending || confirm.isSuccess}
              onClick={() => confirm.mutate()}
            >
              {confirm.isSuccess ? "Thanks" : "This shop is here"}
            </Button>
          )}
          {confirm.isError && (
            <p className="mt-2 type-meta text-ink-muted">{(confirm.error as Error).message}</p>
          )}
        </div>
      )}

      <section className="mt-5">
        <div className="flex items-center justify-between gap-2">
          <h3 className="type-section">What people say</h3>
          {signedIn && !adding && (
            <Button size="sm" variant="ghost" onClick={() => setAdding(true)}>
              <Plus className="mr-1 size-3.5" aria-hidden />
              Add
            </Button>
          )}
        </div>

        {adding && <ReportForm storeId={store.id} onDone={() => setAdding(false)} />}

        {reports.isPending && <p className="mt-2 type-meta text-ink-muted">Loading…</p>}

        {reports.data?.length === 0 && !adding && (
          <p className="mt-2 type-body text-ink-muted">
            {signedIn
              ? "Nothing reported here yet. If you know something this shop stocks, or a price worth knowing, add it."
              : "Nothing reported here yet. Sign in to add what you know."}
          </p>
        )}

        <ul className="mt-2 space-y-3">
          {reports.data?.map((report) => (
            <ReportCard
              key={report.id}
              report={report}
              storeId={store.id}
              signedIn={signedIn}
            />
          ))}
        </ul>
      </section>
    </aside>
  );
}
