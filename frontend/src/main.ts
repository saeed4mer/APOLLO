import { createApp } from "./app";
import { MAX_CAMERA_DISTANCE, MIN_CAMERA_DISTANCE } from "./scene/zoom";

const root = document.getElementById("app");
if (!root) throw new Error("Missing #app root element");

const app = createApp(root);

if (import.meta.env.DEV) {
  // Development-only diagnostics for the interaction torture test (e2e/torture.mjs).
  // Exposes counts and marker positions only: no data the API does not already serve, no secrets.
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
    zoom: () => ({
      target: app.renderer.zoom.targetDistance,
      current: app.renderer.zoom.currentDistance,
      min: MIN_CAMERA_DISTANCE,
      max: MAX_CAMERA_DISTANCE,
    }),
  };
}

// Vite hot-module replacement: dispose the old app before the new module mounts one,
// so development reloads can never stack render loops or listeners.
if (import.meta.hot) {
  import.meta.hot.dispose(() => app.dispose());
}
