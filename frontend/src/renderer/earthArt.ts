import * as THREE from "three";
import type { SkyLayout } from "../scene/skyLayout";

/**
 * Stylized Earth horizon (decorative, data-free). Built in a LOCAL frame whose origin is the
 * arc's crest, so exploration progress only translates the group; geometry is rebuilt only on
 * resize. People and houses are deliberately tiny: they convey human scale, nothing else.
 */
const COLORS = {
  body: 0x2f8f47,
  band: 0x49b764,
  rim: 0xa5703f,
  water: 0x2aa7e0,
  trunk: 0x6b4a2b,
  canopy: 0x1f7a3a,
  wall: 0xeadfce,
  roof: 0xb5523b,
  person: 0x1b1b1b,
} as const;

// Positions along the crest, as fractions of the half-width. Fixed: the scenery never shuffles.
const TREES = [-0.46, -0.37, -0.29, -0.14, 0.05, 0.24, 0.33, 0.44];
const HOUSES = [-0.22, -0.02, 0.15, 0.39];
const PEOPLE = [-0.31, -0.18, -0.07, 0.09, 0.2, 0.28];
const LAKES = [
  { at: -0.25, rx: 0.12, ry: 0.2 },
  { at: 0.02, rx: 0.025, ry: 0.15 },
  { at: 0.22, rx: 0.09, ry: 0.17 },
];

export interface EarthArt {
  group: THREE.Group;
  counts: { trees: number; houses: number; people: number; lakes: number };
  haze: THREE.Mesh<THREE.RingGeometry, THREE.MeshBasicMaterial>;
  dispose(): void;
}

export function buildEarth(layout: SkyLayout): EarthArt {
  const group = new THREE.Group();
  const disposables: { dispose(): void }[] = [];
  const material = (color: number): THREE.MeshBasicMaterial => {
    const m = new THREE.MeshBasicMaterial({ color });
    disposables.push(m);
    return m;
  };
  const add = (geometry: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number, z: number, rotation = 0): THREE.Mesh => {
    disposables.push(geometry);
    const mesh = new THREE.Mesh(geometry, mat);
    mesh.position.set(x, y, z);
    mesh.rotation.z = rotation;
    group.add(mesh);
    return mesh;
  };

  const { radius: R, sag } = layout;
  const halfWidth = layout.width / 2;
  const segments = 512;
  // Surface relative to the crest: y(x) = sqrt(R^2 - x^2) - R; normal angle = -asin(x / R).
  const local = (x: number) => ({ y: Math.sqrt(Math.max(0, R * R - x * x)) - R, angle: -Math.asin(Math.max(-1, Math.min(1, x / R))) });

  add(new THREE.CircleGeometry(R, segments), material(COLORS.body), 0, -R, 0);
  add(new THREE.RingGeometry(R - sag * 0.55, R, segments), material(COLORS.band), 0, -R, 0.1);
  add(new THREE.RingGeometry(R, R + 5, segments), material(COLORS.rim), 0, -R, 0.2);

  const hazeMaterial = new THREE.MeshBasicMaterial({ color: 0xffffff, transparent: true, opacity: 0.3, depthWrite: false });
  disposables.push(hazeMaterial);
  const haze = add(new THREE.RingGeometry(R + 5, R + 46, segments), hazeMaterial, 0, -R, -0.1) as EarthArt["haze"];

  for (const lake of LAKES) {
    const x = lake.at * halfWidth;
    const { y } = local(x);
    const g = new THREE.CircleGeometry(1, 48);
    const mesh = add(g, material(COLORS.water), x, y - sag * 0.42, 0.3);
    mesh.scale.set(lake.rx * halfWidth, lake.ry * sag, 1);
  }

  const onSurface = (fraction: number, build: (g: THREE.Group) => void): void => {
    const x = fraction * halfWidth;
    const { y, angle } = local(x);
    const item = new THREE.Group();
    build(item);
    item.position.set(x, y + 4, 0.5);
    item.rotation.z = angle;
    group.add(item);
  };
  const part = (target: THREE.Group, geometry: THREE.BufferGeometry, mat: THREE.Material, x: number, y: number): void => {
    disposables.push(geometry);
    const mesh = new THREE.Mesh(geometry, mat);
    mesh.position.set(x, y, 0);
    target.add(mesh);
  };

  const trunk = material(COLORS.trunk);
  const canopy = material(COLORS.canopy);
  for (const f of TREES) onSurface(f, (g) => {
    part(g, new THREE.PlaneGeometry(1.6, 5), trunk, 0, 2.5);
    part(g, new THREE.CircleGeometry(4, 16), canopy, 0, 8);
  });
  const wall = material(COLORS.wall);
  const roof = material(COLORS.roof);
  for (const f of HOUSES) onSurface(f, (g) => {
    part(g, new THREE.PlaneGeometry(9, 6), wall, 0, 3);
    const roofShape = new THREE.Shape([new THREE.Vector2(-5.5, 0), new THREE.Vector2(5.5, 0), new THREE.Vector2(0, 4.5)]);
    part(g, new THREE.ShapeGeometry(roofShape), roof, 0, 6);
  });
  const person = material(COLORS.person);
  for (const f of PEOPLE) onSurface(f, (g) => {
    part(g, new THREE.PlaneGeometry(1.1, 3.4), person, 0, 1.9);
    part(g, new THREE.CircleGeometry(1.1, 10), person, 0, 4.4);
  });

  return {
    group,
    counts: { trees: TREES.length, houses: HOUSES.length, people: PEOPLE.length, lakes: LAKES.length },
    haze,
    dispose(): void {
      for (const d of disposables) d.dispose();
    },
  };
}
