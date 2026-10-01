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
 * EXPLORATION JOURNEY: progress in [0, 1] -> revealed distance (km). Three documented stages, each
 * strictly increasing and continuous at the joins, so the mapping is deterministic, monotonic,
 * finite and bounded by the domain:
 *
 *   p = 0                         0 km (nothing revealed: daytime sky, no Moon, no asteroids)
 *   0 < p <= MOON_PROGRESS        log  6,371 km -> 384,400 km   (leaving the atmosphere; sky -> night)
 *   MOON_PROGRESS < p <= FIELD    log  384,400 km -> 1,000,000 km (the Moon landmark is passed)
 *   FIELD_PROGRESS < p <= 1       1M + (maxKm - 1M) * t^FIELD_EXPONENT  (the million-km field;
 *                                 t = (p - FIELD) / (1 - FIELD); ~0.3-2.9M km per wheel notch)
 */
export const MOON_PROGRESS = 0.26;
export const FIELD_PROGRESS = 0.32;
export const FIELD_START_KM = 1_000_000;
export const FIELD_EXPONENT = 1.6;

export function revealedDistanceKm(progress: number, domain: DistanceDomain): number {
  const p = clamp01(progress);
  if (p === 0) return 0;
  if (p <= MOON_PROGRESS) return domain.minKm * (MOON_DISTANCE_KM / domain.minKm) ** (p / MOON_PROGRESS);
  if (p <= FIELD_PROGRESS) {
    return MOON_DISTANCE_KM * (FIELD_START_KM / MOON_DISTANCE_KM) ** ((p - MOON_PROGRESS) / (FIELD_PROGRESS - MOON_PROGRESS));
  }
  const t = (p - FIELD_PROGRESS) / (1 - FIELD_PROGRESS);
  return Math.min(domain.maxKm, FIELD_START_KM + (domain.maxKm - FIELD_START_KM) * t ** FIELD_EXPONENT);
}

/** The exploration progress at which a real distance is first revealed (inverse of the journey). */
export function progressForDistance(km: number, domain: DistanceDomain): number {
  if (!Number.isFinite(km) || km <= domain.minKm) return 0;
  if (km <= MOON_DISTANCE_KM) return (MOON_PROGRESS * Math.log(km / domain.minKm)) / Math.log(MOON_DISTANCE_KM / domain.minKm);
  if (km <= FIELD_START_KM) {
    return MOON_PROGRESS + ((FIELD_PROGRESS - MOON_PROGRESS) * Math.log(km / MOON_DISTANCE_KM)) / Math.log(FIELD_START_KM / MOON_DISTANCE_KM);
  }
  const t = ((Math.min(km, domain.maxKm) - FIELD_START_KM) / (domain.maxKm - FIELD_START_KM)) ** (1 / FIELD_EXPONENT);
  return FIELD_PROGRESS + t * (1 - FIELD_PROGRESS);
}

/** An asteroid is eligible to be shown once the revealed distance reaches its EXACT miss distance. */
export function isRevealed(missDistanceKm: number, revealedKm: number): boolean {
  return missDistanceKm <= revealedKm;
}

/**
 * VISUAL DISTANCE MAPPING (focus + context). Height above the Earth arc is a function of the exact
 * distance AND the current frontier (the revealed distance), so the distance being explored is
 * always legible while everything nearer stays visible, compressed toward Earth:
 *
 *   km <= F:  fraction = PHI * ( BETA * ctx(km) / ctx(F)  +  (1 - BETA) * (km / F)^GAMMA )
 *   km >  F:  fraction = PHI + (1 - PHI) * (1 - (F / km)^2)          (unrevealed: above the frontier)
 *
 *   F    = max(revealed distance, MIN_FRONTIER_KM)
 *   ctx  = log(km / 6,371) / log(maxKm / 6,371)   (global log context, so near objects stay apart)
 *   PHI  = FRONTIER_FRACTION: the frontier always sits at this fraction of the sky height.
 *
 * For any frontier, the fraction is STRICTLY increasing in km (a sum of increasing terms; the two
 * branches meet at PHI), so a nearer asteroid always rests lower than a farther one. As the user
 * travels outward the whole field slides toward Earth: the Moon and earlier arcs are "passed".
 * The million-km guide arcs use exactly this mapping, so an asteroid at 12.4M km rests between the
 * 12M and 13M arcs. Exact source values are used; nothing is rounded.
 */
export const FRONTIER_FRACTION = 0.86;
export const CONTEXT_WEIGHT = 0.3;
export const FRONTIER_EXPONENT = 1.6;
export const MIN_FRONTIER_KM = 10_000;

export interface DistanceView {
  domain: DistanceDomain;
  /** The revealed distance (km) the view is focused on. */
  frontierKm: number;
}

/** Global log context in [0, 1] (0 at or below Earth's radius). */
export function contextFraction(km: number, domain: DistanceDomain): number {
  if (km <= domain.minKm) return 0;
  return clamp01(Math.log(km / domain.minKm) / Math.log(domain.maxKm / domain.minKm));
}

export function distanceFraction(km: number, view: DistanceView): number {
  if (!Number.isFinite(km) || km <= 0) throw new RangeError(`distance must be a positive finite number, got ${km}`);
  const f = Math.max(Number.isFinite(view.frontierKm) ? view.frontierKm : 0, MIN_FRONTIER_KM);
  if (km <= f) {
    const context = contextFraction(km, view.domain) / contextFraction(f, view.domain);
    return FRONTIER_FRACTION * (CONTEXT_WEIGHT * context + (1 - CONTEXT_WEIGHT) * (km / f) ** FRONTIER_EXPONENT);
  }
  return FRONTIER_FRACTION + (1 - FRONTIER_FRACTION) * (1 - (f / km) ** 2);
}

/** Altitude above the Earth surface (px) for a real distance under the current view. */
export function altitudePx(layout: SkyLayout, km: number, view: DistanceView): number {
  return MIN_ALTITUDE_PX + distanceFraction(km, view) * (layout.altitudeRangePx - MIN_ALTITUDE_PX);
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

export function restPosition(layout: SkyLayout, record: WorldRecord, view: DistanceView): RestPosition {
  const x = layout.cx + skyHorizontal(record.illustrative_direction) * (layout.width / 2) * HORIZONTAL_SPAN;
  const altitude = altitudePx(layout, record.encounter.miss_distance_km, view);
  return { x, y: surfaceY(layout, x) + altitude, altitude };
}

/** Horizontal position of the Moon landmark (fraction of width). Decorative: NOT a direction model. */
export const MOON_X_FRACTION = 0.86;

/**
 * Million-kilometre distance guides: one arc per 1,000,000 km across the whole domain (1M, 2M, ...,
 * up to the domain maximum). VISUAL DISTANCE GUIDES (distance from Earth), not orbits or
 * trajectories. Every 10M is a major guide, every 5M a mid guide, the rest minor.
 */
export const SCALE_STEP_KM = 1_000_000;
export type GuideTier = "major" | "mid" | "minor";

export function guideDistances(domain: DistanceDomain): number[] {
  const out: number[] = [];
  for (let km = SCALE_STEP_KM; km <= domain.maxKm; km += SCALE_STEP_KM) out.push(km);
  return out;
}

export function guideTier(km: number): GuideTier {
  if (km % (10 * SCALE_STEP_KM) === 0) return "major";
  if (km % (5 * SCALE_STEP_KM) === 0) return "mid";
  return "minor";
}

/** Revealed-distance indicator text value: floored to whole millions (to 10,000 km below 1M). */
export function frontierLabelKm(revealedKm: number): number {
  if (revealedKm >= SCALE_STEP_KM) return Math.floor(revealedKm / SCALE_STEP_KM) * SCALE_STEP_KM;
  return Math.floor(revealedKm / 10_000) * 10_000;
}
