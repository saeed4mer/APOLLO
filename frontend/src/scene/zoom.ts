import { EARTH_VISUAL_RADIUS } from "./scale";

/**
 * Camera zoom model. The camera orientation is fixed; zoom only changes the camera's distance
 * from the origin along +Z. There is one authoritative TARGET distance (changed by input) and a
 * CURRENT distance that the single render loop eases toward the target:
 *
 *   wheel -> applyWheel() -> target changes (clamped)
 *   frame -> step(dt)     -> current approaches target, settles exactly, reports "changed"
 *
 * Nothing here schedules work or emits events, so input and animation cannot feed each other
 * (no zoom loop). Non-finite input is ignored; both distances are always finite and clamped.
 */
/**
 * Bounds, expressed as the visualization radius visible at the origin (FOV 50°, see legend):
 *   MIN 2.5  -> ~7,240 km     (just outside the Earth placeholder; the camera can never enter it)
 *   INIT 32  -> ~2.8e8 km     (frames every object out to ~1.9 AU; current max is ~7.5e7 km)
 *   MAX 45   -> ~2.9e10 km    (far beyond any NeoWs miss distance; further zoom would show nothing new)
 */
export const MIN_CAMERA_DISTANCE = EARTH_VISUAL_RADIUS * 2.5;
export const MAX_CAMERA_DISTANCE = 45;
export const INITIAL_CAMERA_DISTANCE = 32;
/** Fractional distance change per 100 normalized wheel pixels. */
export const WHEEL_ZOOM_RATE = 0.15;
/** Largest per-event wheel delta honoured (pixels), so one burst cannot jump the full range. */
export const MAX_WHEEL_DELTA = 240;
/** Easing time constant (ms); ~95% of the way in 3 tau. */
export const ZOOM_EASING_TAU_MS = 90;
const SETTLE_EPSILON = 1e-4;

export class ZoomController {
  private target = INITIAL_CAMERA_DISTANCE;
  private current = INITIAL_CAMERA_DISTANCE;

  get targetDistance(): number {
    return this.target;
  }

  get currentDistance(): number {
    return this.current;
  }

  get settled(): boolean {
    return this.current === this.target;
  }

  /** Wheel down (positive deltaY) zooms out; wheel up zooms in. deltaMode per WheelEvent. */
  applyWheel(deltaY: number, deltaMode = 0): void {
    if (!Number.isFinite(deltaY) || deltaY === 0) return;
    const pixels = deltaMode === 1 ? deltaY * 16 : deltaMode === 2 ? deltaY * 800 : deltaY;
    const bounded = Math.max(-MAX_WHEEL_DELTA, Math.min(MAX_WHEEL_DELTA, pixels));
    this.setTarget(this.target * Math.exp((bounded / 100) * WHEEL_ZOOM_RATE));
  }

  setTarget(distance: number): void {
    if (!Number.isFinite(distance)) return;
    this.target = clampDistance(distance);
  }

  /** Advance the eased distance by dtMs. Returns true if the current distance changed. */
  step(dtMs: number): boolean {
    if (this.settled) return false;
    const dt = Number.isFinite(dtMs) ? Math.max(0, Math.min(dtMs, 100)) : 0;
    const blend = 1 - Math.exp(-dt / ZOOM_EASING_TAU_MS);
    const next = this.current + (this.target - this.current) * blend;
    this.current = Math.abs(this.target - next) < SETTLE_EPSILON ? this.target : clampDistance(next);
    return true;
  }
}

export function clampDistance(distance: number): number {
  return Math.max(MIN_CAMERA_DISTANCE, Math.min(MAX_CAMERA_DISTANCE, distance));
}
