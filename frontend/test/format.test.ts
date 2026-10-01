import type { FieldSpec } from "../src/models/profile";
import type { WorldRecord } from "../src/models/world";
import { formatField, formatFlag, formatNumber, SENTRY_STATUS_TEXT, UNAVAILABLE, UNKNOWN } from "../src/ui/format";
import { hoverFacts, tooltipPosition } from "../src/ui/HoverTooltip";
import { fixture } from "./helpers";

describe("formatting preserves true / false / unknown and never invents values", () => {
  it("flags: true -> Yes, false -> No, null -> Unknown (never No)", () => {
    expect(formatFlag(true)).toBe("Yes");
    expect(formatFlag(false)).toBe("No");
    expect(formatFlag(null)).toBe(UNKNOWN);
  });

  it("missing measurements read Unavailable, never 0", () => {
    expect(formatNumber(null, "km/s", 3)).toBe(UNAVAILABLE);
    const spec: FieldSpec = { key: "v", label: "v", kind: "number", unit: "km/s", digits: 3 };
    expect(formatField(spec, null)).toBe(UNAVAILABLE);
  });

  it("formats with units and thousands separators without changing the model value", () => {
    const raw = 1234567.891;
    expect(formatNumber(raw, "km", 0)).toBe("1,234,568 km");
    expect(raw).toBe(1234567.891);
  });

  it("shows values without a declared precision exactly as published", () => {
    expect(formatNumber(6.594578e-5)).toBe(String(6.594578e-5));
  });

  it("never emits placeholder text that could pass for data", () => {
    const outputs = [formatNumber(null, "km"), formatFlag(null), formatNumber(0, "km", 0)];
    for (const text of outputs) expect(text).not.toMatch(/XX|NaN|undefined|null/);
  });
});

describe("hover facts come only from the loaded world record", () => {
  const record = (): WorldRecord => structuredClone(fixture("world.json").data.find((r: WorldRecord) => r.neows_id === "3548666"));

  it("labels NeoWs facts and Sentry linkage separately, using the contract's status", () => {
    const facts = Object.fromEntries(hoverFacts(record()));
    expect(facts["Potentially hazardous (NeoWs)"]).toBe("No");
    expect(facts["JPL Sentry linkage"]).toBe(SENTRY_STATUS_TEXT.available);
    expect(facts["Miss distance (NeoWs)"]).toBe(formatNumber(record().encounter.miss_distance_km, "km", 0));
  });

  it("does not infer Sentry from PHA: a PHA object without linkage shows 'not resolved'", () => {
    const r = record();
    r.encounter.is_potentially_hazardous = true;
    r.sentry.status = "not_resolved";
    const facts = Object.fromEntries(hoverFacts(r));
    expect(facts["Potentially hazardous (NeoWs)"]).toBe("Yes");
    expect(facts["JPL Sentry linkage"]).toBe(SENTRY_STATUS_TEXT.not_resolved);
  });

  it("an unknown PHA flag displays Unknown, and missing velocity/diameter display Unavailable", () => {
    const r = record();
    r.encounter.is_potentially_hazardous = null;
    r.encounter.relative_velocity_km_s = null;
    r.encounter.estimated_diameter_min_km = null;
    const facts = Object.fromEntries(hoverFacts(r));
    expect(facts["Potentially hazardous (NeoWs)"]).toBe(UNKNOWN);
    expect(facts["Relative velocity (NeoWs)"]).toBe(UNAVAILABLE);
    expect(facts["Est. diameter (NeoWs)"]).toBe(UNAVAILABLE);
  });
});

describe("tooltip placement", () => {
  const bounds = { right: 1000, bottom: 800 };
  it("sits below-right of the pointer when there is room", () => {
    expect(tooltipPosition(100, 100, 300, 200, bounds)).toEqual({ left: 116, top: 116 });
  });
  it("flips left before crossing the right bound (e.g. an open profile panel)", () => {
    const { left } = tooltipPosition(900, 100, 300, 200, bounds);
    expect(left + 300).toBeLessThanOrEqual(900);
  });
  it("flips up before crossing the bottom bound", () => {
    const { top } = tooltipPosition(100, 700, 300, 200, bounds);
    expect(top + 200).toBeLessThanOrEqual(700);
  });
});
