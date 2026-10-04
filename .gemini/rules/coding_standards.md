# Coding Standards & Quality Guidelines

## Backend (Python)
- **Code Style:** PEP 8 compliance enforced by Ruff (`ruff check backend/ tests/`). Max line length: 100 characters.
- **Typing:** Strict type annotations (`from __future__ import annotations`).
- **Data Engineering:** PyArrow Parquet files must declare explicit schemas and Snappy compression.
- **Serving Layer:** Pydantic v2 models with `extra="forbid"` to strictly enforce locked API contracts.
- **Testing:** Pytest suites under `tests/` structured into `unit/`, `integration/`, and `api/`.

## Frontend (TypeScript / Vite)
- **Language:** TypeScript with strict mode.
- **Graphics:** Three.js rendering within `frontend/src/renderer/`.
- **Contracts:** HTTP requests consumed via `frontend/src/api/client.ts` with runtime schema validation guards (`validateWorld.ts`, `validateProfile.ts`).
- **Testing:** Vitest unit test suite with 100% test coverage expectation for core spatial and state routines.
