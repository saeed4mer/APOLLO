import { validateProfileResponse } from "../src/api/validateProfile";
import type { WorldRecord } from "../src/models/world";
import { fieldOpacity, labelOpacity, SKY_STOPS, skyColors, starOpacity } from "../src/scene/atmosphere";
import { ExplorationController, MAX_WHEEL_DELTA, PROGRESS_PER_100PX } from "../src/scene/exploration";
import {
  altitudePx, computeLayout, DEFAULT_DOMAIN, distanceFraction, FRONTIER_FRACTION, MIN_ALTITUDE_PX, restPosition,
  skyHorizontal, surfaceY, type DistanceView,
} from "../src/scene/skyLayout";
import { buildCallouts } from "../src/ui/callouts";
import { labelBox, overlaps } from "../src/ui/LabelLayer";
import { fixture } from "./helpers";

const records = (): WorldRecord[] => fixture("world.json").data;
const withDistance = (base: WorldRecord, id: string, km: number): WorldRecord => ({
  ...structuredClone(base), neows_id: id, encounter: { ...base.encounter, miss_distance_km: km },
});
const VIEWPORTS: [number, number][] = [[1280, 720], [1920, 1080], [2560, 1440], [3840, 2160], [900, 1200]];
/** Frontiers spanning the whole journey: before the Moon, the Moon, the early field, deep space. */
const FRONTIERS = [0, 5e4, 384_400, 1e6, 5e6, 23_417_892, 5e7, 1e8];
const view = (frontierKm: number): DistanceView => ({ domain: DEFAULT_DOMAIN, frontierKm });

describe("distance ordering is preserved in the sky (non-negotiable)", () => {
  it("controlled example: 5M < 15M < 50M km rest lower-to-higher above the arc, for every frontier", () => {
    const base = records()[0]!;
    const [a, b, c] = [5e6, 15e6, 50e6].map((km, i) => withDistance(base, `1${i}`, km));
    for (const [w, h] of VIEWPORTS) {
      for (const p of [0, 0.5, 1]) {
        for (const f of FRONTIERS) {
          const layout = computeLayout(w, h, p);
          const [ra, rb, rc] = [a, b, c].map((r) => restPosition(layout, r!, view(f)));
          expect(ra!.altitude).toBeLessThan(rb!.altitude);
          expect(rb!.altitude).toBeLessThan(rc!.altitude);
        }
      }
    }
  });

  it("every real asteroid: altitude is strictly ordered by real miss distance, at every frontier, progress and aspect", () => {
    const sorted = [...records()].sort((x, y) => x.encounter.miss_distance_km - y.encounter.miss_distance_km);
    for (const [w, h] of VIEWPORTS) {
      for (const p of [0, 0.25, 0.75, 1]) {
        for (const f of FRONTIERS) {
          const layout = computeLayout(w, h, p);
          const alts = sorted.map((r) => restPosition(layout, r, view(f)).altitude);
          for (let i = 1; i < alts.length; i++) expect(alts[i]!).toBeGreaterThan(alts[i - 1]!);
        }
      }
    }
  });

  it("the mapping is strictly monotonic across the frontier and bounded in [0, 1)", () => {
    for (const f of FRONTIERS) {
      let last = -Infinity;
      for (let km = 1000; km < 3e8; km *= 1.07) {
        const frac = distanceFraction(km, view(f));
        expect(frac).toBeGreaterThan(last);
        expect(frac).toBeGreaterThanOrEqual(0);
        expect(frac).toBeLessThan(1);
        last = frac;
      }
    }
    expect(distanceFraction(23_417_892, view(23_417_892))).toBeCloseTo(FRONTIER_FRACTION, 12); // frontier height is fixed
    expect(() => distanceFraction(0, view(1e6))).toThrow(RangeError);
    expect(() => distanceFraction(NaN, view(1e6))).toThrow(RangeError);
    expect(altitudePx(computeLayout(1000, 800, 0), 1, view(1e6))).toBeCloseTo(MIN_ALTITUDE_PX, 3);
  });

  it("altitude is measured from the surface directly below, with the same range at every x", () => {
    const layout = computeLayout(1600, 900, 0.3);
    for (const record of records()) {
      const rest = restPosition(layout, record, view(5e7));
      expect(rest.y - surfaceY(layout, rest.x)).toBeCloseTo(altitudePx(layout, record.encounter.miss_distance_km, view(5e7)), 9);
    }
  });

  it("positions do not depend on the rest of the population (adding asteroids moves nobody)", () => {
    const layout = computeLayout(1400, 860, 0.4);
    const one = records()[3]!;
    const alone = restPosition(layout, one, view(3e7));
    const extra = [...records(), withDistance(one, "999999999", 1e4)];
    expect(restPosition(layout, extra[3]!, view(3e7))).toEqual(alone);
  });
});

describe("direction is consumed, never regenerated", () => {
  it("horizontal position is the longitude of the served vector, deterministic and asteroid_key independent", () => {
    const layout = computeLayout(1400, 860, 0);
    for (const record of records()) {
      const d = record.illustrative_direction;
      expect(skyHorizontal(d)).toBe(Math.atan2(d.y, d.x) / Math.PI);
      expect(restPosition(layout, { ...record, asteroid_key: "ast_changed" }, view(1e8))).toEqual(restPosition(layout, record, view(1e8)));
      expect(restPosition(layout, structuredClone(record), view(1e8))).toEqual(restPosition(layout, record, view(1e8)));
    }
  });

  it("asteroids stay inside the viewport horizontally", () => {
    for (const [w, h] of VIEWPORTS) {
      const layout = computeLayout(w, h, 0);
      for (const record of records()) {
        const { x } = restPosition(layout, record, view(1e8));
        expect(x).toBeGreaterThan(0);
        expect(x).toBeLessThan(w);
      }
    }
  });
});

describe("Earth composition stays grounded across aspect ratios", () => {
  it("the crest sits in the lower third and sinks as the user rises", () => {
    for (const [w, h] of VIEWPORTS) {
      const start = computeLayout(w, h, 0);
      const end = computeLayout(w, h, 1);
      expect(start.earthTopY / h).toBeCloseTo(0.3, 6);
      expect(end.earthTopY).toBeLessThan(start.earthTopY);
      expect(surfaceY(start, w / 2)).toBeCloseTo(start.earthTopY, 6);
      expect(surfaceY(start, 0)).toBeLessThan(start.earthTopY); // the arc curves down toward the edges
    }
  });
});

describe("exploration progress", () => {
  it("wheel down goes deeper, wheel up returns; one notch moves a bounded amount", () => {
    const x = new ExplorationController();
    x.applyWheel(100);
    expect(x.targetProgress).toBeCloseTo(PROGRESS_PER_100PX, 9);
    x.applyWheel(1e9);
    expect(x.targetProgress).toBeCloseTo(PROGRESS_PER_100PX * (1 + MAX_WHEEL_DELTA / 100), 9);
    x.applyWheel(-1e9);
    x.applyWheel(-1e9);
    expect(x.targetProgress).toBe(0);
  });

  it("rapid up/down x40 stays inside [0, 1], finite, and settles exactly", () => {
    const x = new ExplorationController();
    for (let i = 0; i < 40; i++) {
      for (let k = 0; k < 20; k++) x.applyWheel(i % 2 ? -240 : 240);
      for (let f = 0; f < 3; f++) x.step(16);
      expect(x.currentProgress).toBeGreaterThanOrEqual(0);
      expect(x.currentProgress).toBeLessThanOrEqual(1);
    }
    let frames = 0;
    while (x.step(16)) if (++frames > 1000) throw new Error("never settled");
    expect(x.currentProgress).toBe(x.targetProgress);
    expect(x.step(16)).toBe(false);
  });

  it("ignores NaN/Infinity and follows the target both ways", () => {
    const x = new ExplorationController();
    for (const bad of [NaN, Infinity, -Infinity]) {
      x.applyWheel(bad);
      x.setTarget(bad);
      x.step(bad);
    }
    expect(x.targetProgress).toBe(0);
    x.setTarget(0.7);
    while (x.step(16));
    x.setTarget(0.2);
    while (x.step(16));
    expect(x.currentProgress).toBe(0.2);
  });
});

describe("sky -> space transition is continuous", () => {
  const luminance = (hex: string) => [1, 3, 5].reduce((sum, i, k) => sum + parseInt(hex.slice(i, i + 2), 16) * [0.2126, 0.7152, 0.0722][k]!, 0);

  it("starts at the sky stop and ends at deep space", () => {
    expect(skyColors(0).zenith).toBe(SKY_STOPS[0]!.zenith);
    expect(skyColors(1).zenith).toBe(SKY_STOPS.at(-1)!.zenith);
  });

  it("no jumps: 1,000 steps never change any channel by more than 2 levels", () => {
    let previous = skyColors(0);
    for (let i = 1; i <= 1000; i++) {
      const next = skyColors(i / 1000);
      for (const key of ["zenith", "horizon"] as const) {
        for (const at of [1, 3, 5]) {
          expect(Math.abs(parseInt(next[key].slice(at, at + 2), 16) - parseInt(previous[key].slice(at, at + 2), 16))).toBeLessThanOrEqual(2);
        }
      }
      previous = next;
    }
  });

  it("the zenith darkens monotonically toward space; overlays fade in smoothly", () => {
    let last = Infinity;
    for (let i = 0; i <= 100; i++) {
      const l = luminance(skyColors(i / 100).zenith);
      expect(l).toBeLessThanOrEqual(last + 1e-9);
      last = l;
    }
    expect(starOpacity(0)).toBe(0);
    expect(starOpacity(1)).toBe(1);
    expect(fieldOpacity(0)).toBe(0);
    expect(labelOpacity(0.3)).toBe(0);
    expect(skyColors(NaN)).toEqual(skyColors(0));
  });
});

describe("labels never overlap", () => {
  it("overlap detection", () => {
    const a = labelBox(100, 100, "(2010 TW54)", "17,457,205 km");
    expect(overlaps(a, labelBox(110, 110, "(2008 ST)", null))).toBe(true);
    expect(overlaps(a, labelBox(400, 100, "(2008 ST)", null))).toBe(false);
  });
});

describe("focus callouts", () => {
  it("linked object: only real values are listed, each callout source-labelled", () => {
    const model = buildCallouts(validateProfileResponse(fixture("profile_3548666.json")));
    expect(model.callouts.map((c) => c.key)).toEqual(["identity", "encounter", "orbit", "sentry_assessment", "neows_physical", "physical"]);
    expect(model.callouts.map((c) => c.source)).toEqual([
      "Identity resolution + crosswalk", "NASA NeoWs", "JPL SBDB", "JPL Sentry", "NASA NeoWs", "JPL SBDB",
    ]);
    for (const c of model.callouts) for (const row of c.rows) expect(row.value).not.toMatch(/^(Unavailable|Unknown|XX)/);
    const physical = model.callouts.find((c) => c.key === "physical")!;
    expect(physical.note).toBe("3 more fields: not provided by the source");
    expect(model.unavailable).toEqual([]);
  });

  it("unresolved object: SBDB and Sentry collapse to one line each with the contract's reason", () => {
    const model = buildCallouts(validateProfileResponse(fixture("profile_3830890.json")));
    expect(model.callouts.map((c) => c.key)).toEqual(["identity", "encounter", "neows_physical"]);
    expect(model.unavailable).toEqual([
      { title: "Orbit", source: "JPL SBDB", reason: "identity not resolved, so this source cannot be linked" },
      { title: "Sentry", source: "JPL Sentry", reason: "Not linkable: identity not resolved" },
      { title: "Physical", source: "JPL SBDB", reason: "identity not resolved, so this source cannot be linked" },
    ]);
  });

  it("Sentry callout requires crosswalk linkage, never the PHA flag", () => {
    const body = fixture("profile_3830890.json");
    body.data.encounter.is_potentially_hazardous = true;
    body.data.encounter.is_sentry_object = true;
    const model = buildCallouts(validateProfileResponse(body));
    expect(model.callouts.some((c) => c.key === "sentry_assessment")).toBe(false);
    expect(model.unavailable.find((u) => u.title === "Sentry")?.reason).toBe("Not linkable: identity not resolved");
  });
});
