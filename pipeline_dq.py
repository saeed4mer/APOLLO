"""NASA Planetary Defense Risk Intelligence Platform — Operational Data Quality Engine.

Provides centralized, reusable data quality validation contracts across:
- check-ingestion:
    Authoritative summary validity, source gate flags, valid record / rejection /
    failure thresholds, and summary-to-Parquet lineage.
- check-outputs:
    Pre-resolution artifact accounting (CURRENT_PRODUCTION=8, HISTORICAL_BACKFILL=7),
    non-empty output files, SBDB table co-presence, SBDB structural grain & referential
    integrity, NeoWs approach-window validation, Sentry snapshot-date validation, and
    SBDB snapshot-date validation.
- check-crosswalk:
    Post-resolution crosswalk invariants (1 primary pivot per key, source identifier
    uniqueness, single resolution run_id, zero AMBIGUOUS records, audit-bridge referential integrity).
- check-s3-publication:
    S3 object publication verification (offline by default, AWS-independent).
- run-suite:
    Unified end-to-end execution of all quality gates aggregating ingestion, outputs,
    and crosswalk diagnostics into a structured report.

Designed to run locally, in CI testing, and in automated production workflows.
Exit codes:
  0: All checks passed or non-fatal warnings
  1: One or more data quality gates failed
  2: Invocation / argument / runtime configuration error
"""

import argparse
from datetime import datetime, timezone
from enum import Enum
import json
import logging
import os
import sys
import uuid

import pyarrow.parquet as pq

# Optional S3 support (AWS-independent by default)
try:
    import boto3
    from botocore.exceptions import BotoCoreError, ClientError
    BOTO3_AVAILABLE = True
except ImportError:
    BOTO3_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DQ Enums and Constants
# ---------------------------------------------------------------------------
class DQStatus(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    WARNING = "WARNING"
    BLOCKED = "BLOCKED"
    NOT_APPLICABLE = "NOT_APPLICABLE"


EXEC_MODE_CURRENT = "CURRENT_PRODUCTION"
EXEC_MODE_BACKFILL = "HISTORICAL_BACKFILL"

DEFAULT_OUTPUT_DIR = "."
DEFAULT_DQ_RESULT_FILE = "dq_result.json"

NEOWS_SUMMARY_FILE = "neows_summary.json"
SBDB_SUMMARY_FILE = "sbdb_batch_summary.json"
NEOWS_PARQUET_FILE = "asteroids.parquet"
SENTRY_PARQUET_FILES = [
    "fact_sentry_risk_snapshot.parquet",
    "sentry_risk_snapshot.parquet",
]
SBDB_PARQUET_TABLES = [
    "fact_sbdb_object_snapshot.parquet",
    "fact_sbdb_orbit.parquet",
    "fact_sbdb_orbit_element.parquet",
    "fact_sbdb_physical_parameter.parquet",
]
CROSSWALK_BRIDGE_FILES = [
    "bridge_asteroid_identifier.parquet",
    "dim_asteroid_crosswalk.parquet",
]
CROSSWALK_AUDIT_FILES = [
    "fact_entity_resolution.parquet",
    "audit_entity_resolution.parquet",
]


# ---------------------------------------------------------------------------
# Data Quality Check Class & Result Container
# ---------------------------------------------------------------------------
class DQCheckResult:
    """Encapsulates the result of an individual data quality check."""

    def __init__(
        self,
        check_name: str,
        dataset: str,
        status: DQStatus,
        diagnostic: str,
        metrics: dict | None = None,
    ):
        self.check_name = check_name
        self.dataset = dataset
        self.status = status
        self.diagnostic = diagnostic
        self.metrics = metrics or {}

    def to_dict(self) -> dict:
        return {
            "check_name": self.check_name,
            "dataset": self.dataset,
            "status": self.status.value,
            "diagnostic": self.diagnostic,
            "metrics": self.metrics,
        }


class DQReport:
    """Aggregates multiple DQCheckResults into a structured execution report."""

    def __init__(self, stage: str, execution_mode: str = EXEC_MODE_CURRENT, dq_run_id: str | None = None):
        self.dq_run_id = dq_run_id or uuid.uuid4().hex[:12]
        self.stage = stage
        self.execution_mode = execution_mode
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.checks: list[DQCheckResult] = []

    def add_check(self, check: DQCheckResult):
        self.checks.append(check)

    @property
    def overall_status(self) -> DQStatus:
        has_failed = any(c.status == DQStatus.FAILED for c in self.checks)
        if has_failed:
            return DQStatus.FAILED
        has_warning = any(c.status == DQStatus.WARNING for c in self.checks)
        if has_warning:
            return DQStatus.WARNING
        return DQStatus.PASSED

    @property
    def exit_code(self) -> int:
        return 1 if self.overall_status == DQStatus.FAILED else 0

    def get_summary(self) -> dict:
        return {
            "total_checks": len(self.checks),
            "passed": sum(1 for c in self.checks if c.status == DQStatus.PASSED),
            "failed": sum(1 for c in self.checks if c.status == DQStatus.FAILED),
            "warnings": sum(1 for c in self.checks if c.status == DQStatus.WARNING),
            "blocked": sum(1 for c in self.checks if c.status == DQStatus.BLOCKED),
            "not_applicable": sum(1 for c in self.checks if c.status == DQStatus.NOT_APPLICABLE),
        }

    def to_dict(self) -> dict:
        return {
            "dq_run_id": self.dq_run_id,
            "timestamp": self.timestamp,
            "stage": self.stage,
            "execution_mode": self.execution_mode,
            "overall_status": self.overall_status.value,
            "summary": self.get_summary(),
            "checks": [c.to_dict() for c in self.checks],
        }

    def save_json(self, output_path: str = DEFAULT_DQ_RESULT_FILE):
        try:
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2)
            logger.info("Saved DQ execution report to %s", output_path)
        except Exception as e:
            logger.error("Failed to write DQ execution report to %s: %s", output_path, e)


# ---------------------------------------------------------------------------
# File Discovery & Helper Utilities
# ---------------------------------------------------------------------------
def _find_file(candidates: list[str], base_dir: str = ".") -> str | None:
    """Return the first candidate file that exists in base_dir, or None."""
    for c in candidates:
        full_path = os.path.join(base_dir, c) if not os.path.isabs(c) else c
        if os.path.exists(full_path):
            return full_path
    return None


def _safe_load_json(file_path: str) -> tuple[dict | None, str | None]:
    """Safely load JSON file returning (data, error_message)."""
    if not os.path.exists(file_path):
        return None, f"File does not exist: {file_path}"
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f), None
    except Exception as e:
        return None, f"Failed to parse JSON from {file_path}: {e}"


def _safe_read_parquet(file_path: str):
    """Safely read PyArrow Parquet table returning (table, error_message)."""
    if not os.path.exists(file_path):
        return None, f"File does not exist: {file_path}"
    try:
        table = pq.read_table(file_path)
        return table, None
    except Exception as e:
        return None, f"Failed to read Parquet from {file_path}: {e}"


# ---------------------------------------------------------------------------
# Check Suite 1: Ingestion Quality & Lineage Checks (check-ingestion)
# Ownership: Authoritative summary validity, source gate flags, thresholds,
#            and summary-to-Parquet lineage.
# ---------------------------------------------------------------------------
def check_neows_ingestion(
    summary_path: str,
    parquet_path: str | None = None,
) -> list[DQCheckResult]:
    """Validate NeoWs authoritative summary, extraction gate, and summary-to-Parquet lineage."""
    results: list[DQCheckResult] = []

    # 1. Summary JSON existence
    summary_data, err = _safe_load_json(summary_path)
    if err:
        results.append(DQCheckResult(
            check_name="neows_summary_presence",
            dataset="neows",
            status=DQStatus.FAILED,
            diagnostic=f"NeoWs authoritative summary missing or invalid: {err}",
            metrics={"summary_path": summary_path},
        ))
        results.append(DQCheckResult(
            check_name="neows_gate_validation",
            dataset="neows",
            status=DQStatus.BLOCKED,
            diagnostic="Blocked due to missing NeoWs summary artifact",
        ))
        results.append(DQCheckResult(
            check_name="neows_parquet_lineage",
            dataset="neows",
            status=DQStatus.BLOCKED,
            diagnostic="Blocked due to missing NeoWs summary artifact",
        ))
        return results

    # 2. Gate evaluation
    records_received = summary_data.get("records_received", 0)
    valid_records = summary_data.get("valid_records", 0)
    skipped_records = summary_data.get("skipped_records", 0)
    rejection_pct = summary_data.get("rejection_pct", 100.0)
    gate_passed = summary_data.get("gate_passed", False)
    summary_run_id = summary_data.get("run_id")

    math_expected_pct = round((skipped_records / records_received * 100.0), 2) if records_received > 0 else 0.0
    math_valid = abs(rejection_pct - math_expected_pct) <= 0.05

    gate_ok = gate_passed and (valid_records > 0) and (rejection_pct < 20.0) and math_valid

    results.append(DQCheckResult(
        check_name="neows_gate_validation",
        dataset="neows",
        status=DQStatus.PASSED if gate_ok else DQStatus.FAILED,
        diagnostic=(
            f"NeoWs gate passed: valid={valid_records}, skipped={skipped_records}, "
            f"rejection={rejection_pct:.1f}%"
            if gate_ok
            else f"NeoWs gate failed: gate_passed={gate_passed}, valid={valid_records}, "
                 f"rejection={rejection_pct:.1f}% (threshold < 20.0%), math_valid={math_valid}"
        ),
        metrics={
            "records_received": records_received,
            "valid_records": valid_records,
            "skipped_records": skipped_records,
            "rejection_pct": rejection_pct,
            "gate_passed": gate_passed,
            "math_valid": math_valid,
        },
    ))

    # 3. Parquet lineage & suppression validation
    if not gate_passed:
        if parquet_path and os.path.exists(parquet_path):
            results.append(DQCheckResult(
                check_name="neows_parquet_lineage",
                dataset="neows",
                status=DQStatus.FAILED,
                diagnostic="Production Parquet exists despite NeoWs extraction gate failure (suppression violation)",
            ))
        else:
            results.append(DQCheckResult(
                check_name="neows_parquet_lineage",
                dataset="neows",
                status=DQStatus.NOT_APPLICABLE,
                diagnostic="NeoWs Parquet suppressed as expected following gate failure",
            ))
        return results

    if not parquet_path or not os.path.exists(parquet_path):
        results.append(DQCheckResult(
            check_name="neows_parquet_lineage",
            dataset="neows",
            status=DQStatus.FAILED,
            diagnostic=f"NeoWs Parquet missing after successful extraction: {parquet_path}",
            metrics={"expected_valid_records": valid_records},
        ))
        return results

    table, err = _safe_read_parquet(parquet_path)
    if err:
        results.append(DQCheckResult(
            check_name="neows_parquet_lineage",
            dataset="neows",
            status=DQStatus.FAILED,
            diagnostic=f"Failed reading NeoWs Parquet: {err}",
        ))
        return results

    parquet_rows = table.num_rows
    row_count_match = (parquet_rows == valid_records)

    schema_meta = table.schema.metadata or {}
    embedded_run_id = schema_meta.get(b"run_id", b"").decode("utf-8") if b"run_id" in schema_meta else None
    run_id_match = (embedded_run_id == summary_run_id) if summary_run_id and embedded_run_id else True

    lineage_ok = row_count_match and run_id_match
    diag_parts = []
    if not row_count_match:
        diag_parts.append(f"Row count mismatch: parquet={parquet_rows}, summary={valid_records}")
    if not run_id_match:
        diag_parts.append(f"Run ID mismatch: parquet={embedded_run_id}, summary={summary_run_id}")

    results.append(DQCheckResult(
        check_name="neows_parquet_lineage",
        dataset="neows",
        status=DQStatus.PASSED if lineage_ok else DQStatus.FAILED,
        diagnostic=(
            f"NeoWs Parquet lineage verified ({parquet_rows} rows, run_id={summary_run_id})"
            if lineage_ok
            else "; ".join(diag_parts)
        ),
        metrics={
            "parquet_rows": parquet_rows,
            "summary_valid_records": valid_records,
            "embedded_run_id": embedded_run_id,
            "summary_run_id": summary_run_id,
        },
    ))

    return results


def check_sentry_ingestion(
    parquet_path: str | None,
    is_backfill: bool = False,
) -> list[DQCheckResult]:
    """Validate Sentry risk snapshot existence, numerical bounds, and grain."""
    results: list[DQCheckResult] = []

    if is_backfill:
        results.append(DQCheckResult(
            check_name="sentry_snapshot_validation",
            dataset="sentry",
            status=DQStatus.NOT_APPLICABLE,
            diagnostic="Sentry risk snapshot intentionally omitted in historical backfill",
        ))
        return results

    if not parquet_path or not os.path.exists(parquet_path):
        results.append(DQCheckResult(
            check_name="sentry_snapshot_validation",
            dataset="sentry",
            status=DQStatus.FAILED,
            diagnostic=f"Sentry risk snapshot Parquet missing: {parquet_path}",
        ))
        return results

    table, err = _safe_read_parquet(parquet_path)
    if err:
        results.append(DQCheckResult(
            check_name="sentry_snapshot_validation",
            dataset="sentry",
            status=DQStatus.FAILED,
            diagnostic=f"Failed reading Sentry Parquet: {err}",
        ))
        return results

    rows = table.num_rows
    if rows == 0:
        results.append(DQCheckResult(
            check_name="sentry_snapshot_validation",
            dataset="sentry",
            status=DQStatus.FAILED,
            diagnostic="Sentry risk snapshot is empty (0 rows)",
        ))
        return results

    col_names = table.column_names
    records = table.to_pylist()

    sentry_ids = [r.get("sentry_id") for r in records if r.get("sentry_id")]
    unique_ids = len(set(sentry_ids))
    grain_ok = (unique_ids == rows)

    bounds_ok = True
    bad_prob_count = 0
    if "impact_probability" in col_names:
        for r in records:
            ip = r.get("impact_probability")
            if ip is not None and not (0.0 <= ip <= 1.0):
                bounds_ok = False
                bad_prob_count += 1

    all_ok = grain_ok and bounds_ok
    diag_msgs = []
    if not grain_ok:
        diag_msgs.append(f"Duplicate sentry_id values detected ({rows - unique_ids} duplicates)")
    if not bounds_ok:
        diag_msgs.append(f"{bad_prob_count} records with impact_probability out of bounds [0, 1]")

    results.append(DQCheckResult(
        check_name="sentry_snapshot_validation",
        dataset="sentry",
        status=DQStatus.PASSED if all_ok else DQStatus.FAILED,
        diagnostic=(
            f"Sentry risk snapshot verified ({rows} objects, grain unique, bounds valid)"
            if all_ok
            else "; ".join(diag_msgs)
        ),
        metrics={
            "row_count": rows,
            "unique_sentry_ids": unique_ids,
            "grain_unique": grain_ok,
            "bounds_valid": bounds_ok,
        },
    ))

    return results


def check_sbdb_ingestion(
    summary_path: str,
    base_dir: str = ".",
) -> list[DQCheckResult]:
    """Validate SBDB circuit breaker metrics, failure thresholds, and suppression/lineage."""
    results: list[DQCheckResult] = []

    summary_data, err = _safe_load_json(summary_path)
    if err:
        results.append(DQCheckResult(
            check_name="sbdb_summary_presence",
            dataset="sbdb",
            status=DQStatus.FAILED,
            diagnostic=f"SBDB batch summary missing or invalid: {err}",
            metrics={"summary_path": summary_path},
        ))
        results.append(DQCheckResult(
            check_name="sbdb_circuit_breaker",
            dataset="sbdb",
            status=DQStatus.BLOCKED,
            diagnostic="Blocked due to missing SBDB summary",
        ))
        results.append(DQCheckResult(
            check_name="sbdb_ingestion_lineage",
            dataset="sbdb",
            status=DQStatus.BLOCKED,
            diagnostic="Blocked due to missing SBDB summary",
        ))
        return results

    total_targets = summary_data.get("total_targets", 0)
    successful_targets = summary_data.get("successful_targets_count", 0)
    failed_targets = summary_data.get("failed_targets_count", 0)
    # Targets resolving to an SPK-ID already ingested in the same run (written once, neither a
    # success row nor a failure). Absent in older summaries: 0.
    duplicate_targets = summary_data.get("duplicate_targets_count", 0)
    failure_rate_pct = summary_data.get("failure_rate_pct", 100.0)
    circuit_breaker_passed = summary_data.get("circuit_breaker_passed", False)

    math_expected_pct = round((failed_targets / total_targets * 100.0), 2) if total_targets > 0 else 100.0
    math_valid = abs(failure_rate_pct - math_expected_pct) <= 0.05
    sum_valid = (successful_targets + failed_targets + duplicate_targets) == total_targets

    cb_ok = circuit_breaker_passed and (successful_targets > 0) and (failure_rate_pct < 25.0) and math_valid and sum_valid

    results.append(DQCheckResult(
        check_name="sbdb_circuit_breaker",
        dataset="sbdb",
        status=DQStatus.PASSED if cb_ok else DQStatus.FAILED,
        diagnostic=(
            f"SBDB circuit breaker passed: successful={successful_targets}/{total_targets}, "
            f"failure_rate={failure_rate_pct:.1f}%"
            if cb_ok
            else f"SBDB circuit breaker failed: circuit_breaker_passed={circuit_breaker_passed}, "
                 f"successful={successful_targets}/{total_targets}, failure_rate={failure_rate_pct:.1f}% "
                 f"(threshold < 25.0%), math_valid={math_valid}"
        ),
        metrics={
            "total_targets": total_targets,
            "successful_targets_count": successful_targets,
            "failed_targets_count": failed_targets,
            "duplicate_targets_count": duplicate_targets,
            "failure_rate_pct": failure_rate_pct,
            "circuit_breaker_passed": circuit_breaker_passed,
            "math_valid": math_valid,
        },
    ))

    # Evaluate Parquet suppression vs object lineage
    obj_table_path = os.path.join(base_dir, "fact_sbdb_object_snapshot.parquet")
    tables_exist = {t: os.path.exists(os.path.join(base_dir, t)) for t in SBDB_PARQUET_TABLES}
    any_exist = any(tables_exist.values())

    if not circuit_breaker_passed:
        if any_exist:
            results.append(DQCheckResult(
                check_name="sbdb_ingestion_lineage",
                dataset="sbdb",
                status=DQStatus.FAILED,
                diagnostic="SBDB Parquets exist despite circuit breaker trip (suppression violation)",
            ))
        else:
            results.append(DQCheckResult(
                check_name="sbdb_ingestion_lineage",
                dataset="sbdb",
                status=DQStatus.NOT_APPLICABLE,
                diagnostic="SBDB Parquets suppressed as expected following circuit breaker trip",
            ))
        return results

    # Circuit breaker passed: object table must exist and match successful_targets_count
    if not os.path.exists(obj_table_path):
        results.append(DQCheckResult(
            check_name="sbdb_ingestion_lineage",
            dataset="sbdb",
            status=DQStatus.FAILED,
            diagnostic="fact_sbdb_object_snapshot.parquet missing after successful SBDB extraction",
        ))
        return results

    obj_table, err = _safe_read_parquet(obj_table_path)
    if err:
        results.append(DQCheckResult(
            check_name="sbdb_ingestion_lineage",
            dataset="sbdb",
            status=DQStatus.FAILED,
            diagnostic=f"Failed reading SBDB object Parquet: {err}",
        ))
        return results

    obj_count_ok = (obj_table.num_rows == successful_targets)
    results.append(DQCheckResult(
        check_name="sbdb_ingestion_lineage",
        dataset="sbdb",
        status=DQStatus.PASSED if obj_count_ok else DQStatus.FAILED,
        diagnostic=(
            f"SBDB object lineage verified: {obj_table.num_rows} objects match summary"
            if obj_count_ok
            else f"Object count mismatch: table={obj_table.num_rows}, summary={successful_targets}"
        ),
        metrics={
            "object_rows": obj_table.num_rows,
            "summary_successful_targets": successful_targets,
        },
    ))

    return results


# ---------------------------------------------------------------------------
# Check Suite 2: Pre-Resolution Output Quality & Integrity (check-outputs)
# Ownership: Artifact accounting/co-presence, non-empty files, SBDB table co-presence,
#            SBDB structural grain/integrity, NeoWs approach-window validation,
#            Sentry snapshot-date validation, SBDB snapshot-date validation.
# ---------------------------------------------------------------------------
def check_source_outputs(
    output_dir: str = DEFAULT_OUTPUT_DIR,
    execution_mode: str = EXEC_MODE_CURRENT,
    snapshot_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[DQCheckResult]:
    """Validate pre-resolution output accounting, SBDB structural integrity, and partition dates."""
    results: list[DQCheckResult] = []
    is_backfill = (execution_mode == EXEC_MODE_BACKFILL)
    expected_count = 7 if is_backfill else 8

    # 1. Artifact accounting & co-presence
    required_artifacts = [
        NEOWS_SUMMARY_FILE,
        NEOWS_PARQUET_FILE,
        SBDB_SUMMARY_FILE,
    ] + SBDB_PARQUET_TABLES

    if not is_backfill:
        sentry_file = _find_file(SENTRY_PARQUET_FILES, base_dir=output_dir)
        sentry_resolved = os.path.basename(sentry_file) if sentry_file else "fact_sentry_risk_snapshot.parquet"
        required_artifacts.append(sentry_resolved)

    missing_artifacts = []
    empty_artifacts = []
    found_artifacts = []

    for art in required_artifacts:
        full_path = os.path.join(output_dir, art)
        if not os.path.exists(full_path):
            missing_artifacts.append(art)
        else:
            size = os.path.getsize(full_path)
            if size == 0:
                empty_artifacts.append(art)
            else:
                found_artifacts.append(art)

    sbdb_present = [t for t in SBDB_PARQUET_TABLES if os.path.exists(os.path.join(output_dir, t))]
    orphan_sbdb = 0 < len(sbdb_present) < 4

    total_found = len(found_artifacts)
    accounting_ok = (total_found == expected_count) and (len(missing_artifacts) == 0) and not orphan_sbdb and (len(empty_artifacts) == 0)

    diag_parts = []
    if missing_artifacts:
        diag_parts.append(f"Missing {len(missing_artifacts)} artifacts: {missing_artifacts}")
    if empty_artifacts:
        diag_parts.append(f"Empty (0 byte) artifacts: {empty_artifacts}")
    if orphan_sbdb:
        diag_parts.append(f"Orphan SBDB tables detected: found {len(sbdb_present)}/4 tables ({sbdb_present})")

    results.append(DQCheckResult(
        check_name="pre_resolution_output_accounting",
        dataset="pipeline_outputs",
        status=DQStatus.PASSED if accounting_ok else DQStatus.FAILED,
        diagnostic=(
            f"Pre-resolution output accounting verified: {total_found}/{expected_count} artifacts co-present "
            f"(mode: {execution_mode}, 0 orphan SBDB tables, non-empty)"
            if accounting_ok
            else "; ".join(diag_parts)
        ),
        metrics={
            "execution_mode": execution_mode,
            "expected_artifacts_count": expected_count,
            "found_artifacts_count": total_found,
            "missing_artifacts": missing_artifacts,
            "empty_artifacts": empty_artifacts,
            "orphan_sbdb_tables": orphan_sbdb,
        },
    ))

    # 2. SBDB Structural Grain & Referential Integrity
    if len(sbdb_present) == 4:
        obj_table, _ = _safe_read_parquet(os.path.join(output_dir, "fact_sbdb_object_snapshot.parquet"))
        orbit_table, _ = _safe_read_parquet(os.path.join(output_dir, "fact_sbdb_orbit.parquet"))
        elem_table, _ = _safe_read_parquet(os.path.join(output_dir, "fact_sbdb_orbit_element.parquet"))
        phys_table, _ = _safe_read_parquet(os.path.join(output_dir, "fact_sbdb_physical_parameter.parquet"))

        if obj_table and orbit_table and elem_table and phys_table:
            obj_records = obj_table.to_pylist()
            orbit_records = orbit_table.to_pylist()
            elem_records = elem_table.to_pylist()
            phys_records = phys_table.to_pylist()

            # Grain uniqueness: (snapshot_key, spkid) unique in object and orbit
            obj_keys = [(r.get("snapshot_key"), r.get("spkid")) for r in obj_records]
            obj_grain_ok = len(obj_keys) == len(set(obj_keys))

            orbit_keys = [(r.get("snapshot_key"), r.get("spkid")) for r in orbit_records]
            orbit_grain_ok = len(orbit_keys) == len(set(orbit_keys))

            # Child tables referential integrity: spkids exist in object snapshot
            valid_spkids = set(r.get("spkid") for r in obj_records if r.get("spkid"))
            orbit_spkids = set(r.get("spkid") for r in orbit_records if r.get("spkid"))
            elem_spkids = set(r.get("spkid") for r in elem_records if r.get("spkid"))
            phys_spkids = set(r.get("spkid") for r in phys_records if r.get("spkid"))

            ref_ok = orbit_spkids.issubset(valid_spkids) and elem_spkids.issubset(valid_spkids) and phys_spkids.issubset(valid_spkids)

            struct_ok = obj_grain_ok and orbit_grain_ok and ref_ok
            struct_diag = []
            if not obj_grain_ok:
                struct_diag.append("Object table grain (snapshot_key, spkid) contains duplicate keys")
            if not orbit_grain_ok:
                struct_diag.append("Orbit table grain (snapshot_key, spkid) contains duplicate keys")
            if not ref_ok:
                struct_diag.append("Orphan spkid detected in child tables not present in object snapshot")

            results.append(DQCheckResult(
                check_name="sbdb_structural_integrity",
                dataset="sbdb",
                status=DQStatus.PASSED if struct_ok else DQStatus.FAILED,
                diagnostic=(
                    f"SBDB structural integrity verified ({len(obj_records)} objects, grain unique, ref integrity intact)"
                    if struct_ok
                    else "; ".join(struct_diag)
                ),
                metrics={
                    "grain_unique": obj_grain_ok and orbit_grain_ok,
                    "referential_integrity": ref_ok,
                },
            ))
        else:
            results.append(DQCheckResult(
                check_name="sbdb_structural_integrity",
                dataset="sbdb",
                status=DQStatus.FAILED,
                diagnostic="Failed reading one or more SBDB tables for structural validation",
            ))
    else:
        results.append(DQCheckResult(
            check_name="sbdb_structural_integrity",
            dataset="sbdb",
            status=DQStatus.BLOCKED,
            diagnostic="Blocked due to incomplete SBDB table co-presence",
        ))

    # 3. Source Partition & Approach Window Validation
    partition_ok = True
    part_diag = []

    # A. NeoWs Approach Window: start_date <= closest_approach_date <= end_date
    neows_pq_path = os.path.join(output_dir, NEOWS_PARQUET_FILE)
    neows_sum_path = os.path.join(output_dir, NEOWS_SUMMARY_FILE)
    if os.path.exists(neows_pq_path):
        table, _ = _safe_read_parquet(neows_pq_path)
        sum_data, _ = _safe_load_json(neows_sum_path)
        res_start = start_date or (sum_data.get("start_date") if sum_data else None)
        res_end = end_date or (sum_data.get("end_date") if sum_data else None)

        if table and res_start and res_end and "closest_approach_date" in table.column_names:
            violating_dates = []
            for d in table["closest_approach_date"].to_pylist():
                if not (res_start <= d <= res_end):
                    violating_dates.append(d)
                    if len(violating_dates) >= 5:
                        break
            if violating_dates:
                partition_ok = False
                part_diag.append(f"NeoWs approach dates outside window [{res_start}, {res_end}]: {violating_dates}")

    # B. Sentry Snapshot Date Validation (in CURRENT_PRODUCTION)
    if not is_backfill:
        sentry_file = _find_file(SENTRY_PARQUET_FILES, base_dir=output_dir)
        if sentry_file:
            s_table, _ = _safe_read_parquet(sentry_file)
            if s_table and "snapshot_key" in s_table.column_names:
                s_records = s_table.to_pylist()
                exp_sentry_date = snapshot_date or (s_records[0].get("snapshot_key") if s_records else None)
                if exp_sentry_date:
                    for r in s_records:
                        sk = r.get("snapshot_key")
                        st = r.get("snapshot_time", "")
                        t_date = st[:10] if isinstance(st, str) and len(st) >= 10 else None
                        if sk != exp_sentry_date or (t_date and t_date != exp_sentry_date):
                            partition_ok = False
                            part_diag.append(f"Sentry partition date mismatch with expected {exp_sentry_date}")
                            break

    # C. SBDB Snapshot Date Validation
    sbdb_obj_path = os.path.join(output_dir, "fact_sbdb_object_snapshot.parquet")
    sbdb_sum_path = os.path.join(output_dir, SBDB_SUMMARY_FILE)
    if os.path.exists(sbdb_obj_path):
        o_table, _ = _safe_read_parquet(sbdb_obj_path)
        sbdb_sum, _ = _safe_load_json(sbdb_sum_path)
        exp_sbdb_date = snapshot_date or (sbdb_sum.get("snapshot_key") if sbdb_sum else None)

        if o_table and exp_sbdb_date and "snapshot_key" in o_table.column_names:
            for r in o_table.to_pylist():
                sk = r.get("snapshot_key")
                st = r.get("snapshot_time", "")
                t_date = st[:10] if isinstance(st, str) and len(st) >= 10 else None
                if sk != exp_sbdb_date or (t_date and t_date != exp_sbdb_date):
                    partition_ok = False
                    part_diag.append(f"SBDB snapshot partition date mismatch with expected {exp_sbdb_date}")
                    break

    results.append(DQCheckResult(
        check_name="source_partition_dates",
        dataset="pipeline_outputs",
        status=DQStatus.PASSED if partition_ok else DQStatus.FAILED,
        diagnostic=(
            "Source partition dates and approach windows verified"
            if partition_ok
            else "; ".join(part_diag)
        ),
        metrics={
            "partition_dates_valid": partition_ok,
        },
    ))

    return results


# ---------------------------------------------------------------------------
# Check Suite 3: Post-Resolution Crosswalk Invariants (check-crosswalk)
# Ownership: Bridge & audit integrity only.
# ---------------------------------------------------------------------------
def check_crosswalk(
    bridge_path: str,
    audit_path: str | None = None,
) -> list[DQCheckResult]:
    """Validate crosswalk invariants: 1 primary pivot, single run_id, zero AMBIGUOUS, ref integrity."""
    results: list[DQCheckResult] = []

    if not os.path.exists(bridge_path):
        results.append(DQCheckResult(
            check_name="crosswalk_bridge_invariants",
            dataset="crosswalk",
            status=DQStatus.FAILED,
            diagnostic=f"Crosswalk bridge Parquet missing: {bridge_path}",
        ))
        results.append(DQCheckResult(
            check_name="crosswalk_audit_invariants",
            dataset="crosswalk",
            status=DQStatus.BLOCKED,
            diagnostic="Blocked due to missing crosswalk bridge Parquet",
        ))
        return results

    b_table, err = _safe_read_parquet(bridge_path)
    if err:
        results.append(DQCheckResult(
            check_name="crosswalk_bridge_invariants",
            dataset="crosswalk",
            status=DQStatus.FAILED,
            diagnostic=f"Failed reading crosswalk bridge Parquet: {err}",
        ))
        return results

    b_rows = b_table.num_rows
    if b_rows == 0:
        results.append(DQCheckResult(
            check_name="crosswalk_bridge_invariants",
            dataset="crosswalk",
            status=DQStatus.FAILED,
            diagnostic="Crosswalk bridge table is empty (0 rows)",
        ))
        return results

    b_records = b_table.to_pylist()
    key_field = "asteroid_key" if "asteroid_key" in b_table.column_names else "crosswalk_key"

    # Invariant A: Exactly 1 primary pivot per key
    pivots_per_key = {}
    for r in b_records:
        k = r.get(key_field)
        is_pivot = r.get("is_primary_pivot", False)
        if k not in pivots_per_key:
            pivots_per_key[k] = 0
        if is_pivot:
            pivots_per_key[k] += 1

    bad_pivot_keys = [k for k, count in pivots_per_key.items() if count != 1]
    pivots_ok = (len(bad_pivot_keys) == 0)

    # Invariant B: Identifier uniqueness in bridge (source_system, identifier_name, identifier_value)
    tuple_keys = [(r.get("source_system"), r.get("identifier_name"), r.get("identifier_value")) for r in b_records]
    tuples_unique = (len(tuple_keys) == len(set(tuple_keys)))

    bridge_ok = pivots_ok and tuples_unique
    b_diag = []
    if not pivots_ok:
        b_diag.append(f"{len(bad_pivot_keys)} keys do not have exactly 1 primary pivot (e.g. {bad_pivot_keys[:3]})")
    if not tuples_unique:
        b_diag.append("Duplicate (source_system, identifier_name, identifier_value) tuples in bridge")

    results.append(DQCheckResult(
        check_name="crosswalk_bridge_invariants",
        dataset="crosswalk",
        status=DQStatus.PASSED if bridge_ok else DQStatus.FAILED,
        diagnostic=(
            f"Crosswalk bridge verified ({b_rows} rows across {len(pivots_per_key)} keys, "
            f"1 primary pivot per key, unique source tuples)"
            if bridge_ok
            else "; ".join(b_diag)
        ),
        metrics={
            "bridge_rows": b_rows,
            "distinct_asteroid_keys": len(pivots_per_key),
            "keys_with_invalid_pivots": len(bad_pivot_keys),
            "identifier_tuples_unique": tuples_unique,
        },
    ))

    # Audit table validation (if provided)
    if not audit_path or not os.path.exists(audit_path):
        results.append(DQCheckResult(
            check_name="crosswalk_audit_invariants",
            dataset="crosswalk",
            status=DQStatus.WARNING,
            diagnostic="Resolution audit Parquet not found; audit invariants skipped",
        ))
        return results

    a_table, err = _safe_read_parquet(audit_path)
    if err:
        results.append(DQCheckResult(
            check_name="crosswalk_audit_invariants",
            dataset="crosswalk",
            status=DQStatus.FAILED,
            diagnostic=f"Failed reading resolution audit Parquet: {err}",
        ))
        return results

    a_rows = a_table.num_rows
    a_records = a_table.to_pylist()

    # Invariant C: Exactly 1 single run_id across audit table
    run_id_col = "resolution_run_id" if "resolution_run_id" in a_table.column_names else "run_id"
    audit_run_ids = set(r.get(run_id_col) for r in a_records if r.get(run_id_col))
    single_run_id_ok = (len(audit_run_ids) == 1)

    # Invariant D: Zero AMBIGUOUS resolution states in production crosswalk
    state_col = "match_state" if "match_state" in a_table.column_names else "resolution_status"
    ambiguous_records = [r for r in a_records if str(r.get(state_col, "")).upper() == "AMBIGUOUS"]
    zero_ambiguous_ok = (len(ambiguous_records) == 0)

    # Invariant E: Referential integrity between audit and bridge
    assigned_key_col = "assigned_asteroid_key" if "assigned_asteroid_key" in a_table.column_names else key_field
    all_bridge_keys = set(pivots_per_key.keys())
    orphan_audit_keys = [
        r.get(assigned_key_col)
        for r in a_records
        if r.get(assigned_key_col) and r.get(assigned_key_col) not in all_bridge_keys
    ]
    ref_integrity_ok = (len(orphan_audit_keys) == 0)

    audit_ok = single_run_id_ok and zero_ambiguous_ok and ref_integrity_ok
    a_diag = []
    if not single_run_id_ok:
        a_diag.append(f"Multiple or zero run IDs in audit trail: {audit_run_ids}")
    if not zero_ambiguous_ok:
        a_diag.append(f"{len(ambiguous_records)} AMBIGUOUS records found in production audit trail")
    if not ref_integrity_ok:
        a_diag.append(f"{len(orphan_audit_keys)} assigned keys missing from bridge table")

    results.append(DQCheckResult(
        check_name="crosswalk_audit_invariants",
        dataset="crosswalk",
        status=DQStatus.PASSED if audit_ok else DQStatus.FAILED,
        diagnostic=(
            f"Resolution audit verified ({a_rows} records, run_id={list(audit_run_ids)[0] if audit_run_ids else 'none'}, "
            f"0 AMBIGUOUS records, 100% referential integrity)"
            if audit_ok
            else "; ".join(a_diag)
        ),
        metrics={
            "audit_rows": a_rows,
            "unique_run_ids": list(audit_run_ids),
            "ambiguous_records_count": len(ambiguous_records),
            "orphan_assigned_keys": len(orphan_audit_keys),
        },
    ))

    return results


# ---------------------------------------------------------------------------
# Check Suite 4: S3 Publication Verification (check-s3-publication)
# Ownership: Verification of published S3 objects (offline by default).
# ---------------------------------------------------------------------------
def check_s3_publication(
    keys: list[str],
    bucket_name: str | None = None,
    verify_s3: bool = False,
    s3_client=None,
) -> list[DQCheckResult]:
    """Verify S3 publication. Stays completely offline/AWS-independent unless verify_s3 is True."""
    results: list[DQCheckResult] = []

    if not verify_s3:
        results.append(DQCheckResult(
            check_name="s3_publication_verification",
            dataset="s3",
            status=DQStatus.NOT_APPLICABLE,
            diagnostic="S3 publication verification skipped (offline mode; pass --verify-s3 to enable live validation)",
            metrics={"keys_count": len(keys)},
        ))
        return results

    if not BOTO3_AVAILABLE:
        results.append(DQCheckResult(
            check_name="s3_publication_verification",
            dataset="s3",
            status=DQStatus.FAILED,
            diagnostic="boto3 library is not installed; live S3 verification cannot execute",
        ))
        return results

    bucket = bucket_name or os.getenv("S3_BUCKET_NAME", "nasa-asteroid-intelligence")
    s3 = s3_client or boto3.client("s3")

    missing_keys = []
    empty_keys = []
    verified_keys = []

    for key in keys:
        try:
            head = s3.head_object(Bucket=bucket, Key=key)
            size = head.get("ContentLength", 0)
            if size == 0:
                empty_keys.append(key)
            else:
                verified_keys.append({"key": key, "size": size, "etag": head.get("ETag")})
        except (BotoCoreError, ClientError) as e:
            missing_keys.append(f"{key} ({e})")

    s3_ok = (len(missing_keys) == 0) and (len(empty_keys) == 0) and (len(verified_keys) == len(keys))
    diag = []
    if missing_keys:
        diag.append(f"Missing S3 objects: {missing_keys}")
    if empty_keys:
        diag.append(f"Empty S3 objects: {empty_keys}")

    results.append(DQCheckResult(
        check_name="s3_publication_verification",
        dataset="s3",
        status=DQStatus.PASSED if s3_ok else DQStatus.FAILED,
        diagnostic=(
            f"Verified {len(verified_keys)}/{len(keys)} objects present in s3://{bucket}"
            if s3_ok
            else "; ".join(diag)
        ),
        metrics={
            "bucket": bucket,
            "total_keys": len(keys),
            "verified_count": len(verified_keys),
            "missing_keys": missing_keys,
            "empty_keys": empty_keys,
        },
    ))

    return results


# ---------------------------------------------------------------------------
# Full Suite Orchestration (run-suite)
# Aggregates: check-ingestion, check-outputs, check-crosswalk, check-s3-publication
# ---------------------------------------------------------------------------
def run_full_suite(
    output_dir: str = DEFAULT_OUTPUT_DIR,
    execution_mode: str = EXEC_MODE_CURRENT,
    snapshot_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    verify_s3: bool = False,
    s3_bucket: str | None = None,
    s3_keys: list[str] | None = None,
    dq_run_id: str | None = None,
) -> DQReport:
    """Run all quality gates end-to-end and aggregate results."""
    report = DQReport(stage="full_suite", execution_mode=execution_mode, dq_run_id=dq_run_id)

    # 1. Ingestion Gate: NeoWs
    neows_sum_path = os.path.join(output_dir, NEOWS_SUMMARY_FILE)
    neows_pq_path = os.path.join(output_dir, NEOWS_PARQUET_FILE)
    for c in check_neows_ingestion(neows_sum_path, neows_pq_path):
        report.add_check(c)

    # 2. Ingestion Gate: Sentry
    sentry_pq = _find_file(SENTRY_PARQUET_FILES, base_dir=output_dir)
    is_backfill = (execution_mode == EXEC_MODE_BACKFILL)
    for c in check_sentry_ingestion(sentry_pq, is_backfill=is_backfill):
        report.add_check(c)

    # 3. Ingestion Gate: SBDB
    sbdb_sum_path = os.path.join(output_dir, SBDB_SUMMARY_FILE)
    for c in check_sbdb_ingestion(sbdb_sum_path, base_dir=output_dir):
        report.add_check(c)

    # 4. Pre-Resolution Outputs, Structural Integrity & Partition Dates
    for c in check_source_outputs(
        output_dir=output_dir,
        execution_mode=execution_mode,
        snapshot_date=snapshot_date,
        start_date=start_date,
        end_date=end_date,
    ):
        report.add_check(c)

    # 5. Crosswalk Gate (if crosswalk files are present or discoverable)
    bridge_file = _find_file(CROSSWALK_BRIDGE_FILES, base_dir=output_dir)
    audit_file = _find_file(CROSSWALK_AUDIT_FILES, base_dir=output_dir)
    if bridge_file:
        for c in check_crosswalk(bridge_file, audit_file):
            report.add_check(c)
    else:
        report.add_check(DQCheckResult(
            check_name="crosswalk_bridge_invariants",
            dataset="crosswalk",
            status=DQStatus.NOT_APPLICABLE,
            diagnostic="Crosswalk artifacts not present in output directory (pre-resolution stage)",
        ))

    # 6. S3 Publication
    keys_to_check = s3_keys or []
    for c in check_s3_publication(keys_to_check, bucket_name=s3_bucket, verify_s3=verify_s3):
        report.add_check(c)

    return report


# ---------------------------------------------------------------------------
# CLI Argument Parsing & Command Dispatch
# ---------------------------------------------------------------------------
def parse_args(args=None):
    parser = argparse.ArgumentParser(
        description="NASA Planetary Defense Risk Intelligence Platform — Operational Data Quality Engine"
    )
    subparsers = parser.add_subparsers(dest="command", help="DQ validation command to execute")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="Directory containing pipeline artifacts")
    common.add_argument("--output-file", default=DEFAULT_DQ_RESULT_FILE, help="Path to write structured DQ JSON results")
    common.add_argument(
        "--execution-mode",
        choices=[EXEC_MODE_CURRENT, EXEC_MODE_BACKFILL],
        default=EXEC_MODE_CURRENT,
        help="Pipeline execution mode (CURRENT_PRODUCTION=8 artifacts, HISTORICAL_BACKFILL=7 artifacts)",
    )

    # 1. check-ingestion
    sub_ingest = subparsers.add_parser("check-ingestion", parents=[common], help="Validate ingestion lineage and gate thresholds")
    sub_ingest.add_argument("--neows-summary", default=None, help="Explicit path to neows_summary.json")
    sub_ingest.add_argument("--neows-parquet", default=None, help="Explicit path to asteroids.parquet")
    sub_ingest.add_argument("--sentry-parquet", default=None, help="Explicit path to sentry Parquet")
    sub_ingest.add_argument("--sbdb-summary", default=None, help="Explicit path to sbdb_batch_summary.json")

    # 2. check-outputs
    sub_out = subparsers.add_parser("check-outputs", parents=[common], help="Validate pre-resolution output accounting, structure, and dates")
    sub_out.add_argument("--snapshot-date", default=None, help="Expected snapshot date (YYYY-MM-DD)")
    sub_out.add_argument("--start-date", default=None, help="Expected NeoWs approach window start (YYYY-MM-DD)")
    sub_out.add_argument("--end-date", default=None, help="Expected NeoWs approach window end (YYYY-MM-DD)")

    # 3. check-crosswalk
    sub_cross = subparsers.add_parser("check-crosswalk", parents=[common], help="Validate crosswalk invariants")
    sub_cross.add_argument("--bridge-file", default=None, help="Path to bridge_asteroid_identifier.parquet")
    sub_cross.add_argument("--audit-file", default=None, help="Path to fact_entity_resolution.parquet")

    # 4. check-s3-publication
    sub_s3 = subparsers.add_parser("check-s3-publication", parents=[common], help="Verify S3 object publication")
    sub_s3.add_argument("--keys", nargs="*", default=[], help="List of S3 object keys to verify")
    sub_s3.add_argument("--keys-file", default=None, help="Path to JSON file containing list of keys")
    sub_s3.add_argument("--bucket", default=None, help="S3 bucket name")
    sub_s3.add_argument("--verify-s3", action="store_true", help="Enable live AWS S3 verification (disabled by default)")

    # 5. run-suite
    sub_suite = subparsers.add_parser("run-suite", parents=[common], help="Run all quality gates end-to-end")
    sub_suite.add_argument("--snapshot-date", default=None, help="Expected snapshot date (YYYY-MM-DD)")
    sub_suite.add_argument("--start-date", default=None, help="Expected NeoWs approach window start (YYYY-MM-DD)")
    sub_suite.add_argument("--end-date", default=None, help="Expected NeoWs approach window end (YYYY-MM-DD)")
    sub_suite.add_argument("--verify-s3", action="store_true", help="Enable live AWS S3 verification")
    sub_suite.add_argument("--bucket", default=None, help="S3 bucket name")
    sub_suite.add_argument("--keys", nargs="*", default=[], help="List of S3 keys to verify")

    return parser.parse_args(args)


def main(args=None) -> int:
    """Execute Data Quality Engine CLI."""
    parsed_args = parse_args(args)

    if not parsed_args.command:
        parsed_args.command = "run-suite"

    output_dir = getattr(parsed_args, "output_dir", DEFAULT_OUTPUT_DIR)
    output_file = getattr(parsed_args, "output_file", DEFAULT_DQ_RESULT_FILE)
    execution_mode = getattr(parsed_args, "execution_mode", EXEC_MODE_CURRENT)

    report = DQReport(stage=parsed_args.command, execution_mode=execution_mode)

    try:
        if parsed_args.command == "check-ingestion":
            neows_sum = parsed_args.neows_summary or os.path.join(output_dir, NEOWS_SUMMARY_FILE)
            neows_pq = parsed_args.neows_parquet or os.path.join(output_dir, NEOWS_PARQUET_FILE)
            for c in check_neows_ingestion(neows_sum, neows_pq):
                report.add_check(c)

            sentry_pq = parsed_args.sentry_parquet or _find_file(SENTRY_PARQUET_FILES, base_dir=output_dir)
            is_backfill = (execution_mode == EXEC_MODE_BACKFILL)
            for c in check_sentry_ingestion(sentry_pq, is_backfill=is_backfill):
                report.add_check(c)

            sbdb_sum = parsed_args.sbdb_summary or os.path.join(output_dir, SBDB_SUMMARY_FILE)
            for c in check_sbdb_ingestion(sbdb_sum, base_dir=output_dir):
                report.add_check(c)

        elif parsed_args.command == "check-outputs":
            for c in check_source_outputs(
                output_dir=output_dir,
                execution_mode=execution_mode,
                snapshot_date=getattr(parsed_args, "snapshot_date", None),
                start_date=getattr(parsed_args, "start_date", None),
                end_date=getattr(parsed_args, "end_date", None),
            ):
                report.add_check(c)

        elif parsed_args.command == "check-crosswalk":
            bridge_path = parsed_args.bridge_file or _find_file(CROSSWALK_BRIDGE_FILES, base_dir=output_dir)
            audit_path = parsed_args.audit_file or _find_file(CROSSWALK_AUDIT_FILES, base_dir=output_dir)
            if not bridge_path:
                report.add_check(DQCheckResult(
                    check_name="crosswalk_bridge_invariants",
                    dataset="crosswalk",
                    status=DQStatus.FAILED,
                    diagnostic=f"No crosswalk bridge file found in {output_dir}",
                ))
            else:
                for c in check_crosswalk(bridge_path, audit_path):
                    report.add_check(c)

        elif parsed_args.command == "check-s3-publication":
            keys = list(parsed_args.keys)
            if parsed_args.keys_file and os.path.exists(parsed_args.keys_file):
                with open(parsed_args.keys_file, "r", encoding="utf-8") as f:
                    file_keys = json.load(f)
                    if isinstance(file_keys, list):
                        keys.extend(file_keys)

            for c in check_s3_publication(
                keys=keys,
                bucket_name=parsed_args.bucket,
                verify_s3=parsed_args.verify_s3
            ):
                report.add_check(c)

        elif parsed_args.command == "run-suite":
            report = run_full_suite(
                output_dir=output_dir,
                execution_mode=execution_mode,
                snapshot_date=getattr(parsed_args, "snapshot_date", None),
                start_date=getattr(parsed_args, "start_date", None),
                end_date=getattr(parsed_args, "end_date", None),
                verify_s3=getattr(parsed_args, "verify_s3", False),
                s3_bucket=getattr(parsed_args, "bucket", None),
                s3_keys=getattr(parsed_args, "keys", []),
            )

        report.save_json(output_file)

        summary = report.get_summary()
        logger.info(
            "DQ Suite Summary: status=%s, total=%d, passed=%d, failed=%d, warnings=%d, blocked=%d, n/a=%d",
            report.overall_status.value,
            summary["total_checks"],
            summary["passed"],
            summary["failed"],
            summary["warnings"],
            summary["blocked"],
            summary["not_applicable"],
        )

        for check in report.checks:
            lvl = logging.ERROR if check.status == DQStatus.FAILED else logging.INFO
            logger.log(lvl, "  [%s] %s (%s): %s", check.status.value, check.check_name, check.dataset, check.diagnostic)

        return report.exit_code

    except Exception as e:
        logger.error("DQ engine runtime exception: %s", e, exc_info=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
