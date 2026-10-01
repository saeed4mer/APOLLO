import type { WorldRecord } from "../models/world";

/**
 * Progressive reveal and the per-asteroid animation lifecycle:
 *
 *   HIDDEN ──(deepest progress >= threshold)──▶ FALLING ──(FALL_MS)──▶ SETTLED (stays settled)
 *
 * Reveal ORDER is farthest-first (larger real miss distance first; ties by neows_id), so the
 * initial sky shows a few distant hints and nearer objects arrive as the user goes deeper.
 * Thresholds are spread evenly over [REVEAL_START, REVEAL_END]; the first few are below 0 and so
 * are revealed at load. Reveal is driven by the DEEPEST progress reached, so scrolling back up,
 * opening/closing a profile, or resizing never re-hides or re-drops an asteroid.
 */
export const REVEAL_START = -0.08;
export const REVEAL_END = 0.6;
export const FALL_MS = 1600;
/**
 * Asteroids revealed in the same moment (e.g. at load) are staggered so they do not land as one:
 * SAME_MOMENT_STAGGER_MS apart, but squeezed into at most MAX_STAGGER_WINDOW_MS in total, so a
 * population of 1,000 does not keep falling for minutes.
 */
export const SAME_MOMENT_STAGGER_MS = 140;
export const MAX_STAGGER_WINDOW_MS = 1200;

export type AsteroidPhase = "HIDDEN" | "FALLING" | "SETTLED";

export function revealOrder(records: readonly WorldRecord[]): string[] {
  return [...records]
    .sort((a, b) => b.encounter.miss_distance_km - a.encounter.miss_distance_km || (a.neows_id < b.neows_id ? -1 : 1))
    .map((r) => r.neows_id);
}

export function revealThresholds(records: readonly WorldRecord[]): Map<string, number> {
  const order = revealOrder(records);
  const span = REVEAL_END - REVEAL_START;
  return new Map(order.map((id, i) => [id, order.length === 1 ? REVEAL_START : REVEAL_START + (span * i) / (order.length - 1)]));
}

export class RevealTracker {
  private thresholds = new Map<string, number>();
  private readonly startedAt = new Map<string, number>();

  /** New population: keeps the lifecycle of asteroids that are still present (no restart). */
  setRecords(records: readonly WorldRecord[]): void {
    this.thresholds = revealThresholds(records);
    for (const id of [...this.startedAt.keys()]) if (!this.thresholds.has(id)) this.startedAt.delete(id);
  }

  /** Reveal everything whose threshold the deepest progress has passed. Returns true if any started. */
  update(deepestProgress: number, now: number): boolean {
    const due: string[] = [];
    for (const [id, threshold] of this.thresholds) {
      if (deepestProgress >= threshold && !this.startedAt.has(id)) due.push(id);
    }
    const step = due.length > 1 ? Math.min(SAME_MOMENT_STAGGER_MS, MAX_STAGGER_WINDOW_MS / (due.length - 1)) : 0;
    due.forEach((id, i) => this.startedAt.set(id, now + i * step));
    return due.length > 0;
  }

  /**
   * Show an asteroid immediately at rest (used when the user deep-links to one that the current
   * progress has not revealed yet). Has no effect on an asteroid that is already revealed.
   */
  settleNow(id: string, now: number): void {
    if (this.thresholds.has(id) && !this.startedAt.has(id)) this.startedAt.set(id, now - FALL_MS);
  }

  /** Fall completion in [0, 1], or null while hidden. */
  fallFraction(id: string, now: number): number | null {
    const started = this.startedAt.get(id);
    if (started === undefined) return null;
    return Math.min(1, Math.max(0, (now - started) / FALL_MS));
  }

  phase(id: string, now: number): AsteroidPhase {
    const f = this.fallFraction(id, now);
    return f === null ? "HIDDEN" : f >= 1 ? "SETTLED" : "FALLING";
  }

  /** True while at least one revealed asteroid is still falling (the loop must keep animating). */
  anyFalling(now: number): boolean {
    for (const id of this.startedAt.keys()) if (this.phase(id, now) === "FALLING") return true;
    return false;
  }

  threshold(id: string): number | undefined {
    return this.thresholds.get(id);
  }
}

/** Ease-out cubic: fast entry, gentle arrival at the resting position. */
export function fallEase(t: number): number {
  return 1 - (1 - t) ** 3;
}
