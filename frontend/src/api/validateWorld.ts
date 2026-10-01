import { SUPPORTED_DIRECTION_ALGORITHMS } from "../config";
import {
  MATCH_STATES, SBDB_STATUSES, SENTRY_STATUSES,
  type WorldRecord, type WorldSnapshotInfo,
} from "../models/world";
import { log } from "../utils/log";
import { ApiError } from "./errors";
import {
  isFiniteNumber, isNullableBoolean, isNullableFinite, isNullableString, isObject, isOneOf,
  RecordInvalid, require, type Json,
} from "./guards";

/** |direction| must equal 1 within this tolerance; the backend emits unit vectors. */
export const UNIT_VECTOR_TOLERANCE = 1e-6;
const NEOWS_ID = /^[1-9]\d*$/;

export interface RejectedRecord {
  index: number;
  neows_id: string | null;
  reason: string;
}

export interface ValidatedWorld {
  snapshot: WorldSnapshotInfo;
  records: WorldRecord[];
  rejected: RejectedRecord[];
  received: number;
}

/**
 * Validate GET /asteroids/world. A broken envelope fails the whole load (ApiError "malformed");
 * an individual bad record is excluded and reported, never rendered and never "repaired".
 * Records whose neows_id appears more than once are all excluded: there is no way to know which is right.
 */
export function validateWorldResponse(body: unknown): ValidatedWorld {
  if (!isObject(body) || !Array.isArray(body.data) || !isObject(body.world)) {
    throw new ApiError("malformed", "World response is missing 'world' or 'data'");
  }
  const snapshot = validateSnapshotInfo(body.world);

  const rejected: RejectedRecord[] = [];
  const candidates: { index: number; record: WorldRecord }[] = [];
  body.data.forEach((raw, index) => {
    try {
      candidates.push({ index, record: validateRecord(raw) });
    } catch (error) {
      if (!(error instanceof RecordInvalid)) throw error;
      const id = isObject(raw) && typeof raw.neows_id === "string" ? raw.neows_id : null;
      rejected.push({ index, neows_id: id, reason: error.message });
    }
  });

  const counts = new Map<string, number>();
  for (const { record } of candidates) counts.set(record.neows_id, (counts.get(record.neows_id) ?? 0) + 1);
  const records: WorldRecord[] = [];
  for (const { index, record } of candidates) {
    if ((counts.get(record.neows_id) ?? 0) > 1) {
      rejected.push({ index, neows_id: record.neows_id, reason: "duplicate neows_id" });
    } else {
      records.push(record);
    }
  }

  rejected.sort((a, b) => a.index - b.index);
  if (rejected.length > 0) log.warn("world records rejected", { rejected });
  return { snapshot, records, rejected, received: body.data.length };
}

function validateSnapshotInfo(world: Json): WorldSnapshotInfo {
  const spatial = world.spatial_model;
  const neows = world.neows;
  if (
    !isObject(spatial) || spatial.direction_semantics !== "illustrative" ||
    typeof spatial.direction_algorithm !== "string" || typeof spatial.note !== "string" ||
    !isObject(neows) || !Number.isInteger(world.object_count) ||
    !Array.isArray(world.neows_fields_not_in_dataset)
  ) {
    throw new ApiError("malformed", "World snapshot metadata does not match the contract");
  }
  if (!SUPPORTED_DIRECTION_ALGORITHMS.includes(spatial.direction_algorithm)) {
    // A different spatial model must not be silently reinterpreted with this renderer's mapping.
    throw new ApiError("malformed", `Unsupported direction algorithm '${spatial.direction_algorithm}'`);
  }
  return world as unknown as WorldSnapshotInfo;
}

function validateRecord(raw: unknown): WorldRecord {
  require(isObject(raw), "record is not an object");
  require(typeof raw.neows_id === "string" && NEOWS_ID.test(raw.neows_id), "missing or malformed neows_id");
  require(typeof raw.name === "string" && raw.name.length > 0, "missing name");
  require(isNullableString(raw.asteroid_key), "asteroid_key has the wrong type");

  const enc = raw.encounter;
  require(isObject(enc), "missing encounter");
  require(typeof enc.closest_approach_date === "string", "missing closest_approach_date");
  require(isFiniteNumber(enc.miss_distance_km) && enc.miss_distance_km > 0, "miss_distance_km is not a positive finite number");
  require(isNullableString(enc.close_approach_datetime), "close_approach_datetime has the wrong type");
  require(isNullableBoolean(enc.is_potentially_hazardous), "is_potentially_hazardous is not true/false/null");
  for (const field of ["relative_velocity_km_s", "estimated_diameter_min_km", "estimated_diameter_max_km"] as const) {
    require(isNullableFinite(enc[field]), `${field} is not a finite number or null`);
  }

  const res = raw.resolution;
  require(isObject(res) && isOneOf(res.match_state, MATCH_STATES), "unknown resolution.match_state");
  const sbdb = raw.sbdb;
  require(isObject(sbdb) && isOneOf(sbdb.status, SBDB_STATUSES), "unknown sbdb.status");
  const sentry = raw.sentry;
  require(isObject(sentry) && isOneOf(sentry.status, SENTRY_STATUSES), "unknown sentry.status");
  require(isNullableBoolean(sentry.in_latest_catalog), "sentry.in_latest_catalog is not true/false/null");

  const dir = raw.illustrative_direction;
  require(isObject(dir), "missing illustrative_direction");
  const { x, y, z } = dir;
  require(isFiniteNumber(x) && isFiniteNumber(y) && isFiniteNumber(z), "illustrative_direction has non-finite components");
  const norm = Math.hypot(x, y, z);
  require(Math.abs(norm - 1) <= UNIT_VECTOR_TOLERANCE, `illustrative_direction is not a unit vector (|v| = ${norm})`);

  return raw as unknown as WorldRecord;
}
