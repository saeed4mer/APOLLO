/**
 * Hash routing. The URL is the single source of truth for which asteroid is selected:
 *
 *   #/                    the world (nothing selected)
 *   #/asteroid/<neows_id> an asteroid's profile
 *
 * A click calls navigate(); the resulting hashchange is the ONLY path that changes selection,
 * so one click yields exactly one selection, refresh restores the selection, and the browser
 * Back button returns to the world.
 */
const ASTEROID_ROUTE = /^#\/asteroid\/([1-9]\d*)$/;

export function parseRoute(hash: string): string | null {
  return ASTEROID_ROUTE.exec(hash)?.[1] ?? null;
}

export function routeFor(neowsId: string | null): string {
  return neowsId === null ? "#/" : `#/asteroid/${neowsId}`;
}

export class HashRouter {
  private readonly onHashChange = (): void => this.emit();

  constructor(private readonly win: Window, private readonly onRoute: (neowsId: string | null) => void) {
    win.addEventListener("hashchange", this.onHashChange);
  }

  /** Apply the current URL (on start-up, e.g. after a browser refresh). */
  emit(): void {
    this.onRoute(parseRoute(this.win.location.hash));
  }

  navigate(neowsId: string | null): void {
    const target = routeFor(neowsId);
    if (this.win.location.hash === target) return;
    this.win.location.hash = target; // fires hashchange -> onRoute
  }

  dispose(): void {
    this.win.removeEventListener("hashchange", this.onHashChange);
  }
}
