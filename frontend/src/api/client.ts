import { API_BASE_URL, REQUEST_TIMEOUT_MS } from "../config";
import { log } from "../utils/log";
import { ApiError } from "./errors";
import { validateProfileResponse } from "./validateProfile";
import { validateWorldResponse, type ValidatedWorld } from "./validateWorld";
import type { AsteroidProfile } from "../models/profile";

/** Bad gateway / unavailable / gateway timeout: the API itself was not reached. */
const GATEWAY_STATUSES = new Set([502, 503, 504]);

export type FetchImpl = (input: string, init?: RequestInit) => Promise<Response>;

export interface RequestOptions {
  signal?: AbortSignal;
  timeoutMs?: number;
  fetchImpl?: FetchImpl;
}

/**
 * GET a JSON document from the serving layer, mapping every failure to a typed ApiError.
 * The caller's signal cancels the request; an internal timer enforces the timeout.
 */
export async function getJson(path: string, options: RequestOptions = {}): Promise<unknown> {
  const fetchImpl = options.fetchImpl ?? ((input, init) => fetch(input, init));
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, options.timeoutMs ?? REQUEST_TIMEOUT_MS);
  const onCallerAbort = (): void => controller.abort();
  if (options.signal?.aborted) controller.abort();
  options.signal?.addEventListener("abort", onCallerAbort, { once: true });

  try {
    let response: Response;
    try {
      response = await fetchImpl(`${API_BASE_URL}${path}`, {
        signal: controller.signal,
        headers: { Accept: "application/json" },
      });
    } catch {
      if (timedOut) throw new ApiError("timeout", `Request to ${path} timed out`);
      if (options.signal?.aborted) throw new ApiError("aborted", `Request to ${path} was cancelled`);
      throw new ApiError("network", "The intelligence service could not be reached");
    }

    let body: unknown = undefined;
    let isJson = true;
    try {
      body = await response.json();
    } catch {
      if (timedOut) throw new ApiError("timeout", `Request to ${path} timed out`);
      if (options.signal?.aborted) throw new ApiError("aborted", `Request to ${path} was cancelled`);
      isJson = false;
    }

    // Classify by HTTP status first: an error page is an error, not a contract violation.
    if (!response.ok) {
      const status = response.status;
      const code = errorCode(body);
      if (status === 404) throw new ApiError("not_found", `Not found: ${path}`, { status, code });
      if (GATEWAY_STATUSES.has(status)) throw new ApiError("network", "The intelligence service could not be reached", { status, code });
      if (status >= 500) throw new ApiError("server", `Server error ${status}`, { status, code });
      throw new ApiError("http", `Request failed with status ${status}`, { status, code });
    }
    if (!isJson) throw new ApiError("malformed", `Response from ${path} was not valid JSON`, { status: response.status });
    return body;
  } finally {
    clearTimeout(timer);
    options.signal?.removeEventListener("abort", onCallerAbort);
  }
}

function errorCode(body: unknown): string | null {
  if (typeof body === "object" && body !== null && "error" in body) {
    const error = (body as { error: unknown }).error;
    if (typeof error === "object" && error !== null && typeof (error as { code?: unknown }).code === "string") {
      return (error as { code: string }).code;
    }
  }
  return null;
}

/** One request for the whole world (the endpoint is set-based; never fetch per asteroid). */
export async function fetchWorld(options: RequestOptions = {}): Promise<ValidatedWorld> {
  log.info("world request started");
  const started = performance.now();
  const world = validateWorldResponse(await getJson("/asteroids/world", options));
  log.info("world request completed", {
    ms: Math.round(performance.now() - started),
    received: world.received,
    accepted: world.records.length,
    rejected: world.rejected.length,
  });
  return world;
}

/** Profile for one asteroid, addressed only by its NeoWs ID. */
export async function fetchProfile(neowsId: string, options: RequestOptions = {}): Promise<AsteroidProfile> {
  log.info("profile request started", { neowsId });
  const profile = validateProfileResponse(await getJson(`/asteroids/${encodeURIComponent(neowsId)}/profile`, options));
  if (profile.neows_id !== neowsId) {
    throw new ApiError("malformed", `Profile response is for ${profile.neows_id}, not ${neowsId}`);
  }
  log.info("profile request completed", { neowsId });
  return profile;
}
