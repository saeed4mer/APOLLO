import type { GLRendererLike } from "../src/renderer/WorldRenderer";
import profile3548666 from "./fixtures/profile_3548666.json";
import profile3830890 from "./fixtures/profile_3830890.json";
import profile404 from "./fixtures/profile_404.json";
import world from "./fixtures/world.json";

const FIXTURES = {
  "world.json": world,
  "profile_3548666.json": profile3548666,
  "profile_3830890.json": profile3830890,
  "profile_404.json": profile404,
} as const;

/** Real API responses captured from the serving layer (see test/fixtures/README.md). Deep-copied per call. */
export function fixture<T = any>(name: keyof typeof FIXTURES): T {
  return structuredClone(FIXTURES[name]) as T;
}

/** A WebGL stand-in: jsdom has no WebGL, and lifecycle tests only need the contract. */
export class FakeGL implements GLRendererLike {
  static live = 0;
  readonly domElement = document.createElement("canvas");
  renders = 0;
  disposed = false;
  constructor() {
    FakeGL.live++;
  }
  setPixelRatio(): void {}
  setSize(): void {}
  setClearColor(): void {}
  render(): void {
    this.renders++;
  }
  dispose(): void {
    if (!this.disposed) FakeGL.live--;
    this.disposed = true;
  }
}

/** Deterministic requestAnimationFrame that counts pending callbacks (= live loops). */
export function installFakeRaf() {
  let nextId = 1;
  let now = 0;
  const pending = new Map<number, FrameRequestCallback>();
  globalThis.requestAnimationFrame = (cb: FrameRequestCallback) => {
    const id = nextId++;
    pending.set(id, cb);
    return id;
  };
  globalThis.cancelAnimationFrame = (id: number) => {
    pending.delete(id);
  };
  return {
    get pending(): number {
      return pending.size;
    },
    /** Run one frame for every pending callback. */
    frame(dtMs = 16): void {
      now += dtMs;
      const callbacks = [...pending.entries()];
      pending.clear();
      for (const [, cb] of callbacks) cb(now);
    },
  };
}

/** ResizeObserver stand-in that counts observers that are still connected. */
export class FakeResizeObserver {
  static active = 0;
  static instances: FakeResizeObserver[] = [];
  private connected = false;
  constructor(private readonly callback: ResizeObserverCallback) {
    FakeResizeObserver.instances.push(this);
  }
  observe(): void {
    if (!this.connected) FakeResizeObserver.active++;
    this.connected = true;
  }
  unobserve(): void {}
  disconnect(): void {
    if (this.connected) FakeResizeObserver.active--;
    this.connected = false;
  }
  trigger(): void {
    this.callback([], this as unknown as ResizeObserver);
  }
}

export function installFakeResizeObserver(): void {
  FakeResizeObserver.active = 0;
  FakeResizeObserver.instances = [];
  globalThis.ResizeObserver = FakeResizeObserver as unknown as typeof ResizeObserver;
}

/** Count add/remove of event listeners on a target to prove cleanup. */
export function trackListeners(target: EventTarget) {
  const live = new Map<string, number>();
  const add = target.addEventListener.bind(target);
  const remove = target.removeEventListener.bind(target);
  target.addEventListener = ((type: string, listener: EventListenerOrEventListenerObject, options?: boolean | AddEventListenerOptions) => {
    live.set(type, (live.get(type) ?? 0) + 1);
    add(type, listener, options);
  }) as typeof target.addEventListener;
  target.removeEventListener = ((type: string, listener: EventListenerOrEventListenerObject, options?: boolean | EventListenerOptions) => {
    live.set(type, (live.get(type) ?? 0) - 1);
    remove(type, listener, options);
  }) as typeof target.removeEventListener;
  return {
    count(type: string): number {
      return live.get(type) ?? 0;
    },
    total(): number {
      return [...live.values()].reduce((a, b) => a + b, 0);
    },
  };
}

export function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

export const flushPromises = () => new Promise<void>((resolve) => setTimeout(resolve, 0));
