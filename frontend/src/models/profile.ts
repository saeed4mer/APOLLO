/**
 * GET /asteroids/{neows_id}/profile, plus the declarative spec of which fields the renderer
 * displays. The spec is the single source of truth for validation AND presentation, so a field
 * can't be shown without being type-checked, and every number carries its unit.
 */

export type UnavailableReason = "not_resolved" | "not_in_source" | "not_in_current_contract" | "ambiguous_linkage";

export interface SectionAvailability {
  status: "available" | "partial" | "unavailable";
  unavailable: Record<string, UnavailableReason>;
}

export type FieldValue = string | number | boolean | null;

/** A profile section as the renderer consumes it: declared source, values, availability. */
export interface ProfileSection {
  source: string | null;
  values: Record<string, FieldValue>;
  availability: SectionAvailability | null;
}

export interface AsteroidProfile {
  neows_id: string;
  sections: Record<ProfileSectionKey, ProfileSection>;
  sentryStatus: string;
  sentrySourceContract: string;
  provenance: Record<string, Record<string, FieldValue>>;
}

export type FieldKind = "number" | "integer" | "boolean" | "string";

export interface FieldSpec {
  key: string;
  label: string;
  kind: FieldKind;
  unit?: string;
  /** Presentation only: maximum fraction digits. The model keeps the raw value. */
  digits?: number;
}

export type ProfileSectionKey = "identity" | "encounter" | "neows_physical" | "orbit" | "physical" | "sentry_assessment";

export interface SectionSpec {
  key: ProfileSectionKey;
  title: string;
  fields: FieldSpec[];
}

export const PROFILE_SPEC: readonly SectionSpec[] = [
  {
    key: "identity",
    title: "Identity",
    fields: [
      { key: "name", label: "Name (NeoWs)", kind: "string" },
      { key: "neows_id", label: "NeoWs ID", kind: "string" },
      { key: "match_state", label: "Identity resolution", kind: "string" },
      { key: "sbdb_spkid", label: "SBDB SPK-ID", kind: "string" },
      { key: "sbdb_designation", label: "SBDB designation", kind: "string" },
      { key: "sentry_id", label: "Sentry ID", kind: "string" },
    ],
  },
  {
    key: "encounter",
    title: "Close approach",
    fields: [
      { key: "close_approach_datetime", label: "Approach time (as published, no zone)", kind: "string" },
      { key: "closest_approach_date", label: "Approach date", kind: "string" },
      { key: "miss_distance_km", label: "Miss distance", kind: "number", unit: "km", digits: 0 },
      { key: "relative_velocity_km_s", label: "Relative velocity", kind: "number", unit: "km/s", digits: 3 },
      { key: "is_potentially_hazardous", label: "Potentially hazardous (NeoWs)", kind: "boolean" },
      { key: "is_sentry_object", label: "NeoWs 'Sentry object' flag", kind: "boolean" },
    ],
  },
  {
    key: "neows_physical",
    title: "NeoWs physical estimates",
    fields: [
      { key: "absolute_magnitude_h", label: "Absolute magnitude H", kind: "number", unit: "mag", digits: 2 },
      { key: "estimated_diameter_min_km", label: "Estimated diameter (min)", kind: "number", unit: "km", digits: 4 },
      { key: "estimated_diameter_max_km", label: "Estimated diameter (max)", kind: "number", unit: "km", digits: 4 },
    ],
  },
  {
    key: "orbit",
    title: "Orbit",
    fields: [
      { key: "orbit_class_name", label: "Orbit class", kind: "string" },
      { key: "semi_major_axis_au", label: "Semi-major axis a", kind: "number", unit: "AU", digits: 6 },
      { key: "eccentricity", label: "Eccentricity e", kind: "number", digits: 6 },
      { key: "inclination_deg", label: "Inclination i", kind: "number", unit: "deg", digits: 4 },
      { key: "ascending_node_longitude_deg", label: "Ascending node Ω", kind: "number", unit: "deg", digits: 4 },
      { key: "argument_of_perihelion_deg", label: "Argument of perihelion ω", kind: "number", unit: "deg", digits: 4 },
      { key: "mean_anomaly_deg", label: "Mean anomaly M", kind: "number", unit: "deg", digits: 4 },
      { key: "perihelion_distance_au", label: "Perihelion q", kind: "number", unit: "AU", digits: 6 },
      { key: "aphelion_distance_au", label: "Aphelion Q", kind: "number", unit: "AU", digits: 6 },
      { key: "orbital_period_days", label: "Orbital period", kind: "number", unit: "days", digits: 2 },
      { key: "epoch_jd", label: "Epoch", kind: "number", unit: "JD (TDB)", digits: 1 },
      { key: "is_pha", label: "PHA (SBDB)", kind: "boolean" },
    ],
  },
  {
    key: "physical",
    title: "Physical (SBDB)",
    fields: [
      { key: "absolute_magnitude", label: "Absolute magnitude H", kind: "number", unit: "mag", digits: 2 },
      { key: "estimated_diameter_km", label: "Diameter", kind: "number", unit: "km", digits: 4 },
      { key: "albedo", label: "Geometric albedo", kind: "number", digits: 3 },
      { key: "rotational_period_hr", label: "Rotation period", kind: "number", unit: "h", digits: 3 },
    ],
  },
  {
    key: "sentry_assessment",
    title: "Sentry assessment",
    fields: [
      { key: "impact_probability", label: "Cumulative impact probability (as published)", kind: "number" },
      { key: "potential_impacts_count", label: "Potential impacts listed", kind: "integer" },
      { key: "impact_year_range", label: "Year range of listed impacts", kind: "string" },
      { key: "palermo_scale_cum", label: "Palermo scale (cumulative)", kind: "number", digits: 2 },
      { key: "palermo_scale_max", label: "Palermo scale (max)", kind: "number", digits: 2 },
      { key: "torino_scale_max", label: "Torino scale (max)", kind: "integer" },
      { key: "v_infinity_km_s", label: "Velocity at infinity", kind: "number", unit: "km/s", digits: 3 },
      { key: "last_obs_date", label: "Last observation used", kind: "string" },
    ],
  },
];

/** Where each displayed section lives in the API response. */
export const SECTION_PATHS: Record<ProfileSectionKey, readonly string[]> = {
  identity: ["identity"],
  encounter: ["encounter"],
  neows_physical: ["neows_physical"],
  orbit: ["orbit"],
  physical: ["physical"],
  sentry_assessment: ["sentry", "assessment"],
};
