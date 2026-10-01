import * as THREE from "three";
import { validateProfileResponse } from "../src/api/validateProfile";
import { validateWorldResponse } from "../src/api/validateWorld";
import { createApp, type AppHandle } from "../src/app";
import type { AsteroidProfile } from "../src/models/profile";
import type { WorldRecord } from "../src/models/world";
import { ROCK_PX, WorldRenderer } from "../src/renderer/WorldRenderer";
import { FALL_MS, revealOrder } from "../src/scene/reveal";
import {
  FakeGL, FakeResizeObserver, fixture, flushPromises, installFakeRaf, installFakeResizeObserver, trackListeners,
} from "./helpers";

let raf: ReturnType<typeof installFakeRaf>;

beforeEach(() => {
  raf = installFakeRaf();
  installFakeResizeObserver();
  FakeGL.live = 0;
  window.location.hash = "#/";
});

const worldRecords = (): WorldRecord[] => validateWorldResponse(fixture("world.json")).records;
const frames = (n: number) => {
  for (let i = 0; i < n; i++) raf.frame(16);
};
const settleFrames = Math.ceil(FALL_MS / 16) + 40;

function newRenderer(host = document.createElement("div")) {
  let gl!: FakeGL;
  const renderer = new WorldRenderer(host, { createGLRenderer: () => (gl = new FakeGL()), onHover: vi.fn(), onClick: vi.fn() });
  return { renderer, host, gl: () => gl };
}

function mountApp(root: HTMLElement): AppHandle {
  return createApp(root, {
    createGLRenderer: () => new FakeGL(),
    fetchWorld: async () => validateWorldResponse(fixture("world.json")),
    fetchProfile: async (id): Promise<AsteroidProfile> =>
      validateProfileResponse(fixture(id === "3548666" ? "profile_3548666.json" : "profile_3830890.json")),
  });
}

describe("WorldRenderer lifecycle", () => {
  it("start() is idempotent: one render loop, however often it is called", () => {
    const { renderer } = newRenderer();
    renderer.start();
    renderer.start();
    renderer.start();
    expect(raf.pending).toBe(1);
    expect(WorldRenderer.activeLoops).toBe(1);
    frames(50);
    expect(raf.pending).toBe(1);
    renderer.dispose();
    expect(raf.pending).toBe(0);
    expect(WorldRenderer.activeLoops).toBe(0);
  });

  it("dispose releases the loop, input listeners, resize observer, WebGL context, canvas and sky", () => {
    const { renderer, host } = newRenderer();
    renderer.setRecords(worldRecords());
    renderer.start();
    frames(3);
    expect(host.style.background).toContain("linear-gradient");
    expect(FakeResizeObserver.active).toBe(1);
    expect(renderer.inputListenerCount).toBe(6);
    renderer.dispose();
    renderer.dispose();
    expect(FakeResizeObserver.active).toBe(0);
    expect(renderer.inputListenerCount).toBe(0);
    expect(FakeGL.live).toBe(0);
    expect(host.querySelector("canvas")).toBeNull();
    expect(host.style.background).toBe("");
    renderer.start();
    expect(raf.pending).toBe(0);
  });

  it("each frame renders exactly once (no per-asteroid loops)", () => {
    const { renderer, gl } = newRenderer();
    renderer.setRecords(worldRecords());
    renderer.start();
    frames(30);
    expect(gl().renders).toBe(30);
    renderer.dispose();
  });
});

describe("falling animation", () => {
  it("only the farthest few appear at load; they fall, then SETTLE and stay settled", () => {
    const { renderer } = newRenderer();
    const records = worldRecords();
    renderer.setRecords(records);
    renderer.start();
    frames(2);
    const phases = () => records.map((r) => renderer.phaseOf(r.neows_id));
    const revealed = phases().filter((p) => p !== "HIDDEN").length;
    expect(revealed).toBeGreaterThan(0);
    expect(revealed).toBeLessThan(records.length / 4);
    expect(phases()).toContain("FALLING");
    frames(settleFrames);
    expect(phases().filter((p) => p === "SETTLED").length).toBe(revealed);
    renderer.dispose();
  });

  it("settled positions are stationary, and focus / resize / re-supplied records never restart a fall", () => {
    const { renderer } = newRenderer();
    const records = worldRecords();
    renderer.setRecords(records);
    renderer.start();
    renderer.exploration.setTarget(1);
    frames(settleFrames * 2);
    const ids = records.map((r) => r.neows_id);
    expect(ids.every((id) => renderer.phaseOf(id) === "SETTLED")).toBe(true);
    const before = ids.map((id) => renderer.screenPositionOf(id));
    frames(20);
    expect(ids.map((id) => renderer.screenPositionOf(id))).toEqual(before); // stationary

    renderer.setFocus(ids[0]!);
    frames(80);
    renderer.setFocus(null);
    frames(80);
    renderer.setRecords(records);
    FakeResizeObserver.instances.at(-1)!.trigger(); // the renderer's real resize path (Earth/stars rebuilt)
    frames(2);
    expect(ids.every((id) => renderer.phaseOf(id) === "SETTLED")).toBe(true);
    renderer.dispose();
  });

  it("scrolling back up never re-hides or re-drops asteroids", () => {
    const { renderer } = newRenderer();
    const records = worldRecords();
    renderer.setRecords(records);
    renderer.start();
    renderer.exploration.setTarget(0.6);
    frames(settleFrames * 2);
    renderer.exploration.setTarget(0);
    frames(settleFrames);
    expect(records.every((r) => renderer.phaseOf(r.neows_id) === "SETTLED")).toBe(true);
    renderer.dispose();
  });

  it("every settled asteroid has the same rock size (size encodes nothing)", () => {
    const { renderer } = newRenderer();
    renderer.setRecords(worldRecords());
    renderer.start();
    renderer.exploration.setTarget(1);
    frames(settleFrames * 2);
    const rocks = (renderer as unknown as { rocks: THREE.InstancedMesh }).rocks;
    const matrix = new THREE.Matrix4();
    const scale = new THREE.Vector3();
    for (let i = 0; i < rocks.count; i++) {
      rocks.getMatrixAt(i, matrix);
      matrix.decompose(new THREE.Vector3(), new THREE.Quaternion(), scale);
      expect(scale.x).toBeCloseTo(ROCK_PX, 5); // decompose() with a rotation leaves ~1e-7 float noise
    }
    renderer.dispose();
  });

  it("the farthest asteroid falls first (reveal order is by real distance)", () => {
    const { renderer } = newRenderer();
    const records = worldRecords();
    renderer.setRecords(records);
    renderer.start();
    frames(1);
    expect(renderer.phaseOf(revealOrder(records)[0]!)).not.toBe("HIDDEN");
    expect(renderer.phaseOf(revealOrder(records).at(-1)!)).toBe("HIDDEN");
    renderer.dispose();
  });
});

describe("App lifecycle", () => {
  it("20 mount/unmount cycles leave no loops, listeners, observers or contexts behind", async () => {
    const windowListeners = trackListeners(window);
    const root = document.createElement("div");
    for (let cycle = 0; cycle < 20; cycle++) {
      const app = mountApp(root);
      await flushPromises();
      raf.frame();
      expect(app.diagnostics()).toEqual({ activeLoops: 1, inputListeners: 6, storeListeners: 1, keyListeners: 1 });
      expect(raf.pending).toBe(1);
      expect(FakeResizeObserver.active).toBe(1);
      app.dispose();
      expect(WorldRenderer.activeLoops).toBe(0);
      expect(raf.pending).toBe(0);
      expect(FakeResizeObserver.active).toBe(0);
      expect(FakeGL.live).toBe(0);
      expect(windowListeners.count("hashchange")).toBe(0);
      expect(windowListeners.count("keydown")).toBe(0);
      expect(root.childElementCount).toBe(0);
    }
  });

  it("world -> focus -> back, 15 times, never duplicates loops or listeners", async () => {
    const root = document.createElement("div");
    const app = mountApp(root);
    await flushPromises();
    for (let i = 0; i < 15; i++) {
      window.location.hash = i % 2 ? "#/asteroid/3830890" : "#/asteroid/3548666";
      await flushPromises();
      frames(5);
      expect(app.store.getState().profile.status).toBe("ready");
      window.location.hash = "#/";
      await flushPromises();
      frames(5);
      expect(app.store.getState().profile.status).toBe("idle");
      expect(app.diagnostics()).toEqual({ activeLoops: 1, inputListeners: 6, storeListeners: 1, keyListeners: 1 });
      expect(raf.pending).toBe(1);
    }
    app.dispose();
  });

  it("Escape returns from focus to the world", async () => {
    const root = document.createElement("div");
    const app = mountApp(root);
    await flushPromises();
    window.location.hash = "#/asteroid/3548666";
    await flushPromises();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await flushPromises();
    expect(window.location.hash).toBe("#/");
    expect(app.store.getState().selectedId).toBeNull();
    app.dispose();
  });

  it("four failed loads + four Retry clicks: one loop, one renderer, then the real world", async () => {
    const root = document.createElement("div");
    let calls = 0;
    const app = createApp(root, {
      createGLRenderer: () => new FakeGL(),
      fetchWorld: async () => {
        if (++calls <= 4) throw new Error("service down");
        return validateWorldResponse(fixture("world.json"));
      },
      fetchProfile: async () => { throw new Error("unused"); },
    });
    await flushPromises();
    for (let i = 0; i < 4; i++) {
      expect(app.store.getState().world.status).toBe("error");
      [...root.querySelectorAll<HTMLButtonElement>(".status-overlay button")].find((b) => b.textContent === "Retry")!.click();
      await flushPromises();
      raf.frame();
    }
    expect(calls).toBe(5);
    expect(app.store.getState().world.status).toBe("ready");
    expect(app.diagnostics()).toEqual({ activeLoops: 1, inputListeners: 6, storeListeners: 1, keyListeners: 1 });
    expect(raf.pending).toBe(1);
    expect(FakeGL.live).toBe(1);
    app.dispose();
  });

  it("disposing while the world is still loading ignores the late response", async () => {
    const root = document.createElement("div");
    let resolveWorld!: (value: ReturnType<typeof validateWorldResponse>) => void;
    const app = createApp(root, {
      createGLRenderer: () => new FakeGL(),
      fetchWorld: () => new Promise((resolve) => (resolveWorld = resolve)),
      fetchProfile: async () => { throw new Error("unused"); },
    });
    app.dispose();
    resolveWorld(validateWorldResponse(fixture("world.json")));
    await flushPromises();
    expect(root.childElementCount).toBe(0);
    expect(WorldRenderer.activeLoops).toBe(0);
  });
});
