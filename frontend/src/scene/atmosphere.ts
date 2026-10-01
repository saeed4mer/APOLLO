import { FIELD_PROGRESS, MOON_PROGRESS, SCALE_STEP_KM, type GuideTier } from "./skyLayout";

/**
 * Sky -> space appearance as a CONTINUOUS function of exploration progress (no threshold
 * switches): colour stops are interpolated per channel, and every opacity uses smoothstep.
 * The stops are timed to the journey stages in skyLayout (the Moon is reached at MOON_PROGRESS):
 *
 *   0.00 bright day sky · 0.05 deeper blue · 0.115 twilight · 0.17 night (Moon at 0.16) · 0.32 space · 1.00 deep space
 */
interface Stop {
  at: number;
  zenith: string;
  horizon: string;
}

export const SKY_STOPS: readonly Stop[] = [
  { at: 0.0, zenith: "#5aa2e6", horizon: "#cfe9ff" },
  { at: 0.05, zenith: "#2f6db8", horizon: "#9cc7ef" },
  { at: 0.115, zenith: "#1a3a78", horizon: "#e2a272" },
  { at: 0.17, zenith: "#0e1d45", horizon: "#8a6a80" },
  { at: 0.32, zenith: "#0a1530", horizon: "#1b2c55" },
  { at: 1.0, zenith: "#02040a", horizon: "#0b1428" },
];

const channel = (hex: string, i: number): number => parseInt(hex.slice(1 + i * 2, 3 + i * 2), 16);
const mix = (a: string, b: string, t: number): string =>
  `#${[0, 1, 2].map((i) => Math.round(channel(a, i) + (channel(b, i) - channel(a, i)) * t).toString(16).padStart(2, "0")).join("")}`;

export function smoothstep(edge0: number, edge1: number, x: number): number {
  const t = Math.min(1, Math.max(0, (x - edge0) / (edge1 - edge0)));
  return t * t * (3 - 2 * t);
}

export function skyColors(progress: number): { zenith: string; horizon: string } {
  const p = Number.isFinite(progress) ? Math.min(1, Math.max(0, progress)) : 0;
  for (let i = 1; i < SKY_STOPS.length; i++) {
    const a = SKY_STOPS[i - 1]!;
    const b = SKY_STOPS[i]!;
    if (p <= b.at) {
      const t = (p - a.at) / (b.at - a.at);
      return { zenith: mix(a.zenith, b.zenith, t), horizon: mix(a.horizon, b.horizon, t) };
    }
  }
  const last = SKY_STOPS[SKY_STOPS.length - 1]!;
  return { zenith: last.zenith, horizon: last.horizon };
}

/** Decorative stars appear with the twilight and dominate by space. */
export const starOpacity = (p: number): number => smoothstep(0.09, 0.22, p);
/** Low-atmosphere haze over the horizon fades as the user leaves the atmosphere. */
export const hazeOpacity = (p: number): number => 0.3 * (1 - smoothstep(0.08, 0.2, p));
/**
 * The Moon landmark is revealed when the frontier REACHES the Moon distance (progress
 * MOON_PROGRESS, during the night transition), fading in over a short window after it.
 * Hidden at load and in the daytime sky.
 */
export const MOON_FADE_PROGRESS = 0.015;
export const moonOpacity = (p: number): number => smoothstep(MOON_PROGRESS, MOON_PROGRESS + MOON_FADE_PROGRESS, p);
/** The revealed-distance frontier line and its label, once the user has started travelling. */
export const frontierOpacity = (p: number): number => smoothstep(0.015, 0.06, p);
/** The million-km distance field fades in as the Moon is passed, fully present from 1M km. */
export const fieldOpacity = (p: number): number => smoothstep(MOON_PROGRESS + 0.005, FIELD_PROGRESS, p);
/** Asteroid name labels, then names plus miss distance (only for asteroids at rest). */
export const labelOpacity = (p: number): number => smoothstep(0.215, 0.23, p);
export const labelDetailOpacity = (p: number): number => smoothstep(0.22, 0.235, p);
/** Opening title and "scroll to explore" hint. */
export const introOpacity = (p: number): number => 1 - smoothstep(0.01, 0.05, p);
/** Most labels shown at once (nearest first), so large populations never become a dashboard. */
export const MAX_LABELS = 24;

/**
 * Opacity of one million-km distance guide (before the global field fade). Visual hierarchy:
 *   - base by tier: every 10M (major) > every 5M (mid) > every 1M (minor);
 *   - emphasis on the distance currently being explored: the arc just behind the frontier is the
 *     strongest, falling off over ~1.5M km (sized so it always beats a major marker further back);
 *   - unrevealed arcs ahead of the frontier are faint and fade out over ~2-3M km;
 *   - level of detail: a guide whose on-screen gap to its neighbours is only a few pixels fades
 *     (minor first). In the distance world the gap is a constant ~120 px, so this only matters
 *     on tiny viewports. Majors never fade.
 */
export const GUIDE_BASE: Record<GuideTier, number> = { major: 0.2, mid: 0.13, minor: 0.09 };
export const GUIDE_EMPHASIS = 0.5;
export const GUIDE_FALLOFF_KM = 1.5 * SCALE_STEP_KM;
export const GUIDE_AHEAD_KM = 0.8 * SCALE_STEP_KM;

export function guideOpacity(km: number, tier: GuideTier, frontierKm: number, spacingPx: number): number {
  const lod = tier === "major" ? 1 : tier === "mid" ? smoothstep(2, 5, spacingPx) : smoothstep(3, 9, spacingPx);
  const base = GUIDE_BASE[tier] * lod;
  if (km > frontierKm) return (base + GUIDE_EMPHASIS * 0.5) * Math.exp(-(km - frontierKm) / GUIDE_AHEAD_KM) * lod;
  return Math.min(0.75, base + GUIDE_EMPHASIS * Math.exp(-(frontierKm - km) / GUIDE_FALLOFF_KM));
}
