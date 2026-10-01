import type { UnitVector, WorldRecord } from "../models/world";

/**
 * THE DISTANCE WORLD (M7.2). Pure functions: viewport + real distances -> world geometry.
 *
 * Coordinates are CSS pixels, +Y up, in a tall VIRTUAL WORLD: the Earth sits at the bottom of the
 * world (its crest at `earthTopY`, 30% up the first screen) and every distance has a fixed height
 * above the Earth surface. The renderer's orthographic camera then TRAVELS up through that world
 * (`travelPx`), so the viewport only ever shows the local window around the distance being
 * explored. Nothing is ever compressed to fit the screen:
 *
 *   real miss_distance_km  ->  altitudePx (world height above the surface)  ->  camera window
 *
 *  - The Earth is a large-radius arc; its centre is far below the world origin at (cx, cy).
 *  - Each asteroid rests at a horizontal position taken from its served direction and at the
 *    world height of its exact miss distance, measured from the Earth surface directly below it.
 *    Distance guides follow the same curve at the same heights, so asteroids sit between them.
 */

/**
 * Real-distance domain (km), derived from the population so that no real record is ever clipped:
 *   minKm = Earth's radius (6,371 km);
 *   maxKm = the larger of 1e8 km and the next 10-million-km boundary above 1.05 x the farthest
 *           real miss distance. The journey ends at maxKm.
 */
export const DISTANCE_MIN_KM = 6371;
export const DEFAULT_MAX_KM = 1e8;
export const MOON_DISTANCE_KM = 384_400;
export const FIELD_START_KM = 1_000_000;
export const SCALE_STEP_KM = 1_000_000;

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

/** Earth crest height in the world, as a fraction of viewport height (fixed: the Earth is world geometry). */
export const EARTH_CREST_FRACTION = 0.3;
/** Arc sag across half the viewport width, as a fraction of viewport height. */
export const EARTH_SAG_FRACTION = 0.1;
/** Horizontal band used for asteroids (fraction of half-width), keeping them off the edges. */
export const HORIZONTAL_SPAN = 0.92;

/** Renderer projection of the served direction into the sky. Versioned: changing it moves every asteroid. */
export const SKY_PROJECTION = "longitude-fan-v1";

/**
 * World spacing of ONE MILLION KM (px), as a fraction of viewport height, clamped: ~120 px on a
 * 860 px screen, so a viewport shows about seven million-km levels with real separation.
 */
export const MILLION_PX_FRACTION = 0.14;
export const MIN_MILLION_PX = 80;
export const MAX_MILLION_PX = 220;
/** World height of the Moon distance above the surface (fraction of viewport height): in the first sky. */
export const MOON_HEIGHT_FRACTION = 0.42;
/** World gap from the Moon distance up to 1,000,000 km, in units of the million-km spacing. */
export const MOON_TO_FIELD_STEPS = 1.6;
/** Once travelling, the revealed frontier is held at this fraction of the viewport height. */
export const FRONTIER_SCREEN_FRACTION = 0.68;

export interface SkyLayout {
  width: number;
  height: number;
  earthTopY: number;
  sag: number;
  cx: number;
  cy: number;
  radius: number;
  /** World px per 1,000,000 km in the million-km field. */
  millionPx: number;
  /** World height (above the surface) of the Moon distance and of 1,000,000 km. */
  moonAltitude: number;
  fieldAltitude: number;
}

export const clamp01 = (t: number): number => (Number.isFinite(t) ? Math.min(1, Math.max(0, t)) : 0);

export function computeLayout(width: number, height: number): SkyLayout {
  const w = Math.max(1, width);
  const h = Math.max(1, height);
  const earthTopY = h * EARTH_CREST_FRACTION;
  const sag = h * EARTH_SAG_FRACTION;
  const halfWidth = (w / 2) * 1.08; // the arc runs slightly past the viewport edges
  const radius = (halfWidth * halfWidth + sag * sag) / (2 * sag);
  const millionPx = Math.min(MAX_MILLION_PX, Math.max(MIN_MILLION_PX, h * MILLION_PX_FRACTION));
  const moonAltitude = h * MOON_HEIGHT_FRACTION;
  return {
    width: w,
    height: h,
    earthTopY,
    sag,
    cx: w / 2,
    cy: earthTopY - radius,
    radius,
    millionPx,
    moonAltitude,
    fieldAltitude: moonAltitude + MOON_TO_FIELD_STEPS * millionPx,
  };
}

/** Height of the Earth surface (world px) at x. */
export function surfaceY(layout: SkyLayout, x: number): number {
  const dx = Math.min(Math.abs(x - layout.cx), layout.radius);
  return layout.cy + Math.sqrt(layout.radius * layout.radius - dx * dx);
}

/**
 * DISTANCE-WORLD MAPPING: exact distance (km) -> world height above the Earth surface (px).
 * Strictly increasing for every km > 0 (so a nearer asteroid is ALWAYS lower), and the same for
 * asteroids, the Moon and the guides:
 *
 *   0 < km <= 384,400        moonAltitude * ln(1 + km/6,371) / ln(1 + 384,400/6,371)   (the sky)
 *   384,400 < km <= 1M       linear from moonAltitude to fieldAltitude                 (past the Moon)
 *   km > 1M                  fieldAltitude + millionPx * (km - 1M) / 1M               (the field)
 *
 * In the million-km field the mapping is LINEAR: every million km has the same spacing, and an
 * asteroid at 47,382,615 km sits exactly 38.2615% of the way from the 47M to the 48M guide.
 * The exact source value is used; nothing is rounded.
 */
export function altitudePx(layout: SkyLayout, km: number): number {
  if (!Number.isFinite(km) || km <= 0) throw new RangeError(`distance must be a positive finite number, got ${km}`);
  if (km <= MOON_DISTANCE_KM) return (layout.moonAltitude * Math.log1p(km / DISTANCE_MIN_KM)) / Math.log1p(MOON_DISTANCE_KM / DISTANCE_MIN_KM);
  if (km <= FIELD_START_KM) {
    return layout.moonAltitude + ((layout.fieldAltitude - layout.moonAltitude) * (km - MOON_DISTANCE_KM)) / (FIELD_START_KM - MOON_DISTANCE_KM);
  }
  return layout.fieldAltitude + (layout.millionPx * (km - FIELD_START_KM)) / SCALE_STEP_KM;
}

/** Inverse of altitudePx (world height above the surface -> km); 0 at or below the surface. */
export function distanceAtAltitude(layout: SkyLayout, altitude: number): number {
  if (!(altitude > 0)) return 0;
  if (altitude <= layout.moonAltitude) return DISTANCE_MIN_KM * Math.expm1((altitude / layout.moonAltitude) * Math.log1p(MOON_DISTANCE_KM / DISTANCE_MIN_KM));
  if (altitude <= layout.fieldAltitude) {
    return MOON_DISTANCE_KM + ((altitude - layout.moonAltitude) / (layout.fieldAltitude - layout.moonAltitude)) * (FIELD_START_KM - MOON_DISTANCE_KM);
  }
  return FIELD_START_KM + ((altitude - layout.fieldAltitude) / layout.millionPx) * SCALE_STEP_KM;
}

/**
 * CAMERA TRAVEL (world px the viewport has moved up from the starting composition), driven only by
 * the revealed distance: the camera stays on the Earth until the frontier would rise above
 * FRONTIER_SCREEN_FRACTION of the screen, then follows it, holding the frontier at that height.
 * Deterministic, continuous, monotonic in the revealed distance, and fully reversible: scrolling
 * back lowers the camera until the Earth is back at the bottom of the screen.
 */
export function travelPx(layout: SkyLayout, revealedKm: number): number {
  if (!(revealedKm > 0)) return 0;
  return Math.max(0, layout.earthTopY + altitudePx(layout, revealedKm) - FRONTIER_SCREEN_FRACTION * layout.height);
}

/**
 * EXPLORATION JOURNEY: progress in [0, 1] -> revealed distance (km), in three documented stages,
 * each strictly increasing and continuous at the joins (deterministic, monotonic, finite, bounded):
 *
 *   p = 0                       0 km (daytime sky: no Moon, no guides, no asteroids)
 *   0 < p <= 0.16               log  6,371 -> 384,400 km     (leaving the atmosphere; sky -> night)
 *   0.16 < p <= 0.22            log  384,400 -> 1,000,000 km (the Moon is passed)
 *   0.22 < p <= 1               LINEAR 1M -> maxKm           (the million-km field at constant speed:
 *                               ~0.8M km per 100 px wheel notch for a 100M km domain)
 */
export const MOON_PROGRESS = 0.16;
export const FIELD_PROGRESS = 0.22;

export function revealedDistanceKm(progress: number, domain: DistanceDomain): number {
  const p = clamp01(progress);
  if (p === 0) return 0;
  if (p <= MOON_PROGRESS) return domain.minKm * (MOON_DISTANCE_KM / domain.minKm) ** (p / MOON_PROGRESS);
  if (p <= FIELD_PROGRESS) {
    return MOON_DISTANCE_KM * (FIELD_START_KM / MOON_DISTANCE_KM) ** ((p - MOON_PROGRESS) / (FIELD_PROGRESS - MOON_PROGRESS));
  }
  const t = (p - FIELD_PROGRESS) / (1 - FIELD_PROGRESS);
  return Math.min(domain.maxKm, FIELD_START_KM + (domain.maxKm - FIELD_START_KM) * t);
}

/** The exploration progress at which a real distance is first revealed (inverse of the journey). */
export function progressForDistance(km: number, domain: DistanceDomain): number {
  if (!Number.isFinite(km) || km <= domain.minKm) return 0;
  if (km <= MOON_DISTANCE_KM) return (MOON_PROGRESS * Math.log(km / domain.minKm)) / Math.log(MOON_DISTANCE_KM / domain.minKm);
  if (km <= FIELD_START_KM) {
    return MOON_PROGRESS + ((FIELD_PROGRESS - MOON_PROGRESS) * Math.log(km / MOON_DISTANCE_KM)) / Math.log(FIELD_START_KM / MOON_DISTANCE_KM);
  }
  const t = (Math.min(km, domain.maxKm) - FIELD_START_KM) / (domain.maxKm - FIELD_START_KM);
  return FIELD_PROGRESS + t * (1 - FIELD_PROGRESS);
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

/** World position of an asteroid at rest: served direction + exact miss distance. Static per viewport. */
export function restPosition(layout: SkyLayout, record: WorldRecord): RestPosition {
  const x = layout.cx + skyHorizontal(record.illustrative_direction) * (layout.width / 2) * HORIZONTAL_SPAN;
  const altitude = altitudePx(layout, record.encounter.miss_distance_km);
  return { x, y: surfaceY(layout, x) + altitude, altitude };
}

/** Horizontal position of the Moon landmark (fraction of width). Decorative: NOT a direction model. */
export const MOON_X_FRACTION = 0.86;

/**
 * Million-kilometre distance guides: one arc per 1,000,000 km across the whole domain (1M, 2M, ...,
 * up to the domain maximum). VISUAL DISTANCE GUIDES (distance from Earth), not orbits or
 * trajectories. Every 10M is a major guide, every 5M a mid guide, the rest minor. Only the guides
 * inside the current viewport window are drawn.
 */
export type GuideTier = "major" | "mid" | "minor";

export function guideDistances(domain: DistanceDomain): number[] {
  const out: number[] = [];
  for (let km = SCALE_STEP_KM; km <= domain.maxKm; km += SCALE_STEP_KM) out.push(km);
  return out;
}

/** The million-km guides whose world height lies in [lowAltitude, highAltitude] (the local window). */
export function guidesInWindow(layout: SkyLayout, domain: DistanceDomain, lowAltitude: number, highAltitude: number): number[] {
  const first = Math.max(1, Math.ceil(distanceAtAltitude(layout, lowAltitude) / SCALE_STEP_KM));
  const last = Math.min(Math.floor(domain.maxKm / SCALE_STEP_KM), Math.floor(distanceAtAltitude(layout, highAltitude) / SCALE_STEP_KM));
  const out: number[] = [];
  for (let m = first; m <= last; m++) out.push(m * SCALE_STEP_KM);
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
