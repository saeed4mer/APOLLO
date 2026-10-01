/**
 * Owns every pointer/wheel listener on the canvas and removes all of them on dispose().
 *
 * Click detection uses pointerdown + pointerup on the same pointer (primary button, moved less
 * than CLICK_MAX_MOVE_PX, within CLICK_MAX_MS). There is no separate "click" listener, so one
 * physical click yields at most one onClick, and a drag never selects.
 */
export const CLICK_MAX_MOVE_PX = 6;
export const CLICK_MAX_MS = 700;

export interface InputHandlers {
  onWheel(deltaY: number, deltaMode: number): void;
  onPointerMove(clientX: number, clientY: number): void;
  onPointerLeave(): void;
  onClick(clientX: number, clientY: number): void;
}

type Registration = [type: string, handler: EventListener, options?: AddEventListenerOptions];

export class InputController {
  private readonly registrations: Registration[] = [];
  private down: { pointerId: number; x: number; y: number; time: number } | null = null;

  constructor(private readonly target: HTMLElement, handlers: InputHandlers, private readonly now: () => number = () => performance.now()) {
    this.listen("wheel", (event) => {
      const wheel = event as WheelEvent;
      wheel.preventDefault(); // the page must not scroll; zoom is the only wheel behaviour
      handlers.onWheel(wheel.deltaY, wheel.deltaMode);
    }, { passive: false });
    this.listen("pointermove", (event) => {
      const p = event as PointerEvent;
      handlers.onPointerMove(p.clientX, p.clientY);
    });
    this.listen("pointerleave", () => {
      this.down = null;
      handlers.onPointerLeave();
    });
    this.listen("pointerdown", (event) => {
      const p = event as PointerEvent;
      if (p.button !== 0) return;
      this.down = { pointerId: p.pointerId, x: p.clientX, y: p.clientY, time: this.now() };
    });
    this.listen("pointerup", (event) => {
      const p = event as PointerEvent;
      const down = this.down;
      this.down = null;
      if (!down || p.button !== 0 || p.pointerId !== down.pointerId) return;
      const moved = Math.hypot(p.clientX - down.x, p.clientY - down.y);
      if (moved <= CLICK_MAX_MOVE_PX && this.now() - down.time <= CLICK_MAX_MS) handlers.onClick(p.clientX, p.clientY);
    });
    this.listen("pointercancel", () => {
      this.down = null;
    });
  }

  get listenerCount(): number {
    return this.registrations.length;
  }

  dispose(): void {
    for (const [type, handler, options] of this.registrations) this.target.removeEventListener(type, handler, options);
    this.registrations.length = 0;
    this.down = null;
  }

  private listen(type: string, handler: EventListener, options?: AddEventListenerOptions): void {
    this.target.addEventListener(type, handler, options);
    this.registrations.push([type, handler, options]);
  }
}
