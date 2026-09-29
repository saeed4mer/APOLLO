# NASA Planetary Defense Platform — System Architecture

This document describes the end-to-end technical architecture of the **NASA Planetary Defense Risk Intelligence Platform**, detailing data ingestion, Lakehouse storage, deterministic entity resolution, analytical modeling, the **FastAPI Data Serving Layer (Milestone 6)**, and future consumer integrations.

---

## 1. High-Level Platform Architecture

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             NASA / JPL DATA SOURCES                              │
│         NASA NeoWs        │    JPL CNEOS Sentry Mode S │     NASA / JPL SBDB      │
│   (Near-Earth Objects)   │   (Impact Risk Monitoring) │  (Small-Body Database)   │
└──────────────────────────┴────────────────────────────┴──────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                       INGESTION & RAW FORENSIC ARCHIVAL                          │
│  • Resilient HTTP sessions (exponential backoff & retry)                         │
│  • Ephemeral raw JSON payloads & authoritative source summaries                  │
│  • Automated API key / credential redaction                                      │
└──────────────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                   CENTRALIZED DATA QUALITY GATES (DQ-1 & DQ-2)                   │
│  • Ingestion schema verification & target count threshold validation             │
│  • Null rate checks, lineage completeness, and execution manifest generation    │
└──────────────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                   LAKEHOUSE STORAGE LAYER (PYARROW / PARQUET)                    │
│  • Strict columnar schemas, Snappy compression, partition pruning               │
│  • Encapsulates operational encounters, orbit solutions, and impact risks        │
└──────────────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                     DETERMINISTIC ENTITY RESOLUTION ENGINE                       │
│  • Canonical UUID5 entity keys (`asteroid_key`) derived from NASA namespace      │
│  • Multi-source crosswalk bridge (`bridge_asteroid_identifier`)                   │
│  • Full match state provenance audit (`fact_entity_resolution`)                  │
└──────────────────────────────────────────────────────────────────────────────────┘
                               │
                               ▼
┌──────────────────────────────────────────────────────────────────────────────────┐
│                     DATA PROVIDER ABSTRACTION LAYER                              │
│                         DashboardDataProvider                                    │
│         ├── LocalDuckDBDataProvider (Active Local Offline Lakehouse)             │
│         └── AthenaDataProvider (Future Serverless Cloud Lakehouse)               │
└──────────────────────────────────────────────────────────────────────────────────┘
                               │
                ┌──────────────┴──────────────┐
                ▼                             ▼
┌──────────────────────────────┐┌──────────────────────────────┐
│  FASTAPI DATA SERVING LAYER  ││  INTERACTIVE MISSION DOSSIER │
│         (Milestone 6)        ││         (Milestone 5)        │
│ • Production REST Endpoints  ││ • 5-Tab Streamlit Dashboard  │
│ • Strict Pydantic v2 Schemas ││ • Local in-process provider  │
│ • Uniform Error Envelopes    ││   integration                │
│ • Local Uvicorn ASGI Server  ││                              │
└──────────────────────────────┘└──────────────────────────────┘
                │                             ▲
                │     Milestone 7 Planned     │
                └─────────────────────────────┘
                     (HTTP API Integration)
```

---

## 2. Ingestion & Lakehouse Foundation (Milestones 1–5)

The platform ingests heterogeneous planetary defense catalogs with differing cadences, schemas, and primary grains:
- **NASA NeoWs:** Rolling 7-day tactical approach feed capturing miss distances, approach dates, relative velocities, and estimated diameters. Grain: `(approach_date, neows_id)`.
- **JPL CNEOS Sentry Mode S:** Automated impact risk table tracking potential future Earth collision solutions, impact probabilities, and Palermo/Torino scale ratings. Grain: `(snapshot_key, sentry_id)`.
- **JPL SBDB:** Astrometric Small-Body Database tracking Keplerian orbital elements ($a$, $e$, $i$, $\Omega$, $\omega$, $M$) and physical parameters (albedo, diameter, absolute magnitude). Grain: `(snapshot_key, spkid)`.

### Columnar Lakehouse Storage
All processed datasets are written to Apache Parquet format using explicit PyArrow schemas and Snappy compression. Parquet files are organized by domain:
- `asteroids.parquet`
- `bridge_asteroid_identifier.parquet`
- `fact_entity_resolution.parquet`
- `fact_sbdb_object_snapshot.parquet`
- `fact_sbdb_orbit.parquet`
- `fact_sbdb_orbit_element.parquet`
- `fact_sbdb_physical_parameter.parquet`
- `fact_sentry_risk_snapshot.parquet`

### Deterministic Entity Resolution
Cross-catalog designation differences are resolved through [`entity_resolution.py`](entity_resolution.py):
1. **Canonical Key Generation:** UUID5 identifier (`asteroid_key`) deterministically generated using a platform-fixed namespace UUID (`NAMESPACE_PLANETARY_DEFENSE`) seeded with the primary pivot designation.
2. **Namespace Isolation:** Prevents accidental conflation across disparate identifier spaces (NeoWs ID $\neq$ SBDB SPK-ID $\neq$ Sentry ID).
3. **Resolution States:**
   - `RESOLVED`: Authoritative cross-source identity mapping established.
   - `UNRESOLVED`: Domain object exists in source telemetry, but no valid cross-catalog link exists.
   - `AMBIGUOUS`: Identifier matches multiple conflicting candidate entity keys; candidate evidence is preserved without arbitrary selection.

---

## 3. FastAPI Data Serving Layer (Milestone 6)

### Architectural Role: Serving Boundary
Milestone 6 establishes an authoritative, decoupled data serving layer implemented in [`api/`](api/). The serving layer serves as a secure, standardized access boundary between Lakehouse storage and downstream consumers:

```
NASA / External Sources
         ↓
Existing Ingestion + Processing (nasa_asteroids, nasa_sentry, nasa_sbdb)
         ↓
M5 Intelligence Lakehouse (PyArrow Parquet Layer)
         ↓
DashboardDataProvider (Unified Facade Provider)
         ↓
FastAPI Routes + Pydantic Schemas (api/service.py, api/routes/)
         ↓
HTTP Consumers / Milestone 7 Dashboard
```

> **Design Principle:** The API layer is strictly a serving boundary. It does not replace the underlying ingestion, validation, entity-resolution, historical risk, or data quality pipelines. Routes delegate exclusively through the `DashboardDataProvider` abstraction.

### Separation of Concerns
1. **Route Layer ([`api/routes/`](api/routes/)):**
   - Handles HTTP protocol concerns: URL routing, path and query parameter parsing, and status codes.
   - Enforces strict path parameter syntax (`Annotated[str, Path(pattern=r"^[1-9]\d*$")]`) to reject non-positive or malformed IDs before service execution.
2. **Service Layer ([`api/service.py`](api/service.py)):**
   - Orchestrates business workflows: existence verification, canonical identity resolution delegation, and provider querying.
   - Maps raw provider outputs into validated Pydantic models.
   - Converts missing values and Pandas `NaN` / `NaT` sentinels into genuine JSON `null`.
3. **Provider Layer ([`dashboard_data.py`](dashboard_data.py)):**
   - Storage-agnostic facade (`DashboardDataProvider`) selecting the underlying engine (`LocalDuckDBDataProvider` or `AthenaDataProvider`).
   - Executes optimized local DuckDB SQL queries or remote cloud queries.

### Why Routes Do Not Directly Query Parquet or Raw SQL
- **Decoupling:** Prevents HTTP routes from becoming tightly coupled to physical file paths, schema changes, or storage layouts.
- **Provider Independence:** The same route handler serves requests identically regardless of whether the backend is local DuckDB or cloud Athena.
- **Cache & Query Consistency:** All analytical business rules (such as primary close encounter selection, sorting ties, and resolution mapping) remain centralized in the provider facade.

---

## 4. Execution Modes & Future Athena Alignment

### 1. Active Mode: Local DuckDB / Parquet Lakehouse
- **Execution Mode Label:** `LOCAL (DUCKDB / PARQUET LAKEHOUSE)`
- **Behavior:** Queries local Parquet files via an in-memory DuckDB connection (`duckdb.connect(":memory:")`).
- **Readiness:** Storage verification checks physical existence of the four critical Parquet assets, and query engine verification executes `SELECT 1` against DuckDB.

### 2. Future Mode: Amazon Athena / S3 Lakehouse
- **Execution Mode Label:** `ATHENA (LIVE AWS S3 LAKEHOUSE)`
- **Status:** Architecture and SQL layer prepared; cloud query execution parked for future implementation.
- **Underlying SQL Views:**
  - `v_neo_threat_watchlist` ([`athena_intelligence_layer.sql`](athena_intelligence_layer.sql))
  - `v_entity_resolution_audit` ([`athena_intelligence_layer.sql`](athena_intelligence_layer.sql))
  - `v_sbdb_asteroid_profile` ([`athena_intelligence_layer.sql`](athena_intelligence_layer.sql))
  - `v_sentry_monitoring_profile` ([`athena_intelligence_layer.sql`](athena_intelligence_layer.sql))
  - `v_sentry_historical_risk_metric_deltas` ([`athena_historical_risk.sql`](athena_historical_risk.sql))
- **Contract Equivalence:** The public REST API contract is designed to be completely provider-agnostic. When `AthenaDataProvider` is activated to execute queries via Boto3, **zero changes** will be required to the public API routes, Pydantic schemas, or external response formats.

---

## 5. Downstream Consumer Readiness (Milestone 7)

### Current Streamlit Integration (Milestone 5)
[`dashboard.py`](dashboard.py) currently imports and instantiates `DashboardDataProvider` directly in-process:
```python
from dashboard_data import DashboardDataProvider
provider = DashboardDataProvider()
```

### Planned Milestone 7 API Consumer Architecture
In Milestone 7, the frontend architecture will be updated to consume the FastAPI HTTP endpoints:
- **HTTP Client Adapter:** An asynchronous client (using `httpx`) will query `http://127.0.0.1:8000/asteroids...`.
- **Decoupled Deployment:** Enables running the FastAPI serving layer and Streamlit dashboard in independent processes or containers.
- **Local Fallback:** Preserves direct provider instantiation as an offline fallback when the HTTP service is not running.
