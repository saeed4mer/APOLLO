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
CSS) · decorative stars · Moon landmark + arc · million-km distance guides · revealed-distance frontier arc · Earth arc (body, grass band, rim, haze,
lakes, tiny trees/houses/people) · fall trails · asteroid rocks · PHA badges · focus glow.

| Concern | Rule | Where |
|---|---|---|
| Earth | Large-radius arc; crest at 30% of viewport height, sinking to 17% as you rise; sag 10% of height; geometry rebuilt only on resize | `scene/skyLayout.ts`, `renderer/earthArt.ts` |
| Height (real distance) | Focus + context around the revealed frontier F (see below): the frontier always sits at 86% of the sky height, the distance being explored is spread out, nearer distances compress toward Earth. Uses the exact value (never rounded). **Strictly increasing in km for every F**, same range at every x, so nearer always rests lower. Domain derived from the data: 6,371 km → max(1e8 km, next 10M-km boundary above 1.05 × the farthest real miss distance), so no real record is ever clipped | `scene/skyLayout.ts` |
| Horizontal (illustrative) | Longitude of the served `illustrative_direction`: `atan2(y, x) / π` (projection `longitude-fan-v1`). The served vector is used as-is, never regenerated; latitude (z) is unused | `scene/skyLayout.ts` |
| Appearance | Every asteroid: same low-poly rock, same on-screen size, same colour. Size and colour encode nothing; facts are text | `renderer/WorldRenderer.ts` |
| Hazard badge | A small ⚠ beside the rock **only** when NeoWs `is_potentially_hazardous === true` (not for `false`, not for `null`). It is the NeoWs PHA flag, not an impact prediction, Sentry result or risk score; nothing else encodes hazard | `renderer/WorldRenderer.ts` |
| Trail | Only while falling; identical for every asteroid; a visual metaphor for approach, not a trajectory | `renderer/WorldRenderer.ts` |
| Moon landmark | A visual Moon on a dashed arc at the height of 384,400 km under the same mapping, labelled "MOON DISTANCE / 384,400 km". **Hidden until the frontier reaches 384,400 km** (progress 0.26, the night transition), then fades in over 0.025 progress. Context, not data: not a record, no direction semantics, never uses `illustrative_direction` | `scene/atmosphere.ts`, `renderer/WorldRenderer.ts` |
| Distance guides | A dashed arc **every 1,000,000 km** (1M … domain max), parallel to the Earth arc, at the height the asteroid mapping gives that distance — visual distance guides, not orbits or trajectories. Hierarchy: every 10M major, every 5M mid, others minor; the guide being explored is the strongest (always within 1M of the frontier), unreached guides are hidden from 2M ahead, dense guides fade by on-screen spacing (minors first, majors never). Labels are progressive: the early field (< 10M) labels each million; later the 3M behind the frontier plus the 5M/10M markers, ≥ 15 px apart. One draw call; per-vertex alpha; fixed-capacity buffer | `scene/atmosphere.ts`, `renderer/WorldRenderer.ts`, `ui/LabelLayer.ts` |
| Frontier | A dashed arc at the revealed distance and "REVEALED TO n km" (floored to whole millions; presentation only) | `ui/LabelLayer.ts` |

## Scroll model

One authoritative `explorationProgress` in [0, 1] (`scene/exploration.ts`): the wheel changes only
the target (0.0125 per 100 px — the full journey is ~80 wheel notches — ≤ 240 px per event); the
single loop eases the current value toward it (τ = 140 ms) and settles exactly; NaN/Infinity are
ignored. While an asteroid is focused, the wheel does not change exploration.

Progress maps to a **revealed distance** in three documented stages (`revealedDistanceKm`, with the
exact inverse `progressForDistance`):

```
revealedKm(p) = 0                                              p = 0
              = 6,371 × (384,400 / 6,371)^(p / 0.26)           0 < p ≤ 0.26   leaving the atmosphere
              = 384,400 × (1,000,000 / 384,400)^((p−0.26)/0.06)  0.26 < p ≤ 0.32   past the Moon
              = 1M + (maxKm − 1M) × t^1.6,  t = (p−0.32)/0.68   0.32 < p ≤ 1      the million-km field (~0.3–2.9M km per notch)
eligible(a)   = a.miss_distance_km <= revealedKm               (exact source value)
```

Height for a distance `km` with frontier `F = max(revealedKm, 10,000 km)`:

```
km ≤ F:  fraction = 0.86 × ( 0.3 × ctx(km)/ctx(F) + 0.7 × (km/F)^1.6 )     ctx = log(km/6,371) / log(maxKm/6,371)
km > F:  fraction = 0.86 + 0.14 × (1 − (F/km)²)                            (unrevealed, above the frontier)
altitude = 28 px + fraction × (range − 28 px)
```

| Progress | Stage (all continuous functions of progress) |
|---|---|
| 0 | Bright day sky, Earth crest at 30%, title + "Scroll to explore"; revealed 0 km: **no Moon, no guides, no asteroid** |
| 0 → 0.21 | Sky deepens to twilight (stops 0.12, 0.21); frontier label from 0.03; stars start at 0.17 |
| 0.26 | Night stop; frontier reaches 384,400 km: **the Moon appears** |
| 0.27 → 0.32 | The million-km field fades in; 1M km at 0.32 |
| 0.32 → 1 | Field travel: guides and asteroids appear **closest first** at their exact distances; space stop 0.5, deep space 1 |
| 0.33 → 0.39 | Asteroid names, then miss distances (exact values), for up to 24 settled asteroids |

## Asteroid lifecycle (reversible)

```
eligible:      HIDDEN ──▶ FALLING (1.6 s, ease-out) ──▶ SETTLED
not eligible:  SETTLED ──▶ RETREATING (0.7 s, same path back up) ──▶ HIDDEN
```

Each asteroid has one animation value in [0, 1] advanced by frame time inside the single render
loop (no timers, no per-asteroid loops, no copies); a reversal mid-animation continues from the
current value. Scrolling back retreats everything beyond the new frontier (farthest first);
scrolling forward re-reveals the same asteroids at the same positions. Because height is focused
on the frontier, settled asteroids, guides and the Moon slide toward Earth as the user travels outward
(they are passed), and are stationary whenever the exploration is at rest. Resizing or re-supplying the
same population restarts nothing. While focused, the selected asteroid is pinned visible (a
deep-linked asteroid beyond the frontier appears for its focus) and the wheel does not move the
exploration; on return the unchanged revealed distance applies again, so nothing re-falls.

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
- **Performance:** one instanced draw per layer; canvas rect cached on resize; picking is an O(n) screen-space disc test (no raycast).

## Interaction torture test

`npm run e2e` starts its own API (port 8765) and Vite server (port 5174), drives the installed
Chrome/Edge (`CHROME_PATH` to override), and fails on any unexpected console error, page error,
NaN/Infinity, duplicated loop/listener/object, broken distance ordering, an asteroid visible beyond
the revealed distance, a fall at load or on return from focus, or a displayed value that differs
from the API. It checks the composition (Earth arc, sky, tiny world), nothing falling before scroll,
distance-eligible reveal at several frontiers, the 1M frontier label progression, the Moon landmark,
the PHA badge on real PHA objects, the staged journey (bright sky, darker sky with no Moon, the Moon at
384,400 km, the first guides, every 1M guide in the early field, frontier emphasis, majors), asteroid labels
matching the API's exact distances, backward retreat and deterministic re-reveal, an exact threshold
on a real non-round distance, distance ordering for every real object, focus return preserving the
revealed distance, rapid up/down scrolling, click-to-focus centring, rapid A→B→C selection, callout values for three objects,
Escape/focus cycles, resizes, refresh, rapid reloads, a 404, an API outage with recovery, and
performance at 35 and 1,035 objects. Output: `e2e/artifacts/` (git-ignored).
