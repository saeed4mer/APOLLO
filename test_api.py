"""Comprehensive tests for Milestone 6.3 API Skeleton and /health endpoint.

Adheres strictly to the locked M6.2 contract and M6.3 requirements:
- Application importability
- /health readiness probe healthy path (HTTP 200)
- /health storage readiness failure (HTTP 503) for each required Parquet asset
- /health query engine failure (HTTP 503)
- Execution mode fidelity ("LOCAL (DUCKDB / PARQUET LAKEHOUSE)")
- Zero external network calls (NASA, AWS, S3, internet)
- Pure offline determinism
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from api.main import app, create_app
from api.schemas import (
    AsteroidDetailResponse,
    CrosswalkRecord,
    CrosswalkResponse,
    ErrorResponse,
    HealthResponse,
    SbdbProfile,
    SbdbResponse,
    SentryProfile,
    SentryResponse,
    SentryHistoryRecord,
    SentryHistoryResponse,
    WatchlistResponse,
)
from api.service import REQUIRED_PARQUET_ASSETS
from dashboard_data import DashboardDataProvider
from entity_resolution import (
    BRIDGE_ASTEROID_IDENTIFIER_SCHEMA,
    FACT_ENTITY_RESOLUTION_SCHEMA,
)
from nasa_asteroids import ASTEROID_SCHEMA
from nasa_sbdb import (
    SBDB_OBJECT_SCHEMA,
    SBDB_ORBIT_ELEMENT_SCHEMA,
    SBDB_ORBIT_SCHEMA,
    SBDB_PHYS_PAR_SCHEMA,
)
from nasa_sentry import SENTRY_RISK_SNAPSHOT_SCHEMA
from test_dashboard import (
    _FIXTURE_ASTEROIDS,
    _FIXTURE_BRIDGE,
    _FIXTURE_RESOLUTION,
    _FIXTURE_SBDB_ELEM,
    _FIXTURE_SBDB_OBJ,
    _FIXTURE_SBDB_ORB,
    _FIXTURE_SBDB_PHYS,
    _FIXTURE_SENTRY,
)


@pytest.fixture
def mock_lakehouse(tmp_path: Path) -> Path:
    """Create a clean-room Lakehouse directory containing the required Parquet files."""
    # 1. asteroids.parquet (35 records)
    ast_table = pa.Table.from_pylist(_FIXTURE_ASTEROIDS, schema=ASTEROID_SCHEMA)
    pq.write_table(ast_table, tmp_path / "asteroids.parquet")

    # 2. bridge_asteroid_identifier.parquet
    bridge_table = pa.Table.from_pylist(
        _FIXTURE_BRIDGE, schema=BRIDGE_ASTEROID_IDENTIFIER_SCHEMA
    )
    pq.write_table(bridge_table, tmp_path / "bridge_asteroid_identifier.parquet")

    # 3. fact_sentry_risk_snapshot.parquet
    sentry_table = pa.Table.from_pylist(
        _FIXTURE_SENTRY, schema=SENTRY_RISK_SNAPSHOT_SCHEMA
    )
    pq.write_table(sentry_table, tmp_path / "fact_sentry_risk_snapshot.parquet")

    # 4. fact_sbdb_object_snapshot.parquet
    sbdb_table = pa.Table.from_pylist(
        _FIXTURE_SBDB_OBJ, schema=SBDB_OBJECT_SCHEMA
    )
    pq.write_table(sbdb_table, tmp_path / "fact_sbdb_object_snapshot.parquet")

    # 5. fact_entity_resolution.parquet (entity resolution audit log)
    res_table = pa.Table.from_pylist(
        _FIXTURE_RESOLUTION, schema=FACT_ENTITY_RESOLUTION_SCHEMA
    )
    pq.write_table(res_table, tmp_path / "fact_entity_resolution.parquet")

    # 6. fact_sbdb_orbit.parquet (orbit solutions)
    orb_table = pa.Table.from_pylist(
        _FIXTURE_SBDB_ORB, schema=SBDB_ORBIT_SCHEMA
    )
    pq.write_table(orb_table, tmp_path / "fact_sbdb_orbit.parquet")

    # 7. fact_sbdb_orbit_element.parquet (Keplerian orbital elements)
    elem_table = pa.Table.from_pylist(
        _FIXTURE_SBDB_ELEM, schema=SBDB_ORBIT_ELEMENT_SCHEMA
    )
    pq.write_table(elem_table, tmp_path / "fact_sbdb_orbit_element.parquet")

    # 8. fact_sbdb_physical_parameter.parquet (physical parameters)
    phys_table = pa.Table.from_pylist(
        _FIXTURE_SBDB_PHYS, schema=SBDB_PHYS_PAR_SCHEMA
    )
    pq.write_table(phys_table, tmp_path / "fact_sbdb_physical_parameter.parquet")

    return tmp_path


@pytest.fixture
def mock_client(mock_lakehouse: Path) -> TestClient:
    """Create a TestClient wired to the clean-room deterministic mock Lakehouse."""
    provider = DashboardDataProvider(base_dir=mock_lakehouse, execution_mode="LOCAL")
    test_app = create_app(provider=provider)
    return TestClient(test_app)


# ============================================================================
# 1. APPLICATION SKELETON & IMPORTS
# ============================================================================


def test_app_imports_cleanly():
    """Verify that the FastAPI app instance can be imported and has valid contract metadata."""
    assert app is not None
    assert app.title == "NASA Planetary Defense Platform API"
    assert app.version == "1.0.0"


def test_openapi_documentation_accessible(mock_client: TestClient):
    """Verify that /docs and /openapi.json are accessible and contain the 1.0.0 contract."""
    docs_resp = mock_client.get("/docs")
    assert docs_resp.status_code == 200

    openapi_resp = mock_client.get("/openapi.json")
    assert openapi_resp.status_code == 200
    spec = openapi_resp.json()
    assert spec["info"]["version"] == "1.0.0"
    assert "/health" in spec["paths"]


# ============================================================================
# 2. /health HEALTHY PATH
# ============================================================================


def test_health_healthy_path(mock_client: TestClient):
    """Verify GET /health returns HTTP 200 and matches the locked M6.2 schema when ready."""
    resp = mock_client.get("/health")
    assert resp.status_code == 200

    body = resp.json()
    # Validate against Pydantic schema (validates extra='forbid' and types)
    validated = HealthResponse.model_validate(body)

    assert validated.meta.api_version == "1.0.0"
    assert validated.meta.execution_mode == "LOCAL (DUCKDB / PARQUET LAKEHOUSE)"
    assert validated.data.status == "healthy"
    assert validated.data.checks.lakehouse_storage is True
    assert validated.data.checks.query_engine is True

    # Validate timestamp is ISO 8601 UTC
    dt = datetime.fromisoformat(validated.meta.timestamp)
    assert dt.tzinfo is not None


# ============================================================================
# 3. /health STORAGE READINESS FAILURES (503)
# ============================================================================


@pytest.mark.parametrize("missing_asset", REQUIRED_PARQUET_ASSETS)
def test_health_storage_failure_missing_asset(
    mock_lakehouse: Path, missing_asset: str
):
    """Verify GET /health returns HTTP 503 when any of the 4 required Parquet assets is missing."""
    target_file = mock_lakehouse / missing_asset
    assert target_file.is_file()
    target_file.unlink()

    provider = DashboardDataProvider(base_dir=mock_lakehouse, execution_mode="LOCAL")
    test_app = create_app(provider=provider)
    client = TestClient(test_app)

    resp = client.get("/health")
    assert resp.status_code == 503

    body = resp.json()
    validated = HealthResponse.model_validate(body)
    assert validated.data.status == "unavailable"
    assert validated.data.checks.lakehouse_storage is False
    assert validated.data.checks.query_engine is True


def test_health_storage_failure_empty_directory(tmp_path: Path):
    """Verify GET /health returns HTTP 503 when the lakehouse directory contains no assets."""
    provider = DashboardDataProvider(base_dir=tmp_path, execution_mode="LOCAL")
    test_app = create_app(provider=provider)
    client = TestClient(test_app)

    resp = client.get("/health")
    assert resp.status_code == 503
    body = resp.json()
    assert body["data"]["status"] == "unavailable"
    assert body["data"]["checks"]["lakehouse_storage"] is False


# ============================================================================
# 4. /health QUERY ENGINE FAILURE (503)
# ============================================================================


def test_health_query_engine_failure(mock_client: TestClient):
    """Verify GET /health returns HTTP 503 when DuckDB SELECT 1 fails."""
    with patch("api.service.duckdb.connect") as mock_conn:
        mock_conn.side_effect = RuntimeError("DuckDB engine initialization error")

        resp = mock_client.get("/health")
        assert resp.status_code == 503

        body = resp.json()
        validated = HealthResponse.model_validate(body)
        assert validated.data.status == "unavailable"
        assert validated.data.checks.lakehouse_storage is True
        assert validated.data.checks.query_engine is False


def test_health_simultaneous_failure(mock_lakehouse: Path):
    """Verify GET /health handles simultaneous storage and query engine failures."""
    (mock_lakehouse / "asteroids.parquet").unlink()

    provider = DashboardDataProvider(base_dir=mock_lakehouse, execution_mode="LOCAL")
    test_app = create_app(provider=provider)
    client = TestClient(test_app)

    with patch("api.service.duckdb.connect") as mock_conn:
        mock_conn.side_effect = RuntimeError("Engine unavailable")

        resp = client.get("/health")
        assert resp.status_code == 503
        body = resp.json()
        assert body["data"]["status"] == "unavailable"
        assert body["data"]["checks"]["lakehouse_storage"] is False
        assert body["data"]["checks"]["query_engine"] is False


# ============================================================================
# 5. EXECUTION MODE FIDELITY
# ============================================================================


def test_health_execution_mode_matches_provider(mock_client: TestClient):
    """Verify the execution_mode reported in meta strictly reflects provider.get_execution_mode()."""
    resp = mock_client.get("/health")
    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert meta["execution_mode"] == "LOCAL (DUCKDB / PARQUET LAKEHOUSE)"
    assert "ATHENA" not in meta["execution_mode"]


# ============================================================================
# 6. ZERO EXTERNAL NETWORK CALLS & HTTP METHODS
# ============================================================================


def test_health_zero_external_network_calls(mock_client: TestClient):
    """Verify that GET /health operates strictly offline with zero external network attempts."""
    with patch("urllib.request.urlopen") as mock_urlopen, patch(
        "requests.get"
    ) as mock_req_get, patch("boto3.client") as mock_boto:

        resp = mock_client.get("/health")
        assert resp.status_code == 200

        mock_urlopen.assert_not_called()
        mock_req_get.assert_not_called()
        mock_boto.assert_not_called()


def test_health_method_not_allowed(mock_client: TestClient):
    """Verify non-GET requests to /health return HTTP 405 Method Not Allowed."""
    resp = mock_client.post("/health")
    assert resp.status_code == 405


def test_default_app_instance_healthy():
    """Verify that the module-level app instance can serve /health."""
    client = TestClient(app)
    resp = client.get("/health")
    # In this environment, the 4 local Parquet assets exist, so it returns 200
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "healthy"


# ============================================================================
# 7. SLICE 1: GET /asteroids (WATCHLIST)
# ============================================================================


def test_asteroids_basic_success(mock_client: TestClient):
    """Test 1 — Basic success: GET /asteroids returns HTTP 200 and valid schema envelope."""
    resp = mock_client.get("/asteroids")
    assert resp.status_code == 200

    body = resp.json()
    validated = WatchlistResponse.model_validate(body)
    assert validated.meta.api_version == "1.0.0"
    assert validated.meta.execution_mode == "LOCAL (DUCKDB / PARQUET LAKEHOUSE)"
    assert validated.pagination.total == 35
    assert validated.pagination.limit == 50
    assert validated.pagination.offset == 0
    assert validated.pagination.returned == 35
    assert len(validated.data) == 35


def test_asteroids_default_pagination(mock_client: TestClient):
    """Test 2 — Default pagination: verify limit=50 and offset=0 when omitted."""
    resp = mock_client.get("/asteroids")
    assert resp.status_code == 200
    pag = resp.json()["pagination"]
    assert pag["limit"] == 50
    assert pag["offset"] == 0


def test_asteroids_custom_pagination(mock_client: TestClient):
    """Test 3 — Custom pagination: verify exact limit and offset slicing."""
    resp_all = mock_client.get("/asteroids?limit=50&offset=0")
    assert resp_all.status_code == 200
    all_data = resp_all.json()["data"]

    resp_slice = mock_client.get("/asteroids?limit=2&offset=1")
    assert resp_slice.status_code == 200
    sliced_body = resp_slice.json()

    assert sliced_body["pagination"]["limit"] == 2
    assert sliced_body["pagination"]["offset"] == 1
    assert sliced_body["pagination"]["returned"] == 2
    assert sliced_body["pagination"]["total"] == 35

    sliced_data = sliced_body["data"]
    assert len(sliced_data) == 2
    assert sliced_data[0]["neows_id"] == all_data[1]["neows_id"]
    assert sliced_data[1]["neows_id"] == all_data[2]["neows_id"]


def test_asteroids_total_before_pagination(mock_client: TestClient):
    """Test 4 — Total before pagination: verify pagination.total reflects filtered count."""
    resp = mock_client.get("/asteroids?limit=5&offset=0")
    assert resp.status_code == 200
    pag = resp.json()["pagination"]
    assert pag["returned"] == 5
    assert pag["total"] == 35
    assert pag["total"] > pag["returned"]


def test_asteroids_hazardous_filter(mock_client: TestClient):
    """Test 5 — Hazardous filter: verify strict boolean predicate satisfaction."""
    resp_true = mock_client.get("/asteroids?hazardous=true")
    assert resp_true.status_code == 200
    data_true = resp_true.json()["data"]
    assert len(data_true) > 0
    assert all(r["hazardous"] is True for r in data_true)

    resp_false = mock_client.get("/asteroids?hazardous=false")
    assert resp_false.status_code == 200
    data_false = resp_false.json()["data"]
    assert len(data_false) > 0
    assert all(r["hazardous"] is False for r in data_false)

    # Invariants: sum of counts equals total
    assert len(data_true) + len(data_false) == 35


def test_asteroids_sentry_filter(mock_client: TestClient):
    """Test 6 — Sentry filter: verify sentry_monitored predicate semantics."""
    resp_true = mock_client.get("/asteroids?sentry_monitored=true")
    assert resp_true.status_code == 200
    data_true = resp_true.json()["data"]
    assert len(data_true) > 0
    assert all(r["is_sentry_monitored"] is True for r in data_true)

    resp_false = mock_client.get("/asteroids?sentry_monitored=false")
    assert resp_false.status_code == 200
    data_false = resp_false.json()["data"]
    assert len(data_false) > 0
    assert all(r["is_sentry_monitored"] is False for r in data_false)

    assert len(data_true) + len(data_false) == 35


def test_asteroids_horizon_filter(mock_client: TestClient):
    """Test 7 — Horizon filter: verify miss_distance_km <= horizon_mkm * 1,000,000."""
    horizon = 20.0
    threshold = horizon * 1_000_000.0
    resp = mock_client.get(f"/asteroids?horizon_mkm={horizon}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) > 0
    assert all(r["miss_distance_km"] <= threshold for r in data)


def test_asteroids_combined_filters(mock_client: TestClient):
    """Test 8 — Combined filters: verify multiple predicates applied together."""
    resp = mock_client.get("/asteroids?hazardous=true&sentry_monitored=true")
    assert resp.status_code == 200
    data = resp.json()["data"]
    for r in data:
        assert r["hazardous"] is True
        assert r["is_sentry_monitored"] is True


def test_asteroids_empty_filtered_collection(mock_client: TestClient):
    """Test 9 — Empty filtered collection: returns HTTP 200 with data=[] and total=0."""
    # No asteroids have miss distance <= 10,000 km in the fixture
    resp = mock_client.get("/asteroids?horizon_mkm=0.01")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["pagination"]["total"] == 0
    assert body["pagination"]["returned"] == 0


def test_asteroids_offset_beyond_total(mock_client: TestClient):
    """Test 10 — Offset beyond total: returns HTTP 200, data=[], total=filtered_total."""
    resp = mock_client.get("/asteroids?offset=500")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["pagination"]["returned"] == 0
    assert body["pagination"]["total"] == 35
    assert body["pagination"]["offset"] == 500


def test_asteroids_invalid_limit_zero(mock_client: TestClient):
    """Test 11 — Invalid limit: limit=0 yields HTTP 422."""
    resp = mock_client.get("/asteroids?limit=0")
    assert resp.status_code == 422


def test_asteroids_invalid_limit_above_max(mock_client: TestClient):
    """Test 12 — Limit above maximum: limit=501 yields HTTP 422."""
    resp = mock_client.get("/asteroids?limit=501")
    assert resp.status_code == 422


def test_asteroids_negative_offset(mock_client: TestClient):
    """Test 13 — Negative offset: offset=-1 yields HTTP 422."""
    resp = mock_client.get("/asteroids?offset=-1")
    assert resp.status_code == 422


def test_asteroids_invalid_horizon(mock_client: TestClient):
    """Test 14 — Invalid horizon: horizon_mkm <= 0 yields HTTP 422."""
    resp_zero = mock_client.get("/asteroids?horizon_mkm=0")
    assert resp_zero.status_code == 422

    resp_neg = mock_client.get("/asteroids?horizon_mkm=-5")
    assert resp_neg.status_code == 422


def test_asteroids_mode_rejected(mock_client: TestClient):
    """Test 15 — Mode rejected: extra parameter mode=hazardous is rejected with HTTP 422."""
    resp = mock_client.get("/asteroids?mode=hazardous")
    assert resp.status_code == 422


def test_asteroids_null_preservation(mock_client: TestClient):
    """Test 16 — Null preservation: missing scientific values serialize as genuine null."""
    resp = mock_client.get("/asteroids?sentry_monitored=false")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) > 0

    # Inspect first unmonitored object
    sample = data[0]
    assert sample["is_sentry_monitored"] is False
    assert sample["sentry_id"] is None
    assert sample["sentry_impact_probability"] is None
    assert sample["sentry_palermo_scale_max"] is None
    assert sample["sentry_torino_scale_max"] is None
    assert sample["sentry_potential_impacts_count"] is None
    assert sample["sentry_impact_year_range"] is None


def test_asteroids_deterministic_ordering(mock_client: TestClient):
    """Test 17 — Deterministic ordering: miss_distance_km ASC, closest_approach_date ASC, neows_id ASC."""
    resp = mock_client.get("/asteroids?limit=50")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) >= 2

    for i in range(len(data) - 1):
        curr = data[i]
        nxt = data[i + 1]

        curr_key = (
            curr["miss_distance_km"],
            curr["closest_approach_date"],
            curr["neows_id"],
        )
        nxt_key = (
            nxt["miss_distance_km"],
            nxt["closest_approach_date"],
            nxt["neows_id"],
        )
        assert curr_key <= nxt_key, f"Ordering violation at index {i}: {curr_key} > {nxt_key}"


def test_asteroids_acceptance_objects(mock_client: TestClient):
    """Test 18 — Acceptance-object compatibility: verify 2010 TW54 and 1998 FF14 expose expected semantics."""
    resp = mock_client.get("/asteroids?limit=50")
    assert resp.status_code == 200
    by_neows = {r["neows_id"]: r for r in resp.json()["data"]}

    # Acceptance Object 1: 2010 TW54 (neows_id: 3548666)
    assert "3548666" in by_neows, "2010 TW54 must be present in watchlist"
    tw = by_neows["3548666"]
    assert "2010 TW54" in tw["name"]
    assert tw["match_state"] == "RESOLVED"
    assert tw["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert tw["is_sentry_monitored"] is True
    assert tw["has_sbdb_characterization"] is True
    assert tw["sbdb_spkid"] == "50548689"

    # Acceptance Object 2: 1998 FF14 (neows_id: 2523934)
    assert "2523934" in by_neows, "1998 FF14 must be present in watchlist"
    ff = by_neows["2523934"]
    assert "1998 FF14" in ff["name"]
    assert ff["hazardous"] is True
    assert ff["match_state"] == "UNRESOLVED"
    assert ff["asteroid_key"] is None
    assert ff["is_sentry_monitored"] is False
    assert ff["has_sbdb_characterization"] is False


# ============================================================================
# 8. SLICE 2: GET /asteroids/{neows_id} (OBJECT + RESOLUTION STATE)
# ============================================================================


def test_asteroid_detail_resolved(mock_client: TestClient):
    """Test 1 — Basic resolved object: GET /asteroids/3548666 returns HTTP 200 with RESOLVED state."""
    resp = mock_client.get("/asteroids/3548666")
    assert resp.status_code == 200

    body = resp.json()
    validated = AsteroidDetailResponse.model_validate(body)
    assert validated.data.neows_id == "3548666"
    assert validated.data.name == "(2010 TW54)"
    assert validated.data.match_state == "RESOLVED"
    assert validated.data.asteroid_key == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert validated.resolution.match_state == "RESOLVED"
    assert validated.resolution.asteroid_key == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert validated.resolution.match_rule == "EXACT_DESIGNATION_MATCH"


def test_asteroid_detail_unresolved(mock_client: TestClient):
    """Test 2 — Unresolved object: GET /asteroids/2523934 returns HTTP 200 with UNRESOLVED state."""
    resp = mock_client.get("/asteroids/2523934")
    assert resp.status_code == 200

    body = resp.json()
    validated = AsteroidDetailResponse.model_validate(body)
    assert validated.data.neows_id == "2523934"
    assert validated.data.hazardous is True
    assert validated.data.match_state == "UNRESOLVED"
    assert validated.data.asteroid_key is None
    assert validated.resolution.match_state == "UNRESOLVED"
    assert validated.resolution.asteroid_key is None
    assert validated.data.approaches_recorded_count >= 1


def test_asteroid_detail_non_existent(mock_client: TestClient):
    """Test 3 — Non-existent numeric ID: returns HTTP 404 with TARGET_NOT_FOUND error code."""
    resp = mock_client.get("/asteroids/99999999")
    assert resp.status_code == 404

    body = resp.json()
    validated = ErrorResponse.model_validate(body)
    assert validated.error.code == "TARGET_NOT_FOUND"
    assert "99999999" in validated.error.message
    assert validated.meta.api_version == "1.0.0"


def test_asteroid_detail_invalid_alphabetic(mock_client: TestClient):
    """Test 4 — Invalid alphabetic ID: /asteroids/abc returns HTTP 422."""
    resp = mock_client.get("/asteroids/abc")
    assert resp.status_code == 422


def test_asteroid_detail_invalid_negative(mock_client: TestClient):
    """Test 5 — Invalid negative ID: /asteroids/-1 returns HTTP 422."""
    resp = mock_client.get("/asteroids/-1")
    assert resp.status_code == 422


def test_asteroid_detail_invalid_decimal(mock_client: TestClient):
    """Test 6 — Invalid decimal ID: /asteroids/1.5 returns HTTP 422."""
    resp = mock_client.get("/asteroids/1.5")
    assert resp.status_code == 422


def test_asteroid_detail_resolution_block(mock_client: TestClient):
    """Test 7 — Resolution block: verify structure, match_state, and evidence fields."""
    resp = mock_client.get("/asteroids/3548666")
    assert resp.status_code == 200
    res = resp.json()["resolution"]
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert res["match_rule"] is not None
    assert res["evidence"] is not None
    assert res["resolved_at"] is not None


def test_asteroid_detail_primary_encounter_selection(tmp_path: Path):
    """Test 8 — Primary encounter selection: verify minimum miss_distance_km is selected."""
    encounters = [
        {
            "id": "7777001",
            "name": "(2026 TEST)",
            "closest_approach_date": "2026-10-10",
            "miss_distance_km": 15000000.0,
            "hazardous": False,
        },
        {
            "id": "7777001",
            "name": "(2026 TEST)",
            "closest_approach_date": "2026-10-05",
            "miss_distance_km": 5000000.0,
            "hazardous": False,
        },
    ]
    pq.write_table(pa.Table.from_pylist(encounters, schema=ASTEROID_SCHEMA), tmp_path / "asteroids.parquet")
    pq.write_table(pa.Table.from_pylist([], schema=BRIDGE_ASTEROID_IDENTIFIER_SCHEMA), tmp_path / "bridge_asteroid_identifier.parquet")
    pq.write_table(pa.Table.from_pylist([], schema=SENTRY_RISK_SNAPSHOT_SCHEMA), tmp_path / "fact_sentry_risk_snapshot.parquet")
    pq.write_table(pa.Table.from_pylist([], schema=SBDB_OBJECT_SCHEMA), tmp_path / "fact_sbdb_object_snapshot.parquet")

    provider = DashboardDataProvider(base_dir=tmp_path, execution_mode="LOCAL")
    client = TestClient(create_app(provider=provider))

    resp = client.get("/asteroids/7777001")
    assert resp.status_code == 200
    assert resp.json()["data"]["miss_distance_km"] == 5000000.0
    assert resp.json()["data"]["closest_approach_date"] == "2026-10-05"


def test_asteroid_detail_tie_breaker(tmp_path: Path):
    """Test 9 — Closest-distance tie-breaker: closest_approach_date ASC breaks identical distance ties."""
    encounters = [
        {
            "id": "7777002",
            "name": "(2026 TIE)",
            "closest_approach_date": "2026-10-15",
            "miss_distance_km": 6000000.0,
            "hazardous": False,
        },
        {
            "id": "7777002",
            "name": "(2026 TIE)",
            "closest_approach_date": "2026-10-02",  # Earlier date wins tie
            "miss_distance_km": 6000000.0,
            "hazardous": False,
        },
    ]
    pq.write_table(pa.Table.from_pylist(encounters, schema=ASTEROID_SCHEMA), tmp_path / "asteroids.parquet")
    pq.write_table(pa.Table.from_pylist([], schema=BRIDGE_ASTEROID_IDENTIFIER_SCHEMA), tmp_path / "bridge_asteroid_identifier.parquet")
    pq.write_table(pa.Table.from_pylist([], schema=SENTRY_RISK_SNAPSHOT_SCHEMA), tmp_path / "fact_sentry_risk_snapshot.parquet")
    pq.write_table(pa.Table.from_pylist([], schema=SBDB_OBJECT_SCHEMA), tmp_path / "fact_sbdb_object_snapshot.parquet")

    provider = DashboardDataProvider(base_dir=tmp_path, execution_mode="LOCAL")
    client = TestClient(create_app(provider=provider))

    resp = client.get("/asteroids/7777002")
    assert resp.status_code == 200
    assert resp.json()["data"]["miss_distance_km"] == 6000000.0
    assert resp.json()["data"]["closest_approach_date"] == "2026-10-02"


def test_asteroid_detail_approaches_count(tmp_path: Path):
    """Test 10 — Approaches count: verify approaches_recorded_count reflects all available encounters."""
    encounters = [
        {"id": "7777003", "name": "(2026 COUNT)", "closest_approach_date": f"2026-10-0{i}", "miss_distance_km": float(i * 1000000), "hazardous": False}
        for i in range(1, 5)
    ]
    pq.write_table(pa.Table.from_pylist(encounters, schema=ASTEROID_SCHEMA), tmp_path / "asteroids.parquet")
    pq.write_table(pa.Table.from_pylist([], schema=BRIDGE_ASTEROID_IDENTIFIER_SCHEMA), tmp_path / "bridge_asteroid_identifier.parquet")
    pq.write_table(pa.Table.from_pylist([], schema=SENTRY_RISK_SNAPSHOT_SCHEMA), tmp_path / "fact_sentry_risk_snapshot.parquet")
    pq.write_table(pa.Table.from_pylist([], schema=SBDB_OBJECT_SCHEMA), tmp_path / "fact_sbdb_object_snapshot.parquet")

    provider = DashboardDataProvider(base_dir=tmp_path, execution_mode="LOCAL")
    client = TestClient(create_app(provider=provider))

    resp = client.get("/asteroids/7777003")
    assert resp.status_code == 200
    assert resp.json()["data"]["approaches_recorded_count"] == 4


def test_asteroid_detail_selection_rule(mock_client: TestClient):
    """Test 11 — Selection rule: verify selection_rule value is exactly CLOSEST_OBSERVED_APPROACH."""
    resp = mock_client.get("/asteroids/3548666")
    assert resp.status_code == 200
    assert resp.json()["data"]["selection_rule"] == "CLOSEST_OBSERVED_APPROACH"


def test_asteroid_detail_null_preservation(mock_client: TestClient):
    """Test 12 — Null preservation: missing cross-source values serialize as genuine null."""
    resp = mock_client.get("/asteroids/2523934")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["asteroid_key"] is None
    assert data["sentry_id"] is None
    assert data["sbdb_spkid"] is None
    assert data["sbdb_designation"] is None
    assert data["sbdb_fullname"] is None
    assert data["sbdb_orbit_class_name"] is None


def test_acceptance_object_2010_tw54(mock_client: TestClient):
    """Test 13 — Acceptance object 2010 TW54: verify all contract requirements for 3548666."""
    resp = mock_client.get("/asteroids/3548666")
    assert resp.status_code == 200
    body = resp.json()

    assert body["data"]["neows_id"] == "3548666"
    assert "2010 TW54" in body["data"]["name"]
    assert body["data"]["match_state"] == "RESOLVED"
    assert body["data"]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert body["data"]["is_sentry_monitored"] is True
    assert body["data"]["has_sbdb_characterization"] is True
    assert body["data"]["sbdb_spkid"] == "50548689"
    assert body["data"]["selection_rule"] == "CLOSEST_OBSERVED_APPROACH"
    assert body["data"]["approaches_recorded_count"] == 1

    assert body["resolution"]["match_state"] == "RESOLVED"
    assert body["resolution"]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"


def test_acceptance_object_1998_ff14(mock_client: TestClient):
    """Test 14 — Acceptance object 1998 FF14: verify all contract requirements for 2523934."""
    resp = mock_client.get("/asteroids/2523934")
    assert resp.status_code == 200
    body = resp.json()

    assert body["data"]["neows_id"] == "2523934"
    assert "1998 FF14" in body["data"]["name"]
    assert body["data"]["hazardous"] is True
    assert body["data"]["match_state"] == "UNRESOLVED"
    assert body["data"]["asteroid_key"] is None
    assert body["data"]["is_sentry_monitored"] is False
    assert body["data"]["has_sbdb_characterization"] is False
    assert body["data"]["selection_rule"] == "CLOSEST_OBSERVED_APPROACH"

    assert body["resolution"]["match_state"] == "UNRESOLVED"
    assert body["resolution"]["asteroid_key"] is None


def test_asteroid_detail_ambiguous_resolution(mock_client: TestClient):
    """Test 15 — Ambiguous resolution state: verify AMBIGUOUS match_state with asteroid_key=null."""
    ambiguous_state = {
        "neows_id": "3548666",
        "match_state": "AMBIGUOUS",
        "asteroid_key": None,
        "match_rule": "BRIDGE_MULTIPLE_CANDIDATE_KEYS",
        "evidence": "Identifier maps to 2 distinct candidate entity keys in bridge fallback.",
        "resolved_at": None,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state",
        return_value=ambiguous_state,
    ):
        resp = mock_client.get("/asteroids/3548666")
        assert resp.status_code == 200
        body = resp.json()

        assert body["data"]["match_state"] == "AMBIGUOUS"
        assert body["data"]["asteroid_key"] is None
        assert body["resolution"]["match_state"] == "AMBIGUOUS"
        assert body["resolution"]["asteroid_key"] is None
        # Telemetry is still present
        assert body["data"]["neows_id"] == "3548666"
        assert body["data"]["name"] == "(2010 TW54)"


def test_asteroid_detail_no_pagination(mock_client: TestClient):
    """Test 16 — No pagination on single object endpoint: verify pagination key is absent."""
    resp = mock_client.get("/asteroids/3548666")
    assert resp.status_code == 200
    body = resp.json()
    assert "pagination" not in body


def test_asteroid_detail_no_future_payloads(mock_client: TestClient):
    """Test 17 — No future endpoint data leakage: top keys are strictly meta, data, resolution."""
    resp = mock_client.get("/asteroids/3548666")
    assert resp.status_code == 200
    body = resp.json()

    # Top-level keys must be strictly meta, data, resolution
    assert set(body.keys()) == {"meta", "data", "resolution"}

    # Data block must not contain future endpoint sub-resource objects
    forbidden_keys = {
        "sbdb_profile",
        "sentry_profile",
        "historical_risk",
        "crosswalk",
        "orbit_elements",
        "physical_parameters",
    }
    assert not forbidden_keys.intersection(body["data"].keys())


def test_asteroid_detail_zero_external_network_calls(mock_client: TestClient):
    """Test 18 — Zero external network calls: operates purely offline without outbound requests."""
    with patch("urllib.request.urlopen") as mock_urlopen, patch(
        "requests.get"
    ) as mock_req_get, patch("boto3.client") as mock_boto:

        resp = mock_client.get("/asteroids/3548666")
        assert resp.status_code == 200

        mock_urlopen.assert_not_called()
        mock_req_get.assert_not_called()
        mock_boto.assert_not_called()


# ============================================================================
# 9. SLICE 3: GET /asteroids/{neows_id}/sbdb (SBDB PROFILE)
# ============================================================================


def test_sbdb_resolved_success(mock_client: TestClient):
    """Test 1 — Resolved object returns HTTP 200 with populated profile."""
    resp = mock_client.get("/asteroids/3548666/sbdb")
    assert resp.status_code == 200

    body = resp.json()
    validated = SbdbResponse.model_validate(body)
    assert validated.data is not None
    assert isinstance(validated.data, SbdbProfile)
    assert validated.data.spkid == "50548689"
    assert validated.data.asteroid_key == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert validated.data.designation == "2010 TW54"
    assert validated.data.fullname == "(2010 TW54)"
    assert validated.data.is_neo is True
    assert validated.data.is_pha is False
    assert validated.data.orbit_class_code == "APO"
    assert validated.data.orbit_class_name == "Apollo"
    assert validated.data.astrometric_data_quality_tier == "FOLLOWUP_PRIORITY_LIMITED_ARC"


def test_sbdb_resolved_resolution_block(mock_client: TestClient):
    """Test 2 — Resolved object contains correct authoritative resolution block."""
    resp = mock_client.get("/asteroids/3548666/sbdb")
    assert resp.status_code == 200
    res = resp.json()["resolution"]
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert res["match_rule"] == "EXACT_DESIGNATION_MATCH"
    assert res["evidence"] is not None
    assert res["resolved_at"] is not None


def test_sbdb_resolved_provider_backed(mock_client: TestClient):
    """Test 3 — Resolved object returns provider-backed SBDB profile fields."""
    resp = mock_client.get("/asteroids/3548666/sbdb")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["epoch_jd"] == 2461200.5
    assert data["soln_date"] == "2021-04-15 04:16:11"
    assert data["orbit_source"] == "JPL"
    assert data["producer"] == "Otto Matic"
    assert data["data_arc_days"] == 5
    assert data["n_obs_used"] == 70
    assert data["condition_code"] == "6"
    assert abs(data["rms"] - 0.47283) < 1e-4
    assert abs(data["earth_moid_au"] - 0.000607628) < 1e-6
    assert abs(data["eccentricity"] - 0.23400365) < 1e-4
    assert abs(data["semi_major_axis_au"] - 1.0426995) < 1e-4


def test_sbdb_canonical_spkid_from_provider(mock_client: TestClient):
    """Test 4 — Canonical SPKID comes from SBDB/provider data, not NeoWs ID."""
    resp = mock_client.get("/asteroids/3548666/sbdb")
    assert resp.status_code == 200
    data = resp.json()["data"]
    # NeoWs ID is 3548666, SPKID is 50548689
    assert data["spkid"] != "3548666"
    assert data["spkid"] == "50548689"


def test_sbdb_unresolved_returns_200(mock_client: TestClient):
    """Test 5 — Unresolved object returns HTTP 200."""
    resp = mock_client.get("/asteroids/2523934/sbdb")
    assert resp.status_code == 200
    validated = SbdbResponse.model_validate(resp.json())
    assert validated.resolution.match_state == "UNRESOLVED"


def test_sbdb_unresolved_data_null(mock_client: TestClient):
    """Test 6 — Unresolved object returns data=null, not an empty SBDB object."""
    resp = mock_client.get("/asteroids/2523934/sbdb")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] is None


def test_sbdb_unresolved_no_fabricated_key(mock_client: TestClient):
    """Test 7 — Unresolved object does not fabricate asteroid_key."""
    resp = mock_client.get("/asteroids/2523934/sbdb")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"]["asteroid_key"] is None


def test_sbdb_ambiguous_returns_200(mock_client: TestClient):
    """Test 8 — Ambiguous object returns HTTP 200."""
    ambiguous_state = {
        "neows_id": "3548666",
        "match_state": "AMBIGUOUS",
        "asteroid_key": None,
        "match_rule": "BRIDGE_MULTIPLE_CANDIDATE_KEYS",
        "evidence": "Identifier maps to multiple candidate entity keys.",
        "resolved_at": None,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state",
        return_value=ambiguous_state,
    ):
        resp = mock_client.get("/asteroids/3548666/sbdb")
        assert resp.status_code == 200
        validated = SbdbResponse.model_validate(resp.json())
        assert validated.resolution.match_state == "AMBIGUOUS"


def test_sbdb_ambiguous_data_null(mock_client: TestClient):
    """Test 9 — Ambiguous object returns data=null."""
    ambiguous_state = {
        "neows_id": "3548666",
        "match_state": "AMBIGUOUS",
        "asteroid_key": None,
        "match_rule": "BRIDGE_MULTIPLE_CANDIDATE_KEYS",
        "evidence": "Identifier maps to multiple candidate entity keys.",
        "resolved_at": None,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state",
        return_value=ambiguous_state,
    ):
        resp = mock_client.get("/asteroids/3548666/sbdb")
        assert resp.status_code == 200
        assert resp.json()["data"] is None


def test_sbdb_ambiguous_no_candidate_chosen(mock_client: TestClient):
    """Test 10 — Ambiguous object does not choose candidate; asteroid_key is null."""
    ambiguous_state = {
        "neows_id": "3548666",
        "match_state": "AMBIGUOUS",
        "asteroid_key": None,
        "match_rule": "BRIDGE_MULTIPLE_CANDIDATE_KEYS",
        "evidence": "Multiple candidates exist.",
        "resolved_at": None,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state",
        return_value=ambiguous_state,
    ), patch(
        "dashboard_data.DashboardDataProvider.get_sbdb_profile"
    ) as mock_sbdb:
        resp = mock_client.get("/asteroids/3548666/sbdb")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resolution"]["match_state"] == "AMBIGUOUS"
        assert body["resolution"]["asteroid_key"] is None
        assert body["data"] is None
        mock_sbdb.assert_not_called()


def test_sbdb_resolved_unavailable_vs_unresolved(mock_client: TestClient):
    """Test 11 — Resolved-but-SBDB-unavailable is distinct from unresolved."""
    # When get_sbdb_profile returns None for a resolved key
    with patch(
        "dashboard_data.DashboardDataProvider.get_sbdb_profile",
        return_value=None,
    ):
        resp = mock_client.get("/asteroids/3548666/sbdb")
        assert resp.status_code == 200
        body = resp.json()
        validated = SbdbResponse.model_validate(body)
        assert validated.resolution.match_state == "RESOLVED"
        assert validated.resolution.asteroid_key == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
        assert validated.data is None


def test_sbdb_nonexistent_numeric_id_returns_404(mock_client: TestClient):
    """Test 12 — Nonexistent numeric NeoWs ID returns HTTP 404 with TARGET_NOT_FOUND."""
    resp = mock_client.get("/asteroids/99999999/sbdb")
    assert resp.status_code == 404

    body = resp.json()
    validated = ErrorResponse.model_validate(body)
    assert validated.error.code == "TARGET_NOT_FOUND"
    assert "99999999" in validated.error.message


def test_sbdb_invalid_alphabetic_id_returns_422(mock_client: TestClient):
    """Test 13 — Invalid alphabetic ID /asteroids/abc/sbdb returns HTTP 422."""
    resp = mock_client.get("/asteroids/abc/sbdb")
    assert resp.status_code == 422


def test_sbdb_invalid_negative_id_returns_422(mock_client: TestClient):
    """Test 14 — Invalid negative ID /asteroids/-1/sbdb returns HTTP 422."""
    resp = mock_client.get("/asteroids/-1/sbdb")
    assert resp.status_code == 422


def test_sbdb_invalid_decimal_id_returns_422(mock_client: TestClient):
    """Test 15 — Invalid decimal ID /asteroids/1.5/sbdb returns HTTP 422."""
    resp = mock_client.get("/asteroids/1.5/sbdb")
    assert resp.status_code == 422


def test_sbdb_invalid_alphanumeric_id_returns_422(mock_client: TestClient):
    """Test 15b — Invalid alphanumeric ID /asteroids/3548666a/sbdb returns HTTP 422."""
    resp = mock_client.get("/asteroids/3548666a/sbdb")
    assert resp.status_code == 422


def test_sbdb_no_pagination(mock_client: TestClient):
    """Test 16 — Response has no pagination field."""
    resp = mock_client.get("/asteroids/3548666/sbdb")
    assert resp.status_code == 200
    body = resp.json()
    assert "pagination" not in body


def test_sbdb_no_future_payloads(mock_client: TestClient):
    """Test 17 — Response has strictly meta, data, resolution keys; no Sentry/history/crosswalk payloads."""
    resp = mock_client.get("/asteroids/3548666/sbdb")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"meta", "data", "resolution"}

    if body["data"] is not None:
        forbidden_keys = {
            "sentry",
            "sentry_profile",
            "historical_risk",
            "crosswalk",
            "threat_score",
            "risk_score",
        }
        assert not forbidden_keys.intersection(body["data"].keys())


def test_sbdb_null_fields_remain_json_null(mock_client: TestClient):
    """Test 18 — Genuine null fields in SBDB profile remain JSON null."""
    resp = mock_client.get("/asteroids/3548666/sbdb")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data is not None
    # shortname is None in fixture
    assert "shortname" in data
    assert data["shortname"] is None


def test_sbdb_api_version_and_metadata(mock_client: TestClient):
    """Test 19 & 20 — API version is 1.0.0 and execution_mode comes from provider."""
    resp = mock_client.get("/asteroids/3548666/sbdb")
    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert meta["api_version"] == "1.0.0"
    assert meta["execution_mode"] == "LOCAL (DUCKDB / PARQUET LAKEHOUSE)"
    dt = datetime.fromisoformat(meta["timestamp"])
    assert dt.tzinfo is not None


def test_sbdb_zero_external_network_calls(mock_client: TestClient):
    """Test 21 — No external network calls during SBDB profile lookup."""
    with patch("urllib.request.urlopen") as mock_urlopen, patch(
        "requests.get"
    ) as mock_req_get, patch("boto3.client") as mock_boto:

        resp_resolved = mock_client.get("/asteroids/3548666/sbdb")
        assert resp_resolved.status_code == 200

        resp_unresolved = mock_client.get("/asteroids/2523934/sbdb")
        assert resp_unresolved.status_code == 200

        mock_urlopen.assert_not_called()
        mock_req_get.assert_not_called()
        mock_boto.assert_not_called()


def test_sbdb_acceptance_object_2010_tw54(mock_client: TestClient):
    """Test 22 — Acceptance object 2010 TW54 (3548666): verify complete authoritative profile."""
    resp = mock_client.get("/asteroids/3548666/sbdb")
    assert resp.status_code == 200
    body = resp.json()

    assert body["resolution"]["match_state"] == "RESOLVED"
    assert body["resolution"]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"

    data = body["data"]
    assert data is not None
    assert data["spkid"] == "50548689"
    assert data["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert data["designation"] == "2010 TW54"
    assert data["fullname"] == "(2010 TW54)"
    assert data["is_neo"] is True
    assert data["is_pha"] is False
    assert data["orbit_class_code"] == "APO"
    assert data["orbit_class_name"] == "Apollo"
    assert data["astrometric_data_quality_tier"] == "FOLLOWUP_PRIORITY_LIMITED_ARC"


def test_sbdb_acceptance_object_1998_ff14(mock_client: TestClient):
    """Test 23 — Acceptance object 1998 FF14 (2523934): verify UNRESOLVED, asteroid_key=null, data=null."""
    resp = mock_client.get("/asteroids/2523934/sbdb")
    assert resp.status_code == 200
    body = resp.json()

    assert body["resolution"]["match_state"] == "UNRESOLVED"
    assert body["resolution"]["asteroid_key"] is None
    assert body["data"] is None


def test_sbdb_nonexistent_does_not_call_resolution_or_sbdb_profile(mock_client: TestClient):
    """Test 24 — Nonexistent object does not call resolution or SBDB lookup after existence check fails."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state"
    ) as mock_res, patch(
        "dashboard_data.DashboardDataProvider.get_sbdb_profile"
    ) as mock_sbdb:
        resp = mock_client.get("/asteroids/99999999/sbdb")
        assert resp.status_code == 404
        mock_res.assert_not_called()
        mock_sbdb.assert_not_called()


# ============================================================================
# 10. SLICE 4: GET /asteroids/{neows_id}/sentry (SENTRY PROFILE)
# ============================================================================


def test_sentry_resolved_success(mock_client: TestClient):
    """Test 1 — Resolved object returns HTTP 200 with populated profile."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200

    body = resp.json()
    validated = SentryResponse.model_validate(body)
    assert validated.data is not None
    assert isinstance(validated.data, SentryProfile)
    assert validated.data.sentry_id == "bK10T54W"
    assert validated.data.asteroid_key == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert validated.data.has_sentry_monitoring is True
    assert validated.data.is_sentry_ambiguous is False
    assert validated.data.sentry_identifier_count == 1
    assert validated.data.designation == "2010 TW54"
    assert validated.data.fullname == "(2010 TW54)"


def test_sentry_resolved_resolution_block(mock_client: TestClient):
    """Test 2 — Resolved object contains authoritative resolution block."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200
    res = resp.json()["resolution"]
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert res["match_rule"] == "EXACT_DESIGNATION_MATCH"
    assert res["evidence"] is not None
    assert res["resolved_at"] is not None


def test_sentry_resolved_provider_backed(mock_client: TestClient):
    """Test 3 — Resolved object returns provider-backed Sentry profile."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["latest_potential_impacts_count"] == 16
    assert abs(data["latest_impact_probability"] - 6.594578e-05) < 1e-8
    assert data["latest_palermo_scale_cum"] == -5.76
    assert data["latest_palermo_scale_max"] == -6.12
    assert data["latest_torino_scale_max"] == 0
    assert abs(data["v_infinity_km_s"] - 7.7620952) < 1e-4
    assert data["impact_year_range"] == "2088-2122"
    assert data["last_obs_date"] == "2010-10-16"
    assert data["total_snapshots_observed"] == 1


def test_sentry_identity_from_provider(mock_client: TestClient):
    """Test 4 — Sentry identity comes from provider/bridge, not derived from NeoWs ID."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["sentry_id"] != "3548666"
    assert data["sentry_id"] == "bK10T54W"


def test_sentry_unresolved_returns_200(mock_client: TestClient):
    """Test 5 — Unresolved object returns HTTP 200."""
    resp = mock_client.get("/asteroids/2523934/sentry")
    assert resp.status_code == 200
    validated = SentryResponse.model_validate(resp.json())
    assert validated.resolution.match_state == "UNRESOLVED"


def test_sentry_unresolved_data_null(mock_client: TestClient):
    """Test 6 — Unresolved object returns data=null, not an empty object."""
    resp = mock_client.get("/asteroids/2523934/sentry")
    assert resp.status_code == 200
    assert resp.json()["data"] is None


def test_sentry_unresolved_does_not_fabricate_key(mock_client: TestClient):
    """Test 7 — Unresolved object does not fabricate asteroid_key."""
    resp = mock_client.get("/asteroids/2523934/sentry")
    assert resp.status_code == 200
    assert resp.json()["resolution"]["asteroid_key"] is None


def test_sentry_unresolved_does_not_call_provider(mock_client: TestClient):
    """Test 8 — Unresolved object does not call get_sentry_profile()."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile"
    ) as mock_sentry:
        resp = mock_client.get("/asteroids/2523934/sentry")
        assert resp.status_code == 200
        mock_sentry.assert_not_called()


def test_sentry_resolved_no_profile_returns_200(mock_client: TestClient):
    """Test 9 — Resolved object with no Sentry profile returns HTTP 200."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=None,
    ):
        resp = mock_client.get("/asteroids/3548666/sentry")
        assert resp.status_code == 200


def test_sentry_resolved_no_profile_remains_resolved(mock_client: TestClient):
    """Test 10 — Resolved object with no Sentry profile preserves resolution state and asteroid_key."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=None,
    ):
        resp = mock_client.get("/asteroids/3548666/sentry")
        assert resp.status_code == 200
        body = resp.json()
        validated = SentryResponse.model_validate(body)
        assert validated.resolution.match_state == "RESOLVED"
        assert validated.resolution.asteroid_key == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"


def test_sentry_resolved_no_profile_data_null(mock_client: TestClient):
    """Test 11 — Resolved object with no Sentry profile returns data=null."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=None,
    ):
        resp = mock_client.get("/asteroids/3548666/sentry")
        assert resp.status_code == 200
        assert resp.json()["data"] is None


def test_sentry_ambiguous_linkage_returns_200(mock_client: TestClient):
    """Test 12 — Ambiguous Sentry linkage returns HTTP 200."""
    ambiguous_profile = {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "has_sentry_monitoring": True,
        "is_sentry_ambiguous": True,
        "sentry_identifier_count": 2,
        "sentry_id": None,
        "designation": None,
        "fullname": None,
        "latest_impact_probability": None,
        "latest_palermo_scale_max": None,
        "latest_palermo_scale_cum": None,
        "latest_torino_scale_max": None,
        "latest_potential_impacts_count": None,
        "v_infinity_km_s": None,
        "impact_year_range": None,
        "last_obs_date": None,
        "latest_snapshot_key": None,
        "total_snapshots_observed": 0,
        "all_time_max_impact_probability": None,
        "all_time_max_palermo_scale_max": None,
        "all_time_max_torino_scale_max": None,
        "is_currently_active": False,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=ambiguous_profile,
    ):
        resp = mock_client.get("/asteroids/3548666/sentry")
        assert resp.status_code == 200
        validated = SentryResponse.model_validate(resp.json())
        assert validated.data is not None
        assert validated.data.is_sentry_ambiguous is True


def test_sentry_ambiguous_linkage_no_candidate_chosen(mock_client: TestClient):
    """Test 13 — Ambiguous Sentry linkage does not select arbitrary candidate."""
    ambiguous_profile = {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "has_sentry_monitoring": True,
        "is_sentry_ambiguous": True,
        "sentry_identifier_count": 2,
        "sentry_id": None,
        "designation": None,
        "fullname": None,
        "latest_impact_probability": None,
        "latest_palermo_scale_max": None,
        "latest_palermo_scale_cum": None,
        "latest_torino_scale_max": None,
        "latest_potential_impacts_count": None,
        "v_infinity_km_s": None,
        "impact_year_range": None,
        "last_obs_date": None,
        "latest_snapshot_key": None,
        "total_snapshots_observed": 0,
        "all_time_max_impact_probability": None,
        "all_time_max_palermo_scale_max": None,
        "all_time_max_torino_scale_max": None,
        "is_currently_active": False,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=ambiguous_profile,
    ):
        resp = mock_client.get("/asteroids/3548666/sentry")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["sentry_id"] is None
        assert data["is_sentry_ambiguous"] is True
        assert data["sentry_identifier_count"] == 2


def test_sentry_ambiguous_linkage_no_scalar_metrics(mock_client: TestClient):
    """Test 14 — Ambiguous Sentry linkage suppresses all scalar metrics."""
    ambiguous_profile = {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "has_sentry_monitoring": True,
        "is_sentry_ambiguous": True,
        "sentry_identifier_count": 2,
        "sentry_id": None,
        "designation": None,
        "fullname": None,
        "latest_impact_probability": None,
        "latest_palermo_scale_max": None,
        "latest_palermo_scale_cum": None,
        "latest_torino_scale_max": None,
        "latest_potential_impacts_count": None,
        "v_infinity_km_s": None,
        "impact_year_range": None,
        "last_obs_date": None,
        "latest_snapshot_key": None,
        "total_snapshots_observed": 0,
        "all_time_max_impact_probability": None,
        "all_time_max_palermo_scale_max": None,
        "all_time_max_torino_scale_max": None,
        "is_currently_active": False,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=ambiguous_profile,
    ):
        resp = mock_client.get("/asteroids/3548666/sentry")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data["latest_impact_probability"] is None
        assert data["latest_palermo_scale_max"] is None
        assert data["latest_palermo_scale_cum"] is None
        assert data["latest_torino_scale_max"] is None
        assert data["latest_potential_impacts_count"] is None
        assert data["v_infinity_km_s"] is None


def test_sentry_nonexistent_numeric_id_returns_404(mock_client: TestClient):
    """Test 15 — Nonexistent numeric NeoWs ID returns HTTP 404 with TARGET_NOT_FOUND."""
    resp = mock_client.get("/asteroids/99999999/sentry")
    assert resp.status_code == 404
    body = resp.json()
    validated = ErrorResponse.model_validate(body)
    assert validated.error.code == "TARGET_NOT_FOUND"
    assert "99999999" in validated.error.message


def test_sentry_nonexistent_does_not_call_resolution(mock_client: TestClient):
    """Test 16 — Nonexistent object does not call resolution lookup after existence check fails."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state"
    ) as mock_res:
        resp = mock_client.get("/asteroids/99999999/sentry")
        assert resp.status_code == 404
        mock_res.assert_not_called()


def test_sentry_nonexistent_does_not_call_provider(mock_client: TestClient):
    """Test 17 — Nonexistent object does not call Sentry provider after existence check fails."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile"
    ) as mock_sentry:
        resp = mock_client.get("/asteroids/99999999/sentry")
        assert resp.status_code == 404
        mock_sentry.assert_not_called()


def test_sentry_invalid_alphabetic_id_returns_422(mock_client: TestClient):
    """Test 18 — Invalid alphabetic ID /asteroids/abc/sentry returns HTTP 422."""
    resp = mock_client.get("/asteroids/abc/sentry")
    assert resp.status_code == 422


def test_sentry_invalid_negative_id_returns_422(mock_client: TestClient):
    """Test 19 — Invalid negative ID /asteroids/-1/sentry returns HTTP 422."""
    resp = mock_client.get("/asteroids/-1/sentry")
    assert resp.status_code == 422


def test_sentry_invalid_decimal_id_returns_422(mock_client: TestClient):
    """Test 20 — Invalid decimal ID /asteroids/1.5/sentry returns HTTP 422."""
    resp = mock_client.get("/asteroids/1.5/sentry")
    assert resp.status_code == 422


def test_sentry_invalid_alphanumeric_id_returns_422(mock_client: TestClient):
    """Test 21 — Invalid alphanumeric ID /asteroids/3548666a/sentry returns HTTP 422."""
    resp = mock_client.get("/asteroids/3548666a/sentry")
    assert resp.status_code == 422


def test_sentry_no_pagination(mock_client: TestClient):
    """Test 22 — Response has no pagination field."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200
    assert "pagination" not in resp.json()


def test_sentry_no_history_payload(mock_client: TestClient):
    """Test 23 — Sentry endpoint does not expose history or trajectory array."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200
    body = resp.json()
    assert "history" not in body
    if body["data"] is not None:
        forbidden = {"history", "historical_risk", "trajectory", "snapshots"}
        assert not forbidden.intersection(body["data"].keys())


def test_sentry_no_crosswalk_payload(mock_client: TestClient):
    """Test 24 — Sentry endpoint does not expose crosswalk payload."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200
    body = resp.json()
    assert "crosswalk" not in body
    if body["data"] is not None:
        assert "crosswalk" not in body["data"]


def test_sentry_null_fields_remain_json_null(mock_client: TestClient):
    """Test 25 — Null provider fields remain JSON null."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data is not None
    assert data["asteroid_key"] is not None


def test_sentry_api_version(mock_client: TestClient):
    """Test 26 — Metadata confirms API version is 1.0.0."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert meta["api_version"] == "1.0.0"


def test_sentry_execution_mode(mock_client: TestClient):
    """Test 27 — Execution mode comes from provider."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert meta["execution_mode"] == "LOCAL (DUCKDB / PARQUET LAKEHOUSE)"
    dt = datetime.fromisoformat(meta["timestamp"])
    assert dt.tzinfo is not None


def test_sentry_zero_external_network_calls(mock_client: TestClient):
    """Test 28 — Zero external network calls during Sentry profile lookup."""
    with patch("urllib.request.urlopen") as mock_urlopen, patch(
        "requests.get"
    ) as mock_req_get, patch("boto3.client") as mock_boto:

        resp_resolved = mock_client.get("/asteroids/3548666/sentry")
        assert resp_resolved.status_code == 200

        resp_unresolved = mock_client.get("/asteroids/2523934/sentry")
        assert resp_unresolved.status_code == 200

        mock_urlopen.assert_not_called()
        mock_req_get.assert_not_called()
        mock_boto.assert_not_called()


def test_sentry_acceptance_object_2010_tw54(mock_client: TestClient):
    """Test 29 — Acceptance object 2010 TW54 (3548666): verify full authoritative Sentry profile."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200
    body = resp.json()

    assert body["resolution"]["match_state"] == "RESOLVED"
    assert body["resolution"]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"

    data = body["data"]
    assert data is not None
    assert data["sentry_id"] == "bK10T54W"
    assert data["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert data["has_sentry_monitoring"] is True
    assert data["is_sentry_ambiguous"] is False
    assert data["sentry_identifier_count"] == 1
    assert data["latest_potential_impacts_count"] == 16


def test_sentry_acceptance_object_1998_ff14(mock_client: TestClient):
    """Test 30 — Acceptance object 1998 FF14 (2523934): verify UNRESOLVED, asteroid_key=null, data=null."""
    resp = mock_client.get("/asteroids/2523934/sentry")
    assert resp.status_code == 200
    body = resp.json()

    assert body["resolution"]["match_state"] == "UNRESOLVED"
    assert body["resolution"]["asteroid_key"] is None
    assert body["data"] is None


def test_sentry_not_safe_semantic_rule(mock_client: TestClient):
    """Test 31 — Semantic rule: unmonitored does NOT equal safe; no synthetic risk scores."""
    resp = mock_client.get("/asteroids/3548666/sentry")
    assert resp.status_code == 200
    data = resp.json()["data"]

    forbidden_concepts = {"is_safe", "safe", "danger_score", "threat_score", "risk_score", "safety_score"}
    assert not forbidden_concepts.intersection(data.keys())


def test_sentry_nullable_profile_fields_remain_json_null(mock_client: TestClient):
    """Test 32 — Nullable Sentry profile fields serialize as genuine JSON null rather than 0/0.0/false/empty."""
    fixture_profile = {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "has_sentry_monitoring": True,
        "is_sentry_ambiguous": False,
        "sentry_identifier_count": 1,
        "sentry_id": "bK10T54W",
        "designation": "2010 TW54",
        "fullname": "(2010 TW54)",
        "latest_impact_probability": 0.0001,
        "latest_palermo_scale_max": None,
        "latest_palermo_scale_cum": None,
        "latest_torino_scale_max": None,
        "latest_potential_impacts_count": None,
        "v_infinity_km_s": None,
        "impact_year_range": None,
        "last_obs_date": None,
        "latest_snapshot_key": "2026-09-26",
        "total_snapshots_observed": 1,
        "all_time_max_impact_probability": 0.0001,
        "all_time_max_palermo_scale_max": None,
        "all_time_max_torino_scale_max": None,
        "is_currently_active": True,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=fixture_profile,
    ):
        resp = mock_client.get("/asteroids/3548666/sentry")
        assert resp.status_code == 200
        data = resp.json()["data"]
        assert data is not None

        # Verify specifically that nullable fields are genuine JSON null, not coerced to 0, 0.0, False, or ""
        nullable_fields = [
            "latest_palermo_scale_max",
            "latest_palermo_scale_cum",
            "latest_torino_scale_max",
            "latest_potential_impacts_count",
            "v_infinity_km_s",
            "impact_year_range",
            "last_obs_date",
            "all_time_max_palermo_scale_max",
            "all_time_max_torino_scale_max",
        ]
        for field in nullable_fields:
            assert field in data
            assert data[field] is None
            assert not isinstance(data[field], (int, float, bool, str))


# ==============================================================================
# M6.4 SLICE 5 — HISTORY ENDPOINT (GET /asteroids/{neows_id}/history)
# ==============================================================================


def test_history_resolved_success(mock_client: TestClient):
    """Test 1 — Resolved history success: verify HTTP 200 with populated trajectory array."""
    resp = mock_client.get("/asteroids/3548666/history")
    assert resp.status_code == 200
    validated = SentryHistoryResponse.model_validate(resp.json())
    assert validated.data is not None
    assert len(validated.data) >= 1
    assert validated.resolution.match_state == "RESOLVED"
    assert validated.resolution.asteroid_key == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"


def test_history_response_schema(mock_client: TestClient):
    """Test 2 — Response schema: top-level keys strictly meta, data, resolution."""
    resp = mock_client.get("/asteroids/3548666/history")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"meta", "data", "resolution"}
    assert "api_version" in body["meta"]
    assert "execution_mode" in body["meta"]
    assert "timestamp" in body["meta"]


def test_history_authoritative_resolution_envelope(mock_client: TestClient):
    """Test 3 — Authoritative resolution envelope: preserves match state and evidence."""
    resp = mock_client.get("/asteroids/3548666/history")
    assert resp.status_code == 200
    res = resp.json()["resolution"]
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert res["match_rule"] == "EXACT_DESIGNATION_MATCH"
    assert res["evidence"] is not None
    assert res["resolved_at"] is not None
    assert res["is_sentry_ambiguous"] is False


def test_history_provider_backed_historical_fields(mock_client: TestClient):
    """Test 4 — Provider-backed historical fields: verify 16 factual fields match lakehouse."""
    resp = mock_client.get("/asteroids/3548666/history")
    assert resp.status_code == 200
    records = resp.json()["data"]
    assert len(records) >= 1
    rec = records[0]

    expected_fields = {
        "snapshot_key",
        "snapshot_time",
        "sentry_id",
        "designation",
        "impact_probability",
        "palermo_scale_max",
        "palermo_scale_cum",
        "torino_scale_max",
        "potential_impacts_count",
        "v_infinity_km_s",
        "estimated_diameter_km",
        "absolute_magnitude",
        "impact_year_range",
        "last_obs_date",
        "is_impact_probability_changed",
        "is_palermo_scale_max_changed",
    }
    assert set(rec.keys()) == expected_fields
    validated_rec = SentryHistoryRecord.model_validate(rec)
    assert validated_rec.sentry_id == "bK10T54W"
    assert rec["sentry_id"] == "bK10T54W"
    assert rec["designation"] == "2010 TW54"
    assert rec["snapshot_key"] == "2026-09-26"
    assert rec["potential_impacts_count"] == 16
    assert isinstance(rec["impact_probability"], float)
    assert isinstance(rec["is_impact_probability_changed"], bool)
    assert isinstance(rec["is_palermo_scale_max_changed"], bool)


def test_history_sentry_identity_delegation(mock_client: TestClient):
    """Test 5 — Sentry identity delegation: calls provider.get_historical_risk with canonical sentry_id."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_historical_risk",
        wraps=DashboardDataProvider().get_historical_risk,
    ) as mock_hist:
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        mock_hist.assert_called_once_with("bK10T54W")


def test_history_array_shape(mock_client: TestClient):
    """Test 6 — History array shape: multi-snapshot trajectory returned in sequence."""
    import pandas as pd
    fake_history = pd.DataFrame([
        {
            "snapshot_key": "2026-09-20",
            "snapshot_time": "2026-09-20T12:00:00+00:00",
            "sentry_id": "bK10T54W",
            "designation": "2010 TW54",
            "impact_probability": 1e-5,
            "palermo_scale_max": -6.5,
            "palermo_scale_cum": -6.0,
            "torino_scale_max": 0,
            "potential_impacts_count": 10,
            "v_infinity_km_s": 7.7,
            "estimated_diameter_km": 0.01,
            "absolute_magnitude": 27.5,
            "impact_year_range": "2088-2122",
            "last_obs_date": "2010-10-15",
            "is_impact_probability_changed": False,
            "is_palermo_scale_max_changed": False,
        },
        {
            "snapshot_key": "2026-09-26",
            "snapshot_time": "2026-09-26T20:15:16+00:00",
            "sentry_id": "bK10T54W",
            "designation": "2010 TW54",
            "impact_probability": 6.59e-5,
            "palermo_scale_max": -6.12,
            "palermo_scale_cum": -5.76,
            "torino_scale_max": 0,
            "potential_impacts_count": 16,
            "v_infinity_km_s": 7.76,
            "estimated_diameter_km": 0.01,
            "absolute_magnitude": 27.55,
            "impact_year_range": "2088-2122",
            "last_obs_date": "2010-10-16",
            "is_impact_probability_changed": True,
            "is_palermo_scale_max_changed": True,
        },
    ])
    with patch(
        "dashboard_data.DashboardDataProvider.get_historical_risk",
        return_value=fake_history,
    ):
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        records = resp.json()["data"]
        assert len(records) == 2
        assert records[0]["snapshot_key"] == "2026-09-20"
        assert records[1]["snapshot_key"] == "2026-09-26"
        assert records[1]["is_impact_probability_changed"] is True


def test_history_zero_snapshots_returns_empty_array(mock_client: TestClient):
    """Test 7 — Zero-history returns empty collection [] when entity is resolved with valid Sentry ID."""
    import pandas as pd
    empty_df = pd.DataFrame(columns=[
        "snapshot_key", "snapshot_time", "sentry_id", "designation",
        "impact_probability", "palermo_scale_max", "palermo_scale_cum",
        "torino_scale_max", "potential_impacts_count", "v_infinity_km_s",
        "estimated_diameter_km", "absolute_magnitude", "impact_year_range",
        "last_obs_date", "is_impact_probability_changed", "is_palermo_scale_max_changed",
    ])
    with patch(
        "dashboard_data.DashboardDataProvider.get_historical_risk",
        return_value=empty_df,
    ):
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert isinstance(body["data"], list)
        assert body["resolution"]["match_state"] == "RESOLVED"
        assert body["resolution"]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"


def test_history_unmonitored_returns_data_null(mock_client: TestClient):
    """Test 8 — Unmonitored object returns data=null (not empty array [])."""
    unmonitored_profile = {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "has_sentry_monitoring": False,
        "is_sentry_ambiguous": False,
        "sentry_identifier_count": 0,
        "sentry_id": None,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=unmonitored_profile,
    ), patch(
        "dashboard_data.DashboardDataProvider.get_historical_risk"
    ) as mock_hist:
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] is None
        assert body["resolution"]["match_state"] == "RESOLVED"
        mock_hist.assert_not_called()


def test_history_unresolved_returns_200(mock_client: TestClient):
    """Test 9 — Unresolved object (1998 FF14) returns HTTP 200 OK."""
    resp = mock_client.get("/asteroids/2523934/history")
    assert resp.status_code == 200


def test_history_unresolved_returns_data_null(mock_client: TestClient):
    """Test 10 — Unresolved object returns data=null."""
    resp = mock_client.get("/asteroids/2523934/history")
    assert resp.status_code == 200
    assert resp.json()["data"] is None


def test_history_unresolved_does_not_fabricate_key(mock_client: TestClient):
    """Test 11 — Unresolved object does not fabricate asteroid_key."""
    resp = mock_client.get("/asteroids/2523934/history")
    assert resp.status_code == 200
    res = resp.json()["resolution"]
    assert res["match_state"] == "UNRESOLVED"
    assert res["asteroid_key"] is None


def test_history_unresolved_does_not_call_historical_provider(mock_client: TestClient):
    """Test 12 — Unresolved object does not call get_historical_risk or get_sentry_profile."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_historical_risk"
    ) as mock_hist, patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile"
    ) as mock_sentry:
        resp = mock_client.get("/asteroids/2523934/history")
        assert resp.status_code == 200
        mock_hist.assert_not_called()
        mock_sentry.assert_not_called()


def test_history_ambiguous_sentry_linkage_returns_200(mock_client: TestClient):
    """Test 13 — Ambiguous Sentry linkage returns HTTP 200 OK."""
    ambiguous_profile = {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "has_sentry_monitoring": True,
        "is_sentry_ambiguous": True,
        "sentry_identifier_count": 2,
        "sentry_id": None,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=ambiguous_profile,
    ):
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200


def test_history_ambiguous_returns_data_null(mock_client: TestClient):
    """Test 14 — Ambiguous Sentry linkage returns data=null (refuses candidate selection)."""
    ambiguous_profile = {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "has_sentry_monitoring": True,
        "is_sentry_ambiguous": True,
        "sentry_identifier_count": 2,
        "sentry_id": None,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=ambiguous_profile,
    ):
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        assert resp.json()["data"] is None


def test_history_ambiguous_does_not_select_arbitrary_sentry_id(mock_client: TestClient):
    """Test 15 — Ambiguous Sentry linkage does not call get_historical_risk for any candidate."""
    ambiguous_profile = {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "has_sentry_monitoring": True,
        "is_sentry_ambiguous": True,
        "sentry_identifier_count": 2,
        "sentry_id": None,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=ambiguous_profile,
    ), patch(
        "dashboard_data.DashboardDataProvider.get_historical_risk"
    ) as mock_hist:
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        mock_hist.assert_not_called()


def test_history_ambiguous_warning_and_state_preserved(mock_client: TestClient):
    """Test 16 — Ambiguous Sentry linkage preserves is_sentry_ambiguous=true and warning note."""
    ambiguous_profile = {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "has_sentry_monitoring": True,
        "is_sentry_ambiguous": True,
        "sentry_identifier_count": 2,
        "sentry_id": None,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=ambiguous_profile,
    ):
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        res = resp.json()["resolution"]
        assert res["is_sentry_ambiguous"] is True
        assert res["warning"] is not None
        assert "ambiguous" in res["warning"].lower()
        assert res["match_state"] == "RESOLVED"
        assert res["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"


def test_history_resolved_but_unavailable_returns_200(mock_client: TestClient):
    """Test 17 — Resolved but Sentry profile/history unavailable returns HTTP 200 OK."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=None,
    ):
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        assert resp.json()["data"] is None


def test_history_resolved_but_unavailable_remains_resolved(mock_client: TestClient):
    """Test 18 — Resolved but history unavailable retains match_state=RESOLVED with authoritative key."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=None,
    ):
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        res = resp.json()["resolution"]
        assert res["match_state"] == "RESOLVED"
        assert res["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"


def test_history_nonexistent_numeric_id_returns_404(mock_client: TestClient):
    """Test 19 — Syntactically valid but nonexistent numeric NeoWs ID returns HTTP 404."""
    resp = mock_client.get("/asteroids/99999999/history")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "TARGET_NOT_FOUND"
    assert "99999999" in body["error"]["message"]


def test_history_nonexistent_short_circuits_resolution(mock_client: TestClient):
    """Test 20 — Nonexistent object does not call resolution lookup after existence check fails."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state"
    ) as mock_res:
        resp = mock_client.get("/asteroids/99999999/history")
        assert resp.status_code == 404
        mock_res.assert_not_called()


def test_history_nonexistent_short_circuits_history_provider(mock_client: TestClient):
    """Test 21 — Nonexistent object does not call historical provider after existence check fails."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_historical_risk"
    ) as mock_hist:
        resp = mock_client.get("/asteroids/99999999/history")
        assert resp.status_code == 404
        mock_hist.assert_not_called()


def test_history_invalid_alphabetic_path_returns_422(mock_client: TestClient):
    """Test 22 — Path validation: alphabetic path /asteroids/abc/history returns 422."""
    resp = mock_client.get("/asteroids/abc/history")
    assert resp.status_code == 422


def test_history_invalid_negative_path_returns_422(mock_client: TestClient):
    """Test 23 — Path validation: negative path /asteroids/-1/history returns 422."""
    resp = mock_client.get("/asteroids/-1/history")
    assert resp.status_code == 422


def test_history_invalid_decimal_path_returns_422(mock_client: TestClient):
    """Test 24 — Path validation: decimal path /asteroids/1.5/history returns 422."""
    resp = mock_client.get("/asteroids/1.5/history")
    assert resp.status_code == 422


def test_history_invalid_alphanumeric_path_returns_422(mock_client: TestClient):
    """Test 25 — Path validation: alphanumeric path /asteroids/3548666a/history returns 422."""
    resp = mock_client.get("/asteroids/3548666a/history")
    assert resp.status_code == 422


def test_history_zero_id_returns_422(mock_client: TestClient):
    """Test 25b — Path validation: non-positive zero ID /asteroids/0/history returns 422."""
    resp = mock_client.get("/asteroids/0/history")
    assert resp.status_code == 422


def test_history_no_crosswalk_payload(mock_client: TestClient):
    """Test 26 — No data leakage: crosswalk payloads are absent from response."""
    resp = mock_client.get("/asteroids/3548666/history")
    assert resp.status_code == 200
    body = resp.json()
    assert "crosswalk" not in body
    if body["data"]:
        for rec in body["data"]:
            assert "crosswalk" not in rec
            assert "source_system" not in rec
            assert "identifier_name" not in rec
            assert "identifier_value" not in rec


def test_history_no_sbdb_payload(mock_client: TestClient):
    """Test 27 — No data leakage: SBDB characterization fields are absent from response."""
    resp = mock_client.get("/asteroids/3548666/history")
    assert resp.status_code == 200
    body = resp.json()
    assert "sbdb" not in body
    if body["data"]:
        for rec in body["data"]:
            assert "orbit_id" not in rec
            assert "epoch_jd" not in rec
            assert "sbdb_spkid" not in rec
            assert "albedo" not in rec
            assert "semi_major_axis_au" not in rec


def test_history_no_synthetic_risk_threat_safety_score(mock_client: TestClient):
    """Test 28 — Strict analytical purity: no synthetic danger/threat/safety scores anywhere in payload."""
    resp = mock_client.get("/asteroids/3548666/history")
    assert resp.status_code == 200
    body = resp.json()

    forbidden_scores = [
        "danger_score",
        "threat_score",
        "safety_score",
        "is_safe",
        "composite_risk",
        "hazard_level",
    ]
    for key in forbidden_scores:
        assert key not in body
        assert key not in body["resolution"]
        if body["data"]:
            for rec in body["data"]:
                assert key not in rec


def test_history_nullable_historical_fields_remain_json_null(mock_client: TestClient):
    """Test 29 — Nullable historical fields remain genuine JSON null (never 0, 0.0, false, "")."""
    import pandas as pd
    fixture_df = pd.DataFrame([
        {
            "snapshot_key": "2026-09-26",
            "snapshot_time": "2026-09-26T20:15:16+00:00",
            "sentry_id": "bK10T54W",
            "designation": "2010 TW54",
            "impact_probability": 0.0001,
            "palermo_scale_max": None,
            "palermo_scale_cum": None,
            "torino_scale_max": None,
            "potential_impacts_count": None,
            "v_infinity_km_s": None,
            "estimated_diameter_km": None,
            "absolute_magnitude": None,
            "impact_year_range": None,
            "last_obs_date": None,
            "is_impact_probability_changed": False,
            "is_palermo_scale_max_changed": False,
        }
    ])
    with patch(
        "dashboard_data.DashboardDataProvider.get_historical_risk",
        return_value=fixture_df,
    ):
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        records = resp.json()["data"]
        assert len(records) == 1
        rec = records[0]

        nullable_fields = [
            "palermo_scale_max",
            "palermo_scale_cum",
            "torino_scale_max",
            "potential_impacts_count",
            "v_infinity_km_s",
            "estimated_diameter_km",
            "absolute_magnitude",
            "impact_year_range",
            "last_obs_date",
        ]
        for field in nullable_fields:
            assert field in rec
            assert rec[field] is None
            assert not isinstance(rec[field], (int, float, bool, str))


def test_history_api_version_and_execution_mode_metadata(mock_client: TestClient):
    """Test 30 — Response meta contains 1.0.0 api_version, valid UTC timestamp, and provider execution mode."""
    resp = mock_client.get("/asteroids/3548666/history")
    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert meta["api_version"] == "1.0.0"
    assert meta["execution_mode"] == "LOCAL (DUCKDB / PARQUET LAKEHOUSE)"
    parsed_time = datetime.fromisoformat(meta["timestamp"])
    assert parsed_time.tzinfo is not None


def test_history_acceptance_object_2010_tw54(mock_client: TestClient):
    """Test 31 — Acceptance object 2010 TW54 (3548666): complete resolved historical contract."""
    resp = mock_client.get("/asteroids/3548666/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"]["match_state"] == "RESOLVED"
    assert body["resolution"]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert body["data"] is not None
    assert len(body["data"]) >= 1
    assert body["data"][0]["sentry_id"] == "bK10T54W"
    assert body["data"][0]["designation"] == "2010 TW54"


def test_history_acceptance_object_1998_ff14(mock_client: TestClient):
    """Test 32 — Acceptance object 1998 FF14 (2523934): unresolved, asteroid_key=null, data=null."""
    resp = mock_client.get("/asteroids/2523934/history")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"]["match_state"] == "UNRESOLVED"
    assert body["resolution"]["asteroid_key"] is None
    assert body["data"] is None


def test_history_zero_external_network_calls(mock_client: TestClient):
    """Test 33 — Offline contract: zero outbound external network calls during endpoint execution."""
    with patch("urllib.request.urlopen") as mock_urlopen, patch(
        "requests.get"
    ) as mock_req_get, patch("boto3.client") as mock_boto:
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        mock_urlopen.assert_not_called()
        mock_req_get.assert_not_called()
        mock_boto.assert_not_called()


def test_history_no_pagination_parameters(mock_client: TestClient):
    """Test 34 — Endpoint does not require pagination and exposes raw trajectory array."""
    resp = mock_client.get("/asteroids/3548666/history?limit=10&offset=5")
    assert resp.status_code == 200
    body = resp.json()
    assert "pagination" not in body


def test_history_unmonitored_not_interpreted_as_safe(mock_client: TestClient):
    """Test 35 — Semantic safeguard: unmonitored object is not marked safe or given synthetic score."""
    unmonitored_profile = {
        "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        "has_sentry_monitoring": False,
        "is_sentry_ambiguous": False,
        "sentry_identifier_count": 0,
        "sentry_id": None,
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_sentry_profile",
        return_value=unmonitored_profile,
    ):
        resp = mock_client.get("/asteroids/3548666/history")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] is None
        assert "is_safe" not in body["resolution"]
        assert "is_safe" not in body


def test_history_non_causal_flags_preserved(mock_client: TestClient):
    """Test 36 — Factual integrity: non-causal flags are strictly boolean."""
    resp = mock_client.get("/asteroids/3548666/history")
    assert resp.status_code == 200
    records = resp.json()["data"]
    for rec in records:
        assert isinstance(rec["is_impact_probability_changed"], bool)
        assert isinstance(rec["is_palermo_scale_max_changed"], bool)


def test_history_does_not_pollute_shared_resolution_envelope(mock_client: TestClient):
    """Test 37 — Envelope isolation: verify previous endpoints retain exact pristine 5-field ResolutionEnvelope."""
    expected_pure_keys = {"match_state", "asteroid_key", "match_rule", "evidence", "resolved_at"}

    # 1. Detail endpoint
    r_detail = mock_client.get("/asteroids/3548666")
    assert r_detail.status_code == 200
    res_detail = r_detail.json()["resolution"]
    assert set(res_detail.keys()) == expected_pure_keys
    assert "is_sentry_ambiguous" not in res_detail
    assert "warning" not in res_detail
    assert "notes" not in res_detail

    # 2. SBDB endpoint
    r_sbdb = mock_client.get("/asteroids/3548666/sbdb")
    assert r_sbdb.status_code == 200
    res_sbdb = r_sbdb.json()["resolution"]
    assert set(res_sbdb.keys()) == expected_pure_keys
    assert "is_sentry_ambiguous" not in res_sbdb
    assert "warning" not in res_sbdb
    assert "notes" not in res_sbdb

    # 3. Sentry endpoint
    r_sentry = mock_client.get("/asteroids/3548666/sentry")
    assert r_sentry.status_code == 200
    res_sentry = r_sentry.json()["resolution"]
    assert set(res_sentry.keys()) == expected_pure_keys
    assert "is_sentry_ambiguous" not in res_sentry
    assert "warning" not in res_sentry
    assert "notes" not in res_sentry

    # 4. History endpoint has isolated fields in HistoryResolutionEnvelope
    r_hist = mock_client.get("/asteroids/3548666/history")
    assert r_hist.status_code == 200
    res_hist = r_hist.json()["resolution"]
    assert "is_sentry_ambiguous" in res_hist
    assert "warning" in res_hist
    assert "notes" in res_hist


# ============================================================================
# 7. M6.4 SLICE 6 — CROSSWALK ENDPOINT (/asteroids/{neows_id}/crosswalk)
# ============================================================================


def test_crosswalk_resolved_success(mock_client: TestClient):
    """Test 1 — Resolved crosswalk returns HTTP 200 with non-empty list of crosswalk records."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["data"], list)
    assert len(body["data"]) > 0
    assert body["resolution"]["match_state"] == "RESOLVED"
    assert body["resolution"]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"


def test_crosswalk_response_envelope_schema(mock_client: TestClient):
    """Test 2 — Response adheres strictly to CrosswalkResponse schema (meta, data, resolution)."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    body = resp.json()
    validated = CrosswalkResponse(**body)
    assert validated.resolution.match_state == "RESOLVED"
    assert set(body.keys()) == {"meta", "data", "resolution"}


def test_crosswalk_authoritative_resolution_envelope(mock_client: TestClient):
    """Test 3 — Standard pristine 5-field ResolutionEnvelope without history-specific pollution."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    res = resp.json()["resolution"]
    expected_keys = {"match_state", "asteroid_key", "match_rule", "evidence", "resolved_at"}
    assert set(res.keys()) == expected_keys
    assert "is_sentry_ambiguous" not in res
    assert "warning" not in res
    assert "notes" not in res


def test_crosswalk_provider_backed_records(mock_client: TestClient):
    """Test 4 — Provider method get_crosswalk provides records matching fixture bridge."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    records = resp.json()["data"]
    assert len(records) == 5
    primary_pivots = [r for r in records if r["is_primary_pivot"] is True]
    assert len(primary_pivots) == 1
    assert primary_pivots[0]["source_system"] == "sbdb"
    assert primary_pivots[0]["identifier_name"] == "spkid"
    assert primary_pivots[0]["identifier_value"] == "50548689"


def test_crosswalk_grain_preservation(mock_client: TestClient):
    """Test 5 — Crosswalk grain (asteroid_key, source_system, identifier_name, identifier_value) preserved."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    records = resp.json()["data"]
    # 2010 TW54 has 5 records in fixture
    assert len(records) == 5
    for rec in records:
        assert rec["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
        assert rec["source_system"] in ("neows", "sbdb", "sentry")
        assert rec["identifier_name"] in ("id", "des", "spkid", "sentry_id")
        assert rec["identifier_value"] is not None


def test_crosswalk_multiple_source_systems_preserved(mock_client: TestClient):
    """Test 6 — Multiple source systems (neows, sbdb, sentry) are preserved without collapsing."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    records = resp.json()["data"]
    systems = {rec["source_system"] for rec in records}
    assert systems == {"neows", "sbdb", "sentry"}


def test_crosswalk_multiple_identifiers_preserved(mock_client: TestClient):
    """Test 7 — Multiple identifiers for same source system (des and spkid for sbdb) are preserved."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    records = resp.json()["data"]
    sbdb_ids = {rec["identifier_name"] for rec in records if rec["source_system"] == "sbdb"}
    assert sbdb_ids == {"des", "spkid"}
    sentry_ids = {rec["identifier_name"] for rec in records if rec["source_system"] == "sentry"}
    assert sentry_ids == {"des", "sentry_id"}


def test_crosswalk_resolved_empty_crosswalk_returns_empty_list(mock_client: TestClient):
    """Test 8 — Resolved asteroid with zero crosswalk records returns HTTP 200 and data: []."""
    import pandas as pd
    empty_df = pd.DataFrame(columns=[
        "asteroid_key", "source_system", "identifier_name", "identifier_value",
        "is_primary_pivot", "created_at", "updated_at"
    ])
    with patch(
        "dashboard_data.DashboardDataProvider.get_crosswalk",
        return_value=empty_df,
    ):
        resp = mock_client.get("/asteroids/3548666/crosswalk")
        assert resp.status_code == 200
        body = resp.json()
        assert body["data"] == []
        assert body["resolution"]["match_state"] == "RESOLVED"
        assert body["resolution"]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"


def test_crosswalk_unresolved_returns_200(mock_client: TestClient):
    """Test 9 — Unresolved object (1998 FF14) returns HTTP 200 (not 404)."""
    resp = mock_client.get("/asteroids/2523934/crosswalk")
    assert resp.status_code == 200


def test_crosswalk_unresolved_returns_empty_list(mock_client: TestClient):
    """Test 10 — Unresolved object returns data: [] (not null)."""
    resp = mock_client.get("/asteroids/2523934/crosswalk")
    assert resp.status_code == 200
    assert resp.json()["data"] == []


def test_crosswalk_unresolved_asteroid_key_remains_null(mock_client: TestClient):
    """Test 11 — Unresolved object has resolution.asteroid_key == null."""
    resp = mock_client.get("/asteroids/2523934/crosswalk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"]["match_state"] == "UNRESOLVED"
    assert body["resolution"]["asteroid_key"] is None


def test_crosswalk_unresolved_does_not_call_provider_crosswalk(mock_client: TestClient):
    """Test 12 — Unresolved object never calls provider.get_crosswalk()."""
    with patch("dashboard_data.DashboardDataProvider.get_crosswalk") as mock_gw:
        resp = mock_client.get("/asteroids/2523934/crosswalk")
        assert resp.status_code == 200
        mock_gw.assert_not_called()


def test_crosswalk_ambiguous_returns_200(mock_client: TestClient):
    """Test 13 — Ambiguous resolution returns HTTP 200."""
    ambiguous_state = {
        "neows_id": "3548666",
        "match_state": "AMBIGUOUS",
        "asteroid_key": None,
        "match_rule": "AMBIGUOUS_MULTI_SPKID",
        "evidence": "Identifier maps to multiple candidate entity keys: [ast_1, ast_2].",
        "resolved_at": "2026-09-28T01:15:26.842071+00:00",
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state",
        return_value=ambiguous_state,
    ):
        resp = mock_client.get("/asteroids/3548666/crosswalk")
        assert resp.status_code == 200


def test_crosswalk_ambiguous_returns_empty_list(mock_client: TestClient):
    """Test 14 — Ambiguous resolution returns data: [] (not null)."""
    ambiguous_state = {
        "neows_id": "3548666",
        "match_state": "AMBIGUOUS",
        "asteroid_key": None,
        "match_rule": "AMBIGUOUS_MULTI_SPKID",
        "evidence": "Identifier maps to multiple candidate entity keys: [ast_1, ast_2].",
        "resolved_at": "2026-09-28T01:15:26.842071+00:00",
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state",
        return_value=ambiguous_state,
    ):
        resp = mock_client.get("/asteroids/3548666/crosswalk")
        assert resp.status_code == 200
        assert resp.json()["data"] == []


def test_crosswalk_ambiguous_does_not_select_arbitrary_asteroid_key(mock_client: TestClient):
    """Test 15 — Ambiguous resolution keeps asteroid_key = null (never selects a candidate)."""
    ambiguous_state = {
        "neows_id": "3548666",
        "match_state": "AMBIGUOUS",
        "asteroid_key": None,
        "match_rule": "AMBIGUOUS_MULTI_SPKID",
        "evidence": "Identifier maps to multiple candidate entity keys: [ast_1, ast_2].",
        "resolved_at": "2026-09-28T01:15:26.842071+00:00",
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state",
        return_value=ambiguous_state,
    ):
        resp = mock_client.get("/asteroids/3548666/crosswalk")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resolution"]["match_state"] == "AMBIGUOUS"
        assert body["resolution"]["asteroid_key"] is None


def test_crosswalk_ambiguous_does_not_call_crosswalk_provider(mock_client: TestClient):
    """Test 16 — Ambiguous resolution never calls provider.get_crosswalk()."""
    ambiguous_state = {
        "neows_id": "3548666",
        "match_state": "AMBIGUOUS",
        "asteroid_key": None,
        "match_rule": "AMBIGUOUS_MULTI_SPKID",
        "evidence": "Identifier maps to multiple candidate entity keys: [ast_1, ast_2].",
        "resolved_at": "2026-09-28T01:15:26.842071+00:00",
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state",
        return_value=ambiguous_state,
    ), patch("dashboard_data.DashboardDataProvider.get_crosswalk") as mock_gw:
        resp = mock_client.get("/asteroids/3548666/crosswalk")
        assert resp.status_code == 200
        mock_gw.assert_not_called()


def test_crosswalk_ambiguous_evidence_preserved(mock_client: TestClient):
    """Test 17 — Ambiguous resolution preserves authoritative ambiguity evidence in resolution envelope."""
    evidence_payload = "Identifier maps to multiple candidate entity keys: [ast_alpha, ast_beta]."
    ambiguous_state = {
        "neows_id": "3548666",
        "match_state": "AMBIGUOUS",
        "asteroid_key": None,
        "match_rule": "AMBIGUOUS_MULTI_SPKID",
        "evidence": evidence_payload,
        "resolved_at": "2026-09-28T01:15:26.842071+00:00",
    }
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state",
        return_value=ambiguous_state,
    ):
        resp = mock_client.get("/asteroids/3548666/crosswalk")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resolution"]["evidence"] == evidence_payload


def test_crosswalk_resolved_but_empty_remains_resolved(mock_client: TestClient):
    """Test 18 — Resolved object with zero records retains match_state = RESOLVED and asteroid_key."""
    import pandas as pd
    empty_df = pd.DataFrame(columns=[
        "asteroid_key", "source_system", "identifier_name", "identifier_value"
    ])
    with patch(
        "dashboard_data.DashboardDataProvider.get_crosswalk",
        return_value=empty_df,
    ):
        resp = mock_client.get("/asteroids/3548666/crosswalk")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resolution"]["match_state"] == "RESOLVED"
        assert body["resolution"]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
        assert body["data"] == []


def test_crosswalk_resolved_but_unavailable_remains_resolved(mock_client: TestClient):
    """Test 19 — Provider unavailable preserves match_state = RESOLVED and authoritative key with data: []."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_crosswalk",
        return_value=None,
    ):
        resp = mock_client.get("/asteroids/3548666/crosswalk")
        assert resp.status_code == 200
        body = resp.json()
        assert body["resolution"]["match_state"] == "RESOLVED"
        assert body["resolution"]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
        assert body["data"] == []


def test_crosswalk_nonexistent_numeric_id_returns_404(mock_client: TestClient):
    """Test 20 — Syntactically valid positive numeric ID that does not exist returns HTTP 404 TARGET_NOT_FOUND."""
    resp = mock_client.get("/asteroids/99999999/crosswalk")
    assert resp.status_code == 404
    body = resp.json()
    assert body["error"]["code"] == "TARGET_NOT_FOUND"


def test_crosswalk_nonexistent_short_circuits_resolution(mock_client: TestClient):
    """Test 21 — Nonexistent asteroid short-circuits resolution lookup."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_resolution_state"
    ) as mock_res:
        resp = mock_client.get("/asteroids/99999999/crosswalk")
        assert resp.status_code == 404
        mock_res.assert_not_called()


def test_crosswalk_nonexistent_short_circuits_crosswalk_provider(mock_client: TestClient):
    """Test 22 — Nonexistent asteroid short-circuits crosswalk provider lookup."""
    with patch(
        "dashboard_data.DashboardDataProvider.get_crosswalk"
    ) as mock_gw:
        resp = mock_client.get("/asteroids/99999999/crosswalk")
        assert resp.status_code == 404
        mock_gw.assert_not_called()


def test_crosswalk_alphabetic_path_returns_422(mock_client: TestClient):
    """Test 23 — Alphabetic ID returns HTTP 422 Unprocessable Entity."""
    resp = mock_client.get("/asteroids/abc/crosswalk")
    assert resp.status_code == 422


def test_crosswalk_zero_id_returns_422(mock_client: TestClient):
    """Test 24 — Zero ID '0' returns HTTP 422."""
    resp = mock_client.get("/asteroids/0/crosswalk")
    assert resp.status_code == 422


def test_crosswalk_negative_id_returns_422(mock_client: TestClient):
    """Test 25 — Negative ID '-1' returns HTTP 422."""
    resp = mock_client.get("/asteroids/-1/crosswalk")
    assert resp.status_code == 422


def test_crosswalk_decimal_id_returns_422(mock_client: TestClient):
    """Test 26 — Decimal ID '1.5' returns HTTP 422."""
    resp = mock_client.get("/asteroids/1.5/crosswalk")
    assert resp.status_code == 422


def test_crosswalk_alphanumeric_id_returns_422(mock_client: TestClient):
    """Test 27 — Alphanumeric ID '3548666a' returns HTTP 422."""
    resp = mock_client.get("/asteroids/3548666a/crosswalk")
    assert resp.status_code == 422


def test_crosswalk_leading_zero_id_returns_422(mock_client: TestClient):
    """Test 28 — Leading zero ID '00' returns HTTP 422."""
    resp = mock_client.get("/asteroids/00/crosswalk")
    assert resp.status_code == 422


def test_crosswalk_no_sbdb_payload_leakage(mock_client: TestClient):
    """Test 29 — No SBDB characterization or orbit solution fields leak into crosswalk response."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    body = resp.json()
    sbdb_fields = ["orbit_id", "epoch_jd", "semi_major_axis_au", "eccentricity", "albedo"]
    for field in sbdb_fields:
        assert field not in body
        assert field not in body["resolution"]
        for rec in body["data"]:
            assert field not in rec


def test_crosswalk_no_sentry_payload_leakage(mock_client: TestClient):
    """Test 30 — No Sentry profile fields leak into crosswalk response."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    body = resp.json()
    sentry_fields = ["palermo_scale_max", "torino_scale_max", "potential_impacts_count", "has_sentry_monitoring"]
    for field in sentry_fields:
        assert field not in body
        assert field not in body["resolution"]
        for rec in body["data"]:
            assert field not in rec


def test_crosswalk_no_history_payload_leakage(mock_client: TestClient):
    """Test 31 — No historical risk trajectory fields leak into crosswalk response."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    body = resp.json()
    hist_fields = ["impact_probability_delta", "palermo_scale_delta", "is_palermo_scale_max_changed", "snapshot_time"]
    for field in hist_fields:
        assert field not in body
        assert field not in body["resolution"]
        for rec in body["data"]:
            assert field not in rec


def test_crosswalk_no_synthetic_identity_mapping(mock_client: TestClient):
    """Test 32 — Identity integrity: no synthetic IDs, composite keys, or assumed spkid=neows_id."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    body = resp.json()
    records = body["data"]
    for rec in records:
        assert "composite_id" not in rec
        assert "unified_id" not in rec
        if rec["source_system"] == "sbdb" and rec["identifier_name"] == "spkid":
            # 50548689 != 3548666
            assert rec["identifier_value"] != "3548666"


def test_crosswalk_2010_tw54_acceptance_object(mock_client: TestClient):
    """Test 33 — Acceptance object 2010 TW54 (3548666): HTTP 200, RESOLVED, authoritative key, populated crosswalk."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"]["match_state"] == "RESOLVED"
    assert body["resolution"]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert isinstance(body["data"], list)
    assert len(body["data"]) == 5
    systems = {r["source_system"] for r in body["data"]}
    assert systems == {"neows", "sbdb", "sentry"}


def test_crosswalk_1998_ff14_acceptance_object(mock_client: TestClient):
    """Test 34 — Acceptance object 1998 FF14 (2523934): HTTP 200, UNRESOLVED, asteroid_key=null, data=[]."""
    resp = mock_client.get("/asteroids/2523934/crosswalk")
    assert resp.status_code == 200
    body = resp.json()
    assert body["resolution"]["match_state"] == "UNRESOLVED"
    assert body["resolution"]["asteroid_key"] is None
    assert body["data"] == []


def test_crosswalk_nullable_fields_remain_json_null_where_applicable(mock_client: TestClient):
    """Test 35 — Nullable fields serialize as genuine JSON null (never 0, 0.0, false, "")."""
    import pandas as pd
    fixture_df = pd.DataFrame([
        {
            "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
            "source_system": "custom",
            "identifier_name": "tag",
            "identifier_value": "test_tag",
            "is_primary_pivot": None,
            "created_at": None,
            "updated_at": None,
        }
    ])
    with patch(
        "dashboard_data.DashboardDataProvider.get_crosswalk",
        return_value=fixture_df,
    ):
        resp = mock_client.get("/asteroids/3548666/crosswalk")
        assert resp.status_code == 200
        rec = resp.json()["data"][0]
        assert rec["is_primary_pivot"] is None
        assert rec["created_at"] is None
        assert rec["updated_at"] is None
        assert not isinstance(rec["is_primary_pivot"], (int, float, bool, str))


def test_crosswalk_api_version_and_execution_metadata(mock_client: TestClient):
    """Test 36 — Response meta contains 1.0.0 api_version, valid UTC timestamp, and provider execution mode."""
    resp = mock_client.get("/asteroids/3548666/crosswalk")
    assert resp.status_code == 200
    meta = resp.json()["meta"]
    assert meta["api_version"] == "1.0.0"
    assert meta["execution_mode"] == "LOCAL (DUCKDB / PARQUET LAKEHOUSE)"
    parsed_time = datetime.fromisoformat(meta["timestamp"])
    assert parsed_time.tzinfo is not None


def test_crosswalk_zero_external_network_calls(mock_client: TestClient):
    """Test 37 — Offline contract: zero outbound external network calls during endpoint execution."""
    with patch("urllib.request.urlopen") as mock_urlopen, patch(
        "requests.get"
    ) as mock_req_get, patch("boto3.client") as mock_boto:
        resp = mock_client.get("/asteroids/3548666/crosswalk")
        assert resp.status_code == 200
        mock_urlopen.assert_not_called()
        mock_req_get.assert_not_called()
        mock_boto.assert_not_called()


def test_crosswalk_no_pagination_parameters(mock_client: TestClient):
    """Test 38 — Endpoint does not require pagination and exposes raw crosswalk array."""
    resp = mock_client.get("/asteroids/3548666/crosswalk?limit=10&offset=5")
    assert resp.status_code == 200
    body = resp.json()
    assert "pagination" not in body


def test_crosswalk_extra_fields_forbidden():
    """Test 39 — Strict schema: extra fields are forbidden by Pydantic models."""
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        CrosswalkRecord.model_validate({
            "asteroid_key": "ast_1",
            "source_system": "neows",
            "identifier_name": "id",
            "identifier_value": "123",
            "extra_field": "disallowed",
        })


def test_crosswalk_envelope_isolation_across_all_endpoints(mock_client: TestClient):
    """Test 40 — Envelope purity: Crosswalk shares standard 5-field envelope without leaking History fields."""
    expected_pure_keys = {"match_state", "asteroid_key", "match_rule", "evidence", "resolved_at"}

    # 1. Detail endpoint
    r_detail = mock_client.get("/asteroids/3548666")
    assert r_detail.status_code == 200
    assert set(r_detail.json()["resolution"].keys()) == expected_pure_keys

    # 2. SBDB endpoint
    r_sbdb = mock_client.get("/asteroids/3548666/sbdb")
    assert r_sbdb.status_code == 200
    assert set(r_sbdb.json()["resolution"].keys()) == expected_pure_keys

    # 3. Sentry endpoint
    r_sentry = mock_client.get("/asteroids/3548666/sentry")
    assert r_sentry.status_code == 200
    assert set(r_sentry.json()["resolution"].keys()) == expected_pure_keys

    # 4. History endpoint has isolated fields
    r_hist = mock_client.get("/asteroids/3548666/history")
    assert r_hist.status_code == 200
    assert "is_sentry_ambiguous" in r_hist.json()["resolution"]

    # 5. Crosswalk endpoint uses pure 5-field envelope
    r_cw = mock_client.get("/asteroids/3548666/crosswalk")
    assert r_cw.status_code == 200
    res_cw = r_cw.json()["resolution"]
    assert set(res_cw.keys()) == expected_pure_keys
    assert "is_sentry_ambiguous" not in res_cw
    assert "warning" not in res_cw
    assert "notes" not in res_cw


# ============================================================================
# PHASE 1 STEP 2 — ROUTE PATTERNS & /asteroids/world ROUTE ORDERING
# ============================================================================

_NEOWS_ID_ROUTES = ["/asteroids/{}", "/asteroids/{}/sbdb", "/asteroids/{}/sentry",
                    "/asteroids/{}/history", "/asteroids/{}/crosswalk"]


@pytest.mark.parametrize("route", _NEOWS_ID_ROUTES)
@pytest.mark.parametrize("bad_id", ["0", "007", "abc", "-1", "1.5"])
def test_neows_id_routes_share_positive_integer_pattern(mock_client: TestClient, route: str, bad_id: str):
    """Every NeoWs ID route rejects zero, leading zeros, and non-integers identically."""
    resp = mock_client.get(route.format(bad_id))
    assert resp.status_code == 422


@pytest.mark.parametrize("route", _NEOWS_ID_ROUTES)
def test_neows_id_routes_accept_positive_integer(mock_client: TestClient, route: str):
    """A well-formed but unknown positive ID passes validation and reaches the handler (404)."""
    resp = mock_client.get(route.format("99999999"))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "TARGET_NOT_FOUND"


def test_world_route_not_captured_by_neows_id_route(mock_client: TestClient):
    """Regression: /asteroids/world must hit the world route, not 422 from /asteroids/{neows_id}."""
    resp = mock_client.get("/asteroids/world")
    assert resp.status_code != 422
    assert resp.status_code == 501
    validated = ErrorResponse.model_validate(resp.json())
    assert validated.error.code == "NOT_IMPLEMENTED"


def test_world_route_registered_before_neows_id_route():
    """Starlette matches in registration order; the static world path must come first."""
    paths = [getattr(r, "path", None) for r in create_app().routes]
    assert paths.index("/asteroids/world") < paths.index("/asteroids/{neows_id}")


# ============================================================================
# PHASE 1 STEP 3 — SBDB ORBIT SERVING: SOURCE FIELDS, COHERENT SNAPSHOT, TRI-STATE
# ============================================================================

_TW54_SPKID = "50548689"
_TW54_SBDB_URL = "/asteroids/3548666/sbdb"

# Served field -> SBDB element_name, per the actual fact_sbdb_orbit_element schema.
_SBDB_ELEMENT_FIELDS = {
    "eccentricity": "e",
    "semi_major_axis_au": "a",
    "perihelion_distance_au": "q",
    "aphelion_distance_au": "ad",
    "inclination_deg": "i",
    "ascending_node_longitude_deg": "om",
    "argument_of_perihelion_deg": "w",
    "mean_anomaly_deg": "ma",
    "mean_motion_deg_per_day": "n",
    "orbital_period_days": "per",
    "time_of_perihelion_jd_tdb": "tp",
}

_REAL_LAKEHOUSE = Path(__file__).resolve().parent


def _fixture_element(spkid: str, name: str) -> float:
    return next(
        r["element_value"] for r in _FIXTURE_SBDB_ELEM
        if r["spkid"] == spkid and r["element_name"] == name
    )


def _sbdb_snapshot_rows(snapshot_key: str, run_id: str, snapshot_time: str, scale: float,
                        element_names: list[str] | None = None,
                        is_neo: bool | None = True, is_pha: bool | None = False,
                        include_physical: bool = True) -> dict[str, list[dict]]:
    """Clone the 2010 TW54 fixture rows into a new snapshot with scaled, distinguishable values."""
    stamp = {"snapshot_key": snapshot_key, "run_id": run_id, "snapshot_time": snapshot_time}
    obj = [{**r, **stamp, "is_neo": is_neo, "is_pha": is_pha}
           for r in _FIXTURE_SBDB_OBJ if r["spkid"] == _TW54_SPKID]
    orb = [{**r, **stamp, "epoch_jd": r["epoch_jd"] + scale}
           for r in _FIXTURE_SBDB_ORB if r["spkid"] == _TW54_SPKID]
    elem = [{**r, **stamp, "epoch_jd": r["epoch_jd"] + scale, "element_value": r["element_value"] * scale}
            for r in _FIXTURE_SBDB_ELEM
            if r["spkid"] == _TW54_SPKID and (element_names is None or r["element_name"] in element_names)]
    phys = [{**r, **stamp, "param_value_numeric": r["param_value_numeric"] * scale}
            for r in _FIXTURE_SBDB_PHYS if r["spkid"] == _TW54_SPKID] if include_physical else []
    return {"obj": obj, "orb": orb, "elem": elem, "phys": phys}


def _write_sbdb_tables(lakehouse: Path, *snapshots: dict[str, list[dict]]) -> None:
    """Overwrite the four SBDB tables with the base fixtures plus extra snapshots."""
    tables = {
        "obj": ("fact_sbdb_object_snapshot.parquet", _FIXTURE_SBDB_OBJ, SBDB_OBJECT_SCHEMA),
        "orb": ("fact_sbdb_orbit.parquet", _FIXTURE_SBDB_ORB, SBDB_ORBIT_SCHEMA),
        "elem": ("fact_sbdb_orbit_element.parquet", _FIXTURE_SBDB_ELEM, SBDB_ORBIT_ELEMENT_SCHEMA),
        "phys": ("fact_sbdb_physical_parameter.parquet", _FIXTURE_SBDB_PHYS, SBDB_PHYS_PAR_SCHEMA),
    }
    for key, (filename, base_rows, schema) in tables.items():
        rows = list(base_rows) + [r for snap in snapshots for r in snap[key]]
        pq.write_table(pa.Table.from_pylist(rows, schema=schema), lakehouse / filename)


def _client_for(lakehouse: Path) -> TestClient:
    return TestClient(create_app(provider=DashboardDataProvider(base_dir=lakehouse, execution_mode="LOCAL")))


@pytest.mark.parametrize("field,element_name", sorted(_SBDB_ELEMENT_FIELDS.items()))
def test_sbdb_serves_every_source_orbital_element(mock_client: TestClient, field: str, element_name: str):
    """Each served orbital field is the exact SBDB element value for the selected snapshot."""
    data = mock_client.get(_TW54_SBDB_URL).json()["data"]
    assert data[field] == pytest.approx(_fixture_element(_TW54_SPKID, element_name), rel=1e-12)


def test_sbdb_serves_epoch_equinox_and_snapshot_provenance(mock_client: TestClient):
    data = mock_client.get(_TW54_SBDB_URL).json()["data"]
    assert data["epoch_jd"] == 2461200.5
    assert data["equinox"] == "J2000"
    assert data["snapshot_key"] == "2026-09-26"
    assert data["run_id"] == "26b0c1ef0bba"
    assert data["snapshot_time"] == "2026-09-26T20:27:39.451638+00:00"


def test_sbdb_orbital_period_comes_from_source_per(mock_client: TestClient):
    """orbital_period_days is SBDB 'per'; the legacy year field is only its unit conversion, not a**1.5."""
    data = mock_client.get(_TW54_SBDB_URL).json()["data"]
    per_days = _fixture_element(_TW54_SPKID, "per")
    assert data["orbital_period_days"] == per_days
    assert data["orbital_period_yr"] == pytest.approx(per_days / 365.25, rel=1e-12)
    a_au = _fixture_element(_TW54_SPKID, "a")
    assert data["orbital_period_yr"] != pytest.approx(a_au ** 1.5, rel=1e-9)


def test_sbdb_orbital_period_yr_is_null_without_source_period(mock_lakehouse: Path):
    """No source 'per' -> no derived year value (no fallback to Kepler's a**1.5)."""
    newer = _sbdb_snapshot_rows("2026-09-30", "run_noper", "2026-09-30T00:00:00+00:00", 1.0,
                                element_names=["a", "e", "q", "i"])
    _write_sbdb_tables(mock_lakehouse, newer)
    data = _client_for(mock_lakehouse).get(_TW54_SBDB_URL).json()["data"]
    assert data["semi_major_axis_au"] is not None
    assert data["orbital_period_days"] is None
    assert data["orbital_period_yr"] is None


def test_sbdb_orbital_period_yr_marked_deprecated_in_openapi(mock_client: TestClient):
    props = mock_client.get("/openapi.json").json()["components"]["schemas"]["SbdbProfile"]["properties"]
    assert props["orbital_period_yr"].get("deprecated") is True
    assert "deprecated" not in props["orbital_period_days"]


def test_sbdb_profile_uses_single_latest_snapshot(mock_lakehouse: Path):
    """With older and newer snapshots present, every field comes from the newest one only."""
    older = _sbdb_snapshot_rows("2026-09-01", "run_older", "2026-09-01T00:00:00+00:00", 0.5)
    newer = _sbdb_snapshot_rows("2026-09-30", "run_newer", "2026-09-30T00:00:00+00:00", 2.0)
    _write_sbdb_tables(mock_lakehouse, older, newer)

    data = _client_for(mock_lakehouse).get(_TW54_SBDB_URL).json()["data"]
    assert data["snapshot_key"] == "2026-09-30"
    assert data["run_id"] == "run_newer"
    for field, element_name in _SBDB_ELEMENT_FIELDS.items():
        assert data[field] == pytest.approx(_fixture_element(_TW54_SPKID, element_name) * 2.0, rel=1e-12), field
    assert data["epoch_jd"] == 2461200.5 + 2.0
    assert data["absolute_magnitude"] == pytest.approx(27.6 * 2.0)


def test_sbdb_profile_does_not_backfill_from_older_snapshot(mock_lakehouse: Path):
    """A newer snapshot missing some elements/physical params yields nulls, never older-snapshot values.

    Under the previous MAX()-across-snapshots logic, the missing fields silently
    came from the older snapshot, producing a profile no single SBDB response ever contained.
    """
    newer = _sbdb_snapshot_rows("2026-09-30", "run_partial", "2026-09-30T00:00:00+00:00", 2.0,
                                element_names=["a", "e"], include_physical=False)
    _write_sbdb_tables(mock_lakehouse, newer)

    data = _client_for(mock_lakehouse).get(_TW54_SBDB_URL).json()["data"]
    assert data["run_id"] == "run_partial"
    assert data["semi_major_axis_au"] == pytest.approx(_fixture_element(_TW54_SPKID, "a") * 2.0)
    assert data["eccentricity"] == pytest.approx(_fixture_element(_TW54_SPKID, "e") * 2.0)
    for field in ("inclination_deg", "ascending_node_longitude_deg", "argument_of_perihelion_deg",
                  "mean_anomaly_deg", "orbital_period_days", "aphelion_distance_au"):
        assert data[field] is None, field
    assert data["absolute_magnitude"] is None


def test_sbdb_profile_same_day_runs_select_latest_snapshot_time(mock_lakehouse: Path):
    """Two runs on the same snapshot_key: the later snapshot_time wins, regardless of run_id order."""
    early = _sbdb_snapshot_rows("2026-09-30", "run_b_early", "2026-09-30T01:00:00+00:00", 3.0)
    late = _sbdb_snapshot_rows("2026-09-30", "run_a_late", "2026-09-30T02:00:00+00:00", 4.0)
    _write_sbdb_tables(mock_lakehouse, early, late)

    data = _client_for(mock_lakehouse).get(_TW54_SBDB_URL).json()["data"]
    assert data["run_id"] == "run_a_late"
    assert data["mean_anomaly_deg"] == pytest.approx(_fixture_element(_TW54_SPKID, "ma") * 4.0)


@pytest.mark.parametrize("source_value", [True, False, None])
def test_sbdb_neo_pha_flags_preserve_tri_state(mock_lakehouse: Path, source_value: bool | None):
    """Explicit True/False pass through; unknown (null in source) is served as null, never false."""
    newer = _sbdb_snapshot_rows("2026-09-30", "run_flags", "2026-09-30T00:00:00+00:00", 1.0,
                                is_neo=source_value, is_pha=source_value)
    _write_sbdb_tables(mock_lakehouse, newer)

    data = _client_for(mock_lakehouse).get(_TW54_SBDB_URL).json()["data"]
    assert data["is_neo"] is source_value
    assert data["is_pha"] is source_value


def test_sbdb_flags_null_when_object_row_missing_for_snapshot(mock_lakehouse: Path):
    """Orbit present but no object row in the selected snapshot: flags are unknown (null), not false."""
    newer = _sbdb_snapshot_rows("2026-09-30", "run_no_obj", "2026-09-30T00:00:00+00:00", 1.0)
    newer["obj"] = []
    _write_sbdb_tables(mock_lakehouse, newer)

    data = _client_for(mock_lakehouse).get(_TW54_SBDB_URL).json()["data"]
    assert data["run_id"] == "run_no_obj"
    assert data["is_neo"] is None
    assert data["is_pha"] is None
    assert data["semi_major_axis_au"] is not None


@pytest.mark.skipif(
    not (_REAL_LAKEHOUSE / "fact_sbdb_orbit_element.parquet").exists(),
    reason="Local Parquet lakehouse not present (gitignored; absent in CI).",
)
def test_sbdb_real_local_lakehouse_serves_source_orbit():
    """Against the actual local Parquet data: served elements equal the stored SBDB rows."""
    import duckdb

    data = _client_for(_REAL_LAKEHOUSE).get("/asteroids/3427460/sbdb").json()["data"]
    assert data is not None and data["spkid"] == "50427483"
    elem_path = str(_REAL_LAKEHOUSE / "fact_sbdb_orbit_element.parquet").replace("\\", "/")
    rows = dict(duckdb.connect().execute(
        f"SELECT element_name, element_value FROM '{elem_path}' WHERE spkid = ? AND run_id = ?",
        ["50427483", data["run_id"]],
    ).fetchall())
    for field, element_name in _SBDB_ELEMENT_FIELDS.items():
        assert data[field] == rows[element_name], field
    assert data["equinox"] == "J2000"


_MISSING_FLAG = object()


@pytest.mark.parametrize(
    "raw,expected",
    [(True, True), (False, False), (_MISSING_FLAG, None)],
    ids=["explicit_true", "explicit_false", "missing"],
)
def test_sbdb_flag_tri_state_survives_ingestion_to_api(mock_lakehouse: Path, raw, expected):
    """End to end: SBDB payload -> nasa_sbdb extraction -> Parquet -> provider -> API keeps all three states."""
    import nasa_sbdb

    payload_obj = {
        "spkid": _TW54_SPKID, "des": "2010 TW54", "fullname": "(2010 TW54)", "kind": "au",
        "orbit_id": "14", "orbit_class": {"code": "APO", "name": "Apollo"},
    }
    if raw is not _MISSING_FLAG:
        payload_obj["neo"] = raw
        payload_obj["pha"] = raw
    snap = ("2026-09-30", "run_ingested", "2026-09-30T00:00:00+00:00")
    newer = _sbdb_snapshot_rows(*snap, 1.0)
    newer["obj"] = [nasa_sbdb.extract_sbdb_object({"object": payload_obj}, *snap)]
    _write_sbdb_tables(mock_lakehouse, newer)

    data = _client_for(mock_lakehouse).get(_TW54_SBDB_URL).json()["data"]
    assert data["run_id"] == "run_ingested"
    assert data["is_neo"] is expected
    assert data["is_pha"] is expected
