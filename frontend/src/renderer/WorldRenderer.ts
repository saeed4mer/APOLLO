import * as THREE from "three";
import { InputController } from "../interaction/InputController";
import type { WorldRecord } from "../models/world";
import { scenePosition } from "../scene/coordinates";
import { distanceKmAtRadius, EARTH_VISUAL_RADIUS } from "../scene/scale";
import { ZoomController } from "../scene/zoom";

/** The subset of THREE.WebGLRenderer the world uses; injectable so lifecycle tests run without WebGL. */
export interface GLRendererLike {
  domElement: HTMLCanvasElement;
  setPixelRatio(ratio: number): void;
  setSize(width: number, height: number, updateStyle?: boolean): void;
  render(scene: THREE.Scene, camera: THREE.Camera): void;
  dispose(): void;
}

export interface WorldRendererOptions {
  createGLRenderer?: () => GLRendererLike;
  onHover(neowsId: string | null, clientX: number, clientY: number): void;
  onClick(neowsId: string | null): void;
  /** Called only when the eased camera distance actually changes (not every frame). */
  onViewChange?(visibleRadiusKm: number): void;
}

/**
 * Visual encoding (documented in the legend):
 * - Marker COLOUR = the NeoWs potentially-hazardous flag: yes / no / unknown. Deliberately not red.
 * - Marker SIZE = a fixed on-screen size so every object stays visible and clickable. It is a
 *   visual marker, NOT physical size: NeoWs publishes a diameter range, which the profile shows.
 */
export const MARKER_COLORS = { pha_yes: 0xf2b134, pha_no: 0x9ec9ff, pha_unknown: 0x8a8f98 } as const;
/** Marker radius as a fraction of each marker's own distance to the camera: identical apparent size
 *  for every marker at every zoom and depth, so size can never be read as diameter or proximity. */
export const MARKER_SCREEN_FRACTION = 0.008;
const CAMERA_FOV_DEG = 50;

export function markerColorKey(isPha: boolean | null): keyof typeof MARKER_COLORS {
  return isPha === true ? "pha_yes" : isPha === false ? "pha_no" : "pha_unknown";
}

let activeLoopCount = 0;

/**
 * Owns the Three.js scene, camera, markers, picking and THE single render loop.
 * start() is idempotent; dispose() cancels the loop and releases every listener and GPU resource.
 */
export class WorldRenderer {
  /** Render loops alive across all instances (must be 0 or 1). */
  static get activeLoops(): number {
    return activeLoopCount;
  }

  readonly zoom = new ZoomController();
  private readonly gl: GLRendererLike;
  private readonly scene = new THREE.Scene();
  private readonly camera: THREE.PerspectiveCamera;
  private readonly raycaster = new THREE.Raycaster();
  private readonly input: InputController;
  private readonly resizeObserver: ResizeObserver;
  private readonly owned: { dispose(): void }[] = [];
  private markers: THREE.InstancedMesh | null = null;
  private records: WorldRecord[] = [];
  private readonly hoverRing: THREE.Mesh;
  private readonly selectRing: THREE.Mesh;
  private frameHandle: number | null = null;
  private lastFrameTime: number | null = null;
  private pointer: { x: number; y: number } | null = null;
  private pointerDirty = false;
  private hoveredId: string | null = null;
  private highlight = { hovered: null as string | null, selected: null as string | null };
  private disposed = false;
  private needsViewUpdate = true;

  constructor(private readonly container: HTMLElement, private readonly options: WorldRendererOptions) {
    this.gl = options.createGLRenderer?.() ?? new THREE.WebGLRenderer({ antialias: true });
    this.gl.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    this.gl.domElement.classList.add("world-canvas");
    container.appendChild(this.gl.domElement);

    this.camera = new THREE.PerspectiveCamera(CAMERA_FOV_DEG, 1, 0.05, 2000);
    this.camera.position.set(0, 0, this.zoom.currentDistance);
    this.camera.up.set(0, 1, 0);
    this.camera.lookAt(0, 0, 0);

    const earthGeometry = this.own(new THREE.SphereGeometry(EARTH_VISUAL_RADIUS, 48, 32));
    const earthMaterial = this.own(new THREE.MeshBasicMaterial({ color: 0x1d4d7a, wireframe: true, transparent: true, opacity: 0.55 }));
    this.scene.add(new THREE.Mesh(earthGeometry, earthMaterial));

    const ringGeometry = this.own(new THREE.SphereGeometry(1, 16, 12));
    const hoverMaterial = this.own(new THREE.MeshBasicMaterial({ color: 0x7fe7ff, wireframe: true }));
    const selectMaterial = this.own(new THREE.MeshBasicMaterial({ color: 0xffffff, wireframe: true }));
    this.hoverRing = new THREE.Mesh(ringGeometry, hoverMaterial);
    this.selectRing = new THREE.Mesh(ringGeometry, selectMaterial);
    this.hoverRing.visible = this.selectRing.visible = false;
    this.scene.add(this.hoverRing, this.selectRing);

    this.input = new InputController(this.gl.domElement, {
      onWheel: (deltaY, deltaMode) => this.zoom.applyWheel(deltaY, deltaMode),
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

  /** Replace the asteroid population (once per successful world load). */
  setRecords(records: readonly WorldRecord[]): void {
    if (this.markers) {
      this.scene.remove(this.markers);
      this.markers.geometry.dispose();
      (this.markers.material as THREE.Material).dispose();
      this.markers.dispose();
      this.markers = null;
    }
    this.records = [...records];
    this.hoveredId = null;
    if (this.records.length === 0) return;

    const markers = new THREE.InstancedMesh(new THREE.SphereGeometry(1, 16, 12), new THREE.MeshBasicMaterial(), this.records.length);
    const color = new THREE.Color();
    this.records.forEach((record, i) => {
      color.setHex(MARKER_COLORS[markerColorKey(record.encounter.is_potentially_hazardous)]);
      markers.setColorAt(i, color);
    });
    this.markers = markers;
    this.scene.add(markers);
    this.needsViewUpdate = true;
  }

  setHighlight(hovered: string | null, selected: string | null): void {
    this.highlight = { hovered, selected };
    this.needsViewUpdate = true;
  }

  /** Start THE render loop. Idempotent: calling twice never creates a second loop. */
  start(): void {
    if (this.disposed || this.frameHandle !== null) return;
    activeLoopCount++;
    this.frameHandle = requestAnimationFrame(this.tick);
  }

  /** Client-pixel position of an asteroid's marker (null if unknown or behind the camera). */
  screenPositionOf(neowsId: string): { x: number; y: number } | null {
    const record = this.records.find((r) => r.neows_id === neowsId);
    if (!record) return null;
    const p = scenePosition(record);
    const v = new THREE.Vector3(p.x, p.y, p.z).project(this.camera);
    if (v.z > 1) return null;
    const rect = this.gl.domElement.getBoundingClientRect();
    return { x: rect.left + ((v.x + 1) / 2) * rect.width, y: rect.top + ((1 - v.y) / 2) * rect.height };
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
    for (const resource of this.owned) resource.dispose();
    this.gl.dispose();
    this.gl.domElement.remove();
  }

  private readonly tick = (now: number): void => {
    if (this.disposed) return;
    const dt = this.lastFrameTime === null ? 16 : now - this.lastFrameTime;
    this.lastFrameTime = now;

    if (this.zoom.step(dt) || this.needsViewUpdate) this.updateView();
    if (this.pointerDirty) this.updateHover();
    this.gl.render(this.scene, this.camera);
    this.frameHandle = requestAnimationFrame(this.tick);
  };

  private updateView(): void {
    this.needsViewUpdate = false;
    const distance = this.zoom.currentDistance;
    this.camera.position.set(0, 0, distance);
    this.camera.lookAt(0, 0, 0);
    this.camera.updateMatrixWorld();

    if (this.markers) {
      const matrix = new THREE.Matrix4();
      const position = new THREE.Vector3();
      const scale = new THREE.Vector3();
      const rotation = new THREE.Quaternion();
      this.records.forEach((record, i) => {
        const p = scenePosition(record);
        position.set(p.x, p.y, p.z);
        scale.setScalar(this.markerRadiusAt(position));
        matrix.compose(position, rotation, scale);
        this.markers!.setMatrixAt(i, matrix);
      });
      this.markers.instanceMatrix.needsUpdate = true;
      this.markers.computeBoundingSphere();
    }
    this.placeRing(this.hoverRing, this.highlight.hovered, 1.9);
    this.placeRing(this.selectRing, this.highlight.selected, 2.4);
    this.pointerDirty = this.pointer !== null; // markers moved under a stationary pointer

    // Visible radius at the origin plane, translated back to kilometres on the visualization scale.
    const halfHeight = distance * Math.tan(THREE.MathUtils.degToRad(CAMERA_FOV_DEG / 2));
    this.options.onViewChange?.(distanceKmAtRadius(halfHeight));
  }

  private placeRing(ring: THREE.Mesh, neowsId: string | null, markerMultiple: number): void {
    const record = neowsId === null ? undefined : this.records.find((r) => r.neows_id === neowsId);
    ring.visible = record !== undefined;
    if (!record) return;
    const p = scenePosition(record);
    ring.position.set(p.x, p.y, p.z);
    ring.scale.setScalar(this.markerRadiusAt(ring.position) * markerMultiple);
  }

  private markerRadiusAt(position: THREE.Vector3): number {
    return Math.max(position.distanceTo(this.camera.position), 1e-3) * MARKER_SCREEN_FRACTION;
  }

  private updateHover(): void {
    this.pointerDirty = false;
    const id = this.pointer ? this.pick(this.pointer.x, this.pointer.y) : null;
    if (id === this.hoveredId) return;
    this.hoveredId = id;
    this.options.onHover(id, this.pointer?.x ?? 0, this.pointer?.y ?? 0);
  }

  /** Raycast the instanced markers; returns the NeoWs ID under the client point, if any. */
  private pick(clientX: number, clientY: number): string | null {
    if (!this.markers) return null;
    const rect = this.gl.domElement.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0) return null;
    const ndc = new THREE.Vector2(((clientX - rect.left) / rect.width) * 2 - 1, -((clientY - rect.top) / rect.height) * 2 + 1);
    this.raycaster.setFromCamera(ndc, this.camera);
    const hit = this.raycaster.intersectObject(this.markers, false)[0];
    return hit?.instanceId !== undefined ? this.records[hit.instanceId]?.neows_id ?? null : null;
  }

  private resize(): void {
    const width = Math.max(1, this.container.clientWidth);
    const height = Math.max(1, this.container.clientHeight);
    this.gl.setSize(width, height, false);
    this.camera.aspect = width / height;
    this.camera.updateProjectionMatrix();
    this.needsViewUpdate = true;
  }

  private own<T extends { dispose(): void }>(resource: T): T {
    this.owned.push(resource);
    return resource;
  }
}
