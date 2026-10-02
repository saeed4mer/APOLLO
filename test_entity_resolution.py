"""Comprehensive offline unit and integration test suite for Entity Resolution Engine.

Verifies the locked M5 Phase 6 entity-resolution contract for entity_resolution.py:
- Source identifier extraction and validation across NeoWs, SBDB, and Sentry
- Deterministic designation and whitespace normalization
- Exact SPK-ID matching (primary bridge)
- Exact designation matching (secondary bridge)
- Strict prevention of heuristic / fuzzy matching
- Complete resolution state machine (RESOLVED, UNRESOLVED, AMBIGUOUS, INVALID)
- Collision handling, contradiction detection, and quarantine isolation
- Authoritative bridge dataset schema, grain, and invariant enforcement
- Entity resolution audit dataset schema, grain, and explicit target preservation
- Deterministic, collision-resistant UUID5 asteroid_key generation
- Complete offline isolation and S3 reference pathing
"""

from collections import Counter
from datetime import date
import json
import os
from unittest.mock import patch

from botocore.exceptions import BotoCoreError, ClientError
import pyarrow.parquet as pq
import pytest

from entity_resolution import (
    BRIDGE_ASTEROID_IDENTIFIER_SCHEMA,
    FACT_ENTITY_RESOLUTION_SCHEMA,
    NAMESPACE_PLANETARY_DEFENSE,
    RULE_ALTERNATE_DESIGNATION,
    RULE_ASSOCIATED_SENTRY_ID,
    RULE_CANONICAL_ASSOCIATED_ID,
    RULE_CANONICAL_EXTERNAL_PIVOT,
    RULE_COLLISION_QUARANTINE,
    RULE_CONTRADICTORY_CANDIDATE,
    RULE_EXACT_DESIGNATION,
    RULE_EXACT_FULLNAME,
    RULE_EXACT_SPKID,
    RULE_INVALID_IDENTIFIER,
    RULE_NO_MATCH,
    STATE_AMBIGUOUS,
    STATE_INVALID,
    STATE_RESOLVED,
    STATE_UNRESOLVED,
    generate_asteroid_key,
    load_parquet_records,
    main,
    normalize_designation,
    normalize_whitespace,
    resolve_entities,
    save_resolution_outputs,
    upload_crosswalk_to_s3,
)
from test_data_provider import _FIXTURE_ASTEROIDS, _FIXTURE_SBDB_IDENTITIES_ALL35, _FIXTURE_SENTRY

# ---------------------------------------------------------------------------
# Test Fixtures & Representative Payloads
# ---------------------------------------------------------------------------

# 1. Realistic 2025 HX Fixtures (Empirically verified target)
FIXTURE_2025_HX_NEOWS = {
    "id": "54527277",
    "name": "(2025 HX)",
    "closest_approach_date": "2025-04-18",
    "miss_distance_km": 1500000.0,
    "hazardous": False,
}

FIXTURE_2025_HX_SBDB = {
    "spkid": "54527277",
    "designation": "2025 HX",
    "fullname": "(2025 HX)",
    "orbit_id": "4",
    "is_neo": True,
}

FIXTURE_2025_HX_SENTRY = {
    "sentry_id": "bK25H00X",
    "designation": "2025 HX",
    "fullname": "(2025 HX)",
    "impact_probability": 1.2e-4,
    "palermo_scale_max": -2.5,
}

# 2. Numbered Asteroid Fixture (138971 / 2001 CB21)
FIXTURE_138971_NEOWS = {
    "id": "2138971",
    "name": "138971 (2001 CB21)",
    "closest_approach_date": "2026-03-04",
    "miss_distance_km": 4900000.0,
    "hazardous": True,
}

FIXTURE_138971_SBDB = {
    "spkid": "2138971",
    "designation": "138971",
    "fullname": "138971 (2001 CB21)",
    "orbit_id": "128",
    "is_neo": True,
}

FIXTURE_138971_SENTRY = {
    "sentry_id": "a0138971",
    "designation": "138971",
    "fullname": "(2001 CB21)",
    "impact_probability": 3.4e-6,
    "palermo_scale_max": -4.1,
}


# ===========================================================================
# 1. Identifier Extraction & Validation Tests
# ===========================================================================

def test_identifier_extraction_valid():
    """Verify source identifiers are extracted correctly from all 3 systems."""
    bridge, audit, metrics = resolve_entities(
        neows_records=[FIXTURE_2025_HX_NEOWS],
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[FIXTURE_2025_HX_SENTRY],
        run_id="run_valid_extract",
    )

    # Bridge must contain exactly 5 identifier rows
    bridge_ids = {(b["source_system"], b["identifier_name"], b["identifier_value"]) for b in bridge}
    expected_bridge_ids = {
        ("neows", "id", "54527277"),
        ("sbdb", "spkid", "54527277"),
        ("sbdb", "des", "2025 HX"),
        ("sentry", "sentry_id", "bK25H00X"),
        ("sentry", "des", "2025 HX"),
    }
    assert bridge_ids == expected_bridge_ids

    # Audit must contain exactly 5 rows (one per source identifier namespace)
    audit_ids = {(a["source_system"], a["identifier_name"], a["source_identifier_value"]) for a in audit}
    expected_audit_ids = {
        ("neows", "id", "54527277"),
        ("sbdb", "spkid", "54527277"),
        ("sbdb", "des", "2025 HX"),
        ("sentry", "sentry_id", "bK25H00X"),
        ("sentry", "des", "2025 HX"),
    }
    assert audit_ids == expected_audit_ids
    assert metrics["resolved_count"] == 5


def test_identifier_extraction_malformed_empty():
    """Verify empty/missing identifiers are flagged INVALID and quarantined."""
    malformed_neows = [{"id": "", "name": "No ID"}, {"id": None, "name": "None ID"}]
    malformed_sbdb = [{"spkid": "", "designation": "DES1"}, {"spkid": "   ", "designation": "DES2"}]
    malformed_sentry = [
        {"sentry_id": "", "designation": "2025 ZZ"},
        {"sentry_id": "s_valid", "designation": "   "},
    ]

    bridge, audit, metrics = resolve_entities(
        neows_records=malformed_neows,
        sbdb_records=malformed_sbdb,
        sentry_records=malformed_sentry,
        run_id="run_malformed",
    )

    # Zero bridge rows emitted
    assert len(bridge) == 0

    # All evaluated records must be in INVALID state
    for a in audit:
        assert a["match_state"] == STATE_INVALID
        assert a["match_rule"] == RULE_INVALID_IDENTIFIER
        assert a["assigned_asteroid_key"] is None

    assert metrics["invalid_count"] > 0
    assert metrics["resolved_count"] == 0


def test_names_and_fullnames_are_contextual_evidence_only():
    """Verify names/fullnames are NOT emitted as separate identifier records."""
    bridge, audit, metrics = resolve_entities(
        neows_records=[FIXTURE_2025_HX_NEOWS],
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[FIXTURE_2025_HX_SENTRY],
        run_id="run_names_context",
    )

    # Verify no name or fullname appears as identifier_name in bridge or audit
    for b in bridge:
        assert b["identifier_name"] not in ("name", "fullname")

    for a in audit:
        assert a["identifier_name"] not in ("name", "fullname")

    # Verify name/fullname are present in evidence_json
    neows_audit = [a for a in audit if a["source_system"] == "neows" and a["identifier_name"] == "id"][0]
    neows_evidence = json.loads(neows_audit["evidence_json"])
    assert neows_evidence.get("name") == "(2025 HX)"

    sbdb_spk_audit = [a for a in audit if a["source_system"] == "sbdb" and a["identifier_name"] == "spkid"][0]
    sbdb_evidence = json.loads(sbdb_spk_audit["evidence_json"])
    assert sbdb_evidence.get("fullname") == "(2025 HX)"

    sentry_des_audit = [a for a in audit if a["source_system"] == "sentry" and a["identifier_name"] == "des"][0]
    sentry_evidence = json.loads(sentry_des_audit["evidence_json"])
    assert sentry_evidence.get("fullname") == "(2025 HX)"


# ===========================================================================
# 2. Deterministic Normalization Tests
# ===========================================================================

def test_normalize_whitespace():
    """Test whitespace collapse and bounding trim."""
    assert normalize_whitespace("  hello   world  ") == "hello world"
    assert normalize_whitespace("\t 2025 \n  HX \r") == "2025 HX"
    assert normalize_whitespace("") == ""
    assert normalize_whitespace(None) == ""


def test_normalize_designation_rules():
    """Test deterministic designation normalization rules."""
    # Outer parenthesis removal when wrapping whole string
    assert normalize_designation("(2025 HX)") == "2025 HX"
    assert normalize_designation(" (2025 HX) ") == "2025 HX"

    # Repeated whitespace and lower-to-upper conversion
    assert normalize_designation(" 2025  hx ") == "2025 HX"
    assert normalize_designation("  (  2025   hx  )  ") == "2025 HX"

    # Numbered designations where parens do not wrap the full string
    assert normalize_designation("(153814) 2001 WN5") == "(153814) 2001 WN5"
    assert normalize_designation("(99942) Apophis") == "(99942) APOPHIS"
    assert normalize_designation("(433)") == "433"

    # Non-strings
    assert normalize_designation(None) == ""
    assert normalize_designation(12345) == ""


def test_source_native_values_not_mutated():
    """Verify source-native stored values remain uncorrupted."""
    original_des = "(2025 HX)"
    sentry_record = {
        "sentry_id": "bK25H00X",
        "designation": original_des,
        "fullname": "(2025 HX)",
    }

    bridge, audit, _ = resolve_entities(
        neows_records=[],
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[sentry_record],
        run_id="run_native_check",
    )

    sentry_bridge_des = [b for b in bridge if b["source_system"] == "sentry" and b["identifier_name"] == "des"][0]
    sentry_audit_des = [a for a in audit if a["source_system"] == "sentry" and a["identifier_name"] == "des"][0]

    # Stored identifier values must retain exact native string
    assert sentry_bridge_des["identifier_value"] == original_des
    assert sentry_audit_des["source_identifier_value"] == original_des


# ===========================================================================
# 3. Exact SPK Match Tests
# ===========================================================================

def test_exact_spk_match_2025_hx():
    """Verify primary bridge NeoWs.id == SBDB.spkid matches deterministically."""
    bridge, audit, metrics = resolve_entities(
        neows_records=[FIXTURE_2025_HX_NEOWS],
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[],
        run_id="run_spk_match",
    )

    neows_audit = [a for a in audit if a["source_system"] == "neows" and a["identifier_name"] == "id"][0]
    assert neows_audit["match_state"] == STATE_RESOLVED
    assert neows_audit["match_rule"] == RULE_EXACT_SPKID
    assert neows_audit["matched_target_system"] == "sbdb"
    assert neows_audit["matched_target_identifier_name"] == "spkid"
    assert neows_audit["matched_target_identifier_value"] == "54527277"

    expected_key = generate_asteroid_key("54527277")
    assert neows_audit["assigned_asteroid_key"] == expected_key

    neows_bridge = [b for b in bridge if b["source_system"] == "neows" and b["identifier_name"] == "id"][0]
    assert neows_bridge["asteroid_key"] == expected_key
    assert neows_bridge["is_primary_pivot"] is False


# ===========================================================================
# 4. Exact Designation Match Tests
# ===========================================================================

def test_exact_designation_match_2025_hx():
    """Verify secondary bridge Sentry.des == SBDB.des matches deterministically."""
    bridge, audit, metrics = resolve_entities(
        neows_records=[],
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[FIXTURE_2025_HX_SENTRY],
        run_id="run_des_match",
    )

    sentry_des_audit = [a for a in audit if a["source_system"] == "sentry" and a["identifier_name"] == "des"][0]
    assert sentry_des_audit["match_state"] == STATE_RESOLVED
    assert sentry_des_audit["match_rule"] == RULE_EXACT_DESIGNATION
    assert sentry_des_audit["matched_target_system"] == "sbdb"
    assert sentry_des_audit["matched_target_identifier_name"] == "des"
    assert sentry_des_audit["matched_target_identifier_value"] == "2025 HX"

    sentry_id_audit = [a for a in audit if a["source_system"] == "sentry" and a["identifier_name"] == "sentry_id"][0]
    assert sentry_id_audit["match_state"] == STATE_RESOLVED
    assert sentry_id_audit["match_rule"] == RULE_ASSOCIATED_SENTRY_ID

    expected_key = generate_asteroid_key("54527277")
    assert sentry_des_audit["assigned_asteroid_key"] == expected_key
    assert sentry_id_audit["assigned_asteroid_key"] == expected_key


def test_exact_designation_match_formatting_tolerance():
    """Verify secondary bridge matches across whitespace and casing differences."""
    sentry_var = {
        "sentry_id": "bK25H00X",
        "designation": "  (2025   hx)  ",
        "fullname": "(2025 HX)",
    }

    bridge, audit, metrics = resolve_entities(
        neows_records=[],
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[sentry_var],
        run_id="run_des_format",
    )

    sentry_des_audit = [a for a in audit if a["source_system"] == "sentry" and a["identifier_name"] == "des"][0]
    assert sentry_des_audit["match_state"] == STATE_RESOLVED
    assert sentry_des_audit["match_rule"] == RULE_EXACT_DESIGNATION
    assert sentry_des_audit["assigned_asteroid_key"] == generate_asteroid_key("54527277")


def test_neows_exact_designation_match_when_id_differs_from_spkid():
    """Verify NeoWs ID 3548666 with name (2010 TW54) resolves to SBDB 50548689 via exact designation."""
    neows_tw54 = {
        "id": "3548666",
        "name": "(2010 TW54)",
        "closest_approach_date": "2026-09-30",
        "miss_distance_km": 17457205.0,
        "hazardous": False,
    }
    sbdb_tw54 = {
        "spkid": "50548689",
        "designation": "2010 TW54",
        "fullname": "(2010 TW54)",
        "orbit_id": "14",
        "is_neo": True,
    }

    bridge, audit, metrics = resolve_entities(
        neows_records=[neows_tw54],
        sbdb_records=[sbdb_tw54],
        sentry_records=[],
        run_id="run_neows_tw54",
    )

    expected_key = "ast_b8259bf1-e6e5-5059-853e-9434274cdf2c"
    assert generate_asteroid_key("50548689") == expected_key

    # Audit assertions
    neows_audits = [a for a in audit if a["source_system"] == "neows" and a["identifier_name"] == "id"]
    assert len(neows_audits) == 1
    a = neows_audits[0]
    assert a["match_state"] == STATE_RESOLVED
    assert a["match_rule"] == RULE_EXACT_DESIGNATION
    assert a["assigned_asteroid_key"] == expected_key
    assert a["matched_target_system"] == "sbdb"
    assert a["matched_target_identifier_name"] == "des"
    assert a["matched_target_identifier_value"] == "2010 TW54"

    # Bridge assertions
    neows_bridge = [b for b in bridge if b["source_system"] == "neows"]
    assert len(neows_bridge) == 1
    b = neows_bridge[0]
    assert b["asteroid_key"] == expected_key
    assert b["identifier_name"] == "id"
    assert b["identifier_value"] == "3548666"
    assert b["is_primary_pivot"] is False

    # Name is not emitted as identifier row
    assert not any(row["identifier_name"] == "name" for row in bridge)


def test_neows_exact_designation_match_2008_st():
    """Verify NeoWs ID 3427460 with name (2008 ST) resolves to SBDB 50427483."""
    neows_st = {
        "id": "3427460",
        "name": "(2008 ST)",
        "closest_approach_date": "2026-09-27",
        "miss_distance_km": 12000000.0,
        "hazardous": False,
    }
    sbdb_st = {
        "spkid": "50427483",
        "designation": "2008 ST",
        "fullname": "(2008 ST)",
        "orbit_id": "14",
        "is_neo": True,
    }

    bridge, audit, metrics = resolve_entities(
        neows_records=[neows_st],
        sbdb_records=[sbdb_st],
        sentry_records=[],
        run_id="run_neows_st",
    )

    expected_key = "ast_8520aaac-9c77-5e8f-9a88-b4e501749e26"
    assert generate_asteroid_key("50427483") == expected_key

    # Audit assertions
    neows_audits = [a for a in audit if a["source_system"] == "neows" and a["identifier_name"] == "id"]
    assert len(neows_audits) == 1
    assert neows_audits[0]["match_state"] == STATE_RESOLVED
    assert neows_audits[0]["match_rule"] == RULE_EXACT_DESIGNATION
    assert neows_audits[0]["assigned_asteroid_key"] == expected_key

    # Bridge assertions
    neows_bridge = [b for b in bridge if b["source_system"] == "neows"]
    assert len(neows_bridge) == 1
    assert neows_bridge[0]["asteroid_key"] == expected_key
    assert neows_bridge[0]["identifier_name"] == "id"
    assert neows_bridge[0]["identifier_value"] == "3427460"


def test_neows_designation_ambiguity_quarantined():
    """Verify NeoWs name matching multiple distinct SBDB entities is quarantined as AMBIGUOUS."""
    sbdb_dup1 = {
        "spkid": "9000001",
        "designation": "2026 AMB1",
        "fullname": "(2026 AMB1)",
    }
    sbdb_dup2 = {
        "spkid": "9000002",
        "designation": "2026 AMB1",
        "fullname": "2026 AMB1 Duplicate",
    }
    neows_amb = {
        "id": "7777777",
        "name": "(2026 AMB1)",
        "closest_approach_date": "2026-10-01",
    }

    bridge, audit, metrics = resolve_entities(
        neows_records=[neows_amb],
        sbdb_records=[sbdb_dup1, sbdb_dup2],
        sentry_records=[],
        run_id="run_neows_amb",
    )

    neows_audits = [a for a in audit if a["source_system"] == "neows" and a["identifier_name"] == "id"]
    assert len(neows_audits) == 1
    assert neows_audits[0]["match_state"] == STATE_AMBIGUOUS
    assert neows_audits[0]["match_rule"] == RULE_COLLISION_QUARANTINE
    assert neows_audits[0]["assigned_asteroid_key"] is None

    # Zero bridge rows emitted for NeoWs
    assert not any(b["source_system"] == "neows" for b in bridge)


def test_neows_unresolved_when_designation_not_in_sbdb():
    """Verify NeoWs object with unknown designation returns UNRESOLVED with null key."""
    neows_unres = {
        "id": "8888888",
        "name": "(2099 ZZ99)",
        "closest_approach_date": "2026-12-31",
    }
    sbdb_known = {
        "spkid": "50548689",
        "designation": "2010 TW54",
        "fullname": "(2010 TW54)",
    }

    bridge, audit, metrics = resolve_entities(
        neows_records=[neows_unres],
        sbdb_records=[sbdb_known],
        sentry_records=[],
        run_id="run_neows_unres",
    )

    neows_audits = [a for a in audit if a["source_system"] == "neows" and a["identifier_name"] == "id"]
    assert len(neows_audits) == 1
    assert neows_audits[0]["match_state"] == STATE_UNRESOLVED
    assert neows_audits[0]["match_rule"] == RULE_NO_MATCH
    assert neows_audits[0]["assigned_asteroid_key"] is None

    # Zero bridge rows emitted for NeoWs
    assert not any(b["source_system"] == "neows" for b in bridge)


# ===========================================================================
# 5. No Heuristic / Fuzzy Matching Tests
# ===========================================================================

def test_no_fuzzy_or_heuristic_matching_similar_designations():
    """Verify visually similar designations do NOT match."""
    # SBDB has 2025 HX; Sentry has 2025 HA and 2025 HB
    sentry_ha = {"sentry_id": "s_ha", "designation": "2025 HA"}
    sentry_hb = {"sentry_id": "s_hb", "designation": "2025 HB"}

    bridge, audit, metrics = resolve_entities(
        neows_records=[],
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[sentry_ha, sentry_hb],
        run_id="run_no_fuzzy",
    )

    sentry_audits = [a for a in audit if a["source_system"] == "sentry"]
    for a in sentry_audits:
        assert a["match_state"] == STATE_UNRESOLVED
        assert a["match_rule"] == RULE_NO_MATCH
        assert a["assigned_asteroid_key"] is None

    # Zero Sentry bridge rows
    assert not any(b["source_system"] == "sentry" for b in bridge)


def test_no_partial_name_or_token_similarity_matching():
    """Verify partial name similarities (e.g. Apophis) do not synthetically match."""
    sbdb_apophis = {"spkid": "2099942", "designation": "99942", "fullname": "99942 Apophis (2004 MN4)"}
    sentry_guess = {"sentry_id": "s_guess", "designation": "Apophis"}

    bridge, audit, metrics = resolve_entities(
        neows_records=[],
        sbdb_records=[sbdb_apophis],
        sentry_records=[sentry_guess],
        run_id="run_no_token_match",
    )

    sentry_audit = [a for a in audit if a["source_system"] == "sentry" and a["identifier_name"] == "des"][0]
    assert sentry_audit["match_state"] == STATE_UNRESOLVED
    assert sentry_audit["match_rule"] == RULE_NO_MATCH
    assert sentry_audit["assigned_asteroid_key"] is None


# ===========================================================================
# 6. Resolution State Machine Tests
# ===========================================================================

def test_resolution_states_exhaustive():
    """Verify exact downstream behavior across all 4 resolution states."""
    # 1. RESOLVED
    sbdb_res = FIXTURE_2025_HX_SBDB
    neows_res = FIXTURE_2025_HX_NEOWS

    # 2. UNRESOLVED
    neows_unres = {"id": "99999999", "name": "Unmatched Asteroid"}

    # 3. AMBIGUOUS (Conflict)
    sbdb_col1 = {"spkid": "101", "designation": "DUP_DES"}
    sbdb_col2 = {"spkid": "102", "designation": "DUP_DES"}
    sentry_col = {"sentry_id": "s_col", "designation": "DUP_DES"}

    # 4. INVALID
    neows_inv = {"id": "", "name": "Empty ID"}

    bridge, audit, metrics = resolve_entities(
        neows_records=[neows_res, neows_unres, neows_inv],
        sbdb_records=[sbdb_res, sbdb_col1, sbdb_col2],
        sentry_records=[sentry_col],
        run_id="run_states_all",
    )

    audit_by_state = {
        STATE_RESOLVED: [a for a in audit if a["match_state"] == STATE_RESOLVED],
        STATE_UNRESOLVED: [a for a in audit if a["match_state"] == STATE_UNRESOLVED],
        STATE_AMBIGUOUS: [a for a in audit if a["match_state"] == STATE_AMBIGUOUS],
        STATE_INVALID: [a for a in audit if a["match_state"] == STATE_INVALID],
    }

    # Verify RESOLVED behavior: key assigned, bridge row exists
    assert len(audit_by_state[STATE_RESOLVED]) > 0
    for a in audit_by_state[STATE_RESOLVED]:
        assert a["assigned_asteroid_key"] is not None

    # Verify UNRESOLVED behavior: key null, no bridge row
    assert len(audit_by_state[STATE_UNRESOLVED]) > 0
    for a in audit_by_state[STATE_UNRESOLVED]:
        assert a["assigned_asteroid_key"] is None
        assert not any(b["identifier_value"] == a["source_identifier_value"] for b in bridge)

    # Verify AMBIGUOUS behavior: key null, quarantined, zero competing bridge rows
    assert len(audit_by_state[STATE_AMBIGUOUS]) > 0
    for a in audit_by_state[STATE_AMBIGUOUS]:
        assert a["assigned_asteroid_key"] is None
        assert not any(b["identifier_value"] == a["source_identifier_value"] for b in bridge)

    # Verify INVALID behavior: key null, quarantined, zero bridge rows
    assert len(audit_by_state[STATE_INVALID]) > 0
    for a in audit_by_state[STATE_INVALID]:
        assert a["assigned_asteroid_key"] is None


# ===========================================================================
# 7. Collision & Contradiction Tests
# ===========================================================================

def test_collision_single_source_id_maps_to_multiple_candidates():
    """Test Case A: 1 Sentry designation matches multiple SBDB entities -> AMBIGUOUS quarantine."""
    sbdb_1 = {"spkid": "1001", "designation": "COLLISION_DES", "fullname": "Object 1"}
    sbdb_2 = {"spkid": "1002", "designation": "COLLISION_DES", "fullname": "Object 2"}
    sentry = {"sentry_id": "s_col", "designation": "COLLISION_DES"}

    bridge, audit, metrics = resolve_entities(
        neows_records=[],
        sbdb_records=[sbdb_1, sbdb_2],
        sentry_records=[sentry],
        run_id="run_col_a",
    )

    sentry_audits = [a for a in audit if a["source_system"] == "sentry"]
    assert len(sentry_audits) == 2
    for a in sentry_audits:
        assert a["match_state"] == STATE_AMBIGUOUS
        assert a["match_rule"] == RULE_COLLISION_QUARANTINE
        assert a["assigned_asteroid_key"] is None

    # Zero Sentry records in bridge
    assert not any(b["source_system"] == "sentry" for b in bridge)


def test_collision_multiple_source_ids_map_to_one_canonical_entity():
    """Test Case B: Multiple valid source IDs map to 1 canonical entity (valid aliases)."""
    bridge, audit, metrics = resolve_entities(
        neows_records=[FIXTURE_138971_NEOWS],
        sbdb_records=[FIXTURE_138971_SBDB],
        sentry_records=[FIXTURE_138971_SENTRY],
        run_id="run_aliases_b",
    )

    expected_key = generate_asteroid_key("2138971")
    assert len(bridge) == 5
    for b in bridge:
        assert b["asteroid_key"] == expected_key

    # All resolved records share the same key
    for a in audit:
        assert a["assigned_asteroid_key"] == expected_key
        assert a["match_state"] == STATE_RESOLVED


def test_collision_contradiction_spkid_vs_designation():
    """Test Case C: NeoWs ID points to SPK A but designation points to SPK B -> AMBIGUOUS."""
    sbdb_a = {"spkid": "54527277", "designation": "2025 HX", "fullname": "(2025 HX)"}
    sbdb_b = {"spkid": "2000433", "designation": "433", "fullname": "433 Eros (1898 DQ)"}

    # Contradictory NeoWs record: ID is 54527277 (2025 HX), but name is 433 (Eros)
    contradictory_neows = {
        "id": "54527277",
        "name": "433",
        "closest_approach_date": "2025-04-18",
    }

    bridge, audit, metrics = resolve_entities(
        neows_records=[contradictory_neows],
        sbdb_records=[sbdb_a, sbdb_b],
        sentry_records=[],
        run_id="run_contradiction_c",
    )

    neows_audit = [a for a in audit if a["source_system"] == "neows" and a["identifier_name"] == "id"][0]
    assert neows_audit["match_state"] == STATE_AMBIGUOUS
    assert neows_audit["match_rule"] == RULE_CONTRADICTORY_CANDIDATE
    assert neows_audit["assigned_asteroid_key"] is None

    # Zero NeoWs rows in bridge
    assert not any(b["source_system"] == "neows" for b in bridge)


def test_collision_duplicate_bridge_input_deduplicated():
    """Test Case D: Duplicate identical approaches in NeoWs input deduplicated in bridge."""
    repeated_neows = [
        FIXTURE_2025_HX_NEOWS,
        FIXTURE_2025_HX_NEOWS,
        FIXTURE_2025_HX_NEOWS,
    ]

    bridge, audit, metrics = resolve_entities(
        neows_records=repeated_neows,
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[],
        run_id="run_dedup_d",
    )

    # Exactly 1 NeoWs ID row in bridge
    neows_bridge_rows = [b for b in bridge if b["source_system"] == "neows"]
    assert len(neows_bridge_rows) == 1
    assert neows_bridge_rows[0]["identifier_value"] == "54527277"


# ===========================================================================
# 8. Bridge Dataset Schema & Invariant Tests
# ===========================================================================

def test_bridge_dataset_schema_and_grain():
    """Verify PyArrow schema compliance and grain uniqueness in bridge dataset."""
    bridge, _, _ = resolve_entities(
        neows_records=[FIXTURE_2025_HX_NEOWS],
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[FIXTURE_2025_HX_SENTRY],
        run_id="run_schema_bridge",
    )

    # Test PyArrow table creation with locked schema
    table = pyarrow_table = pyarrow_table = pyarrow_table = None  # noqa: F841
    table = pyarrow_table = pyarrow_table = pyarrow_table = None  # noqa: F841
    import pyarrow as pa
    table = pa.Table.from_pylist(bridge, schema=BRIDGE_ASTEROID_IDENTIFIER_SCHEMA)
    assert table.schema == BRIDGE_ASTEROID_IDENTIFIER_SCHEMA
    assert table.num_rows == 5

    # Verify grain uniqueness: (asteroid_key, source_system, identifier_name, identifier_value)
    grain_keys = [(b["asteroid_key"], b["source_system"], b["identifier_name"], b["identifier_value"]) for b in bridge]
    assert len(grain_keys) == len(set(grain_keys))

    # Verify primary pivot: only SBDB spkid has is_primary_pivot=True
    for b in bridge:
        if b["source_system"] == "sbdb" and b["identifier_name"] == "spkid":
            assert b["is_primary_pivot"] is True
        else:
            assert b["is_primary_pivot"] is False


def test_bridge_uniqueness_invariant_quarantine():
    """Verify DQ Invariant: (source_system, identifier_name, identifier_value) -> at most one asteroid_key."""
    # Construct a synthetic candidate where one identifier would map to two keys
    # e.g., SBDB input has 2 objects with different SPK-IDs sharing the same designation
    sbdb_1 = {"spkid": "1001", "designation": "CONFLICT_DES"}
    sbdb_2 = {"spkid": "1002", "designation": "CONFLICT_DES"}

    bridge, audit, metrics = resolve_entities(
        neows_records=[],
        sbdb_records=[sbdb_1, sbdb_2],
        sentry_records=[],
        run_id="run_invariant_check",
    )

    # Invariant quarantine must drop CONFLICT_DES from bridge
    des_bridge_rows = [b for b in bridge if b["identifier_name"] == "des"]
    assert len(des_bridge_rows) == 0

    # Only spkid primary pivots remain
    spk_bridge_rows = [b for b in bridge if b["identifier_name"] == "spkid"]
    assert len(spk_bridge_rows) == 2


# ===========================================================================
# 9. Entity Resolution Audit Dataset Tests
# ===========================================================================

def test_fact_entity_resolution_schema_and_explicit_targets():
    """Verify audit dataset schema, grain, and explicit target identifier fields."""
    _, audit, _ = resolve_entities(
        neows_records=[FIXTURE_2025_HX_NEOWS],
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[FIXTURE_2025_HX_SENTRY],
        run_id="run_schema_audit",
    )

    import pyarrow as pa
    table = pa.Table.from_pylist(audit, schema=FACT_ENTITY_RESOLUTION_SCHEMA)
    assert table.schema == FACT_ENTITY_RESOLUTION_SCHEMA
    assert table.num_rows == 5

    # Verify explicit target columns are populated for resolved cross-source records
    neows_audit = [a for a in audit if a["source_system"] == "neows"][0]
    assert neows_audit["matched_target_system"] == "sbdb"
    assert neows_audit["matched_target_identifier_name"] == "spkid"
    assert neows_audit["matched_target_identifier_value"] == "54527277"

    sentry_audit = [a for a in audit if a["source_system"] == "sentry" and a["identifier_name"] == "des"][0]
    assert sentry_audit["matched_target_system"] == "sbdb"
    assert sentry_audit["matched_target_identifier_name"] == "des"
    assert sentry_audit["matched_target_identifier_value"] == "2025 HX"


# ===========================================================================
# 10. Canonical Asteroid Key Generation Tests
# ===========================================================================

def test_asteroid_key_generation_spec():
    """Verify deterministic, collision-resistant UUID5 asteroid_key specification."""
    import uuid

    # Deterministic generation
    key1 = generate_asteroid_key("54527277")
    key2 = generate_asteroid_key("54527277")
    assert key1 == key2
    assert key1 == "ast_c43b711d-1749-52e9-bac5-1ba300e47e63"

    # Whitespace invariance
    key_ws = generate_asteroid_key("  54527277  ")
    assert key1 == key_ws

    # Prefix verification: 'ast_' prefix is present
    assert key1.startswith("ast_")

    # Full UUID5 representation retained (no truncation: 4-char prefix + 36-char canonical UUID = 40 chars)
    uuid_part = key1[4:]
    assert len(uuid_part) == 36
    assert len(key1) == 40
    assert "-" in uuid_part

    # Standard UUID parsing: verify version is 5 and variant is RFC 4122
    parsed_uuid = uuid.UUID(uuid_part)
    assert parsed_uuid.version == 5
    assert parsed_uuid.variant == uuid.RFC_4122

    # Namespace invariance: fixed planetary defense platform namespace
    assert NAMESPACE_PLANETARY_DEFENSE == uuid.UUID("e7b8c9d0-1234-5678-9abc-def012345678")
    assert RULE_CANONICAL_EXTERNAL_PIVOT == "CANONICAL_EXTERNAL_PIVOT"
    assert RULE_CANONICAL_ASSOCIATED_ID == "CANONICAL_ASSOCIATED_IDENTIFIER"

    # Distinct deterministic outputs across different SPK-IDs
    key_eros = generate_asteroid_key("2000433")
    key_numbered = generate_asteroid_key("2138971")
    assert key1 != key_eros != key_numbered


# ===========================================================================
# 11. Idempotency & Order-Independence Tests
# ===========================================================================

def test_idempotency_and_order_independence():
    """Verify that input order permutations produce bit-for-bit identical outputs."""
    inputs_order_a_neows = [FIXTURE_2025_HX_NEOWS, FIXTURE_138971_NEOWS]
    inputs_order_a_sbdb = [FIXTURE_2025_HX_SBDB, FIXTURE_138971_SBDB]
    inputs_order_a_sentry = [FIXTURE_2025_HX_SENTRY, FIXTURE_138971_SENTRY]

    inputs_order_b_neows = [FIXTURE_138971_NEOWS, FIXTURE_2025_HX_NEOWS]
    inputs_order_b_sbdb = [FIXTURE_138971_SBDB, FIXTURE_2025_HX_SBDB]
    inputs_order_b_sentry = [FIXTURE_138971_SENTRY, FIXTURE_2025_HX_SENTRY]

    bridge_a, audit_a, _ = resolve_entities(
        neows_records=inputs_order_a_neows,
        sbdb_records=inputs_order_a_sbdb,
        sentry_records=inputs_order_a_sentry,
        run_id="run_fixed_id",
        resolved_at="2026-09-26T12:00:00Z",
    )

    bridge_b, audit_b, _ = resolve_entities(
        neows_records=inputs_order_b_neows,
        sbdb_records=inputs_order_b_sbdb,
        sentry_records=inputs_order_b_sentry,
        run_id="run_fixed_id",
        resolved_at="2026-09-26T12:00:00Z",
    )

    # Exact deterministic identity and sorting
    assert bridge_a == bridge_b
    assert audit_a == audit_b


# ===========================================================================
# 12. Local File I/O & Parquet Writing Tests
# ===========================================================================

def test_save_resolution_outputs_local(tmp_path):
    """Verify local Parquet serialization using Snappy compression and locked schemas."""
    bridge, audit, _ = resolve_entities(
        neows_records=[FIXTURE_2025_HX_NEOWS],
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[FIXTURE_2025_HX_SENTRY],
        run_id="run_local_save",
    )

    out_dir = str(tmp_path)
    b_path, r_path = save_resolution_outputs(
        bridge_records=bridge,
        resolution_records=audit,
        output_dir=out_dir,
    )

    assert os.path.exists(b_path)
    assert os.path.exists(r_path)

    # Read back and verify Parquet metadata
    b_table = pq.read_table(b_path)
    r_table = pq.read_table(r_path)

    assert b_table.schema == BRIDGE_ASTEROID_IDENTIFIER_SCHEMA
    assert r_table.schema == FACT_ENTITY_RESOLUTION_SCHEMA
    assert b_table.num_rows == 5
    assert r_table.num_rows == 5


def test_load_parquet_records_helper(tmp_path):
    """Verify load_parquet_records safely loads local Parquet tables."""
    dummy_records = [{"id": "123", "name": "Test"}]
    test_path = os.path.join(str(tmp_path), "test.parquet")
    import pyarrow as pa
    schema = pa.schema([("id", pa.string()), ("name", pa.string())])
    table = pa.Table.from_pylist(dummy_records, schema=schema)
    pq.write_table(table, test_path)

    loaded = load_parquet_records(test_path)
    assert loaded == dummy_records

    # Non-existent file returns empty list safely
    assert load_parquet_records(os.path.join(str(tmp_path), "non_existent.parquet")) == []


# ===========================================================================
# 13. S3 Partitioning & Mocked Upload Tests
# ===========================================================================

@patch("entity_resolution.upload_file_to_s3")
def test_upload_crosswalk_to_s3_exact_paths(mock_upload):
    """Verify exact S3 partitioning in the dedicated reference namespace."""
    target_date = date(2026, 9, 26)
    b_local = "dummy_bridge.parquet"
    r_local = "dummy_resolution.parquet"

    b_s3_key, r_s3_key = upload_crosswalk_to_s3(
        bridge_local_path=b_local,
        resolution_local_path=r_local,
        resolution_date=target_date,
        bucket_name="my-test-bucket",
    )

    expected_bridge_key = (
        "reference/asteroid_crosswalk/bridge_asteroid_identifier/"
        "year=2026/month=09/day=26/bridge_asteroid_identifier.parquet"
    )
    expected_resolution_key = (
        "reference/asteroid_crosswalk/fact_entity_resolution/"
        "year=2026/month=09/day=26/fact_entity_resolution.parquet"
    )

    assert b_s3_key == expected_bridge_key
    assert r_s3_key == expected_resolution_key
    assert mock_upload.call_count == 2


@patch("entity_resolution.upload_file_to_s3", side_effect=ClientError({"Error": {"Code": "500", "Message": "S3 Error"}}, "PutObject"))
def test_upload_crosswalk_to_s3_error_handling(mock_upload):
    """Verify S3 upload error propagates without silent swallowing."""
    with pytest.raises(ClientError):
        upload_crosswalk_to_s3(
            bridge_local_path="b.parquet",
            resolution_local_path="r.parquet",
            resolution_date=date(2026, 9, 26),
        )


@patch("entity_resolution.upload_file_to_s3", side_effect=BotoCoreError())
def test_upload_crosswalk_to_s3_botocore_error(mock_upload):
    """Verify BotoCoreError propagates without silent swallowing."""
    with pytest.raises(BotoCoreError):
        upload_crosswalk_to_s3(
            bridge_local_path="b.parquet",
            resolution_local_path="r.parquet",
            resolution_date=date(2026, 9, 26),
        )


# ===========================================================================
# 14. CLI Orchestration & Main Entrypoint Tests
# ===========================================================================

@patch("entity_resolution.upload_crosswalk_to_s3")
def test_main_orchestration_success(mock_s3, tmp_path):
    """Verify main() end-to-end execution with in-memory records."""
    exit_code = main(
        neows_records=[FIXTURE_2025_HX_NEOWS],
        sbdb_records=[FIXTURE_2025_HX_SBDB],
        sentry_records=[FIXTURE_2025_HX_SENTRY],
        resolution_date_str="2026-09-26",
        output_dir=str(tmp_path),
        upload_s3=True,
    )

    assert exit_code == 0
    assert mock_s3.call_count == 1
    assert os.path.exists(os.path.join(str(tmp_path), "bridge_asteroid_identifier.parquet"))
    assert os.path.exists(os.path.join(str(tmp_path), "fact_entity_resolution.parquet"))


def test_main_invalid_date_returns_code_1():
    """Verify main() rejects invalid date formats."""
    exit_code = main(resolution_date_str="invalid-date")
    assert exit_code == 1


# ===========================================================================
# 15. Empty Inputs Safety Test
# ===========================================================================

def test_empty_inputs_safety(tmp_path):
    """Verify resolve_entities handles empty inputs without throwing errors."""
    bridge, audit, metrics = resolve_entities([], [], [], run_id="run_empty")
    assert len(bridge) == 0
    assert len(audit) == 0
    assert metrics["total_evaluated"] == 0

    # Ensure empty outputs can be saved as valid Parquet tables
    b_path, r_path = save_resolution_outputs(bridge, audit, output_dir=str(tmp_path))
    assert os.path.exists(b_path)
    assert os.path.exists(r_path)
    assert pq.read_table(b_path).num_rows == 0


# ---------------------------------------------------------------------------
# M8: all-35 NeoWs -> SBDB identity resolution (exact fallback rules)
# ---------------------------------------------------------------------------

def _neows_audit(audit, neows_id):
    rows = [a for a in audit if a["source_system"] == "neows" and a["source_identifier_value"] == neows_id]
    assert len(rows) == 1
    return rows[0]


def test_numbered_asteroid_resolves_by_exact_sbdb_fullname():
    """Numbered objects: NeoWs id 2138971 != SPK-ID 20138971 and NeoWs name "138971 (2001 CB21)" !=
    SBDB des "138971", but the name equals SBDB's fullname exactly."""
    bridge, audit, _ = resolve_entities(
        neows_records=[{"id": "2138971", "name": "138971 (2001 CB21)"}],
        sbdb_records=[{"spkid": "20138971", "designation": "138971", "fullname": "138971 (2001 CB21)",
                       "alternate_designations": ["2001 CB21"]}],
        run_id="run_numbered",
    )
    a = _neows_audit(audit, "2138971")
    assert a["match_state"] == STATE_RESOLVED
    assert a["match_rule"] == RULE_EXACT_FULLNAME
    assert a["assigned_asteroid_key"] == generate_asteroid_key("20138971")
    assert a["matched_target_identifier_value"] == "138971"
    assert json.loads(a["evidence_json"])["matched_sbdb_field"] == "fullname"
    assert {(b["source_system"], b["identifier_value"]) for b in bridge} >= {("neows", "2138971"), ("sbdb", "20138971")}


def test_alternate_designation_links_second_neows_record_to_same_object():
    """NeoWs lists 2001 SY169 (3092330) and 2013 ET (3629117) separately; SBDB knows 2013 ET as an
    alternate designation of 2001 SY169. Both NeoWs ids map to the one canonical key."""
    sbdb = [{"spkid": "50092353", "designation": "2001 SY169", "fullname": "(2001 SY169)", "alternate_designations": ["2013 ET"]}]
    neows = [{"id": "3092330", "name": "(2001 SY169)"}, {"id": "3629117", "name": "(2013 ET)"}]
    bridge, audit, _ = resolve_entities(neows_records=neows, sbdb_records=sbdb, run_id="run_alt")
    key = generate_asteroid_key("50092353")
    assert _neows_audit(audit, "3092330")["match_rule"] == RULE_EXACT_DESIGNATION
    alt = _neows_audit(audit, "3629117")
    assert (alt["match_state"], alt["match_rule"], alt["assigned_asteroid_key"]) == (STATE_RESOLVED, RULE_ALTERNATE_DESIGNATION, key)
    assert json.loads(alt["evidence_json"])["matched_sbdb_field"] == "alternate_designation"
    neows_bridge = sorted(b["identifier_value"] for b in bridge if b["source_system"] == "neows" and b["asteroid_key"] == key)
    assert neows_bridge == ["3092330", "3629117"]
    assert sum(1 for b in bridge if b["is_primary_pivot"] and b["asteroid_key"] == key) == 1


def test_designation_match_keeps_precedence_over_fallbacks():
    """An exact SBDB designation always wins; the fallback indexes are consulted only when it finds nothing."""
    sbdb = [
        {"spkid": "1", "designation": "2020 AB", "fullname": "(2020 AB)", "alternate_designations": []},
        {"spkid": "2", "designation": "2019 ZZ", "fullname": "(2019 ZZ)", "alternate_designations": ["2020 AB"]},
    ]
    _, audit, _ = resolve_entities(neows_records=[{"id": "9001", "name": "(2020 AB)"}], sbdb_records=sbdb, run_id="run_prec")
    a = _neows_audit(audit, "9001")
    assert (a["match_rule"], a["assigned_asteroid_key"]) == (RULE_EXACT_DESIGNATION, generate_asteroid_key("1"))


def test_fallback_candidates_for_different_objects_are_quarantined():
    """If the fullname and an alternate designation point at different SPK-IDs, nothing is guessed."""
    sbdb = [
        {"spkid": "1", "designation": "111", "fullname": "111 (2001 AA)", "alternate_designations": []},
        {"spkid": "2", "designation": "2002 BB", "fullname": "(2002 BB)", "alternate_designations": ["111 (2001 AA)"]},
    ]
    bridge, audit, _ = resolve_entities(neows_records=[{"id": "9002", "name": "111 (2001 AA)"}], sbdb_records=sbdb, run_id="run_amb")
    a = _neows_audit(audit, "9002")
    assert a["match_state"] == STATE_AMBIGUOUS
    assert a["assigned_asteroid_key"] is None
    assert sorted(json.loads(a["evidence_json"])["conflicting_spkids"]) == ["1", "2"]
    assert not any(b["source_system"] == "neows" for b in bridge)


@pytest.mark.parametrize("alternate_designations", [None, [], "missing"])
def test_fallback_is_exact_and_tolerates_uncaptured_alternates(alternate_designations):
    """Older SBDB snapshots have no alternate designations (null / absent): resolution still works by
    fullname, and a near-miss name stays UNRESOLVED (no partial or token matching)."""
    sbdb_row = {"spkid": "20138971", "designation": "138971", "fullname": "138971 (2001 CB21)"}
    if alternate_designations != "missing":
        sbdb_row["alternate_designations"] = alternate_designations
    neows = [{"id": "2138971", "name": "138971 (2001 CB21)"}, {"id": "9003", "name": "138971 (2001 CB2)"}]
    _, audit, _ = resolve_entities(neows_records=neows, sbdb_records=[sbdb_row], run_id="run_tolerant")
    assert _neows_audit(audit, "2138971")["match_rule"] == RULE_EXACT_FULLNAME
    near = _neows_audit(audit, "9003")
    assert (near["match_state"], near["match_rule"]) == (STATE_UNRESOLVED, RULE_NO_MATCH)


def test_real_35_neows_objects_all_resolve_with_sentry_only_where_linked():
    """The real 35 NeoWs records against the real SBDB identities of the same objects: every record
    gets an explicit outcome (all RESOLVED here), and Sentry links come only from the crosswalk."""
    bridge, audit, _ = resolve_entities(
        neows_records=_FIXTURE_ASTEROIDS,
        sbdb_records=_FIXTURE_SBDB_IDENTITIES_ALL35,
        sentry_records=_FIXTURE_SENTRY,
        run_id="run_all35",
    )
    neows = [a for a in audit if a["source_system"] == "neows"]
    assert len(neows) == 35 and {a["source_identifier_value"] for a in neows} == {r["id"] for r in _FIXTURE_ASTEROIDS}
    assert all(a["match_state"] == STATE_RESOLVED for a in neows)
    assert Counter(a["match_rule"] for a in neows) == {RULE_EXACT_DESIGNATION: 30, RULE_EXACT_FULLNAME: 4, RULE_ALTERNATE_DESIGNATION: 1}
    assert len({a["assigned_asteroid_key"] for a in neows}) == 34  # 2001 SY169 and 2013 ET are one object
    assert _neows_audit(audit, "3092330")["assigned_asteroid_key"] == _neows_audit(audit, "3629117")["assigned_asteroid_key"]

    linked_sentry = {b["asteroid_key"] for b in bridge if b["source_system"] == "sentry"}
    sentry_neows = sorted(a["source_identifier_value"] for a in neows if a["assigned_asteroid_key"] in linked_sentry)
    assert sentry_neows == ["3427460", "3548666"]  # 2008 ST, 2010 TW54: the only Mode S listings
    pha_ids = {r["id"] for r in _FIXTURE_ASTEROIDS if r["hazardous"] is True}
    assert pha_ids and not (pha_ids & set(sentry_neows))  # PHA is not Sentry linkage
