import type { WorldRecord } from "../models/world";
import { isRevealed } from "./skyLayout";

/**
 * Distance-driven, REVERSIBLE reveal lifecycle. Each asteroid has exactly one animation value
 * `fall` in [0, 1] (0 = hidden above the sky, 1 = at rest), advanced by frame time inside the
 * single render loop — no timers, no per-asteroid loops, no duplicate objects:
 *
 *   eligible = miss_distance_km <= revealedDistanceKm        (exact source value)
 *   eligible:     fall -> 1 over FALL_MS      HIDDEN -> FALLING -> SETTLED
 *   not eligible: fall -> 0 over RETREAT_MS   SETTLED -> RETREATING -> HIDDEN
 *
 * Reveal order is therefore CLOSEST FIRST by construction (a smaller distance is reached first).
 * Reversing mid-animation continues from the current value, so rapid up/down input never jumps
 * or stacks. The focused asteroid may be pinned visible while it is the subject.
 */
export const FALL_MS = 1600;
export const RETREAT_MS = 700;

export type AsteroidPhase = "HIDDEN" | "FALLING" | "SETTLED" | "RETREATING";

/** Closest-first order (ties by neows_id): the order in which a forward scroll reveals asteroids. */
export function revealOrder(records: readonly WorldRecord[]): string[] {
  return [...records]
    .sort((a, b) => a.encounter.miss_distance_km - b.encounter.miss_distance_km || (a.neows_id < b.neows_id ? -1 : 1))
    .map((r) => r.neows_id);
}

export class RevealAnimator {
  private readonly fall = new Map<string, number>();
  private readonly eligible = new Map<string, boolean>();

  /** New population: asteroids still present keep their animation value (no restart, no copies). */
  setRecords(records: readonly WorldRecord[]): void {
    const ids = new Set(records.map((r) => r.neows_id));
    for (const id of [...this.fall.keys()]) if (!ids.has(id)) this.fall.delete(id);
    for (const id of [...this.eligible.keys()]) if (!ids.has(id)) this.eligible.delete(id);
    for (const id of ids) if (!this.fall.has(id)) this.fall.set(id, 0);
  }

  /** Advance every asteroid toward its target. Returns true if anything moved this frame. */
  update(records: readonly WorldRecord[], revealedKm: number, dtMs: number, pinnedId: string | null = null): boolean {
    const dt = Number.isFinite(dtMs) ? Math.max(0, Math.min(dtMs, 100)) : 0;
    let moved = false;
    for (const record of records) {
      const id = record.neows_id;
      const target = isRevealed(record.encounter.miss_distance_km, revealedKm) || id === pinnedId;
      this.eligible.set(id, target);
      const f = this.fall.get(id) ?? 0;
      const next = target ? Math.min(1, f + dt / FALL_MS) : Math.max(0, f - dt / RETREAT_MS);
      if (next !== f) {
        this.fall.set(id, next);
        moved = true;
      }
    }
    return moved;
  }

  /** Animation value in [0, 1] (0 hidden, 1 at rest). */
  fraction(id: string): number {
    return this.fall.get(id) ?? 0;
  }

  phase(id: string): AsteroidPhase {
    const f = this.fraction(id);
    const target = this.eligible.get(id) ?? false;
    if (target) return f >= 1 ? "SETTLED" : "FALLING";
    return f <= 0 ? "HIDDEN" : "RETREATING";
  }

  isApproaching(id: string): boolean {
    return (this.eligible.get(id) ?? false) && this.fraction(id) < 1;
  }
}

/** Ease-out cubic: fast entry, gentle arrival at the resting position (retreat runs it backwards). */
export function fallEase(t: number): number {
  return 1 - (1 - t) ** 3;
}
