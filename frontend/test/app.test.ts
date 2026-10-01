import { ApiError } from "../src/api/errors";
import { validateProfileResponse } from "../src/api/validateProfile";
import { validateWorldResponse } from "../src/api/validateWorld";
import { createApp, type AppDeps } from "../src/app";
import type { AsteroidProfile } from "../src/models/profile";
import { formatFlag, formatNumber, SENTRY_STATUS_TEXT } from "../src/ui/format";
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
  const callout = (key: string) => root.querySelector<HTMLElement>(`[data-callout="${key}"]`);
  const value = (key: string, label: string) => {
    for (const row of callout(key)?.querySelectorAll(".fact") ?? []) {
      if (row.querySelector(".fact-label")?.textContent === label) return row.querySelector(".fact-value")!.textContent;
    }
    throw new Error(`no '${label}' in callout ${key}`);
  };
  const button = (label: string) =>
    [...root.querySelectorAll<HTMLButtonElement>("button")].find((b) => b.textContent?.includes(label))!;
  const cleanup = () => {
    app.dispose();
    root.remove();
  };
  return { app, root, text, callout, value, button, fetchWorld, fetchProfile, cleanup };
}

async function goTo(hash: string) {
  window.location.hash = hash;
  await flushPromises();
  await flushPromises();
}

describe("world", () => {
  it("explicit loading state, then minimal chrome; one world request and no profile requests", async () => {
    const world = deferred<ReturnType<typeof validateWorldResponse>>();
    const fetchWorld = vi.fn(() => world.promise);
    const t = mount({ fetchWorld });
    expect(t.text(".status-overlay")).toBe("INITIALIZING ASTEROID INTELLIGENCE FIELD");
    world.resolve(validateWorldResponse(fixture("world.json")));
    await flushPromises();
    expect((t.root.querySelector(".status-overlay") as HTMLElement).hidden).toBe(true); // no permanent banner
    expect(t.text(".intro")).toContain("Scroll to explore");
    expect(fetchWorld).toHaveBeenCalledTimes(1);
    expect(t.fetchProfile).not.toHaveBeenCalled();
    t.cleanup();
  });

  it("About this view states the illustrative model and the real count", async () => {
    const t = mount();
    await flushPromises();
    t.button("About this view").click();
    const about = t.text(".about-panel");
    expect(about).toContain("illustrative visualization, not a physics simulation");
    expect(about).toContain("real NeoWs miss distance");
    expect(about).toContain("sha256-uniform-sphere-v1");
    expect(about).toContain("35 NeoWs object(s) shown");
    expect(about).toContain("visual metaphor");
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
    t.button("Retry").click();
    await flushPromises();
    expect(t.app.store.getState().world.status).toBe("ready");
    t.cleanup();
  });

  it("reports rejected records instead of rendering them", async () => {
    const body = fixture("world.json");
    body.data[0].illustrative_direction = { x: 5, y: 0, z: 0 };
    const t = mount({ fetchWorld: vi.fn(async () => validateWorldResponse(body)) });
    await flushPromises();
    expect(t.text(".status-overlay")).toContain("1 record(s) failed validation");
    t.button("About this view").click();
    expect(t.text(".about-panel")).toContain("34 NeoWs object(s) shown");
    t.cleanup();
  });
});

describe("focus: information emerges around the asteroid, exactly as served", () => {
  it("never shows numbers while loading", async () => {
    const pending = deferred<AsteroidProfile>();
    const t = mount({ fetchProfile: vi.fn(() => pending.promise) });
    await flushPromises();
    await goTo("#/asteroid/3548666");
    const values = [...t.root.querySelectorAll(".callout .fact-value")].map((n) => n.textContent);
    expect(values.length).toBeGreaterThan(0);
    expect(new Set(values)).toEqual(new Set(["Loading…"]));
    t.cleanup();
  });

  it("2010 TW54: each callout's values match the API, each labelled with its source", async () => {
    const t = mount();
    await flushPromises();
    await goTo("#/asteroid/3548666");
    const api = fixture("profile_3548666.json").data;
    expect(t.fetchProfile).toHaveBeenCalledTimes(1);
    expect(t.fetchProfile.mock.calls[0]![0]).toBe("3548666");

    expect(t.value("encounter", "Miss distance")).toBe(formatNumber(api.encounter.miss_distance_km, "km", 0));
    expect(t.value("encounter", "Relative velocity")).toBe(formatNumber(api.encounter.relative_velocity_km_s, "km/s", 3));
    expect(t.value("encounter", "Potentially hazardous (NeoWs)")).toBe(formatFlag(api.encounter.is_potentially_hazardous));
    expect(t.value("identity", "NeoWs ID")).toBe("3548666");
    expect(t.value("identity", "Sentry ID")).toBe(api.identity.sentry_id);
    expect(t.value("orbit", "Ascending node Ω")).toBe(formatNumber(api.orbit.ascending_node_longitude_deg, "deg", 4));
    expect(t.value("neows_physical", "Absolute magnitude H")).toBe(`${api.neows_physical.absolute_magnitude_h} mag`);
    expect(t.value("physical", "Absolute magnitude H")).toBe(`${api.physical.absolute_magnitude} mag`);
    expect(t.value("sentry_assessment", "Linkage")).toBe(SENTRY_STATUS_TEXT.available);
    expect(t.value("sentry_assessment", "Cumulative impact probability (as published)")).toBe(String(api.sentry.assessment.impact_probability));

    const sources = Object.fromEntries([...t.root.querySelectorAll<HTMLElement>(".callout")].map((c) => [c.dataset.callout, c.querySelector(".source")?.textContent]));
    expect(sources).toEqual({
      identity: "Identity resolution + crosswalk", encounter: "NASA NeoWs", orbit: "JPL SBDB",
      sentry_assessment: "JPL Sentry", neows_physical: "NASA NeoWs", physical: "JPL SBDB",
    });
    expect(t.text(".focus-title")).toContain("(2010 TW54)");
    t.cleanup();
  });

  it("unresolved 2018 SP2: no empty callouts; SBDB and Sentry collapse with the contract's reason", async () => {
    const t = mount();
    await flushPromises();
    await goTo("#/asteroid/3830890");
    expect(t.callout("orbit")).toBeNull();
    expect(t.callout("sentry_assessment")).toBeNull();
    expect(t.callout("encounter")).not.toBeNull();
    const unavailable = [...t.root.querySelectorAll(".focus-unavailable")].map((n) => n.textContent);
    expect(unavailable).toContain("Orbit · JPL SBDB — identity not resolved, so this source cannot be linked");
    expect(unavailable).toContain("Sentry · JPL Sentry — Not linkable: identity not resolved");
    expect(t.root.querySelector(".focus-view")!.textContent).not.toMatch(/XX|NaN|undefined/);
    t.cleanup();
  });

  it("a 404 reads 'Asteroid not found', not a server error", async () => {
    const t = mount({ fetchProfile: vi.fn(async () => { throw new ApiError("not_found", "nf", { status: 404, code: "TARGET_NOT_FOUND" }); }) });
    await flushPromises();
    await goTo("#/asteroid/99999999");
    expect(t.text(".focus-error .status-title")).toBe("Asteroid not found");
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
    expect(t.text(".focus-error .status-title")).toBe("Profile unavailable");
    t.button("Retry").click();
    await flushPromises();
    expect(t.app.store.getState().profile.status).toBe("ready");
    t.cleanup();
  });
});

describe("selection", () => {
  it("A -> B -> C quickly: late answers for A and B never overwrite C", async () => {
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
    await goTo("#/asteroid/3427460");
    await goTo("#/asteroid/3830890");
    requests.get("3830890")!.resolve(profileFixture("3830890"));
    await flushPromises();
    requests.get("3548666")!.resolve(profileFixture("3548666"));
    requests.get("3427460")!.resolve(profileFixture("3548666"));
    await flushPromises();
    expect(t.app.store.getState().selectedId).toBe("3830890");
    expect(t.app.store.getState().profile).toMatchObject({ status: "ready", neowsId: "3830890" });
    expect(t.text(".focus-title")).toContain("(2018 SP2)");
    t.cleanup();
  });

  it("Back returns to the world; a refresh restores the focused asteroid from the URL", async () => {
    window.location.hash = "#/asteroid/3548666";
    await flushPromises();
    const t = mount();
    await flushPromises();
    await flushPromises();
    expect(t.app.store.getState().selectedId).toBe("3548666");
    t.button("Back to world").click();
    await flushPromises();
    expect(window.location.hash).toBe("#/");
    expect(t.app.store.getState().profile).toEqual({ status: "idle" });
    expect((t.root.querySelector(".focus-view") as HTMLElement).hidden).toBe(true);
    t.cleanup();
  });
});
