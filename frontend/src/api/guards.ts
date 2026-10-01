/** Small, explicit type guards for validating untrusted API JSON. */

export type Json = Record<string, unknown>;

export function isObject(value: unknown): value is Json {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

export function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

export function isNullableBoolean(value: unknown): value is boolean | null {
  return value === null || typeof value === "boolean";
}

export function isNullableFinite(value: unknown): value is number | null {
  return value === null || isFiniteNumber(value);
}

export function isOneOf<T extends string>(value: unknown, allowed: readonly T[]): value is T {
  return typeof value === "string" && (allowed as readonly string[]).includes(value);
}

/** Thrown internally for a single bad record; carries a human-readable reason. */
export class RecordInvalid extends Error {}

export function require(condition: boolean, reason: string): asserts condition {
  if (!condition) throw new RecordInvalid(reason);
}
