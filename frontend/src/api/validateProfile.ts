import {
  PROFILE_SPEC, SECTION_PATHS,
  type AsteroidProfile, type FieldKind, type FieldValue, type ProfileSection, type ProfileSectionKey,
  type SectionAvailability, type UnavailableReason,
} from "../models/profile";
import { ApiError } from "./errors";
import { isFiniteNumber, isObject, type Json } from "./guards";

const REASONS: readonly UnavailableReason[] = ["not_resolved", "not_in_source", "not_in_current_contract", "ambiguous_linkage"];

/**
 * Validate GET /asteroids/{id}/profile for every field the renderer displays (see PROFILE_SPEC).
 * Any type mismatch fails the profile: a partially trusted dossier is worse than an error.
 */
export function validateProfileResponse(body: unknown): AsteroidProfile {
  const data = isObject(body) ? body.data : undefined;
  if (!isObject(data) || typeof data.neows_id !== "string") {
    throw new ApiError("malformed", "Profile response is missing 'data.neows_id'");
  }
  const sections = {} as Record<ProfileSectionKey, ProfileSection>;
  for (const spec of PROFILE_SPEC) {
    const node = SECTION_PATHS[spec.key].reduce<unknown>((acc, key) => (isObject(acc) ? acc[key] : undefined), data);
    if (!isObject(node)) throw new ApiError("malformed", `Profile section '${spec.key}' is missing`);
    const values: Record<string, FieldValue> = {};
    for (const field of spec.fields) {
      const value = node[field.key];
      if (!matchesKind(value, field.kind)) {
        throw new ApiError("malformed", `Profile field '${spec.key}.${field.key}' is not a ${field.kind} or null`);
      }
      values[field.key] = value;
    }
    sections[spec.key] = { source: sectionSource(spec.key, node, data), values, availability: availability(node.availability) };
  }

  const sentry = data.sentry as Json;
  const provenance = data.provenance;
  if (typeof sentry.status !== "string" || typeof sentry.source_contract !== "string" || !isObject(provenance)) {
    throw new ApiError("malformed", "Profile sentry/provenance blocks do not match the contract");
  }
  const flatProvenance: Record<string, Record<string, FieldValue>> = {};
  for (const [source, block] of Object.entries(provenance)) {
    if (!isObject(block)) throw new ApiError("malformed", `Provenance '${source}' is not an object`);
    flatProvenance[source] = Object.fromEntries(
      Object.entries(block).filter(([, v]) => v === null || ["string", "number", "boolean"].includes(typeof v)),
    ) as Record<string, FieldValue>;
  }
  return {
    neows_id: data.neows_id,
    sections,
    sentryStatus: sentry.status,
    sentrySourceContract: sentry.source_contract,
    provenance: flatProvenance,
  };
}

function matchesKind(value: unknown, kind: FieldKind): value is FieldValue {
  if (value === null) return true;
  switch (kind) {
    case "number": return isFiniteNumber(value);
    case "integer": return Number.isInteger(value);
    case "boolean": return typeof value === "boolean";
    case "string": return typeof value === "string";
  }
}

function sectionSource(key: ProfileSectionKey, node: Json, data: Json): string | null {
  if (typeof node.source === "string") return node.source;
  if (key === "sentry_assessment" && isObject(data.sentry) && typeof data.sentry.source === "string") return data.sentry.source;
  return null; // identity spans resolution + crosswalk by design
}

function availability(raw: unknown): SectionAvailability | null {
  if (raw === undefined) return null;
  if (!isObject(raw) || !["available", "partial", "unavailable"].includes(raw.status as string) || !isObject(raw.unavailable)) {
    throw new ApiError("malformed", "Section availability does not match the contract");
  }
  for (const reason of Object.values(raw.unavailable)) {
    if (!REASONS.includes(reason as UnavailableReason)) throw new ApiError("malformed", `Unknown unavailable reason '${String(reason)}'`);
  }
  return raw as unknown as SectionAvailability;
}
