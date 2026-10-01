/// <reference types="vitest" />
import { defineConfig } from "vite";

// The renderer only talks to the FastAPI serving layer. In development, /api is proxied
// to the API so browser and API share one origin: no CORS configuration is required.
const env = (globalThis as { process?: { env: Record<string, string | undefined> } }).process?.env ?? {};
const API_TARGET = env.ASTEROID_API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: API_TARGET,
        changeOrigin: false,
        rewrite: (path) => path.replace(/^\/api/, ""),
        // API down: answer 502 (gateway could not reach upstream) so the UI says "could not be reached".
        configure: (proxy) => {
          proxy.on("error", (_error, _request, response) => {
            const res = response as unknown as { headersSent?: boolean; writeHead?: (s: number, h: object) => void; end: (b: string) => void };
            if (typeof res.writeHead !== "function" || res.headersSent) return;
            res.writeHead(502, { "Content-Type": "application/json" });
            res.end(JSON.stringify({ error: { code: "UPSTREAM_UNAVAILABLE", message: "The API server could not be reached." } }));
          });
        },
      },
    },
  },
  test: {
    environment: "jsdom",
    globals: true,
    include: ["test/**/*.test.ts"],
    restoreMocks: true,
  },
});
