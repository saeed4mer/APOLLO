import { validateWorldResponse } from "../src/api/validateWorld";
import { createApp, type AppHandle } from "../src/app";
import type { AsteroidProfile } from "../src/models/profile";
import { validateProfileResponse } from "../src/api/validateProfile";
import * as THREE from "three";
import { MARKER_SCREEN_FRACTION, WorldRenderer } from "../src/renderer/WorldRenderer";
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
    const host = document.createElement("div");
    const renderer = new WorldRenderer(host, { createGLRenderer: () => new FakeGL(), onHover: vi.fn(), onClick: vi.fn() });
    renderer.start();
    renderer.start();
    renderer.start();
    expect(raf.pending).toBe(1);
    expect(WorldRenderer.activeLoops).toBe(1);
    for (let i = 0; i < 50; i++) raf.frame();
    expect(raf.pending).toBe(1); // each frame schedules exactly one successor
    renderer.dispose();
    expect(raf.pending).toBe(0);
    expect(WorldRenderer.activeLoops).toBe(0);
  });

  it("dispose releases the loop, input listeners, resize observer, WebGL context and canvas", () => {
    const host = document.createElement("div");
    const renderer = new WorldRenderer(host, { createGLRenderer: () => new FakeGL(), onHover: vi.fn(), onClick: vi.fn() });
    renderer.setRecords(validateWorldResponse(fixture("world.json")).records);
    renderer.start();
    expect(FakeResizeObserver.active).toBe(1);
    expect(renderer.inputListenerCount).toBe(6);
    renderer.dispose();
    renderer.dispose(); // idempotent
    expect(FakeResizeObserver.active).toBe(0);
    expect(renderer.inputListenerCount).toBe(0);
    expect(FakeGL.live).toBe(0);
    expect(host.querySelector("canvas")).toBeNull();
    renderer.start(); // a disposed renderer can never restart a loop
    expect(raf.pending).toBe(0);
  });

  it("every marker has the same on-screen size (size encodes nothing): radius / distance-to-camera is constant", () => {
    const host = document.createElement("div");
    const renderer = new WorldRenderer(host, { createGLRenderer: () => new FakeGL(), onHover: vi.fn(), onClick: vi.fn() });
    renderer.setRecords(validateWorldResponse(fixture("world.json")).records);
    renderer.start();
    for (const wheel of [0, -400, 600]) {
      renderer.zoom.applyWheel(wheel);
      for (let i = 0; i < 60; i++) raf.frame();
      const internals = renderer as unknown as { markers: THREE.InstancedMesh; camera: THREE.PerspectiveCamera };
      const matrix = new THREE.Matrix4();
      const position = new THREE.Vector3();
      const scale = new THREE.Vector3();
      const ratios: number[] = [];
      for (let i = 0; i < internals.markers.count; i++) {
        internals.markers.getMatrixAt(i, matrix);
        matrix.decompose(position, new THREE.Quaternion(), scale);
        ratios.push(scale.x / position.distanceTo(internals.camera.position));
      }
      for (const ratio of ratios) expect(ratio).toBeCloseTo(MARKER_SCREEN_FRACTION, 9);
    }
    renderer.dispose();
  });

  it("each frame renders exactly once (no duplicated loops)", () => {
    const host = document.createElement("div");
    let gl!: FakeGL;
    const renderer = new WorldRenderer(host, { createGLRenderer: () => (gl = new FakeGL()), onHover: vi.fn(), onClick: vi.fn() });
    renderer.start();
    for (let i = 0; i < 30; i++) raf.frame();
    expect(gl.renders).toBe(30);
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
      expect(app.diagnostics()).toEqual({ activeLoops: 1, inputListeners: 6, storeListeners: 1 });
      expect(raf.pending).toBe(1);
      expect(FakeResizeObserver.active).toBe(1);
      app.dispose();
      expect(WorldRenderer.activeLoops).toBe(0);
      expect(raf.pending).toBe(0);
      expect(FakeResizeObserver.active).toBe(0);
      expect(FakeGL.live).toBe(0);
      expect(windowListeners.count("hashchange")).toBe(0);
      expect(root.childElementCount).toBe(0);
    }
  });

  it("world -> profile -> back, 15 times, never duplicates loops or listeners", async () => {
    const root = document.createElement("div");
    const app = mountApp(root);
    await flushPromises();
    for (let i = 0; i < 15; i++) {
      window.location.hash = i % 2 ? "#/asteroid/3830890" : "#/asteroid/3548666";
      await flushPromises();
      raf.frame();
      expect(app.store.getState().profile.status).toBe("ready");
      window.location.hash = "#/";
      await flushPromises();
      raf.frame();
      expect(app.store.getState().profile.status).toBe("idle");
      expect(app.diagnostics()).toEqual({ activeLoops: 1, inputListeners: 6, storeListeners: 1 });
      expect(raf.pending).toBe(1);
    }
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
      (root.querySelector(".status-overlay button") as HTMLButtonElement).click();
      await flushPromises();
      raf.frame();
    }
    expect(calls).toBe(5);
    expect(app.store.getState().world.status).toBe("ready");
    expect(app.diagnostics()).toEqual({ activeLoops: 1, inputListeners: 6, storeListeners: 1 });
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
