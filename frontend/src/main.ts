import { fetchWorld } from "./api/client";
import { createApp, type AppDeps } from "./app";
import { withSyntheticRecords } from "./dev/synthetic";

const root = document.getElementById("app");
if (!root) throw new Error("Missing #app root element");

const deps: AppDeps = {};
const stress = import.meta.env.DEV ? Number(new URLSearchParams(window.location.search).get("stress") ?? 0) : 0;
if (stress > 0) {
  // Development-only performance testing. Clearly labelled; never available in production builds.
  deps.fetchWorld = async (options) => withSyntheticRecords(await fetchWorld(options), Math.min(stress, 5000));
  const banner = document.createElement("div");
  banner.className = "synthetic-banner";
  banner.textContent = `SYNTHETIC STRESS DATA: ${Math.min(stress, 5000)} fake records added for performance testing. Not real asteroids.`;
  document.body.appendChild(banner);
}

const app = createApp(root, deps);

if (import.meta.env.DEV) {
  // Development-only diagnostics for the interaction torture test (e2e/torture.mjs).
  // Exposes counts, phases and positions only: no data the API does not already serve, no secrets.
  (window as unknown as Record<string, unknown>).__ASTEROID_DEBUG__ = {
    diagnostics: () => app.diagnostics(),
    worldStatus: () => app.store.getState().world.status,
    worldIds: () => {
      const world = app.store.getState().world;
      return world.status === "ready" ? world.data.records.map((r) => r.neows_id) : [];
    },
    selectedId: () => app.store.getState().selectedId,
    hoveredId: () => app.store.getState().hoveredId,
    profileStatus: () => app.store.getState().profile.status,
    screenPositionOf: (id: string) => app.renderer.screenPositionOf(id),
    phaseOf: (id: string) => app.renderer.phaseOf(id),
    restAltitudeOf: (id: string) => app.renderer.restAltitudeOf(id),
    progress: () => ({
      target: app.renderer.exploration.targetProgress,
      current: app.renderer.exploration.currentProgress,
      deepest: app.renderer.exploration.deepestProgress,
    }),
    focusProgress: () => app.renderer.focusProgress,
    focusSettled: () => app.renderer.focusSettled,
    earthCrestY: () => app.renderer.viewLayout.earthTopY,
    earthCounts: () => app.renderer.earthCounts,
    skyBackground: () => (document.querySelector(".canvas-host") as HTMLElement | null)?.style.background ?? "",
    frames: () => app.renderer.frames,
    timing: () => ({ ...app.renderer.timing }),
  };
}

// Vite hot-module replacement: dispose the old app before the new module mounts one,
// so development reloads can never stack render loops or listeners.
if (import.meta.hot) {
  import.meta.hot.dispose(() => app.dispose());
}
