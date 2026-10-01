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
CSS) · decorative stars · Moon landmark + arc · revealed-distance frontier arc · 1M-km ruler · Earth arc (body, grass band, rim, haze,
lakes, tiny trees/houses/people) · fall trails · asteroid rocks · PHA badges · focus glow.

| Concern | Rule | Where |
|---|---|---|
| Earth | Large-radius arc; crest at 30% of viewport height, sinking to 17% as you rise; sag 10% of height; geometry rebuilt only on resize | `scene/skyLayout.ts`, `renderer/earthArt.ts` |
| Height (real distance) | Altitude above the surface **directly below** = `MIN + log10-fraction(miss km) × range`, over a domain derived from the data: 6,371 km (Earth radius) → max(1e8 km, next 10M-km boundary above 1.05 × the farthest real miss distance), so no real record is ever clipped. Uses the exact value (never rounded). Strictly increasing, same range at every x, so **nearer always rests lower** | `scene/skyLayout.ts` |
| Horizontal (illustrative) | Longitude of the served `illustrative_direction`: `atan2(y, x) / π` (projection `longitude-fan-v1`). The served vector is used as-is, never regenerated; latitude (z) is unused | `scene/skyLayout.ts` |
| Appearance | Every asteroid: same low-poly rock, same on-screen size, same colour. Size and colour encode nothing; facts are text | `renderer/WorldRenderer.ts` |
| Hazard badge | A small ⚠ beside the rock **only** when NeoWs `is_potentially_hazardous === true` (not for `false`, not for `null`). It is the NeoWs PHA flag, not an impact prediction, Sentry result or risk score; nothing else encodes hazard | `renderer/WorldRenderer.ts` |
| Trail | Only while falling; identical for every asteroid; a visual metaphor for approach, not a trajectory | `renderer/WorldRenderer.ts` |
| Moon landmark | A visual Moon on a dashed arc at the altitude of 384,400 km, labelled "MOON DISTANCE / 384,400 km". Context, not data: not a record, no direction semantics, never uses `illustrative_direction` | `renderer/WorldRenderer.ts`, `ui/LabelLayer.ts` |
| Distance scale | A ruler at the right edge with a tick every 1,000,000 km up to the revealed frontier (longer every 10M), labels thinned to stay ≥ 15 px apart; a frontier arc and "REVEALED TO n km" label (floored to whole millions). Labels are presentation only — positions always use exact distances | `scene/skyLayout.ts`, `ui/LabelLayer.ts` |

## Scroll model

One authoritative `explorationProgress` in [0, 1] (`scene/exploration.ts`): the wheel changes only
the target (0.03 per 100 px, ≤ 240 px per event); the single loop eases the current value toward it
(τ = 140 ms) and settles exactly; NaN/Infinity are ignored. While an asteroid is focused, the wheel
does not change exploration.

Progress maps to a **revealed distance** (`revealedDistanceKm`, the exact inverse of the altitude
scale, so the frontier arc always sits where an asteroid at that distance rests):

```
revealedKm(p) = 0                                   if p = 0
              = minKm × (maxKm / minKm)^p           otherwise      (deterministic, monotonic, finite, ≤ maxKm)
eligible(a)   = a.miss_distance_km <= revealedKm    (exact source value)
```

| Progress | What changes (all continuous functions of progress) |
|---|---|
| 0 | Sky blue, Earth crest at 30%, title + "Scroll to explore", the Moon landmark; revealed distance 0 km, **no asteroid visible or falling** |
| 0 → 1 | Asteroids appear **closest first** as the frontier reaches each exact miss distance (ties by `neows_id`) |
| 0.25 / 0.45 / 0.7 / 1 | Sky colour stops: upper atmosphere · twilight · space · deep space (per-channel interpolation, no thresholds) |
| 0.42 → 0.5 | 1M-km ruler fades in (frontier ≈ 1M km) |
| 0.4 → 0.9 | Stars fade in |
| 0.45 → 0.55 | Names for up to 24 settled asteroids, nearest first, skipping any that would overlap a nearer label or the scale |
| 0.62 → 0.7 | Miss distances added under names |

## Asteroid lifecycle (reversible)

```
eligible:      HIDDEN ──▶ FALLING (1.6 s, ease-out) ──▶ SETTLED
not eligible:  SETTLED ──▶ RETREATING (0.7 s, same path back up) ──▶ HIDDEN
```

Each asteroid has one animation value in [0, 1] advanced by frame time inside the single render
loop (no timers, no per-asteroid loops, no copies); a reversal mid-animation continues from the
current value. Scrolling back retreats everything beyond the new frontier (farthest first);
scrolling forward re-reveals the same asteroids at the same positions. Resizing or re-supplying the
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
the PHA badge on real PHA objects, backward retreat and deterministic re-reveal, an exact threshold
on a real non-round distance, distance ordering for every real object, focus return preserving the
revealed distance, rapid up/down scrolling, click-to-focus centring, rapid A→B→C selection, callout values for three objects,
Escape/focus cycles, resizes, refresh, rapid reloads, a 404, an API outage with recovery, and
performance at 35 and 1,035 objects. Output: `e2e/artifacts/` (git-ignored).
