/**
 * Development diagnostics. Logs lifecycle and data events only (never per frame), and never
 * secrets: the browser holds no credentials, and only API paths and counts are logged.
 */
const enabled = import.meta.env.DEV && import.meta.env.MODE !== "test";

export const log = {
  info(event: string, details?: Record<string, unknown>): void {
    if (enabled) console.info(`[asteroid-renderer] ${event}`, details ?? "");
  },
  warn(event: string, details?: Record<string, unknown>): void {
    if (enabled) console.warn(`[asteroid-renderer] ${event}`, details ?? "");
  },
};
