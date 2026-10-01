# Asteroid Intelligence Renderer (M7)

An immersive, source-grounded view of the platform's asteroid intelligence: Earth's curved horizon
below, the asteroid field in the sky above, and a scroll-driven journey from sky into space. The
renderer is a **consumer of the FastAPI serving layer only**: it never reads Parquet/SQLite, never
imports Python modules, never re-derives directions, resolution or Sentry membership, and holds no
credentials.

```
Browser ─▶ src/app.ts ─▶ src/api/client.ts ─▶ /api (Vite proxy) ─▶ FastAPI
                │                                  GET /asteroids/world            (once, at load)
                │                                  GET /asteroids/{id}/profile     (on selection only)
                ▼
   validated models ─▶ WorldRenderer (Three.js, one loop) ─▶ Earth arc, sky, asteroids, focus camera
                   └─▶ DOM views: HUD, progressive labels, hover card, focus callouts
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
`ASTEROID_API_TARGET` changes where the dev proxy forwards. Because the dev server proxies the API,
browser and API share one origin: **no CORS is configured**.

| Command | What it does |
|---|---|
| `npm test` | Unit + integration tests (Vitest, jsdom, real API fixtures) |
| `npm run typecheck` | Strict TypeScript check |
| `npm run build` | Typecheck + production bundle in `dist/` |
| `npm run e2e` | Real-browser torture test (see below) |

Development-only: `?stress=N` appends N clearly-labelled **synthetic** records (IDs ≥ 900000000,
names `SYNTHETIC …`, red banner) for performance testing. It does not exist in production builds.

## Technology decision

**Three.js + TypeScript + Vite, plain DOM for UI.** Instanced rendering, raycast picking, full
camera/loop control, first-class types. No UI framework: one module owns the single animation frame
and every listener has an explicit `dispose()`. (Rejected: Babylon.js, deck.gl, CesiumJS,
react-three-fiber; see M7.1 report.) Dependencies are exact-pinned: `three` at runtime; `typescript`,
`vite`, `vitest`, `jsdom`, `@types/three`, `playwright-core` for development.

## Scene composition (M7.2)

Orthographic camera in CSS-pixel units (origin bottom-left, +Y up), so the world is a 2.5D
composition and DOM overlays line up exactly. Scene layers, back to front: sky gradient (container
CSS) · decorative stars · real-distance reference arcs · Earth arc (body, grass band, rim, haze,
lakes, tiny trees/houses/people) · fall trails · asteroid rocks · invisible hit discs · focus glow.

| Concern | Rule | Where |
|---|---|---|
| Earth | Large-radius arc; crest at 30% of viewport height, sinking to 17% as you rise; sag 10% of height; geometry rebuilt only on resize | `scene/skyLayout.ts`, `renderer/earthArt.ts` |
| Height (real distance) | Altitude above the surface **directly below** = `MIN + log10-fraction(miss km) × range`, domain 6,371 km (Earth radius) → 1e8 km, fixed constants. Strictly increasing, same range at every x, so **nearer always rests lower** | `scene/skyLayout.ts` |
| Horizontal (illustrative) | Longitude of the served `illustrative_direction`: `atan2(y, x) / π` (projection `longitude-fan-v1`). The served vector is used as-is, never regenerated; latitude (z) is unused | `scene/skyLayout.ts` |
| Appearance | Every asteroid: same low-poly rock, same on-screen size, same colour. Size and colour encode nothing (no PHA/Sentry/danger encoding); facts are text | `renderer/WorldRenderer.ts` |
| Trail | Only while falling; identical for every asteroid; a visual metaphor for approach, not a trajectory | `renderer/WorldRenderer.ts` |
| Reference arcs | Moon distance (384,400 km), 1M, 10M, 100M km, drawn at their true altitudes on the same scale | `scene/skyLayout.ts` |

## Scroll model

One authoritative `explorationProgress` in [0, 1] (`scene/exploration.ts`): the wheel changes only
the target (0.04 per 100 px, ≤ 240 px per event); the single loop eases the current value toward it
(τ = 140 ms) and settles exactly; NaN/Infinity are ignored. While an asteroid is focused, the wheel
does not change exploration.

| Progress | What changes (all continuous functions of progress) |
|---|---|
| 0 | Sky blue, Earth crest at 30%, title + "Scroll to explore", the farthest few asteroids falling in |
| 0 → 0.6 | Asteroids revealed farthest-first, evenly spread over [−0.08, 0.6] (ties by `neows_id`) |
| 0.25 / 0.45 / 0.7 / 1 | Sky colour stops: upper atmosphere · twilight · space · deep space (per-channel interpolation, no thresholds) |
| 0.3 → 0.5 | Reference arcs fade in |
| 0.4 → 0.9 | Stars fade in |
| 0.55 → 0.65 | Names for up to 24 settled asteroids, nearest first, skipping any that would overlap a nearer label |
| 0.78 → 0.86 | Miss distances added under names |

## Asteroid lifecycle

```
HIDDEN ──(deepest progress ≥ its threshold)──▶ FALLING (1.6 s, ease-out) ──▶ SETTLED (stays settled)
```

Reveal follows the **deepest** progress reached, so scrolling back up, focusing, resizing or
re-loading the same population never re-hides or re-drops an asteroid. Simultaneous reveals are
staggered 140 ms apart but within ≤ 1.2 s total. A deep-linked asteroid not yet revealed appears
settled immediately.

## Focus (selection)

Click → URL `#/asteroid/<id>` → profile request → the camera glides onto the asteroid (zoom ×2.2,
rock ×5, field dimmed, warm glow) → callouts with leader lines arranged in two stacked columns:
Identity, Encounter (NASA NeoWs), Orbit (JPL SBDB), Sentry (JPL Sentry, only when the crosswalk
links a record), Physical estimates (NeoWs), Physical (SBDB). Callouts list only real values; a
section with none collapses into one "Not available" line with the contract's reason; a provenance
panel sits at the bottom. Back button, `Esc`, or browser Back return to the world. The URL is the
only path that changes selection, so one click is one selection and refresh restores focus.

## Lifecycle and correctness guarantees

- **One render loop:** `start()` is idempotent; `activeLoops` is 0 or 1 and is asserted in tests.
- **No scroll/zoom loop:** input changes only the target; animation never emits input.
- **No stale profiles:** each load aborts the previous request; a response applies only if its token is current *and* its ID is still selected.
- **Cleanup:** `dispose()` cancels the frame, disconnects the ResizeObserver, removes every listener (canvas, `hashchange`, `keydown`), frees GPU resources, clears the sky and DOM. Vite HMR disposes the old app first.
- **No N+1:** one world request at load; a profile request only on selection; hover uses loaded data.
- **No invented values:** `null` is *Unavailable* (measurements) or *Unknown* (flags); loading is *Loading…*; numbers carry units; formatting never changes the model; every shown asteroid is a real API record.
- **Performance:** one instanced draw per layer; canvas rect cached on resize; hit bounds computed lazily on pick. Measured JS cost per frame while scrolling: ~0.2 ms (35 real objects), ~1.6 ms (1,035 incl. synthetic).

## Interaction torture test

`npm run e2e` starts its own API (port 8765) and Vite server (port 5174), drives the installed
Chrome/Edge (`CHROME_PATH` to override), and fails on any unexpected console error, page error,
NaN/Infinity, duplicated loop/listener, broken distance ordering, restarted fall, or displayed value
that differs from the API. It checks the composition (Earth arc, sky, tiny world), the scroll
journey with screenshots of six defined states, distance ordering for every real object, rapid
up/down scrolling, click-to-focus centring, rapid A→B→C selection, callout values for three objects,
Escape/focus cycles, resizes, refresh, rapid reloads, a 404, an API outage with recovery, and
performance at 35 and 1,035 objects. Output: `e2e/artifacts/` (git-ignored).
