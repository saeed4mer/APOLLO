import * as THREE from "three";
import { InputController } from "../interaction/InputController";
import type { WorldRecord } from "../models/world";
import { fieldOpacity, frontierOpacity, guideOpacity, hazeOpacity, moonOpacity, skyColors, starOpacity } from "../scene/atmosphere";
import { ExplorationController } from "../scene/exploration";
import { fallEase, RevealAnimator, type AsteroidPhase } from "../scene/reveal";
import {
  altitudePx, computeLayout, DEFAULT_DOMAIN, distanceDomain, guideDistances, guideTier, MOON_DISTANCE_KM, MOON_X_FRACTION,
  restPosition, revealedDistanceKm, surfaceY, type DistanceDomain, type DistanceView, type GuideTier, type RestPosition,
  type SkyLayout,
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
  /** Real distance (km) revealed by the current exploration progress. */
  revealedKm: number;
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
 * - Every asteroid uses the same rock, on-screen size and colour: these encode NOTHING.
 * - A ⚠ badge is attached only where NASA NeoWs is_potentially_hazardous === true (never for false
 *   or unknown). It states that flag; it is not an impact prediction, a Sentry result or a score.
 * - The fiery trail appears only while an asteroid approaches its resting place. It is a visual
 *   metaphor, identical for every asteroid, not an observed trajectory.
 * - The Moon is a distance landmark at 384,400 km; it is not data and has no direction semantics.
 * - Dashed arcs every 1,000,000 km are VISUAL DISTANCE GUIDES (distance from Earth under the same
 *   mapping as the asteroids), not orbits or trajectories.
 */
export const ROCK_PX = 9;
export const HIT_PX = 16;
export const FOCUS_ZOOM = 2.2;
export const FOCUS_ROCK_SCALE = 5;
export const FOCUS_MS = 750;
export const MOON_PX = 11;
const TRAIL_LENGTH_PX = 58;
const TRAIL_WIDTH_PX = 8;
const SPAWN_MARGIN_PX = 70;
const ROCK_COLOR = new THREE.Color(0xa08470);
const TRAIL_COLOR = new THREE.Color(0xff9a3c);
const DIMMED = 0.3;
const OFFSCREEN = -1e6;
const CAMERA_TAU_MS = 110;
const ARC_POINTS = 97;
const LABEL_X_INSET_PX = 26;
/** Dashes of the distance guides (px): drawn as individual segments so each guide has its own opacity. */
const GUIDE_DASH_PX = 6;
const GUIDE_GAP_PX = 8;

/** One distance guide as currently drawn. */
export interface GuideState {
  km: number;
  tier: GuideTier;
  alpha: number;
  altitude: number;
}

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

/** Warning-triangle badge (unit size) and its "!" mark, drawn as two instanced layers. */
function hazardGeometries(): { triangle: THREE.BufferGeometry; mark: THREE.BufferGeometry } {
  const triangle = new THREE.ShapeGeometry(new THREE.Shape([new THREE.Vector2(0, 1.05), new THREE.Vector2(-1, -0.7), new THREE.Vector2(1, -0.7)]));
  const bar = new THREE.Shape([new THREE.Vector2(-0.11, -0.05), new THREE.Vector2(0.11, -0.05), new THREE.Vector2(0.08, 0.62), new THREE.Vector2(-0.08, 0.62)]);
  const dot = new THREE.Shape();
  dot.absarc(0, -0.36, 0.12, 0, Math.PI * 2, false);
  return { triangle, mark: new THREE.ShapeGeometry([bar, dot]) };
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
 * Every animation (exploration easing, reveal/retreat, trails, focus) is state evaluated inside it.
 */
export class WorldRenderer {
  static get activeLoops(): number {
    return activeLoopCount;
  }

  readonly exploration = new ExplorationController();
  private readonly animator = new RevealAnimator();
  private readonly gl: GLRendererLike;
  private readonly scene = new THREE.Scene();
  private readonly camera = new THREE.OrthographicCamera(-1, 1, 1, -1, -100, 100);
  private readonly input: InputController;
  private readonly resizeObserver: ResizeObserver;
  private readonly owned: { dispose(): void }[] = [];
  private readonly rockMaterial = this.own(new THREE.MeshStandardMaterial({ color: 0xffffff, roughness: 0.95, metalness: 0, flatShading: true }));
  private readonly trailMaterial = this.own(new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, blending: THREE.AdditiveBlending, depthWrite: false }));
  private readonly hazardMaterial = this.own(new THREE.MeshBasicMaterial({ color: 0xffc53d }));
  private readonly hazardMarkMaterial = this.own(new THREE.MeshBasicMaterial({ color: 0x1b1b1b }));
  private readonly rockGeo = this.own(rockGeometry());
  private readonly trailGeo = this.own(trailGeometry());
  private readonly hazardGeo = hazardGeometries();
  private readonly hoverRing: THREE.Mesh;
  private readonly focusGlow = new THREE.Group();
  private readonly moon = new THREE.Group();
  private readonly moonArc: THREE.Line<THREE.BufferGeometry, THREE.LineDashedMaterial>;
  private readonly frontierArc: THREE.Line<THREE.BufferGeometry, THREE.LineDashedMaterial>;
  private readonly moonMaterials: THREE.MeshBasicMaterial[] = [];
  private readonly guides: THREE.LineSegments<THREE.BufferGeometry, THREE.LineBasicMaterial>;
  private guideStates: GuideState[] = [];
  private moonAlpha = 0;

  private width = 1;
  private height = 1;
  private layout: SkyLayout = computeLayout(1, 1, 0);
  private domain: DistanceDomain = DEFAULT_DOMAIN;
  private revealed = 0;
  private records: WorldRecord[] = [];
  private readonly index = new Map<string, number>();
  private rest: RestPosition[] = [];
  private rotations: THREE.Quaternion[] = [];
  private current: ({ x: number; y: number } | null)[] = [];
  private rocks: THREE.InstancedMesh | null = null;
  private trails: THREE.InstancedMesh | null = null;
  private hazards: THREE.InstancedMesh | null = null;
  private hazardMarks: THREE.InstancedMesh | null = null;
  private earth: EarthArt | null = null;
  private stars: THREE.Points<THREE.BufferGeometry, THREE.PointsMaterial> | null = null;
  private background = "";

  private focusedId: string | null = null;
  private anchorId: string | null = null;
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
  private viewDirty = true;
  /** Pick radius (world px) of each shown asteroid; 0 while hidden. Picking is a screen-space distance test. */
  private hitRadius: number[] = [];
  /** Canvas client rect, cached on resize: per-call getBoundingClientRect() forced layout thrash. */
  private rect: { left: number; top: number; width: number; height: number } = { left: 0, top: 0, width: 0, height: 0 };
  private disposed = false;
  frames = 0;
  /** Development timing: average ms per frame spent updating the scene (JS) and rendering. */
  readonly timing = { updateMs: 0, renderMs: 0 };

  constructor(private readonly container: HTMLElement, private readonly options: WorldRendererOptions) {
    this.own(this.hazardGeo.triangle);
    this.own(this.hazardGeo.mark);
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

    // Moon landmark: a small cratered disc. Decorative scale reference, never an asteroid record.
    const moonBodyMaterial = this.own(new THREE.MeshBasicMaterial({ color: 0xdfe3ea, transparent: true, opacity: 0 }));
    const moonBody = new THREE.Mesh(this.own(new THREE.CircleGeometry(1, 40)), moonBodyMaterial);
    this.moon.add(moonBody);
    const crater = this.own(new THREE.MeshBasicMaterial({ color: 0xb9bfc9, transparent: true, opacity: 0 }));
    this.moonMaterials.push(moonBodyMaterial, crater);
    for (const [x, y, r] of [[-0.35, 0.25, 0.22], [0.3, -0.2, 0.28], [0.15, 0.45, 0.12], [-0.25, -0.45, 0.14]] as const) {
      const c = new THREE.Mesh(this.own(new THREE.CircleGeometry(r, 20)), crater);
      c.position.set(x, y, 0.01);
      this.moon.add(c);
    }
    this.moon.position.z = -2;
    this.moon.visible = false;
    this.scene.add(this.moon);

    const dashedArc = (opacity: number): THREE.Line<THREE.BufferGeometry, THREE.LineDashedMaterial> => {
      const geometry = this.own(new THREE.BufferGeometry());
      geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(ARC_POINTS * 3), 3));
      const line = new THREE.Line(geometry, this.own(new THREE.LineDashedMaterial({ color: 0xffffff, dashSize: 6, gapSize: 7, transparent: true, opacity })));
      line.position.z = -4;
      this.scene.add(line);
      return line;
    };
    this.moonArc = dashedArc(0.35);
    this.frontierArc = dashedArc(0.55);

    // Million-km distance guides: ONE LineSegments draw, dashes as segments with per-vertex RGBA so each
    // guide has its own opacity. Buffer capacity grows with domain/viewport only (no per-frame allocation).
    this.guides = new THREE.LineSegments(new THREE.BufferGeometry(), this.own(new THREE.LineBasicMaterial({
      color: 0xffffff, vertexColors: true, transparent: true, depthWrite: false,
    })));
    this.guides.position.z = -4;
    this.guides.frustumCulled = false;
    this.scene.add(this.guides);

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

  /** Replace the population. Asteroids still present keep their animation state (no restart, no copies). */
  setRecords(records: readonly WorldRecord[]): void {
    for (const mesh of [this.rocks, this.trails, this.hazards, this.hazardMarks]) if (mesh) {
      this.scene.remove(mesh);
      mesh.dispose();
    }
    this.rocks = this.trails = this.hazards = this.hazardMarks = null;
    this.records = [...records];
    this.index.clear();
    this.records.forEach((r, i) => this.index.set(r.neows_id, i));
    this.domain = this.records.length ? distanceDomain(this.records) : DEFAULT_DOMAIN;
    this.animator.setRecords(this.records);
    this.rotations = this.records.map((r) => {
      const d = r.illustrative_direction; // decorative orientation only; deterministic per asteroid
      return new THREE.Quaternion().setFromEuler(new THREE.Euler(d.x * 3, d.y * 3, d.z * 3));
    });
    this.current = this.records.map(() => null);
    this.hitRadius = this.records.map(() => 0);
    this.hoveredId = null;
    const n = this.records.length;
    if (n > 0) {
      this.rocks = new THREE.InstancedMesh(this.rockGeo, this.rockMaterial, n);
      this.trails = new THREE.InstancedMesh(this.trailGeo, this.trailMaterial, n);
      this.hazards = new THREE.InstancedMesh(this.hazardGeo.triangle, this.hazardMaterial, n);
      this.hazardMarks = new THREE.InstancedMesh(this.hazardGeo.mark, this.hazardMarkMaterial, n);
      this.rocks.position.z = 6;
      this.trails.position.z = 5;
      // Above any rock depth (a focused rock spans z ≈ 6 ± ROCK_PX × FOCUS_ROCK_SCALE).
      this.hazards.position.z = 70;
      this.hazardMarks.position.z = 70.1;
      for (const mesh of [this.rocks, this.trails, this.hazards, this.hazardMarks]) {
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

  /**
   * Focus an asteroid (camera glides onto it) or return to the world (null). Focus never changes the
   * revealed distance. While focused, the subject is shown even if a deep link opened it beyond the
   * frontier; on return, normal eligibility applies again (it retreats if beyond the frontier).
   */
  setFocus(neowsId: string | null): void {
    if (neowsId !== null && !this.index.has(neowsId)) neowsId = null; // unknown here (e.g. a 404 deep link)
    this.focusedId = neowsId;
    if (neowsId !== null) this.anchorId = neowsId;
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
    return this.index.has(neowsId) ? this.animator.phase(neowsId) : null;
  }

  /** The mapping currently used for every distance (asteroids, Moon, guides, frontier). */
  get distanceView(): DistanceView {
    return { domain: this.domain, frontierKm: this.revealed };
  }

  /** Distance guides currently drawn (alpha > 0), nearest to the frontier first. */
  get visibleGuides(): readonly GuideState[] {
    return this.guideStates;
  }

  /** Current opacity of the Moon landmark (0 = hidden). */
  get moonOpacity(): number {
    return this.moonAlpha;
  }

  restAltitudeOf(neowsId: string): number | null {
    const i = this.index.get(neowsId);
    return i === undefined ? null : this.rest[i]?.altitude ?? null;
  }

  /** True when the NeoWs PHA badge is currently drawn on this asteroid. */
  hazardShownOf(neowsId: string): boolean {
    const i = this.index.get(neowsId);
    return i !== undefined && this.records[i]!.encounter.is_potentially_hazardous === true && this.current[i] !== null;
  }

  get revealedKm(): number {
    return this.revealed;
  }

  get distanceDomain(): DistanceDomain {
    return this.domain;
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

  /** Asteroid instances drawn (one per record) and total scene objects: proves nothing is duplicated. */
  get objectCounts(): { asteroidInstances: number; sceneObjects: number } {
    let sceneObjects = 0;
    this.scene.traverse(() => void sceneObjects++);
    return { asteroidInstances: this.rocks?.count ?? 0, sceneObjects };
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

  /** Client-pixel centre of the Moon landmark. */
  moonScreenPosition(): { x: number; y: number } {
    return this.toScreen(this.moon.position.x, this.moon.position.y);
  }

  /** Client-pixel point at the right end of the revealed-distance frontier (null at 0 km). */
  frontierScreenPosition(): { x: number; y: number } | null {
    if (this.revealed <= 0) return null;
    return this.distanceScreenPositions([Math.max(this.revealed, this.domain.minKm)])[0]!;
  }

  /** Client-pixel point of each distance on its guide, near the right edge (where guide labels sit). */
  distanceScreenPositions(kms: readonly number[]): { km: number; x: number; y: number }[] {
    const x = this.layout.width - LABEL_X_INSET_PX;
    const view = this.distanceView;
    return kms.map((km) => ({ km, ...this.toScreen(x, surfaceY(this.layout, x) + altitudePx(this.layout, km, view)) }));
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
    this.guides.geometry.dispose();
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
    this.frames++;

    let changed = this.exploration.step(dt);
    this.revealed = revealedDistanceKm(this.exploration.currentProgress, this.domain);
    if (this.animator.update(this.records, this.revealed, dt, this.focusedId)) changed = true;
    if (this.stepFocus(dt)) changed = true;
    const t0 = performance.now();
    if (changed || this.viewDirty) this.updateView();
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
    const view = this.distanceView;
    this.rest = this.records.map((r) => restPosition(this.layout, r, view));

    const focus = easeInOut(this.focusT);
    this.camera.position.set(this.camX, this.camY, 10);
    this.camera.zoom = 1 + (FOCUS_ZOOM - 1) * focus;
    this.camera.updateProjectionMatrix();
    this.camera.updateMatrixWorld();

    this.updateBackground(progress);
    if (this.earth) {
      this.earth.group.position.set(this.layout.cx, this.layout.earthTopY, 0);
      this.earth.haze.material.opacity = hazeOpacity(progress);
      this.earth.haze.material.color.set(skyColors(progress).horizon);
    }
    if (this.stars) this.stars.material.opacity = starOpacity(progress) * (1 - 0.5 * focus);
    this.updateScale(progress, focus);
    this.updateInstances(focus);
    this.pointerDirty = this.pointer !== null; // things may have moved under a stationary pointer
    this.options.onViewChange?.({ progress, revealedKm: this.revealed, focus, layout: this.layout });
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

  private placeArc(line: THREE.Line<THREE.BufferGeometry, THREE.LineDashedMaterial>, km: number): void {
    const position = line.geometry.attributes.position as THREE.BufferAttribute;
    const alt = altitudePx(this.layout, km, this.distanceView);
    for (let k = 0; k < position.count; k++) {
      const x = -20 + ((this.width + 40) * k) / (position.count - 1);
      position.setXYZ(k, x, surfaceY(this.layout, x) + alt, 0);
    }
    position.needsUpdate = true;
    line.geometry.computeBoundingSphere();
    line.computeLineDistances();
  }

  /** Moon landmark + arc, revealed-distance frontier arc, and the million-km distance guides. */
  private updateScale(progress: number, focus: number): void {
    const fade = 1 - focus;
    const view = this.distanceView;
    const moonX = this.layout.width * MOON_X_FRACTION;
    this.moonAlpha = moonOpacity(progress);
    this.moon.visible = this.moonAlpha > 0.001;
    this.moonArc.visible = this.moon.visible && fade > 0.001;
    if (this.moon.visible) {
      this.moon.position.set(moonX, surfaceY(this.layout, moonX) + altitudePx(this.layout, MOON_DISTANCE_KM, view), -2);
      this.moon.scale.setScalar(MOON_PX);
      for (const material of this.moonMaterials) material.opacity = this.moonAlpha;
      this.placeArc(this.moonArc, MOON_DISTANCE_KM);
      this.moonArc.material.opacity = 0.32 * this.moonAlpha * fade;
    }

    const frontierAlpha = frontierOpacity(progress) * fade;
    this.frontierArc.visible = this.revealed > 0 && frontierAlpha > 0.001;
    if (this.frontierArc.visible) {
      this.placeArc(this.frontierArc, Math.max(this.revealed, this.domain.minKm));
      this.frontierArc.material.opacity = 0.5 * frontierAlpha;
    }
    this.updateGuides(fieldOpacity(progress) * fade, view);
  }

  /** Rebuilds the visible guide dashes in place (fixed-capacity buffer, one draw call). */
  private updateGuides(globalAlpha: number, view: DistanceView): void {
    const distances = guideDistances(this.domain);
    const dashes = Math.ceil((this.width + 40) / (GUIDE_DASH_PX + GUIDE_GAP_PX));
    const capacity = distances.length * dashes * 2;
    let position = this.guides.geometry.getAttribute("position") as THREE.BufferAttribute | undefined;
    let color = this.guides.geometry.getAttribute("color") as THREE.BufferAttribute | undefined;
    if (!position || !color || position.count < capacity) {
      this.guides.geometry.dispose();
      this.guides.geometry = new THREE.BufferGeometry();
      position = new THREE.BufferAttribute(new Float32Array(capacity * 3), 3).setUsage(THREE.DynamicDrawUsage);
      color = new THREE.BufferAttribute(new Float32Array(capacity * 4), 4).setUsage(THREE.DynamicDrawUsage);
      this.guides.geometry.setAttribute("position", position);
      this.guides.geometry.setAttribute("color", color);
    }
    this.guideStates = [];
    if (globalAlpha <= 0.001) {
      this.guides.visible = false;
      return;
    }
    const altitudes = distances.map((km) => altitudePx(this.layout, km, view));
    // Surface height under each dash end, computed once for all guides (they run parallel to the arc).
    const xs: number[] = [];
    const surface: number[] = [];
    for (let k = 0; k < dashes; k++) {
      const x0 = -20 + k * (GUIDE_DASH_PX + GUIDE_GAP_PX);
      xs.push(x0, x0 + GUIDE_DASH_PX);
      surface.push(surfaceY(this.layout, x0), surfaceY(this.layout, x0 + GUIDE_DASH_PX));
    }
    let v = 0;
    for (let i = 0; i < distances.length; i++) {
      const km = distances[i]!;
      const tier = guideTier(km);
      const below = i > 0 ? altitudes[i]! - altitudes[i - 1]! : Infinity;
      const above = i + 1 < distances.length ? altitudes[i + 1]! - altitudes[i]! : Infinity;
      const alpha = guideOpacity(km, tier, this.revealed, Math.min(below, above)) * globalAlpha;
      if (alpha < 0.005) continue;
      this.guideStates.push({ km, tier, alpha, altitude: altitudes[i]! });
      for (let k = 0; k < xs.length; k++) {
        position.setXYZ(v, xs[k]!, surface[k]! + altitudes[i]!, 0);
        color.setXYZW(v, 1, 1, 1, alpha);
        v++;
      }
    }
    this.guideStates.sort((a, b) => Math.abs(a.km - this.revealed) - Math.abs(b.km - this.revealed) || a.km - b.km);
    position.needsUpdate = true;
    color.needsUpdate = true;
    this.guides.geometry.setDrawRange(0, v);
    this.guides.visible = v > 0;
  }

  private updateInstances(focus: number): void {
    if (!this.rocks || !this.trails || !this.hazards || !this.hazardMarks) return;
    const matrix = new THREE.Matrix4();
    const position = new THREE.Vector3();
    const scale = new THREE.Vector3();
    const identity = new THREE.Quaternion();
    const color = new THREE.Color();
    const spawnY = this.height + SPAWN_MARGIN_PX;
    const focusIndex = this.anchorId === null ? -1 : this.index.get(this.anchorId) ?? -1;
    const hidden = matrix.clone().compose(new THREE.Vector3(OFFSCREEN, OFFSCREEN, 0), identity, new THREE.Vector3(1, 1, 1));

    for (let i = 0; i < this.records.length; i++) {
      const record = this.records[i]!;
      const id = record.neows_id;
      const f = this.animator.fraction(id);
      if (f <= 0) {
        this.current[i] = null;
        this.hitRadius[i] = 0;
        for (const mesh of [this.rocks, this.trails, this.hazards, this.hazardMarks]) mesh.setMatrixAt(i, hidden);
        continue;
      }
      const rest = this.rest[i]!;
      const e = fallEase(f);
      const x = rest.x;
      const y = spawnY + (rest.y - spawnY) * e; // retreat runs the same path back up
      this.current[i] = { x, y };

      const appear = Math.min(1, f / 0.12);
      const isFocus = i === focusIndex;
      const radius = ROCK_PX * appear * (isFocus ? 1 + (FOCUS_ROCK_SCALE - 1) * focus : 1);
      matrix.compose(position.set(x, y, 0), this.rotations[i]!, scale.setScalar(Math.max(radius, 1e-3)));
      this.rocks.setMatrixAt(i, matrix);
      this.rocks.setColorAt(i, color.copy(ROCK_COLOR).multiplyScalar(isFocus ? 1 : 1 - (1 - DIMMED) * focus));

      // Trail only while approaching (visual metaphor); none while settled or retreating.
      const approaching = this.animator.isApproaching(id);
      const trailFade = approaching ? (1 - f) * (1 - focus) : 0;
      matrix.compose(position.set(x, y + ROCK_PX * 0.4, 0), identity,
        scale.set(TRAIL_WIDTH_PX * appear, Math.max(approaching ? TRAIL_LENGTH_PX * Math.sqrt(1 - e) : 0, 1e-3), 1));
      this.trails.setMatrixAt(i, matrix);
      this.trails.setColorAt(i, color.copy(TRAIL_COLOR).multiplyScalar(trailFade));

      this.hitRadius[i] = Math.max(HIT_PX, radius);

      if (record.encounter.is_potentially_hazardous === true) {
        // A fixed-size flag at the rock's upper-right edge: it never grows with the focus zoom (it
        // marks a NeoWs flag, not a magnitude). Other asteroids' badges shrink away during focus.
        const badge = Math.max(ROCK_PX * appear * 0.85 * (isFocus ? 1 : 1 - focus), 1e-3);
        matrix.compose(position.set(x + radius * 0.9 + badge * 0.6, y + radius * 0.9 + badge * 0.75, 0), identity, scale.set(badge, badge, 1));
        this.hazards.setMatrixAt(i, matrix);
        this.hazardMarks.setMatrixAt(i, matrix);
      } else {
        this.hazards.setMatrixAt(i, hidden);
        this.hazardMarks.setMatrixAt(i, hidden);
      }
    }
    for (const mesh of [this.rocks, this.trails, this.hazards, this.hazardMarks]) {
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }

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

  /**
   * The NeoWs ID under a client point: shown asteroids whose pick disc (HIT_PX, or the rock radius
   * if larger) contains the point; when discs overlap, the centre nearest the pointer wins. A plain
   * O(n) screen-space test: the camera is orthographic and the discs face it, so this is exactly
   * what a raycast against the discs would return, without per-instance matrix work.
   */
  private pick(clientX: number, clientY: number): string | null {
    const rect = this.rect;
    if (rect.width === 0 || rect.height === 0) return null;
    const zoom = this.camera.zoom;
    // Same mapping as toScreen(), inlined so the loop allocates nothing.
    const cx = rect.left + this.width / 2;
    const cy = rect.top + this.height / 2;
    let best = -1;
    let bestD = Infinity;
    for (let i = 0; i < this.current.length; i++) {
      const p = this.current[i];
      if (!p) continue;
      const d = Math.hypot(cx + (p.x - this.camX) * zoom - clientX, cy - (p.y - this.camY) * zoom - clientY);
      if (d <= this.hitRadius[i]! * zoom && d < bestD) {
        best = i;
        bestD = d;
      }
    }
    return best < 0 ? null : this.records[best]!.neows_id;
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
