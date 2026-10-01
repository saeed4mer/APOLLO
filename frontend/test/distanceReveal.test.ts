import * as THREE from "three";
import { validateProfileResponse } from "../src/api/validateProfile";
import { validateWorldResponse } from "../src/api/validateWorld";
import { createApp } from "../src/app";
import type { AsteroidProfile } from "../src/models/profile";
import type { WorldRecord } from "../src/models/world";
import { ROCK_PX, WorldRenderer } from "../src/renderer/WorldRenderer";
import { GUIDE_BASE, guideOpacity, moonOpacity, skyColors, starOpacity } from "../src/scene/atmosphere";
import { MAX_WHEEL_DELTA, PROGRESS_PER_100PX } from "../src/scene/exploration";
import { FALL_MS, RETREAT_MS, RevealAnimator, revealOrder, type AsteroidPhase } from "../src/scene/reveal";
import {
  altitudePx, DEFAULT_MAX_KM, DISTANCE_MIN_KM, distanceDomain, FIELD_PROGRESS, frontierLabelKm, guideDistances, guideTier,
  isRevealed, MOON_DISTANCE_KM, MOON_PROGRESS, progressForDistance, restPosition, revealedDistanceKm, SCALE_STEP_KM,
} from "../src/scene/skyLayout";
import { chooseGuideLabels, formatScaleKm, guideLabelCandidates, MAX_GUIDE_LABELS } from "../src/ui/LabelLayer";
import { FakeGL, fixture, flushPromises, installFakeRaf, installFakeResizeObserver } from "./helpers";

/**
 * M7.2 correction passes: the distance-driven, reversible asteroid reveal, and the staged journey
 * (daytime sky -> night + Moon -> million-km distance field -> deep space).
 * Controlled populations use the real fixture record shape with only distance / id / PHA changed.
 */
let raf: ReturnType<typeof installFakeRaf>;

beforeEach(() => {
  raf = installFakeRaf();
  installFakeResizeObserver();
  window.location.hash = "#/";
});

const fixtureRecords = (): WorldRecord[] => validateWorldResponse(fixture("world.json")).records;
const base = (): WorldRecord => fixtureRecords()[0]!;
function record(id: string, km: number, pha: boolean | null = false): WorldRecord {
  const b = structuredClone(base());
  return { ...b, neows_id: id, encounter: { ...b.encounter, miss_distance_km: km, is_potentially_hazardous: pha } };
}
/** The spec's controlled distances, deliberately supplied out of order. */
const CONTROLLED: [string, number][] = [["4700", 47e6], ["0500", 5e5], ["9200", 92e6], ["0200", 2e6], ["1200", 12e6]];
const controlled = (): WorldRecord[] => CONTROLLED.map(([id, km]) => record(id, km));

const frames = (n: number, dt = 16) => {
  for (let i = 0; i < n; i++) raf.frame(dt);
};
const SETTLE = Math.ceil(Math.max(FALL_MS, RETREAT_MS) / 16) + 60; // easing + the longest animation

function sizedHost(width = 1600, height = 900): HTMLElement {
  const host = document.createElement("div");
  Object.defineProperty(host, "clientWidth", { value: width });
  Object.defineProperty(host, "clientHeight", { value: height });
  return host;
}

function newRenderer(records: WorldRecord[]) {
  const renderer = new WorldRenderer(sizedHost(), { createGLRenderer: () => new FakeGL(), onHover: vi.fn(), onClick: vi.fn() });
  renderer.setRecords(records);
  renderer.start();
  const phases = () => new Map(records.map((r) => [r.neows_id, renderer.phaseOf(r.neows_id)!]));
  /** Travel to the progress whose revealed distance is `km`, then let everything settle. */
  const travelTo = (km: number) => {
    renderer.exploration.setTarget(km <= 0 ? 0 : progressForDistance(km, renderer.distanceDomain));
    frames(SETTLE);
  };
  return { renderer, phases, travelTo };
}

/** Each id was first seen on a frame no earlier than the id before it (same-frame ties allowed). */
function expectOrdered(frameOf: Map<string, number>, order: string[]) {
  expect([...frameOf.keys()].sort()).toEqual([...order].sort());
  for (let i = 1; i < order.length; i++) expect(frameOf.get(order[i]!)!).toBeGreaterThanOrEqual(frameOf.get(order[i - 1]!)!);
}

/** Every rendered position is finite and nothing is drawn while HIDDEN. */
function expectConsistent(renderer: WorldRenderer, records: WorldRecord[]) {
  expect(Number.isFinite(renderer.revealedKm)).toBe(true);
  for (const r of records) {
    const phase = renderer.phaseOf(r.neows_id);
    const p = renderer.screenPositionOf(r.neows_id);
    if (phase === "HIDDEN") expect(p).toBeNull();
    else expect(Number.isFinite(p!.x) && Number.isFinite(p!.y)).toBe(true);
    // At rest, eligibility is exactly "exact miss distance <= revealed distance".
    if (phase === "SETTLED" || phase === "HIDDEN") expect(phase === "SETTLED").toBe(isRevealed(r.encounter.miss_distance_km, renderer.revealedKm));
  }
}

describe("A. initial state: nothing falls before the user scrolls", () => {
  it("at load, revealed distance is 0 km and every asteroid stays HIDDEN, however long the page sits", () => {
    const records = fixtureRecords();
    const { renderer, phases } = newRenderer(records);
    for (let i = 0; i < 10; i++) {
      frames(60);
      expect(renderer.revealedKm).toBe(0);
      expect([...phases().values()].every((p) => p === "HIDDEN")).toBe(true);
      expect(records.every((r) => renderer.screenPositionOf(r.neows_id) === null)).toBe(true);
    }
    expect(revealedDistanceKm(0, renderer.distanceDomain)).toBe(0);
    renderer.dispose();
  });
});

describe("B/F. controlled distances reveal closest first, at their own distance", () => {
  it("500,000 / 2M / 12M / 47M / 92M km appear in that order, each exactly when the frontier reaches it", () => {
    const records = controlled();
    const { renderer, phases } = newRenderer(records);
    expect(revealOrder(records)).toEqual(["0500", "0200", "1200", "4700", "9200"]);

    const firstSeen = new Map<string, number>();
    renderer.exploration.setTarget(1);
    for (let f = 0; f < 600; f++) {
      frames(1);
      for (const [id, phase] of phases()) {
        if (phase !== "HIDDEN" && !firstSeen.has(id)) {
          firstSeen.set(id, f);
          const km = records.find((r) => r.neows_id === id)!.encounter.miss_distance_km;
          expect(renderer.revealedKm).toBeGreaterThanOrEqual(km); // never before its distance is reached
        }
      }
    }
    expectOrdered(firstSeen, ["0500", "0200", "1200", "4700", "9200"]);
    expect([...phases().values()].every((p) => p === "SETTLED")).toBe(true);
    renderer.dispose();
  });

  it("reveal order is closest first with ties broken by neows_id, independent of input order", () => {
    const tie = [record("300", 5e6), record("100", 5e6), record("200", 1e6)];
    expect(revealOrder(tie)).toEqual(["200", "100", "300"]);
    expect(revealOrder([...tie].reverse())).toEqual(["200", "100", "300"]);
    const real = fixtureRecords();
    const order = revealOrder(real);
    const km = new Map(real.map((r) => [r.neows_id, r.encounter.miss_distance_km]));
    for (let i = 1; i < order.length; i++) expect(km.get(order[i]!)!).toBeGreaterThanOrEqual(km.get(order[i - 1]!)!);
  });

  it("revealed distance is deterministic, monotonic, finite and bounded by the domain", () => {
    const domain = distanceDomain(fixtureRecords());
    let last = -1;
    for (let i = 0; i <= 1000; i++) {
      const km = revealedDistanceKm(i / 1000, domain);
      expect(Number.isFinite(km)).toBe(true);
      expect(km).toBeGreaterThan(last);
      expect(km).toBeLessThanOrEqual(domain.maxKm);
      expect(revealedDistanceKm(i / 1000, domain)).toBe(km);
      last = km;
    }
    expect(revealedDistanceKm(1, domain)).toBeCloseTo(domain.maxKm, 0);
    for (const bad of [NaN, -1, Infinity]) expect(Number.isFinite(revealedDistanceKm(bad, domain))).toBe(true);
  });

  it("the domain is derived from the data and never clips a real record", () => {
    expect(distanceDomain(fixtureRecords())).toEqual({ minKm: DISTANCE_MIN_KM, maxKm: DEFAULT_MAX_KM });
    const far = distanceDomain([record("1", 3.3e8)]);
    expect(far.maxKm).toBeGreaterThanOrEqual(3.3e8 * 1.05);
    expect(progressForDistance(3.3e8, far)).toBeLessThan(1);
    for (const r of fixtureRecords()) expect(progressForDistance(r.encounter.miss_distance_km, distanceDomain(fixtureRecords()))).toBeLessThan(1);
  });
});

describe("C. an asteroid beyond the frontier stays hidden", () => {
  it("at a revealed distance of 10M km, the 47M km asteroid is HIDDEN; 500,000 and 2M km are SETTLED", () => {
    const records = controlled();
    const { renderer, phases, travelTo } = newRenderer(records);
    travelTo(10e6);
    expect(renderer.revealedKm).toBeCloseTo(10e6, -1);
    expect(Object.fromEntries(phases())).toEqual({ "4700": "HIDDEN", "0500": "SETTLED", "9200": "HIDDEN", "0200": "SETTLED", "1200": "HIDDEN" });
    expect(renderer.screenPositionOf("4700")).toBeNull();
    expectConsistent(renderer, records);
    renderer.dispose();
  });
});

describe("D. exact threshold on a non-round distance", () => {
  const KM = 23_417_892;

  it("eligibility uses the exact miss distance: hidden at 23,417,891 km, revealed at 23,417,892 km", () => {
    expect(isRevealed(KM, KM - 1)).toBe(false);
    expect(isRevealed(KM, KM)).toBe(true);
    expect(isRevealed(KM, KM - 0.001)).toBe(false);
    const animator = new RevealAnimator();
    const r = [record("1", KM)];
    animator.setRecords(r);
    for (let i = 0; i < 200; i++) animator.update(r, KM - 1, 16);
    expect(animator.phase("1")).toBe("HIDDEN");
    animator.update(r, KM, 16);
    expect(animator.phase("1")).toBe("FALLING");
    for (let i = 0; i < 200; i++) animator.update(r, KM, 16);
    expect(animator.phase("1")).toBe("SETTLED");
  });

  it("in the renderer, the asteroid appears only once the revealed distance passes its exact value", () => {
    const records = [record("23417892", KM), record("23000000", 23e6)];
    const { renderer, phases, travelTo } = newRenderer(records);
    travelTo(23_417_000); // past 23M (the rounded label value) but short of the exact distance
    expect(renderer.revealedKm).toBeLessThan(KM);
    expect(frontierLabelKm(renderer.revealedKm)).toBe(23e6);
    expect(phases().get("23417892")).toBe("HIDDEN");
    expect(phases().get("23000000")).toBe("SETTLED");
    travelTo(23_418_000);
    expect(renderer.revealedKm).toBeGreaterThanOrEqual(KM);
    expect(phases().get("23417892")).toBe("SETTLED");
    renderer.dispose();
  });
});

describe("E. resting position follows the actual miss distance", () => {
  it("settled screen position equals the rest position for the exact distance and served direction", () => {
    const records = [...controlled(), record("23417892", 23_417_892)];
    const { renderer, travelTo } = newRenderer(records);
    travelTo(1e8);
    const layout = renderer.viewLayout;
    for (const r of records) {
      const rest = restPosition(layout, r, renderer.distanceView);
      expect(renderer.restAltitudeOf(r.neows_id)).toBe(altitudePx(layout, r.encounter.miss_distance_km, renderer.distanceView));
      const p = renderer.screenPositionOf(r.neows_id)!;
      expect(p.x).toBeCloseTo(rest.x, 6);
      expect(p.y).toBeCloseTo(layout.height - rest.y, 6); // screen y grows downward
    }
    renderer.dispose();
  });

  it("distances are never rounded: 23,417,892 km rests strictly between 23,000,000 and 24,000,000 km", () => {
    const records = [record("a", 23e6), record("b", 23_417_892), record("c", 24e6)];
    const { renderer, travelTo } = newRenderer(records);
    travelTo(1e8);
    const [a, b, c] = ["a", "b", "c"].map((id) => renderer.restAltitudeOf(id)!);
    expect(a).toBeLessThan(b!);
    expect(b).toBeLessThan(c!);
    renderer.dispose();
  });
});

describe("G/H. reversible: scrolling back retreats, scrolling forward re-reveals", () => {
  it("backward from 100M to 10M km: 12M/47M/92M retreat to HIDDEN, 500,000 and 2M km stay SETTLED", () => {
    const records = controlled();
    const { renderer, phases, travelTo } = newRenderer(records);
    travelTo(1e8);
    expect([...phases().values()].every((p) => p === "SETTLED")).toBe(true);
    renderer.exploration.setTarget(progressForDistance(10e6, renderer.distanceDomain));
    const seen = new Map<string, Set<AsteroidPhase>>();
    for (let f = 0; f < SETTLE; f++) {
      frames(1);
      for (const [id, p] of phases()) (seen.get(id) ?? seen.set(id, new Set()).get(id)!).add(p);
    }
    for (const id of ["1200", "4700", "9200"]) {
      expect(seen.get(id)!.has("RETREATING")).toBe(true);
      expect(seen.get(id)!.has("FALLING")).toBe(false);
      expect(phases().get(id)).toBe("HIDDEN");
    }
    for (const id of ["0500", "0200"]) expect([...seen.get(id)!]).toEqual(["SETTLED"]);
    expectConsistent(renderer, records);
    renderer.dispose();
  });

  it("the farthest retreat first on the way back (reverse of reveal order)", () => {
    const records = controlled();
    const { renderer, phases, travelTo } = newRenderer(records);
    travelTo(1e8);
    renderer.exploration.setTarget(0);
    const leaving = new Map<string, number>();
    for (let f = 0; f < SETTLE * 2; f++) {
      frames(1);
      for (const [id, p] of phases()) if (p !== "SETTLED" && !leaving.has(id)) leaving.set(id, f);
    }
    expectOrdered(leaving, ["9200", "4700", "1200", "0200", "0500"]);
    expect([...phases().values()].every((p) => p === "HIDDEN")).toBe(true);
    renderer.dispose();
  });

  it("20 forward/backward cycles (settled and mid-animation) stay consistent: no NaN, no duplicates, one loop", () => {
    const records = [...controlled(), ...fixtureRecords()];
    const { renderer, travelTo } = newRenderer(records);
    const rocks = (renderer as unknown as { rocks: THREE.InstancedMesh }).rocks;
    for (let cycle = 0; cycle < 20; cycle++) {
      if (cycle % 2) {
        // abrupt reversals mid-animation
        for (let k = 0; k < 12; k++) {
          renderer.exploration.setTarget(k % 2 ? 0.1 : 0.95);
          frames(7);
        }
      }
      travelTo(cycle % 2 ? 1e8 : 12e6);
      expectConsistent(renderer, records);
      expect(rocks.count).toBe(records.length);
      expect(WorldRenderer.activeLoops).toBe(1);
    }
    travelTo(12e6);
    const shown = records.filter((r) => renderer.phaseOf(r.neows_id) === "SETTLED").map((r) => r.neows_id).sort();
    expect(shown).toEqual(records.filter((r) => r.encounter.miss_distance_km <= renderer.revealedKm).map((r) => r.neows_id).sort());
    renderer.dispose();
  });

  it("rock size is identical for every visible asteroid (size encodes nothing)", () => {
    const { renderer, travelTo } = newRenderer(fixtureRecords());
    travelTo(1e8);
    const rocks = (renderer as unknown as { rocks: THREE.InstancedMesh }).rocks;
    const matrix = new THREE.Matrix4();
    const scale = new THREE.Vector3();
    for (let i = 0; i < rocks.count; i++) {
      rocks.getMatrixAt(i, matrix);
      matrix.decompose(new THREE.Vector3(), new THREE.Quaternion(), scale);
      expect(scale.x).toBeCloseTo(ROCK_PX, 5);
    }
    renderer.dispose();
  });
});

describe("I. Moon landmark", () => {
  it("sits at the altitude of 384,400 km, is not an asteroid, and is labelled in the world view", async () => {
    expect(MOON_DISTANCE_KM).toBe(384_400);
    const records = fixtureRecords();
    const { renderer, travelTo } = newRenderer(records);
    travelTo(5e6);
    const layout = renderer.viewLayout;
    const moon = renderer.moonScreenPosition();
    const x = moon.x;
    const surface = layout.cy + Math.sqrt(layout.radius ** 2 - Math.min(Math.abs(x - layout.cx), layout.radius) ** 2);
    expect(renderer.moonOpacity).toBe(1);
    expect(layout.height - moon.y - surface).toBeCloseTo(altitudePx(layout, MOON_DISTANCE_KM, renderer.distanceView), 6);
    expect(records.some((r) => r.neows_id === "moon")).toBe(false);
    renderer.dispose();

    const root = document.createElement("div");
    document.body.appendChild(root);
    const app = createApp(root, {
      createGLRenderer: () => new FakeGL(),
      fetchWorld: async () => validateWorldResponse(fixture("world.json")),
      fetchProfile: async (): Promise<AsteroidProfile> => validateProfileResponse(fixture("profile_3830890.json")),
    });
    await flushPromises();
    frames(5);
    const label = root.querySelector<HTMLElement>(".moon-label")!;
    expect(label.hidden).toBe(true); // not at load
    app.renderer.exploration.setTarget(progressForDistance(2e6, app.renderer.distanceDomain));
    frames(SETTLE);
    expect(label.hidden).toBe(false);
    expect(root.querySelector(".moon-title")?.textContent).toBe("MOON DISTANCE");
    expect(root.querySelector(".moon-km")?.textContent).toBe("384,400 km");
    app.dispose();
    root.remove();
  });
});

describe("J. hazard badge = NeoWs PHA flag true only", () => {
  it("shown for true, not for false or null (Unknown is not hazardous)", () => {
    const records = [record("t", 3e6, true), record("f", 4e6, false), record("n", 5e6, null)];
    const { renderer, travelTo } = newRenderer(records);
    expect(renderer.hazardShownOf("t")).toBe(false); // hidden asteroids show no badge
    travelTo(1e8);
    expect(renderer.hazardShownOf("t")).toBe(true);
    expect(renderer.hazardShownOf("f")).toBe(false);
    expect(renderer.hazardShownOf("n")).toBe(false);
    travelTo(0);
    expect(renderer.hazardShownOf("t")).toBe(false);
    renderer.dispose();
  });

  it("the badge changes nothing else: same rock size, same rest position as a non-hazardous twin", () => {
    const a = record("a", 7e6, true);
    const b = { ...record("b", 7e6, false), illustrative_direction: a.illustrative_direction };
    const { renderer, travelTo } = newRenderer([a, b]);
    travelTo(1e8);
    expect(renderer.screenPositionOf("a")).toEqual(renderer.screenPositionOf("b"));
    renderer.dispose();
  });
});

describe("K. million-km distance guides", () => {
  const domain = { minKm: DISTANCE_MIN_KM, maxKm: 1e8 };

  it("a guide exists for every 1,000,000 km: 1M, 2M, ... 100M, with 10M majors and 5M mids", () => {
    const kms = guideDistances(domain);
    expect(kms.length).toBe(100);
    expect(kms[0]).toBe(1e6);
    expect(kms.at(-1)).toBe(100e6);
    for (let i = 1; i < kms.length; i++) expect(kms[i]! - kms[i - 1]!).toBe(SCALE_STEP_KM);
    expect(kms.filter((km) => guideTier(km) === "major")).toEqual([10, 20, 30, 40, 50, 60, 70, 80, 90, 100].map((m) => m * 1e6));
    expect([5e6, 15e6, 95e6].map(guideTier)).toEqual(["mid", "mid", "mid"]);
    expect([1e6, 11e6, 23e6].map(guideTier)).toEqual(["minor", "minor", "minor"]);
    expect(formatScaleKm(23e6)).toBe("23M km");
  });

  it("the guides use the asteroids' own mapping: 12.4M km rests between the 12M and 13M guides, 47,382,615 km between 47M and 48M", () => {
    const records = [record("a", 12_400_000), record("b", 47_382_615)];
    const { renderer, travelTo } = newRenderer(records);
    for (const km of [13e6, 5e7, 1e8]) {
      travelTo(km);
      const layout = renderer.viewLayout;
      const alt = (d: number) => altitudePx(layout, d, renderer.distanceView);
      if (renderer.phaseOf("a") === "SETTLED") {
        expect(renderer.restAltitudeOf("a")!).toBeGreaterThan(alt(12e6));
        expect(renderer.restAltitudeOf("a")!).toBeLessThan(alt(13e6));
      }
      if (renderer.phaseOf("b") === "SETTLED") {
        expect(renderer.restAltitudeOf("b")!).toBeGreaterThan(alt(47e6));
        expect(renderer.restAltitudeOf("b")!).toBeLessThan(alt(48e6));
      }
    }
    expect(renderer.phaseOf("b")).toBe("SETTLED");
    renderer.dispose();
  });

  it("the distance being explored is emphasised: at ~23.4M km, 23M > 22M > 21M; unreached guides far ahead are hidden", () => {
    const { renderer, travelTo } = newRenderer(fixtureRecords());
    travelTo(23_417_892);
    const alpha = new Map(renderer.visibleGuides.map((g) => [g.km, g.alpha]));
    expect(alpha.get(23e6)!).toBeGreaterThan(alpha.get(22e6)!);
    expect(alpha.get(22e6)!).toBeGreaterThan(alpha.get(21e6)!);
    expect(alpha.get(20e6)!).toBeGreaterThan(alpha.get(19e6) ?? 0); // a major stays stronger than its minor neighbour
    expect(alpha.get(23e6)!).toBeGreaterThan(alpha.get(24e6) ?? 0);
    for (const km of [26e6, 30e6, 50e6, 1e8]) expect(alpha.has(km)).toBe(false);
    expect(Math.max(...alpha.values())).toBeLessThanOrEqual(0.75); // never louder than the scene
    renderer.dispose();
  });

  it("for every frontier from 1.5M to 100M km, the strongest guide is within 1M km of the frontier (never a major further back)", () => {
    for (let f = 1.5e6; f <= 1e8; f += 0.137e6) {
      const kms = guideDistances({ minKm: DISTANCE_MIN_KM, maxKm: 1e8 });
      const alphas = kms.map((km) => guideOpacity(km, guideTier(km), f, 50));
      const strongest = kms[alphas.indexOf(Math.max(...alphas))]!;
      expect(Math.abs(strongest - f)).toBeLessThan(1e6); // the guide being explored, never a major 2M+ back
      expect(guideOpacity(Math.ceil((f + 2e6) / 1e6) * 1e6, "major", f, 50)).toBeLessThan(0.005); // 2M+ ahead: hidden
    }
  });

  it("the field grows with depth: more guides at 50M than at 10M; every major up to the frontier is drawn", () => {
    const { renderer, travelTo } = newRenderer(fixtureRecords());
    travelTo(10.5e6);
    const at10 = renderer.visibleGuides.length;
    travelTo(1e8);
    const deep = renderer.visibleGuides;
    expect(deep.length).toBeGreaterThan(at10);
    for (let m = 10; m <= 100; m += 10) expect(deep.some((g) => g.km === m * 1e6)).toBe(true);
    // LOD: minors fade where the field is dense, majors never do.
    const major = deep.find((g) => g.km === 50e6)!;
    expect(major.alpha).toBeGreaterThanOrEqual(GUIDE_BASE.major * 0.99);
    renderer.dispose();
  });

  it("labels are progressive: early field labels each million; later, the frontier neighbourhood plus 10M/5M markers", () => {
    const g = (km: number, alpha = 0.2) => ({ km, tier: guideTier(km), alpha, altitude: 0 });
    const early = guideLabelCandidates([1, 2, 3, 4, 5, 6, 7, 8, 9].map((m) => g(m * 1e6)), 8.4e6).map((x) => x.km);
    expect(early).toEqual(expect.arrayContaining([1e6, 2e6, 3e6, 4e6, 7e6, 8e6]));
    expect(early).not.toContain(9e6); // not yet reached
    const later = guideLabelCandidates(Array.from({ length: 30 }, (_, i) => g((i + 1) * 1e6)), 23.4e6).map((x) => x.km);
    expect(later.slice(0, 4)).toEqual([23e6, 22e6, 21e6, 10e6]); // nearest the frontier first, then majors
    expect(later).toEqual(expect.arrayContaining([5e6, 15e6, 20e6]));
    expect(later).not.toContain(13e6); // a far minor is not labelled
    expect(later.some((km) => km > 23.4e6)).toBe(false);
  });

  it("label placement never overlaps and is bounded", () => {
    const ordered = Array.from({ length: 100 }, (_, i) => ({ km: (i + 1) * 1e6, y: 800 - i * 4 }));
    const chosen = chooseGuideLabels(ordered, 400);
    expect(chosen.length).toBeLessThanOrEqual(MAX_GUIDE_LABELS);
    for (let i = 1; i < chosen.length; i++) expect(Math.abs(chosen[i]!.y - chosen[i - 1]!.y)).toBeGreaterThanOrEqual(15);
    for (const c of chosen) expect(Math.abs(c.y - 400)).toBeGreaterThanOrEqual(15);
  });

  it("the frontier indicator counts up in whole millions (presentation only)", () => {
    expect([10e6, 10.999e6, 11e6, 47_312_000, 99_999_999].map(frontierLabelKm)).toEqual([10e6, 10e6, 11e6, 47e6, 99e6]);
    expect(frontierLabelKm(384_400)).toBe(380_000);
  });

  it("the maximum is not clipped at 100M: a 330M km asteroid gets guides beyond 100M and is revealed", () => {
    const far = record("far", 330_123_456);
    const { renderer, travelTo } = newRenderer([far, record("near", 2e6)]);
    expect(renderer.distanceDomain.maxKm).toBeGreaterThanOrEqual(330_123_456 * 1.05);
    expect(guideDistances(renderer.distanceDomain).at(-1)!).toBeGreaterThan(330e6);
    travelTo(renderer.distanceDomain.maxKm);
    expect(renderer.phaseOf("far")).toBe("SETTLED");
    const alt = (d: number) => altitudePx(renderer.viewLayout, d, renderer.distanceView);
    expect(renderer.restAltitudeOf("far")!).toBeGreaterThan(alt(330e6));
    expect(renderer.restAltitudeOf("far")!).toBeLessThan(alt(331e6));
    renderer.dispose();
  });
});

describe("journey: daytime sky -> night + Moon -> distance field -> deep space", () => {
  const luminance = (hex: string) => [1, 3, 5].reduce((sum, i, k) => sum + parseInt(hex.slice(i, i + 2), 16) * [0.2126, 0.7152, 0.0722][k]!, 0);

  it("A. initial state: bright sky, no Moon, no guides, no asteroid falling", () => {
    const { renderer, phases } = newRenderer(fixtureRecords());
    frames(120);
    expect(luminance(skyColors(renderer.exploration.currentProgress).zenith)).toBeGreaterThan(130);
    expect(renderer.moonOpacity).toBe(0);
    expect(renderer.visibleGuides.length).toBe(0);
    expect([...phases().values()].every((p) => p === "HIDDEN")).toBe(true);
    expect(starOpacity(0)).toBe(0);
    renderer.dispose();
  });

  it("B. a small scroll darkens the sky but the Moon stays hidden", () => {
    const { renderer } = newRenderer(fixtureRecords());
    for (let i = 0; i < 8; i++) renderer.exploration.applyWheel(100);
    frames(SETTLE);
    const p = renderer.exploration.currentProgress;
    expect(p).toBeGreaterThan(0);
    expect(luminance(skyColors(p).zenith)).toBeLessThan(luminance(skyColors(0).zenith));
    expect(renderer.revealedKm).toBeLessThan(MOON_DISTANCE_KM);
    expect(renderer.moonOpacity).toBe(0);
    expect(renderer.visibleGuides.length).toBe(0);
    renderer.dispose();
  });

  it("C. the Moon appears when the frontier reaches 384,400 km, during the night transition", () => {
    const { renderer } = newRenderer(fixtureRecords());
    const domain = renderer.distanceDomain;
    expect(progressForDistance(MOON_DISTANCE_KM, domain)).toBeCloseTo(MOON_PROGRESS, 12);
    renderer.exploration.setTarget(progressForDistance(MOON_DISTANCE_KM * 0.98, domain));
    frames(SETTLE);
    expect(renderer.revealedKm).toBeLessThan(MOON_DISTANCE_KM);
    expect(renderer.moonOpacity).toBe(0);
    renderer.exploration.setTarget(MOON_PROGRESS + 0.03);
    frames(SETTLE);
    expect(renderer.moonOpacity).toBe(1);
    expect(moonOpacity(MOON_PROGRESS)).toBe(0);
    expect(luminance(skyColors(MOON_PROGRESS).zenith)).toBeLessThan(45); // night, not day
    expect(starOpacity(MOON_PROGRESS)).toBeGreaterThan(0.35); // stars are appearing
    renderer.dispose();
  });

  it("M. the sky goes bright day -> twilight -> night -> deep space, monotonically darker", () => {
    const stages = [0, 0.12, 0.21, MOON_PROGRESS, FIELD_PROGRESS, 0.5, 1].map((p) => luminance(skyColors(p).zenith));
    for (let i = 1; i < stages.length; i++) expect(stages[i]!).toBeLessThan(stages[i - 1]!);
  });

  it("the journey is staged and long: Moon at 26%, 1M at 32%, then at most ~3M km per notch to the maximum", () => {
    const domain = { minKm: DISTANCE_MIN_KM, maxKm: 1e8 };
    expect(revealedDistanceKm(MOON_PROGRESS, domain)).toBeCloseTo(MOON_DISTANCE_KM, 3);
    expect(revealedDistanceKm(FIELD_PROGRESS, domain)).toBeCloseTo(1e6, 3);
    expect(revealedDistanceKm(1, domain)).toBe(1e8);
    const notches = 1 / PROGRESS_PER_100PX;
    expect(notches).toBeGreaterThanOrEqual(60); // full journey: >= 60 wheel notches of 100 px
    const fieldNotches = (1 - FIELD_PROGRESS) * notches;
    expect(fieldNotches).toBeGreaterThanOrEqual(40);
    // In the field no single notch jumps more than ~3M km (2.9M at the very end), and a burst event is bounded.
    for (let p = FIELD_PROGRESS; p < 1; p += PROGRESS_PER_100PX) {
      const step = revealedDistanceKm(Math.min(1, p + PROGRESS_PER_100PX), domain) - revealedDistanceKm(p, domain);
      expect(step).toBeLessThan(3e6);
    }
    expect((MAX_WHEEL_DELTA / 100) * PROGRESS_PER_100PX).toBeLessThanOrEqual(0.03);
    for (const km of [5e5, 2e6, 23_417_892, 7.4e7]) expect(revealedDistanceKm(progressForDistance(km, domain), domain)).toBeCloseTo(km, 0);
  });
});

describe("L. the sky transition follows the same progression", () => {
  it("background and stars are functions of exploration progress, darkening as the frontier moves out", () => {
    const host = sizedHost();
    const renderer = new WorldRenderer(host, { createGLRenderer: () => new FakeGL(), onHover: vi.fn(), onClick: vi.fn() });
    renderer.setRecords(fixtureRecords());
    renderer.start();
    frames(3);
    expect(host.style.background).toContain(skyColors(0).zenith);
    renderer.exploration.setTarget(progressForDistance(50e6, renderer.distanceDomain));
    frames(SETTLE);
    expect(host.style.background).toContain(skyColors(renderer.exploration.currentProgress).zenith);
    expect(starOpacity(renderer.exploration.currentProgress)).toBeGreaterThan(0.5);
    renderer.dispose();
  });
});

describe("M. focus never corrupts the revealed state", () => {
  it("scroll -> select -> return: same revealed distance, nothing re-falls, nothing new appears", () => {
    const records = controlled();
    const { renderer, phases, travelTo } = newRenderer(records);
    travelTo(15e6);
    const revealed = renderer.revealedKm;
    const before = Object.fromEntries(phases());
    const seen = new Set<AsteroidPhase>();
    renderer.setFocus("1200");
    for (let f = 0; f < 120; f++) {
      frames(1);
      for (const p of phases().values()) seen.add(p);
    }
    renderer.setFocus(null);
    for (let f = 0; f < 120; f++) {
      frames(1);
      for (const p of phases().values()) seen.add(p);
    }
    expect(seen.has("FALLING")).toBe(false);
    expect(renderer.revealedKm).toBe(revealed);
    expect(Object.fromEntries(phases())).toEqual(before);
    renderer.dispose();
  });

  it("a deep-linked asteroid beyond the frontier is shown while focused and retreats on return", () => {
    const records = controlled();
    const { renderer, phases, travelTo } = newRenderer(records);
    travelTo(3e6);
    renderer.setFocus("9200");
    frames(SETTLE);
    expect(phases().get("9200")).toBe("SETTLED");
    expect(phases().get("4700")).toBe("HIDDEN"); // nothing else is revealed by focus
    renderer.setFocus(null);
    frames(SETTLE);
    expect(phases().get("9200")).toBe("HIDDEN");
    expectConsistent(renderer, records);
    renderer.dispose();
  });

  it("scrolling backward after a focus round trip makes the correct asteroids retreat", () => {
    const records = controlled();
    const { renderer, phases, travelTo } = newRenderer(records);
    travelTo(1e8);
    renderer.setFocus("4700");
    frames(80);
    renderer.setFocus(null);
    frames(80);
    travelTo(5e6);
    expect(Object.fromEntries(phases())).toEqual({ "4700": "HIDDEN", "0500": "SETTLED", "9200": "HIDDEN", "0200": "SETTLED", "1200": "HIDDEN" });
    renderer.dispose();
  });
});

describe("picking (screen-space discs)", () => {
  it("a click on a shown asteroid selects it; empty sky and hidden asteroids select nothing", () => {
    const rect = vi.spyOn(HTMLCanvasElement.prototype, "getBoundingClientRect").mockReturnValue(
      { left: 0, top: 0, right: 1600, bottom: 900, width: 1600, height: 900, x: 0, y: 0, toJSON: () => ({}) } as DOMRect,
    );
    const onClick = vi.fn();
    const records = [record("near", 2e6), record("far", 80e6)];
    const renderer = new WorldRenderer(sizedHost(), { createGLRenderer: () => new FakeGL(), onHover: vi.fn(), onClick });
    renderer.setRecords(records);
    renderer.start();
    const canvas = (renderer as unknown as { gl: FakeGL }).gl.domElement;
    const click = (x: number, y: number) => {
      for (const type of ["pointerdown", "pointerup"]) {
        canvas.dispatchEvent(Object.assign(new MouseEvent(type, { clientX: x, clientY: y, button: 0 }), { pointerId: 1 }));
      }
    };
    renderer.exploration.setTarget(progressForDistance(10e6, renderer.distanceDomain));
    frames(SETTLE);
    const near = renderer.screenPositionOf("near")!;
    click(near.x + 3, near.y - 2);
    expect(onClick).toHaveBeenLastCalledWith("near");
    click(near.x + 200, near.y + 200);
    expect(onClick).toHaveBeenLastCalledWith(null);
    // "far" is hidden (beyond the frontier): its would-be rest position is not clickable.
    const layout = renderer.viewLayout;
    const farRest = restPosition(layout, records[1]!, renderer.distanceView);
    click(farRest.x, layout.height - farRest.y);
    expect(onClick).toHaveBeenLastCalledWith(null);
    renderer.dispose();
    rect.mockRestore();
  });
});
