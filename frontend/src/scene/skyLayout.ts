import type { UnitVector, WorldRecord } from "../models/world";

/**
 * Sky composition (M7.2). Pure functions: viewport + exploration progress -> geometry.
 *
 * Coordinates are CSS pixels with the origin at the bottom-left of the viewport and +Y up
 * (the renderer's orthographic camera uses the same units, so DOM overlays line up exactly).
 *
 *  - The Earth is a large-radius arc: its crest sits at `earthTopY`, it sags by `sag` at the
 *    viewport edges, and its centre is far below the screen at (cx, cy).
 *  - Each asteroid rests at a horizontal position taken from its served direction and at an
 *    ALTITUDE above the Earth surface directly below it, derived from its real miss distance.
 *    The altitude range is identical at every x, so nearer always means lower above the arc.
 */

/**
 * Real-distance domain of the altitude scale (km). Fixed constants, never fitted to the data.
 * min = Earth's radius (a miss distance at or below it is "at the surface"; it cannot be a genuine
 * miss); max = 1e8 km, beyond NeoWs's ~0.5 AU close-approach reporting range.
 */
export const ALTITUDE_DOMAIN_KM = { min: 6371, max: 1e8 } as const;
const LOG_MIN = Math.log10(ALTITUDE_DOMAIN_KM.min);
const LOG_MAX = Math.log10(ALTITUDE_DOMAIN_KM.max);

/** Earth crest height as a fraction of viewport height: it sinks as the user rises toward space. */
export const EARTH_CREST_FRACTION = { start: 0.3, end: 0.17 } as const;
/** Arc sag across half the viewport width, as a fraction of viewport height. */
export const EARTH_SAG_FRACTION = 0.1;
/** Lowest resting altitude above the surface (px) and clear space kept below the top edge (px). */
export const MIN_ALTITUDE_PX = 28;
export const TOP_MARGIN_PX = 56;
/** Horizontal band used for asteroids (fraction of half-width), keeping them off the edges. */
export const HORIZONTAL_SPAN = 0.92;

/** Renderer projection of the served direction into the sky. Versioned: changing it moves every asteroid. */
export const SKY_PROJECTION = "longitude-fan-v1";

export interface SkyLayout {
  width: number;
  height: number;
  earthTopY: number;
  sag: number;
  cx: number;
  cy: number;
  radius: number;
  /** Altitude (px) of the farthest representable distance; the nearest sits at MIN_ALTITUDE_PX. */
  altitudeRangePx: number;
}

const smooth = (t: number): number => t * t * (3 - 2 * t);
export const clamp01 = (t: number): number => (Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0);

export function computeLayout(width: number, height: number, progress: number): SkyLayout {
  const w = Math.max(1, width);
  const h = Math.max(1, height);
  const p = smooth(clamp01(progress));
  const earthTopY = h * (EARTH_CREST_FRACTION.start + (EARTH_CREST_FRACTION.end - EARTH_CREST_FRACTION.start) * p);
  const sag = h * EARTH_SAG_FRACTION;
  const halfWidth = (w / 2) * 1.08; // the arc runs slightly past the viewport edges
  const radius = (halfWidth * halfWidth + sag * sag) / (2 * sag);
  return {
    width: w,
    height: h,
    earthTopY,
    sag,
    cx: w / 2,
    cy: earthTopY - radius,
    radius,
    altitudeRangePx: Math.max(MIN_ALTITUDE_PX + 1, h - TOP_MARGIN_PX - earthTopY),
  };
}

/** Height of the Earth surface (px) at screen x. */
export function surfaceY(layout: SkyLayout, x: number): number {
  const dx = Math.min(Math.abs(x - layout.cx), layout.radius);
  return layout.cy + Math.sqrt(layout.radius * layout.radius - dx * dx);
}

/**
 * Real miss distance -> altitude fraction in [0, 1] on a logarithmic scale.
 * Strictly increasing inside ALTITUDE_DOMAIN_KM; distances outside the domain are clamped to its
 * ends (the only case where two different distances share an altitude).
 */
export function altitudeFraction(missDistanceKm: number): number {
  if (!Number.isFinite(missDistanceKm) || missDistanceKm <= 0) {
    throw new RangeError(`miss distance must be a positive finite number, got ${missDistanceKm}`);
  }
  return clamp01((Math.log10(missDistanceKm) - LOG_MIN) / (LOG_MAX - LOG_MIN));
}

/** Altitude above the Earth surface (px) for a real distance. */
export function altitudePx(layout: SkyLayout, missDistanceKm: number): number {
  return MIN_ALTITUDE_PX + altitudeFraction(missDistanceKm) * (layout.altitudeRangePx - MIN_ALTITUDE_PX);
}

/**
 * Horizontal sky position in [-1, 1] from the served illustrative_direction: its longitude
 * atan2(y, x) / pi. The served vector is used as-is (never regenerated or re-seeded); for the
 * backend's uniform-sphere model the longitude is uniformly distributed. Latitude (z) is unused.
 */
export function skyHorizontal(direction: UnitVector): number {
  return Math.atan2(direction.y, direction.x) / Math.PI;
}

export interface RestPosition {
  x: number;
  y: number;
  altitude: number;
}

export function restPosition(layout: SkyLayout, record: WorldRecord): RestPosition {
  const x = layout.cx + skyHorizontal(record.illustrative_direction) * (layout.width / 2) * HORIZONTAL_SPAN;
  const altitude = altitudePx(layout, record.encounter.miss_distance_km);
  return { x, y: surfaceY(layout, x) + altitude, altitude };
}

/** Real reference distances drawn as faint altitude arcs (labelled in the UI). */
export const REFERENCE_DISTANCES_KM: readonly { km: number; label: string }[] = [
  { km: 384_400, label: "Moon distance · 384,400 km" },
  { km: 1e6, label: "1 million km" },
  { km: 1e7, label: "10 million km" },
  { km: 1e8, label: "100 million km" },
];
