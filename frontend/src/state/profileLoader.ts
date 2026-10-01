import { ApiError, isAbort } from "../api/errors";
import type { AsteroidProfile } from "../models/profile";
import { log } from "../utils/log";
import type { Store } from "./store";

export type ProfileFetcher = (neowsId: string, options: { signal: AbortSignal }) => Promise<AsteroidProfile>;

/**
 * Loads one profile at a time. Stale responses can never be applied:
 *  1. starting a new load aborts the previous request, and
 *  2. a response is applied only if its sequence token is still current AND its neows_id is
 *     still the selected asteroid at the moment the response arrives.
 */
export class ProfileLoader {
  private sequence = 0;
  private controller: AbortController | null = null;

  constructor(private readonly store: Store, private readonly fetchProfile: ProfileFetcher) {}

  load(neowsId: string): void {
    this.controller?.abort();
    const controller = new AbortController();
    this.controller = controller;
    const token = ++this.sequence;
    this.store.update({ profile: { status: "loading", neowsId } });

    this.fetchProfile(neowsId, { signal: controller.signal }).then(
      (profile) => {
        if (!this.isCurrent(token, neowsId)) return log.info("stale profile response discarded", { neowsId });
        this.store.update({ profile: { status: "ready", neowsId, profile } });
      },
      (error: unknown) => {
        if (isAbort(error) || !this.isCurrent(token, neowsId)) return;
        const apiError = error instanceof ApiError ? error : new ApiError("malformed", String(error));
        this.store.update({ profile: { status: "error", neowsId, error: apiError } });
      },
    );
  }

  /** Abandon any in-flight request (e.g. returning to the world) and reset to idle. */
  cancel(): void {
    this.controller?.abort();
    this.controller = null;
    this.sequence++;
    this.store.update({ profile: { status: "idle" } });
  }

  dispose(): void {
    this.controller?.abort();
    this.controller = null;
    this.sequence++;
  }

  private isCurrent(token: number, neowsId: string): boolean {
    return token === this.sequence && this.store.getState().selectedId === neowsId;
  }
}
