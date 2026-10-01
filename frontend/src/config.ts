/**
 * Single configuration point for the renderer.
 *
 * API_BASE_URL is the only place the serving-layer location is defined. In development it
 * defaults to "/api", which Vite proxies to the FastAPI server (see vite.config.ts).
 */
export const API_BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? "/api").replace(/\/+$/, "");

/** Abort any single API request that takes longer than this. */
export const REQUEST_TIMEOUT_MS = 15_000;

/** The direction algorithm this renderer knows how to interpret (see GET /asteroids/world spatial_model). */
export const SUPPORTED_DIRECTION_ALGORITHMS: readonly string[] = ["sha256-uniform-sphere-v1"];
