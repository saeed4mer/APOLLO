import { ApiError } from "../src/api/errors";
import { CLICK_MAX_MOVE_PX, InputController } from "../src/interaction/InputController";
import type { AsteroidProfile } from "../src/models/profile";
import { ProfileLoader } from "../src/state/profileLoader";
import { HashRouter, parseRoute, routeFor } from "../src/state/router";
import { Store } from "../src/state/store";
import { deferred, flushPromises } from "./helpers";

const profileFor = (id: string) => ({ neows_id: id }) as unknown as AsteroidProfile;

describe("Store", () => {
  it("does not notify when nothing changed", () => {
    const store = new Store();
    const listener = vi.fn();
    store.subscribe(listener);
    store.update({ hoveredId: null });
    expect(listener).not.toHaveBeenCalled();
  });

  it("detects a listener-driven update loop instead of spinning forever", () => {
    const store = new Store();
    let n = 0;
    store.subscribe(() => store.update({ hoveredId: String(++n) }));
    expect(() => store.update({ hoveredId: "start" })).toThrow(/update loop/);
  });

  it("unsubscribe removes the listener", () => {
    const store = new Store();
    const off = store.subscribe(() => undefined);
    expect(store.listenerCount).toBe(1);
    off();
    expect(store.listenerCount).toBe(0);
  });
});

describe("ProfileLoader — stale responses can never win", () => {
  function setup() {
    const store = new Store();
    const requests = new Map<string, ReturnType<typeof deferred<AsteroidProfile>>>();
    const signals = new Map<string, AbortSignal>();
    const loader = new ProfileLoader(store, (id, { signal }) => {
      const d = deferred<AsteroidProfile>();
      requests.set(id, d);
      signals.set(id, signal);
      return d.promise;
    });
    const select = (id: string) => {
      store.update({ selectedId: id });
      loader.load(id);
    };
    return { store, loader, requests, signals, select };
  }

  it("A selected, B selected, A answers late: B stays", async () => {
    const { store, requests, signals, select } = setup();
    select("A");
    select("B");
    expect(signals.get("A")!.aborted).toBe(true);
    requests.get("B")!.resolve(profileFor("B"));
    await flushPromises();
    requests.get("A")!.resolve(profileFor("A"));
    await flushPromises();
    const state = store.getState();
    expect(state.selectedId).toBe("B");
    expect(state.profile).toMatchObject({ status: "ready", neowsId: "B" });
  });

  it("A's late failure does not replace B's success", async () => {
    const { store, requests, select } = setup();
    select("A");
    select("B");
    requests.get("B")!.resolve(profileFor("B"));
    requests.get("A")!.reject(new ApiError("server", "boom"));
    await flushPromises();
    expect(store.getState().profile).toMatchObject({ status: "ready", neowsId: "B" });
  });

  it("returning to the world discards an in-flight response", async () => {
    const { store, loader, requests, signals, select } = setup();
    select("A");
    store.update({ selectedId: null });
    loader.cancel();
    expect(signals.get("A")!.aborted).toBe(true);
    requests.get("A")!.resolve(profileFor("A"));
    await flushPromises();
    expect(store.getState().profile).toEqual({ status: "idle" });
  });

  it("reports loading with the requested ID before the response arrives", () => {
    const { store, select } = setup();
    select("A");
    expect(store.getState().profile).toEqual({ status: "loading", neowsId: "A" });
  });
});

describe("router", () => {
  it("parses only well-formed NeoWs routes", () => {
    expect(parseRoute("#/asteroid/3548666")).toBe("3548666");
    expect(parseRoute("#/asteroid/0")).toBeNull();
    expect(parseRoute("#/asteroid/abc")).toBeNull();
    expect(parseRoute("#/")).toBeNull();
    expect(routeFor(null)).toBe("#/");
  });

  it("navigate() produces exactly one route event per change, and none for a repeat", async () => {
    window.location.hash = "#/";
    await flushPromises();
    const onRoute = vi.fn();
    const router = new HashRouter(window, onRoute);
    router.navigate("3548666");
    await flushPromises();
    router.navigate("3548666"); // already there: no change
    await flushPromises();
    expect(onRoute).toHaveBeenCalledTimes(1);
    expect(onRoute).toHaveBeenLastCalledWith("3548666");
    router.dispose();
  });
});

describe("InputController — one click, one selection", () => {
  function setup() {
    const target = document.createElement("div");
    const onClick = vi.fn();
    let now = 0;
    const input = new InputController(target, {
      onWheel: vi.fn(), onPointerMove: vi.fn(), onPointerLeave: vi.fn(), onClick,
    }, () => now);
    const pointer = (type: string, x: number, y: number, button = 0, pointerId = 1) =>
      target.dispatchEvent(Object.assign(new MouseEvent(type, { clientX: x, clientY: y, button }), { pointerId }));
    return { input, onClick, pointer, advance: (ms: number) => (now += ms) };
  }

  it("a press + release fires exactly one click (no extra 'click' handling)", () => {
    const { onClick, pointer } = setup();
    pointer("pointerdown", 10, 10);
    pointer("pointerup", 11, 10);
    pointer("click", 11, 10);
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("a drag or a slow press is not a click", () => {
    const { onClick, pointer, advance } = setup();
    pointer("pointerdown", 10, 10);
    pointer("pointerup", 10 + CLICK_MAX_MOVE_PX + 5, 10);
    pointer("pointerdown", 10, 10);
    advance(2000);
    pointer("pointerup", 10, 10);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("a pointerup without its pointerdown, or from another button, is ignored", () => {
    const { onClick, pointer } = setup();
    pointer("pointerup", 10, 10);
    pointer("pointerdown", 10, 10, 2);
    pointer("pointerup", 10, 10, 2);
    expect(onClick).not.toHaveBeenCalled();
  });

  it("dispose removes every listener it added", () => {
    const { input } = setup();
    expect(input.listenerCount).toBe(6);
    input.dispose();
    expect(input.listenerCount).toBe(0);
  });
});
