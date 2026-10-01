import { PROFILE_SPEC, type AsteroidProfile, type ProfileSectionKey, type UnavailableReason } from "../models/profile";
import type { MatchState, SentryLinkageStatus } from "../models/world";
import { formatField, MATCH_STATE_TEXT, REASON_TEXT, SENTRY_STATUS_TEXT, sourceLabel } from "./format";

/**
 * Focus-view callouts. Rules:
 * - A callout lists only values the source actually has (no columns of "Unavailable").
 * - A section with no values collapses into one "not available" line carrying the contract's reason.
 * - A partially populated section says how many fields are missing and why.
 * - The Sentry callout exists only when the crosswalk links a Sentry record; its status text is
 *   the contract's, never inferred from the PHA flag or NeoWs is_sentry_object.
 */
export interface Callout {
  key: string;
  title: string;
  source: string;
  rows: { label: string; value: string }[];
  note: string | null;
}

export interface UnavailableSection {
  title: string;
  source: string;
  reason: string;
}

export interface CalloutModel {
  callouts: Callout[];
  unavailable: UnavailableSection[];
}

const TITLES: Record<ProfileSectionKey, string> = {
  identity: "Identity",
  encounter: "Encounter",
  neows_physical: "Physical estimates",
  orbit: "Orbit",
  physical: "Physical",
  sentry_assessment: "Sentry",
};

/** Callout order = slot order around the asteroid. */
const ORDER: ProfileSectionKey[] = ["identity", "encounter", "orbit", "sentry_assessment", "neows_physical", "physical"];

function dominantReason(reasons: UnavailableReason[]): string {
  const counts = new Map<UnavailableReason, number>();
  for (const r of reasons) counts.set(r, (counts.get(r) ?? 0) + 1);
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
  return top ? REASON_TEXT[top] : "not provided by the source";
}

export function buildCallouts(profile: AsteroidProfile): CalloutModel {
  const callouts: Callout[] = [];
  const unavailable: UnavailableSection[] = [];

  for (const key of ORDER) {
    const spec = PROFILE_SPEC.find((s) => s.key === key)!;
    const section = profile.sections[key];
    const source = sourceLabel(section.source);
    const title = TITLES[key];

    if (key === "sentry_assessment") {
      const status = profile.sentryStatus as SentryLinkageStatus;
      if (status !== "available") {
        unavailable.push({ title, source, reason: SENTRY_STATUS_TEXT[status] ?? profile.sentryStatus });
        continue;
      }
    }

    const rows: Callout["rows"] = [];
    const missing: UnavailableReason[] = [];
    for (const field of spec.fields) {
      const value = section.values[field.key] ?? null;
      if (value === null) {
        missing.push(section.availability?.unavailable[field.key] ?? "not_in_source");
        continue;
      }
      const text = key === "identity" && field.key === "match_state" ? MATCH_STATE_TEXT[value as MatchState] ?? String(value) : formatField(field, value);
      rows.push({ label: field.label, value: text });
    }

    if (rows.length === 0) {
      unavailable.push({ title, source, reason: dominantReason(missing) });
      continue;
    }
    if (key === "sentry_assessment") rows.unshift({ label: "Linkage", value: SENTRY_STATUS_TEXT.available });
    const note = missing.length > 0
      ? `${missing.length} more field${missing.length === 1 ? "" : "s"}: ${dominantReason(missing)}`
      : key === "sentry_assessment" ? `Published values only (${profile.sentrySourceContract}); no score is derived.` : null;
    callouts.push({ key, title, source, rows, note });
  }
  return { callouts, unavailable };
}

/** Compact provenance line: which snapshot/run each source came from (only recorded values). */
export function provenanceLines(profile: AsteroidProfile): string[] {
  return Object.entries(profile.provenance).map(([source, block]) => {
    const parts = Object.entries(block)
      .filter(([key, value]) => key !== "source" && value !== null)
      .map(([key, value]) => `${key} ${String(value)}`);
    return `${sourceLabel(source)}: ${parts.length ? parts.join(" · ") : "no snapshot metadata recorded"}`;
  });
}
