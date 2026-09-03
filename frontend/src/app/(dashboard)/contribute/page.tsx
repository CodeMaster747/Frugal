"use client";

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useRouter } from "next/navigation";

import { Button } from "@/components/ui/button";
import { Field } from "@/components/ui/field";
import { Section } from "@/components/ui/section";
import { pinStore } from "@/features/map/api";

/**
 * Put a shop on the map.
 *
 * The fallback for everywhere the OpenStreetMap import has never heard of,
 * which for small local retail is most places. Geocoding a printed Indian
 * address resolves poorly and would be a metered call on the path of every
 * receipt (ADR-008's shape of mistake) — while the person standing in the shop
 * knows exactly where it is. So this asks them.
 */
export default function ContributePage() {
  const router = useRouter();
  const queryClient = useQueryClient();

  const [name, setName] = useState("");
  const [pincode, setPincode] = useState("");
  const [note, setNote] = useState("");
  const [coords, setCoords] = useState<{ lat: string; lon: string } | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState<string | null>(null);

  const locate = () => {
    if (!("geolocation" in navigator)) {
      setLocationError("This device cannot report its location. Enter coordinates instead.");
      return;
    }
    setLocating(true);
    setLocationError(null);
    navigator.geolocation.getCurrentPosition(
      (position) => {
        setCoords({
          // Six decimals is about 10 cm, which is far finer than a shopfront
          // needs and is what the NUMERIC(9,6) column stores.
          lat: position.coords.latitude.toFixed(6),
          lon: position.coords.longitude.toFixed(6),
        });
        setLocating(false);
      },
      () => {
        setLocationError(
          "Could not get your location. You can still add the shop by entering coordinates.",
        );
        setLocating(false);
      },
      { enableHighAccuracy: true, timeout: 10_000 },
    );
  };

  const submit = useMutation({
    mutationFn: () =>
      pinStore({
        name: name.trim(),
        latitude: coords!.lat,
        longitude: coords!.lon,
        pincode: pincode.trim() || undefined,
        note: note.trim() || undefined,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["map-pins"] });
      router.push("/");
    },
  });

  const ready = name.trim().length > 1 && coords !== null;

  return (
    <>
      <h1 className="type-title">Add a shop</h1>
      <p className="mt-1 type-body text-ink-secondary">
        For shops that are not on the map yet. Stand in or outside the shop and use your
        location — that is more accurate than any address lookup.
      </p>

      <Section title="The shop" variant="bordered" className="mt-6" as="form">
        <div className="space-y-4">
          <Field
            label="Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="Sri Lakshmi Stores"
            maxLength={200}
          />

          <Field
            label="PIN code"
            hint="Optional, and it makes matching receipts much better."
            value={pincode}
            onChange={(event) => setPincode(event.target.value)}
            inputMode="numeric"
            maxLength={6}
            placeholder="560001"
          />

          <div className="space-y-2">
            <label htmlFor="shop-note" className="block type-body font-medium">
              Anything worth knowing
            </label>
            {/* Not a `Field`: that component owns an `<input>`, and a note wants
             * more than one line. Same border and radius so it reads as the
             * same control family. */}
            <textarea
              id="shop-note"
              value={note}
              onChange={(event) => setNote(event.target.value)}
              className="min-h-20 w-full rounded-control border border-gridline bg-transparent px-3 py-2 type-body text-ink"
              maxLength={400}
            />
            <p className="type-meta text-ink-muted">Optional.</p>
          </div>

          <div>
            <Button type="button" variant="secondary" onClick={locate} disabled={locating}>
              {locating ? "Locating…" : coords ? "Update location" : "Use my location"}
            </Button>

            {coords && (
              <p className="tabular mt-2 type-meta text-ink-muted">
                {coords.lat}, {coords.lon}
              </p>
            )}
            {locationError && <p className="mt-2 type-meta text-ink">{locationError}</p>}
          </div>

          <Button
            type="button"
            disabled={!ready || submit.isPending}
            onClick={() => submit.mutate()}
          >
            {submit.isPending ? "Adding…" : "Add this shop"}
          </Button>

          {submit.isError && (
            <p className="type-meta text-ink">{(submit.error as Error).message}</p>
          )}
        </div>
      </Section>

      <p className="mt-4 type-meta text-ink-muted">
        A shop you add is marked unconfirmed until somebody else agrees it is there. Prices need
        more than one person before anyone else sees them — see{" "}
        <a className="underline" href="/points">
          how points work
        </a>
        .
      </p>
    </>
  );
}
