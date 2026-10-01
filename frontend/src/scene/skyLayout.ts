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
 * Real-distance domain of the visualization (km), derived from the population so that no real
 * record is ever clipped:
 *   minKm = Earth's radius (6,371 km): a miss distance at or below it is "at the surface".
 *   maxKm = the larger of 1e8 km and the next 10-million-km boundary above 1.05 x the farthest
 *           real miss distance. Every real asteroid therefore rests strictly inside the sky.
 */
export const DISTANCE_MIN_KM = 6371;
export const DEFAULT_MAX_KM = 1e8;
export const MOON_DISTANCE_KM = 384_400;

export interface DistanceDomain {
  minKm: number;
  maxKm: number;
}

export function distanceDomain(records: readonly WorldRecord[]): DistanceDomain {
  const farthest = records.reduce((m, r) => Math.max(m, r.encounter.miss_distance_km), 0);
  const padded = Math.ceil((farthest * 1.05) / 1e7) * 1e7;
  return { minKm: DISTANCE_MIN_KM, maxKm: Math.max(DEFAULT_MAX_KM, padded) };
}

export const DEFAULT_DOMAIN: DistanceDomain = { minKm: DISTANCE_MIN_KM, maxKm: DEFAULT_MAX_KM };

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
 * Real distance -> fraction in [0, 1] on a logarithmic scale over the domain. Uses the exact
 * value (never rounded). Strictly increasing inside the domain; only distances at or below Earth's
 * radius share the bottom (they cannot be genuine misses), and no real record exceeds maxKm.
 */
export function altitudeFraction(km: number, domain: DistanceDomain = DEFAULT_DOMAIN): number {
  if (!Number.isFinite(km) || km <= 0) throw new RangeError(`distance must be a positive finite number, got ${km}`);
  return clamp01(Math.log(km / domain.minKm) / Math.log(domain.maxKm / domain.minKm));
}

/** Altitude above the Earth surface (px) for a real distance. */
export function altitudePx(layout: SkyLayout, km: number, domain: DistanceDomain = DEFAULT_DOMAIN): number {
  return MIN_ALTITUDE_PX + altitudeFraction(km, domain) * (layout.altitudeRangePx - MIN_ALTITUDE_PX);
}

/**
 * Exploration progress -> REVEALED DISTANCE (km): the inverse of the altitude mapping, so the
 * revealed frontier always sits at altitude MIN + progress x range. Progress 0 reveals nothing
 * (0 km); progress 1 reveals the whole domain. Deterministic, monotonic, finite, bounded.
 */
export function revealedDistanceKm(progress: number, domain: DistanceDomain): number {
  const p = clamp01(progress);
  return p === 0 ? 0 : domain.minKm * (domain.maxKm / domain.minKm) ** p;
}

/** The exploration progress at which a real distance is first revealed. */
export function progressForDistance(km: number, domain: DistanceDomain): number {
  return altitudeFraction(km, domain);
}

/** An asteroid is eligible to be shown once the revealed distance reaches its EXACT miss distance. */
export function isRevealed(missDistanceKm: number, revealedKm: number): boolean {
  return missDistanceKm <= revealedKm;
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

export function restPosition(layout: SkyLayout, record: WorldRecord, domain: DistanceDomain = DEFAULT_DOMAIN): RestPosition {
  const x = layout.cx + skyHorizontal(record.illustrative_direction) * (layout.width / 2) * HORIZONTAL_SPAN;
  const altitude = altitudePx(layout, record.encounter.miss_distance_km, domain);
  return { x, y: surfaceY(layout, x) + altitude, altitude };
}

/** Horizontal position of the Moon landmark (fraction of width). Decorative: NOT a direction model. */
export const MOON_X_FRACTION = 0.86;

/**
 * Distance-scale ticks: every 1,000,000 km up to `uptoKm` (the revealed frontier), within the
 * domain. Presentation only: asteroid positions always use exact miss distances.
 */
export const SCALE_STEP_KM = 1_000_000;
export function scaleTicks(uptoKm: number, domain: DistanceDomain): number[] {
  const out: number[] = [];
  const last = Math.min(uptoKm, domain.maxKm);
  for (let km = SCALE_STEP_KM; km <= last; km += SCALE_STEP_KM) out.push(km);
  return out;
}

/** Revealed-distance indicator text value: floored to whole millions (to 10,000 km below 1M). */
export function frontierLabelKm(revealedKm: number): number {
  if (revealedKm >= SCALE_STEP_KM) return Math.floor(revealedKm / SCALE_STEP_KM) * SCALE_STEP_KM;
  return Math.floor(revealedKm / 10_000) * 10_000;
}
