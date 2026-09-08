"use client";

import { Suspense, useCallback, useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Info, MapPin as PinIcon, Search } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/features/auth/auth-provider";
import { fetchPins, fetchStore, type Bounds, type MapPin } from "@/features/map/api";
import { PriceMap } from "@/features/map/components/price-map";
import { StoreSheet } from "@/features/map/components/store-sheet";
import { keys } from "@/lib/api/query-keys";

/**
 * The landing screen: a map of where things are cheaper.
 *
 * This replaced the marketing page at `/` in M18, and the reasoning is the same
 * one that moved `/` off the dashboard in the first place. The first screen
 * should be the thing the product does. For a price graph that is a map — a
 * description of one is what `/about` is for.
 *
 * **Readable signed out.** The pins are aggregates over a k-anonymity floor
 * (ADR-013) and name nobody, so there is nothing here to gate. A visitor who
 * has to sign in before seeing whether the map has anything near them has been
 * asked to buy before looking.
 *
 * Because the Android APK is a WebView over the live site, this is also the
 * app's first screen the moment it deploys.
 */
function MapLandingView() {
  const { status } = useAuth();
  const params = useSearchParams();
  const [bounds, setBounds] = useState<Bounds | null>(null);
  const [selected, setSelected] = useState<MapPin | null>(null);
  // Remembered here rather than stripped out of the URL. A `router.replace`
  // would re-render mid-flight and cancel the camera animation, and it would
  // make the back button reopen a sheet the user had just closed.
  const [dismissed, setDismissed] = useState<string | null>(null);

  // `?store=` is how "add a shop" hands the map its answer. The coordinates
  // ride along so the camera can start moving on the first render rather than
  // waiting for the store fetch -- and so it still moves if that request is
  // slow.
  const focusId = params.get("store");
  const focusLat = coordinate(params.get("lat"), 90);
  const focusLon = coordinate(params.get("lon"), 180);

  const pins = useQuery({
    queryKey: bounds ? keys.map.pins(bounds) : ["map-pins", "idle"],
    queryFn: () => fetchPins(bounds!),
    enabled: bounds !== null,
    // The graph changes on the timescale of people shopping, not of panning.
    staleTime: 60_000,
  });

  const focusStore = useQuery({
    queryKey: keys.map.store(focusId ?? ""),
    queryFn: () => fetchStore(focusId!),
    enabled: focusId !== null && focusId !== dismissed,
    staleTime: 60_000,
  });

  // `StoreSheet` takes a `MapPin`, and a shop that was just added is not in
  // `pins` yet: the map has to fly, `moveend` has to land, bounds have to be
  // re-emitted and the viewport query has to come back -- four async steps
  // after the sheet should already be open. So synthesise one from the store
  // and let the real pin replace it when it arrives.
  //
  // The synthesised counts are zero, and for a shop created seconds ago that is
  // not a placeholder, it is the truth: a shop has no prices until somebody
  // reports one.
  const focused = useMemo<MapPin | null>(() => {
    if (!focusId || focusId === dismissed) return null;
    const fromPins = pins.data?.find((pin) => pin.store.id === focusId);
    if (fromPins) return fromPins;
    if (!focusStore.data) return null;
    return { store: focusStore.data, price_count: 0, report_count: 0, headline: null };
  }, [focusId, dismissed, pins.data, focusStore.data]);

  // A pin the user tapped always wins over the one the URL asked for.
  const sheet = selected ?? focused;

  const onBoundsChange = useCallback((next: Bounds) => setBounds(next), []);
  const onSelect = useCallback((pin: MapPin) => setSelected(pin), []);
  const onCloseSheet = useCallback(() => {
    setSelected(null);
    setDismissed(focusId);
  }, [focusId]);

  const found = pins.data?.length ?? 0;

  return (
    // `overflow-clip`, not `overflow-hidden`, and the difference is the whole
    // fix. `hidden` still creates a scroll *container* -- it only hides the
    // scrollbar -- so the browser may still scroll it programmatically. A
    // MapLibre marker is a real <button> positioned by transform; clicking one
    // moves focus, the browser scrolls the container to reveal it, and the
    // header and the sheet end up 442px above the fold while still reporting
    // themselves visible to every assertion that asks. `clip` creates no scroll
    // container, so there is nothing to scroll.
    //
    // `100dvh` rather than `100vh`: on mobile Safari the latter is taller than
    // the visible area, which puts the bottom of the map under the browser
    // chrome.
    <div className="relative h-[100dvh] w-full overflow-clip">
      <div className="h-full w-full">
        <PriceMap
          pins={pins.data ?? []}
          onBoundsChange={onBoundsChange}
          onSelect={onSelect}
          selectedId={sheet?.store.id ?? null}
          focusLat={focusLat}
          focusLon={focusLon}
        />
      </div>

      {/* `md:pr-96` matches the sheet's width, so the header shrinks out from
       * under it rather than being covered. The sheet has to sit above the
       * header -- it is the thing you just opened -- and without this the
       * primary call to action disappears behind it on desktop. */}
      <header
        className={
          sheet
            ? "pointer-events-none absolute inset-x-0 top-0 z-10 p-3 md:pr-96"
            : "pointer-events-none absolute inset-x-0 top-0 z-10 p-3"
        }
      >
        <div className="pointer-events-auto mx-auto flex w-full max-w-3xl items-center gap-2 rounded-card border border-hairline bg-surface p-2 shadow-none">
          <Link href="/about" className="rounded-control px-2 type-section font-semibold">
            Frugal
          </Link>

          <Link
            href="/search"
            className="flex flex-1 items-center gap-2 rounded-control border border-hairline px-3 py-2 type-body text-ink-muted"
          >
            <Search className="size-4" aria-hidden />
            Find something cheaper
          </Link>

          {status === "authenticated" ? (
            <Button size="sm" asChild>
              <Link href="/dashboard">Dashboard</Link>
            </Button>
          ) : (
            <Button variant="brand" size="sm" asChild>
              <Link href="/register">Get started</Link>
            </Button>
          )}
        </div>

        {bounds !== null && !pins.isPending && found === 0 && (
          // An honest empty state rather than an empty map. The graph is built
          // from what people upload, so "nothing here" is a real and common
          // answer early on, and saying why is the difference between a young
          // product and a broken one.
          <p className="pointer-events-auto mx-auto mt-2 w-full max-w-3xl rounded-card border border-hairline bg-surface px-3 py-2 type-meta text-ink-secondary">
            <Info className="mr-1 inline size-3.5" aria-hidden />
            Nothing verified around here yet. Prices appear once a few people have reported the
            same item at the same shop — scan a bill or add a shop to start it off.
          </p>
        )}
      </header>

      {status === "authenticated" && (
        <div className="absolute right-3 bottom-6 z-10 md:bottom-3">
          <Button asChild>
            <Link href="/contribute">
              <PinIcon className="mr-1 size-4" aria-hidden />
              Add a shop
            </Link>
          </Button>
        </div>
      )}

      {sheet && <StoreSheet pin={sheet} onClose={onCloseSheet} />}
    </div>
  );
}

/**
 * Coordinates arrive from a URL anybody can edit, so they become numbers only
 * once they have been proved to be numbers. `flyTo` with a NaN centre throws
 * inside MapLibre, which would take the whole map down over a typo.
 */
function coordinate(raw: string | null, limit: number): number | null {
  if (raw === null) return null;
  const value = Number(raw);
  return Number.isFinite(value) && Math.abs(value) <= limit ? value : null;
}

export default function MapLandingPage() {
  // `useSearchParams` needs a Suspense boundary to keep the route statically
  // renderable in the App Router. Not cosmetic: without one `next build` fails
  // this page outright, and `make check-frontend` runs the build.
  return (
    <Suspense>
      <MapLandingView />
    </Suspense>
  );
}
