/**
 * Typed views of GET /asteroids/world. Field names mirror the API exactly (no renaming, no
 * reinterpretation); nullable fields stay nullable so true / false / unknown remain distinct.
 */

export type MatchState = "RESOLVED" | "UNRESOLVED" | "AMBIGUOUS" | "INVALID";
export type SbdbStatus = "available" | "not_resolved" | "not_present";
export type SentryLinkageStatus = "available" | "not_resolved" | "not_present" | "ambiguous" | "linked_no_record";

export const MATCH_STATES: readonly MatchState[] = ["RESOLVED", "UNRESOLVED", "AMBIGUOUS", "INVALID"];
export const SBDB_STATUSES: readonly SbdbStatus[] = ["available", "not_resolved", "not_present"];
export const SENTRY_STATUSES: readonly SentryLinkageStatus[] = [
  "available", "not_resolved", "not_present", "ambiguous", "linked_no_record",
];

export interface WorldEncounter {
  source: "nasa_neows";
  closest_approach_date: string;
  close_approach_datetime: string | null;
  miss_distance_km: number;
  relative_velocity_km_s: number | null;
  estimated_diameter_min_km: number | null;
  estimated_diameter_max_km: number | null;
  is_potentially_hazardous: boolean | null;
}

export interface UnitVector {
  x: number;
  y: number;
  z: number;
}

export interface WorldRecord {
  neows_id: string;
  name: string;
  asteroid_key: string | null;
  encounter: WorldEncounter;
  resolution: { match_state: MatchState; match_rule: string | null; resolved_at: string | null };
  sbdb: { status: SbdbStatus; spkid: string | null; snapshot_key: string | null; run_id: string | null };
  sentry: {
    status: SentryLinkageStatus;
    sentry_id: string | null;
    latest_snapshot_key: string | null;
    run_id: string | null;
    in_latest_catalog: boolean | null;
  };
  illustrative_direction: UnitVector;
}

export interface WorldSnapshotInfo {
  object_count: number;
  encounter_selection_rule: string;
  sentry_latest_catalog_snapshot_key: string | null;
  neows_fields_not_in_dataset: string[];
  neows: {
    source: string;
    dataset_run_id: string | null;
    source_raw_file: string | null;
    source_raw_sha256: string | null;
  };
  spatial_model: {
    direction_semantics: "illustrative";
    direction_algorithm: string;
    direction_seed_field: string;
    distance_field: string;
    note: string;
  };
}
