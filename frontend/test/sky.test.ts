import { validateProfileResponse } from "../src/api/validateProfile";
import type { WorldRecord } from "../src/models/world";
import { fieldOpacity, labelOpacity, SKY_STOPS, skyColors, starOpacity } from "../src/scene/atmosphere";
import { ExplorationController, MAX_WHEEL_DELTA, PROGRESS_PER_100PX } from "../src/scene/exploration";
import {
  altitudePx, computeLayout, distanceAtAltitude, FRONTIER_SCREEN_FRACTION, MIN_MILLION_PX, MOON_DISTANCE_KM, restPosition,
  skyHorizontal, surfaceY, travelPx,
} from "../src/scene/skyLayout";
import { buildCallouts } from "../src/ui/callouts";
import { labelBox, overlaps } from "../src/ui/LabelLayer";
import { fixture } from "./helpers";

const records = (): WorldRecord[] => fixture("world.json").data;
const withDistance = (base: WorldRecord, id: string, km: number): WorldRecord => ({
  ...structuredClone(base), neows_id: id, encounter: { ...base.encounter, miss_distance_km: km },
});
const VIEWPORTS: [number, number][] = [[1280, 720], [1920, 1080], [2560, 1440], [3840, 2160], [900, 1200], [700, 500]];

describe("the distance world: ordering and spacing (non-negotiable)", () => {
  it("controlled example: 5M < 15M < 50M km rest lower-to-higher above the arc on every viewport", () => {
    const base = records()[0]!;
    const [a, b, c] = [5e6, 15e6, 50e6].map((km, i) => withDistance(base, `1${i}`, km));
    for (const [w, h] of VIEWPORTS) {
      const layout = computeLayout(w, h);
      const [ra, rb, rc] = [a, b, c].map((r) => restPosition(layout, r!));
      expect(ra!.altitude).toBeLessThan(rb!.altitude);
      expect(rb!.altitude).toBeLessThan(rc!.altitude);
    }
  });

  it("every real asteroid: world height is strictly ordered by real miss distance on every viewport", () => {
    const sorted = [...records()].sort((x, y) => x.encounter.miss_distance_km - y.encounter.miss_distance_km);
    for (const [w, h] of VIEWPORTS) {
      const layout = computeLayout(w, h);
      const alts = sorted.map((r) => restPosition(layout, r).altitude);
      for (let i = 1; i < alts.length; i++) expect(alts[i]!).toBeGreaterThan(alts[i - 1]!);
    }
  });

  it("the mapping is strictly monotonic from the surface to beyond 100M km, and invertible", () => {
    for (const [w, h] of VIEWPORTS) {
      const layout = computeLayout(w, h);
      let last = -Infinity;
      for (let km = 100; km < 3e8; km *= 1.05) {
        const alt = altitudePx(layout, km);
        expect(alt).toBeGreaterThan(last);
        expect(distanceAtAltitude(layout, alt) / km).toBeCloseTo(1, 9);
        last = alt;
      }
    }
    expect(() => altitudePx(computeLayout(1000, 800), 0)).toThrow(RangeError);
    expect(() => altitudePx(computeLayout(1000, 800), NaN)).toThrow(RangeError);
  });

  it("every million km has the same, real spacing in the field; 47,382,615 km sits 38.2615% of the way from 47M to 48M", () => {
    for (const [w, h] of VIEWPORTS) {
      const layout = computeLayout(w, h);
      expect(layout.millionPx).toBeGreaterThanOrEqual(MIN_MILLION_PX);
      for (let m = 1; m < 100; m++) expect(altitudePx(layout, (m + 1) * 1e6) - altitudePx(layout, m * 1e6)).toBeCloseTo(layout.millionPx, 6);
      const [a47, a48, x] = [47e6, 48e6, 47_382_615].map((km) => altitudePx(layout, km));
      expect((x! - a47!) / (a48! - a47!)).toBeCloseTo(0.382615, 9);
      // The field is far taller than a screen: about seven million-km levels per viewport.
      expect(h / layout.millionPx).toBeLessThan(11);
      expect(altitudePx(layout, 1e8) / h).toBeGreaterThan(9);
    }
  });

  it("altitude is measured from the surface directly below, with the same range at every x", () => {
    const layout = computeLayout(1600, 900);
    for (const record of records()) {
      const rest = restPosition(layout, record);
      expect(rest.y - surfaceY(layout, rest.x)).toBeCloseTo(altitudePx(layout, record.encounter.miss_distance_km), 9);
    }
  });

  it("positions do not depend on the rest of the population (adding asteroids moves nobody)", () => {
    const layout = computeLayout(1400, 860);
    const one = records()[3]!;
    const alone = restPosition(layout, one);
    const extra = [...records(), withDistance(one, "999999999", 1e4)];
    expect(restPosition(layout, extra[3]!)).toEqual(alone);
  });
});

describe("camera travel through the distance world", () => {
  it("no travel at the top or in the sky; then the frontier is held at a fixed screen height", () => {
    for (const [w, h] of VIEWPORTS) {
      const layout = computeLayout(w, h);
      expect(travelPx(layout, 0)).toBe(0);
      expect(travelPx(layout, 50_000)).toBe(0); // still in the first sky: the Earth does not move
      for (const km of [2e6, 23_417_892, 5e7, 1e8]) {
        const screenY = layout.earthTopY + altitudePx(layout, km) - travelPx(layout, km);
        expect(screenY).toBeCloseTo(FRONTIER_SCREEN_FRACTION * h, 6);
      }
    }
  });

  it("travel is monotonic in the revealed distance (so scrolling back brings everything back)", () => {
    const layout = computeLayout(1400, 860);
    let last = -1;
    for (let km = 1000; km < 1.2e8; km *= 1.03) {
      const t = travelPx(layout, km);
      expect(t).toBeGreaterThanOrEqual(last);
      last = t;
    }
  });

  it("the Earth is in view up to the Moon, and has left the viewport by 3M km", () => {
    for (const [w, h] of VIEWPORTS) {
      const layout = computeLayout(w, h);
      expect(layout.earthTopY - travelPx(layout, MOON_DISTANCE_KM)).toBeGreaterThan(0.2 * h); // crest still well on screen
      expect(layout.earthTopY - travelPx(layout, 3e6)).toBeLessThan(0); // crest below the bottom edge
    }
  });
});

describe("direction is consumed, never regenerated", () => {
  it("horizontal position is the longitude of the served vector, deterministic and asteroid_key independent", () => {
    const layout = computeLayout(1400, 860);
    for (const record of records()) {
      const d = record.illustrative_direction;
      expect(skyHorizontal(d)).toBe(Math.atan2(d.y, d.x) / Math.PI);
      expect(restPosition(layout, { ...record, asteroid_key: "ast_changed" })).toEqual(restPosition(layout, record));
      expect(restPosition(layout, structuredClone(record))).toEqual(restPosition(layout, record));
    }
  });

  it("asteroids stay inside the viewport horizontally", () => {
    for (const [w, h] of VIEWPORTS) {
      const layout = computeLayout(w, h);
      for (const record of records()) {
        const { x } = restPosition(layout, record);
        expect(x).toBeGreaterThan(0);
        expect(x).toBeLessThan(w);
      }
    }
  });
});

describe("Earth composition", () => {
  it("the crest sits in the lower third of the first screen; the arc curves down toward the edges", () => {
    for (const [w, h] of VIEWPORTS) {
      const layout = computeLayout(w, h);
      expect(layout.earthTopY / h).toBeCloseTo(0.3, 6);
      expect(surfaceY(layout, w / 2)).toBeCloseTo(layout.earthTopY, 6);
      expect(surfaceY(layout, 0)).toBeLessThan(layout.earthTopY);
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
    expect(labelOpacity(0.2)).toBe(0); // no asteroid labels before the field
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
