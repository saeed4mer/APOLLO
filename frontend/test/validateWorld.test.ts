import { ApiError } from "../src/api/errors";
import { validateWorldResponse } from "../src/api/validateWorld";
import { fixture } from "./helpers";

const realWorld = () => fixture("world.json");

describe("validateWorldResponse — real contract", () => {
  it("accepts every record of the real 35-object world unchanged", () => {
    const body = realWorld();
    const world = validateWorldResponse(body);
    expect(world.received).toBe(35);
    expect(world.records).toHaveLength(35);
    expect(world.rejected).toEqual([]);
    expect(world.snapshot.spatial_model.direction_algorithm).toBe("sha256-uniform-sphere-v1");
    // No reinterpretation: validated records are exactly the served records.
    expect(world.records).toEqual(body.data);
  });

  it("preserves null, false and true as distinct PHA states", () => {
    const body = realWorld();
    body.data[0].encounter.is_potentially_hazardous = null;
    body.data[1].encounter.is_potentially_hazardous = false;
    body.data[2].encounter.is_potentially_hazardous = true;
    const { records } = validateWorldResponse(body);
    expect(records.slice(0, 3).map((r) => r.encounter.is_potentially_hazardous)).toEqual([null, false, true]);
  });

  it("keeps optional NeoWs fields null rather than defaulting them", () => {
    const body = realWorld();
    Object.assign(body.data[0].encounter, {
      relative_velocity_km_s: null, estimated_diameter_min_km: null, estimated_diameter_max_km: null, close_approach_datetime: null,
    });
    const record = validateWorldResponse(body).records[0]!;
    expect(record.encounter.relative_velocity_km_s).toBeNull();
    expect(record.encounter.estimated_diameter_min_km).toBeNull();
    expect(record.encounter.close_approach_datetime).toBeNull();
  });
});

describe("validateWorldResponse — corrupted records are excluded, not repaired", () => {
  const cases: [string, (r: any) => void, RegExp][] = [
    ["missing neows_id", (r) => delete r.neows_id, /neows_id/],
    ["malformed neows_id", (r) => (r.neows_id = "07"), /neows_id/],
    ["zero miss distance", (r) => (r.encounter.miss_distance_km = 0), /miss_distance_km/],
    ["infinite miss distance", (r) => (r.encounter.miss_distance_km = Infinity), /miss_distance_km/],
    ["string miss distance", (r) => (r.encounter.miss_distance_km = "1000"), /miss_distance_km/],
    ["NaN velocity", (r) => (r.encounter.relative_velocity_km_s = NaN), /relative_velocity_km_s/],
    ["non-boolean PHA", (r) => (r.encounter.is_potentially_hazardous = "false"), /is_potentially_hazardous/],
    ["unknown sentry status", (r) => (r.sentry.status = "maybe"), /sentry.status/],
    ["unknown sbdb status", (r) => (r.sbdb.status = "partial"), /sbdb.status/],
    ["missing direction", (r) => delete r.illustrative_direction, /illustrative_direction/],
    ["non-finite direction", (r) => (r.illustrative_direction.x = NaN), /non-finite/],
    ["non-unit direction", (r) => (r.illustrative_direction = { x: 2, y: 0, z: 0 }), /unit vector/],
    ["zero direction", (r) => (r.illustrative_direction = { x: 0, y: 0, z: 0 }), /unit vector/],
  ];

  it.each(cases)("%s", (_name, corrupt, reason) => {
    const body = realWorld();
    const victim = body.data[5].neows_id;
    corrupt(body.data[5]);
    const world = validateWorldResponse(body);
    expect(world.records).toHaveLength(34);
    expect(world.records.map((r) => r.neows_id)).not.toContain(body.data[5].neows_id ?? "__none__");
    expect(world.rejected).toHaveLength(1);
    expect(world.rejected[0]!.index).toBe(5);
    expect(world.rejected[0]!.reason).toMatch(reason);
    if (typeof body.data[5].neows_id === "string" && body.data[5].neows_id === victim) {
      expect(world.rejected[0]!.neows_id).toBe(victim);
    }
  });

  it("excludes every copy of a duplicated neows_id", () => {
    const body = realWorld();
    const duplicate = structuredClone(body.data[3]);
    duplicate.illustrative_direction = { x: 0, y: 1, z: 0 };
    body.data.push(duplicate);
    const world = validateWorldResponse(body);
    expect(world.records).toHaveLength(34);
    expect(world.records.some((r) => r.neows_id === duplicate.neows_id)).toBe(false);
    expect(world.rejected.map((r) => r.reason)).toEqual(["duplicate neows_id", "duplicate neows_id"]);
  });
});

describe("validateWorldResponse — broken envelope fails the whole load", () => {
  it.each([
    ["not an object", null],
    ["missing data", { world: {} }],
    ["missing world", { data: [] }],
  ])("%s", (_name, body) => {
    expect(() => validateWorldResponse(body)).toThrow(ApiError);
  });

  it("refuses a spatial model this renderer does not know how to interpret", () => {
    const body = realWorld();
    body.world.spatial_model.direction_algorithm = "sha256-uniform-sphere-v2";
    expect(() => validateWorldResponse(body)).toThrow(/Unsupported direction algorithm/);
  });

  it("refuses non-illustrative direction semantics", () => {
    const body = realWorld();
    body.world.spatial_model.direction_semantics = "astronomical";
    expect(() => validateWorldResponse(body)).toThrow(ApiError);
  });
});
