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
import math as _math
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
    WorldResponse,
)
from api.service import REQUIRED_PARQUET_ASSETS
from api.service import ILLUSTRATIVE_DIRECTION_ALGORITHM, illustrative_direction
from dashboard_data import LocalDuckDBDataProvider
from api.schemas import AsteroidProfileResponse
from nasa_asteroids import ASTEROID_SCHEMA as _NEOWS_SCHEMA
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
from test_data_provider import (
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
    """Verify that the module-level app instance can serve /health.

    With a real local lakehouse it serves that; on a fresh checkout it serves an empty
    placeholder lakehouse from outside the repository (see resolve_default_lakehouse).
    """
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "healthy"


def _write_real_lakehouse(root: Path, names: tuple[str, ...]) -> None:
    from api.main import _PLACEHOLDER_SCHEMAS

    for name in names:
        schema = _PLACEHOLDER_SCHEMAS[name]
        pq.write_table(pa.Table.from_arrays([pa.array([], type=f.type) for f in schema], schema=schema), root / name)


def test_default_lakehouse_fresh_checkout_uses_placeholder_outside_the_repo(tmp_path: Path):
    """No required tables at all: serve an empty schema-valid lakehouse from a temp dir, never the root."""
    from api.main import resolve_default_lakehouse

    root = tmp_path / "fresh_clone"
    root.mkdir()
    resolved = resolve_default_lakehouse(root)
    assert list(root.iterdir()) == []  # nothing written into the working tree
    assert resolved != root.resolve() and root.resolve() not in resolved.parents
    assert sorted(p.name for p in resolved.iterdir()) == sorted(REQUIRED_PARQUET_ASSETS)
    for name in REQUIRED_PARQUET_ASSETS:
        assert pq.read_table(resolved / name).num_rows == 0
    client = TestClient(create_app(provider=DashboardDataProvider(base_dir=resolved, execution_mode="LOCAL")))
    assert client.get("/health").status_code == 200
    world = client.get("/asteroids/world")
    assert world.status_code == 200
    assert world.json()["data"] == []


def test_default_lakehouse_real_data_is_served_unchanged(tmp_path: Path):
    """All required tables present: the project root itself is the lakehouse; nothing is added."""
    from api.main import resolve_default_lakehouse

    _write_real_lakehouse(tmp_path, REQUIRED_PARQUET_ASSETS)
    before = sorted(p.name for p in tmp_path.iterdir())
    assert resolve_default_lakehouse(tmp_path) == tmp_path.resolve()
    assert sorted(p.name for p in tmp_path.iterdir()) == before


def test_default_lakehouse_partial_data_is_not_masked(tmp_path: Path):
    """Some required tables present: serve the root as-is (health stays 503); no placeholders are mixed in."""
    from api.main import resolve_default_lakehouse

    _write_real_lakehouse(tmp_path, ("asteroids.parquet",))
    assert resolve_default_lakehouse(tmp_path) == tmp_path.resolve()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["asteroids.parquet"]
    client = TestClient(create_app(provider=DashboardDataProvider(base_dir=tmp_path, execution_mode="LOCAL")))
    assert client.get("/health").status_code == 503


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
                    "/asteroids/{}/history", "/asteroids/{}/crosswalk", "/asteroids/{}/profile"]


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
    assert resp.status_code == 200
    WorldResponse.model_validate(resp.json())


def test_world_route_registered_before_neows_id_route(mock_client: TestClient):
    """The static /asteroids/world path takes precedence over /asteroids/{neows_id}.

    Asserted through observable behaviour (HTTP responses and the OpenAPI document), not by
    inspecting FastAPI/Starlette route objects, whose structure differs between versions.
    """
    # /asteroids/world resolves to the world endpoint: the world envelope, not a NeoWs object.
    world_resp = mock_client.get("/asteroids/world")
    assert world_resp.status_code == 200
    world_body = world_resp.json()
    WorldResponse.model_validate(world_body)
    assert {"meta", "world", "data"} <= world_body.keys()
    assert "resolution" not in world_body  # the single-asteroid envelope would carry one

    # /asteroids/{neows_id} still resolves a real asteroid: the single-object envelope.
    detail_resp = mock_client.get("/asteroids/3548666")
    assert detail_resp.status_code == 200
    detail_body = detail_resp.json()
    AsteroidDetailResponse.model_validate(detail_body)
    assert detail_body["data"]["neows_id"] == "3548666"
    assert "world" not in detail_body  # ... and is not the world snapshot

    # The two routes are distinct: "world" is never treated as an ID (422 pattern rejection), and
    # a malformed ID is still rejected by the dynamic route's own validation.
    assert world_resp.status_code != 422
    assert mock_client.get("/asteroids/abc").status_code == 422

    # The documented API lists the static path before the dynamic one.
    paths = list(mock_client.get("/openapi.json").json()["paths"])
    assert paths.index("/asteroids") < paths.index("/asteroids/world") < paths.index("/asteroids/{neows_id}")


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


# ============================================================================
# PHASE 1 STEP 4 — GET /asteroids/world (set-based world snapshot contract)
# ============================================================================



_WORLD_URL = "/asteroids/world"
_TW54_NEOWS = "3548666"
_ST_NEOWS = "3427460"


def _world(client: TestClient) -> dict:
    resp = client.get(_WORLD_URL)
    assert resp.status_code == 200
    body = resp.json()
    WorldResponse.model_validate(body)
    return body


def _by_id(body: dict) -> dict[str, dict]:
    return {rec["neows_id"]: rec for rec in body["data"]}


def _write_table(lakehouse: Path, filename: str, rows: list[dict], schema) -> None:
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), lakehouse / filename)


# --- 1-4: shape, cardinality, uniqueness ------------------------------------

def test_world_returns_one_record_per_neows_object(mock_client: TestClient):
    body = _world(mock_client)
    ids = [rec["neows_id"] for rec in body["data"]]
    assert len(ids) == len(set(ids)), "duplicate NeoWs IDs in world snapshot"
    assert set(ids) == {a["id"] for a in _FIXTURE_ASTEROIDS}
    assert body["world"]["object_count"] == len(ids) == 35


def test_world_collapses_multiple_approaches_to_closest(mock_lakehouse: Path):
    """An object with several approaches appears once, carrying its closest real miss distance."""
    extra = [
        {**_FIXTURE_ASTEROIDS[0], "closest_approach_date": "2026-10-02", "miss_distance_km": 9e9},
        {**_FIXTURE_ASTEROIDS[0], "closest_approach_date": "2026-09-27", "miss_distance_km": 1234.5},
    ]
    _write_table(mock_lakehouse, "asteroids.parquet", _FIXTURE_ASTEROIDS + extra, ASTEROID_SCHEMA)
    body = _world(_client_for(mock_lakehouse))
    assert body["world"]["object_count"] == 35
    rec = _by_id(body)[_FIXTURE_ASTEROIDS[0]["id"]]
    assert rec["encounter"]["miss_distance_km"] == 1234.5
    assert rec["encounter"]["closest_approach_date"] == "2026-09-27"


# --- 5-6, 14: identity and real distance ------------------------------------

def test_world_resolved_objects_carry_asteroid_key(mock_client: TestClient):
    recs = _by_id(_world(mock_client))
    assert recs[_TW54_NEOWS]["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert recs[_ST_NEOWS]["asteroid_key"] == "ast_8520aaac-9c77-5e8f-9a88-b4e501749e26"
    for rec in recs.values():
        assert (rec["asteroid_key"] is not None) == (rec["resolution"]["match_state"] == "RESOLVED")


def test_world_unresolved_objects_remain_present(mock_client: TestClient):
    recs = _world(mock_client)["data"]
    unresolved = [r for r in recs if r["resolution"]["match_state"] == "UNRESOLVED"]
    assert len(unresolved) == 33
    for rec in unresolved:
        assert rec["asteroid_key"] is None
        assert rec["sbdb"] == {"status": "not_resolved", "spkid": None, "snapshot_key": None, "run_id": None}
        assert rec["sentry"]["status"] == "not_resolved"
        assert rec["sentry"]["sentry_id"] is None and rec["sentry"]["in_latest_catalog"] is None


def test_world_preserves_real_neows_miss_distance(mock_client: TestClient):
    source = {a["id"]: a for a in _FIXTURE_ASTEROIDS}
    for rec in _world(mock_client)["data"]:
        src = source[rec["neows_id"]]
        assert rec["name"] == src["name"]
        assert rec["encounter"]["miss_distance_km"] == src["miss_distance_km"]
        assert rec["encounter"]["closest_approach_date"] == src["closest_approach_date"]
        assert rec["encounter"]["is_potentially_hazardous"] is src["hazardous"]
        assert rec["encounter"]["source"] == "nasa_neows"


# --- Parity: world agrees with the per-object endpoints ---------------------

_PARITY_SAMPLE = (_TW54_NEOWS, _ST_NEOWS, "2138971")  # both resolved objects + an unresolved PHA control


def _assert_world_matches_per_object_endpoints(client: TestClient, ids: tuple[str, ...] | None = None) -> None:
    """World records agree with /asteroids/{id}, /sbdb and /sentry (all objects, or only `ids`)."""
    for rec in _world(client)["data"]:
        nid = rec["neows_id"]
        if ids is not None and nid not in ids:
            continue
        detail = client.get(f"/asteroids/{nid}").json()
        assert rec["resolution"]["match_state"] == detail["resolution"]["match_state"], nid
        assert rec["resolution"]["match_rule"] == detail["resolution"]["match_rule"], nid
        assert rec["asteroid_key"] == detail["resolution"]["asteroid_key"], nid

        sbdb = client.get(f"/asteroids/{nid}/sbdb").json()["data"]
        assert (rec["sbdb"]["status"] == "available") == (sbdb is not None), nid
        if sbdb is not None:
            assert (rec["sbdb"]["spkid"], rec["sbdb"]["snapshot_key"], rec["sbdb"]["run_id"]) == (
                sbdb["spkid"], sbdb["snapshot_key"], sbdb["run_id"]), nid

        sentry = client.get(f"/asteroids/{nid}/sentry").json()["data"]
        if sentry is None:
            assert rec["sentry"]["status"] == "not_resolved", nid
        else:
            assert rec["sentry"]["sentry_id"] == sentry["sentry_id"], nid
            assert rec["sentry"]["latest_snapshot_key"] == sentry["latest_snapshot_key"], nid
            assert (rec["sentry"]["status"] == "ambiguous") == sentry["is_sentry_ambiguous"], nid
            assert (rec["sentry"]["status"] == "not_present") == (not sentry["has_sentry_monitoring"]), nid
            if rec["sentry"]["status"] == "available":
                assert rec["sentry"]["in_latest_catalog"] == sentry["is_currently_active"], nid


def test_world_matches_per_object_endpoints(mock_client: TestClient):
    _assert_world_matches_per_object_endpoints(mock_client)


def test_world_resolution_bridge_fallback_matches_detail(mock_lakehouse: Path):
    """Without the audit log, both world and detail fall back to the bridge identically."""
    (mock_lakehouse / "fact_entity_resolution.parquet").unlink()
    client = _client_for(mock_lakehouse)
    recs = _by_id(_world(client))
    assert recs[_TW54_NEOWS]["resolution"]["match_rule"] == "BRIDGE_EXACT_NEOWS_ID"
    assert recs["2138971"]["resolution"]["match_rule"] == "NO_RESOLUTION_RECORD"
    _assert_world_matches_per_object_endpoints(client, _PARITY_SAMPLE)


def test_world_ambiguous_bridge_resolution_matches_detail(mock_lakehouse: Path):
    (mock_lakehouse / "fact_entity_resolution.parquet").unlink()
    extra = {**next(r for r in _FIXTURE_BRIDGE if r["source_system"] == "neows" and r["identifier_value"] == _TW54_NEOWS),
             "asteroid_key": "ast_00000000-0000-5000-8000-000000000000"}
    _write_table(mock_lakehouse, "bridge_asteroid_identifier.parquet", _FIXTURE_BRIDGE + [extra],
                 BRIDGE_ASTEROID_IDENTIFIER_SCHEMA)
    client = _client_for(mock_lakehouse)
    rec = _by_id(_world(client))[_TW54_NEOWS]
    assert rec["resolution"]["match_state"] == "AMBIGUOUS"
    assert rec["asteroid_key"] is None
    assert rec["sbdb"]["status"] == "not_resolved" and rec["sentry"]["status"] == "not_resolved"
    _assert_world_matches_per_object_endpoints(client, _PARITY_SAMPLE)


# --- 7-9: SBDB / Sentry availability and PHA independence -------------------

def test_world_sbdb_availability_and_provenance(mock_client: TestClient):
    recs = _by_id(_world(mock_client))
    assert recs[_TW54_NEOWS]["sbdb"] == {
        "status": "available", "spkid": "50548689", "snapshot_key": "2026-09-26", "run_id": "26b0c1ef0bba"}
    assert recs[_ST_NEOWS]["sbdb"]["run_id"] == "3a217b3b1657"


def test_world_sbdb_uses_same_latest_snapshot_as_profile(mock_lakehouse: Path):
    newer = _sbdb_snapshot_rows("2026-09-30", "run_newer", "2026-09-30T00:00:00+00:00", 2.0)
    _write_sbdb_tables(mock_lakehouse, newer)
    client = _client_for(mock_lakehouse)
    rec = _by_id(_world(client))[_TW54_NEOWS]
    assert (rec["sbdb"]["snapshot_key"], rec["sbdb"]["run_id"]) == ("2026-09-30", "run_newer")
    _assert_world_matches_per_object_endpoints(client, _PARITY_SAMPLE)


def test_world_sbdb_not_present_when_resolved_without_snapshot(mock_lakehouse: Path):
    rows = [r for r in _FIXTURE_SBDB_OBJ if r["spkid"] != _TW54_SPKID]
    _write_table(mock_lakehouse, "fact_sbdb_object_snapshot.parquet", rows, SBDB_OBJECT_SCHEMA)
    rows = [r for r in _FIXTURE_SBDB_ORB if r["spkid"] != _TW54_SPKID]
    _write_table(mock_lakehouse, "fact_sbdb_orbit.parquet", rows, SBDB_ORBIT_SCHEMA)
    client = _client_for(mock_lakehouse)
    rec = _by_id(_world(client))[_TW54_NEOWS]
    assert rec["sbdb"] == {"status": "not_present", "spkid": "50548689", "snapshot_key": None, "run_id": None}
    _assert_world_matches_per_object_endpoints(client, _PARITY_SAMPLE)


def test_world_sentry_availability_and_provenance(mock_client: TestClient):
    body = _world(mock_client)
    recs = _by_id(body)
    assert recs[_TW54_NEOWS]["sentry"] == {
        "status": "available", "sentry_id": "bK10T54W", "latest_snapshot_key": "2026-09-26",
        "run_id": "7d446dc65b5a", "in_latest_catalog": True}
    assert recs[_ST_NEOWS]["sentry"]["sentry_id"] == "bK08S00T"
    assert body["world"]["sentry_latest_catalog_snapshot_key"] == "2026-09-26"


def test_world_sentry_not_present_ambiguous_and_linked_no_record(mock_lakehouse: Path):
    """Crosswalk membership drives every Sentry status; metrics never leak across ambiguous links."""
    bridge = [r for r in _FIXTURE_BRIDGE
              if not (r["source_system"] == "sentry" and r["asteroid_key"].startswith("ast_b8259"))]  # TW54: unlinked
    st_link = next(r for r in _FIXTURE_BRIDGE if r["source_system"] == "sentry"
                   and r["identifier_name"] == "sentry_id" and r["asteroid_key"].startswith("ast_8520"))
    bridge.append({**st_link, "identifier_value": "bKXXXXXX"})  # 2008 ST: two Sentry IDs
    _write_table(mock_lakehouse, "bridge_asteroid_identifier.parquet", bridge, BRIDGE_ASTEROID_IDENTIFIER_SCHEMA)
    client = _client_for(mock_lakehouse)
    recs = _by_id(_world(client))
    assert recs[_TW54_NEOWS]["sentry"]["status"] == "not_present"
    assert recs[_ST_NEOWS]["sentry"] == {"status": "ambiguous", "sentry_id": None, "latest_snapshot_key": None,
                                          "run_id": None, "in_latest_catalog": None}
    _assert_world_matches_per_object_endpoints(client, _PARITY_SAMPLE)

    _write_table(mock_lakehouse, "fact_sentry_risk_snapshot.parquet",
                 [r for r in _FIXTURE_SENTRY if r["sentry_id"] != "bK10T54W"], SENTRY_RISK_SNAPSHOT_SCHEMA)
    _write_table(mock_lakehouse, "bridge_asteroid_identifier.parquet", _FIXTURE_BRIDGE, BRIDGE_ASTEROID_IDENTIFIER_SCHEMA)
    client = _client_for(mock_lakehouse)
    assert _by_id(_world(client))[_TW54_NEOWS]["sentry"] == {
        "status": "linked_no_record", "sentry_id": "bK10T54W", "latest_snapshot_key": None,
        "run_id": None, "in_latest_catalog": None}
    _assert_world_matches_per_object_endpoints(client, _PARITY_SAMPLE)


def test_world_pha_flag_is_independent_of_sentry_availability(mock_lakehouse: Path):
    """Sentry objects that are not PHA keep sentry=available; PHA objects without links do not gain Sentry."""
    rows = [{**a, "hazardous": True} if a["id"] == _TW54_NEOWS else a for a in _FIXTURE_ASTEROIDS]
    _write_table(mock_lakehouse, "asteroids.parquet", rows, ASTEROID_SCHEMA)
    recs = _by_id(_world(_client_for(mock_lakehouse)))

    st = recs[_ST_NEOWS]
    assert st["encounter"]["is_potentially_hazardous"] is False and st["sentry"]["status"] == "available"
    tw54 = recs[_TW54_NEOWS]
    assert tw54["encounter"]["is_potentially_hazardous"] is True and tw54["sentry"]["status"] == "available"
    pha_unlinked = [r for r in recs.values()
                    if r["encounter"]["is_potentially_hazardous"] and r["resolution"]["match_state"] != "RESOLVED"]
    assert pha_unlinked, "fixture must contain PHA objects without Sentry linkage"
    assert all(r["sentry"]["status"] == "not_resolved" for r in pha_unlinked)


# --- 15: unknown stays null --------------------------------------------------

def test_world_unknown_pha_is_null_not_false(mock_lakehouse: Path):
    rows = [{**a, "hazardous": None} if a["id"] == "2138971" else a for a in _FIXTURE_ASTEROIDS]
    _write_table(mock_lakehouse, "asteroids.parquet", rows, ASTEROID_SCHEMA)
    rec = _by_id(_world(_client_for(mock_lakehouse)))["2138971"]
    assert rec["encounter"]["is_potentially_hazardous"] is None


def test_world_neows_run_id_from_dataset_metadata_only(mock_lakehouse: Path):
    """No metadata -> null (never guessed); real run_id metadata written by ingestion -> served."""
    import nasa_asteroids

    assert _world(_client_for(mock_lakehouse))["world"]["neows"]["dataset_run_id"] is None
    nasa_asteroids.save_to_parquet(_FIXTURE_ASTEROIDS, filename=str(mock_lakehouse / "asteroids.parquet"),
                                   run_id="abc123def456")
    assert _world(_client_for(mock_lakehouse))["world"]["neows"]["dataset_run_id"] == "abc123def456"


def test_world_survives_missing_enrichment_assets(mock_lakehouse: Path):
    """With only NeoWs data present, every object is still served, all unresolved, nothing fabricated."""
    for name in ("bridge_asteroid_identifier.parquet", "fact_entity_resolution.parquet",
                 "fact_sentry_risk_snapshot.parquet", "fact_sbdb_object_snapshot.parquet", "fact_sbdb_orbit.parquet"):
        (mock_lakehouse / name).unlink()
    body = _world(_client_for(mock_lakehouse))
    assert body["world"]["object_count"] == 35
    assert body["world"]["sentry_latest_catalog_snapshot_key"] is None
    for rec in body["data"]:
        assert rec["resolution"] == {"match_state": "UNRESOLVED", "match_rule": "NO_RESOLUTION_RECORD", "resolved_at": None}
        assert rec["sbdb"]["status"] == "not_resolved" and rec["sentry"]["status"] == "not_resolved"


def test_world_empty_when_neows_dataset_absent(mock_lakehouse: Path):
    (mock_lakehouse / "asteroids.parquet").unlink()
    body = _world(_client_for(mock_lakehouse))
    assert body["data"] == [] and body["world"]["object_count"] == 0


# --- 10-13: illustrative direction -------------------------------------------

def test_illustrative_direction_is_pinned_and_deterministic():
    """Golden values pin the algorithm: any change must bump ILLUSTRATIVE_DIRECTION_ALGORITHM."""
    assert ILLUSTRATIVE_DIRECTION_ALGORITHM == "sha256-uniform-sphere-v1"
    x, y, z = illustrative_direction(_ST_NEOWS)
    assert (x, y, z) == pytest.approx((0.41789421064936794, 0.4178751627645238, -0.8066875337144268), abs=1e-15)
    assert illustrative_direction(_ST_NEOWS) == illustrative_direction(_ST_NEOWS)


def test_world_direction_stable_across_requests(mock_client: TestClient):
    first = {r["neows_id"]: r["illustrative_direction"] for r in _world(mock_client)["data"]}
    second = {r["neows_id"]: r["illustrative_direction"] for r in _world(mock_client)["data"]}
    assert first == second
    for nid, d in first.items():
        assert (d["x"], d["y"], d["z"]) == illustrative_direction(nid)


def test_world_direction_independent_of_resolution(mock_lakehouse: Path):
    """An object keeps its direction when its identity changes from unresolved to resolved."""
    resolved = _by_id(_world(_client_for(mock_lakehouse)))
    for name in ("bridge_asteroid_identifier.parquet", "fact_entity_resolution.parquet"):
        (mock_lakehouse / name).unlink()
    unresolved = _by_id(_world(_client_for(mock_lakehouse)))
    assert resolved[_TW54_NEOWS]["asteroid_key"] is not None
    assert unresolved[_TW54_NEOWS]["asteroid_key"] is None
    for nid in resolved:
        assert resolved[nid]["illustrative_direction"] == unresolved[nid]["illustrative_direction"]


def test_world_every_object_has_valid_unit_direction(mock_client: TestClient):
    for rec in _world(mock_client)["data"]:
        d = rec["illustrative_direction"]
        assert _math.isclose(d["x"] ** 2 + d["y"] ** 2 + d["z"] ** 2, 1.0, abs_tol=1e-12), rec["neows_id"]


def test_illustrative_direction_distribution_is_spread_over_sphere():
    """Over many IDs, directions are uniform on the sphere: near-zero mean, every octant used, no collisions."""
    ids = [str(3_000_000 + i) for i in range(4000)]
    vecs = [illustrative_direction(i) for i in ids]
    for axis in range(3):
        assert abs(sum(v[axis] for v in vecs) / len(vecs)) < 0.05
    octants = {(v[0] > 0, v[1] > 0, v[2] > 0) for v in vecs}
    assert len(octants) == 8
    upper = sum(v[2] > 0 for v in vecs) / len(vecs)
    assert 0.45 < upper < 0.55
    assert len(set(vecs)) == len(vecs)


def test_world_declares_illustrative_spatial_model(mock_client: TestClient):
    model = _world(mock_client)["world"]["spatial_model"]
    assert model["direction_semantics"] == "illustrative"
    assert model["direction_seed_field"] == "neows_id"
    assert model["distance_field"] == "encounter.miss_distance_km"
    assert model["direction_algorithm"] == ILLUSTRATIVE_DIRECTION_ALGORITHM
    assert "not astronomical" in model["note"]


# --- 16-17: no N+1, set-based ------------------------------------------------

class _CountingConnection:
    def __init__(self, conn, log: list[str]):
        self._conn, self._log = conn, log

    def execute(self, query, *args, **kwargs):
        self._log.append(query)
        return self._conn.execute(query, *args, **kwargs)

    def close(self):
        self._conn.close()


def _instrumented_world_call(lakehouse: Path) -> tuple[list[str], int, int]:
    """Return (executed statements, connections opened, objects served) for one world request."""
    executed: list[str] = []
    opened = 0
    real_connect = LocalDuckDBDataProvider._get_connection

    def counting_connect(self):
        nonlocal opened
        opened += 1
        return _CountingConnection(real_connect(self), executed)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("world endpoint must not call per-object provider methods")

    with patch.object(LocalDuckDBDataProvider, "_get_connection", counting_connect), \
         patch.object(LocalDuckDBDataProvider, "get_threat_watchlist", forbidden), \
         patch.object(LocalDuckDBDataProvider, "get_resolution_state", forbidden), \
         patch.object(LocalDuckDBDataProvider, "get_sbdb_profile", forbidden), \
         patch.object(LocalDuckDBDataProvider, "get_sentry_profile", forbidden), \
         patch.object(LocalDuckDBDataProvider, "get_crosswalk", forbidden):
        body = _world(_client_for(lakehouse))
    return executed, opened, len(body["data"])


def test_world_uses_one_connection_and_one_query_without_per_object_calls(mock_lakehouse: Path):
    executed, opened, served = _instrumented_world_call(mock_lakehouse)
    assert served == 35
    assert opened == 1
    assert len(executed) == 1


def test_world_query_count_does_not_grow_with_population(mock_lakehouse: Path):
    """150 objects cost exactly the same number of connections/queries as 35 (no N+1)."""
    small = _instrumented_world_call(mock_lakehouse)
    extra = [{"id": str(9_000_000 + i), "name": f"(SYNTH {i})", "closest_approach_date": "2026-09-30",
              "miss_distance_km": 1e6 + i, "hazardous": False} for i in range(115)]
    _write_table(mock_lakehouse, "asteroids.parquet", _FIXTURE_ASTEROIDS + extra, ASTEROID_SCHEMA)
    large = _instrumented_world_call(mock_lakehouse)
    assert (small[2], large[2]) == (35, 150)
    assert (len(large[0]), large[1]) == (len(small[0]), small[1]) == (1, 1)


# --- 16 (real data): actual local Parquet lakehouse ---------------------------

@pytest.mark.skipif(
    not (_REAL_LAKEHOUSE / "asteroids.parquet").exists(),
    reason="Local Parquet lakehouse not present (gitignored; absent in CI).",
)
def test_world_real_local_lakehouse():
    import duckdb

    client = _client_for(_REAL_LAKEHOUSE)
    body = _world(client)
    ast_path = str(_REAL_LAKEHOUSE / "asteroids.parquet").replace("\\", "/")
    source = dict(duckdb.connect().execute(
        f"SELECT id, MIN(miss_distance_km) FROM '{ast_path}' GROUP BY id").fetchall())
    recs = _by_id(body)
    assert set(recs) == set(source) and body["world"]["object_count"] == len(source)
    for nid, rec in recs.items():
        assert rec["encounter"]["miss_distance_km"] == source[nid]
    resolved = {nid for nid, r in recs.items() if r["resolution"]["match_state"] == "RESOLVED"}
    assert {_TW54_NEOWS, _ST_NEOWS} <= resolved
    for nid in resolved:
        assert recs[nid]["sbdb"]["status"] == "available"
        assert recs[nid]["sentry"]["status"] == "available"
    _assert_world_matches_per_object_endpoints(client)


# --- Resolution consistency: GET /asteroids == GET /asteroids/world == detail ----

def _assert_list_world_detail_resolution_agree(client: TestClient) -> dict[str, dict]:
    """Every object has the same match_state/asteroid_key in the list, the world and the detail route."""
    listed = client.get("/asteroids", params={"limit": 500}).json()["data"]
    world = _by_id(_world(client))
    assert {r["neows_id"] for r in listed} == set(world)
    for row in listed:
        rec = world[row["neows_id"]]
        assert row["match_state"] == rec["resolution"]["match_state"], row["neows_id"]
        assert row["asteroid_key"] == rec["asteroid_key"], row["neows_id"]
    for nid in _PARITY_SAMPLE:
        detail = client.get(f"/asteroids/{nid}").json()
        assert detail["data"]["match_state"] == world[nid]["resolution"]["match_state"], nid
        assert detail["resolution"]["match_state"] == world[nid]["resolution"]["match_state"], nid
        assert detail["data"]["asteroid_key"] == world[nid]["asteroid_key"], nid
    return {r["neows_id"]: r for r in listed}


def test_list_and_world_resolution_agree_on_fixture(mock_client: TestClient):
    listed = _assert_list_world_detail_resolution_agree(mock_client)
    assert listed[_TW54_NEOWS]["match_state"] == "RESOLVED"
    assert sum(r["match_state"] == "UNRESOLVED" for r in listed.values()) == 33


def test_list_follows_audit_log_over_bridge(mock_lakehouse: Path):
    """Audit says UNRESOLVED while the bridge still holds a key: the audit log wins everywhere.

    The previous bridge-only watchlist reported RESOLVED here while detail/world said UNRESOLVED.
    """
    audit = [{**r, "match_state": "UNRESOLVED", "assigned_asteroid_key": None, "match_rule": "NO_CROSS_SOURCE_MATCH"}
             if r["source_system"] == "neows" and r["source_identifier_value"] == _TW54_NEOWS else r
             for r in _FIXTURE_RESOLUTION]
    _write_table(mock_lakehouse, "fact_entity_resolution.parquet", audit, FACT_ENTITY_RESOLUTION_SCHEMA)
    client = _client_for(mock_lakehouse)
    listed = _assert_list_world_detail_resolution_agree(client)
    row = listed[_TW54_NEOWS]
    assert (row["match_state"], row["asteroid_key"]) == ("UNRESOLVED", None)
    assert row["is_sentry_monitored"] is False and row["has_sbdb_characterization"] is False
    assert listed[_ST_NEOWS]["match_state"] == "RESOLVED"


def test_list_resolves_from_audit_when_enrichment_assets_missing(mock_lakehouse: Path):
    """Audit says RESOLVED but bridge/Sentry files are absent: still RESOLVED, enrichment simply empty.

    The previous watchlist fell back to all-UNRESOLVED whenever any enrichment file was missing.
    """
    for name in ("bridge_asteroid_identifier.parquet", "fact_sentry_risk_snapshot.parquet"):
        (mock_lakehouse / name).unlink()
    client = _client_for(mock_lakehouse)
    listed = _assert_list_world_detail_resolution_agree(client)
    row = listed[_TW54_NEOWS]
    assert (row["match_state"], row["asteroid_key"]) == ("RESOLVED", "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c")
    assert row["is_sentry_monitored"] is False and row["sentry_id"] is None
    assert row["has_sbdb_characterization"] is False


def test_list_bridge_fallback_and_ambiguity_match_world(mock_lakehouse: Path):
    """Without the audit log both paths use the bridge; multiple candidate keys are AMBIGUOUS in both."""
    (mock_lakehouse / "fact_entity_resolution.parquet").unlink()
    extra = {**next(r for r in _FIXTURE_BRIDGE if r["source_system"] == "neows" and r["identifier_value"] == _TW54_NEOWS),
             "asteroid_key": "ast_00000000-0000-5000-8000-000000000000"}
    _write_table(mock_lakehouse, "bridge_asteroid_identifier.parquet", _FIXTURE_BRIDGE + [extra],
                 BRIDGE_ASTEROID_IDENTIFIER_SCHEMA)
    client = _client_for(mock_lakehouse)
    listed = _assert_list_world_detail_resolution_agree(client)
    assert (listed[_TW54_NEOWS]["match_state"], listed[_TW54_NEOWS]["asteroid_key"]) == ("AMBIGUOUS", None)
    assert listed[_TW54_NEOWS]["is_sentry_monitored"] is False
    assert (listed[_ST_NEOWS]["match_state"], listed[_ST_NEOWS]["asteroid_key"]) == (
        "RESOLVED", "ast_8520aaac-9c77-5e8f-9a88-b4e501749e26")


@pytest.mark.skipif(
    not (_REAL_LAKEHOUSE / "asteroids.parquet").exists(),
    reason="Local Parquet lakehouse not present (gitignored; absent in CI).",
)
def test_list_and_world_resolution_agree_on_real_lakehouse():
    _assert_list_world_detail_resolution_agree(_client_for(_REAL_LAKEHOUSE))


# ============================================================================
# PHASE 1 STEP 5 — GET /asteroids/{neows_id}/profile
# ============================================================================


_PROFILE_ORBIT_FIELDS = (
    "orbit_class_code", "orbit_class_name", "is_neo", "is_pha", "orbit_id", "epoch_jd", "equinox",
    "semi_major_axis_au", "eccentricity", "perihelion_distance_au", "aphelion_distance_au", "inclination_deg",
    "ascending_node_longitude_deg", "argument_of_perihelion_deg", "mean_anomaly_deg", "mean_motion_deg_per_day",
    "orbital_period_days", "time_of_perihelion_jd_tdb", "soln_date", "first_obs", "last_obs", "data_arc_days",
    "n_obs_used", "condition_code", "rms", "earth_moid_au", "jupiter_moid_au", "t_jup",
)
_PROFILE_PHYSICAL_FIELDS = ("absolute_magnitude", "estimated_diameter_km", "albedo", "rotational_period_hr")


def _profile(client: TestClient, neows_id: str) -> dict:
    resp = client.get(f"/asteroids/{neows_id}/profile")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    AsteroidProfileResponse.model_validate(body)
    return body["data"]


def _assert_profile_matches_existing_endpoints(client: TestClient, neows_id: str) -> None:
    """The profile agrees field-for-field with /asteroids/{id}, /sbdb, /sentry and /crosswalk."""
    prof = _profile(client, neows_id)
    detail = client.get(f"/asteroids/{neows_id}").json()
    assert prof["identity"]["match_state"] == detail["resolution"]["match_state"]
    assert prof["identity"]["asteroid_key"] == detail["resolution"]["asteroid_key"]
    assert prof["identity"]["name"] == detail["data"]["name"]
    assert prof["encounter"]["closest_approach_date"] == detail["data"]["closest_approach_date"]
    assert prof["encounter"]["miss_distance_km"] == detail["data"]["miss_distance_km"]
    assert prof["encounter"]["is_potentially_hazardous"] == detail["data"]["hazardous"]
    assert prof["provenance"]["resolution"]["match_rule"] == detail["resolution"]["match_rule"]

    sbdb = client.get(f"/asteroids/{neows_id}/sbdb").json()["data"]
    if sbdb is None:
        assert all(prof["orbit"][f] is None for f in _PROFILE_ORBIT_FIELDS)
        assert all(prof["physical"][f] is None for f in _PROFILE_PHYSICAL_FIELDS)
        assert prof["provenance"]["sbdb"]["run_id"] is None
    else:
        for field in _PROFILE_ORBIT_FIELDS:
            assert prof["orbit"][field] == sbdb[field], field
        for field in _PROFILE_PHYSICAL_FIELDS:
            assert prof["physical"][field] == sbdb[field], field
        assert prof["identity"]["sbdb_spkid"] == sbdb["spkid"]
        assert prof["identity"]["sbdb_designation"] == sbdb["designation"]
        assert (prof["provenance"]["sbdb"]["snapshot_key"], prof["provenance"]["sbdb"]["run_id"],
                prof["provenance"]["sbdb"]["snapshot_time"]) == (sbdb["snapshot_key"], sbdb["run_id"], sbdb["snapshot_time"])

    sentry = client.get(f"/asteroids/{neows_id}/sentry").json()["data"]
    if sentry is None:
        assert prof["sentry"]["status"] == "not_resolved"
    else:
        assert prof["identity"]["sentry_id"] == sentry["sentry_id"]
        assert prof["provenance"]["sentry"]["latest_snapshot_key"] == sentry["latest_snapshot_key"]

    crosswalk = client.get(f"/asteroids/{neows_id}/crosswalk").json()["data"]
    assert prof["identity"]["crosswalk"] == crosswalk


# --- 1, 3, 10: resolved profile, identity, provenance ------------------------

def test_profile_resolved_identity_and_provenance(mock_client: TestClient):
    prof = _profile(mock_client, _TW54_NEOWS)
    ident = prof["identity"]
    assert (ident["neows_id"], ident["name"], ident["match_state"]) == (_TW54_NEOWS, "(2010 TW54)", "RESOLVED")
    assert ident["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert (ident["sbdb_spkid"], ident["sbdb_designation"], ident["sbdb_fullname"]) == ("50548689", "2010 TW54", "(2010 TW54)")
    assert ident["sentry_id"] == "bK10T54W"
    assert {(r["source_system"], r["identifier_name"]) for r in ident["crosswalk"]} >= {
        ("neows", "id"), ("sbdb", "spkid"), ("sentry", "sentry_id")}
    assert ident["availability"] == {"status": "available", "unavailable": {}}

    prov = prof["provenance"]
    assert prov["neows"] == {"source": "nasa_neows", "dataset_run_id": None,
                             "source_raw_file": None, "source_raw_sha256": None}
    assert prov["resolution"]["source"] == "entity_resolution"
    assert prov["resolution"]["match_rule"] == "EXACT_DESIGNATION_MATCH"
    assert prov["sbdb"] == {"source": "jpl_sbdb", "spkid": "50548689", "snapshot_key": "2026-09-26",
                            "run_id": "26b0c1ef0bba", "snapshot_time": "2026-09-26T20:27:39.451638+00:00"}
    assert prov["sentry"] == {"source": "jpl_sentry", "sentry_id": "bK10T54W",
                              "latest_snapshot_key": "2026-09-26", "run_id": "7d446dc65b5a",
                              "snapshot_time": "2026-09-26T20:15:16.035354+00:00",
                              "latest_catalog_snapshot_key": "2026-09-26"}


def test_profile_sections_declare_their_source(mock_client: TestClient):
    prof = _profile(mock_client, _TW54_NEOWS)
    assert prof["orbit"]["source"] == "jpl_sbdb"
    assert prof["physical"]["source"] == "jpl_sbdb"
    assert prof["encounter"]["source"] == "nasa_neows"
    assert prof["sentry"]["source"] == "jpl_sentry"


# --- 4, 6: orbit fields and source period ------------------------------------

@pytest.mark.parametrize("field,element_name", sorted(_SBDB_ELEMENT_FIELDS.items()))
def test_profile_orbit_serves_source_elements(mock_client: TestClient, field: str, element_name: str):
    assert _profile(mock_client, _TW54_NEOWS)["orbit"][field] == pytest.approx(
        _fixture_element(_TW54_SPKID, element_name), rel=1e-12)


def test_profile_orbit_epoch_equinox_class_and_fit(mock_client: TestClient):
    orbit = _profile(mock_client, _TW54_NEOWS)["orbit"]
    assert (orbit["epoch_jd"], orbit["equinox"], orbit["orbit_id"]) == (2461200.5, "J2000", "14")
    assert (orbit["orbit_class_code"], orbit["orbit_class_name"]) == ("APO", "Apollo")
    assert (orbit["is_neo"], orbit["is_pha"]) == (True, False)
    assert (orbit["data_arc_days"], orbit["n_obs_used"], orbit["condition_code"]) == (5, 70, "6")
    assert orbit["availability"] == {"status": "available", "unavailable": {}}


def test_profile_period_is_source_days_only(mock_client: TestClient):
    """The profile serves SBDB 'per' in days and does not carry the deprecated derived year field."""
    orbit = _profile(mock_client, _TW54_NEOWS)["orbit"]
    assert orbit["orbital_period_days"] == _fixture_element(_TW54_SPKID, "per")
    assert "orbital_period_yr" not in orbit
    assert "astrometric_data_quality_tier" not in orbit


# --- 5: coherent snapshot ------------------------------------------------------

def test_profile_uses_single_latest_sbdb_snapshot(mock_lakehouse: Path):
    older = _sbdb_snapshot_rows("2026-09-01", "run_older", "2026-09-01T00:00:00+00:00", 0.5)
    newer = _sbdb_snapshot_rows("2026-09-30", "run_newer", "2026-09-30T00:00:00+00:00", 2.0)
    _write_sbdb_tables(mock_lakehouse, older, newer)
    client = _client_for(mock_lakehouse)
    prof = _profile(client, _TW54_NEOWS)
    assert (prof["provenance"]["sbdb"]["snapshot_key"], prof["provenance"]["sbdb"]["run_id"]) == ("2026-09-30", "run_newer")
    for field, element_name in _SBDB_ELEMENT_FIELDS.items():
        assert prof["orbit"][field] == pytest.approx(_fixture_element(_TW54_SPKID, element_name) * 2.0, rel=1e-12)
    assert prof["physical"]["absolute_magnitude"] == pytest.approx(27.6 * 2.0)
    world = _by_id(_world(client))[_TW54_NEOWS]
    assert (world["sbdb"]["snapshot_key"], world["sbdb"]["run_id"]) == ("2026-09-30", "run_newer")
    _assert_profile_matches_existing_endpoints(client, _TW54_NEOWS)


def test_profile_partial_snapshot_reports_not_in_source(mock_lakehouse: Path):
    """Fields absent from the selected snapshot are null with reason not_in_source, never older values."""
    newer = _sbdb_snapshot_rows("2026-09-30", "run_partial", "2026-09-30T00:00:00+00:00", 2.0,
                                element_names=["a", "e"], include_physical=False)
    _write_sbdb_tables(mock_lakehouse, newer)
    prof = _profile(_client_for(mock_lakehouse), _TW54_NEOWS)
    orbit = prof["orbit"]
    assert orbit["semi_major_axis_au"] == pytest.approx(_fixture_element(_TW54_SPKID, "a") * 2.0)
    assert orbit["inclination_deg"] is None and orbit["mean_anomaly_deg"] is None
    assert orbit["availability"]["status"] == "partial"
    assert orbit["availability"]["unavailable"]["inclination_deg"] == "not_in_source"
    assert "semi_major_axis_au" not in orbit["availability"]["unavailable"]
    physical = prof["physical"]
    assert physical["absolute_magnitude"] is None
    assert physical["availability"]["status"] == "unavailable"
    assert set(physical["availability"]["unavailable"].values()) == {"not_in_source"}


# --- 7: physical ------------------------------------------------------------

def test_profile_physical_serves_only_source_values(mock_client: TestClient):
    physical = _profile(mock_client, _ST_NEOWS)["physical"]
    assert physical["absolute_magnitude"] == 27.1
    assert physical["estimated_diameter_km"] is None and physical["albedo"] is None
    assert physical["rotational_period_hr"] is None
    assert physical["availability"] == {"status": "partial", "unavailable": {
        "estimated_diameter_km": "not_in_source", "albedo": "not_in_source", "rotational_period_hr": "not_in_source"}}


# --- 8: encounter -------------------------------------------------------------

def test_profile_encounter_is_neows_closest_approach(mock_lakehouse: Path):
    extra = [{**_FIXTURE_ASTEROIDS[0], "closest_approach_date": "2026-09-27", "miss_distance_km": 1234.5}]
    _write_table(mock_lakehouse, "asteroids.parquet", _FIXTURE_ASTEROIDS + extra, ASTEROID_SCHEMA)
    client = _client_for(mock_lakehouse)
    enc = _profile(client, _FIXTURE_ASTEROIDS[0]["id"])["encounter"]
    assert enc["selection_rule"] == "CLOSEST_OBSERVED_APPROACH"
    assert (enc["closest_approach_date"], enc["miss_distance_km"]) == ("2026-09-27", 1234.5)
    assert enc["is_potentially_hazardous"] is True
    assert "is_potentially_hazardous" not in enc["availability"]["unavailable"]
    _assert_profile_matches_existing_endpoints(client, _FIXTURE_ASTEROIDS[0]["id"])


# --- 2, 9: unresolved profile and null semantics ------------------------------

def test_profile_unresolved_object_is_served_with_reasons(mock_client: TestClient):
    unresolved_id = "2138971"
    prof = _profile(mock_client, unresolved_id)
    assert prof["identity"]["match_state"] == "UNRESOLVED"
    assert prof["identity"]["asteroid_key"] is None and prof["identity"]["crosswalk"] == []
    assert set(prof["identity"]["availability"]["unavailable"].values()) == {"not_resolved"}
    for section, fields in (("orbit", _PROFILE_ORBIT_FIELDS), ("physical", _PROFILE_PHYSICAL_FIELDS)):
        assert all(prof[section][f] is None for f in fields), section
        assert prof[section]["availability"]["status"] == "unavailable"
        assert set(prof[section]["availability"]["unavailable"]) == set(fields)
        assert set(prof[section]["availability"]["unavailable"].values()) == {"not_resolved"}
    assert prof["encounter"]["miss_distance_km"] == next(
        a["miss_distance_km"] for a in _FIXTURE_ASTEROIDS if a["id"] == unresolved_id)
    assert {k: prof["sentry"][k] for k in ("status", "sentry_id", "in_latest_catalog", "assessment_endpoint")} == {
        "status": "not_resolved", "sentry_id": None, "in_latest_catalog": None, "assessment_endpoint": None}
    assert set(prof["sentry"]["assessment"]["availability"]["unavailable"].values()) == {"not_resolved"}
    assert prof["provenance"]["sbdb"]["run_id"] is None and prof["provenance"]["sentry"]["run_id"] is None


def test_profile_unknown_flags_are_null_not_false(mock_lakehouse: Path):
    """Unknown NeoWs PHA and unknown SBDB NEO/PHA flags are null with not_in_source, never false."""
    rows = [{**a, "hazardous": None} if a["id"] == _TW54_NEOWS else a for a in _FIXTURE_ASTEROIDS]
    _write_table(mock_lakehouse, "asteroids.parquet", rows, ASTEROID_SCHEMA)
    newer = _sbdb_snapshot_rows("2026-09-30", "run_flags", "2026-09-30T00:00:00+00:00", 1.0, is_neo=None, is_pha=None)
    _write_sbdb_tables(mock_lakehouse, newer)
    prof = _profile(_client_for(mock_lakehouse), _TW54_NEOWS)
    assert prof["encounter"]["is_potentially_hazardous"] is None
    assert prof["encounter"]["availability"]["unavailable"]["is_potentially_hazardous"] == "not_in_source"
    assert prof["orbit"]["is_neo"] is None and prof["orbit"]["is_pha"] is None
    assert prof["orbit"]["availability"]["unavailable"] == {"is_neo": "not_in_source", "is_pha": "not_in_source"}


# --- 11, 13: Sentry linkage ----------------------------------------------------

def _all_keys(obj):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _all_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _all_keys(v)


def test_profile_has_no_synthetic_scores_and_sentry_values_stay_in_sentry(mock_client: TestClient):
    """No danger/risk/threat/score field anywhere; published Sentry values appear only under sentry.assessment."""
    prof = _profile(mock_client, _TW54_NEOWS)
    keys = set(_all_keys(prof))
    assert not {k for k in keys if any(word in k.lower() for word in ("danger", "risk", "threat", "score"))}
    outside_sentry = set(_all_keys({k: v for k, v in prof.items() if k != "sentry"}))
    assert not (outside_sentry & {"impact_probability", "palermo_scale_cum", "palermo_scale_max",
                                  "torino_scale_max", "potential_impacts_count", "v_infinity_km_s"})


def test_profile_sentry_statuses_follow_crosswalk(mock_lakehouse: Path):
    bridge = [r for r in _FIXTURE_BRIDGE
              if not (r["source_system"] == "sentry" and r["asteroid_key"].startswith("ast_b8259"))]
    st_link = next(r for r in _FIXTURE_BRIDGE if r["source_system"] == "sentry"
                   and r["identifier_name"] == "sentry_id" and r["asteroid_key"].startswith("ast_8520"))
    bridge.append({**st_link, "identifier_value": "bKXXXXXX"})
    _write_table(mock_lakehouse, "bridge_asteroid_identifier.parquet", bridge, BRIDGE_ASTEROID_IDENTIFIER_SCHEMA)
    client = _client_for(mock_lakehouse)

    tw54 = _profile(client, _TW54_NEOWS)  # resolved, SBDB present, no Sentry link
    assert tw54["sentry"]["status"] == "not_present" and tw54["sentry"]["assessment_endpoint"] is None
    assert tw54["identity"]["sentry_id"] is None
    assert tw54["identity"]["availability"]["unavailable"] == {"sentry_id": "not_in_source"}
    assert tw54["orbit"]["availability"]["status"] == "available"

    st = _profile(client, _ST_NEOWS)  # two Sentry IDs: ambiguous, nothing leaks
    assert st["sentry"]["status"] == "ambiguous" and st["sentry"]["sentry_id"] is None
    assert st["provenance"]["sentry"] == {"source": "jpl_sentry", "sentry_id": None, "latest_snapshot_key": None,
                                          "run_id": None, "snapshot_time": None,
                                          "latest_catalog_snapshot_key": "2026-09-26"}
    assert st["identity"]["availability"]["unavailable"] == {"sentry_id": "ambiguous_linkage"}
    for nid in (_TW54_NEOWS, _ST_NEOWS):
        _assert_profile_matches_existing_endpoints(client, nid)


def test_profile_sentry_linked_without_record(mock_lakehouse: Path):
    _write_table(mock_lakehouse, "fact_sentry_risk_snapshot.parquet",
                 [r for r in _FIXTURE_SENTRY if r["sentry_id"] != "bK10T54W"], SENTRY_RISK_SNAPSHOT_SCHEMA)
    client = _client_for(mock_lakehouse)
    prof = _profile(client, _TW54_NEOWS)
    assert prof["sentry"]["status"] == "linked_no_record"
    assert prof["sentry"]["sentry_id"] == "bK10T54W"
    assert prof["sentry"]["assessment_endpoint"] == f"/asteroids/{_TW54_NEOWS}/sentry"
    assert prof["provenance"]["sentry"]["latest_snapshot_key"] is None
    _assert_profile_matches_existing_endpoints(client, _TW54_NEOWS)


# --- 12: missing SBDB ----------------------------------------------------------

def test_profile_resolved_without_sbdb_snapshot(mock_lakehouse: Path):
    """Resolved identity, SPK-ID linked, but no SBDB snapshot stored: SBDB sections are not_in_source."""
    _write_table(mock_lakehouse, "fact_sbdb_object_snapshot.parquet",
                 [r for r in _FIXTURE_SBDB_OBJ if r["spkid"] != _TW54_SPKID], SBDB_OBJECT_SCHEMA)
    _write_table(mock_lakehouse, "fact_sbdb_orbit.parquet",
                 [r for r in _FIXTURE_SBDB_ORB if r["spkid"] != _TW54_SPKID], SBDB_ORBIT_SCHEMA)
    client = _client_for(mock_lakehouse)
    prof = _profile(client, _TW54_NEOWS)
    assert prof["identity"]["match_state"] == "RESOLVED"
    assert prof["identity"]["sbdb_spkid"] == "50548689"
    assert prof["identity"]["availability"]["unavailable"] == {
        "sbdb_designation": "not_in_source", "sbdb_fullname": "not_in_source"}
    for section in ("orbit", "physical"):
        assert prof[section]["availability"]["status"] == "unavailable"
        assert set(prof[section]["availability"]["unavailable"].values()) == {"not_in_source"}
    assert prof["provenance"]["sbdb"] == {"source": "jpl_sbdb", "spkid": "50548689", "snapshot_key": None,
                                         "run_id": None, "snapshot_time": None}
    assert prof["sentry"]["status"] == "available"
    _assert_profile_matches_existing_endpoints(client, _TW54_NEOWS)


def test_profile_not_found_returns_404(mock_client: TestClient):
    resp = mock_client.get("/asteroids/99999999/profile")
    assert resp.status_code == 404
    assert ErrorResponse.model_validate(resp.json()).error.code == "TARGET_NOT_FOUND"


# --- 14: agreement with existing endpoints ------------------------------------

def test_profile_matches_existing_endpoints_for_every_fixture_object(mock_client: TestClient):
    for neows_id in sorted(a["id"] for a in _FIXTURE_ASTEROIDS):
        _assert_profile_matches_existing_endpoints(mock_client, neows_id)


def test_profile_agrees_with_world_record(mock_client: TestClient):
    world = _by_id(_world(mock_client))
    for neows_id in _PARITY_SAMPLE:
        prof, rec = _profile(mock_client, neows_id), world[neows_id]
        assert prof["identity"]["asteroid_key"] == rec["asteroid_key"]
        assert prof["identity"]["match_state"] == rec["resolution"]["match_state"]
        assert prof["sentry"]["status"] == rec["sentry"]["status"]
        assert prof["encounter"]["miss_distance_km"] == rec["encounter"]["miss_distance_km"]
        assert prof["provenance"]["sbdb"]["run_id"] == rec["sbdb"]["run_id"]


# --- Retrieval budget ------------------------------------------------------------

def test_profile_uses_bounded_retrievals_without_per_source_resolution(mock_lakehouse: Path):
    """A resolved profile costs a fixed number of connections and never re-runs per-object resolution."""
    executed: list[str] = []
    opened = 0
    real_connect = LocalDuckDBDataProvider._get_connection

    def counting_connect(self):
        nonlocal opened
        opened += 1
        return _CountingConnection(real_connect(self), executed)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("profile must reuse the canonical world query, not the per-object watchlist/resolution")

    with patch.object(LocalDuckDBDataProvider, "_get_connection", counting_connect), \
         patch.object(LocalDuckDBDataProvider, "get_threat_watchlist", forbidden), \
         patch.object(LocalDuckDBDataProvider, "get_resolution_state", forbidden), \
         patch.object(LocalDuckDBDataProvider, "get_sentry_profile", forbidden):
        _profile(_client_for(mock_lakehouse), _TW54_NEOWS)
        resolved_cost = (opened, len(executed))
        opened, executed[:] = 0, []
        _profile(_client_for(mock_lakehouse), "2138971")
        unresolved_cost = (opened, len(executed))
    assert resolved_cost[0] == 3          # world (filtered) + SBDB profile + crosswalk
    assert unresolved_cost == (1, 1)      # world query only; nothing to enrich


# --- 15: real local data --------------------------------------------------------

@pytest.mark.skipif(
    not (_REAL_LAKEHOUSE / "asteroids.parquet").exists(),
    reason="Local Parquet lakehouse not present (gitignored; absent in CI).",
)
def test_profile_real_local_lakehouse():
    client = _client_for(_REAL_LAKEHOUSE)
    world = _by_id(_world(client))
    for neows_id in (_TW54_NEOWS, _ST_NEOWS):
        prof = _profile(client, neows_id)
        assert prof["orbit"]["availability"]["status"] == "available"
        assert prof["sentry"]["status"] == "available"
        assert prof["orbit"]["ascending_node_longitude_deg"] is not None
        _assert_profile_matches_existing_endpoints(client, neows_id)
    unresolved = sorted(nid for nid, r in world.items() if r["resolution"]["match_state"] != "RESOLVED")
    assert len(unresolved) == 33
    for neows_id in unresolved:
        prof = _profile(client, neows_id)
        assert prof["orbit"]["availability"]["status"] == "unavailable"
        assert prof["encounter"]["miss_distance_km"] == world[neows_id]["encounter"]["miss_distance_km"]


# ============================================================================
# PHASE 1 STEP 6 — SENTRY ASSESSMENT SERVING BRANCH (profile.sentry)
# ============================================================================

_TW54_SENTRY = "bK10T54W"
_ST_SENTRY = "bK08S00T"
_ASSESSMENT_FROM_SOURCE = {  # profile.sentry.assessment field -> fact_sentry_risk_snapshot column
    "designation": "designation", "fullname": "fullname", "impact_probability": "impact_probability",
    "potential_impacts_count": "potential_impacts_count", "impact_year_range": "impact_year_range",
    "palermo_scale_cum": "palermo_scale_cum", "palermo_scale_max": "palermo_scale_max",
    "torino_scale_max": "torino_scale_max", "v_infinity_km_s": "v_infinity_km_s",
    "absolute_magnitude": "absolute_magnitude", "estimated_diameter_km": "estimated_diameter_km",
    "last_obs_date": "last_obs_date", "last_obs_jd": "last_obs_jd",
}
_SCALED_SENTRY_FIELDS = ("impact_probability", "palermo_scale_cum", "palermo_scale_max", "v_infinity_km_s",
                         "absolute_magnitude", "estimated_diameter_km")


def _sentry_record(sentry_id: str, snapshot_key: str, run_id: str, snapshot_time: str,
                   scale: float = 1.0, **overrides) -> dict:
    """Clone a fixture Sentry row into another snapshot with distinguishable (scaled) published values."""
    base = next(r for r in _FIXTURE_SENTRY if r["sentry_id"] == sentry_id)
    row = {**base, "snapshot_key": snapshot_key, "run_id": run_id, "snapshot_time": snapshot_time,
           "potential_impacts_count": base["potential_impacts_count"] + int(scale * 10),
           "impact_year_range": f"{2000 + int(scale * 10)}-2122"}
    for field in _SCALED_SENTRY_FIELDS:
        row[field] = base[field] * scale
    row.update(overrides)
    return row


def _write_sentry(lakehouse: Path, rows: list[dict]) -> None:
    _write_table(lakehouse, "fact_sentry_risk_snapshot.parquet", rows, SENTRY_RISK_SNAPSHOT_SCHEMA)


def _assert_assessment_equals_record(assessment: dict, record: dict) -> None:
    for field, column in _ASSESSMENT_FROM_SOURCE.items():
        assert assessment[field] == record[column], field


def _assert_sentry_matches_legacy_endpoint(client: TestClient, neows_id: str) -> None:
    """profile.sentry agrees with the existing GET /asteroids/{id}/sentry for every shared field."""
    prof = _profile(client, neows_id)["sentry"]
    legacy = client.get(f"/asteroids/{neows_id}/sentry").json()["data"]
    if legacy is None:
        assert prof["status"] == "not_resolved"
        return
    a = prof["assessment"]
    assert prof["sentry_id"] == legacy["sentry_id"]
    assert (a["designation"], a["fullname"]) == (legacy["designation"], legacy["fullname"])
    assert a["impact_probability"] == legacy["latest_impact_probability"]
    assert a["palermo_scale_cum"] == legacy["latest_palermo_scale_cum"]
    assert a["palermo_scale_max"] == legacy["latest_palermo_scale_max"]
    assert a["torino_scale_max"] == legacy["latest_torino_scale_max"]
    assert a["potential_impacts_count"] == legacy["latest_potential_impacts_count"]
    assert a["v_infinity_km_s"] == legacy["v_infinity_km_s"]
    assert a["impact_year_range"] == (legacy["impact_year_range"] or None)
    assert a["last_obs_date"] == legacy["last_obs_date"]
    if prof["status"] == "available":
        assert _profile(client, neows_id)["provenance"]["sentry"]["latest_snapshot_key"] == legacy["latest_snapshot_key"]
        assert prof["in_latest_catalog"] == legacy["is_currently_active"]


# --- Availability by linkage ------------------------------------------------------

def test_sentry_assessment_available_for_linked_objects(mock_client: TestClient):
    for neows_id, sentry_id in ((_TW54_NEOWS, _TW54_SENTRY), (_ST_NEOWS, _ST_SENTRY)):
        sentry = _profile(mock_client, neows_id)["sentry"]
        assert (sentry["status"], sentry["sentry_id"], sentry["source_contract"]) == (
            "available", sentry_id, "sentry_mode_s_summary")
        _assert_assessment_equals_record(sentry["assessment"], next(r for r in _FIXTURE_SENTRY if r["sentry_id"] == sentry_id))
        assert sentry["assessment"]["availability"] == {"status": "available", "unavailable": {}}
        _assert_sentry_matches_legacy_endpoint(mock_client, neows_id)


def test_sentry_assessment_unavailable_reasons_by_status(mock_lakehouse: Path):
    """not_present -> not_in_source; ambiguous -> ambiguous_linkage, with no values from either record."""
    bridge = [r for r in _FIXTURE_BRIDGE
              if not (r["source_system"] == "sentry" and r["asteroid_key"].startswith("ast_b8259"))]
    st_link = next(r for r in _FIXTURE_BRIDGE if r["source_system"] == "sentry"
                   and r["identifier_name"] == "sentry_id" and r["asteroid_key"].startswith("ast_8520"))
    bridge.append({**st_link, "identifier_value": _TW54_SENTRY})  # 2008 ST now linked to two Sentry IDs
    _write_table(mock_lakehouse, "bridge_asteroid_identifier.parquet", bridge, BRIDGE_ASTEROID_IDENTIFIER_SCHEMA)
    client = _client_for(mock_lakehouse)

    not_present = _profile(client, _TW54_NEOWS)["sentry"]
    assert not_present["status"] == "not_present"
    assert all(not_present["assessment"][f] is None for f in _ASSESSMENT_FROM_SOURCE)
    assert set(not_present["assessment"]["availability"]["unavailable"].values()) == {"not_in_source"}

    ambiguous = _profile(client, _ST_NEOWS)
    assessment = ambiguous["sentry"]["assessment"]
    assert ambiguous["sentry"]["status"] == "ambiguous"
    assert all(assessment[f] is None for f in _ASSESSMENT_FROM_SOURCE)
    assert set(assessment["availability"]["unavailable"].values()) == {"ambiguous_linkage"}
    assert ambiguous["identity"]["availability"]["unavailable"]["sentry_id"] == "ambiguous_linkage"
    for neows_id in (_TW54_NEOWS, _ST_NEOWS):
        _assert_sentry_matches_legacy_endpoint(client, neows_id)


def test_sentry_assessment_linked_without_record(mock_lakehouse: Path):
    _write_sentry(mock_lakehouse, [r for r in _FIXTURE_SENTRY if r["sentry_id"] != _TW54_SENTRY])
    prof = _profile(_client_for(mock_lakehouse), _TW54_NEOWS)
    assert (prof["sentry"]["status"], prof["sentry"]["sentry_id"]) == ("linked_no_record", _TW54_SENTRY)
    assert set(prof["sentry"]["assessment"]["availability"]["unavailable"].values()) == {"not_in_source"}
    assert prof["provenance"]["sentry"]["snapshot_time"] is None
    assert prof["provenance"]["sentry"]["latest_catalog_snapshot_key"] == "2026-09-26"


def test_sentry_assessment_requires_linkage_not_pha(mock_lakehouse: Path):
    """A PHA without Sentry linkage gets no assessment; a non-PHA with linkage gets the published one."""
    bridge = [r for r in _FIXTURE_BRIDGE
              if not (r["source_system"] == "sentry" and r["asteroid_key"].startswith("ast_b8259"))]
    _write_table(mock_lakehouse, "bridge_asteroid_identifier.parquet", bridge, BRIDGE_ASTEROID_IDENTIFIER_SCHEMA)
    rows = [{**a, "hazardous": True} if a["id"] == _TW54_NEOWS else a for a in _FIXTURE_ASTEROIDS]
    _write_table(mock_lakehouse, "asteroids.parquet", rows, ASTEROID_SCHEMA)
    client = _client_for(mock_lakehouse)

    pha_resolved_unlinked = _profile(client, _TW54_NEOWS)
    assert pha_resolved_unlinked["encounter"]["is_potentially_hazardous"] is True
    assert pha_resolved_unlinked["sentry"]["status"] == "not_present"
    assert pha_resolved_unlinked["sentry"]["assessment"]["impact_probability"] is None

    pha_unresolved = _profile(client, "2138971")
    assert pha_unresolved["encounter"]["is_potentially_hazardous"] is True
    assert pha_unresolved["sentry"]["status"] == "not_resolved"
    assert pha_unresolved["sentry"]["assessment"]["availability"]["status"] == "unavailable"

    non_pha_linked = _profile(client, _ST_NEOWS)
    assert non_pha_linked["encounter"]["is_potentially_hazardous"] is False
    assert non_pha_linked["sentry"]["status"] == "available"
    assert non_pha_linked["sentry"]["assessment"]["impact_probability"] == pytest.approx(0.00013679128)


# --- Snapshot coherence / provenance --------------------------------------------------

def test_sentry_assessment_uses_latest_record_only(mock_lakehouse: Path):
    older = _sentry_record(_TW54_SENTRY, "2026-09-01", "run_older", "2026-09-01T00:00:00+00:00", 0.5)
    newer = _sentry_record(_TW54_SENTRY, "2026-09-30", "run_newer", "2026-09-30T00:00:00+00:00", 2.0)
    _write_sentry(mock_lakehouse, _FIXTURE_SENTRY + [older, newer])
    client = _client_for(mock_lakehouse)
    prof = _profile(client, _TW54_NEOWS)
    _assert_assessment_equals_record(prof["sentry"]["assessment"], newer)
    assert prof["provenance"]["sentry"] == {
        "source": "jpl_sentry", "sentry_id": _TW54_SENTRY, "latest_snapshot_key": "2026-09-30",
        "run_id": "run_newer", "snapshot_time": "2026-09-30T00:00:00+00:00", "latest_catalog_snapshot_key": "2026-09-30"}
    assert prof["sentry"]["in_latest_catalog"] is True
    _assert_sentry_matches_legacy_endpoint(client, _TW54_NEOWS)


def test_sentry_same_day_runs_select_one_coherent_record(mock_lakehouse: Path):
    """Two runs on one snapshot_key: values, run_id and snapshot_time all come from the later run."""
    early = _sentry_record(_TW54_SENTRY, "2026-09-30", "run_b_early", "2026-09-30T01:00:00+00:00", 3.0)
    late = _sentry_record(_TW54_SENTRY, "2026-09-30", "run_a_late", "2026-09-30T02:00:00+00:00", 4.0)
    _write_sentry(mock_lakehouse, _FIXTURE_SENTRY + [early, late])
    prof = _profile(_client_for(mock_lakehouse), _TW54_NEOWS)
    _assert_assessment_equals_record(prof["sentry"]["assessment"], late)
    assert (prof["provenance"]["sentry"]["run_id"], prof["provenance"]["sentry"]["snapshot_time"]) == (
        "run_a_late", "2026-09-30T02:00:00+00:00")


def test_sentry_object_absent_from_latest_catalog(mock_lakehouse: Path):
    """A newer catalog without the object: last stored assessment is served, flagged in_latest_catalog=false."""
    newer_st = _sentry_record(_ST_SENTRY, "2026-09-30", "run_catalog2", "2026-09-30T00:00:00+00:00", 2.0)
    _write_sentry(mock_lakehouse, _FIXTURE_SENTRY + [newer_st])
    client = _client_for(mock_lakehouse)

    tw54 = _profile(client, _TW54_NEOWS)
    assert tw54["sentry"]["status"] == "available" and tw54["sentry"]["in_latest_catalog"] is False
    _assert_assessment_equals_record(tw54["sentry"]["assessment"],
                                     next(r for r in _FIXTURE_SENTRY if r["sentry_id"] == _TW54_SENTRY))
    assert tw54["provenance"]["sentry"]["latest_snapshot_key"] == "2026-09-26"
    assert tw54["provenance"]["sentry"]["latest_catalog_snapshot_key"] == "2026-09-30"

    st = _profile(client, _ST_NEOWS)
    assert st["sentry"]["in_latest_catalog"] is True
    _assert_assessment_equals_record(st["sentry"]["assessment"], newer_st)
    for neows_id in (_TW54_NEOWS, _ST_NEOWS):
        _assert_sentry_matches_legacy_endpoint(client, neows_id)


# --- Missing published values ---------------------------------------------------------

def test_sentry_missing_published_values_are_null_with_reason(mock_lakehouse: Path):
    """Null H/diameter stay null; an empty stored 'range' ("" from ingestion) is served as null, not ""."""
    row = {**next(r for r in _FIXTURE_SENTRY if r["sentry_id"] == _TW54_SENTRY),
           "absolute_magnitude": None, "estimated_diameter_km": None, "impact_year_range": "", "last_obs_jd": None}
    _write_sentry(mock_lakehouse, [row] + [r for r in _FIXTURE_SENTRY if r["sentry_id"] != _TW54_SENTRY])
    assessment = _profile(_client_for(mock_lakehouse), _TW54_NEOWS)["sentry"]["assessment"]
    for field in ("absolute_magnitude", "estimated_diameter_km", "impact_year_range", "last_obs_jd"):
        assert assessment[field] is None, field
    assert assessment["availability"] == {"status": "partial", "unavailable": {
        "impact_year_range": "not_in_source", "absolute_magnitude": "not_in_source",
        "estimated_diameter_km": "not_in_source", "last_obs_jd": "not_in_source"}}
    assert assessment["impact_probability"] == pytest.approx(6.594578e-05)


def test_sentry_contract_declares_mode_s_and_excludes_mode_o_fields(mock_client: TestClient):
    """Only Mode S summary fields are served; no solution-level, impact-date or impact-energy fields exist."""
    sentry = _profile(mock_client, _TW54_NEOWS)["sentry"]
    assert sentry["source_contract"] == "sentry_mode_s_summary"
    assert set(sentry["assessment"]) == set(_ASSESSMENT_FROM_SOURCE) | {"availability"}
    assert not {k for k in _all_keys(sentry) if any(w in k for w in ("energy", "solution", "impact_date"))}


def test_sentry_values_stay_source_separated(mock_client: TestClient):
    """Sentry's H/diameter live only in sentry.assessment; SBDB physical keeps SBDB's own values."""
    prof = _profile(mock_client, _TW54_NEOWS)
    assert prof["sentry"]["assessment"]["absolute_magnitude"] == 27.55
    assert prof["sentry"]["assessment"]["estimated_diameter_km"] == 0.01
    assert prof["physical"]["absolute_magnitude"] == 27.6
    assert prof["physical"]["estimated_diameter_km"] is None


def test_world_contract_unchanged_by_sentry_branch(mock_client: TestClient):
    """The assessment is profile-only: world records keep their compact sentry linkage block."""
    for rec in _world(mock_client)["data"]:
        assert set(rec["sentry"]) == {"status", "sentry_id", "latest_snapshot_key", "run_id", "in_latest_catalog"}


# --- Real local data -------------------------------------------------------------------

@pytest.mark.skipif(
    not (_REAL_LAKEHOUSE / "fact_sentry_risk_snapshot.parquet").exists(),
    reason="Local Parquet lakehouse not present (gitignored; absent in CI).",
)
def test_sentry_assessment_real_local_lakehouse():
    import duckdb

    client = _client_for(_REAL_LAKEHOUSE)
    sentry_path = str(_REAL_LAKEHOUSE / "fact_sentry_risk_snapshot.parquet").replace("\\", "/")
    con = duckdb.connect()
    for neows_id in (_TW54_NEOWS, _ST_NEOWS):
        prof = _profile(client, neows_id)
        prov = prof["provenance"]["sentry"]
        cur = con.execute(
            f"SELECT * FROM '{sentry_path}' WHERE sentry_id = ? AND snapshot_key = ? AND run_id = ?",
            [prof["sentry"]["sentry_id"], prov["latest_snapshot_key"], prov["run_id"]])
        cols = [d[0] for d in cur.description]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        assert len(rows) == 1
        _assert_assessment_equals_record(prof["sentry"]["assessment"], rows[0])
        assert prov["snapshot_time"] == rows[0]["snapshot_time"]
        _assert_sentry_matches_legacy_endpoint(client, neows_id)


# ============================================================================
# PHASE 1 STEP 7 — NORMALIZED NEOWS FIELDS IN WORLD AND PROFILE
# ============================================================================


# NeoWs values chosen to differ from SBDB/Sentry so source separation is observable.
_NEOWS_ENRICHED = {
    _TW54_NEOWS: {"close_approach_datetime": "2026-09-30T05:42", "close_approach_epoch_ms": 1790746920000,
                  "relative_velocity_km_s": 4.5115736676, "absolute_magnitude_h": 27.4,
                  "estimated_diameter_min_km": 0.0080270317, "estimated_diameter_max_km": 0.0179489885,
                  "is_sentry_object": True},
    _ST_NEOWS: {"close_approach_datetime": "2026-09-26T11:03", "close_approach_epoch_ms": 1790420580000,
                "relative_velocity_km_s": 3.0, "absolute_magnitude_h": 27.1,
                "estimated_diameter_min_km": 0.0101054342, "estimated_diameter_max_km": 0.0225964377,
                "is_sentry_object": True},
}
_NEOWS_NEW_FIELDS = tuple(next(iter(_NEOWS_ENRICHED.values())))
_WORLD_NEOWS_FIELDS = ("close_approach_datetime", "relative_velocity_km_s",
                       "estimated_diameter_min_km", "estimated_diameter_max_km")


def _write_neows(lakehouse: Path, enriched: dict[str, dict] | None = None) -> list[dict]:
    rows = [{**a, **(enriched or _NEOWS_ENRICHED).get(a["id"], {})} for a in _FIXTURE_ASTEROIDS]
    _write_table(lakehouse, "asteroids.parquet", rows, _NEOWS_SCHEMA)
    return rows


def test_world_serves_renderer_neows_fields(mock_lakehouse: Path):
    _write_neows(mock_lakehouse)
    body = _world(_client_for(mock_lakehouse))
    assert body["world"]["neows_fields_not_in_dataset"] == []
    recs = _by_id(body)
    for neows_id, values in _NEOWS_ENRICHED.items():
        enc = recs[neows_id]["encounter"]
        for field in _WORLD_NEOWS_FIELDS:
            assert enc[field] == values[field], field
    plain = recs["2138971"]["encounter"]  # not enriched in the fixture: null, never defaulted
    assert all(plain[field] is None for field in _WORLD_NEOWS_FIELDS)


def test_world_encounter_contract_is_minimal(mock_client: TestClient):
    """The world gains only renderer-relevant fields; H and is_sentry_object stay profile-only."""
    for rec in _world(mock_client)["data"]:
        assert set(rec["encounter"]) == {
            "source", "closest_approach_date", "miss_distance_km", "is_potentially_hazardous",
            "close_approach_datetime", "relative_velocity_km_s", "estimated_diameter_min_km", "estimated_diameter_max_km"}
        assert set(rec) == {"neows_id", "name", "asteroid_key", "encounter", "resolution", "sbdb", "sentry",
                            "illustrative_direction"}


def test_profile_serves_neows_encounter_and_physical(mock_lakehouse: Path):
    _write_neows(mock_lakehouse)
    prof = _profile(_client_for(mock_lakehouse), _TW54_NEOWS)
    values = _NEOWS_ENRICHED[_TW54_NEOWS]
    enc = prof["encounter"]
    for field in ("close_approach_datetime", "close_approach_epoch_ms", "relative_velocity_km_s", "is_sentry_object"):
        assert enc[field] == values[field], field
    assert enc["availability"] == {"status": "available", "unavailable": {}}
    neows_physical = prof["neows_physical"]
    assert neows_physical["source"] == "nasa_neows"
    for field in ("absolute_magnitude_h", "estimated_diameter_min_km", "estimated_diameter_max_km"):
        assert neows_physical[field] == values[field], field
    assert neows_physical["availability"] == {"status": "available", "unavailable": {}}


def test_profile_keeps_neows_sbdb_and_sentry_values_separate(mock_lakehouse: Path):
    """H, diameter and velocity from three sources stay in three labelled places, never merged."""
    _write_neows(mock_lakehouse)
    prof = _profile(_client_for(mock_lakehouse), _TW54_NEOWS)
    assert prof["neows_physical"]["absolute_magnitude_h"] == 27.4       # NeoWs
    assert prof["physical"]["absolute_magnitude"] == 27.6               # JPL SBDB
    assert prof["sentry"]["assessment"]["absolute_magnitude"] == 27.55  # JPL Sentry
    assert (prof["neows_physical"]["estimated_diameter_min_km"], prof["neows_physical"]["estimated_diameter_max_km"]) == (
        0.0080270317, 0.0179489885)
    assert prof["physical"]["estimated_diameter_km"] is None            # SBDB has none for this object
    assert prof["sentry"]["assessment"]["estimated_diameter_km"] == 0.01
    assert prof["encounter"]["relative_velocity_km_s"] == 4.5115736676
    assert prof["sentry"]["assessment"]["v_infinity_km_s"] == pytest.approx(7.76209523793638)
    assert (prof["physical"]["source"], prof["neows_physical"]["source"], prof["encounter"]["source"],
            prof["sentry"]["source"]) == ("jpl_sbdb", "nasa_neows", "nasa_neows", "jpl_sentry")


def test_neows_is_sentry_object_does_not_drive_sentry_linkage(mock_lakehouse: Path):
    """Both disagreements are preserved: the NeoWs flag is reported, linkage alone decides sentry.status."""
    enriched = {**_NEOWS_ENRICHED,
                _TW54_NEOWS: {**_NEOWS_ENRICHED[_TW54_NEOWS], "is_sentry_object": False},
                "2138971": {"is_sentry_object": True}}
    _write_neows(mock_lakehouse, enriched)
    client = _client_for(mock_lakehouse)

    linked_but_flag_false = _profile(client, _TW54_NEOWS)
    assert linked_but_flag_false["encounter"]["is_sentry_object"] is False
    assert linked_but_flag_false["sentry"]["status"] == "available"
    assert linked_but_flag_false["sentry"]["assessment"]["impact_probability"] == pytest.approx(6.594578e-05)

    flag_true_but_unlinked = _profile(client, "2138971")
    assert flag_true_but_unlinked["encounter"]["is_sentry_object"] is True
    assert flag_true_but_unlinked["sentry"]["status"] == "not_resolved"
    assert flag_true_but_unlinked["sentry"]["assessment"]["availability"]["status"] == "unavailable"

    world = _by_id(_world(client))
    assert world[_TW54_NEOWS]["sentry"]["status"] == "available"
    assert world["2138971"]["sentry"]["status"] == "not_resolved"


def test_profile_null_neows_fields_report_not_in_source(mock_lakehouse: Path):
    _write_neows(mock_lakehouse, {_TW54_NEOWS: {"relative_velocity_km_s": 4.5, "is_sentry_object": None}})
    prof = _profile(_client_for(mock_lakehouse), _TW54_NEOWS)
    assert prof["encounter"]["relative_velocity_km_s"] == 4.5
    assert prof["encounter"]["is_sentry_object"] is None
    assert prof["encounter"]["availability"]["unavailable"] == {
        "close_approach_datetime": "not_in_source", "close_approach_epoch_ms": "not_in_source",
        "is_sentry_object": "not_in_source"}
    assert prof["neows_physical"]["availability"]["status"] == "unavailable"
    assert set(prof["neows_physical"]["availability"]["unavailable"].values()) == {"not_in_source"}


def test_dataset_predating_normalization_reports_not_in_current_contract(mock_lakehouse: Path):
    """A 5-column NeoWs Parquet (pre-Step 7) still serves; the new fields are null for a declared reason."""
    legacy_schema = pa.schema([_NEOWS_SCHEMA.field(n) for n in
                               ("id", "name", "closest_approach_date", "miss_distance_km", "hazardous")])
    _write_table(mock_lakehouse, "asteroids.parquet", _FIXTURE_ASTEROIDS, legacy_schema)
    client = _client_for(mock_lakehouse)

    body = _world(client)
    assert body["world"]["neows_fields_not_in_dataset"] == list(_NEOWS_NEW_FIELDS)
    assert all(rec["encounter"][f] is None for rec in body["data"] for f in _WORLD_NEOWS_FIELDS)

    prof = _profile(client, _TW54_NEOWS)
    assert prof["encounter"]["availability"]["unavailable"] == {
        f: "not_in_current_contract"
        for f in ("close_approach_datetime", "close_approach_epoch_ms", "relative_velocity_km_s", "is_sentry_object")}
    assert set(prof["neows_physical"]["availability"]["unavailable"].values()) == {"not_in_current_contract"}
    assert prof["identity"]["match_state"] == "RESOLVED" and prof["sentry"]["status"] == "available"

    executed, opened, served = _instrumented_world_call(mock_lakehouse)
    assert (opened, len(executed), served) == (1, 1, 35)


def test_world_still_one_connection_one_query_with_normalized_fields(mock_lakehouse: Path):
    _write_neows(mock_lakehouse)
    executed, opened, served = _instrumented_world_call(mock_lakehouse)
    assert (opened, len(executed), served) == (1, 1, 35)


@pytest.mark.skipif(
    not (_REAL_LAKEHOUSE / "asteroids.parquet").exists(),
    reason="Local Parquet lakehouse not present (gitignored; absent in CI).",
)
def test_real_lakehouse_serves_normalized_neows_fields():
    """The real processed dataset (re-derived from asteroids_raw.json) serves every normalized field."""
    import duckdb

    ast_path = str(_REAL_LAKEHOUSE / "asteroids.parquet").replace("\\", "/")
    con = duckdb.connect()
    columns = [r[0] for r in con.execute(f"DESCRIBE SELECT * FROM '{ast_path}'").fetchall()]
    if "relative_velocity_km_s" not in columns:
        pytest.skip("Local asteroids.parquet predates Step 7; run: python nasa_asteroids.py --from-raw asteroids_raw.json")
    cur = con.execute(f"SELECT * FROM '{ast_path}'")
    names = [d[0] for d in cur.description]
    stored = {row[0]: dict(zip(names, row)) for row in cur.fetchall()}

    client = _client_for(_REAL_LAKEHOUSE)
    body = _world(client)
    assert body["world"]["neows_fields_not_in_dataset"] == []
    for rec in body["data"]:
        src = stored[rec["neows_id"]]
        for field in _WORLD_NEOWS_FIELDS:
            assert rec["encounter"][field] == src[field], (rec["neows_id"], field)
            assert rec["encounter"][field] is not None
    for neows_id in (_TW54_NEOWS, _ST_NEOWS, "2138971"):
        prof = _profile(client, neows_id)
        assert prof["encounter"]["is_sentry_object"] is stored[neows_id]["is_sentry_object"]
        assert prof["encounter"]["close_approach_epoch_ms"] == stored[neows_id]["close_approach_epoch_ms"]
        assert prof["neows_physical"]["absolute_magnitude_h"] == stored[neows_id]["absolute_magnitude_h"]
        assert prof["neows_physical"]["availability"]["status"] == "available"


def test_serving_optional_neows_columns_match_ingestion_schema():
    """dashboard_data.NEOWS_OPTIONAL_COLUMNS must mirror the optional fields of nasa_asteroids.ASTEROID_SCHEMA."""
    from dashboard_data import NEOWS_OPTIONAL_COLUMNS

    arrow_to_sql = {pa.string(): "VARCHAR", pa.int64(): "BIGINT", pa.float64(): "DOUBLE", pa.bool_(): "BOOLEAN"}
    legacy = ("id", "name", "closest_approach_date", "miss_distance_km", "hazardous")
    ingestion = {f.name: arrow_to_sql[f.type] for f in _NEOWS_SCHEMA if f.name not in legacy}
    assert NEOWS_OPTIONAL_COLUMNS == ingestion


# ============================================================================
# PHASE 1 STEP 8 — FINAL CONTRACT INTEGRATION (renderer-facing guarantees)
# ============================================================================
# These tests pin the final world/profile contracts as a whole, rather than any
# single implementation step. A failure here means the renderer-facing contract changed.

_FINAL_WORLD_RECORD_SHAPE = {
    "neows_id": None, "name": None, "asteroid_key": None,
    "encounter": {"source", "closest_approach_date", "close_approach_datetime", "miss_distance_km",
                  "relative_velocity_km_s", "estimated_diameter_min_km", "estimated_diameter_max_km",
                  "is_potentially_hazardous"},
    "resolution": {"match_state", "match_rule", "resolved_at"},
    "sbdb": {"status", "spkid", "snapshot_key", "run_id"},
    "sentry": {"status", "sentry_id", "latest_snapshot_key", "run_id", "in_latest_catalog"},
    "illustrative_direction": {"x", "y", "z"},
}
_FINAL_WORLD_INFO_SHAPE = {
    "object_count": None, "encounter_selection_rule": None, "sentry_latest_catalog_snapshot_key": None,
    "neows_fields_not_in_dataset": None,
    "neows": {"source", "dataset_run_id", "source_raw_file", "source_raw_sha256"},
    "spatial_model": {"direction_semantics", "direction_algorithm", "direction_seed_field", "distance_field", "note"},
}
_FINAL_PROFILE_SECTIONS = {  # section -> declared source (None: section spans sources by design)
    "identity": None, "orbit": "jpl_sbdb", "physical": "jpl_sbdb", "neows_physical": "nasa_neows",
    "encounter": "nasa_neows", "sentry": "jpl_sentry", "provenance": None,
}
_FORBIDDEN_KEY_TOKENS = {"danger", "risk", "threat", "score", "energy", "solution", "solutions"}


def _is_forbidden_key(name: str) -> bool:
    """Whole-token match (so 'resolution' is fine, 'impact_solution' is not), plus impact dates."""
    lowered = name.lower()
    return bool(set(lowered.split("_")) & _FORBIDDEN_KEY_TOKENS) or "impact_date" in lowered


def _assert_shape(obj: dict, shape: dict) -> None:
    assert set(obj) == set(shape), set(obj) ^ set(shape)
    for key, sub in shape.items():
        if sub is not None:
            assert set(obj[key]) == sub, (key, set(obj[key]) ^ sub)


def _schema_property_names(openapi: dict, root: str) -> set[str]:
    """All property names reachable from one OpenAPI component schema."""
    schemas, seen, names, stack = openapi["components"]["schemas"], set(), set(), [root]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        for prop, spec in schemas[name].get("properties", {}).items():
            names.add(prop)
            for ref in _all_refs(spec):
                stack.append(ref.rsplit("/", 1)[-1])
    return names


def _all_refs(spec):
    if isinstance(spec, dict):
        for key, value in spec.items():
            if key == "$ref":
                yield value
            else:
                yield from _all_refs(value)
    elif isinstance(spec, list):
        for value in spec:
            yield from _all_refs(value)


# --- Final contract shapes ------------------------------------------------------------

def test_final_world_contract_shape(mock_lakehouse: Path):
    _write_neows(mock_lakehouse)
    body = _world(_client_for(mock_lakehouse))
    assert set(body) == {"meta", "world", "data"}
    _assert_shape(body["world"], _FINAL_WORLD_INFO_SHAPE)
    assert body["world"]["spatial_model"]["direction_semantics"] == "illustrative"
    for rec in body["data"]:
        _assert_shape(rec, _FINAL_WORLD_RECORD_SHAPE)
        assert rec["encounter"]["source"] == "nasa_neows"


def test_final_profile_contract_sections_and_sources(mock_lakehouse: Path):
    _write_neows(mock_lakehouse)
    client = _client_for(mock_lakehouse)
    for neows_id in (_TW54_NEOWS, "2138971"):  # resolved+linked, and unresolved
        prof = _profile(client, neows_id)
        assert set(prof) == {"neows_id", *_FINAL_PROFILE_SECTIONS}
        for section, source in _FINAL_PROFILE_SECTIONS.items():
            if source is not None:
                assert prof[section]["source"] == source, section
        assert {k: v["source"] for k, v in prof["provenance"].items()} == {
            "neows": "nasa_neows", "resolution": "entity_resolution", "sbdb": "jpl_sbdb", "sentry": "jpl_sentry"}
        assert prof["sentry"]["source_contract"] == "sentry_mode_s_summary"
        for section in ("identity", "orbit", "physical", "neows_physical", "encounter"):
            assert prof[section]["availability"]["status"] in {"available", "partial", "unavailable"}
        assert prof["sentry"]["assessment"]["availability"]["status"] in {"available", "partial", "unavailable"}


def test_final_contracts_expose_no_synthetic_or_mode_o_fields(mock_client: TestClient):
    """Regression guard over the published OpenAPI schemas of the renderer-facing contracts."""
    openapi = mock_client.get("/openapi.json").json()
    for root in ("WorldResponse", "AsteroidProfileResponse"):
        names = _schema_property_names(openapi, root)
        offending = {n for n in names if _is_forbidden_key(n)}
        assert not offending, (root, offending)
    world_names = _schema_property_names(openapi, "WorldResponse")
    assert not world_names & {"impact_probability", "palermo_scale_max", "torino_scale_max"}, \
        "Sentry assessment values belong to the profile, not the world"


def test_final_routing_matrix(mock_client: TestClient):
    """World is never captured by the ID routes; malformed IDs are 422, unknown well-formed IDs 404."""
    assert mock_client.get("/asteroids/world").status_code == 200
    for route in ("/asteroids/{}", "/asteroids/{}/profile", "/asteroids/{}/sentry"):
        assert mock_client.get(route.format("0")).status_code == 422
        assert mock_client.get(route.format("world")).status_code == 422 or route == "/asteroids/{}"
        assert mock_client.get(route.format("99999999")).status_code == 404
    assert mock_client.get("/asteroids/world/profile").status_code == 422


# --- Integration bugs fixed in Step 8 ---------------------------------------------------

def test_legacy_routes_serve_unknown_pha_as_null(mock_lakehouse: Path):
    """Previously GET /asteroids and /asteroids/{id} returned 500 for an unknown NeoWs PHA flag,
    while world/profile served null. All routes now agree: unknown stays null."""
    rows = [{**a, "hazardous": None} if a["id"] == "2138971" else a for a in _FIXTURE_ASTEROIDS]
    _write_table(mock_lakehouse, "asteroids.parquet", rows, ASTEROID_SCHEMA)
    client = _client_for(mock_lakehouse)

    listed = {r["neows_id"]: r for r in client.get("/asteroids", params={"limit": 500}).json()["data"]}
    assert listed["2138971"]["hazardous"] is None
    detail = client.get("/asteroids/2138971")
    assert detail.status_code == 200 and detail.json()["data"]["hazardous"] is None
    assert _by_id(_world(client))["2138971"]["encounter"]["is_potentially_hazardous"] is None
    for flag in ("true", "false"):  # unknown is neither
        ids = {r["neows_id"] for r in client.get("/asteroids", params={"limit": 500, "hazardous": flag}).json()["data"]}
        assert "2138971" not in ids


def test_neows_lineage_served_identically_by_world_and_profile(mock_lakehouse: Path):
    """An offline re-derived dataset exposes its raw snapshot hash through both contracts."""
    import hashlib
    import json as _json
    import nasa_asteroids

    raw = mock_lakehouse / "asteroids_raw.json"
    raw.write_text(_json.dumps({"near_earth_objects": {"2026-09-30": [{
        "id": _TW54_NEOWS, "name": "(2010 TW54)", "is_potentially_hazardous_asteroid": False,
        "absolute_magnitude_h": 27.6, "is_sentry_object": True,
        "estimated_diameter": {"kilometers": {"estimated_diameter_min": 0.008, "estimated_diameter_max": 0.018}},
        "close_approach_data": [{"close_approach_date": "2026-09-30", "close_approach_date_full": "2026-Sep-30 05:42",
                                 "epoch_date_close_approach": 1790746920000,
                                 "relative_velocity": {"kilometers_per_second": "4.5"},
                                 "miss_distance": {"kilometers": "17457205.45181"}}],
    }]}}), encoding="utf-8")
    raw_sha = hashlib.sha256(raw.read_bytes()).hexdigest()
    assert nasa_asteroids.reprocess_raw_snapshot(str(raw), str(mock_lakehouse / "asteroids.parquet")) == 0
    client = _client_for(mock_lakehouse)

    lineage = {"source": "nasa_neows", "dataset_run_id": None, "source_raw_file": "asteroids_raw.json",
               "source_raw_sha256": raw_sha}
    assert _world(client)["world"]["neows"] == lineage
    assert _profile(client, _TW54_NEOWS)["provenance"]["neows"] == lineage


# --- Full real-data validation -------------------------------------------------------------

@pytest.mark.skipif(
    not (_REAL_LAKEHOUSE / "asteroids.parquet").exists(),
    reason="Local Parquet lakehouse not present (gitignored; absent in CI).",
)
def test_final_contract_real_data_end_to_end():
    """Every real NeoWs object: world and profile agree, sources stay separated, all values trace to storage."""
    import duckdb
    import hashlib

    lake = _REAL_LAKEHOUSE
    con = duckdb.connect()

    def rows(sql: str, params=()):
        cur = con.execute(sql, list(params))
        names = [d[0] for d in cur.description]
        return [dict(zip(names, r)) for r in cur.fetchall()]

    def p(name: str) -> str:
        return str(lake / name).replace("\\", "/")
    neows = {r["id"]: r for r in rows(f"SELECT * FROM '{p('asteroids.parquet')}'")}
    if "relative_velocity_km_s" not in next(iter(neows.values())):
        pytest.skip("Local asteroids.parquet predates Step 7; run: python nasa_asteroids.py --from-raw asteroids_raw.json")

    client = _client_for(lake)
    body = _world(client)
    world = _by_id(body)
    assert set(world) == set(neows) and body["world"]["object_count"] == len(neows)

    raw_path = lake / "asteroids_raw.json"
    if body["world"]["neows"]["source_raw_sha256"] is not None and raw_path.exists():
        assert body["world"]["neows"]["source_raw_sha256"] == hashlib.sha256(raw_path.read_bytes()).hexdigest()

    resolved, linked = set(), set()
    for neows_id, rec in world.items():
        src, enc = neows[neows_id], rec["encounter"]
        # NeoWs facts trace to the stored NeoWs row, internally consistent.
        for field in ("closest_approach_date", "close_approach_datetime", "miss_distance_km",
                      "relative_velocity_km_s", "estimated_diameter_min_km", "estimated_diameter_max_km"):
            assert enc[field] == src[field], (neows_id, field)
        assert enc["is_potentially_hazardous"] is src["hazardous"]
        assert enc["close_approach_datetime"][:10] == enc["closest_approach_date"]
        assert enc["estimated_diameter_min_km"] <= enc["estimated_diameter_max_km"]
        d = rec["illustrative_direction"]
        assert (d["x"], d["y"], d["z"]) == illustrative_direction(neows_id)

        prof = _profile(client, neows_id)
        assert prof["identity"]["asteroid_key"] == rec["asteroid_key"]
        assert prof["identity"]["match_state"] == rec["resolution"]["match_state"]
        assert prof["encounter"]["miss_distance_km"] == enc["miss_distance_km"]
        assert prof["encounter"]["close_approach_datetime"] == enc["close_approach_datetime"]
        assert prof["encounter"]["is_sentry_object"] is src["is_sentry_object"]
        assert prof["neows_physical"]["absolute_magnitude_h"] == src["absolute_magnitude_h"]
        assert prof["sentry"]["status"] == rec["sentry"]["status"]
        assert prof["provenance"]["sbdb"]["run_id"] == rec["sbdb"]["run_id"]
        assert prof["provenance"]["neows"] == body["world"]["neows"]

        if rec["resolution"]["match_state"] == "RESOLVED":
            resolved.add(neows_id)
            # SBDB: every orbit element served equals the single stored snapshot's row.
            spkid, run_id = rec["sbdb"]["spkid"], rec["sbdb"]["run_id"]
            elements = {r["element_name"]: r["element_value"] for r in rows(
                f"SELECT element_name, element_value FROM '{p('fact_sbdb_orbit_element.parquet')}' "
                "WHERE spkid = ? AND run_id = ?", (spkid, run_id))}
            for field, element in _SBDB_ELEMENT_FIELDS.items():
                assert prof["orbit"][field] == elements[element], (neows_id, field)
            sbdb_h = rows(f"SELECT param_value_numeric FROM '{p('fact_sbdb_physical_parameter.parquet')}' "
                          "WHERE spkid = ? AND run_id = ? AND param_name = 'H'", (spkid, run_id))
            assert prof["physical"]["absolute_magnitude"] == sbdb_h[0]["param_value_numeric"]
        if rec["sentry"]["status"] == "available":
            linked.add(neows_id)
            sentry_row = rows(f"SELECT * FROM '{p('fact_sentry_risk_snapshot.parquet')}' "
                              "WHERE sentry_id = ? AND snapshot_key = ? AND run_id = ?",
                              (rec["sentry"]["sentry_id"], rec["sentry"]["latest_snapshot_key"], rec["sentry"]["run_id"]))
            assert len(sentry_row) == 1
            _assert_assessment_equals_record(prof["sentry"]["assessment"], sentry_row[0])
            # Three sources, three places: NeoWs H, SBDB H and Sentry H are each served from their own table.
            assert prof["sentry"]["assessment"]["absolute_magnitude"] == sentry_row[0]["absolute_magnitude"]
            assert prof["encounter"]["relative_velocity_km_s"] == src["relative_velocity_km_s"]
            assert prof["sentry"]["assessment"]["v_infinity_km_s"] == sentry_row[0]["v_infinity_km_s"]
        else:
            assert all(v is None for k, v in prof["sentry"]["assessment"].items() if k != "availability")

    # Present-day facts of the local dataset (not architectural assumptions).
    assert resolved == linked == {_TW54_NEOWS, _ST_NEOWS}


@pytest.mark.skipif(
    not (_REAL_LAKEHOUSE / "asteroids.parquet").exists(),
    reason="Local Parquet lakehouse not present (gitignored; absent in CI).",
)
def test_final_query_budget_on_real_data():
    """World: 1 connection, 1 query. Profile: 3 connections resolved, 1 connection + 1 query unresolved."""
    executed, opened, served = _instrumented_world_call(_REAL_LAKEHOUSE)
    assert (opened, len(executed)) == (1, 1) and served > 0

    counts = []
    real_connect = LocalDuckDBDataProvider._get_connection
    for neows_id in (_TW54_NEOWS, "2138971"):
        log: list[str] = []
        opened_box = [0]

        def counting_connect(self, _log=log, _box=opened_box):
            _box[0] += 1
            return _CountingConnection(real_connect(self), _log)

        with patch.object(LocalDuckDBDataProvider, "_get_connection", counting_connect):
            _profile(_client_for(_REAL_LAKEHOUSE), neows_id)
        counts.append((opened_box[0], len(log)))
    assert counts[0][0] == 3
    assert counts[1] == (1, 1)


def test_forbidden_key_guard_is_token_based():
    assert _is_forbidden_key("impact_solution_count") and _is_forbidden_key("danger_score")
    assert _is_forbidden_key("impact_energy_mt") and _is_forbidden_key("impact_dates")
    assert not _is_forbidden_key("resolution") and not _is_forbidden_key("match_rule")
