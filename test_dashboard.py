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
from unittest.mock import patch

import pandas as pd
import pytest

from dashboard import (
    POPULATION_FILTER_MODES,
    build_canvas_payload,
    filter_population,
    render_crosswalk_tab,
    render_history_tab,
    render_overview_tab,
    render_sbdb_tab,
    render_sentry_tab,
    scene_html,
)
from dashboard_data import UUID5_PATTERN, DashboardDataProvider, LocalDuckDBDataProvider
from streamlit.testing.v1 import AppTest

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
    res = provider.get_resolution_state("9999999")
    assert res["match_state"] == "UNRESOLVED"
    assert res["asteroid_key"] is None
    assert "neows_id" in res
    assert res["neows_id"] == "9999999"


def test_resolution_state_resolved_2010_tw54(provider: DashboardDataProvider):
    """Verify resolved NeoWs target 3548666 (2010 TW54) returns RESOLVED with canonical key."""
    res = provider.get_resolution_state("3548666")
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert res["neows_id"] == "3548666"
    assert res["match_rule"] == "EXACT_DESIGNATION_MATCH"


def test_resolution_state_resolved_2008_st(provider: DashboardDataProvider):
    """Verify resolved NeoWs target 3427460 (2008 ST) returns RESOLVED with canonical key."""
    res = provider.get_resolution_state("3427460")
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_8520aaac-9c77-5e8f-9a88-b4e501749e26"
    assert res["neows_id"] == "3427460"
    assert res["match_rule"] == "EXACT_DESIGNATION_MATCH"


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


def test_resolution_state_authoritative_rule_priority_over_bridge(tmp_path: Path):
    """Regression test: verify fact_entity_resolution rule takes priority over bridge fallback."""
    # Create fact_entity_resolution fixture
    res_df = pd.DataFrame(
        [
            {
                "resolution_run_id": "run_1",
                "resolved_at": "2026-09-28T01:15:26.842071+00:00",
                "source_system": "neows",
                "identifier_name": "id",
                "source_identifier_value": "7777777",
                "matched_target_system": "sbdb",
                "matched_target_identifier_name": "des",
                "matched_target_identifier_value": "2026 TEST",
                "assigned_asteroid_key": "ast_77777777-7777-5777-8777-777777777777",
                "match_state": "RESOLVED",
                "match_rule": "EXACT_DESIGNATION_MATCH",
                "evidence_json": '{"rule": "EXACT_DESIGNATION_MATCH"}',
            }
        ]
    )
    res_df.to_parquet(tmp_path / "fact_entity_resolution.parquet")

    # Create bridge fixture mapping same identifier
    bridge_df = pd.DataFrame(
        [
            {
                "asteroid_key": "ast_77777777-7777-5777-8777-777777777777",
                "source_system": "neows",
                "identifier_name": "id",
                "identifier_value": "7777777",
                "is_primary_pivot": False,
                "created_at": "2026-09-28T01:15:26.842071+00:00",
                "updated_at": "2026-09-28T01:15:26.842071+00:00",
            }
        ]
    )
    bridge_df.to_parquet(tmp_path / "bridge_asteroid_identifier.parquet")

    local_p = LocalDuckDBDataProvider(base_dir=tmp_path)
    res = local_p.get_resolution_state("7777777")
    assert res["match_state"] == "RESOLVED"
    assert res["asteroid_key"] == "ast_77777777-7777-5777-8777-777777777777"
    # MUST return authoritative audit rule, NEVER synthetic BRIDGE_EXACT_NEOWS_ID
    assert res["match_rule"] == "EXACT_DESIGNATION_MATCH"
    assert res["match_rule"] != "BRIDGE_EXACT_NEOWS_ID"
    assert "EXACT_DESIGNATION_MATCH" in res["evidence"]


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


# ============================================================================
# I. DASHBOARD UI DOSSIER RENDERERS & ACCEPTANCE TESTS
# ============================================================================


def test_canvas_payload_contract(provider: DashboardDataProvider):
    """Verify build_canvas_payload returns dictionaries matching Canvas scene contract."""
    df = provider.get_threat_watchlist()
    payload = build_canvas_payload(df)
    assert isinstance(payload, list)
    assert len(payload) == len(df)

    expected_keys = {
        "id",
        "neows_id",
        "name",
        "hazardous",
        "miss_distance_km",
        "closest_approach_date",
        "is_sentry_monitored",
        "has_sbdb_characterization",
        "asteroid_key",
    }
    for item in payload:
        assert expected_keys.issubset(item.keys())
        assert isinstance(item["id"], str)
        assert isinstance(item["hazardous"], bool)
        assert isinstance(item["miss_distance_km"], float)


def test_scene_html_canvas_contract():
    """Verify scene_html generates self-contained canvas with no external CDN dependency."""
    objects = [
        {
            "id": "3548666",
            "neows_id": "3548666",
            "name": "(2010 TW54)",
            "hazardous": False,
            "miss_distance_km": 17457205.45,
            "closest_approach_date": "2026-09-30",
            "is_sentry_monitored": True,
            "has_sbdb_characterization": True,
            "asteroid_key": "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c",
        }
    ]
    html = scene_html(objects, active_neows_id="3548666")
    assert '<canvas id="space"></canvas>' in html
    assert "3548666" in html
    assert "2010 TW54" in html
    # Must have no external CDN scripts
    assert "https://" not in html
    assert "http://" not in html


def test_render_overview_tab_resolved_2010_tw54(provider: DashboardDataProvider):
    """Verify overview tab renders complete close-approach encounter metrics."""
    df = provider.get_threat_watchlist()
    tw54_row = df[df["neows_id"] == "3548666"]
    res = provider.get_resolution_state("3548666")
    sentry_prof = provider.get_sentry_profile(res["asteroid_key"])
    sbdb_prof = provider.get_sbdb_profile(res["asteroid_key"])

    with patch("streamlit.markdown") as mock_md:
        render_overview_tab(
            target_row=tw54_row,
            res=res,
            selected_id="3548666",
            target_name="(2010 TW54)",
            sentry_profile=sentry_prof,
            sbdb_profile=sbdb_prof,
        )
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])

        assert "Close-Approach Encounter Summary" in rendered_text
        assert "2026-09-30" in rendered_text
        assert "Potentially Hazardous: NO" in rendered_text
        # Velocity unavailable in contract -> must NOT be manufactured
        assert "Unavailable" in rendered_text
        assert "Not reported in telemetry contract" in rendered_text
        # Sentry monitored indicator
        assert "Monitored (ID: bK10T54W)" in rendered_text
        # SBDB characterized indicator
        assert "Characterized (SPK-ID: 50548689)" in rendered_text


def test_render_overview_tab_unresolved_target(provider: DashboardDataProvider):
    """Verify overview tab renders safely for unresolved target without errors."""
    df = provider.get_threat_watchlist()
    unres_row = df[df["neows_id"] == "2523934"]
    res = provider.get_resolution_state("2523934")

    with patch("streamlit.markdown") as mock_md:
        render_overview_tab(
            target_row=unres_row,
            res=res,
            selected_id="2523934",
            target_name="523934 (1998 FF14)",
            sentry_profile=None,
            sbdb_profile=None,
        )
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])

        assert "Close-Approach Encounter Summary" in rendered_text
        assert "UNRESOLVED" in rendered_text
        assert "Potentially Hazardous: YES" in rendered_text
        assert "None (Unresolved in lakehouse crosswalk)" in rendered_text
        assert "Not Monitored in Sentry" in rendered_text
        assert "Not Characterized in Current SBDB Snapshot" in rendered_text


def test_render_sbdb_tab_resolved_2010_tw54(provider: DashboardDataProvider):
    """Verify SBDB tab renders verified orbital & physical profile for 2010 TW54."""
    key = "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    sbdb_prof = provider.get_sbdb_profile(key)
    assert sbdb_prof is not None

    with patch("streamlit.markdown") as mock_md:
        render_sbdb_tab(sbdb_prof, asteroid_key=key)
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])

        assert "50548689" in rendered_text
        assert "2010 TW54" in rendered_text
        assert "Apollo" in rendered_text
        assert "FOLLOWUP_PRIORITY_LIMITED_ARC" in rendered_text
        assert "6" in rendered_text  # condition code
        assert "1.0427" in rendered_text  # a
        assert "0.234004" in rendered_text  # e
        assert "0.7987" in rendered_text  # q
        assert "3.8480" in rendered_text  # i
        assert "0.000608" in rendered_text  # MOID
        assert "27.60 mag" in rendered_text  # H
        # Diameter and albedo null in snapshot -> must preserve neutral message, not 0.0
        assert "NOT REPORTED IN CURRENT SBDB SNAPSHOT" in rendered_text


def test_render_sbdb_tab_unresolved_and_missing():
    """Verify SBDB tab displays neutral notices when unmapped or data absent."""
    # Unresolved: key is None
    with patch("streamlit.markdown") as mock_md:
        render_sbdb_tab(None, asteroid_key=None)
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])
        assert "Target Unresolved — No SBDB Linkage" in rendered_text

    # Resolved key but no SBDB profile
    with patch("streamlit.markdown") as mock_md:
        render_sbdb_tab(None, asteroid_key="ast_nonexistent")
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])
        assert "NOT REPORTED IN CURRENT SBDB SNAPSHOT" in rendered_text


def test_render_sentry_tab_resolved_2010_tw54(provider: DashboardDataProvider):
    """Verify Sentry tab renders reported metrics with non-predictive attribution."""
    key = "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    sentry_prof = provider.get_sentry_profile(key)
    assert sentry_prof is not None

    with patch("streamlit.markdown") as mock_md:
        render_sentry_tab(sentry_prof, asteroid_key=key)
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])

        assert "bK10T54W" in rendered_text
        assert "Current reported Sentry metrics" in rendered_text
        assert "6.59e-05" in rendered_text
        assert "-6.12" in rendered_text
        assert "16" in rendered_text  # potential impacts


def test_render_sentry_tab_unmonitored_and_ambiguous():
    """Verify Sentry tab displays neutral empty state and suppresses ambiguous metrics."""
    # Unmonitored entity
    unmonitored = {
        "has_sentry_monitoring": False,
        "is_sentry_ambiguous": False,
        "sentry_id": None,
    }
    with patch("streamlit.markdown") as mock_md:
        render_sentry_tab(unmonitored, asteroid_key="ast_some_key")
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])
        assert "NO CURRENT SENTRY RECORD" in rendered_text

    # Ambiguous Sentry records
    ambiguous = {
        "has_sentry_monitoring": True,
        "is_sentry_ambiguous": True,
        "sentry_identifier_count": 2,
        "sentry_id": None,
    }
    with patch("streamlit.markdown") as mock_md:
        render_sentry_tab(ambiguous, asteroid_key="ast_some_key")
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])
        assert "MULTIPLE SENTRY RECORDS DETECTED" in rendered_text
        assert "Scalar impact risk metrics are suppressed" in rendered_text


def test_render_history_tab_timeline_and_empty_state():
    """Verify history tab formats timeline table and handles empty states safely."""
    # Multi-snapshot history
    hist_df = pd.DataFrame(
        [
            {
                "snapshot_key": "2026-09-10",
                "impact_probability": 1.0e-4,
                "palermo_scale_max": -3.1,
                "palermo_scale_cum": -2.8,
                "torino_scale_max": 0,
                "potential_impacts_count": 5,
                "v_infinity_km_s": 14.5,
                "impact_year_range": "2045-2080",
                "last_obs_date": "2026-09-09",
                "is_impact_probability_changed": False,
                "is_palermo_scale_max_changed": False,
            },
            {
                "snapshot_key": "2026-09-18",
                "impact_probability": 2.5e-5,
                "palermo_scale_max": -3.1,
                "palermo_scale_cum": -3.2,
                "torino_scale_max": 0,
                "potential_impacts_count": 3,
                "v_infinity_km_s": 14.5,
                "impact_year_range": "2045-2080",
                "last_obs_date": "2026-09-17",
                "is_impact_probability_changed": True,
                "is_palermo_scale_max_changed": False,
            },
        ]
    )

    with patch("streamlit.markdown"), patch("streamlit.dataframe") as mock_df:
        render_history_tab(hist_df, sentry_id="test_id", asteroid_key="ast_key")
        called_df = mock_df.call_args[0][0]
        assert len(called_df) == 2
        # First row is baseline
        assert "Baseline snapshot" in called_df.iloc[0]["Reported Change Log"]
        # Second row reports detected IP change
        assert "Reported impact probability changed." in called_df.iloc[1]["Reported Change Log"]

    # Empty history
    with patch("streamlit.markdown") as mock_md:
        render_history_tab(pd.DataFrame(), sentry_id="test_id", asteroid_key="ast_key")
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])
        assert "NO HISTORICAL RISK SNAPSHOTS RECORDED" in rendered_text


def test_render_crosswalk_tab_namespaces_and_provenance(provider: DashboardDataProvider):
    """Verify crosswalk tab isolates source namespaces and highlights primary pivot."""
    key = "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    cw_df = provider.get_crosswalk(key)
    res = provider.get_resolution_state("3548666")

    with patch("streamlit.markdown") as mock_md, patch("streamlit.dataframe") as mock_df:
        render_crosswalk_tab(
            crosswalk_df=cw_df,
            res=res,
            selected_id="3548666",
            target_name="(2010 TW54)",
            asteroid_key=key,
        )
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])

        assert "NeoWs Namespace" in rendered_text
        assert "SBDB Namespace" in rendered_text
        assert "Sentry Namespace" in rendered_text
        assert "50548689" in rendered_text
        assert "bK10T54W" in rendered_text
        assert "True (Canonical Anchor)" in rendered_text
        assert "Rule: EXACT_DESIGNATION_MATCH" in rendered_text
        # Audit dataframe was rendered
        assert mock_df.called


def test_render_crosswalk_tab_unresolved_and_ambiguous():
    """Verify crosswalk tab displays clear notices for unresolved and ambiguous targets."""
    # Unresolved
    res_unres = {"match_state": "UNRESOLVED", "asteroid_key": None}
    with patch("streamlit.markdown") as mock_md:
        render_crosswalk_tab(
            crosswalk_df=pd.DataFrame(),
            res=res_unres,
            selected_id="9999999",
            target_name="Unresolved Object",
            asteroid_key=None,
        )
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])
        assert "TARGET UNRESOLVED — NO CROSSWALK LINKAGE" in rendered_text

    # Ambiguous
    res_ambig = {"match_state": "AMBIGUOUS", "asteroid_key": None}
    with patch("streamlit.markdown") as mock_md:
        render_crosswalk_tab(
            crosswalk_df=pd.DataFrame(),
            res=res_ambig,
            selected_id="8888888",
            target_name="Ambiguous Object",
            asteroid_key=None,
        )
        rendered_text = " ".join([str(c) for c in mock_md.call_args_list])
        assert "AMBIGUOUS RESOLUTION STATE" in rendered_text


def test_dashboard_scientific_safety_no_forbidden_terms():
    """Verify dashboard.py contains zero unscientific or sensationalist terminology."""
    src_path = PROJ_DIR / "dashboard.py"
    with open(src_path, encoding="utf-8") as f:
        code = f.read().lower()

    prohibited = [
        "will impact earth",
        "will hit earth",
        "became more dangerous",
        "became safer",
        "risk increased by",
        "danger score",
        "threat index",
        "lethality",
        "orbit refinement",
        "danger threshold",
        "impact threshold",
        "safety threshold",
        "probability threshold",
    ]
    for term in prohibited:
        assert term not in code, f"Prohibited unscientific term '{term}' found in dashboard.py!"


# ============================================================================
# J. RADAR HORIZON & POPULATION FILTERING TESTS (PHASE 9 RESTORATION)
# ============================================================================


def test_radar_horizon_filtering_threshold(provider: DashboardDataProvider):
    """Verify radar horizon filters strictly by observed miss_distance_km <= horizon_mkm * 1e6."""
    df = provider.get_threat_watchlist()
    assert not df.empty

    # Test at 20.0M km
    view_20m = filter_population(df, mode="All tracked targets", horizon_mkm=20.0)
    assert not view_20m.empty
    assert (view_20m["miss_distance_km"] <= 20_000_000.0).all()
    assert len(view_20m) < len(df)

    # Test at 10.0M km
    view_10m = filter_population(df, mode="All tracked targets", horizon_mkm=10.0)
    assert not view_10m.empty
    assert (view_10m["miss_distance_km"] <= 10_000_000.0).all()
    assert len(view_10m) < len(view_20m)


def test_filtered_population_counts_by_mode(provider: DashboardDataProvider):
    """Verify population counts for each classification mode and combination with horizon."""
    df = provider.get_threat_watchlist()
    total_count = len(df)

    # 1. All tracked targets
    all_targets = filter_population(df, mode="All tracked targets")
    assert len(all_targets) == total_count

    # 2. Potentially hazardous only
    haz_targets = filter_population(df, mode="Potentially hazardous only")
    assert len(haz_targets) == len(df[df["hazardous"]])
    assert haz_targets["hazardous"].all()

    # 3. Nominal targets only
    nom_targets = filter_population(df, mode="Nominal targets only")
    assert len(nom_targets) == len(df[~df["hazardous"]])
    assert not nom_targets["hazardous"].any()

    # 4. Sentry-monitored targets
    sentry_targets = filter_population(df, mode="Sentry-monitored targets")
    assert len(sentry_targets) == len(df[df["is_sentry_monitored"]])
    assert sentry_targets["is_sentry_monitored"].all()

    # 5. Combined mode + horizon
    haz_10m = filter_population(df, mode="Potentially hazardous only", horizon_mkm=10.0)
    assert (haz_10m["miss_distance_km"] <= 10_000_000.0).all()
    assert haz_10m["hazardous"].all()


def test_canvas_and_selector_same_population_invariant(provider: DashboardDataProvider):
    """Verify Canvas payload and target selector options receive the EXACT same filtered population."""
    df = provider.get_threat_watchlist()

    # Test multiple filter configurations
    for mode in POPULATION_FILTER_MODES:
        for horizon in [5.0, 15.0, 30.0, None]:
            view = filter_population(df, mode=mode, horizon_mkm=horizon)
            canvas_payload = build_canvas_payload(view)
            canvas_ids = [item["neows_id"] for item in canvas_payload]
            selector_ids = [str(nid) for nid in view["neows_id"].unique()] if not view.empty else []

            # Invariant 1: exact same count
            assert len(canvas_payload) == len(view)
            assert len(selector_ids) == len(view)
            # Invariant 2: exact same IDs in exact same order
            assert canvas_ids == selector_ids


def test_combined_filter_behavior_deterministic(provider: DashboardDataProvider):
    """Verify combined filters operate deterministically on factual NeoWs telemetry."""
    df = provider.get_threat_watchlist()

    # Horizon + Hazardous
    haz_20m = filter_population(df, mode="Potentially hazardous only", horizon_mkm=20.0)
    for _, row in haz_20m.iterrows():
        assert bool(row["hazardous"]) is True
        assert float(row["miss_distance_km"]) <= 20_000_000.0

    # Horizon + Sentry monitored
    sentry_20m = filter_population(df, mode="Sentry-monitored targets", horizon_mkm=20.0)
    sentry_ids = set(sentry_20m["neows_id"].astype(str))
    # 2010 TW54 (17.46M km) and 2008 ST (14.88M km) must both be present
    assert "3548666" in sentry_ids
    assert "3427460" in sentry_ids

    # Horizon at 16M km excludes 2010 TW54 while keeping 2008 ST
    sentry_16m = filter_population(df, mode="Sentry-monitored targets", horizon_mkm=16.0)
    sentry_16m_ids = set(sentry_16m["neows_id"].astype(str))
    assert "3427460" in sentry_16m_ids
    assert "3548666" not in sentry_16m_ids

    # Horizon + Both (Hazardous & Sentry-monitored)
    both_filtered = filter_population(df, mode="Hazardous & Sentry-monitored", horizon_mkm=20.0)
    # Returns valid dataframe with exact same columns, even if empty
    assert isinstance(both_filtered, pd.DataFrame)
    assert set(df.columns).issubset(both_filtered.columns)


def test_empty_filtered_population(provider: DashboardDataProvider):
    """Verify system handles empty filtered population gracefully with zero exceptions."""
    df = provider.get_threat_watchlist()
    # 0.1M km is below the minimum miss distance in dataset (~1.93M km)
    empty_view = filter_population(df, mode="All tracked targets", horizon_mkm=0.1)
    assert empty_view.empty

    canvas_payload = build_canvas_payload(empty_view)
    assert canvas_payload == []

    # Streamlit AppTest verification of empty state
    at = AppTest.from_file(str(PROJ_DIR / "dashboard.py")).run()
    at.sidebar.slider[0].set_value(0.1).run()
    assert not at.exception
    content = " ".join([m.value for m in at.markdown])
    assert "0 OF" in content
    assert "No targets within active radar horizon" in at.selectbox[0].options[0]


def test_target_preservation_when_still_eligible():
    """Verify target selection is preserved when horizon slider changes but target remains in range."""
    at = AppTest.from_file(str(PROJ_DIR / "dashboard.py")).run()
    # Select 2010 TW54 (miss distance ~17.46M km)
    idx = [i for i, o in enumerate(at.selectbox[0].options) if "3548666" in o][0]
    at.selectbox[0].select_index(idx).run()
    assert not at.exception
    assert "(2010 TW54)" in " ".join([m.value for m in at.markdown])

    # Reduce slider to 25.0M km (still >= 17.46M km)
    at.sidebar.slider[0].set_value(25.0).run()
    assert not at.exception
    content = " ".join([m.value for m in at.markdown])
    assert "(2010 TW54)" in content
    assert "RESOLVED" in content
    assert "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c" in content


def test_stale_target_clearing_when_excluded():
    """Verify selected target and dossier are cleared safely without exception when target is excluded."""
    at = AppTest.from_file(str(PROJ_DIR / "dashboard.py")).run()
    # Select 2010 TW54 (miss distance ~17.46M km)
    idx = [i for i, o in enumerate(at.selectbox[0].options) if "3548666" in o][0]
    at.selectbox[0].select_index(idx).run()
    assert not at.exception
    assert "(2010 TW54)" in " ".join([m.value for m in at.markdown])

    # Lower horizon slider to 10.0M km (below 17.46M km)
    at.sidebar.slider[0].set_value(10.0).run()
    assert not at.exception
    content = " ".join([m.value for m in at.markdown])

    # Must NOT contain 2010 TW54 identity header or dossier
    assert "Selected Target Telemetry & Identity" not in content
    assert "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c" not in content
    assert "Close-Approach Encounter Summary" not in content
    # Selectbox resets to placeholder
    assert at.selectbox[0].value is None


def test_2010_tw54_dossier_regression_with_horizon():
    """Verify 2010 TW54 full 5-tab dossier remains completely functional when included in horizon."""
    at = AppTest.from_file(str(PROJ_DIR / "dashboard.py")).run()
    # Ensure horizon is 20.0M km (includes 17.46M km)
    at.sidebar.slider[0].set_value(20.0).run()
    assert not at.exception

    idx = [i for i, o in enumerate(at.selectbox[0].options) if "3548666" in o][0]
    at.selectbox[0].select_index(idx).run()
    assert not at.exception

    content = " ".join([m.value for m in at.markdown])
    # Identity header
    assert "(2010 TW54)" in content
    assert "3548666" in content
    assert "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c" in content
    assert "RESOLVED" in content
    # Overview
    assert "Close-Approach Encounter Summary" in content
    assert "2026-09-30" in content
    assert "Potentially Hazardous: NO" in content
    # SBDB
    assert "50548689" in content
    assert "Apollo" in content
    # Sentry
    assert "bK10T54W" in content
    assert "Current reported Sentry metrics" in content


def test_unresolved_target_regression_with_horizon():
    """Verify unresolved target 523934 (1998 FF14) renders safely with horizon."""
    at = AppTest.from_file(str(PROJ_DIR / "dashboard.py")).run()
    # Set horizon to 20.0M km (includes 15.39M km)
    at.sidebar.slider[0].set_value(20.0).run()
    assert not at.exception

    idx = [i for i, o in enumerate(at.selectbox[0].options) if "2523934" in o][0]
    at.selectbox[0].select_index(idx).run()
    assert not at.exception

    content = " ".join([m.value for m in at.markdown])
    assert "UNRESOLVED" in content
    assert "523934 (1998 FF14)" in content
    assert "None (Unresolved in lakehouse crosswalk)" in content
    assert "Target Unresolved — No SBDB Linkage" in content
    assert "Target Unresolved — No Sentry Linkage" in content
