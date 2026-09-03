"use client";

// MapLibre's own stylesheet, and it is not optional.
//
// Without it `.maplibregl-marker` has no `position: absolute`, so every marker
// falls back to static flow: they stack vertically below the map, the container
// grows a scroll height of 1242px against an 800px box, and the whole overlay
// drifts the first time a click moves focus. Everything still *renders*, which
// is what makes the omission so easy to miss.
import "maplibre-gl/dist/maplibre-gl.css";

import { useCallback, useEffect, useRef, useState } from "react";
import type { Map as MapLibreMap, Marker } from "maplibre-gl";

import { buildStyle, hasBasemap, registerProtocol } from "../basemap";
import { MAX_SPAN_DEGREES, type Bounds, type MapPin } from "../api";

/** Chennai. A first-load centre has to be somewhere, and this is the region the
 *  basemap archive covers -- `scripts/build-basemap.sh` defaults to the same
 *  box. Overridden immediately by device location when the user allows it. */
const FALLBACK_CENTRE: [number, number] = [80.257, 13.0067];
const FALLBACK_ZOOM = 13;

/** Below this the viewport exceeds the two-degree cap the API enforces, so
 *  panning would only produce 400s. */
const MIN_ZOOM = 9;

interface Props {
  pins: MapPin[];
  onBoundsChange: (bounds: Bounds) => void;
  onSelect: (pin: MapPin) => void;
  onLongPress?: (lngLat: { lng: number; lat: number }) => void;
  selectedId?: string | null;
}

export function PriceMap({ pins, onBoundsChange, onSelect, onLongPress, selectedId }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const map = useRef<MapLibreMap | null>(null);
  const markers = useRef<Map<string, Marker>>(new Map());
  const [ready, setReady] = useState(false);

  // Takes the instance rather than reading `map.current`.
  //
  // `load` can fire before the assignment below completes -- reliably so once a
  // basemap is configured and its first tiles are already cached. Reading the
  // ref meant `emitBounds` returned early, no bounds were ever emitted, the
  // pins query stayed disabled, and `moveend` never rescued it because nobody
  // had moved the map. A map with a perfect basemap and no pins on it.
  const emitBounds = useCallback(
    (instance: MapLibreMap) => {
      const b = instance.getBounds();
      onBoundsChange({
        minLat: b.getSouth(),
        maxLat: b.getNorth(),
        minLon: b.getWest(),
        maxLon: b.getEast(),
      });
    },
    [onBoundsChange],
  );

  useEffect(() => {
    if (!container.current || map.current) return;
    let cancelled = false;

    void (async () => {
      const maplibre = await import("maplibre-gl");
      // The same module object `new maplibre.Map` uses below, so the protocol
      // cannot land on a different instance.
      await registerProtocol(maplibre);
      if (cancelled || !container.current) return;

      const dark = document.documentElement.classList.contains("dark");
      const instance = new maplibre.Map({
        container: container.current,
        style: buildStyle(dark),
        center: FALLBACK_CENTRE,
        zoom: FALLBACK_ZOOM,
        minZoom: MIN_ZOOM,
        // Explicit, not `undefined`. ODbL requires attribution wherever these
        // tiles are shown, and leaving it to MapLibre's default meant no
        // control rendered at all. `compact` keeps it to a single "i" on a
        // phone, which is the smallest form that still discharges the licence.
        attributionControl: hasBasemap() ? { compact: true } : false,
      });

      instance.addControl(new maplibre.NavigationControl({ showCompass: false }), "top-right");
      instance.addControl(
        new maplibre.GeolocateControl({
          positionOptions: { enableHighAccuracy: true },
          trackUserLocation: false,
        }),
        "top-right",
      );

      // A long press drops a pin, which is how a shop the OSM import has never
      // heard of gets onto the map. `contextmenu` covers both a right-click and
      // a touch-and-hold, so one handler serves phone and desktop.
      if (onLongPress) {
        instance.on("contextmenu", (event) => onLongPress(event.lngLat));
      }

      // Assigned before the handlers are wired, so nothing downstream can
      // observe a half-built map.
      map.current = instance;

      instance.on("load", () => {
        if (cancelled) return;
        setReady(true);
        emitBounds(instance);
      });
      // `moveend`, not `move`: refetching on every animation frame would send
      // a request per pixel of pan.
      instance.on("moveend", () => emitBounds(instance));
    })();

    return () => {
      cancelled = true;
      map.current?.remove();
      map.current = null;
    };
  }, [emitBounds, onLongPress]);

  // Markers are reconciled rather than rebuilt: clearing and re-adding on every
  // fetch makes the whole set flicker on each pan, and a map that flickers
  // looks broken even when it is right.
  useEffect(() => {
    if (!ready || !map.current) return;
    let cancelled = false;

    void (async () => {
      const maplibre = await import("maplibre-gl");
      if (cancelled || !map.current) return;

      const seen = new Set<string>();

      for (const pin of pins) {
        const { latitude, longitude } = pin.store;
        if (!latitude || !longitude) continue;
        seen.add(pin.store.id);

        const existing = markers.current.get(pin.store.id);
        if (existing) {
          existing.getElement().dataset.selected = String(selectedId === pin.store.id);
          continue;
        }

        const element = document.createElement("button");
        element.type = "button";
        element.className = "price-pin";
        element.dataset.selected = String(selectedId === pin.store.id);
        element.setAttribute(
          "aria-label",
          `${pin.store.name}${pin.headline ? `, ${pin.headline}` : ""}`,
        );
        element.textContent = pin.price_count > 0 ? String(pin.price_count) : "·";
        element.addEventListener("click", (event) => {
          event.stopPropagation();
          onSelect(pin);
        });

        const marker = new maplibre.Marker({ element })
          .setLngLat([Number(longitude), Number(latitude)])
          .addTo(map.current);
        markers.current.set(pin.store.id, marker);
      }

      for (const [id, marker] of markers.current) {
        if (!seen.has(id)) {
          marker.remove();
          markers.current.delete(id);
        }
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [pins, ready, selectedId, onSelect]);

  return (
    <div className="relative h-full w-full">
      <div ref={container} className="h-full w-full" />
      {!hasBasemap() && (
        // Said out loud rather than left to look like a broken map. The pins
        // are the product; the basemap is context for them.
        <p className="pointer-events-none absolute bottom-3 left-3 rounded-control bg-surface/90 px-2 py-1 type-meta text-ink-muted">
          Basemap not configured — pins only.
        </p>
      )}
    </div>
  );
}

export { MAX_SPAN_DEGREES };
