/**
 * Typed API failures. The UI branches on `kind`, never on message text.
 *
 * - network:   the service could not be reached
 * - timeout:   no response within REQUEST_TIMEOUT_MS
 * - aborted:   the caller cancelled the request (e.g. a newer selection superseded it)
 * - not_found: the platform's TARGET_NOT_FOUND (HTTP 404)
 * - server:    HTTP 5xx
 * - http:      any other non-2xx status (e.g. 422 for a malformed ID)
 * - malformed: the response was not the contract the renderer expects
 */
export type ApiErrorKind = "network" | "timeout" | "aborted" | "not_found" | "server" | "http" | "malformed";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;

  constructor(kind: ApiErrorKind, message: string, options: { status?: number | null; code?: string | null } = {}) {
    super(message);
    this.name = "ApiError";
    this.kind = kind;
    this.status = options.status ?? null;
    this.code = options.code ?? null;
  }
}

export function isAbort(error: unknown): boolean {
  return error instanceof ApiError && error.kind === "aborted";
}
