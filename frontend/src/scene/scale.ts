/**
 * Distance scale: real NeoWs miss distance (km) -> visual radius (scene units).
 *
 *   visualRadius(d) = EARTH_VISUAL_RADIUS + UNITS_PER_DECADE * log10(d / EARTH_RADIUS_KM)
 *
 * Why logarithmic: close approaches span from thousands of km to ~1e8 km; a linear scale would
 * collapse nearly every object onto Earth or push the rest out of view.
 *
 * Guarantees:
 * - Strictly increasing in d (above the clamp below), so relative near/far ORDER is preserved. Equal visual spacing
 *   means equal distance RATIOS, not equal kilometres; the UI labels this as a visualization scale.
 * - The constants are fixed, not fitted to the current dataset, so an asteroid's radius never
 *   changes when other asteroids are added or removed.
 * - d = EARTH_RADIUS_KM lands exactly on the Earth placeholder surface.
 * - Below ~2,957 km (deep inside Earth, i.e. an impact trajectory) the formula would go negative
 *   and flip the marker through the origin; it is clamped to the origin instead. That is the only
 *   range where ordering is not strict, and it cannot occur for a genuine miss.
 */
export const EARTH_RADIUS_KM = 6371;
export const EARTH_VISUAL_RADIUS = 1;
export const UNITS_PER_DECADE = 3;

export function visualRadius(missDistanceKm: number): number {
  if (!Number.isFinite(missDistanceKm) || missDistanceKm <= 0) {
    throw new RangeError(`miss distance must be a positive finite number, got ${missDistanceKm}`);
  }
  return Math.max(0, EARTH_VISUAL_RADIUS + UNITS_PER_DECADE * Math.log10(missDistanceKm / EARTH_RADIUS_KM));
}

/** Inverse of visualRadius: the real distance (km) represented by a visual radius. */
export function distanceKmAtRadius(radius: number): number {
  return EARTH_RADIUS_KM * 10 ** ((radius - EARTH_VISUAL_RADIUS) / UNITS_PER_DECADE);
}
