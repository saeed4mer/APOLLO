import * as THREE from "three";
import { validateWorldResponse } from "../src/api/validateWorld";
import { isSentryLinked, SENTRY_STATUSES, type WorldRecord } from "../src/models/world";
import { WorldRenderer } from "../src/renderer/WorldRenderer";
import { brandOpacity, introOpacity } from "../src/scene/atmosphere";
import { progressForDistance } from "../src/scene/skyLayout";
import { HoverTooltip } from "../src/ui/HoverTooltip";
import { PRODUCT_NAME, PRODUCT_SUBTITLE, WorldHud } from "../src/ui/WorldHud";
import { FakeGL, fixture, installFakeRaf, installFakeResizeObserver } from "./helpers";

/** APOLLO identity and the gold designation for actual Sentry links. */
let raf: ReturnType<typeof installFakeRaf>;

beforeEach(() => {
  raf = installFakeRaf();
  installFakeResizeObserver();
});

const fixtureRecords = (): WorldRecord[] => validateWorldResponse(fixture("world.json")).records;
const frames = (n: number) => {
  for (let i = 0; i < n; i++) raf.frame(16);
};

describe("APOLLO title", () => {
  it("shows APOLLO with the subtitle exactly as written, in the opening title and the docked wordmark", () => {
    expect(PRODUCT_NAME).toBe("APOLLO");
    expect(PRODUCT_SUBTITLE).toBe("Asteroid Proximity & Orbital Logistics Lookout Operation");
    const hud = new WorldHud(() => {});
    for (const selector of [".brand-hero", ".brand-docked"]) {
      const block = hud.element.querySelector(selector)!;
      expect(block.querySelector(".brand-name")!.textContent).toBe("APOLLO");
      expect(block.querySelector(".brand-subtitle")!.textContent).toBe("Asteroid Proximity & Orbital Logistics Lookout Operation");
    }
    expect(hud.element.querySelector("h1")?.textContent).toBe("APOLLO");
    expect(hud.element.textContent).not.toContain("ASTEROID INTELLIGENCE");
    hud.dispose();
  });

  it("the opening title gives way to the docked wordmark, which stays for the journey and yields to focus", () => {
    const hud = new WorldHud(() => {});
    const hero = hud.element.querySelector<HTMLElement>(".intro")!;
    const docked = hud.element.querySelector<HTMLElement>(".brand-docked")!;
    hud.setProgress(0, 0);
    expect([Number(hero.style.opacity), Number(docked.style.opacity)]).toEqual([1, 0]);
    for (const p of [0.1, 0.5, 1]) {
      hud.setProgress(p, 0);
      expect(Number(hero.style.opacity)).toBe(0);
      expect(Number(docked.style.opacity)).toBe(1);
    }
    hud.setProgress(0.5, 1); // focused: the Back button and profile title own the top of the screen
    expect(Number(docked.style.opacity)).toBe(0);
    for (let p = 0; p <= 1; p += 0.001) expect(introOpacity(p) + brandOpacity(p)).toBeGreaterThan(0.2); // never both gone
    hud.dispose();
  });
});

describe("gold = actual Sentry link (served status only)", () => {
  const withSentry = (base: WorldRecord, status: WorldRecord["sentry"]["status"], pha: boolean | null): WorldRecord => ({
    ...structuredClone(base),
    sentry: { ...base.sentry, status },
    encounter: { ...base.encounter, is_potentially_hazardous: pha },
  });

  it("linked means sentry.status 'available' or 'linked_no_record', exactly as the API defines it; PHA plays no part", () => {
    const base = fixtureRecords()[0]!;
    const linked = SENTRY_STATUSES.filter((status) => isSentryLinked(withSentry(base, status, null)));
    expect(linked).toEqual(["available", "linked_no_record"]);
    for (const pha of [true, false, null]) {
      expect(isSentryLinked(withSentry(base, "not_resolved", pha))).toBe(false);
      expect(isSentryLinked(withSentry(base, "not_present", pha))).toBe(false);
      expect(isSentryLinked(withSentry(base, "available", pha))).toBe(true);
    }
  });

  it("in the real fixture exactly two asteroids are Sentry-linked (2008 ST, 2010 TW54); no PHA object is", () => {
    const records = fixtureRecords();
    expect(records.filter(isSentryLinked).map((r) => r.neows_id).sort()).toEqual(["3427460", "3548666"]);
    for (const r of records.filter((x) => x.encounter.is_potentially_hazardous === true)) expect(isSentryLinked(r)).toBe(false);
  });

  it("renderer: gold body + rim/halo only on linked asteroids; PHA-only objects stay plain; positions unchanged", () => {
    const host = document.createElement("div");
    Object.defineProperty(host, "clientWidth", { value: 1600 });
    Object.defineProperty(host, "clientHeight", { value: 900 });
    const base = fixtureRecords()[0]!;
    const sentry = { ...withSentry(base, "available", false), neows_id: "sentry" };
    const plain = { ...withSentry(base, "not_present", false), neows_id: "plain" };
    const pha = { ...withSentry(base, "not_resolved", true), neows_id: "pha" };
    const renderer = new WorldRenderer(host, { createGLRenderer: () => new FakeGL(), onHover: vi.fn(), onClick: vi.fn() });
    renderer.setRecords([sentry, plain, pha]); // identical distance and direction
    renderer.start();
    renderer.exploration.setTarget(progressForDistance(base.encounter.miss_distance_km * 1.05, renderer.distanceDomain));
    frames(400);
    const internals = renderer as unknown as { rocks: THREE.InstancedMesh; sentryRims: THREE.InstancedMesh; sentryHalos: THREE.InstancedMesh };
    const color = (i: number) => {
      const c = new THREE.Color();
      internals.rocks.getColorAt(i, c);
      return c.getHexString();
    };
    const shown = (mesh: THREE.InstancedMesh, i: number) => {
      const m = new THREE.Matrix4();
      mesh.getMatrixAt(i, m);
      return new THREE.Vector3().setFromMatrixPosition(m).x > -1e5;
    };
    expect(color(0)).not.toBe(color(1)); // gold vs plain
    expect(color(2)).toBe(color(1)); // PHA does not make an asteroid gold
    expect([0, 1, 2].map((i) => shown(internals.sentryRims, i))).toEqual([true, false, false]);
    expect([0, 1, 2].map((i) => shown(internals.sentryHalos, i))).toEqual([true, false, false]);
    expect(["sentry", "plain", "pha"].map((id) => renderer.sentryGoldShownOf(id))).toEqual([true, false, false]);
    expect(renderer.screenPositionOf("sentry")).toEqual(renderer.screenPositionOf("plain")); // same place
    expect(renderer.restAltitudeOf("sentry")).toBe(renderer.restAltitudeOf("plain")); // same distance mapping
    renderer.dispose();
  });

  it("hover: a small SENTRY LINKED tag only for linked asteroids", () => {
    const tooltip = new HoverTooltip();
    const [st] = fixtureRecords().filter(isSentryLinked);
    tooltip.show(st!, 10, 10);
    expect(tooltip.element.querySelector(".sentry-tag")?.textContent).toBe("SENTRY LINKED");
    const pha = fixtureRecords().find((r) => r.encounter.is_potentially_hazardous === true)!;
    tooltip.show(pha, 10, 10);
    expect(tooltip.element.querySelector(".sentry-tag")).toBeNull();
    tooltip.dispose();
  });
});
