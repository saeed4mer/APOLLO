"""Tests for Phase 9 Data Access Layer (dashboard_data.py).

Validates:
A. Watchlist contract & event grain (closest_approach_date, neows_id)
B. Identifier separation (NeoWs ID != SBDB SPK-ID, UUID5 format, source namespaces)
C. Entity resolution states (RESOLVED, UNRESOLVED, AMBIGUOUS, INVALID)
D. Null safety across uncharacterized and unmonitored targets
E. Sentry reverse cardinality defense (ambiguity flags, scalar metric suppression)
F. Historical risk grain and snapshot sequencing
G. Scientific safety (zero danger scores, non-causal reporting)
H. No data fabrication (no synthetic velocity, no invented physical parameters)
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from dashboard_data import UUID5_PATTERN, DashboardDataProvider, LocalDuckDBDataProvider

PROJ_DIR = Path(__file__).resolve().parent


@pytest.fixture
def provider() -> DashboardDataProvider:
    """Default local provider pointing to actual lakehouse Parquet files."""
    return DashboardDataProvider(base_dir=PROJ_DIR, execution_mode="LOCAL")


# ============================================================================
# A. WATCHLIST CONTRACT & GRAIN TESTS
# ============================================================================


def test_watchlist_contract_and_columns(provider: DashboardDataProvider):
    """Verify threat watchlist returns all required operational columns."""
    df = provider.get_threat_watchlist()
    assert isinstance(df, pd.DataFrame)
    assert not df.empty, "Watchlist should contain active close-approach rows."

    expected_cols = [
        "closest_approach_date",
        "neows_id",
        "name",
        "miss_distance_km",
        "miss_distance_lunar",
        "hazardous",
        "asteroid_key",
        "match_state",
        "is_sentry_monitored",
        "is_sentry_ambiguous",
        "sentry_id",
        "sentry_impact_probability",
        "sentry_palermo_scale_max",
        "sentry_torino_scale_max",
        "sentry_potential_impacts_count",
        "sentry_impact_year_range",
        "has_sbdb_characterization",
        "sbdb_spkid",
        "sbdb_designation",
        "sbdb_fullname",
        "sbdb_orbit_class_name",
    ]
    for col in expected_cols:
        assert col in df.columns, f"Column '{col}' missing from threat watchlist."


def test_watchlist_grain_uniqueness(provider: DashboardDataProvider):
    """Verify grain is strictly (closest_approach_date, neows_id) with no duplicates."""
    df = provider.get_threat_watchlist()
    grain_keys = list(zip(df["closest_approach_date"], df["neows_id"]))
    assert len(grain_keys) == len(set(grain_keys)), "Duplicate (closest_approach_date, neows_id) in watchlist."


def test_watchlist_no_synthetic_velocity(provider: DashboardDataProvider):
    """Verify watchlist does not fabricate relative velocity when absent from NeoWs data."""
    df = provider.get_threat_watchlist()
    # asteroids.parquet has no velocity column; verify it is not manufactured in watchlist
    assert "relative_velocity_km_s" not in df.columns
    assert "velocity_km_s" not in df.columns


# ============================================================================
# B. IDENTIFIER SEPARATION & NAMESPACE INTEGRITY
# ============================================================================


def test_identifier_separation_neows_vs_spkid(provider: DashboardDataProvider):
    """Verify NeoWs ID is strictly isolated from SBDB SPK-ID."""
    # (2010 TW54) has NeoWs ID '3548666' and SBDB SPK-ID '50548689'
    df = provider.get_threat_watchlist()
    tw54_row = df[df["name"].str.contains("2010 TW54", na=False)]
    if not tw54_row.empty:
        neows_id = tw54_row.iloc[0]["neows_id"]
        assert neows_id == "3548666"
        assert neows_id != "50548689", "NeoWs ID must not be conflated with SBDB SPK-ID!"


def test_asteroid_key_uuid5_format(provider: DashboardDataProvider):
    """Verify resolved asteroid_key adheres strictly to platform UUID5 format."""
    # ast_8520aaac-9c77-5e8f-9a88-b4e501749e26
    profile = provider.get_sbdb_profile("ast_8520aaac-9c77-5e8f-9a88-b4e501749e26")
    assert profile is not None
    key = profile["asteroid_key"]
    assert UUID5_PATTERN.match(key), f"asteroid_key '{key}' does not match UUID5 pattern."


def test_crosswalk_namespace_separation(provider: DashboardDataProvider):
    """Verify crosswalk table separates source systems into distinct namespaces."""
    df = provider.get_crosswalk("ast_8520aaac-9c77-5e8f-9a88-b4e501749e26")
    assert not df.empty
    assert "source_system" in df.columns
    assert "identifier_name" in df.columns
    assert "identifier_value" in df.columns

    systems = set(df["source_system"].unique())
    assert "sbdb" in systems
    assert "sentry" in systems

    # SBDB SPKID is marked as primary pivot
    spkid_row = df[(df["source_system"] == "sbdb") & (df["identifier_name"] == "spkid")]
    assert not spkid_row.empty
    assert bool(spkid_row.iloc[0]["is_primary_pivot"]) is True


# ============================================================================
# C. ENTITY RESOLUTION STATES (RESOLVED, UNRESOLVED, AMBIGUOUS, INVALID)
# ============================================================================


def test_resolution_state_unresolved(provider: DashboardDataProvider):
    """Verify unmapped NeoWs targets return UNRESOLVED state with None key."""
    res = provider.get_resolution_state("3548666")
    assert res["match_state"] == "UNRESOLVED"
    assert res["asteroid_key"] is None
    assert "neows_id" in res
    assert res["neows_id"] == "3548666"


def test_resolution_state_invalid_inputs(provider: DashboardDataProvider):
    """Verify invalid or malformed NeoWs IDs return INVALID state."""
    # None
    res1 = provider.get_resolution_state(None)
    assert res1["match_state"] == "INVALID"
    assert res1["asteroid_key"] is None

    # Empty string
    res2 = provider.get_resolution_state("   ")
    assert res2["match_state"] == "INVALID"
    assert res2["asteroid_key"] is None

    # Non-numeric string
    res3 = provider.get_resolution_state("invalid_id_123")
    assert res3["match_state"] == "INVALID"
    assert res3["asteroid_key"] is None


def test_resolution_state_ambiguous_synthetic(tmp_path: Path):
    """Verify ambiguous multiple-key bridge mappings return AMBIGUOUS with None key."""
    # Create synthetic bridge fixture with 2 conflicting keys for same NeoWs ID
    bridge_df = pd.DataFrame(
        [
            {
                "asteroid_key": "ast_11111111-1111-5111-8111-111111111111",
                "source_system": "neows",
                "identifier_name": "id",
                "identifier_value": "9999999",
                "is_primary_pivot": False,
                "created_at": "2026-09-27T00:00:00Z",
                "updated_at": "2026-09-27T00:00:00Z",
            },
            {
                "asteroid_key": "ast_22222222-2222-5222-8222-222222222222",
                "source_system": "neows",
                "identifier_name": "id",
                "identifier_value": "9999999",
                "is_primary_pivot": False,
                "created_at": "2026-09-27T00:00:00Z",
                "updated_at": "2026-09-27T00:00:00Z",
            },
        ]
    )
    bridge_path = tmp_path / "bridge_asteroid_identifier.parquet"
    bridge_df.to_parquet(bridge_path)

    # Instantiate local provider pointed at tmp_path
    local_p = LocalDuckDBDataProvider(base_dir=tmp_path)
    res = local_p.get_resolution_state("9999999")
    assert res["match_state"] == "AMBIGUOUS"
    assert res["asteroid_key"] is None, "Ambiguous resolution must not arbitrarily choose an asteroid_key!"


def test_resolution_state_resolved_synthetic(tmp_path: Path):
    """Verify deterministic single-key bridge mapping returns RESOLVED."""
    bridge_df = pd.DataFrame(
        [
            {
                "asteroid_key": "ast_33333333-3333-5333-8333-333333333333",
                "source_system": "neows",
                "identifier_name": "id",
                "identifier_value": "8888888",
                "is_primary_pivot": False,
                "created_at": "2026-09-27T00:00:00Z",
                "updated_at": "2026-09-27T00:00:00Z",
            }
        ]
    )
    bridge_df.to_parquet(tmp_path / "bridge_asteroid_identifier.parquet")

    local_p = LocalDuckDBDataProvider(base_dir=tmp_path)
    res = local_p.get_resolution_state("8888888")
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_33333333-3333-5333-8333-333333333333"


# ============================================================================
# D. NULL SAFETY & MISSINGNESS
# ============================================================================


def test_sbdb_profile_null_key(provider: DashboardDataProvider):
    """Verify get_sbdb_profile returns None when key is missing or unmapped."""
    assert provider.get_sbdb_profile(None) is None
    assert provider.get_sbdb_profile("") is None
    assert provider.get_sbdb_profile("non_existent_key") is None


def test_sbdb_profile_physical_null_preservation(provider: DashboardDataProvider):
    """Verify physical parameters preserve None when not present in source catalog."""
    # 2008 ST has H magnitude, but diameter and albedo are null in source snapshot
    profile = provider.get_sbdb_profile("ast_8520aaac-9c77-5e8f-9a88-b4e501749e26")
    assert profile is not None
    assert profile["absolute_magnitude"] == 27.1
    assert profile["estimated_diameter_km"] is None, "Missing diameter must be None, not 0.0!"
    assert profile["albedo"] is None, "Missing albedo must be None, not 0.0!"


def test_sentry_profile_unmonitored_key(provider: DashboardDataProvider):
    """Verify unmonitored entity returns clean inactive structure without raising."""
    assert provider.get_sentry_profile(None) is None
    assert provider.get_sentry_profile("") is None

    # Unmonitored key returns inactive structure
    profile = provider.get_sentry_profile("non_existent_key")
    assert profile is not None
    assert profile["has_sentry_monitoring"] is False
    assert profile["is_sentry_ambiguous"] is False
    assert profile["sentry_id"] is None
    assert profile["latest_impact_probability"] is None


def test_historical_risk_empty_id(provider: DashboardDataProvider):
    """Verify get_historical_risk returns empty DataFrame for None or missing sentry_id."""
    df1 = provider.get_historical_risk(None)
    assert isinstance(df1, pd.DataFrame)
    assert df1.empty
    assert "impact_probability" in df1.columns

    df2 = provider.get_historical_risk("non_existent_sentry_id_xyz")
    assert isinstance(df2, pd.DataFrame)
    assert df2.empty


# ============================================================================
# E. SENTRY CARDINALITY & AMBIGUITY DEFENSE
# ============================================================================


def test_sentry_ambiguity_defense(tmp_path: Path):
    """Verify multiple Sentry IDs linked to 1 asteroid_key suppress scalar metrics."""
    # Create synthetic bridge with 2 Sentry IDs for 1 asteroid key
    bridge_df = pd.DataFrame(
        [
            {
                "asteroid_key": "ast_44444444-4444-5444-8444-444444444444",
                "source_system": "sentry",
                "identifier_name": "sentry_id",
                "identifier_value": "sentry_obj_1",
                "is_primary_pivot": False,
                "created_at": "2026-09-27T00:00:00Z",
                "updated_at": "2026-09-27T00:00:00Z",
            },
            {
                "asteroid_key": "ast_44444444-4444-5444-8444-444444444444",
                "source_system": "sentry",
                "identifier_name": "sentry_id",
                "identifier_value": "sentry_obj_2",
                "is_primary_pivot": False,
                "created_at": "2026-09-27T00:00:00Z",
                "updated_at": "2026-09-27T00:00:00Z",
            },
        ]
    )
    bridge_df.to_parquet(tmp_path / "bridge_asteroid_identifier.parquet")

    local_p = LocalDuckDBDataProvider(base_dir=tmp_path)
    profile = local_p.get_sentry_profile("ast_44444444-4444-5444-8444-444444444444")
    assert profile is not None
    assert profile["has_sentry_monitoring"] is True
    assert profile["is_sentry_ambiguous"] is True
    assert profile["sentry_identifier_count"] == 2
    # All scalar risk metrics MUST be None when ambiguous!
    assert profile["sentry_id"] is None
    assert profile["latest_impact_probability"] is None
    assert profile["latest_palermo_scale_max"] is None


def test_sentry_single_id_resolution(provider: DashboardDataProvider):
    """Verify exactly one Sentry ID populates verified scalar risk metrics."""
    # ast_8520aaac-9c77-5e8f-9a88-b4e501749e26 maps to exactly 1 Sentry ID: bK08S00T
    profile = provider.get_sentry_profile("ast_8520aaac-9c77-5e8f-9a88-b4e501749e26")
    assert profile is not None
    assert profile["has_sentry_monitoring"] is True
    assert profile["is_sentry_ambiguous"] is False
    assert profile["sentry_identifier_count"] == 1
    assert profile["sentry_id"] == "bK08S00T"
    assert profile["latest_impact_probability"] is not None
    assert profile["latest_impact_probability"] > 0
    assert profile["latest_palermo_scale_max"] is not None
    assert profile["v_infinity_km_s"] is not None


# ============================================================================
# F. HISTORICAL RISK GRAIN & SNAPSHOT SEQUENCING
# ============================================================================


def test_historical_risk_grain_and_change_flags(tmp_path: Path):
    """Verify historical risk grain (snapshot_key, sentry_id) and non-causal change flags."""
    # Create multi-snapshot risk dataset for 1 sentry_id
    snap_df = pd.DataFrame(
        [
            {
                "snapshot_key": "2026-09-10",
                "run_id": "r1",
                "snapshot_time": "2026-09-10T00:00:00Z",
                "sentry_id": "test_sentry_obj",
                "designation": "2026 TS1",
                "fullname": "(2026 TS1)",
                "absolute_magnitude": 22.1,
                "estimated_diameter_km": 0.12,
                "impact_probability": 1.0e-4,
                "potential_impacts_count": 5,
                "palermo_scale_cum": -2.8,
                "palermo_scale_max": -3.1,
                "torino_scale_max": 0,
                "v_infinity_km_s": 14.5,
                "impact_year_range": "2045-2080",
                "last_obs_date": "2026-09-09",
                "last_obs_jd": 2461000.5,
            },
            {
                "snapshot_key": "2026-09-18",
                "run_id": "r2",
                "snapshot_time": "2026-09-18T00:00:00Z",
                "sentry_id": "test_sentry_obj",
                "designation": "2026 TS1",
                "fullname": "(2026 TS1)",
                "absolute_magnitude": 22.1,
                "estimated_diameter_km": 0.12,
                "impact_probability": 2.5e-5,  # Changed
                "potential_impacts_count": 3,
                "palermo_scale_cum": -3.2,
                "palermo_scale_max": -3.1,  # Unchanged
                "torino_scale_max": 0,
                "v_infinity_km_s": 14.5,
                "impact_year_range": "2045-2080",
                "last_obs_date": "2026-09-17",
                "last_obs_jd": 2461008.5,
            },
        ]
    )
    snap_df.to_parquet(tmp_path / "fact_sentry_risk_snapshot.parquet")

    local_p = LocalDuckDBDataProvider(base_dir=tmp_path)
    hist = local_p.get_historical_risk("test_sentry_obj")
    assert len(hist) == 2

    # First row has no previous row -> change flags False
    assert bool(hist.iloc[0]["is_impact_probability_changed"]) is False
    assert bool(hist.iloc[0]["is_palermo_scale_max_changed"]) is False

    # Second row has changed IP -> is_impact_probability_changed is True
    assert bool(hist.iloc[1]["is_impact_probability_changed"]) is True
    # Second row has identical palermo -> is_palermo_scale_max_changed is False
    assert bool(hist.iloc[1]["is_palermo_scale_max_changed"]) is False


# ============================================================================
# G. SCIENTIFIC SAFETY & PROHIBITED TERMS
# ============================================================================


def test_scientific_safety_no_prohibited_terms():
    """Verify data access layer contains zero synthetic risk score terms."""
    src_path = PROJ_DIR / "dashboard_data.py"
    with open(src_path, encoding="utf-8") as f:
        code = f.read().lower()

    prohibited = [
        "danger_score",
        "danger score",
        "threat_index",
        "threat index",
        "lethality",
        "became safer",
        "became more dangerous",
        "orbit refinement",  # Must not claim causal mechanisms in code
    ]
    for term in prohibited:
        assert term not in code, f"Prohibited unscientific term '{term}' found in dashboard_data.py!"


# ============================================================================
# H. ATHENA PROVIDER NOTIMPLEMENTED GUARD
# ============================================================================


def test_athena_provider_offline_guard():
    """Verify Athena provider cleanly raises NotImplementedError during offline step."""
    athena_p = DashboardDataProvider(execution_mode="ATHENA")
    assert "ATHENA" in athena_p.get_execution_mode()

    with pytest.raises(NotImplementedError):
        athena_p.get_threat_watchlist()

    with pytest.raises(NotImplementedError):
        athena_p.get_resolution_state("3548666")
