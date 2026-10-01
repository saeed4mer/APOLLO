import { ApiError } from "../src/api/errors";
import { validateProfileResponse } from "../src/api/validateProfile";
import { validateWorldResponse } from "../src/api/validateWorld";
import { createApp, type AppDeps } from "../src/app";
import type { AsteroidProfile } from "../src/models/profile";
import { formatFlag, formatNumber, REASON_TEXT, SENTRY_STATUS_TEXT } from "../src/ui/format";
import { deferred, FakeGL, fixture, flushPromises, installFakeRaf, installFakeResizeObserver } from "./helpers";

beforeEach(() => {
  installFakeRaf();
  installFakeResizeObserver();
  window.location.hash = "#/";
});

const profileFixture = (id: string) =>
  validateProfileResponse(fixture(id === "3548666" ? "profile_3548666.json" : "profile_3830890.json"));

function mount(overrides: Partial<AppDeps> = {}) {
  const root = document.createElement("div");
  document.body.appendChild(root);
  const fetchWorld = vi.fn(async () => validateWorldResponse(fixture("world.json")));
  const fetchProfile = vi.fn(async (id: string): Promise<AsteroidProfile> => profileFixture(id));
  const app = createApp(root, { createGLRenderer: () => new FakeGL(), fetchWorld, fetchProfile, ...overrides });
  const text = (selector: string) => root.querySelector(selector)?.textContent ?? "";
  const fact = (sectionKey: string, label: string) => {
    const rows = root.querySelectorAll(`[data-section="${sectionKey}"] .fact`);
    for (const row of rows) if (row.querySelector(".fact-label")?.textContent === label) return row;
    throw new Error(`no '${label}' row in ${sectionKey}`);
  };
  const value = (sectionKey: string, label: string) => fact(sectionKey, label).querySelector(".fact-value")!.textContent;
  const cleanup = () => {
    app.dispose();
    root.remove();
  };
  return { app, root, text, value, fact, fetchWorld, fetchProfile, cleanup };
}

async function goTo(hash: string) {
  window.location.hash = hash;
  await flushPromises();
  await flushPromises();
}

describe("world loading", () => {
  it("shows an explicit loading state, then the real object count; one request, no profile requests", async () => {
    const world = deferred<ReturnType<typeof validateWorldResponse>>();
    const t = mount({ fetchWorld: vi.fn(() => world.promise) });
    expect(t.text(".status-overlay")).toBe("INITIALIZING ASTEROID INTELLIGENCE FIELD");
    world.resolve(validateWorldResponse(fixture("world.json")));
    await flushPromises();
    expect(t.text(".status-overlay")).toBe("LOADED · 35 OBJECTS");
    expect(t.fetchProfile).not.toHaveBeenCalled();
    t.cleanup();
  });

  it("network failure shows a readable error; Retry loads the world", async () => {
    let attempt = 0;
    const t = mount({
      fetchWorld: vi.fn(async () => {
        if (attempt++ === 0) throw new ApiError("network", "unreachable");
        return validateWorldResponse(fixture("world.json"));
      }),
    });
    await flushPromises();
    expect(t.text(".status-overlay")).toContain("ASTEROID INTELLIGENCE UNAVAILABLE");
    expect(t.text(".status-overlay")).toContain("could not be reached");
    expect(t.text(".status-overlay")).not.toMatch(/Error:|stack|at /);
    (t.root.querySelector(".status-overlay button") as HTMLButtonElement).click();
    await flushPromises();
    expect(t.text(".status-overlay")).toBe("LOADED · 35 OBJECTS");
    t.cleanup();
  });

  it("reports rejected records instead of rendering them", async () => {
    const body = fixture("world.json");
    body.data[0].illustrative_direction = { x: 5, y: 0, z: 0 };
    const t = mount({ fetchWorld: vi.fn(async () => validateWorldResponse(body)) });
    await flushPromises();
    expect(t.text(".status-overlay")).toContain("LOADED · 34 OBJECTS");
    expect(t.text(".status-overlay")).toContain("1 record(s) failed validation");
    t.cleanup();
  });
});

describe("profile: data shown is exactly the API's, source by source", () => {
  it("never shows numbers while loading", async () => {
    const pending = deferred<AsteroidProfile>();
    const t = mount({ fetchProfile: vi.fn(() => pending.promise) });
    await flushPromises();
    await goTo("#/asteroid/3548666");
    const values = [...t.root.querySelectorAll(".profile-panel .fact-value")].map((n) => n.textContent);
    expect(values.length).toBeGreaterThan(10);
    expect(new Set(values)).toEqual(new Set(["Loading…"]));
    t.cleanup();
  });

  it("2010 TW54: NeoWs, SBDB and Sentry values match the API response", async () => {
    const t = mount();
    await flushPromises();
    await goTo("#/asteroid/3548666");
    const api = fixture("profile_3548666.json").data;
    expect(t.fetchProfile).toHaveBeenCalledTimes(1);
    expect(t.fetchProfile.mock.calls[0]![0]).toBe("3548666");

    expect(t.value("encounter", "Miss distance")).toBe(formatNumber(api.encounter.miss_distance_km, "km", 0));
    expect(t.value("encounter", "Relative velocity")).toBe(formatNumber(api.encounter.relative_velocity_km_s, "km/s", 3));
    expect(t.value("encounter", "Potentially hazardous (NeoWs)")).toBe(formatFlag(api.encounter.is_potentially_hazardous));
    expect(t.value("encounter", "NeoWs 'Sentry object' flag")).toBe("Yes");
    expect(t.value("neows_physical", "Absolute magnitude H")).toBe(`${api.neows_physical.absolute_magnitude_h} mag`);
    expect(t.value("physical", "Absolute magnitude H")).toBe(`${api.physical.absolute_magnitude} mag`);
    expect(t.value("orbit", "Ascending node Ω")).toBe(formatNumber(api.orbit.ascending_node_longitude_deg, "deg", 4));
    expect(t.value("sentry_assessment", "Cumulative impact probability (as published)")).toBe(String(api.sentry.assessment.impact_probability));
    expect(t.value("sentry_assessment", "Torino scale (max)")).toBe(String(api.sentry.assessment.torino_scale_max));

    const sources = [...t.root.querySelectorAll(".profile-panel .source")].map((n) => n.textContent);
    expect(sources).toEqual([
      "Source: Identity resolution + crosswalk", "Source: NASA NeoWs", "Source: NASA NeoWs",
      "Source: JPL SBDB", "Source: JPL SBDB", "Source: JPL Sentry",
    ]);
    expect(t.text('[data-section="sentry_assessment"] .sentry-status')).toBe(`Linkage: ${SENTRY_STATUS_TEXT.available}`);
    t.cleanup();
  });

  it("unresolved 2018 SP2: SBDB/Sentry read Unavailable with the contract's reason; nothing inferred", async () => {
    const t = mount();
    await flushPromises();
    await goTo("#/asteroid/3830890");
    const row = t.fact("orbit", "Semi-major axis a");
    expect(row.querySelector(".fact-value")!.textContent).toBe("Unavailable");
    expect(row.querySelector(".fact-reason")!.textContent).toBe(REASON_TEXT.not_resolved);
    expect(t.value("orbit", "PHA (SBDB)")).toBe("Unknown");
    expect(t.value("sentry_assessment", "Cumulative impact probability (as published)")).toBe("Unavailable");
    expect(t.text('[data-section="sentry_assessment"] .sentry-status')).toBe(`Linkage: ${SENTRY_STATUS_TEXT.not_resolved}`);
    expect(t.root.querySelector(".profile-panel")!.textContent).not.toMatch(/XX|NaN|undefined/);
    t.cleanup();
  });

  it("a 404 reads 'Asteroid not found', not a server error", async () => {
    const t = mount({ fetchProfile: vi.fn(async () => { throw new ApiError("not_found", "nf", { status: 404, code: "TARGET_NOT_FOUND" }); }) });
    await flushPromises();
    await goTo("#/asteroid/99999999");
    expect(t.text(".profile-panel .status-title")).toBe("Asteroid not found");
    t.cleanup();
  });

  it("a 500 reads 'Profile unavailable' with Retry", async () => {
    let attempt = 0;
    const t = mount({
      fetchProfile: vi.fn(async (id: string) => {
        if (attempt++ === 0) throw new ApiError("server", "boom", { status: 500 });
        return profileFixture(id);
      }),
    });
    await flushPromises();
    await goTo("#/asteroid/3548666");
    expect(t.text(".profile-panel .status-title")).toBe("Profile unavailable");
    [...t.root.querySelectorAll<HTMLButtonElement>(".profile-panel button")].find((b) => b.textContent === "Retry")!.click();
    await flushPromises();
    expect(t.app.store.getState().profile.status).toBe("ready");
    t.cleanup();
  });
});

describe("selection", () => {
  it("A then B: A's late response never overwrites B", async () => {
    const requests = new Map<string, ReturnType<typeof deferred<AsteroidProfile>>>();
    const t = mount({
      fetchProfile: vi.fn((id: string) => {
        const d = deferred<AsteroidProfile>();
        requests.set(id, d);
        return d.promise;
      }),
    });
    await flushPromises();
    await goTo("#/asteroid/3548666");
    await goTo("#/asteroid/3830890");
    requests.get("3830890")!.resolve(profileFixture("3830890"));
    await flushPromises();
    requests.get("3548666")!.resolve(profileFixture("3548666"));
    await flushPromises();
    expect(t.app.store.getState().selectedId).toBe("3830890");
    expect(t.app.store.getState().profile).toMatchObject({ status: "ready", neowsId: "3830890" });
    expect(t.text(".profile-panel h2")).toBe("(2018 SP2)");
    t.cleanup();
  });

  it("Back returns to the world and clears the profile; a refresh restores the selection from the URL", async () => {
    window.location.hash = "#/asteroid/3548666";
    await flushPromises();
    const t = mount();
    await flushPromises();
    await flushPromises();
    expect(t.app.store.getState().selectedId).toBe("3548666");
    [...t.root.querySelectorAll<HTMLButtonElement>(".profile-panel button")].find((b) => b.textContent?.includes("Back"))!.click();
    await flushPromises();
    expect(window.location.hash).toBe("#/");
    expect(t.app.store.getState().selectedId).toBeNull();
    expect(t.app.store.getState().profile).toEqual({ status: "idle" });
    expect((t.root.querySelector(".profile-panel") as HTMLElement).hidden).toBe(true);
    t.cleanup();
  });
});
