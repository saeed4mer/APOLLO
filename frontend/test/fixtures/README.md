# Contract fixtures

Real responses captured from the FastAPI serving layer (local lakehouse, 35 NeoWs objects),
using `fastapi.testclient` against `api.main:create_app()`. They are NASA/JPL public data and
contain no credentials.

| File | Request |
|---|---|
| `world.json` | `GET /asteroids/world` |
| `profile_3548666.json` | `GET /asteroids/3548666/profile` (2010 TW54: resolved, SBDB + Sentry linked) |
| `profile_3830890.json` | `GET /asteroids/3830890/profile` (2018 SP2: unresolved) |
| `profile_404.json` | `GET /asteroids/99999999/profile` (HTTP 404, `TARGET_NOT_FOUND`) |

Regenerate them whenever the serving contract changes, so the renderer is always tested
against what the API actually returns.
