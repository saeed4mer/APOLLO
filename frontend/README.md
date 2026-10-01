# Asteroid Intelligence Renderer (M7)

An immersive, source-grounded view of the platform's asteroid intelligence. The renderer is a
**consumer of the FastAPI serving layer only**: it never reads Parquet/SQLite, never imports Python
modules, never re-derives directions, resolution, or Sentry membership, and holds no credentials.

```
Browser ─▶ src/app.ts ─▶ src/api/client.ts ─▶ /api (Vite proxy) ─▶ FastAPI
                │                                  GET /asteroids/world            (once, at load)
                │                                  GET /asteroids/{id}/profile     (on selection only)
                ▼
   validated models ─▶ WorldRenderer (Three.js) ─▶ markers, hover, selection
                   └─▶ DOM views (status, legend, tooltip, profile)
```

## Run it

```bash
# 1. API (repository root)
uvicorn api.main:app --host 127.0.0.1 --port 8000

# 2. Renderer (this directory; Node.js LTS)
npm install
npm run dev            # http://127.0.0.1:5173  (proxies /api -> 127.0.0.1:8000)
```

`VITE_API_BASE_URL` (default `/api`) is the single API location setting (`src/config.ts`).
`ASTEROID_API_TARGET` changes where the dev proxy forwards (default `http://127.0.0.1:8000`).
Because the dev server proxies the API, browser and API share one origin: **no CORS is configured**.

| Command | What it does |
|---|---|
| `npm test` | Unit + integration tests (Vitest, jsdom, real API fixtures) |
| `npm run typecheck` | Strict TypeScript check |
| `npm run build` | Typecheck + production bundle in `dist/` |
| `npm run e2e` | Interaction torture test in a real browser (see below) |

## Technology decision

**Three.js + TypeScript + Vite, plain DOM for UI.** Three.js gives instanced rendering, raycast
picking by instance, full camera/loop control, and room for terrain and orbit curves, with
first-class types. No UI framework: one module owns the single animation frame and every listener
has an explicit `dispose()`, which removes the render/update-loop class of bugs a reactive layer
between state and the canvas invites. Rejected: Babylon.js (game engine, heavier than needed),
deck.gl (geospatial layers, wrong fit for a stylized world), CesiumJS (real geodesy would imply an
accuracy the illustrative spatial model disclaims), react-three-fiber (render-cycle coupling).

Dependencies (exact-pinned): `three` (runtime); `typescript`, `vite`, `vitest`, `jsdom`,
`@types/three`, `playwright-core` (development only; drives an installed Chrome/Edge, no download).

## Spatial model

| Concern | Rule | Where |
|---|---|---|
| Direction | `illustrative_direction` from the API, used unchanged (identity mapping); never generated, re-seeded or random | `scene/coordinates.ts` |
| Axes | Right-handed: +X right, +Y up, +Z toward the viewer; origin = Earth placeholder; camera on +Z looking at the origin | `scene/coordinates.ts` |
| Distance | `r = 1 + 3·log10(miss_km / 6371)`: strictly increasing, fixed constants (never fitted to the data), Earth radius lands on the placeholder surface | `scene/scale.ts` |
| Zoom | Camera distance 2.5–45 units ≈ 7,240 km – 2.9×10¹⁰ km visualization radius; wheel sets a target, the single loop eases toward it and settles | `scene/zoom.ts` |
| Markers | Colour = NeoWs PHA flag (yes / no / unknown, not red); size = fixed on-screen size at every depth and zoom, **not** physical size | `renderer/WorldRenderer.ts` |

The legend states that placement is illustrative, distance follows the real miss distance on a
logarithmic visualization scale, and marker size is not physical. The direction algorithm must be
`sha256-uniform-sphere-v1`; any other value is refused rather than silently reinterpreted.

## State model

```
world:    LOADING ──ok──▶ READY
             └──fail──▶ ERROR ──Retry──▶ LOADING
hover:    pointer over marker ─▶ hoveredId (tooltip from loaded world data; no request)
select:   click ─▶ URL #/asteroid/<id> ─▶ hashchange ─▶ selectedId ─▶ PROFILE LOADING ─▶ READY | ERROR(404 = not found)
return:   Back / browser back ─▶ URL #/ ─▶ selectedId = null ─▶ profile request aborted, PROFILE IDLE
```

The URL is the only path that changes selection, so one click yields one selection, refresh
restores it, and browser Back returns to the world. Camera focus animation is deferred (M7.2).

## Lifecycle and correctness guarantees

- **One render loop:** `WorldRenderer.start()` is idempotent; `activeLoops` is 0 or 1 and is asserted in tests.
- **No zoom loop:** input changes only the target; the loop eases and stops when settled; NaN/Infinity input is ignored.
- **No stale profiles:** each load aborts the previous request and is applied only if its token is current *and* its ID is still selected.
- **Cleanup:** `dispose()` cancels the frame, disconnects the ResizeObserver, removes every listener, frees GPU resources, and clears the DOM. Vite HMR disposes the old app first.
- **No N+1:** one world request at load; a profile request only when an asteroid is selected; hover uses loaded data.
- **No invented values:** `null` displays as *Unavailable* (measurements) or *Unknown* (flags) with the contract's reason; loading displays *Loading…*; numbers always carry units; formatting never changes the model.
- **Source separation:** every profile section shows its source (NASA NeoWs, JPL SBDB, JPL Sentry); Sentry status is the contract's crosswalk status, never inferred from PHA or `is_sentry_object`.
- **Untrusted input:** responses are validated; a bad record is excluded and reported (never repaired); a broken envelope or profile fails visibly. All text is inserted with `textContent`.

## Interaction torture test

`npm run e2e` starts its own API (port 8765) and Vite server (port 5174), drives the installed
Chrome/Edge (`CHROME_PATH` to override), and fails on any unexpected console error, page error,
NaN/Infinity, duplicated loop/listener, or displayed value that differs from the API. It covers
hover, rapid zoom bursts, A→B rapid selection, 12 open/close cycles, browser Back, repeated
resizes, refresh with a selection, rapid reloads, a 404 asteroid, and an API outage with recovery.
Screenshots and `report.json` are written to `e2e/artifacts/` (git-ignored).
