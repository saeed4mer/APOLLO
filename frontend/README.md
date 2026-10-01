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

## Scene composition (M7.2): the distance world

The distance field is a **long virtual world**, not a chart fitted to the screen:

```
real miss_distance_km -> world height above the Earth (altitudePx) -> camera travel (travelPx) -> local viewport window
```

Orthographic camera in CSS-pixel units (+Y up), so DOM overlays line up exactly. The Earth sits at
the bottom of the world; every distance has a fixed world height above it; the camera translates up
through the world as the user explores, so the screen only ever shows the local region around the
distance being explored (about 5M km behind the frontier and 2M km ahead). Scene layers, back to
front: sky gradient (container CSS) · stars (travel with the camera: infinitely far) · Moon landmark +
arc · million-km distance guides · frontier arc · Earth (body, grass band, rim, haze, lakes, tiny
trees/houses/people) · fall trails · asteroid rocks · PHA badges · focus glow.

| Concern | Rule | Where |
|---|---|---|
| Distance world | `altitudePx(km)`: in the sky, `moonAlt × ln(1+km/6,371) / ln(1+384,400/6,371)` (Moon at 42% of a screen above the crest); Moon → 1M linear over 1.6 million-km steps; **above 1M km LINEAR: one million km = 14% of the viewport height (≈120 px, clamped 80–220)**. So each million has real separation, and 47,382,615 km sits exactly 38.2615% of the way from the 47M to the 48M guide. Strictly increasing (nearer always lower), exact values only, same for asteroids, guides and the Moon. Domain: 6,371 km → max(1e8 km, next 10M boundary above 1.05 × the farthest real miss distance) | `scene/skyLayout.ts` |
| Camera travel | `travelPx(F) = max(0, crestY + altitudePx(F) − 0.68 × viewportHeight)`: the camera stays on the Earth until the frontier would rise above 68% of the screen, then follows it, holding the frontier there. A pure function of the revealed distance, so it is deterministic and fully reversible | `scene/skyLayout.ts`, `renderer/WorldRenderer.ts` |
| Earth | World geometry at the base of the world (crest 30% up the first screen; sag 10% of height; rebuilt only on resize). It **recedes** as the camera travels (still in view at the Moon), **leaves the viewport by ~2.5M km**, and returns exactly when scrolling back. The sky's horizon band leaves with it | `scene/skyLayout.ts`, `renderer/earthArt.ts` |
| Horizontal (illustrative) | Longitude of the served `illustrative_direction`: `atan2(y, x) / π` (projection `longitude-fan-v1`). The served vector is used as-is, never regenerated; latitude (z) is unused | `scene/skyLayout.ts` |
| Appearance | Every asteroid: same low-poly rock, same on-screen size, same colour. Size and colour encode nothing; facts are text | `renderer/WorldRenderer.ts` |
| Hazard badge | A small ⚠ beside the rock **only** when NeoWs `is_potentially_hazardous === true` (not for `false`, not for `null`). It is the NeoWs PHA flag, not an impact prediction, Sentry result or risk score; nothing else encodes hazard | `renderer/WorldRenderer.ts` |
| Trail | Only while falling; identical for every asteroid; a visual metaphor for approach, not a trajectory | `renderer/WorldRenderer.ts` |
| Moon landmark | A visual Moon on a dashed arc at the world height of 384,400 km, labelled "MOON DISTANCE / 384,400 km" (to its left). **Hidden until the frontier reaches 384,400 km** (progress 0.16, the night transition), fading in over 0.015 progress; it is then passed and leaves the viewport (~6M km), and returns when scrolling back. Context, not data: not a record, no direction semantics, never uses `illustrative_direction` | `scene/atmosphere.ts`, `renderer/WorldRenderer.ts` |
| Distance guides | A dashed arc **every 1,000,000 km** (1M … domain max), parallel to the Earth arc, at the same world heights as the asteroids — visual distance guides, not orbits or trajectories. **Only the guides inside the viewport window are processed and drawn** (~7; culled, one draw call, per-vertex alpha, fixed-capacity buffer). Hierarchy: every 10M major, every 5M mid; the guide being explored is the strongest; unreached guides ahead are faint and gone within ~3M km; guides fade toward the bottom edge (where the user came from). Every drawn guide carries its label ("17M km", "18M km" …), ≥ 15 px apart | `scene/atmosphere.ts`, `renderer/WorldRenderer.ts`, `ui/LabelLayer.ts` |
| Frontier | A dashed arc at the revealed distance and "REVEALED TO n km" (floored to whole millions; presentation only) | `ui/LabelLayer.ts` |

## Scroll model

One authoritative `explorationProgress` in [0, 1] (`scene/exploration.ts`): the wheel changes only
the target (1/160 per 100 px — the **full journey is 160 wheel notches** — ≤ 240 px per event); the
single loop eases the current value toward it (τ = 140 ms) and settles exactly; NaN/Infinity are
ignored. While an asteroid is focused, the wheel does not change exploration.

Progress maps to a **revealed distance** in three documented stages (`revealedDistanceKm`, with the
exact inverse `progressForDistance`), and the camera travel follows from it:

```
revealedKm(p) = 0                                                 p = 0
              = 6,371 × (384,400 / 6,371)^(p / 0.16)              0 < p ≤ 0.16    leaving the atmosphere (sky -> night)
              = 384,400 × (1,000,000 / 384,400)^((p−0.16)/0.06)   0.16 < p ≤ 0.22 past the Moon
              = 1M + (maxKm − 1M) × (p − 0.22) / 0.78             0.22 < p ≤ 1    the million-km field at constant speed
                                                                                  (~125 notches, ~0.8M km each for 100M)
eligible(a)   = a.miss_distance_km <= revealedKm                  (exact source value)
travel        = travelPx(revealedKm)                              (camera position; no "deepest ever" state)
```

| Progress | Stage (all continuous functions of progress) |
|---|---|
| 0 | Bright day sky, Earth, title + "Scroll to explore"; revealed 0 km: **no Moon, no guides, no asteroid, no stars** |
| 0 → 0.115 | Sky deepens to twilight (stops 0.05, 0.115); frontier label from 0.015; stars start at 0.09 |
| 0.16 | Frontier reaches 384,400 km: **the Moon appears**; night stop at 0.17 |
| 0.16 → 0.22 | The first guides fade in; the camera starts travelling; 1M km at 0.22 |
| 0.22 → 1 | Field travel: the Earth leaves (~2.5M km), then the Moon (~6M km); asteroids appear **closest first** at their exact distances and are passed as the user travels on; space stop 0.32, deep space 1 |

## Asteroid lifecycle (reversible)

```
eligible:      HIDDEN ──▶ FALLING (1.6 s, ease-out) ──▶ SETTLED
not eligible:  SETTLED ──▶ RETREATING (0.7 s, same path back up) ──▶ HIDDEN
```

Each asteroid has one animation value in [0, 1] advanced by frame time inside the single render
loop (no timers, no per-asteroid loops, no copies); a reversal mid-animation continues from the
current value. Scrolling back retreats everything beyond the new frontier (farthest first);
scrolling forward re-reveals the same asteroids at the same positions. World positions are static:
as the camera travels on, settled asteroids, guides, the Moon and the Earth leave the bottom of the
viewport (they are passed) and return when scrolling back. A newly reached asteroid always falls in
from just above the current viewport. Resizing or re-supplying the
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
384,400 km with the Earth still in view, the first guides, local windows of ~7 well-spaced guides at
~9M/20M/50M/100M with no Earth, frontier emphasis), a real asteroid passed and returning to the same
place, TOP -> 10M -> 30M -> 60M -> 100M -> 60M -> 30M -> 10M -> TOP twice (Earth back in place, no Moon,
all hidden, no duplicates), asteroid labels matching the API's exact distances, backward retreat and deterministic re-reveal, an exact threshold
on a real non-round distance, distance ordering for every real object, focus return preserving the
revealed distance, rapid up/down scrolling, click-to-focus centring, rapid A→B→C selection, callout values for three objects,
Escape/focus cycles, resizes, refresh, rapid reloads, a 404, an API outage with recovery, and
performance at 35 and 1,035 objects. Output: `e2e/artifacts/` (git-ignored).
