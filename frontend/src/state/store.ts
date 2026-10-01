import type { ApiError } from "../api/errors";
import type { ValidatedWorld } from "../api/validateWorld";
import type { AsteroidProfile } from "../models/profile";

/**
 * Application state. Every asynchronous resource is an explicit tagged state; "undefined" is
 * never used to mean "loading".
 *
 *   world:   loading -> ready | error   (error -> loading on Retry)
 *   profile: idle -> loading(id) -> ready(id) | error(id);  any -> idle when returning to the world
 *   hoveredId / selectedId: NeoWs IDs of objects in the loaded world (selectedId comes from the URL)
 */
export type WorldState =
  | { status: "loading" }
  | { status: "ready"; data: ValidatedWorld }
  | { status: "error"; error: ApiError };

export type ProfileState =
  | { status: "idle" }
  | { status: "loading"; neowsId: string }
  | { status: "ready"; neowsId: string; profile: AsteroidProfile }
  | { status: "error"; neowsId: string; error: ApiError };

export interface AppState {
  world: WorldState;
  hoveredId: string | null;
  selectedId: string | null;
  profile: ProfileState;
}

export type Listener = (state: AppState, previous: AppState) => void;

/** Maximum cascaded updates triggered from inside listeners before we declare an update loop. */
const MAX_CASCADE = 20;

export class Store {
  private state: AppState;
  private readonly listeners = new Set<Listener>();
  private notifying = false;
  private pending: AppState | null = null;

  constructor(initial?: Partial<AppState>) {
    this.state = { world: { status: "loading" }, hoveredId: null, selectedId: null, profile: { status: "idle" }, ...initial };
  }

  getState(): AppState {
    return this.state;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  }

  get listenerCount(): number {
    return this.listeners.size;
  }

  /** Shallow-merge a patch. No-op (no notification) if nothing changed by identity. */
  update(patch: Partial<AppState>): void {
    const base = this.pending ?? this.state;
    const next = { ...base, ...patch };
    if ((Object.keys(patch) as (keyof AppState)[]).every((k) => base[k] === next[k])) return;
    if (this.notifying) {
      this.pending = next; // applied after the current notification round
      return;
    }
    this.commit(next);
  }

  private commit(next: AppState): void {
    let cascade = 0;
    let candidate: AppState | null = next;
    while (candidate) {
      if (++cascade > MAX_CASCADE) throw new Error("State update loop detected");
      const previous = this.state;
      this.state = candidate;
      this.pending = null;
      this.notifying = true;
      try {
        for (const listener of [...this.listeners]) listener(this.state, previous);
      } finally {
        this.notifying = false;
      }
      candidate = this.pending;
    }
  }
}
