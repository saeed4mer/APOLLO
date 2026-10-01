import { clamp01 } from "./skyLayout";

/**
 * The single authoritative exploration state. Scroll changes only the TARGET; the render loop
 * eases CURRENT toward it and settles exactly. Nothing here schedules work or emits events, so
 * input and animation cannot feed each other. Both values are always finite and inside [0, 1].
 *
 *   wheel -> applyWheel() -> target (clamped)
 *   frame -> step(dt)     -> current eases to target, returns whether it moved
 */
/** Progress gained per 100 normalized wheel pixels: a full journey is ~25 wheel notches. */
export const PROGRESS_PER_100PX = 0.04;
/** Largest per-event wheel delta honoured (px), so one burst cannot jump the whole journey. */
export const MAX_WHEEL_DELTA = 240;
export const PROGRESS_EASING_TAU_MS = 140;
const SETTLE_EPSILON = 1e-4;

export class ExplorationController {
  private target = 0;
  private current = 0;
  private deepest = 0;

  get targetProgress(): number {
    return this.target;
  }

  get currentProgress(): number {
    return this.current;
  }

  /** Deepest progress ever reached. Drives reveal, so scrolling back never re-hides or re-drops asteroids. */
  get deepestProgress(): number {
    return this.deepest;
  }

  get settled(): boolean {
    return this.current === this.target;
  }

  /** Wheel down (positive deltaY) goes deeper toward space; wheel up returns toward Earth. */
  applyWheel(deltaY: number, deltaMode = 0): void {
    if (!Number.isFinite(deltaY) || deltaY === 0) return;
    const pixels = deltaMode === 1 ? deltaY * 16 : deltaMode === 2 ? deltaY * 800 : deltaY;
    const bounded = Math.max(-MAX_WHEEL_DELTA, Math.min(MAX_WHEEL_DELTA, pixels));
    this.setTarget(this.target + (bounded / 100) * PROGRESS_PER_100PX);
  }

  setTarget(progress: number): void {
    if (!Number.isFinite(progress)) return;
    this.target = clamp01(progress);
  }

  step(dtMs: number): boolean {
    if (this.settled) return false;
    const dt = Number.isFinite(dtMs) ? Math.max(0, Math.min(dtMs, 100)) : 0;
    const blend = 1 - Math.exp(-dt / PROGRESS_EASING_TAU_MS);
    const next = this.current + (this.target - this.current) * blend;
    this.current = Math.abs(this.target - next) < SETTLE_EPSILON ? this.target : clamp01(next);
    this.deepest = Math.max(this.deepest, this.current);
    return true;
  }
}
