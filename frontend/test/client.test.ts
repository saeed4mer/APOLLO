import { fetchProfile, fetchWorld, getJson, type FetchImpl } from "../src/api/client";
import { ApiError } from "../src/api/errors";
import { fixture } from "./helpers";

const json = (status: number, body: unknown): Response =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected the request to fail");
}

describe("API client", () => {
  it("requests the world exactly once, from the configured base URL", async () => {
    const fetchImpl = vi.fn<FetchImpl>(async () => json(200, fixture("world.json")));
    const world = await fetchWorld({ fetchImpl });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expect(fetchImpl.mock.calls[0]![0]).toBe("/api/asteroids/world");
    expect(world.records).toHaveLength(35);
  });

  it("requests a profile by NeoWs ID only", async () => {
    const fetchImpl = vi.fn<FetchImpl>(async () => json(200, fixture("profile_3548666.json")));
    const profile = await fetchProfile("3548666", { fetchImpl });
    expect(fetchImpl.mock.calls[0]![0]).toBe("/api/asteroids/3548666/profile");
    expect(profile.neows_id).toBe("3548666");
  });

  it("maps the platform's 404 to not_found with its error code", async () => {
    const error = await failure(fetchProfile("99999999", { fetchImpl: async () => json(404, fixture("profile_404.json")) }));
    expect(error.kind).toBe("not_found");
    expect(error.code).toBe("TARGET_NOT_FOUND");
  });

  it("maps 5xx to server", async () => {
    const error = await failure(getJson("/x", { fetchImpl: async () => json(500, { detail: "boom" }) }));
    expect(error.kind).toBe("server");
    expect(error.status).toBe(500);
  });

  it.each([502, 503, 504])("maps gateway status %i to network (service unreachable)", async (status) => {
    const error = await failure(getJson("/x", { fetchImpl: async () => json(status, { error: { code: "UPSTREAM_UNAVAILABLE" } }) }));
    expect(error.kind).toBe("network");
  });

  it("an HTML 500 error page is a server error, not a contract violation", async () => {
    const error = await failure(getJson("/x", { fetchImpl: async () => new Response("<h1>Internal Server Error</h1>", { status: 500 }) }));
    expect(error.kind).toBe("server");
  });

  it("maps 422 to http (malformed ID), not to not_found", async () => {
    const error = await failure(getJson("/x", { fetchImpl: async () => json(422, { detail: [] }) }));
    expect(error.kind).toBe("http");
  });

  it("maps a network failure to network", async () => {
    const error = await failure(getJson("/x", { fetchImpl: async () => { throw new TypeError("Failed to fetch"); } }));
    expect(error.kind).toBe("network");
  });

  it("maps a non-JSON body to malformed", async () => {
    const error = await failure(getJson("/x", { fetchImpl: async () => new Response("<html>", { status: 200 }) }));
    expect(error.kind).toBe("malformed");
  });

  it("maps a contract violation to malformed", async () => {
    const error = await failure(fetchWorld({ fetchImpl: async () => json(200, { data: "nope" }) }));
    expect(error.kind).toBe("malformed");
  });

  it("rejects a profile response for a different asteroid", async () => {
    const error = await failure(fetchProfile("3830890", { fetchImpl: async () => json(200, fixture("profile_3548666.json")) }));
    expect(error.kind).toBe("malformed");
  });

  it("times out a request that never answers", async () => {
    vi.useFakeTimers();
    try {
      const hanging: FetchImpl = (_input, init) =>
        new Promise((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
      const pending = failure(getJson("/x", { fetchImpl: hanging, timeoutMs: 1000 }));
      await vi.advanceTimersByTimeAsync(1000);
      expect((await pending).kind).toBe("timeout");
    } finally {
      vi.useRealTimers();
    }
  });

  it("reports a caller cancellation as aborted (not as an error to display)", async () => {
    const controller = new AbortController();
    const hanging: FetchImpl = (_input, init) =>
      new Promise((_resolve, reject) => init?.signal?.addEventListener("abort", () => reject(new DOMException("aborted", "AbortError"))));
    const pending = failure(getJson("/x", { fetchImpl: hanging, signal: controller.signal }));
    controller.abort();
    expect((await pending).kind).toBe("aborted");
  });
});
