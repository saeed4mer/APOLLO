import type { WorldRecord } from "../src/models/world";
import { directionToScene, scenePosition } from "../src/scene/coordinates";
import { distanceKmAtRadius, EARTH_RADIUS_KM, EARTH_VISUAL_RADIUS, visualRadius } from "../src/scene/scale";
import {
  clampDistance, INITIAL_CAMERA_DISTANCE, MAX_CAMERA_DISTANCE, MIN_CAMERA_DISTANCE, ZoomController,
} from "../src/scene/zoom";
import { fixture } from "./helpers";

const records = (): WorldRecord[] => fixture("world.json").data;

describe("distance scale", () => {
  it("preserves the order of the spec example (100,000 < 500,000 < 5,000,000 km)", () => {
    const [a, b, c] = [1e5, 5e5, 5e6].map(visualRadius);
    expect(a!).toBeLessThan(b!);
    expect(b!).toBeLessThan(c!);
  });

  it("preserves the near/far order of every real asteroid", () => {
    const sorted = [...records()].sort((x, y) => x.encounter.miss_distance_km - y.encounter.miss_distance_km);
    const radii = sorted.map((r) => visualRadius(r.encounter.miss_distance_km));
    for (let i = 1; i < radii.length; i++) expect(radii[i]!).toBeGreaterThan(radii[i - 1]!);
  });

  it("places Earth's radius on the Earth placeholder surface and inverts exactly", () => {
    expect(visualRadius(EARTH_RADIUS_KM)).toBeCloseTo(EARTH_VISUAL_RADIUS, 12);
    for (const km of [7000, 384_400, 1.9e6, 7.5e7]) expect(distanceKmAtRadius(visualRadius(km))).toBeCloseTo(km, 3);
  });

  it("never yields a negative radius and rejects non-physical distances", () => {
    expect(visualRadius(1)).toBe(0);
    expect(() => visualRadius(0)).toThrow(RangeError);
    expect(() => visualRadius(NaN)).toThrow(RangeError);
    expect(() => visualRadius(Infinity)).toThrow(RangeError);
  });
});

describe("coordinates", () => {
  it("uses the served illustrative_direction unchanged (identity mapping)", () => {
    for (const record of records()) expect(directionToScene(record.illustrative_direction)).toEqual(record.illustrative_direction);
  });

  it("is deterministic: the same record always maps to the same position", () => {
    const record = records()[0]!;
    const first = scenePosition(record);
    for (let i = 0; i < 100; i++) expect(scenePosition(structuredClone(record))).toEqual(first);
  });

  it("places each asteroid along its served direction at its scaled real distance", () => {
    for (const record of records()) {
      const p = scenePosition(record);
      const r = Math.hypot(p.x, p.y, p.z);
      expect(r).toBeCloseTo(visualRadius(record.encounter.miss_distance_km), 9);
      expect(p.x / r).toBeCloseTo(record.illustrative_direction.x, 9);
      expect(p.y / r).toBeCloseTo(record.illustrative_direction.y, 9);
      expect(p.z / r).toBeCloseTo(record.illustrative_direction.z, 9);
    }
  });

  it("does not depend on asteroid_key (resolution can change without moving the marker)", () => {
    const record = records()[0]!;
    expect(scenePosition({ ...record, asteroid_key: "ast_changed" })).toEqual(scenePosition(record));
  });
});

describe("zoom", () => {
  it("starts inside the bounds, outside the Earth placeholder", () => {
    const zoom = new ZoomController();
    expect(zoom.currentDistance).toBe(INITIAL_CAMERA_DISTANCE);
    expect(MIN_CAMERA_DISTANCE).toBeGreaterThan(EARTH_VISUAL_RADIUS);
  });

  it("wheel up zooms in, wheel down zooms out", () => {
    const zoom = new ZoomController();
    zoom.applyWheel(-100);
    expect(zoom.targetDistance).toBeLessThan(INITIAL_CAMERA_DISTANCE);
    const zoomedIn = zoom.targetDistance;
    zoom.applyWheel(100);
    zoom.applyWheel(100);
    expect(zoom.targetDistance).toBeGreaterThan(zoomedIn);
  });

  it("clamps at both bounds under 1,000 rapid wheel events in each direction", () => {
    const zoom = new ZoomController();
    for (let i = 0; i < 1000; i++) zoom.applyWheel(-5000);
    expect(zoom.targetDistance).toBe(MIN_CAMERA_DISTANCE);
    for (let i = 0; i < 1000; i++) zoom.applyWheel(5000, 2);
    expect(zoom.targetDistance).toBe(MAX_CAMERA_DISTANCE);
  });

  it("ignores NaN and Infinity input and stays finite", () => {
    const zoom = new ZoomController();
    for (const bad of [NaN, Infinity, -Infinity]) {
      zoom.applyWheel(bad);
      zoom.setTarget(bad);
      zoom.step(bad);
    }
    expect(zoom.targetDistance).toBe(INITIAL_CAMERA_DISTANCE);
    expect(Number.isFinite(zoom.currentDistance)).toBe(true);
  });

  it("eases toward the target, settles exactly, then reports no further change (no loop)", () => {
    const zoom = new ZoomController();
    zoom.applyWheel(-240);
    let frames = 0;
    while (zoom.step(16)) {
      frames++;
      expect(zoom.currentDistance).toBeGreaterThanOrEqual(MIN_CAMERA_DISTANCE);
      expect(zoom.currentDistance).toBeLessThanOrEqual(MAX_CAMERA_DISTANCE);
      if (frames > 500) throw new Error("zoom never settled");
    }
    expect(zoom.currentDistance).toBe(zoom.targetDistance);
    expect(zoom.step(16)).toBe(false);
  });

  it("bounds a single wheel burst (no uncontrolled acceleration)", () => {
    const zoom = new ZoomController();
    zoom.applyWheel(1e9);
    expect(zoom.targetDistance / INITIAL_CAMERA_DISTANCE).toBeLessThan(1.5);
  });

  it("clampDistance keeps every value inside the bounds", () => {
    expect(clampDistance(0)).toBe(MIN_CAMERA_DISTANCE);
    expect(clampDistance(1e9)).toBe(MAX_CAMERA_DISTANCE);
  });
});
