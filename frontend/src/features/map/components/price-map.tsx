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
import type { GeoJSONSource, Map as MapLibreMap, Marker } from "maplibre-gl";

import { buildStyle, hasBasemap, registerProtocol } from "../basemap";
import { GRATICULE_SOURCE, graticule } from "../graticule";
import { MAX_SPAN_DEGREES, type Bounds, type MapPin } from "../api";

/** Chennai. A first-load centre has to be somewhere, and this is the region the
 *  basemap archive covers -- `scripts/build-basemap.sh` defaults to the same
 *  box. Overridden immediately by device location when the user allows it. */
const FALLBACK_CENTRE: [number, number] = [80.257, 13.0067];
const FALLBACK_ZOOM = 13;

/** Where a focused shop lands. The point of flying somewhere is being able to
 *  see which corner the shop is on, and zoom 13 cannot show that. Past the
 *  archive's maxzoom of 14, so MapLibre overzooms the z14 tile -- which is what
 *  overzooming is for, and costs no extra request. */
const FOCUS_ZOOM = 16;

/** Below this the viewport exceeds the two-degree cap the API enforces, so
 *  panning would only produce 400s. */
const MIN_ZOOM = 9;

interface Props {
  pins: MapPin[];
  onBoundsChange: (bounds: Bounds) => void;
  onSelect: (pin: MapPin) => void;
  onLongPress?: (lngLat: { lng: number; lat: number }) => void;
  selectedId?: string | null;
  /** Where to put the camera once the map is up -- a shop somebody just added,
   *  arriving through the URL.
   *
   *  Two numbers rather than one `{lng, lat}` object, deliberately: an object
   *  literal from the parent is a fresh identity on every render, so the effect
   *  below would re-fly on each one and fight the user's own panning.
   *  Primitives make that unrepresentable rather than merely avoided by a
   *  `useMemo` somebody can later delete. */
  focusLat?: number | null;
  focusLon?: number | null;
}

export function PriceMap({
  pins,
  onBoundsChange,
  onSelect,
  onLongPress,
  selectedId,
  focusLat,
  focusLon,
}: Props) {
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

      // The fallback grid is regenerated for whatever is now on screen. Only
      // when there is no archive: with one, the style has no such source and
      // `getSource` would return undefined.
      if (hasBasemap()) return;
      // `getSource` is generic over the source type rather than returning a
      // discriminated union, so the type is named here. It can still be
      // undefined -- during style reloads there is a window where the source
      // does not exist yet -- which is why this is optional-chained.
      const source = instance.getSource<GeoJSONSource>(GRATICULE_SOURCE);
      source?.setData(
        graticule(
          {
            north: b.getNorth(),
            south: b.getSouth(),
            east: b.getEast(),
            west: b.getWest(),
          },
          instance.getZoom(),
        ),
      );
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
      // Distance, stated. It earns its place either way, but it is what makes
      // the no-archive grid readable as a map rather than as decoration.
      instance.addControl(new maplibre.ScaleControl({ unit: "metric" }), "bottom-right");
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

  // Brings a shop somebody just added under the eye.
  //
  // Its own effect, and deliberately *not* folded into the one above. That
  // effect's cleanup calls `map.current.remove()`, so any dependency added to
  // it tears the map down and rebuilds it whenever the value changes -- which
  // would turn "fly to the new shop" into "destroy the map and lose every
  // marker".
  //
  // Keyed on `ready` rather than on `map.current`, because a ref is not
  // reactive and this has to run when the map finishes loading. By the time
  // `ready` is true the assignment above has happened -- that ordering is the
  // invariant the comment there is protecting.
  //
  // Nothing here places a marker. The flight ends in `moveend`, which re-emits
  // bounds and refetches the pins for wherever it landed; the new shop's own
  // marker arrives with them.
  useEffect(() => {
    if (!ready || !map.current) return;
    if (focusLat == null || focusLon == null) return;

    // No `essential: true`: under prefers-reduced-motion MapLibre jumps instead
    // of animating, which still ends at the shop. The destination is the
    // requirement; the flight is decoration.
    map.current.flyTo({ center: [focusLon, focusLat], zoom: FOCUS_ZOOM });
  }, [ready, focusLat, focusLon]);

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
        // An unconfirmed pin is one person's word that a shop is there, which
        // is a different claim from an imported one. The store sheet has said
        // so since M19; the map said nothing, so the two disagreed.
        element.dataset.unconfirmed = String(!pin.store.confirmed);
        element.setAttribute(
          "aria-label",
          `${pin.store.name}${pin.headline ? `, ${pin.headline}` : ""}`,
        );

        // The name, not just a count. A pin whose entire content was
        // `price_count || "·"` made a shop somebody had just added
        // indistinguishable from a speck of dust, and made a map full of pins
        // unreadable without tapping every one of them. The count keeps its
        // own slot so the two never run together.
        const label = document.createElement("span");
        label.className = "price-pin__name";
        label.textContent = pin.store.name;
        element.append(label);

        if (pin.price_count > 0) {
          const count = document.createElement("span");
          count.className = "price-pin__count";
          count.textContent = String(pin.price_count);
          element.append(count);
        }

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
        // Said out loud rather than left to look like a broken map. The grid
        // behind the pins is real coordinates, not streets, and the difference
        // matters to anybody trying to find a shop by eye.
        <p className="pointer-events-none absolute bottom-3 left-3 rounded-control bg-surface/90 px-2 py-1 type-meta text-ink-muted">
          No basemap here — showing a coordinate grid and pins.
        </p>
      )}
    </div>
  );
}

export { MAX_SPAN_DEGREES };
