import type { ValidatedWorld } from "../api/validateWorld";
import type { WorldRecord } from "../models/world";

/**
 * DEVELOPMENT-ONLY stress data (?stress=N). These records are NOT real asteroids: IDs start at
 * 900000000, names start with "SYNTHETIC", and the app shows a banner while they are loaded.
 * They exist solely to measure renderer performance at populations the real dataset lacks.
 */
export const SYNTHETIC_ID_BASE = 900_000_000;

export function withSyntheticRecords(world: ValidatedWorld, count: number): ValidatedWorld {
  let seed = 4242;
  const next = (): number => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
  const synthetic: WorldRecord[] = [];
  for (let i = 0; i < count; i++) {
    const z = 1 - 2 * next();
    const phi = 2 * Math.PI * next();
    const r = Math.sqrt(Math.max(0, 1 - z * z));
    synthetic.push({
      neows_id: String(SYNTHETIC_ID_BASE + i),
      name: `SYNTHETIC ${String(i + 1).padStart(4, "0")}`,
      asteroid_key: null,
      encounter: {
        source: "nasa_neows",
        closest_approach_date: "1970-01-01",
        close_approach_datetime: null,
        miss_distance_km: 10 ** (4 + 4 * next()),
        relative_velocity_km_s: null,
        estimated_diameter_min_km: null,
        estimated_diameter_max_km: null,
        is_potentially_hazardous: null,
      },
      resolution: { match_state: "UNRESOLVED", match_rule: "SYNTHETIC_STRESS_DATA", resolved_at: null },
      sbdb: { status: "not_resolved", spkid: null, snapshot_key: null, run_id: null },
      sentry: { status: "not_resolved", sentry_id: null, latest_snapshot_key: null, run_id: null, in_latest_catalog: null },
      illustrative_direction: { x: r * Math.cos(phi), y: r * Math.sin(phi), z },
    });
  }
  return { ...world, records: [...world.records, ...synthetic] };
}
