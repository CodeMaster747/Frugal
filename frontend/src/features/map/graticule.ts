/**
 * A coordinate grid, for when there is no basemap to draw.
 *
 * `NEXT_PUBLIC_BASEMAP_URL` is unset by default -- the archive has to be built
 * and deployed once (ADR-014) -- and until it is, `buildStyle` returns a single
 * background layer. Every pin still renders, which was the design intent, but
 * the result is a flat rectangle of colour: nothing moves when you pan, nothing
 * grows when you zoom, and a working map is indistinguishable from a broken
 * one. That is not a fair description of the state, and it is the first thing
 * anybody says about this screen.
 *
 * So the fallback draws real graticule lines instead. Deliberately *not*
 * invented geography: a made-up coastline would be a lie told in the one place
 * a user is entitled to trust what they see. Meridians and parallels are true
 * everywhere on earth, they give panning and zooming something to move against,
 * and their spacing is a scale bar you cannot lose.
 *
 * Generated per viewport rather than shipped as a fixture, because a grid fine
 * enough for a street is millions of lines at country scale.
 */

/** Degrees between lines, by zoom. Each step is what keeps roughly 8-20 lines
 *  on screen across the whole usable range -- fewer reads as an accident,
 *  more reads as graph paper. */
const STEPS: readonly (readonly [minZoom: number, degrees: number])[] = [
  [15, 0.002],
  [13, 0.005],
  [11, 0.02],
  [9, 0.05],
  [0, 0.2],
];

/** Hard ceiling on lines per axis.
 *
 *  A guard, not a tuning knob: a viewport can be arbitrarily wide during a
 *  zoom-out animation, and without this the step lookup would happily be asked
 *  for tens of thousands of features on a single frame. */
const MAX_LINES = 60;

export function stepFor(zoom: number): number {
  for (const [minZoom, degrees] of STEPS) {
    if (zoom >= minZoom) return degrees;
  }
  return STEPS[STEPS.length - 1][1];
}

export interface GraticuleBounds {
  north: number;
  south: number;
  east: number;
  west: number;
}

/**
 * Lines covering `bounds`, snapped to whole multiples of the step so they stay
 * put while the map moves under them.
 *
 * Returned as GeoJSON rather than drawn, so MapLibre owns the projection and
 * the lines curve correctly at the edges of a wide viewport.
 */
export function graticule(
  bounds: GraticuleBounds,
  zoom: number,
): GeoJSON.FeatureCollection<GeoJSON.LineString> {
  const step = stepFor(zoom);
  const features: GeoJSON.Feature<GeoJSON.LineString>[] = [];

  // One step of margin on each side, so a line does not pop into existence at
  // the edge of the screen part-way through a pan.
  const west = Math.floor(bounds.west / step) * step - step;
  const east = Math.ceil(bounds.east / step) * step + step;
  const south = Math.floor(bounds.south / step) * step - step;
  const north = Math.ceil(bounds.north / step) * step + step;

  // Every tenth line is heavier, which is what turns a uniform mesh into
  // something you can count against.
  const major = (value: number) => Math.abs(Math.round(value / step) % 10) === 0;

  let drawn = 0;
  for (let lon = west; lon <= east && drawn < MAX_LINES; lon += step, drawn += 1) {
    features.push({
      type: "Feature",
      properties: { major: major(lon) },
      geometry: {
        type: "LineString",
        coordinates: [
          [lon, south],
          [lon, north],
        ],
      },
    });
  }

  drawn = 0;
  for (let lat = south; lat <= north && drawn < MAX_LINES; lat += step, drawn += 1) {
    features.push({
      type: "Feature",
      properties: { major: major(lat) },
      geometry: {
        type: "LineString",
        coordinates: [
          [west, lat],
          [east, lat],
        ],
      },
    });
  }

  return { type: "FeatureCollection", features };
}

/** The id the fallback style gives the source, so the component and the style
 *  cannot disagree about it. */
export const GRATICULE_SOURCE = "graticule";

export const EMPTY_GRATICULE: GeoJSON.FeatureCollection<GeoJSON.LineString> = {
  type: "FeatureCollection",
  features: [],
};
