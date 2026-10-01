import type { UnitVector, WorldRecord } from "../models/world";
import { visualRadius } from "./scale";

/**
 * World coordinate system (right-handed, Three.js convention):
 *   +X  screen right     +Y  screen up     +Z  toward the viewer
 *   origin = Earth placeholder centre; 1 scene unit = EARTH_VISUAL_RADIUS.
 *
 * The API's illustrative_direction (x, y, z) maps to scene (x, y, z) UNCHANGED (identity mapping).
 * The renderer never generates, re-seeds or re-normalizes directions: the backend's versioned
 * algorithm (sha256-uniform-sphere-v1, seeded by neows_id) is authoritative. The camera looks
 * from +Z toward the origin with +Y up, so the direction a user sees is a fixed, documented
 * projection of the served vector.
 */
export interface ScenePosition {
  x: number;
  y: number;
  z: number;
}

export function directionToScene(direction: UnitVector): ScenePosition {
  return { x: direction.x, y: direction.y, z: direction.z };
}

export function scenePosition(record: WorldRecord): ScenePosition {
  const r = visualRadius(record.encounter.miss_distance_km);
  const d = directionToScene(record.illustrative_direction);
  return { x: d.x * r, y: d.y * r, z: d.z * r };
}
