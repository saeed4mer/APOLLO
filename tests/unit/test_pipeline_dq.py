"""Unit and integration test suite for the Operational Data Quality Engine (pipeline_dq.py).

Verifies the locked architecture and strict CLI responsibility boundaries:
1. check-ingestion:
   - NeoWs: authoritative summary validity, gate thresholds, summary-to-Parquet lineage.
   - Sentry: snapshot existence, numerical bounds, sentry_id grain.
   - SBDB: circuit breaker validity, thresholds, suppression/object lineage.
   - Passes without evaluating approach windows, partition dates, or cross-table integrity.

2. check-outputs:
   - Pre-resolution artifact accounting (CURRENT_PRODUCTION=8, HISTORICAL_BACKFILL=7).
   - Non-empty output files and zero orphan SBDB tables.
   - SBDB structural grain uniqueness (snapshot_key, spkid) and child table referential integrity.
   - NeoWs approach-window validation (start_date <= closest_approach_date <= end_date).
   - Sentry snapshot-date validation (snapshot_key and snapshot_time).
   - SBDB snapshot-date validation (snapshot_key and snapshot_time).

3. check-crosswalk:
   - Bridge & audit invariants only (1 primary pivot, source uniqueness, single run_id, zero AMBIGUOUS).

4. run-suite:
   - Unified orchestration aggregating all categories without duplicate ownership.
"""

import json
from unittest.mock import MagicMock

from botocore.exceptions import ClientError
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

import pipeline_dq
from pipeline_dq import (
    EXEC_MODE_BACKFILL,
    EXEC_MODE_CURRENT,
    DQStatus,
    check_crosswalk,
    check_neows_ingestion,
    check_s3_publication,
    check_sbdb_ingestion,
    check_sentry_ingestion,
    check_source_outputs,
    main,
    run_full_suite,
)


# ---------------------------------------------------------------------------
# Fixture Helpers
# ---------------------------------------------------------------------------
def write_json(path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def write_parquet(path, records, schema=None, metadata=None):
    if schema:
        table = pa.Table.from_pylist(records, schema=schema)
    else:
        table = pa.Table.from_pylist(records)

    if metadata:
        existing_meta = dict(table.schema.metadata or {})
        for k, v in metadata.items():
            existing_meta[k.encode("utf-8") if isinstance(k, str) else k] = (
                v.encode("utf-8") if isinstance(v, str) else v
            )
        table = table.replace_schema_metadata(existing_meta)

    pq.write_table(table, path)


# ---------------------------------------------------------------------------
# Test Suite 1: check-ingestion Gate Responsibilities
# ---------------------------------------------------------------------------
def test_neows_ingestion_success(tmp_path):
    summary_file = tmp_path / "neows_summary.json"
    parquet_file = tmp_path / "asteroids.parquet"
    run_id = "test_run_123"

    summary_data = {
        "run_id": run_id,
        "records_received": 10,
        "valid_records": 10,
        "skipped_records": 0,
        "rejection_pct": 0.0,
        "gate_passed": True,
    }
    write_json(summary_file, summary_data)

    records = [
        {"id": f"A{i}", "name": f"Asteroid {i}", "closest_approach_date": "2026-09-22", "hazardous": False}
        for i in range(10)
    ]
    write_parquet(parquet_file, records, metadata={"run_id": run_id})

    results = check_neows_ingestion(str(summary_file), str(parquet_file))
    assert len(results) == 2
    assert all(r.status == DQStatus.PASSED for r in results)
    assert results[0].check_name == "neows_gate_validation"
    assert results[1].check_name == "neows_parquet_lineage"


def test_neows_ingestion_summary_missing(tmp_path):
    non_existent = tmp_path / "absent_neows_summary.json"
    results = check_neows_ingestion(str(non_existent), str(tmp_path / "asteroids.parquet"))

    assert len(results) == 3
    assert results[0].check_name == "neows_summary_presence"
    assert results[0].status == DQStatus.FAILED
    assert results[1].status == DQStatus.BLOCKED
    assert results[2].status == DQStatus.BLOCKED


def test_neows_ingestion_gate_failed_suppression_verified(tmp_path):
    summary_file = tmp_path / "neows_summary.json"
    parquet_file = tmp_path / "asteroids.parquet"

    summary_data = {
        "run_id": "failed_run",
        "records_received": 10,
        "valid_records": 5,
        "skipped_records": 5,
        "rejection_pct": 50.0,
        "gate_passed": False,
    }
    write_json(summary_file, summary_data)

    results = check_neows_ingestion(str(summary_file), str(parquet_file))
    assert results[0].status == DQStatus.FAILED
    assert results[1].status == DQStatus.NOT_APPLICABLE


def test_neows_ingestion_gate_failed_but_parquet_leaked(tmp_path):
    summary_file = tmp_path / "neows_summary.json"
    parquet_file = tmp_path / "asteroids.parquet"

    summary_data = {
        "run_id": "failed_run",
        "records_received": 10,
        "valid_records": 5,
        "skipped_records": 5,
        "rejection_pct": 50.0,
        "gate_passed": False,
    }
    write_json(summary_file, summary_data)
    write_parquet(parquet_file, [{"id": "1", "closest_approach_date": "2026-09-22"}])

    results = check_neows_ingestion(str(summary_file), str(parquet_file))
    assert results[0].status == DQStatus.FAILED
    assert results[1].status == DQStatus.FAILED
    assert "Production Parquet exists despite NeoWs extraction gate failure" in results[1].diagnostic


def test_neows_ingestion_parquet_missing_after_gate_passed(tmp_path):
    summary_file = tmp_path / "neows_summary.json"
    summary_data = {
        "run_id": "succ_run",
        "records_received": 5,
        "valid_records": 5,
        "skipped_records": 0,
        "rejection_pct": 0.0,
        "gate_passed": True,
    }
    write_json(summary_file, summary_data)

    results = check_neows_ingestion(str(summary_file), str(tmp_path / "non_existent.parquet"))
    assert results[0].status == DQStatus.PASSED
    assert results[1].status == DQStatus.FAILED
    assert "NeoWs Parquet missing after successful extraction" in results[1].diagnostic


def test_neows_ingestion_ignores_approach_window_boundary_isolation(tmp_path):
    """Boundary isolation: check-ingestion validates lineage, NOT window bounds."""
    summary_file = tmp_path / "neows_summary.json"
    parquet_file = tmp_path / "asteroids.parquet"
    run_id = "run_iso_1"

    summary_data = {
        "run_id": run_id,
        "records_received": 1,
        "valid_records": 1,
        "skipped_records": 0,
        "rejection_pct": 0.0,
        "gate_passed": True,
    }
    write_json(summary_file, summary_data)

    # Date 2099-12-31 is intentionally outside normal window
    records = [{"id": "A1", "closest_approach_date": "2099-12-31"}]
    write_parquet(parquet_file, records, metadata={"run_id": run_id})

    # check-ingestion MUST pass because row count and run_id match
    results = check_neows_ingestion(str(summary_file), str(parquet_file))
    assert all(r.status == DQStatus.PASSED for r in results)


def test_sentry_ingestion_success(tmp_path):
    parquet_file = tmp_path / "fact_sentry_risk_snapshot.parquet"
    records = [
        {"snapshot_key": "2026-09-28", "sentry_id": "s1", "impact_probability": 0.005},
        {"snapshot_key": "2026-09-28", "sentry_id": "s2", "impact_probability": 0.01},
    ]
    write_parquet(parquet_file, records)

    results = check_sentry_ingestion(str(parquet_file))
    assert len(results) == 1
    assert results[0].status == DQStatus.PASSED


def test_sentry_ingestion_duplicate_grain(tmp_path):
    parquet_file = tmp_path / "fact_sentry_risk_snapshot.parquet"
    records = [
        {"sentry_id": "DUP_ID", "impact_probability": 0.01},
        {"sentry_id": "DUP_ID", "impact_probability": 0.02},
    ]
    write_parquet(parquet_file, records)

    results = check_sentry_ingestion(str(parquet_file))
    assert results[0].status == DQStatus.FAILED
    assert "Duplicate sentry_id" in results[0].diagnostic


def test_sentry_ingestion_bounds_violation(tmp_path):
    parquet_file = tmp_path / "fact_sentry_risk_snapshot.parquet"
    records = [{"sentry_id": "S1", "impact_probability": 1.5}]
    write_parquet(parquet_file, records)

    results = check_sentry_ingestion(str(parquet_file))
    assert results[0].status == DQStatus.FAILED
    assert "impact_probability out of bounds" in results[0].diagnostic


def test_sentry_ingestion_backfill_omission():
    results = check_sentry_ingestion(None, is_backfill=True)
    assert len(results) == 1
    assert results[0].status == DQStatus.NOT_APPLICABLE


def test_sbdb_ingestion_success(tmp_path):
    summary_file = tmp_path / "sbdb_batch_summary.json"
    summary_data = {
        "run_id": "sbdb_run_1",
        "total_targets": 2,
        "successful_targets_count": 2,
        "failed_targets_count": 0,
        "failure_rate_pct": 0.0,
        "circuit_breaker_passed": True,
    }
    write_json(summary_file, summary_data)

    obj_records = [
        {"snapshot_key": "2026-09-28", "spkid": "1001"},
        {"snapshot_key": "2026-09-28", "spkid": "1002"},
    ]
    write_parquet(tmp_path / "fact_sbdb_object_snapshot.parquet", obj_records)

    results = check_sbdb_ingestion(str(summary_file), base_dir=str(tmp_path))
    assert len(results) == 2
    assert results[0].status == DQStatus.PASSED
    assert results[1].status == DQStatus.PASSED


def test_sbdb_ingestion_accounts_for_same_object_duplicate_targets(tmp_path):
    """3 targets, 2 distinct SPK-IDs: one duplicate target is neither a success row nor a failure.

    successful (2) == object rows (2), and successful + failed + duplicate == total (3)."""
    summary_file = tmp_path / "sbdb_batch_summary.json"
    summary_file.write_text(json.dumps({
        "run_id": "run_sbdb_dup",
        "total_targets": 3,
        "successful_targets_count": 2,
        "failed_targets_count": 0,
        "duplicate_targets_count": 1,
        "duplicate_targets": [{"target": "3629117", "spkid": "50092353"}],
        "failure_rate_pct": 0.0,
        "circuit_breaker_passed": True,
    }), encoding="utf-8")
    write_parquet(
        tmp_path / "fact_sbdb_object_snapshot.parquet",
        [{"spkid": "50092353", "snapshot_key": "2026-10-02"}, {"spkid": "50548689", "snapshot_key": "2026-10-02"}],
    )
    results = check_sbdb_ingestion(str(summary_file), base_dir=str(tmp_path))
    by_name = {r.check_name: r for r in results}
    assert by_name["sbdb_circuit_breaker"].status == DQStatus.PASSED
    assert by_name["sbdb_circuit_breaker"].metrics["duplicate_targets_count"] == 1
    assert by_name["sbdb_ingestion_lineage"].status == DQStatus.PASSED


def test_sbdb_ingestion_rejects_unaccounted_targets(tmp_path):
    """Without the duplicate count, 2 successes of 3 targets with 0 failures does not reconcile."""
    summary_file = tmp_path / "sbdb_batch_summary.json"
    summary_file.write_text(json.dumps({
        "run_id": "run_sbdb_gap",
        "total_targets": 3,
        "successful_targets_count": 2,
        "failed_targets_count": 0,
        "failure_rate_pct": 0.0,
        "circuit_breaker_passed": True,
    }), encoding="utf-8")
    write_parquet(
        tmp_path / "fact_sbdb_object_snapshot.parquet",
        [{"spkid": "50092353", "snapshot_key": "2026-10-02"}, {"spkid": "50548689", "snapshot_key": "2026-10-02"}],
    )
    results = check_sbdb_ingestion(str(summary_file), base_dir=str(tmp_path))
    assert {r.check_name: r for r in results}["sbdb_circuit_breaker"].status == DQStatus.FAILED


def test_sbdb_ingestion_ignores_child_table_integrity_boundary_isolation(tmp_path):
    """Boundary isolation: check-ingestion validates object count, NOT child table integrity."""
    summary_file = tmp_path / "sbdb_batch_summary.json"
    summary_data = {
        "run_id": "sbdb_run_iso",
        "total_targets": 1,
        "successful_targets_count": 1,
        "failed_targets_count": 0,
        "failure_rate_pct": 0.0,
        "circuit_breaker_passed": True,
    }
    write_json(summary_file, summary_data)
    write_parquet(tmp_path / "fact_sbdb_object_snapshot.parquet", [{"spkid": "1001"}])

    # Even if child tables are missing or corrupted, check-ingestion passes its gate
    results = check_sbdb_ingestion(str(summary_file), base_dir=str(tmp_path))
    assert all(r.status == DQStatus.PASSED for r in results)


def test_sbdb_circuit_breaker_trip_suppression_verified(tmp_path):
    summary_file = tmp_path / "sbdb_batch_summary.json"
    summary_data = {
        "run_id": "cb_fail_run",
        "total_targets": 2,
        "successful_targets_count": 1,
        "failed_targets_count": 1,
        "failure_rate_pct": 50.0,
        "circuit_breaker_passed": False,
    }
    write_json(summary_file, summary_data)

    results = check_sbdb_ingestion(str(summary_file), base_dir=str(tmp_path))
    assert results[0].status == DQStatus.FAILED
    assert results[1].status == DQStatus.NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Test Suite 2: check-outputs Gate Responsibilities
# ---------------------------------------------------------------------------
def _setup_valid_outputs(tmp_path):
    """Helper to populate all 8 valid pre-resolution outputs."""
    write_json(tmp_path / "neows_summary.json", {
        "start_date": "2026-09-20", "end_date": "2026-09-26"
    })
    write_parquet(tmp_path / "asteroids.parquet", [
        {"id": "1", "closest_approach_date": "2026-09-22"}
    ])
    write_parquet(tmp_path / "fact_sentry_risk_snapshot.parquet", [
        {"snapshot_key": "2026-09-28", "snapshot_time": "2026-09-28T12:00:00Z", "sentry_id": "S1"}
    ])
    write_json(tmp_path / "sbdb_batch_summary.json", {
        "snapshot_key": "2026-09-28"
    })
    write_parquet(tmp_path / "fact_sbdb_object_snapshot.parquet", [
        {"snapshot_key": "2026-09-28", "snapshot_time": "2026-09-28T12:00:00Z", "spkid": "1001"}
    ])
    write_parquet(tmp_path / "fact_sbdb_orbit.parquet", [
        {"snapshot_key": "2026-09-28", "snapshot_time": "2026-09-28T12:00:00Z", "spkid": "1001"}
    ])
    write_parquet(tmp_path / "fact_sbdb_orbit_element.parquet", [{"spkid": "1001"}])
    write_parquet(tmp_path / "fact_sbdb_physical_parameter.parquet", [{"spkid": "1001"}])


def test_check_outputs_current_production_success(tmp_path):
    _setup_valid_outputs(tmp_path)
    results = check_source_outputs(output_dir=str(tmp_path), execution_mode=EXEC_MODE_CURRENT, snapshot_date="2026-09-28")
    assert len(results) == 3
    assert all(r.status == DQStatus.PASSED for r in results)
    assert results[0].check_name == "pre_resolution_output_accounting"
    assert results[1].check_name == "sbdb_structural_integrity"
    assert results[2].check_name == "source_partition_dates"


def test_check_outputs_fails_on_neows_approach_window_violation(tmp_path):
    _setup_valid_outputs(tmp_path)
    # Replace asteroids.parquet with date outside [2026-09-20, 2026-09-26]
    write_parquet(tmp_path / "asteroids.parquet", [
        {"id": "1", "closest_approach_date": "2026-10-15"}
    ])
    results = check_source_outputs(output_dir=str(tmp_path), execution_mode=EXEC_MODE_CURRENT)
    assert results[0].status == DQStatus.PASSED  # accounting ok
    assert results[2].status == DQStatus.FAILED  # date window failed
    assert "NeoWs approach dates outside window" in results[2].diagnostic


def test_check_outputs_fails_on_sentry_partition_date_mismatch(tmp_path):
    _setup_valid_outputs(tmp_path)
    # Expect 2026-09-30 when Sentry has 2026-09-28
    results = check_source_outputs(output_dir=str(tmp_path), execution_mode=EXEC_MODE_CURRENT, snapshot_date="2026-09-30")
    assert results[2].status == DQStatus.FAILED
    assert "Sentry partition date mismatch" in results[2].diagnostic


def test_check_outputs_fails_on_sbdb_duplicate_grain(tmp_path):
    _setup_valid_outputs(tmp_path)
    # Duplicate (snapshot_key, spkid) in object table
    write_parquet(tmp_path / "fact_sbdb_object_snapshot.parquet", [
        {"snapshot_key": "2026-09-28", "spkid": "1001"},
        {"snapshot_key": "2026-09-28", "spkid": "1001"},
    ])
    results = check_source_outputs(output_dir=str(tmp_path), execution_mode=EXEC_MODE_CURRENT)
    assert results[1].status == DQStatus.FAILED
    assert "Object table grain (snapshot_key, spkid) contains duplicate keys" in results[1].diagnostic


def test_check_outputs_fails_on_sbdb_orphan_child_records(tmp_path):
    _setup_valid_outputs(tmp_path)
    # Child table has spkid 9999 not in object snapshot
    write_parquet(tmp_path / "fact_sbdb_orbit_element.parquet", [{"spkid": "9999"}])
    results = check_source_outputs(output_dir=str(tmp_path), execution_mode=EXEC_MODE_CURRENT)
    assert results[1].status == DQStatus.FAILED
    assert "Orphan spkid detected" in results[1].diagnostic


def test_check_outputs_fails_on_orphan_sbdb_tables(tmp_path):
    _setup_valid_outputs(tmp_path)
    # Remove one of the 4 SBDB tables
    (tmp_path / "fact_sbdb_physical_parameter.parquet").unlink()
    results = check_source_outputs(output_dir=str(tmp_path), execution_mode=EXEC_MODE_CURRENT)
    assert results[0].status == DQStatus.FAILED
    assert "Orphan SBDB tables detected" in results[0].diagnostic


def test_check_outputs_historical_backfill_omits_sentry(tmp_path):
    _setup_valid_outputs(tmp_path)
    # Remove Sentry in backfill mode
    (tmp_path / "fact_sentry_risk_snapshot.parquet").unlink()
    results = check_source_outputs(output_dir=str(tmp_path), execution_mode=EXEC_MODE_BACKFILL)
    assert results[0].status == DQStatus.PASSED
    assert results[0].metrics["expected_artifacts_count"] == 7
    assert results[0].metrics["found_artifacts_count"] == 7


# ---------------------------------------------------------------------------
# Test Suite 3: check-crosswalk Gate Responsibilities
# ---------------------------------------------------------------------------
def test_crosswalk_invariants_success(tmp_path):
    bridge_file = tmp_path / "bridge_asteroid_identifier.parquet"
    audit_file = tmp_path / "fact_entity_resolution.parquet"

    bridge_records = [
        {"asteroid_key": "ast_1", "source_system": "NASA_NEOWS", "identifier_name": "ID", "identifier_value": "12345", "is_primary_pivot": True},
        {"asteroid_key": "ast_1", "source_system": "NASA_SBDB", "identifier_name": "SPKID", "identifier_value": "54321", "is_primary_pivot": False},
    ]
    write_parquet(bridge_file, bridge_records)

    audit_records = [
        {"resolution_run_id": "run_res_999", "assigned_asteroid_key": "ast_1", "match_state": "RESOLVED"},
    ]
    write_parquet(audit_file, audit_records)

    results = check_crosswalk(str(bridge_file), str(audit_file))
    assert len(results) == 2
    assert results[0].status == DQStatus.PASSED
    assert results[1].status == DQStatus.PASSED


def test_crosswalk_multiple_primary_pivots_fails(tmp_path):
    bridge_file = tmp_path / "bridge_asteroid_identifier.parquet"
    bridge_records = [
        {"asteroid_key": "ast_1", "source_system": "S1", "identifier_name": "ID", "identifier_value": "1", "is_primary_pivot": True},
        {"asteroid_key": "ast_1", "source_system": "S2", "identifier_name": "ID", "identifier_value": "2", "is_primary_pivot": True},
    ]
    write_parquet(bridge_file, bridge_records)

    results = check_crosswalk(str(bridge_file))
    assert results[0].status == DQStatus.FAILED
    assert "do not have exactly 1 primary pivot" in results[0].diagnostic


def test_crosswalk_ambiguous_record_fails(tmp_path):
    bridge_file = tmp_path / "bridge_asteroid_identifier.parquet"
    audit_file = tmp_path / "fact_entity_resolution.parquet"

    write_parquet(bridge_file, [
        {"asteroid_key": "ast_1", "source_system": "S1", "identifier_name": "ID", "identifier_value": "1", "is_primary_pivot": True}
    ])
    write_parquet(audit_file, [
        {"resolution_run_id": "r1", "assigned_asteroid_key": "ast_1", "match_state": "AMBIGUOUS"}
    ])

    results = check_crosswalk(str(bridge_file), str(audit_file))
    assert results[1].status == DQStatus.FAILED
    assert "AMBIGUOUS records found" in results[1].diagnostic


# ---------------------------------------------------------------------------
# Test Suite 4: check-s3-publication
# ---------------------------------------------------------------------------
def test_s3_publication_offline_default():
    results = check_s3_publication(["key1.parquet"], verify_s3=False)
    assert results[0].status == DQStatus.NOT_APPLICABLE


def test_s3_publication_live_mocked_success():
    mock_s3 = MagicMock()
    mock_s3.head_object.return_value = {"ContentLength": 1024, "ETag": '"etag1"'}
    results = check_s3_publication(["test.parquet"], verify_s3=True, s3_client=mock_s3)
    assert results[0].status == DQStatus.PASSED


def test_s3_publication_live_missing_key():
    mock_s3 = MagicMock()
    mock_s3.head_object.side_effect = ClientError({"Error": {"Code": "404"}}, "HeadObject")
    results = check_s3_publication(["missing.parquet"], verify_s3=True, s3_client=mock_s3)
    assert results[0].status == DQStatus.FAILED


# ---------------------------------------------------------------------------
# Test Suite 5: CLI, Exit Codes & run-suite Aggregation
# ---------------------------------------------------------------------------
def test_run_full_suite_aggregation(tmp_path):
    _setup_valid_outputs(tmp_path)
    # Add valid summary metrics for ingestion checks
    write_json(tmp_path / "neows_summary.json", {
        "run_id": "r1", "start_date": "2026-09-20", "end_date": "2026-09-26",
        "records_received": 1, "valid_records": 1, "skipped_records": 0, "rejection_pct": 0.0, "gate_passed": True
    })
    write_parquet(tmp_path / "asteroids.parquet", [{"id": "1", "closest_approach_date": "2026-09-22"}], metadata={"run_id": "r1"})

    write_json(tmp_path / "sbdb_batch_summary.json", {
        "run_id": "r1", "snapshot_key": "2026-09-28", "total_targets": 1, "successful_targets_count": 1,
        "failed_targets_count": 0, "failure_rate_pct": 0.0, "circuit_breaker_passed": True
    })

    write_parquet(tmp_path / "bridge_asteroid_identifier.parquet", [
        {"asteroid_key": "k1", "source_system": "S1", "identifier_name": "ID", "identifier_value": "1", "is_primary_pivot": True}
    ])
    write_parquet(tmp_path / "fact_entity_resolution.parquet", [
        {"resolution_run_id": "r1", "assigned_asteroid_key": "k1", "match_state": "RESOLVED"}
    ])

    report = run_full_suite(output_dir=str(tmp_path), execution_mode=EXEC_MODE_CURRENT, snapshot_date="2026-09-28")
    assert report.overall_status == DQStatus.PASSED
    assert report.exit_code == 0

    # Ensure all categories are aggregated
    datasets = set(c.dataset for c in report.checks)
    assert "neows" in datasets
    assert "sentry" in datasets
    assert "sbdb" in datasets
    assert "pipeline_outputs" in datasets
    assert "crosswalk" in datasets


def test_cli_subcommands_exit_codes(tmp_path):
    _setup_valid_outputs(tmp_path)
    out_file = tmp_path / "dq_out.json"

    # check-outputs exits 0 on valid outputs
    exit_code = main(["check-outputs", "--output-dir", str(tmp_path), "--output-file", str(out_file)])
    assert exit_code == 0

    # check-outputs exits 1 when output files missing
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    exit_code_fail = main(["check-outputs", "--output-dir", str(empty_dir), "--output-file", str(out_file)])
    assert exit_code_fail == 1


def test_cli_invalid_argument_exits_two():
    with pytest.raises(SystemExit) as exc_info:
        pipeline_dq.parse_args(["check-outputs", "--unrecognized-option"])
    assert exc_info.value.code == 2
