/**
 * The basemap, and why it is a file rather than a service.
 *
 * NFR-7 requires the deployment to cost nothing, and ADR-008 requires any
 * metered dependency to pass through a quota ledger. A tile API is metered by
 * construction, so a map built on one would either need a key, a card, and a
 * ledger — or would silently stop working the month somebody zoomed too much.
 *
 * Protomaps sidesteps the whole category: one `.pmtiles` archive, fetched by
 * HTTP range requests straight from object storage. No key, no rate limit, no
 * third-party usage policy, no per-tile cost.
 *
 * **MapLibre is pinned to the 5.x line, deliberately.** `pmtiles` 4.5 is built
 * against it, and against 6.x its protocol never resolves: the header range
 * request goes out, the vector source stays pending forever, and the map fires
 * neither `load` nor `error`. A perfectly good archive renders as a blank
 * background with nothing anywhere to debug. Verified by bisection -- 6.6.0
 * hangs, 5.24.0 fetches tiles and paints. Do not bump the major without
 * re-running `frontend/e2e/m19-map.spec.ts` against a real archive.
 *
 * When `NEXT_PUBLIC_BASEMAP_URL` is unset — which is the default, because the
 * archive has to be built and uploaded once — the map falls back to a plain
 * background and still renders every pin. That is a deliberate degradation
 * rather than a blank screen: the pins are the product, and the basemap is
 * context for them.
 */

import type { AddProtocolAction, StyleSpecification } from "maplibre-gl";

import { EMPTY_GRATICULE, GRATICULE_SOURCE } from "./graticule";

export const BASEMAP_URL = process.env.NEXT_PUBLIC_BASEMAP_URL ?? "";

/** Must match `--maxzoom` in `scripts/build-basemap.sh`. */
export const BASEMAP_MAXZOOM = Number(process.env.NEXT_PUBLIC_BASEMAP_MAXZOOM ?? 14);

export function hasBasemap(): boolean {
  return BASEMAP_URL.length > 0;
}

/**
 * Registers the `pmtiles://` protocol. Idempotent.
 *
 * Takes the MapLibre module rather than importing it, and that is the whole
 * point of the signature. Two `await import("maplibre-gl")` calls in two files
 * can resolve to two module instances once the bundler splits them, and then
 * `addProtocol` registers on one while `new Map` runs on the other. The symptom
 * is silent and total: the source stays pending forever, and the map fires
 * neither `load` nor `error`. Passing the instance in makes that
 * unrepresentable.
 */
let registered = false;
export async function registerProtocol(maplibre: {
  addProtocol: (name: string, fn: AddProtocolAction) => void;
}): Promise<void> {
  if (registered || !hasBasemap()) return;

  const { Protocol } = await import("pmtiles");

  // `metadata: true` costs one extra range request and is what lets the
  // archive's own attribution string reach the attribution control -- an ODbL
  // obligation, not a nicety.
  const protocol = new Protocol({ metadata: true });

  // `tilev4`, not `tile`: the latter is a dual-mode shim that still supports
  // MapLibre's old callback style, and v6 expects the Promise form.
  maplibre.addProtocol("pmtiles", protocol.tilev4);
  registered = true;
}

/**
 * Two palettes, because the artifact renders in the viewer's theme and a map
 * that ignores it is the brightest thing on a dark screen.
 *
 * Deliberately muted: the pins carry the information, and a basemap competing
 * with them is a basemap doing the wrong job.
 */
const LIGHT = {
  earth: "#f6f5f3",
  water: "#c9dced",
  land: "#eceae5",
  building: "#e4e1db",
  road: "#ffffff",
  roadCase: "#e0ddd7",
  boundary: "#d5d0c9",
  grid: "#dedbd5",
  gridMajor: "#c9c5be",
};

const DARK = {
  earth: "#0f0f0f",
  water: "#10202b",
  land: "#181818",
  building: "#202020",
  road: "#2b2b2b",
  roadCase: "#1a1a1a",
  boundary: "#333333",
  grid: "#232323",
  gridMajor: "#333333",
};

export function buildStyle(dark: boolean): StyleSpecification {
  const c = dark ? DARK : LIGHT;

  if (!hasBasemap()) {
    // No archive configured. A supported state -- the pins are the product and
    // the basemap is context for them -- but a bare background layer made it
    // look like a failed one: nothing moved on pan, nothing grew on zoom, and
    // a correct map was indistinguishable from a broken one.
    //
    // So the fallback draws a coordinate grid, fed per viewport by the map
    // component. Real meridians and parallels rather than invented geography:
    // a made-up coastline would be a lie told in the one place a user is
    // entitled to trust what they see. See `graticule.ts`.
    return {
      version: 8,
      sources: {
        [GRATICULE_SOURCE]: { type: "geojson", data: EMPTY_GRATICULE },
      },
      layers: [
        { id: "background", type: "background", paint: { "background-color": c.earth } },
        {
          id: "graticule",
          type: "line",
          source: GRATICULE_SOURCE,
          filter: ["!", ["get", "major"]],
          paint: { "line-color": c.grid, "line-width": 1 },
        },
        // Every tenth line, heavier. A uniform mesh gives no sense of scale;
        // a repeating heavier line is what you count against.
        {
          id: "graticule-major",
          type: "line",
          source: GRATICULE_SOURCE,
          filter: ["get", "major"],
          paint: { "line-color": c.gridMajor, "line-width": 1.25 },
        },
      ],
    };
  }

  return {
    version: 8,
    sources: {
      protomaps: {
        type: "vector",
        // The explicit `tiles` form, not `url: "pmtiles://..."`.
        //
        // The `url` form asks MapLibre to resolve TileJSON through the custom
        // protocol first. Against MapLibre 6 that never completes: the header
        // range request goes out, the source stays pending, and the map fires
        // neither `load` nor `error` -- a working archive renders as a blank
        // background with nothing to debug. Naming the tile template skips that
        // handshake and one round trip with it.
        //
        // `maxzoom` must match what `scripts/build-basemap.sh` extracted, or
        // MapLibre asks for tiles the archive does not contain instead of
        // overzooming the ones it does.
        tiles: [`pmtiles://${BASEMAP_URL}/{z}/{x}/{y}`],
        minzoom: 0,
        maxzoom: BASEMAP_MAXZOOM,
        // Required by ODbL. The archive carries its own attribution string in
        // metadata, but MapLibre only reads that for TileJSON sources, so it is
        // repeated here where the control can find it.
        attribution:
          '<a href="https://protomaps.com" target="_blank">Protomaps</a> © ' +
          '<a href="https://www.openstreetmap.org/copyright" target="_blank">OpenStreetMap</a>',
      },
    },
    // Layer ids and `source-layer` names come from the archive itself
    // (`pmtiles show --metadata`): boundaries, buildings, earth, landcover,
    // landuse, places, pois, roads, water. Guessing them produces a map that
    // renders a blank background with no error, which is exactly how a wrong
    // basemap looks like a missing one.
    //
    // No `places` or `pois` layer, and no glyph source: labels need a font
    // stack served from somewhere, and adding one would put a second network
    // dependency behind a map whose entire point is not having any. The pins
    // carry the names that matter.
    layers: [
      { id: "background", type: "background", paint: { "background-color": c.water } },
      {
        id: "earth",
        type: "fill",
        source: "protomaps",
        "source-layer": "earth",
        paint: { "fill-color": c.earth },
      },
      {
        id: "landuse",
        type: "fill",
        source: "protomaps",
        "source-layer": "landuse",
        paint: { "fill-color": c.land },
      },
      {
        id: "water",
        type: "fill",
        source: "protomaps",
        "source-layer": "water",
        paint: { "fill-color": c.water },
      },
      {
        id: "buildings",
        type: "fill",
        source: "protomaps",
        "source-layer": "buildings",
        minzoom: 14,
        paint: { "fill-color": c.building },
      },
      // Two passes: a wider casing under a narrower fill, which is what makes a
      // road read as a road rather than a scratch. Widths interpolate so the
      // network stays legible from city scale to street scale.
      {
        id: "roads-casing",
        type: "line",
        source: "protomaps",
        "source-layer": "roads",
        paint: {
          "line-color": c.roadCase,
          "line-width": ["interpolate", ["exponential", 1.6], ["zoom"], 10, 1, 16, 8],
        },
      },
      {
        id: "roads",
        type: "line",
        source: "protomaps",
        "source-layer": "roads",
        paint: {
          "line-color": c.road,
          "line-width": ["interpolate", ["exponential", 1.6], ["zoom"], 10, 0.5, 16, 5],
        },
      },
      {
        id: "boundaries",
        type: "line",
        source: "protomaps",
        "source-layer": "boundaries",
        paint: { "line-color": c.boundary, "line-width": 0.8, "line-dasharray": [3, 2] },
      },
    ],
  };
}
