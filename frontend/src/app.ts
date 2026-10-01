import { fetchProfile as defaultFetchProfile, fetchWorld as defaultFetchWorld, type RequestOptions } from "./api/client";
import { ApiError, isAbort } from "./api/errors";
import type { ValidatedWorld } from "./api/validateWorld";
import type { WorldRecord } from "./models/world";
import { WorldRenderer, type GLRendererLike, type ViewSnapshot } from "./renderer/WorldRenderer";
import { ProfileLoader, type ProfileFetcher } from "./state/profileLoader";
import { HashRouter } from "./state/router";
import { Store, type AppState } from "./state/store";
import { el } from "./ui/dom";
import { FocusView } from "./ui/FocusView";
import { HoverTooltip } from "./ui/HoverTooltip";
import { LabelLayer } from "./ui/LabelLayer";
import { WorldHud } from "./ui/WorldHud";
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
  diagnostics(): { activeLoops: number; inputListeners: number; storeListeners: number; keyListeners: number };
}

/**
 * Composition root. The API is the renderer's only data source; nothing here reads storage,
 * re-derives directions, or infers source facts. Selection flows only through the URL:
 *
 *   click asteroid -> router.navigate(id) -> hashchange -> select(id) -> focus + profile load
 *   Back / Esc / browser back -> "#/" -> select(null) -> profile cancelled, camera returns
 *
 * Exploration (scroll) lives entirely inside the renderer's single loop; the app only reacts to
 * its view snapshots (labels, title, callout anchoring) and never writes back into it.
 */
export function createApp(root: HTMLElement, deps: AppDeps = {}): AppHandle {
  const win = deps.win ?? window;
  const fetchWorld = deps.fetchWorld ?? defaultFetchWorld;
  const store = new Store();
  const profileLoader = new ProfileLoader(store, deps.fetchProfile ?? defaultFetchProfile);
  let recordsById = new Map<string, WorldRecord>();
  let worldController: AbortController | null = null;
  let lastView: ViewSnapshot | null = null;
  let disposed = false;
  let keyListeners = 0;

  const canvasHost = el("div", { className: "canvas-host" });
  const tooltip = new HoverTooltip();
  const labels = new LabelLayer();
  const hud = new WorldHud(() => loadWorld());
  const router = new HashRouter(win, (neowsId) => select(neowsId));
  const focusView = new FocusView(
    () => router.navigate(null),
    () => {
      const selected = store.getState().selectedId;
      if (selected) profileLoader.load(selected);
    },
  );
  root.replaceChildren(el("div", { className: "app-shell" }, [canvasHost, labels.element, hud.element, focusView.element, tooltip.element]));

  const renderer = new WorldRenderer(canvasHost, {
    createGLRenderer: deps.createGLRenderer,
    onHover: (neowsId, x, y) => {
      store.update({ hoveredId: neowsId });
      const record = neowsId ? recordsById.get(neowsId) : undefined;
      if (record && store.getState().selectedId === null) tooltip.show(record, x, y);
      else tooltip.hide();
    },
    onClick: (neowsId) => {
      if (neowsId) router.navigate(neowsId);
    },
    onViewChange: (view) => {
      lastView = view;
      hud.setProgress(view.progress, view.focus);
      labels.update(view, renderer, store.getState().hoveredId);
      const selected = store.getState().selectedId;
      focusView.setAnchor(selected ? renderer.screenPositionOf(selected) : null, view.focus, renderer.focusedScreenRadius);
    },
  });

  const onKeyDown = (event: KeyboardEvent): void => {
    if (event.key === "Escape" && store.getState().selectedId !== null) router.navigate(null);
  };
  win.addEventListener("keydown", onKeyDown);
  keyListeners++;

  function select(neowsId: string | null): void {
    if (neowsId === store.getState().selectedId) return;
    log.info("selection changed", { neowsId });
    store.update({ selectedId: neowsId });
    tooltip.hide();
    renderer.setFocus(neowsId);
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
        labels.setRecords(data.records);
        renderer.setFocus(store.getState().selectedId); // a deep-linked selection can now be focused
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
    if (state.world !== previous.world) hud.update(state.world);
    if (state.hoveredId !== previous.hoveredId) renderer.setHighlight(state.hoveredId);
    if (state.profile !== previous.profile || state.world !== previous.world) {
      focusView.update(state.profile, state.selectedId ? recordsById.get(state.selectedId) ?? null : null);
      if (lastView && state.selectedId) focusView.setAnchor(renderer.screenPositionOf(state.selectedId), lastView.focus, renderer.focusedScreenRadius);
    }
  });

  hud.update(store.getState().world);
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
      keyListeners,
    }),
    dispose(): void {
      if (disposed) return;
      disposed = true;
      worldController?.abort();
      unsubscribe();
      win.removeEventListener("keydown", onKeyDown);
      keyListeners--;
      router.dispose();
      profileLoader.dispose();
      renderer.dispose();
      hud.dispose();
      focusView.dispose();
      labels.dispose();
      tooltip.dispose();
      root.replaceChildren();
    },
  };
}
