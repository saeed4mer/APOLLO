/**
 * Sky -> space appearance as a CONTINUOUS function of exploration progress (no threshold
 * switches): colour stops are interpolated per channel, and every opacity uses smoothstep.
 *
 *   0.00 sky · 0.25 upper atmosphere · 0.45 twilight · 0.70 space · 1.00 deep space
 */
interface Stop {
  at: number;
  zenith: string;
  horizon: string;
}

export const SKY_STOPS: readonly Stop[] = [
  { at: 0.0, zenith: "#5aa2e6", horizon: "#cfe9ff" },
  { at: 0.25, zenith: "#2f6db8", horizon: "#9cc7ef" },
  { at: 0.45, zenith: "#1a3a78", horizon: "#e2a272" },
  { at: 0.7, zenith: "#0a1530", horizon: "#253b6e" },
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

/** Decorative stars fade in as the sky darkens. */
export const starOpacity = (p: number): number => smoothstep(0.4, 0.9, p);
/** The 1M-km distance ruler fades in once the frontier passes ~1M km. */
export const rulerOpacity = (p: number): number => 0.75 * smoothstep(0.42, 0.5, p);
/** Asteroid name labels, then names plus miss distance (only for asteroids at rest). */
export const labelOpacity = (p: number): number => smoothstep(0.45, 0.55, p);
export const labelDetailOpacity = (p: number): number => smoothstep(0.62, 0.7, p);
/** Opening title and "scroll to explore" hint. */
export const introOpacity = (p: number): number => 1 - smoothstep(0.04, 0.14, p);
/** Most labels shown at once (nearest first), so large populations never become a dashboard. */
export const MAX_LABELS = 24;
