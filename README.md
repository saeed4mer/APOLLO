# NASA Planetary Defense Risk Intelligence Platform

An end-to-end planetary defense data engineering platform that ingests, validates, characterizes, resolves, and tracks Near-Earth Objects (NEOs) across multiple distinct NASA/JPL astronomical data sources. The platform unifies operational close approaches, long-term impact monitoring, and Keplerian orbital characterizations into an analytics-ready Amazon S3 lakehouse, Amazon Athena serverless SQL intelligence views, and an interactive Streamlit intelligence dossier.

![Tests](https://img.shields.io/badge/tests-286%20passed-brightgreen)
![Python](https://img.shields.io/badge/python-3.11-blue)
![CI](https://img.shields.io/badge/CI-GitHub%20Actions-informational)
![Code Style](https://img.shields.io/badge/code%20style-ruff-000000.svg)

---

## Table of Contents

- [Platform Purpose](#platform-purpose)
- [Source Architecture](#source-architecture)
- [End-to-End Architecture](#end-to-end-architecture)
- [Core Architectural Principles](#core-architectural-principles)
- [Analytical Intelligence Layer](#analytical-intelligence-layer)
- [Interactive Streamlit Dossier](#interactive-streamlit-dossier)
- [Production Orchestration](#production-orchestration)
- [Historical Backfill Semantics](#historical-backfill-semantics)
- [Data Quality & Reliability Gates](#data-quality--reliability-gates)
- [Continuous Integration](#continuous-integration)
- [Security & Secrets Management](#security--secrets-management)
- [Repository Structure](#repository-structure)
- [Tech Stack](#tech-stack)
- [Running the Platform Locally](#running-the-platform-locally)
- [Project Status & Roadmap](#project-status--roadmap)

---

## Platform Purpose

Planetary defense against asteroid impacts relies on disparate observational programs and catalogs maintained across NASA and the Jet Propulsion Laboratory (JPL). Each source serves a distinct operational purpose with its own identifier taxonomy, cadence, and data structures:

1. **Short-Term Operations:** Tactical approach feeds tracking imminent close approaches to Earth.
2. **Impact Risk Monitoring:** Computational impact solution catalogs tracking collision probabilities across future encounter epochs.
3. **Physical & Astrometric Characterization:** Astrometric catalogs tracking Keplerian orbital elements and physical properties (albedo, diameter, absolute magnitude).

The **NASA Planetary Defense Risk Intelligence Platform** solves this fragmentation by building an automated, reliable data lakehouse and deterministic entity resolution engine. The platform cross-references heterogeneous designations, enforces scientific data integrity, and delivers actionable multi-source risk intelligence without synthetic danger scores or causal overreach.

---

## Source Architecture

The platform ingests from three primary NASA/JPL planetary defense sources:

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                             NASA / JPL DATA SOURCES                              │
├──────────────────────────┬────────────────────────────┬──────────────────────────┤
│        NASA NeoWs        │    JPL CNEOS Sentry Mode S │     NASA / JPL SBDB      │
│   (Near-Earth Objects)   │   (Impact Risk Monitoring) │  (Small-Body Database)   │
├──────────────────────────┼────────────────────────────┼──────────────────────────┤
│ • Ingestion:             │ • Ingestion:               │ • Ingestion:             │
│   nasa_asteroids.py      │   nasa_sentry.py           │   nasa_sbdb.py           │
│ • Domain:                │ • Domain:                  │ • Domain:                │
│   Operational encounters │   Potential Earth impacts  │   Keplerian orbit &      │
│ • Primary Metrics:       │ • Primary Metrics:         │   physical parameters    │
│   - Miss distance (km/LD)│   - Cumulative/max impact  │ • Primary Metrics:       │
│   - Relative velocity    │     probability            │   - Semi-major axis (a)  │
│   - Estimated diameter   │   - Palermo Technical Scale│   - Eccentricity (e)     │
│   - Potentially Hazardous│   - Torino Hazard Scale    │   - Inclination (i)      │
│     Asteroid (PHA) flag  │   - Potential impact paths │   - Absolute magnitude H │
│   - Close-approach date  │   - Velocity at infinity   │   - Astrometric tier     │
│ • Ingestion Grain:       │ • Ingestion Grain:         │ • Ingestion Grain:       │
│   (approach_date, neows_id) (snapshot_key, sentry_id) │   (snapshot_key, spkid)  │
└──────────────────────────┴────────────────────────────┴──────────────────────────┘
```

---

## End-to-End Architecture

```
                       NASA / JPL REST APIs
           (NASA NeoWs  •  CNEOS Sentry  •  JPL SBDB)
                               │
                               ▼
               Ingestion & Raw Forensic Archival
       (Preserved JSON payloads & Authoritative Summaries)
                               │
                               ▼
            Centralized Ingestion Quality Gate (DQ-1)
          (pipeline_dq.py check-ingestion: Lineage & Gates)
                               │
                               ▼
               Processed Lakehouse Layer (Parquet)
           (Strict PyArrow schemas, Snappy compression)
                               │
                               ▼
           Pre-Resolution Source Output Quality Gate (DQ-2)
          (pipeline_dq.py check-outputs: 8 vs 7, Grains, Dates)
                               │
                               ▼
                  Deterministic Entity Resolution
                       (entity_resolution.py)
       ┌───────────────────────┴───────────────────────┐
       ▼                                               ▼
bridge_asteroid_identifier                    fact_entity_resolution
(Source IDs mapped to asteroid_key)           (Run metadata, rules & status)
       └───────────────────────┬───────────────────────┘
                               │
                               ▼
             Post-Resolution Crosswalk Quality Gate (DQ-3)
         (pipeline_dq.py check-crosswalk: Invariants & 1 Pivot)
                               │
                               ▼
              Unified Run Manifest & S3 Publication
         (run_manifest.json with all stage metrics & keys)
                               │
                               ▼
            Amazon S3 & Athena Serverless SQL Analytics
               (External tables & partition projection)
                               │
                               ▼
                 Interactive Streamlit Dossier
                   (5-Tab Mission Intelligence)
```

---

## Core Architectural Principles

### 1. Parquet is the Authoritative Storage Contract
All downstream analytics, Athena queries, entity resolution logic, and dashboard providers consume **processed Parquet files** with explicit PyArrow schemas.
- Raw JSON responses are archived for auditability, lineage, and replay.
- SQLite and CSV files serve as local development inspection targets and transient operational exports.
- Cloud analytics strictly query Snappy-compressed Parquet.

### 2. Conceptual S3 Lakehouse Layout
Storage keys follow deterministic, idempotent partition structures:

```
s3://nasa-asteroid-intelligence/
├── raw/                                                # Raw JSON payloads (audit & lineage)
│   ├── year=YYYY/month=MM/day=DD/                      # NeoWs raw JSON approach feeds
│   │   └── asteroids_raw.json
│   ├── sentry/risk_snapshot/                           # Sentry raw JSON risk snapshots
│   │   └── year=YYYY/month=MM/day=DD/
│   │       └── sentry_risk_snapshot_raw.json
│   └── sbdb/object/                                    # SBDB raw JSON payloads (per target)
│       └── year=YYYY/month=MM/day=DD/spkid={spkid}/
│           └── sbdb_raw_{spkid}.json
├── processed/                                          # Processed Parquet lakehouse tables
│   ├── year=YYYY/month=MM/day=DD/                      # NeoWs processed approach Parquet
│   │   └── asteroids.parquet
│   ├── sentry/risk_snapshot/                           # Sentry risk snapshot Parquet
│   │   └── year=YYYY/month=MM/day=DD/
│   │       └── fact_sentry_risk_snapshot.parquet
│   └── sbdb/                                           # SBDB normalized characterization tables
│       ├── fact_sbdb_object_snapshot/
│       │   └── year=YYYY/month=MM/day=DD/
│       │       └── fact_sbdb_object_snapshot.parquet
│       ├── fact_sbdb_orbit/
│       │   └── year=YYYY/month=MM/day=DD/
│       │       └── fact_sbdb_orbit.parquet
│       ├── fact_sbdb_orbit_element/
│       │   └── year=YYYY/month=MM/day=DD/
│       │       └── fact_sbdb_orbit_element.parquet
│       └── fact_sbdb_physical_parameter/
│           └── year=YYYY/month=MM/day=DD/
│               └── fact_sbdb_physical_parameter.parquet
├── processed_csv/                                      # Transient operational CSV exports
│   └── year=YYYY/month=MM/day=DD/
│       └── asteroids.csv
├── reference/asteroid_crosswalk/                       # Canonical entity resolution crosswalk
│   ├── bridge_asteroid_identifier/
│   │   └── year=YYYY/month=MM/day=DD/
│   │       └── bridge_asteroid_identifier.parquet
│   └── fact_entity_resolution/
│       └── year=YYYY/month=MM/day=DD/
│           └── fact_entity_resolution.parquet
└── metadata/                                           # Unified pipeline execution manifests & operational audit
    └── pipeline_runs/
        └── year=YYYY/month=MM/day=DD/
            └── run_manifest_{pipeline_run_id}.json
```


### 3. Canonical `asteroid_key` and SPK-ID Pivot
Because different NASA systems identify celestial objects using varying nomenclature (NeoWs IDs, Sentry catalog designations, provisional designations, IAU numbers), the platform implements a canonical identity model:
- **Primary Pivot:** The JPL SBDB **SPK-ID** serves as the canonical platform anchor (`is_primary_pivot == True`).
- **Canonical Key:** Every recognized asteroid is assigned a deterministic `asteroid_key` (e.g., `AST-2099942` or `AST-50548689`).
- **Crosswalk Datasets:**
  - `bridge_asteroid_identifier`: Resolves source-specific identifiers (`neows_id`, `sentry_id`, `des`, `fullname`, `spkid`) to a single `asteroid_key`.
  - `fact_entity_resolution`: Records resolution run metadata, applied matching rules, confidence states, and run linkages.

### 4. Deterministic Entity Resolution
The resolution engine (`entity_resolution.py`) implements deterministic matching rules without heuristic fuzziness:
- Matches on verified SPK-IDs (`EXACT_SPKID_MATCH`) or normalized astronomical designations (`EXACT_DESIGNATION_MATCH`).
- Strictly enforces three foundational crosswalk invariants:
  1. **Exactly One Primary Pivot:** Every canonical `asteroid_key` has exactly one record with `is_primary_pivot == True`.
  2. **Source Identifier Uniqueness:** Within any source system namespace, an identifier value is unique.
  3. **Zero Ambiguity:** Zero bridge records are generated in an `AMBIGUOUS` state; ambiguous entities are flagged and isolated.

### 5. Historical Sentry Snapshot Semantics
CNEOS Sentry does not provide retroactive historical observation APIs; it provides active computed risk solutions based on current astrometric fits.
- Each Sentry ingestion captures an immutable point-in-time snapshot (`fact_sentry_risk_snapshot`).
- **Historical backfills skip Sentry entirely.** Historical Sentry snapshots are never fabricated or backdated.
- During backfills, entity resolution executes without Sentry inputs, guaranteeing that zero synthetic Sentry `UNRESOLVED` records are generated.

---

## Analytical Intelligence Layer

The platform provides unified serverless SQL analytics in Amazon Athena across three DDL and view scripts:
- [`athena_schema.sql`](athena_schema.sql): Foundation table schemas with partition projection.
- [`athena_intelligence_layer.sql`](athena_intelligence_layer.sql): Multi-source relational views.
- [`athena_historical_risk.sql`](athena_historical_risk.sql): Snapshot coverage and risk metric lifecycle tracking.

### Major Analytical Views

| Analytical View | Grain | Description |
|---|---|---|
| `v_sbdb_characterization_profile` | `(spkid)` | Deep astronomical profile combining object snapshot metadata, Keplerian orbital parameters, and physical properties. |
| `v_neows_sentry_threat_watchlist` | `(closest_approach_date, neows_id)` | Integrated operations watchlist correlating upcoming NeoWs close approaches with active Sentry impact probabilities and risk scales. |
| `v_asteroid_cross_source_profile` | `(asteroid_key)` | Complete unified celestial portrait joining close approach telemetry, Keplerian orbits, physical characteristics, and impact risk. |
| `v_crosswalk_coverage_audit` | `(source_system, match_state, match_rule)` | Governance and data quality audit tracking resolution rates, unmapped targets, and match rules across all three source namespaces. |
| `v_sentry_risk_metric_history` | `(snapshot_key, sentry_id)` | Snapshot-by-snapshot observational delta tracking that monitors catalog metric changes across observation epochs without causal claims. |
| `v_sentry_snapshot_coverage` | `(snapshot_key)` | Longitudinal audit tracking monitored object counts, newly added/removed threats, and active impact solutions across ingestion runs. |

### Scientific Safety Protocol
- **Zero Composite Threat Formulas:** No synthetic danger scores, weighted indices, or combined "danger percentages" are fabricated.
- **Logarithmic Integrity:** No percentage differences are computed on logarithmic scales (such as the Palermo Scale). Only linear arithmetic deltas ($\Delta$) are reported.
- **Zero Causal Overreach:** Metric changes describe updates in published catalog parameters following new observation epochs, never physical orbital decay or causal trajectory shifts.

---

## Interactive Streamlit Dossier

The platform includes an interactive mission intelligence dossier implemented in [`dashboard.py`](dashboard.py) with cached data access in [`dashboard_data.py`](dashboard_data.py):

```bash
streamlit run dashboard.py
```

### The 5-Tab Multi-Source Dossier

1. **TAB 1 — OVERVIEW:**
   - Planetary defense KPI summary cards (imminent approaches, PHAs, monitored impact threats).
   - Multi-source identity coverage matrix showing resolution state (`RESOLVED`, `UNRESOLVED`, `AMBIGUOUS`).
   - Close-approach geometry telemetry (miss distance, relative velocity, estimated diameter bounds).
   - Observational protocol and safety guidance disclosures.
2. **TAB 2 — SBDB (Small-Body Database):**
   - Full Keplerian orbit elements ($a$, $e$, $i$, $\Omega$, $\omega$, $M$, period, perihelion, aphelion).
   - Physical characteristics ($H$ absolute magnitude, estimated diameter, geometric albedo, rotation period).
   - Orbit solution quality tier, solution ID, and data-arc parameters.
3. **TAB 3 — SENTRY (Impact Risk Monitor):**
   - Published Palermo Technical Scale (maximum and cumulative).
   - Torino Scale maximum hazard classification.
   - Cumulative and maximum impact probabilities with encounter paths count.
   - Potential impact year range and velocity at infinity ($v_\infty$).
4. **TAB 4 — HISTORY (Risk Metric Lifecycles):**
   - Longitudinal tracking of risk parameters across catalog snapshots.
   - Observational delta change log highlighting when impact probabilities or Palermo ratings shift across published epochs.
5. **TAB 5 — CROSSWALK (Identity Provenance):**
   - Full identity bridge audit displaying exact match rules (`EXACT_SPKID_MATCH`, `EXACT_DESIGNATION_MATCH`) and evidence.
   - Side-by-side namespace isolation grid displaying raw keys across NeoWs, SBDB, and Sentry.
   - Verification of the canonical anchor and primary pivot assignment.

---

## Production Orchestration

Automated production orchestration is implemented in [`.github/workflows/scheduled_pipeline.yml`](.github/workflows/scheduled_pipeline.yml).

### Architecture & Concurrency
- **Runner:** Single-job runner on `ubuntu-latest` with Python 3.11.
- **Schedule:** Automated daily execution at `06:00 UTC` (`cron: "0 6 * * *"`).
- **Manual Trigger:** `workflow_dispatch` with fail-fast `start_date` and `end_date` inputs (`YYYY-MM-DD`).
- **Concurrency Protection:** Group `nasa-asteroid-pipeline` with `cancel-in-progress: false` prevents overlapping runs while allowing active ingestions to finish deterministically.

### Execution Sequence

```
1. Clean Local Workspace Artifacts
   • Purges transient Parquets, raw payloads, summaries, DQ results, targets, and manifests
       ↓
2. Determine Execution Mode & Initialize Run
   • Validates manual date bounds (rejects end_date < start_date)
   • Generates deterministic PIPELINE_RUN_ID (pipe_<uuid12>) and exports run environment
   • Sets CURRENT_PRODUCTION or HISTORICAL_BACKFILL
       ↓
3. Ingest NASA NeoWs Telemetry
   • python nasa_asteroids.py (emits neows_summary.json with authoritative run_id and lineage)
   • Captures non-zero exit code without terminating prematurely before centralized DQ
       ↓
4. Ingest NASA CNEOS Sentry Telemetry (Mode S)
   • python nasa_sentry.py (CURRENT_PRODUCTION only; skipped during HISTORICAL_BACKFILL)
   • Captures non-zero exit code; preserves forensics
       ↓
5. Dynamic SBDB Target Generation
   • Generates sbdb_targets.txt from active NeoWs & Sentry threats (with priority filters)
   • Handles upstream ingestion failures gracefully (exports SBDB_TARGETS_COUNT)
       ↓
6. Ingest NASA/JPL SBDB Batch Telemetry
   • If SBDB_TARGETS_COUNT == 0: emits WORKFLOW_DIAGNOSTIC_SUMMARY (SBDB_NOT_EXECUTED_UPSTREAM_FAILURE)
   • If targets exist: runs python nasa_sbdb.py --targets-file ./sbdb_targets.txt (emits sbdb_batch_summary.json)
   • Captures exit code; preserves forensics
       ↓
7. Enforce Centralized Ingestion Quality Gate (DQ-1)
   • python pipeline_dq.py check-ingestion --execution-mode ... --output-file dq_ingestion.json
   • Halts pipeline immediately on threshold breach, missing summary, or lineage violation
       ↓
8. Enforce Pre-Resolution Source Output Quality Gate (DQ-2)
   • python pipeline_dq.py check-outputs --execution-mode ... --output-file dq_outputs.json
   • Verifies artifact co-presence (8 production vs 7 backfill), structural grains, and approach windows
       ↓
9. Execute Deterministic Entity Resolution Engine
   • python entity_resolution.py
   • Maps NeoWs + SBDB (+ Sentry in prod) to canonical crosswalk without synthetic unresolved records
       ↓
10. Enforce Crosswalk Quality Gate (DQ-3)
    • python pipeline_dq.py check-crosswalk --bridge-file ... --audit-file ... --output-file dq_crosswalk.json
    • Validates exactly 1 primary pivot per key, source identifier uniqueness, and zero AMBIGUOUS records
       ↓
11. Extract Crosswalk Metadata
    • Extracts resolution_run_id, evaluated identifiers, resolved count, and resolution rate
       ↓
12. Publish Crosswalk to Amazon S3
    • Uploads bridge and audit Parquets with lineage metadata to reference/asteroid_crosswalk/...
       ↓
13. Post-Publish S3 HeadObject Verification
    • Verifies non-zero ContentLength and matching run_id metadata on published S3 objects
       ↓
14. Generate and Publish Unified Execution Manifest
    • python pipeline_utils.py (generate_run_manifest & upload_run_manifest_to_s3)
    • Serializes run_manifest.json with all stage metrics and uploads to s3://.../metadata/pipeline_runs/...
       ↓
15. Emit GitHub Actions Step Summary
    • Markdown table reporting DQ gates, ingestion metrics, entity resolution, and S3 artifact links
```

---

## Historical Backfill Semantics

The platform maintains strict temporal semantics for historical data processing:

1. **Backfill Applies Exclusively to NeoWs:** NASA NeoWs supports historical queries over any valid date window.
2. **Sentry Ingestion is Skipped:** Sentry provides forward-looking impact probabilities based on active orbit solutions; it does not support retrospective historical snapshots. Historical backfill workflows explicitly skip Sentry.
3. **No Fabricated Snapshots:** Sentry snapshots are never backdated, interpolated, or synthesized.
4. **SBDB Capture Time:** Keplerian orbital solutions queried during historical backfills represent physical parameters at execution/query time.
5. **Crosswalk Partitioning:** Historical runs publish crosswalk datasets under the actual execution date partition (`year=YYYY/month=MM/day=DD/`) within `reference/asteroid_crosswalk/`.
6. **No Synthetic Unresolved Records:** Because Sentry input is omitted during backfill resolution, entity resolution does not create synthetic Sentry `UNRESOLVED` records.

---

## Data Quality & Reliability Gates

The pipeline enforces centralized operational data quality gates via `pipeline_dq.py`. A violation halts execution immediately (`exit 1`) before corrupt, partial, or unverified data can publish to S3:

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│                   OPERATIONAL DATA QUALITY ENGINE (pipeline_dq.py)               │
├─────────────────────────┬───────────────────────────────┬────────────────────────┤
│ Check Suite             │ Validation Logic              │ Failure Behavior       │
├─────────────────────────┼───────────────────────────────┼────────────────────────┤
│ check-ingestion         │ • Authoritative summary check │ Halts execution;       │
│                         │   (neows_summary, sbdb_batch) │ blocks downstream      │
│                         │ • Gate thresholds: NeoWs      │ resolution and S3      │
│                         │   rejection < 20%, SBDB batch │ publication            │
│                         │   failure < 25%               │                        │
│                         │ • Summary-to-Parquet lineage  │                        │
│                         │   (run_id & record alignment) │                        │
├─────────────────────────┼───────────────────────────────┼────────────────────────┤
│ check-outputs           │ • Pre-resolution accounting   │ Halts execution;       │
│                         │   (8 files prod, 7 backfill)  │ suppresses entity      │
│                         │ • SBDB table co-presence      │ resolution crosswalk   │
│                         │ • SBDB structural grain &     │                        │
│                         │   referential integrity       │                        │
│                         │ • Approach-window validation  │                        │
│                         │ • Snapshot-date validation    │                        │
├─────────────────────────┼───────────────────────────────┼────────────────────────┤
│ check-crosswalk         │ • Exactly 1 primary pivot per │ Halts execution;       │
│                         │   canonical asteroid_key      │ suppresses crosswalk   │
│                         │ • Source identifier uniqueness│ publication to S3      │
│                         │ • Single resolution_run_id    │                        │
│                         │ • Zero AMBIGUOUS bridge rows  │                        │
│                         │ • Audit-bridge integrity      │                        │
├─────────────────────────┼───────────────────────────────┼────────────────────────┤
│ check-s3-publication    │ • HeadObject verification of  │ Halts execution on     │
│                         │   ContentLength > 0           │ missing or corrupt     │
│                         │ • Metadata run_id alignment   │ cloud objects          │
└─────────────────────────┴───────────────────────────────┴────────────────────────┘
```

---

## Continuous Integration

The repository includes a fast, fully isolated quality gate in [`.github/workflows/ci.yml`](.github/workflows/ci.yml):

- **Triggers:** Every `push` and `pull_request` targeting `main` or `master`.
- **Environment:** `ubuntu-latest`, Python 3.11 with pip caching.
- **Formatting Gate:** `git diff --check` with zero tolerance for trailing whitespace or newline discrepancies.
- **Linter Gate:** `ruff check .` with zero tolerance for lint or syntax errors.
- **Full Test Suite:** `pytest -v` executing all **286 automated tests**.
- **Isolation Guarantee:** Runs with **zero AWS credentials**, **zero NASA API keys**, and **zero live network calls**. All external APIs and cloud operations are strictly mocked.

---

## Security & Secrets Management

- **Zero Credentials in Repository:** No API keys, AWS credentials, secret keys, or account IDs are stored in version control.
- **Local Development:** Credentials are loaded via `python-dotenv` from a local `.env` file that is excluded in `.gitignore`. A template is provided in [`.env.example`](.env.example).
- **Production CI/CD:** Production credentials (`NASA_API_KEY`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`) are managed exclusively through GitHub Actions Secrets and injected only into scoped production runner steps.
- **Log Masking & Redaction:** All URL query strings and logs scrub the `api_key` parameter using `pipeline_utils.redact_api_key`.

---

## Repository Structure

```
NASA-Intelligence-Platform/
├── .github/
│   └── workflows/
│       ├── ci.yml                           # Hardened, credential-free CI gate
│       └── scheduled_pipeline.yml           # Single-job M5 production orchestration
│
├── nasa_asteroids.py                        # NASA NeoWs ingestion engine & CLI
├── nasa_sentry.py                           # CNEOS Sentry Mode S ingestion engine & CLI
├── nasa_sbdb.py                             # NASA/JPL SBDB batch ingestion engine & CLI
├── entity_resolution.py                     # Deterministic multi-source crosswalk engine
├── database.py                              # SQLite relational storage engine (local dev)
├── pipeline_utils.py                        # Reusable HTTP, S3, PyArrow & redaction utils
├── pipeline_dq.py                           # Centralized operational data quality engine & CLI
│
├── dashboard.py                             # Interactive 5-tab Streamlit intelligence dossier
├── dashboard_data.py                        # Data provider & caching layer for dashboard
│
├── athena_schema.sql                        # Foundation Athena external table DDL
├── athena_queries.sql                       # Standard operational Athena SQL queries
├── athena_intelligence_layer.sql            # M5 cross-source Athena views & DDL
├── athena_historical_risk.sql               # M5 historical Sentry risk lifecycle views
├── schema.sql                               # Local SQLite schema DDL
│
├── test_nasa_asteroids.py                   # NeoWs ingestion test suite
├── test_nasa_sentry.py                      # Sentry Mode S ingestion test suite
├── test_nasa_sbdb.py                        # SBDB batch ingestion & failure gate test suite
├── test_entity_resolution.py                # Entity resolution & invariant test suite
├── test_historical_risk.py                  # Historical risk view validation test suite
├── test_intelligence_layer.py               # Multi-source intelligence view test suite
├── test_dashboard.py                        # Streamlit dashboard & data provider test suite
├── test_pipeline_utils.py                   # Shared utilities & manifest test suite
├── test_pipeline_dq.py                      # Centralized data quality engine test suite
│
├── requirements.txt                         # Pinned production and test dependencies
├── .env.example                             # Configuration environment variable template
├── .gitignore                               # Git exclusion rules
└── README.md                                # Authoritative platform documentation
```

---

## Tech Stack

| Component | Technology | Purpose |
|---|---|---|
| **Language** | Python 3.11 | Core ingestion, transformation, validation, and CLI tools |
| **Data Sources** | NASA NeoWs, JPL CNEOS Sentry, JPL SBDB | Planetary defense observation, impact risk, and Keplerian orbit APIs |
| **Object Storage** | Amazon S3 | Serverless data lakehouse (raw JSON, partitioned Parquet, and run manifests) |
| **Query Engine** | Amazon Athena (Trino) | Serverless interactive SQL analytics and multi-source views |
| **Columnar Engine** | PyArrow / Apache Parquet | Explicit schemas, Snappy compression, authoritative lakehouse datasets |
| **Identity Engine** | Python / PyArrow | Deterministic entity resolution, namespace isolation, primary pivot crosswalk |
| **Quality Engine** | Python / PyArrow / Boto3 | Centralized operational DQ engine (`pipeline_dq.py`) & invariant enforcement |
| **Dashboard** | Streamlit, Pandas | Interactive 5-tab mission intelligence dossier |
| **Quality & Linting**| Ruff, Pytest | Code formatting, static linting, and 286-test automated regression suite |
| **CI / CD** | GitHub Actions | Hardened pull-request validation and daily production orchestration |

---

## Running the Platform Locally

### 1. Clone the Repository
```bash
git clone <repository-url>
cd "Nasa Intelligence Platform"
```

### 2. Set Up a Virtual Environment & Install Dependencies
```bash
python -m venv .venv
# On Linux/macOS:
source .venv/bin/activate
# On Windows PowerShell:
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

### 3. Configure Local Environment Variables
Copy the example configuration to `.env` and provide your credentials:
```bash
cp .env.example .env
```
Edit `.env`:
```env
NASA_API_KEY=your_nasa_api_key_here
AWS_ACCESS_KEY_ID=your_aws_access_key
AWS_SECRET_ACCESS_KEY=your_aws_secret_key
AWS_DEFAULT_REGION=us-east-1
S3_BUCKET_NAME=nasa-asteroid-intelligence
```

### 4. Run Ingestion Pipelines Locally

**NASA NeoWs (Rolling 7-day approach feed):**
```bash
python nasa_asteroids.py
# Or custom date range:
python nasa_asteroids.py --start-date 2026-09-01 --end-date 2026-09-07
```

**NASA/JPL CNEOS Sentry (Mode S impact risk table):**
```bash
python nasa_sentry.py --mode S
```

**NASA/JPL SBDB Batch Ingestion (via targets file):**
```bash
# Ingest single target:
python nasa_sbdb.py --target 99942

# Ingest batch of targets:
python nasa_sbdb.py --targets-file sbdb_targets.txt
```

**Execute Deterministic Entity Resolution:**
```bash
python entity_resolution.py \
    --neows-path asteroids.parquet \
    --sbdb-path fact_sbdb_object_snapshot.parquet \
    --sentry-path fact_sentry_risk_snapshot.parquet \
    --output-dir crosswalk_out
```

### 5. Run Operational Data Quality Gates Locally

**Validate Source Ingestion Lineage & Thresholds:**
```bash
python pipeline_dq.py check-ingestion --execution-mode CURRENT_PRODUCTION
```

**Validate Pre-Resolution Source Outputs & Grains:**
```bash
python pipeline_dq.py check-outputs --execution-mode CURRENT_PRODUCTION --snapshot-date 2026-09-28
```

**Validate Crosswalk Invariants & Identity Bridge:**
```bash
python pipeline_dq.py check-crosswalk \
    --bridge-file crosswalk_out/bridge_asteroid_identifier.parquet \
    --audit-file crosswalk_out/fact_entity_resolution.parquet
```

**Execute Unified End-to-End Data Quality Suite:**
```bash
python pipeline_dq.py run-suite --execution-mode CURRENT_PRODUCTION
```

### 6. Run Quality Checks & Automated Tests
```bash
# Run complete test suite (286 tests)
pytest -v

# Run linter
ruff check .

# Check formatting and whitespace
git diff --check
```

### 7. Launch the Streamlit Intelligence Dossier
```bash
streamlit run dashboard.py
```

---

## Project Status & Roadmap

### Current Milestone Status

- **M1–M4:** Complete
  - NeoWs ingestion and data-quality foundation
  - SQL/data modeling
  - S3 / Parquet / Athena lakehouse and analytics
  - M4 productionization and GitHub Actions scheduling

- **M5:** Complete / Operational
  - **Phase 1–10:** Complete
    - NASA/JPL CNEOS Sentry Mode S impact monitoring (introduced in M5)
    - NASA/JPL Small-Body Database (SBDB) Keplerian and physical characterization (introduced in M5)
    - Deterministic multi-source entity resolution and canonical `asteroid_key` / SPK-ID crosswalk
    - Historical Sentry risk intelligence and snapshot lifecycle tracking
    - Cross-source intelligence views in Amazon Athena
    - Mission intelligence dashboard expansion (5-tab dossier)
    - Phase 10 production orchestration, native SBDB batch ingestion, and CI hardening
  - **Phase 11:** Complete
    - Phase 11A: Unified pipeline execution manifest (`run_manifest.json`) and metadata S3 publication
    - Phase 11B: Authoritative source summaries (`neows_summary.json`, `sbdb_batch_summary.json`) and centralized DQ engine (`pipeline_dq.py`)
    - Phase 11C: Scheduled workflow integration, multi-stage DQ enforcement, and zero-target failure provenance (`SBDB_NOT_EXECUTED_UPSTREAM_FAILURE`)
    - Phase 11D: Complete (Final regression verification, test expansion to 286 tests, and documentation hardening)
  - **Phase 12:** Pending

### Parked / Future Architectural Roadmap
*The following items are explicitly parked and represent future potential enhancements:*
- **Event-Driven Architecture:** Decoupling batch runs with Apache Kafka or AWS EventBridge.
- **Multi-Region Disaster Recovery:** Automated S3 Cross-Region Replication (CRR) and multi-region Athena catalog sync.
- **Ephemeris Calculations:** N-body gravitational trajectory simulation (the platform presents factual observational telemetry, not orbital integrations).
- **Containerized Deployment:** Docker packaging and AWS ECS / Fargate deployment for the Streamlit dashboard.
