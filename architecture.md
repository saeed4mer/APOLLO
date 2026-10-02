# APOLLO — System Architecture

**APOLLO** (*Asteroid Proximity & Orbital Location, Linkage & Observation*) is an immersive near-Earth
asteroid intelligence platform. This document describes how it is built: from the NASA/JPL sources,
through ingestion, storage and entity resolution, to the FastAPI serving layer and the APOLLO renderer.

---

## 1. End-to-End Flow

```
NASA / JPL sources
  ├── NASA NeoWs            close approaches (encounter data)
  ├── JPL SBDB              orbital elements and physical parameters
  └── JPL CNEOS Sentry      impact-monitoring assessments (Mode S)
        │
        ▼
Ingestion / validation / normalization
  nasa_asteroids.py · nasa_sbdb.py · nasa_sentry.py · pipeline_utils.py
  data-quality gates: pipeline_dq.py (check-ingestion, check-outputs)
        │
        ▼
Storage: snapshots as Parquet (PyArrow schemas, Snappy)
  local lakehouse files · Amazon S3 partitions · Athena tables and views (athena_*.sql)
        │
        ▼
Entity resolution / enrichment
  entity_resolution.py → bridge_asteroid_identifier · fact_entity_resolution
  data-quality gate: pipeline_dq.py check-crosswalk
        │
        ▼
Data-access provider: dashboard_data.py
  DashboardDataProvider → LocalDuckDBDataProvider (active) · AthenaDataProvider (not implemented)
        │
        ▼
FastAPI serving layer: api/
  Pydantic v2 contracts · uniform error envelope · readiness probe
        │   HTTP: GET /asteroids/world (once) · GET /asteroids/{neows_id}/profile (per selection)
        ▼
APOLLO frontend: frontend/ (TypeScript, Vite)
        │
        ▼
3D immersive renderer (Three.js): the real asteroid population in a scrollable distance world
```

Production runs of the ingestion, quality gates, resolution and S3 publication are orchestrated by
[`.github/workflows/scheduled_pipeline.yml`](.github/workflows/scheduled_pipeline.yml).

---

## 2. Three Distinct Sources

The three sources answer different questions. They are stored, served and displayed separately and are
never merged into one value.

| Source | What it describes | Grain | Examples |
|---|---|---|---|
| **NASA NeoWs** | **Encounter data**: one close approach of an object to Earth in the ingested window (by default the 7-day feed) | `(approach_date, neows_id)` | miss distance, relative velocity, estimated diameter range, approach time, the NeoWs PHA flag |
| **JPL SBDB** | **Orbital and physical data**: the object's orbit solution and physical parameters | `(snapshot_key, spkid)` | a, e, i, Ω, ω, M, perihelion/aphelion, period, H, diameter, albedo |
| **JPL Sentry (Mode S)** | **Impact-monitoring assessment**: objects the Sentry system currently lists, with its published summary metrics | `(snapshot_key, sentry_id)` | cumulative impact probability, number of potential impacts, Palermo and Torino scales, v∞ |

Interpretation rules that hold everywhere (pipeline, API and renderer):

- **PHA is not Sentry linkage.** The NeoWs "potentially hazardous asteroid" flag is an orbital/size
  classification. Whether an object is linked to a Sentry record comes **only** from the identity
  crosswalk. The NeoWs PHA flag and the NeoWs `is_sentry_object` flag never change the served Sentry status.
- **No Sentry record does not mean "safe".** A missing link can mean the identity is not resolved,
  the object is not in the stored Sentry catalog, or the linkage is ambiguous. The API states which
  (`not_resolved`, `not_present`, `ambiguous`, `linked_no_record`, or `available`), and nothing is inferred
  from absence.
- **No synthetic risk score.** There is no combined danger, threat or risk score anywhere. Sentry values
  are copied from the published Mode S summary; Mode O detail (individual impact solutions) is not ingested.
- **Unknown is not false.** Missing values stay `null`, with the reason (`not_resolved`, `not_in_source`,
  `not_in_current_contract`, `ambiguous_linkage`).

---

## 3. Ingestion, Validation & Storage

- **Ingestion:** `nasa_asteroids.py` (NeoWs; `--start-date`/`--end-date`, or `--from-raw` to rebuild from a
  stored raw payload offline), `nasa_sbdb.py` (SBDB; `--target`, `--targets`, `--targets-file`),
  `nasa_sentry.py` (Sentry Mode S; optional `--snapshot-date`). Shared HTTP, S3, PyArrow and redaction
  helpers live in `pipeline_utils.py`. Raw JSON payloads are kept for lineage and replay.
- **Quality gates:** `pipeline_dq.py` halts the pipeline (`exit 1`) on a violation: `check-ingestion`
  (summaries, thresholds, lineage), `check-outputs` (artifact accounting, grains, dates), `check-crosswalk`
  (crosswalk invariants), plus S3 publication verification; `run-suite` runs them together.
- **Storage:** every processed dataset is a Parquet snapshot with an explicit PyArrow schema:
  `asteroids.parquet`, `fact_sbdb_object_snapshot`, `fact_sbdb_orbit`, `fact_sbdb_orbit_element`,
  `fact_sbdb_physical_parameter`, `fact_sentry_risk_snapshot`, `bridge_asteroid_identifier`,
  `fact_entity_resolution`. In production they are published to date-partitioned S3 keys and queried
  through Athena (`athena_schema.sql`, `athena_intelligence_layer.sql`, `athena_historical_risk.sql`,
  `athena_queries.sql`). NeoWs ingestion also writes a five-column CSV export and a local SQLite
  database (`database.py`, `schema.sql`) for inspection; the API reads neither.
- **Sentry history:** each Sentry ingestion is an immutable point-in-time snapshot. Historical backfills
  skip Sentry entirely; snapshots are never backdated or synthesized.

### Athena views

| View | Grain | Defined in |
|---|---|---|
| `v_sbdb_characterization_profile` | `(spkid)` | `athena_intelligence_layer.sql` |
| `v_neows_sentry_threat_watchlist` | `(closest_approach_date, neows_id)` | `athena_intelligence_layer.sql` |
| `v_asteroid_cross_source_profile` | `(asteroid_key)` | `athena_intelligence_layer.sql` |
| `v_crosswalk_coverage_audit` | `(source_system, match_state, match_rule)` | `athena_intelligence_layer.sql` |
| `v_sentry_snapshot_coverage` | `(snapshot_key)` | `athena_historical_risk.sql` |
| `v_sentry_risk_metric_history` | `(snapshot_key, sentry_id)` | `athena_historical_risk.sql` |
| `v_sentry_presence_history` | `(snapshot_key, sentry_id)` | `athena_historical_risk.sql` |
| `v_sentry_object_lifecycle` | `(sentry_id)` | `athena_historical_risk.sql` |

---

## 4. Entity Resolution & Enrichment

[`entity_resolution.py`](entity_resolution.py) links the three identifier spaces deterministically, with
no fuzzy matching:

1. **Canonical key:** `asteroid_key = ast_<UUID5>`, derived from a fixed platform namespace and the
   object's SBDB SPK-ID, the primary pivot.
2. **Rules:** exact SPK-ID or exact normalized-designation matches only.
3. **Namespace isolation:** a NeoWs ID, an SBDB SPK-ID and a Sentry ID are never interchangeable.
4. **States:** `RESOLVED` (linked), `UNRESOLVED` (present in its source, no authoritative link),
   `AMBIGUOUS` (several candidates; the evidence is kept and no candidate is chosen).
5. **Outputs:** `bridge_asteroid_identifier` (source identifiers → `asteroid_key`) and
   `fact_entity_resolution` (the audit of rules, states and run metadata), both validated by
   `pipeline_dq.py check-crosswalk`.

This crosswalk is what attaches SBDB orbits and Sentry assessments to a NeoWs encounter. It is the
only source of an object's Sentry linkage.

---

## 5. Data-Access Provider (`dashboard_data.py`)

`DashboardDataProvider` is the storage-agnostic facade the API delegates every query to. The module name
is historical: it originally also served a Streamlit dashboard, since retired.

- **`LocalDuckDBDataProvider`** (active): queries the local Parquet lakehouse through an in-memory DuckDB
  connection. It owns the business rules (primary encounter selection, ordering, resolution mapping,
  coherent SBDB and Sentry snapshot selection).
- **`AthenaDataProvider`**: prepared for the S3/Athena lakehouse but **not implemented**; every query
  raises `NotImplementedError`.

---

## 6. FastAPI Serving Layer (`api/`)

A read-only HTTP boundary between storage and consumers:

- **Routes (`api/routes/`)** parse paths and queries (NeoWs IDs must match `^[1-9]\d*$`) and map status codes.
- **Service (`api/service.py`)** orchestrates provider calls and maps results into Pydantic v2 models
  (`api/schemas.py`, `extra="forbid"`), turning missing values into JSON `null`.
- **Endpoints:** `GET /health`; the renderer contracts `GET /asteroids/world` and
  `GET /asteroids/{neows_id}/profile`; and the per-source routes `GET /asteroids`, `/asteroids/{id}`,
  `/asteroids/{id}/sbdb`, `/asteroids/{id}/sentry`, `/asteroids/{id}/history`, `/asteroids/{id}/crosswalk`.
- **`GET /asteroids/world`** returns every NeoWs object in one set-based query, with its encounter, its
  resolution, its SBDB and Sentry availability, and an `illustrative_direction` unit vector
  (`sha256-uniform-sphere-v1`, seeded only by `neows_id`). The snapshot declares the spatial model: the
  **distance is real and the direction is illustrative**.
- **`GET /asteroids/{neows_id}/profile`** returns one object by source section: identity, SBDB orbit,
  SBDB physical, NeoWs physical, NeoWs encounter, Sentry and provenance, each with its availability.
- **Readiness:** `/health` checks the four required Parquet assets and DuckDB. On a fresh checkout
  with none of those assets, the module-level app serves an empty, schema-valid placeholder lakehouse
  from a temporary directory outside the repository (`api.main.resolve_default_lakehouse`). A partly
  present lakehouse is reported as not ready (503).
- **Boundaries:** routes never read Parquet, run ad-hoc SQL or call NASA/JPL/AWS. There is no
  authentication, rate limiting or caching (local-first).

---

## 7. APOLLO Frontend & Renderer (`frontend/`)

The APOLLO frontend is a TypeScript application built with Vite that renders with Three.js. It is a
pure HTTP consumer of the serving layer: one `GET /asteroids/world` at load and one
`GET /asteroids/{neows_id}/profile` per selection. In development, `/api` is proxied by the Vite dev
server to the local Uvicorn server. It never reads storage or re-derives backend logic; resolution,
Sentry linkage and directions are used exactly as served.

The renderer presents the real NeoWs population in a scrollable "distance world". Each object's height
follows its exact miss distance, nearer objects always rest lower, and asteroids are revealed closest
first as the journey reaches their distance. Objects with an actual Sentry link (served
`sentry.status`) are marked gold; the NeoWs PHA flag is shown as a separate small badge. Selecting an
asteroid opens source-labelled callouts from the profile endpoint.

Details of the spatial model, the lifecycle guarantees and the tests are in
[`frontend/README.md`](frontend/README.md).
