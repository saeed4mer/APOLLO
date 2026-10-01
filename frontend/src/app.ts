import { fetchProfile as defaultFetchProfile, fetchWorld as defaultFetchWorld, type RequestOptions } from "./api/client";
import { ApiError, isAbort } from "./api/errors";
import type { ValidatedWorld } from "./api/validateWorld";
import type { WorldRecord } from "./models/world";
import { WorldRenderer, type GLRendererLike } from "./renderer/WorldRenderer";
import { ProfileLoader, type ProfileFetcher } from "./state/profileLoader";
import { HashRouter } from "./state/router";
import { Store, type AppState } from "./state/store";
import { el } from "./ui/dom";
import { HoverTooltip } from "./ui/HoverTooltip";
import { Legend } from "./ui/Legend";
import { ProfilePanel } from "./ui/ProfilePanel";
import { StatusOverlay } from "./ui/StatusOverlay";
import { log } from "./utils/log";

export interface AppDeps {
  fetchWorld?: (options: RequestOptions) => Promise<ValidatedWorld>;
  fetchProfile?: ProfileFetcher;
  createGLRenderer?: () => GLRendererLike;
  win?: Window;
}

export interface AppHandle {
  dispose(): void;
  readonly store: Store;
  readonly renderer: WorldRenderer;
  diagnostics(): { activeLoops: number; inputListeners: number; storeListeners: number };
}

/**
 * Composition root. The API is the renderer's only data source; nothing here reads storage,
 * re-derives directions, or infers source facts. Selection flows only through the URL:
 *
 *   click marker -> router.navigate(id) -> hashchange -> select(id) -> profile load
 *   Back / browser back -> "#/" -> select(null) -> profile cancelled, world remains
 */
export function createApp(root: HTMLElement, deps: AppDeps = {}): AppHandle {
  const win = deps.win ?? window;
  const fetchWorld = deps.fetchWorld ?? defaultFetchWorld;
  const store = new Store();
  const profileLoader = new ProfileLoader(store, deps.fetchProfile ?? defaultFetchProfile);
  let recordsById = new Map<string, WorldRecord>();
  let worldController: AbortController | null = null;
  let disposed = false;

  const canvasHost = el("div", { className: "canvas-host" });
  const tooltip = new HoverTooltip();
  const legend = new Legend();
  const overlay = new StatusOverlay(() => loadWorld());
  const router = new HashRouter(win, (neowsId) => select(neowsId));
  const panel = new ProfilePanel(
    () => router.navigate(null),
    () => {
      const selected = store.getState().selectedId;
      if (selected) profileLoader.load(selected);
    },
  );
  root.replaceChildren(el("div", { className: "app-shell" }, [canvasHost, overlay.element, legend.element, panel.element, tooltip.element]));

  const renderer = new WorldRenderer(canvasHost, {
    createGLRenderer: deps.createGLRenderer,
    onHover: (neowsId, x, y) => {
      store.update({ hoveredId: neowsId });
      const record = neowsId ? recordsById.get(neowsId) : undefined;
      if (record) tooltip.show(record, x, y);
      else tooltip.hide();
    },
    onClick: (neowsId) => {
      if (neowsId) router.navigate(neowsId);
    },
    onViewChange: (km) => legend.setVisibleRadius(km),
  });

  function select(neowsId: string | null): void {
    if (neowsId === store.getState().selectedId) return;
    log.info("selection changed", { neowsId });
    store.update({ selectedId: neowsId });
    if (neowsId) profileLoader.load(neowsId);
    else profileLoader.cancel();
  }

  function loadWorld(): void {
    worldController?.abort();
    const controller = new AbortController();
    worldController = controller;
    store.update({ world: { status: "loading" } });
    fetchWorld({ signal: controller.signal }).then(
      (data) => {
        if (disposed || controller !== worldController) return;
        recordsById = new Map(data.records.map((r) => [r.neows_id, r]));
        renderer.setRecords(data.records);
        legend.setSpatialModel(data.snapshot);
        store.update({ world: { status: "ready", data } });
      },
      (error: unknown) => {
        if (disposed || controller !== worldController || isAbort(error)) return;
        const apiError = error instanceof ApiError ? error : new ApiError("malformed", String(error));
        log.warn("world request failed", { kind: apiError.kind });
        store.update({ world: { status: "error", error: apiError } });
      },
    );
  }

  const unsubscribe = store.subscribe((state: AppState, previous: AppState) => {
    if (state.world !== previous.world) overlay.update(state.world);
    if (state.hoveredId !== previous.hoveredId || state.selectedId !== previous.selectedId) {
      renderer.setHighlight(state.hoveredId, state.selectedId);
    }
    if (state.profile !== previous.profile || state.world !== previous.world) {
      const id = state.selectedId;
      panel.update(state.profile, id ? recordsById.get(id)?.name ?? null : null);
      tooltip.place(); // the panel may now occupy space the tooltip was using
    }
  });

  overlay.update(store.getState().world);
  renderer.start();
  router.emit(); // apply the URL (e.g. after a browser refresh)
  loadWorld();

  return {
    store,
    renderer,
    diagnostics: () => ({
      activeLoops: WorldRenderer.activeLoops,
      inputListeners: renderer.inputListenerCount,
      storeListeners: store.listenerCount,
    }),
    dispose(): void {
      if (disposed) return;
      disposed = true;
      worldController?.abort();
      unsubscribe();
      router.dispose();
      profileLoader.dispose();
      renderer.dispose();
      overlay.dispose();
      panel.dispose();
      tooltip.dispose();
      legend.dispose();
      root.replaceChildren();
    },
  };
}
