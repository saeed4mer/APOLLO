import type { FieldSpec, FieldValue, UnavailableReason } from "../models/profile";
import type { MatchState, SentryLinkageStatus } from "../models/world";

/**
 * Presentation only. Raw values are never changed in the model; formatting happens here.
 *  - null measurement -> "Unavailable" (never 0, never "No")
 *  - null flag        -> "Unknown"     (true -> "Yes", false -> "No")
 *  - every number keeps its unit
 */
export const UNAVAILABLE = "Unavailable";
export const UNKNOWN = "Unknown";
export const LOADING = "Loading…";

const numberFormats = new Map<number, Intl.NumberFormat>();
function numberFormat(digits: number): Intl.NumberFormat {
  let format = numberFormats.get(digits);
  if (!format) {
    format = new Intl.NumberFormat("en-US", { maximumFractionDigits: digits, minimumFractionDigits: 0 });
    numberFormats.set(digits, format);
  }
  return format;
}

export function formatNumber(value: number | null, unit?: string, digits?: number): string {
  if (value === null) return UNAVAILABLE;
  // Without a declared precision, show the value exactly as published (e.g. impact probabilities).
  const text = digits === undefined ? String(value) : numberFormat(digits).format(value);
  return unit ? `${text} ${unit}` : text;
}

export function formatFlag(value: boolean | null): string {
  return value === null ? UNKNOWN : value ? "Yes" : "No";
}

export function formatField(spec: FieldSpec, value: FieldValue): string {
  if (value === null) return spec.kind === "boolean" ? UNKNOWN : UNAVAILABLE;
  switch (spec.kind) {
    case "boolean": return formatFlag(value as boolean);
    case "number":
    case "integer": return formatNumber(value as number, spec.unit, spec.digits);
    case "string": return String(value);
  }
}

export const REASON_TEXT: Record<UnavailableReason, string> = {
  not_resolved: "identity not resolved, so this source cannot be linked",
  not_in_source: "not provided by the source",
  not_in_current_contract: "not present in the current dataset",
  ambiguous_linkage: "several source records are linked, so none is attributed",
};

export const SOURCE_LABELS: Record<string, string> = {
  nasa_neows: "NASA NeoWs",
  jpl_sbdb: "JPL SBDB",
  jpl_sentry: "JPL Sentry",
  entity_resolution: "Entity resolution",
};

export function sourceLabel(source: string | null): string {
  if (source === null) return "Identity resolution + crosswalk";
  return SOURCE_LABELS[source] ?? source;
}

/** Text for the contract's Sentry linkage status. Never derived from the PHA flag. */
export const SENTRY_STATUS_TEXT: Record<SentryLinkageStatus, string> = {
  available: "Linked Sentry record available",
  not_resolved: "Not linkable: identity not resolved",
  not_present: "No Sentry record linked",
  ambiguous: "Ambiguous: several Sentry records linked",
  linked_no_record: "Linked, but no Sentry record stored",
};

export const MATCH_STATE_TEXT: Record<MatchState, string> = {
  RESOLVED: "Resolved",
  UNRESOLVED: "Unresolved",
  AMBIGUOUS: "Ambiguous",
  INVALID: "Invalid identifier",
};

export function formatKmCompact(km: number): string {
  if (!Number.isFinite(km)) return UNAVAILABLE;
  return `${numberFormat(km >= 100 ? 0 : 1).format(km)} km`;
}
