import { validateProfileResponse } from "../src/api/validateProfile";
import { PROFILE_SPEC } from "../src/models/profile";
import { fixture } from "./helpers";

describe("validateProfileResponse", () => {
  it("accepts the real linked profile and keeps values exactly as served", () => {
    const body = fixture("profile_3548666.json");
    const profile = validateProfileResponse(body);
    expect(profile.neows_id).toBe("3548666");
    expect(profile.sections.encounter.values.miss_distance_km).toBe(body.data.encounter.miss_distance_km);
    expect(profile.sections.orbit.values.ascending_node_longitude_deg).toBe(body.data.orbit.ascending_node_longitude_deg);
    expect(profile.sections.sentry_assessment.values.impact_probability).toBe(body.data.sentry.assessment.impact_probability);
    expect(profile.sentryStatus).toBe("available");
    expect(profile.sentrySourceContract).toBe("sentry_mode_s_summary");
  });

  it("keeps each section's declared source (no merging into 'NASA data')", () => {
    const profile = validateProfileResponse(fixture("profile_3548666.json"));
    expect(profile.sections.encounter.source).toBe("nasa_neows");
    expect(profile.sections.neows_physical.source).toBe("nasa_neows");
    expect(profile.sections.orbit.source).toBe("jpl_sbdb");
    expect(profile.sections.physical.source).toBe("jpl_sbdb");
    expect(profile.sections.sentry_assessment.source).toBe("jpl_sentry");
    expect(profile.sections.identity.source).toBeNull();
  });

  it("accepts the real unresolved profile with nulls and reasons intact", () => {
    const profile = validateProfileResponse(fixture("profile_3830890.json"));
    expect(profile.sections.orbit.values.semi_major_axis_au).toBeNull();
    expect(profile.sections.orbit.availability?.unavailable.semi_major_axis_au).toBe("not_resolved");
    expect(profile.sentryStatus).toBe("not_resolved");
  });

  it("covers every displayed field with a type check", () => {
    const profile = validateProfileResponse(fixture("profile_3548666.json"));
    for (const spec of PROFILE_SPEC) {
      for (const field of spec.fields) expect(profile.sections[spec.key].values).toHaveProperty(field.key);
    }
  });

  it.each([
    ["number field as string", (d: any) => (d.encounter.miss_distance_km = "17457205")],
    ["flag as string", (d: any) => (d.encounter.is_sentry_object = "true")],
    ["missing section", (d: any) => delete d.orbit],
    ["unknown unavailable reason", (d: any) => (d.orbit.availability.unavailable = { eccentricity: "probably_fine" })],
    ["missing sentry status", (d: any) => delete d.sentry.status],
  ])("fails the whole profile on %s", (_name, corrupt) => {
    const body = fixture("profile_3548666.json");
    corrupt(body.data);
    expect(() => validateProfileResponse(body)).toThrow();
  });
});
