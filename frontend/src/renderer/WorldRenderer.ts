import * as THREE from "three";
import { InputController } from "../interaction/InputController";
import type { WorldRecord } from "../models/world";
import { referenceOpacity, skyColors, smoothstep, starOpacity } from "../scene/atmosphere";
import { ExplorationController } from "../scene/exploration";
import { fallEase, RevealTracker, type AsteroidPhase } from "../scene/reveal";
import {
  altitudePx, computeLayout, REFERENCE_DISTANCES_KM, restPosition, surfaceY, type RestPosition, type SkyLayout,
} from "../scene/skyLayout";
import { buildEarth, type EarthArt } from "./earthArt";

/** The subset of THREE.WebGLRenderer the world uses; injectable so lifecycle tests run without WebGL. */
export interface GLRendererLike {
  domElement: HTMLCanvasElement;
  setPixelRatio(ratio: number): void;
  setSize(width: number, height: number, updateStyle?: boolean): void;
  setClearColor(color: THREE.ColorRepresentation, alpha?: number): void;
  render(scene: THREE.Scene, camera: THREE.Camera): void;
  dispose(): void;
}

export interface ViewSnapshot {
  progress: number;
  deepestProgress: number;
  /** 0 = world view, 1 = fully focused on an asteroid. */
  focus: number;
  layout: SkyLayout;
}

export interface WorldRendererOptions {
  createGLRenderer?: () => GLRendererLike;
  onHover(neowsId: string | null, clientX: number, clientY: number): void;
  onClick(neowsId: string | null): void;
  /** Called after any frame in which something visible moved (never on idle frames). */
  onViewChange?(view: ViewSnapshot): void;
}

/**
 * Visual encoding (stated in "About this view"):
 * - Every asteroid uses the same rock and the same on-screen size: size and colour encode NOTHING
 *   (no PHA, Sentry or danger encoding). Facts are shown as text on hover and in focus.
 * - The fiery trail appears only while an asteroid falls into place. It is a visual metaphor for
 *   "approach", identical for every asteroid, not an observed trajectory.
 */
export const ROCK_PX = 9;
export const HIT_PX = 16;
export const FOCUS_ZOOM = 2.2;
export const FOCUS_ROCK_SCALE = 5;
export const FOCUS_MS = 750;
const TRAIL_LENGTH_PX = 58;
const TRAIL_WIDTH_PX = 8;
const SPAWN_MARGIN_PX = 70;
const ROCK_COLOR = new THREE.Color(0xa08470);
const TRAIL_COLOR = new THREE.Color(0xff9a3c);
const DIMMED = 0.3;
const OFFSCREEN = -1e6;
const CAMERA_TAU_MS = 110;

let activeLoopCount = 0;

function rockGeometry(): THREE.BufferGeometry {
  // A deterministic lumpy icosahedron. The jitter is a pure function of vertex POSITION, so the
  // duplicated vertices of the non-indexed geometry move together and the rock stays closed.
  const geometry = new THREE.IcosahedronGeometry(1, 1);
  const position = geometry.attributes.position as THREE.BufferAttribute;
  const v = new THREE.Vector3();
  for (let i = 0; i < position.count; i++) {
    v.fromBufferAttribute(position, i);
    const n = Math.sin(v.x * 12.9898 + v.y * 78.233 + v.z * 37.719) * 43758.5453;
    v.multiplyScalar(0.8 + 0.2 * (n - Math.floor(n)));
    position.setXYZ(i, v.x, v.y, v.z);
  }
  geometry.computeVertexNormals();
  return geometry;
}

function trailGeometry(): THREE.BufferGeometry {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.Float32BufferAttribute([-0.5, 0, 0, 0.5, 0, 0, 0, 1, 0], 3));
  return geometry;
}

/** Deterministic decorative star field (not data): fixed-seed LCG, regenerated only on resize. */
function starPositions(width: number, height: number): Float32Array {
  const count = Math.min(900, Math.round((width * height) / 2200));
  const out = new Float32Array(count * 3);
  let seed = 1337;
  const next = (): number => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
  for (let i = 0; i < count; i++) {
    out[i * 3] = next() * width;
    out[i * 3 + 1] = height * (0.22 + 0.78 * next());
    out[i * 3 + 2] = -5;
  }
  return out;
}

const easeInOut = (t: number): number => (t < 0.5 ? 4 * t * t * t : 1 - (-2 * t + 2) ** 3 / 2);

/**
 * Owns the Three.js scene, the orthographic pixel camera, picking and THE single render loop.
 * Every animation (exploration easing, falls, trails, focus) is state evaluated inside that loop.
 */
export class WorldRenderer {
  static get activeLoops(): number {
    return activeLoopCount;
  }

  readonly exploration = new ExplorationController();
  private readonly reveal = new RevealTracker();
  private readonly gl: GLRendererLike;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -100, 100);
  private readonly raycaster = new THREE.Raycaster();
  private readonly input: InputController;
  private readonly resizeObserver: ResizeObserver;
  private readonly owned: { dispose(): void }[] = [];
  private readonly rockMaterial = this.own(new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.95, metalness: 0, flatShading: true }));
  private readonly trailMaterial = this.own(new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
  private readonly hitMaterial = this.own(new THREE.MeshBasicMaterial({ visible: false }));
  private readonly rockGeo = this.own(rockGeometry());
  private readonly trailGeo = this.own(trailGeometry());
  private readonly hitGeo = this.own(new THREE.CircleGeometry(1, 12));
  private readonly hoverRing: THREE.Mesh;
  private readonly focusGlow = new THREE.Group();
  private readonly references: THREE.Line<THREE.BufferGeometry, THREE.LineDashedMaterial>[] = [];

  private width = 1;
  private height = 1;
  private layout: SkyLayout = computeLayout(1, 1, 0);
  private records: WorldRecord[] = [];
  private readonly index = new Map<string, number>();
  private rest: RestPosition[] = [];
  private rotations: THREE.Quaternion[] = [];
  private current: ({ x: number; y: number } | null)[] = [];
  private rocks: THREE.InstancedMesh | null = null;
  private trails: THREE.InstancedMesh | null = null;
  private hits: THREE.InstancedMesh | null = null;
  private earth: EarthArt | null = null;
  private stars: THREE.Points<THREE.BufferGeometry, THREE.PointsMaterial> | null = null;
  private background = "";

  private focusedId: string | null = null;
  private anchorId: string | null = null;
  private pendingSettle: string | null = null;
  private focusT = 0;
  private camX = 0;
  private camY = 0;
  private cameraMoving = false;
  private hoveredId: string | null = null;
  private highlightedId: string | null = null;
  private pointer: { x: number; y: number } | null = null;
  private pointerDirty = false;
  private frameHandle: number | null = null;
  private lastFrameTime: number | null = null;
  private now = 0;
  private viewDirty = true;
  private hitBoundsDirty = true;
  /** Canvas client rect, cached on resize: per-call getBoundingClientRect() forced layout thrash. */
  private rect: { left: number; top: number; width: number; height: number } = { left: 0, top: 0, width: 0, height: 0 };
  private disposed = false;
  frames = 0;
  /** Development timing: average ms per frame spent updating the scene (JS) and rendering. */
  readonly timing = { updateMs: 0, renderMs: 0 };

  constructor(private readonly container: HTMLElement, private readonly options: WorldRendererOptions) {
    this.gl = options.createGLRenderer?.() ?? new THREE.WebGLRenderer({ antialias: true, alpha: true });
    this.gl.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.gl.setClearColor(0x000000, 0); // transparent: the sky gradient is the container's background
    this.gl.domElement.classList.add("world-canvas");
    container.appendChild(this.gl.domElement);

    this.scene.add(new THREE.AmbientLight(0xffffff, 1.1));
    const sun = new THREE.DirectionalLight(0xfff1dc, 2.2);
    sun.position.set(-0.6, 0.9, 1);
    this.scene.add(sun);

    this.hoverRing = new THREE.Mesh(this.own(new THREE.RingGeometry(1.35, 1.6, 40)), this.own(new THREE.MeshBasicMaterial({ color: 0xffffff })));
    this.hoverRing.visible = false;
    this.hoverRing.position.z = 8;
    for (const [scale, opacity] of [[1.3, 0.3], [1.7, 0.13], [2.3, 0.05]] as const) {
      const glow = new THREE.Mesh(this.own(new THREE.CircleGeometry(scale, 48)), this.own(new THREE.MeshBasicMaterial({
        color: 0xffa24a, transparent: true, opacity, blending: THREE.AdditiveBlending, depthWrite: false,
      })));
      glow.userData.baseOpacity = opacity;
      this.focusGlow.add(glow);
    }
    this.focusGlow.visible = false;
    this.focusGlow.position.z = 3;
    this.scene.add(this.hoverRing, this.focusGlow);

    for (let i = 0; i < REFERENCE_DISTANCES_KM.length; i++) {
      const geometry = this.own(new THREE.BufferGeometry());
      geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(97 * 3), 3));
      const line = new THREE.Line(geometry, this.own(new THREE.LineDashedMaterial({ color: 0xffffff, dashSize: 6, gapSize: 7, transparent: true, opacity: 0 })));
      line.position.z = -4;
      this.references.push(line);
      this.scene.add(line);
    }

    this.input = new InputController(this.gl.domElement, {
      onWheel: (deltaY, deltaMode) => {
        if (this.focusedId === null) this.exploration.applyWheel(deltaY, deltaMode); // no exploration while focused
      },
      onPointerMove: (x, y) => {
        this.pointer = { x, y };
        this.pointerDirty = true;
      },
      onPointerLeave: () => {
        this.pointer = null;
        this.pointerDirty = true;
      },
      onClick: (x, y) => this.options.onClick(this.pick(x, y)),
    });

    this.resizeObserver = new ResizeObserver(() => this.resize());
    this.resizeObserver.observe(container);
    this.resize();
  }

  /** Replace the population. Asteroids still present keep their lifecycle (no re-fall). */
  setRecords(records: readonly WorldRecord[]): void {
    for (const mesh of [this.rocks, this.trails, this.hits]) if (mesh) {
      this.scene.remove(mesh);
      mesh.dispose();
    }
    this.rocks = this.trails = this.hits = null;
    this.records = [...records];
    this.index.clear();
    this.records.forEach((r, i) => this.index.set(r.neows_id, i));
    this.reveal.setRecords(this.records);
    this.rotations = this.records.map((r) => {
      const d = r.illustrative_direction; // decorative orientation only; deterministic per asteroid
      return new THREE.Quaternion().setFromEuler(new THREE.Euler(d.x * 3, d.y * 3, d.z * 3));
    });
    this.current = this.records.map(() => null);
    this.hoveredId = null;
    const n = this.records.length;
    if (n > 0) {
      this.rocks = new THREE.InstancedMesh(this.rockGeo, this.rockMaterial, n);
      this.trails = new THREE.InstancedMesh(this.trailGeo, this.trailMaterial, n);
      this.hits = new THREE.InstancedMesh(this.hitGeo, this.hitMaterial, n);
      this.rocks.position.z = 6;
      this.trails.position.z = 5;
      this.hits.position.z = 7;
      for (const mesh of [this.rocks, this.trails, this.hits]) {
        mesh.frustumCulled = false;
        mesh.instanceMatrix.setUsage(THREE.DynamicDrawUsage);
        this.scene.add(mesh);
      }
      for (let i = 0; i < n; i++) {
        this.rocks.setColorAt(i, ROCK_COLOR);
        this.trails.setColorAt(i, TRAIL_COLOR);
      }
    }
    if (this.focusedId && !this.index.has(this.focusedId)) this.focusedId = null;
    this.viewDirty = true;
  }

  /** Focus an asteroid (camera glides onto it) or return to the world (null). Never re-drops anything. */
  setFocus(neowsId: string | null): void {
    if (neowsId !== null && !this.index.has(neowsId)) neowsId = null; // unknown here (e.g. a 404 deep link)
    this.focusedId = neowsId;
    if (neowsId !== null) {
      this.anchorId = neowsId;
      this.pendingSettle = neowsId; // a deep-linked asteroid not yet revealed appears in place
    }
    this.viewDirty = true;
  }

  setHighlight(neowsId: string | null): void {
    this.highlightedId = neowsId;
    this.viewDirty = true;
  }

  start(): void {
    if (this.disposed || this.frameHandle !== null) return;
    activeLoopCount++;
    this.frameHandle = requestAnimationFrame(this.tick);
  }

  phaseOf(neowsId: string): AsteroidPhase | null {
    return this.index.has(neowsId) ? this.reveal.phase(neowsId, this.now) : null;
  }

  restAltitudeOf(neowsId: string): number | null {
    const i = this.index.get(neowsId);
    return i === undefined ? null : this.rest[i]?.altitude ?? null;
  }

  get focusProgress(): number {
    return this.focusT;
  }

  /** True once the focus animation AND the camera glide have both arrived. */
  get focusSettled(): boolean {
    return this.focusT === (this.focusedId ? 1 : 0) && !this.cameraMoving;
  }

  /** On-screen radius (px) of the focused asteroid, so callouts can keep clear of it. */
  get focusedScreenRadius(): number {
    return ROCK_PX * (1 + (FOCUS_ROCK_SCALE - 1) * easeInOut(this.focusT)) * this.camera.zoom;
  }

  get viewLayout(): SkyLayout {
    return this.layout;
  }

  get earthCounts(): EarthArt["counts"] | null {
    return this.earth?.counts ?? null;
  }

  /** Client-pixel position of an asteroid as currently drawn (null while hidden). */
  screenPositionOf(neowsId: string): { x: number; y: number } | null {
    const i = this.index.get(neowsId);
    const p = i === undefined ? null : this.current[i];
    return p ? this.toScreen(p.x, p.y) : null;
  }

  /** Client-pixel anchor for each reference-distance label (right end of its arc). */
  referenceAnchors(): { label: string; x: number; y: number }[] {
    const x = this.layout.width * 0.985;
    return REFERENCE_DISTANCES_KM.map(({ km, label }) => ({ label, ...this.toScreen(x, surfaceY(this.layout, x) + altitudePx(this.layout, km)) }));
  }

  get inputListenerCount(): number {
    return this.input.listenerCount;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    if (this.frameHandle !== null) {
      cancelAnimationFrame(this.frameHandle);
      this.frameHandle = null;
      activeLoopCount--;
    }
    this.resizeObserver.disconnect();
    this.input.dispose();
    this.setRecords([]);
    this.earth?.dispose();
    this.stars?.geometry.dispose();
    this.stars?.material.dispose();
    for (const resource of this.owned) resource.dispose();
    this.gl.dispose();
    this.gl.domElement.remove();
    this.container.style.background = "";
  }

  private readonly tick = (now: number): void => {
    if (this.disposed) return;
    const dt = this.lastFrameTime === null ? 16 : now - this.lastFrameTime;
    this.lastFrameTime = now;
    this.now = now;
    this.frames++;

    let changed = this.exploration.step(dt);
    if (this.pendingSettle) {
      this.reveal.settleNow(this.pendingSettle, now);
      this.pendingSettle = null;
      changed = true;
    }
    if (this.reveal.update(this.exploration.deepestProgress, now)) changed = true;
    if (this.stepFocus(dt)) changed = true;
    const t0 = performance.now();
    if (changed || this.viewDirty || this.reveal.anyFalling(now)) this.updateView();
    if (this.pointerDirty) this.updateHover();
    const t1 = performance.now();
    this.gl.render(this.scene, this.camera);
    const t2 = performance.now();
    this.timing.updateMs += (t1 - t0 - this.timing.updateMs) * 0.05;
    this.timing.renderMs += (t2 - t1 - this.timing.renderMs) * 0.05;
    this.frameHandle = requestAnimationFrame(this.tick);
  };

  private stepFocus(dt: number): boolean {
    let moved = false;
    const target = this.focusedId ? 1 : 0;
    if (this.focusT !== target) {
      this.focusT = Math.min(1, Math.max(0, this.focusT + (target ? 1 : -1) * (dt / FOCUS_MS)));
      if (this.focusT === 0) this.anchorId = null;
      moved = true;
    }
    // Camera centre eases toward its goal, so switching focus A -> B glides instead of jumping.
    const eased = easeInOut(this.focusT);
    const anchor = this.anchorId === null ? undefined : this.current[this.index.get(this.anchorId) ?? -1];
    const goalX = this.width / 2 + ((anchor?.x ?? this.width / 2) - this.width / 2) * eased;
    const goalY = this.height / 2 + ((anchor?.y ?? this.height / 2) - this.height / 2) * eased;
    this.cameraMoving = Math.abs(goalX - this.camX) > 0.05 || Math.abs(goalY - this.camY) > 0.05;
    if (this.cameraMoving) {
      const blend = 1 - Math.exp(-Math.max(0, dt) / CAMERA_TAU_MS);
      this.camX += (goalX - this.camX) * blend;
      this.camY += (goalY - this.camY) * blend;
      moved = true;
    } else if (this.camX !== goalX || this.camY !== goalY) {
      this.camX = goalX;
      this.camY = goalY;
      moved = true;
    }
    return moved;
  }

  private updateView(): void {
    this.viewDirty = false;
    const progress = this.exploration.currentProgress;
    this.layout = computeLayout(this.width, this.height, progress);
    this.rest = this.records.map((r) => restPosition(this.layout, r));

    const focus = easeInOut(this.focusT);
    this.camera.position.set(this.camX, this.camY, 10);
    this.camera.zoom = 1 + (FOCUS_ZOOM - 1) * focus;
    this.camera.updateProjectionMatrix();
    this.camera.updateMatrixWorld();

    this.updateBackground(progress);
    if (this.earth) {
      this.earth.group.position.set(this.layout.cx, this.layout.earthTopY, 0);
      this.earth.haze.material.opacity = 0.3 * (1 - smoothstep(0.45, 0.9, progress));
      this.earth.haze.material.color.set(skyColors(progress).horizon);
    }
    if (this.stars) this.stars.material.opacity = starOpacity(progress) * (1 - 0.5 * focus);
    this.updateReferences(progress, focus);
    this.updateInstances(focus);
    this.pointerDirty = this.pointer !== null; // things may have moved under a stationary pointer
    this.options.onViewChange?.({ progress, deepestProgress: this.exploration.deepestProgress, focus, layout: this.layout });
  }

  private updateBackground(progress: number): void {
    const { zenith, horizon } = skyColors(progress);
    const crest = (100 * this.layout.earthTopY) / this.height;
    const css = `linear-gradient(to top, ${horizon} 0%, ${horizon} ${crest.toFixed(2)}%, ${zenith} 100%)`;
    if (css !== this.background) {
      this.background = css;
      this.container.style.background = css;
    }
  }

  private updateReferences(progress: number, focus: number): void {
    const opacity = referenceOpacity(progress) * (1 - focus);
    REFERENCE_DISTANCES_KM.forEach(({ km }, i) => {
      const line = this.references[i]!;
      line.material.opacity = opacity;
      line.visible = opacity > 0.001;
      if (!line.visible) return;
      const position = line.geometry.attributes.position as THREE.BufferAttribute;
      const alt = altitudePx(this.layout, km);
      for (let k = 0; k < position.count; k++) {
        const x = -20 + ((this.width + 40) * k) / (position.count - 1);
        position.setXYZ(k, x, surfaceY(this.layout, x) + alt, 0);
      }
      position.needsUpdate = true;
      line.geometry.computeBoundingSphere();
      line.computeLineDistances();
    });
  }

  private updateInstances(focus: number): void {
    if (!this.rocks || !this.trails || !this.hits) return;
    const matrix = new THREE.Matrix4();
    const position = new THREE.Vector3();
    const scale = new THREE.Vector3();
    const identity = new THREE.Quaternion();
    const color = new THREE.Color();
    const spawnY = this.height + SPAWN_MARGIN_PX;
    const focusIndex = this.anchorId === null ? -1 : this.index.get(this.anchorId) ?? -1;

    for (let i = 0; i < this.records.length; i++) {
      const id = this.records[i]!.neows_id;
      const f = this.reveal.fallFraction(id, this.now);
      if (f === null) {
        this.current[i] = null;
        matrix.compose(position.set(OFFSCREEN, OFFSCREEN, 0), identity, scale.set(1, 1, 1));
        this.rocks.setMatrixAt(i, matrix);
        this.trails.setMatrixAt(i, matrix);
        this.hits.setMatrixAt(i, matrix);
        continue;
      }
      const rest = this.rest[i]!;
      const e = fallEase(f);
      const x = rest.x;
      const y = spawnY + (rest.y - spawnY) * e;
      this.current[i] = { x, y };

      const appear = Math.min(1, f / 0.12);
      const isFocus = i === focusIndex;
      const radius = ROCK_PX * appear * (isFocus ? 1 + (FOCUS_ROCK_SCALE - 1) * focus : 1);
      matrix.compose(position.set(x, y, 0), this.rotations[i]!, scale.setScalar(Math.max(radius, 1e-3)));
      this.rocks.setMatrixAt(i, matrix);
      this.rocks.setColorAt(i, color.copy(ROCK_COLOR).multiplyScalar(isFocus ? 1 : 1 - (1 - DIMMED) * focus));

      const trailFade = f < 1 ? (1 - f) * (1 - focus) : 0;
      matrix.compose(position.set(x, y + ROCK_PX * 0.4, 0), identity,
        scale.set(TRAIL_WIDTH_PX * appear, Math.max(TRAIL_LENGTH_PX * Math.sqrt(1 - e) * (f < 1 ? 1 : 0), 1e-3), 1));
      this.trails.setMatrixAt(i, matrix);
      this.trails.setColorAt(i, color.copy(TRAIL_COLOR).multiplyScalar(trailFade));

      matrix.compose(position.set(x, y, 0), identity, scale.setScalar(Math.max(HIT_PX, radius)));
      this.hits.setMatrixAt(i, matrix);
    }
    for (const mesh of [this.rocks, this.trails, this.hits]) {
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
    this.hitBoundsDirty = true; // recomputed lazily in pick(); rendering does not need it (no culling)

    const anchor = focusIndex >= 0 ? this.current[focusIndex] : null;
    this.focusGlow.visible = anchor !== null && focus > 0;
    if (anchor) {
      this.focusGlow.position.set(anchor.x, anchor.y, 3);
      this.focusGlow.scale.setScalar(ROCK_PX * (1 + (FOCUS_ROCK_SCALE - 1) * focus));
      for (const glow of this.focusGlow.children as THREE.Mesh<THREE.BufferGeometry, THREE.MeshBasicMaterial>[]) {
        glow.material.opacity = (glow.userData.baseOpacity as number) * focus;
      }
    }
    const hi = this.highlightedId === null ? undefined : this.index.get(this.highlightedId);
    const hovered = hi === undefined ? null : this.current[hi];
    this.hoverRing.visible = hovered !== null && focus < 0.01;
    if (hovered) {
      this.hoverRing.position.set(hovered.x, hovered.y, 8);
      this.hoverRing.scale.setScalar(ROCK_PX);
    }
  }

  private updateHover(): void {
    this.pointerDirty = false;
    const id = this.pointer ? this.pick(this.pointer.x, this.pointer.y) : null;
    if (id === this.hoveredId) return;
    this.hoveredId = id;
    this.options.onHover(id, this.pointer?.x ?? 0, this.pointer?.y ?? 0);
  }

  /** Raycast the hit discs of revealed asteroids; returns the NeoWs ID under the client point. */
  private pick(clientX: number, clientY: number): string | null {
    if (!this.hits) return null;
    const rect = this.rect;
    if (rect.width === 0 || rect.height === 0) return null;
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    if (this.hitBoundsDirty) {
      this.hits.computeBoundingSphere();
      this.hitBoundsDirty = false;
    }
    this.raycaster.setFromCamera(ndc, this.camera);
    // Hit discs of nearby asteroids can overlap; choose the one whose centre is nearest the pointer.
    let best: { id: string; d: number } | null = null;
    for (const hit of this.raycaster.intersectObject(this.hits, false)) {
      if (hit.instanceId === undefined) continue;
      const p = this.current[hit.instanceId];
      const id = this.records[hit.instanceId]?.neows_id;
      if (!p || !id) continue;
      const screen = this.toScreen(p.x, p.y);
      const d = Math.hypot(screen.x - clientX, screen.y - clientY);
      if (!best || d < best.d) best = { id, d };
    }
    return best?.id ?? null;
  }

  private toScreen(x: number, y: number): { x: number; y: number } {
    const rect = this.rect;
    const zoom = this.camera.zoom;
    return { x: rect.left + this.width / 2 + (x - this.camX) * zoom, y: rect.top + this.height / 2 - (y - this.camY) * zoom };
  }

  private resize(): void {
    this.width = Math.max(1, this.container.clientWidth);
    this.height = Math.max(1, this.container.clientHeight);
    this.gl.setSize(this.width, this.height, false);
    const r = this.gl.domElement.getBoundingClientRect();
    this.rect = { left: r.left, top: r.top, width: r.width, height: r.height };
    this.camera.left = -this.width / 2;
    this.camera.right = this.width / 2;
    this.camera.top = this.height / 2;
    this.camera.bottom = -this.height / 2;
    if (this.focusT === 0) {
      this.camX = this.width / 2;
      this.camY = this.height / 2;
    }
    this.layout = computeLayout(this.width, this.height, this.exploration.currentProgress);

    if (this.earth) {
      this.scene.remove(this.earth.group);
      this.earth.dispose();
    }
    this.earth = buildEarth(this.layout);
    this.scene.add(this.earth.group);

    if (this.stars) {
      this.scene.remove(this.stars);
      this.stars.geometry.dispose();
      this.stars.material.dispose();
    }
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(starPositions(this.width, this.height), 3));
    this.stars = new THREE.Points(geometry, new THREE.PointsMaterial({ color: 0xffffff, size: 1.6, sizeAttenuation: false, transparent: true, opacity: 0 }));
    this.scene.add(this.stars);
    this.viewDirty = true;
  }

  private own<T extends { dispose(): void }>(resource: T): T {
    this.owned.push(resource);
    return resource;
  }
}
