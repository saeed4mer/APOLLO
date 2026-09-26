"""Offline tests for Phase 7 Historical Sentry Risk Analytics.

Validates the AWS Athena / Trino SQL views (v_sentry_snapshot_coverage,
v_sentry_risk_metric_history) using an in-memory SQL execution engine (DuckDB)
to verify mathematical logic, windowing, delta calculations, coverage-gap
detection, and NULL semantics with zero live cloud I/O.
"""
from pathlib import Path
import re

import duckdb
import pytest

SQL_FILE_PATH = Path(__file__).parent / "athena_historical_risk.sql"


def build_db_connection(frozen_now: str = "2026-09-27T00:00:00Z"):
    """Create an in-memory DuckDB connection with Athena DDL and deterministic clock."""
    conn = duckdb.connect(":memory:")

    with open(SQL_FILE_PATH, encoding="utf-8") as f:
        sql_content = f.read()

    conn.execute("CREATE SCHEMA IF NOT EXISTS nasa_asteroids;")
    # DuckDB compatibility shim for Athena/Trino from_iso8601_timestamp function
    conn.execute(
        "CREATE OR REPLACE MACRO from_iso8601_timestamp(x) AS "
        "(CAST(x AS TIMESTAMPTZ) AT TIME ZONE 'UTC');"
    )

    clean_lines = [re.sub(r"--.*$", "", line) for line in sql_content.splitlines()]
    clean_sql = "\n".join(clean_lines)

    statements = [s.strip() for s in clean_sql.split(";") if s.strip()]
    for stmt in statements:
        clean_stmt = re.sub(r"CREATE\s+DATABASE", "CREATE SCHEMA", stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"TBLPROPERTIES\s*\([^)]*\)", "", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"LOCATION\s*'[^']*'", "", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"STORED\s+AS\s+PARQUET", "", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(
            r"PARTITIONED\s+BY\s*\([^)]*\)", "", clean_stmt, flags=re.IGNORECASE
        )
        clean_stmt = re.sub(r"\bEXTERNAL\b", "", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"\bNOW\(\)", f"CAST('{frozen_now}' AS TIMESTAMP)", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = clean_stmt.strip()

        if clean_stmt:
            conn.execute(clean_stmt)

    return conn


@pytest.fixture
def db_conn():
    """Create a fresh in-memory DuckDB connection with schema and test views."""
    return build_db_connection()


def test_athena_sql_syntax_and_schema_ddl(db_conn):
    """Verify that athena_historical_risk.sql parses and registers tables and views cleanly."""
    tables = [
        row[0]
        for row in db_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'nasa_asteroids'"
        ).fetchall()
    ]
    assert "fact_sentry_risk_snapshot" in tables
    assert "bridge_asteroid_identifier" in tables
    assert "v_sentry_snapshot_coverage" in tables
    assert "v_sentry_risk_metric_history" in tables
    assert "v_sentry_presence_history" in tables
    assert "v_sentry_object_lifecycle" in tables


def test_snapshot_coverage_aggregation_and_sequence(db_conn):
    """Verify that v_sentry_snapshot_coverage aggregates by snapshot_key and computes snapshot_seq."""
    # Insert mock snapshots: 2 records on 2026-09-01, 1 record on 2026-09-03
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'run_01', '2026-09-01T00:00:00Z', 'id_1', '1979 XB', '(1979 XB)', 1e-6, 5, -2.5, -2.5, 0, 23.7, '1979-12-15'),
        ('2026-09-01', 'run_01', '2026-09-01T00:00:10Z', 'id_2', '2000 SB45', '(2000 SB45)', 2e-6, 2, -3.0, -3.0, 0, 15.0, '2000-09-29'),
        ('2026-09-03', 'run_03', '2026-09-03T00:00:00Z', 'id_1', '1979 XB', '(1979 XB)', 1e-6, 5, -2.5, -2.5, 0, 23.7, '1979-12-15');
    """)

    res = db_conn.execute("""
        SELECT snapshot_key, is_captured, row_count, first_snapshot_time, last_snapshot_time, snapshot_seq
        FROM nasa_asteroids.v_sentry_snapshot_coverage
        ORDER BY snapshot_key ASC
    """).fetchall()

    assert len(res) == 2

    # Snapshot 2026-09-01
    assert res[0][0] == "2026-09-01"
    assert res[0][1] is True  # is_captured
    assert res[0][2] == 2      # row_count
    assert res[0][3] == "2026-09-01T00:00:00Z"  # min time
    assert res[0][4] == "2026-09-01T00:00:10Z"  # max time
    assert res[0][5] == 1      # snapshot_seq

    # Snapshot 2026-09-03
    assert res[1][0] == "2026-09-03"
    assert res[1][1] is True
    assert res[1][2] == 1
    assert res[1][5] == 2      # snapshot_seq (dense rank)


def test_first_snapshot_row_behavior(db_conn):
    """Verify first snapshot row: prev_* values are NULL, is_first_snapshot is TRUE, gap is FALSE."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date, estimated_diameter_km, absolute_magnitude
        ) VALUES
        ('2026-09-01', 'run_01', '2026-09-01T00:00:00Z', 'id_1', '1979 XB', '(1979 XB)', 1.2e-6, 3, -2.8, -3.1, 0, 23.5, '1979-12-15', 0.66, 18.5);
    """)

    row = db_conn.execute("""
        SELECT
            prev_snapshot_key,
            prev_impact_probability,
            prev_palermo_scale_max,
            delta_impact_probability,
            delta_palermo_scale_max,
            is_first_snapshot,
            is_metric_changed,
            has_new_observations,
            coverage_gap_flag
        FROM nasa_asteroids.v_sentry_risk_metric_history
        WHERE sentry_id = 'id_1'
    """).fetchone()

    assert row[0] is None      # prev_snapshot_key
    assert row[1] is None      # prev_impact_probability
    assert row[2] is None      # prev_palermo_scale_max
    assert row[3] is None      # delta_impact_probability
    assert row[4] is None      # delta_palermo_scale_max
    assert row[5] is True      # is_first_snapshot
    assert row[6] is False     # is_metric_changed
    assert row[7] is False     # has_new_observations
    assert row[8] is False     # coverage_gap_flag


def test_contiguous_snapshots_unchanged_metrics(db_conn):
    """Verify contiguous daily snapshots with unchanged metrics yield zero deltas and gap=FALSE."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'ast_1', '1979 XB', '(1979 XB)', 1.0e-6, 4, -2.5, -2.5, 0, 20.0, '1979-12-15'),
        ('2026-09-02', 'r2', '2026-09-02T00:00:00Z', 'ast_1', '1979 XB', '(1979 XB)', 1.0e-6, 4, -2.5, -2.5, 0, 20.0, '1979-12-15');
    """)

    rows = db_conn.execute("""
        SELECT
            snapshot_key,
            prev_snapshot_key,
            delta_impact_probability,
            delta_palermo_scale_max,
            delta_torino_scale_max,
            delta_potential_impacts_count,
            delta_last_obs_days,
            is_metric_changed,
            has_new_observations,
            coverage_gap_flag
        FROM nasa_asteroids.v_sentry_risk_metric_history
        WHERE sentry_id = 'ast_1'
        ORDER BY snapshot_key ASC
    """).fetchall()

    assert len(rows) == 2

    # Snapshot 2026-09-02
    curr = rows[1]
    assert curr[0] == "2026-09-02"
    assert curr[1] == "2026-09-01"
    assert curr[2] == 0.0      # delta_impact_probability
    assert curr[3] == 0.0      # delta_palermo_scale_max
    assert curr[4] == 0        # delta_torino_scale_max
    assert curr[5] == 0        # delta_potential_impacts_count
    assert curr[6] == 0        # delta_last_obs_days
    assert curr[7] is False    # is_metric_changed
    assert curr[8] is False    # has_new_observations
    assert curr[9] is False    # coverage_gap_flag (calendar diff = 1 day)


def test_metric_deltas_linear_and_observation_expansion(db_conn):
    """Verify linear metric deltas and observation arc expansion across snapshots."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_A', '2025 HX', '(2025 HX)', 1.0e-5, 2, -2.0, -2.5, 0, 10.0, '2025-05-01'),
        ('2026-09-02', 'r2', '2026-09-02T00:00:00Z', 'obj_A', '2025 HX', '(2025 HX)', 3.5e-5, 5, -1.2, -1.8, 1, 10.5, '2025-05-15');
    """)

    row = db_conn.execute("""
        SELECT
            prev_snapshot_key,
            delta_impact_probability,
            delta_palermo_scale_max,
            delta_palermo_scale_cum,
            delta_torino_scale_max,
            delta_potential_impacts_count,
            delta_v_infinity_km_s,
            delta_last_obs_days,
            is_metric_changed,
            has_new_observations,
            coverage_gap_flag
        FROM nasa_asteroids.v_sentry_risk_metric_history
        WHERE sentry_id = 'obj_A' AND snapshot_key = '2026-09-02'
    """).fetchone()

    assert row[0] == "2026-09-01"
    assert pytest.approx(row[1], rel=1e-6) == 2.5e-5   # 3.5e-5 - 1.0e-5
    assert pytest.approx(row[2], rel=1e-6) == 0.7      # -1.8 - (-2.5) = +0.7
    assert pytest.approx(row[3], rel=1e-6) == 0.8      # -1.2 - (-2.0) = +0.8
    assert row[4] == 1                                 # 1 - 0 = +1
    assert row[5] == 3                                 # 5 - 2 = +3
    assert pytest.approx(row[6], rel=1e-6) == 0.5      # 10.5 - 10.0
    assert row[7] == 14                                # 2025-05-15 minus 2025-05-01 = 14 days
    assert row[8] is True                              # is_metric_changed
    assert row[9] is True                              # has_new_observations
    assert row[10] is False                            # coverage_gap_flag (contiguous)


def test_uncaptured_date_coverage_gap(db_conn):
    """Verify that a missing uncaptured snapshot date sets coverage_gap_flag = TRUE."""
    # Only 2026-09-01 and 2026-09-03 are captured; 2026-09-02 was uncaptured.
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_gap', 'Gap Asteroid', 'Gap', 1e-6, 1, -3.0, -3.0, 0, 12.0, '2026-01-01'),
        ('2026-09-03', 'r3', '2026-09-03T00:00:00Z', 'obj_gap', 'Gap Asteroid', 'Gap', 1e-6, 1, -3.0, -3.0, 0, 12.0, '2026-01-01');
    """)

    row = db_conn.execute("""
        SELECT
            prev_snapshot_key,
            coverage_gap_flag
        FROM nasa_asteroids.v_sentry_risk_metric_history
        WHERE sentry_id = 'obj_gap' AND snapshot_key = '2026-09-03'
    """).fetchone()

    assert row[0] == "2026-09-01"
    # calendar_days_diff = 2 (Sep 1 to Sep 3)
    # captured_snapshot_seq_diff = 1 (seq 1 to seq 2)
    # 1 < 2 => coverage_gap_flag is TRUE!
    assert row[1] is True


def test_captured_intervening_absence(db_conn):
    """Verify that when an intervening snapshot WAS captured (object absent), coverage_gap_flag = FALSE."""
    # 2026-09-01, 2026-09-02, and 2026-09-03 all captured globally.
    # obj_reentry is present in 2026-09-01 and 2026-09-03, but absent in 2026-09-02.
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_reentry', 'Reentry Ast', 'Reentry', 1e-6, 1, -3.0, -3.0, 0, 12.0, '2026-01-01'),
        ('2026-09-02', 'r2', '2026-09-02T00:00:00Z', 'other_obj', 'Other Ast', 'Other', 2e-6, 1, -4.0, -4.0, 0, 15.0, '2026-01-01'),
        ('2026-09-03', 'r3', '2026-09-03T00:00:00Z', 'obj_reentry', 'Reentry Ast', 'Reentry', 1e-6, 1, -3.0, -3.0, 0, 12.0, '2026-01-01');
    """)

    row = db_conn.execute("""
        SELECT
            prev_snapshot_key,
            coverage_gap_flag
        FROM nasa_asteroids.v_sentry_risk_metric_history
        WHERE sentry_id = 'obj_reentry' AND snapshot_key = '2026-09-03'
    """).fetchone()

    assert row[0] == "2026-09-01"
    # calendar_days_diff = 2 (Sep 1 to Sep 3)
    # captured_snapshot_seq_diff = 2 (seq 1 to seq 3, because Sep 2 was captured!)
    # 2 == 2 => coverage_gap_flag is FALSE! (True source absence, NOT a missing coverage gap!)
    assert row[1] is False


def test_optional_nullable_fields_handling(db_conn):
    """Verify that current and previous optional physical fields (diameter, magnitude) are tracked."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date, estimated_diameter_km, absolute_magnitude
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'opt_obj', 'Opt Ast', 'Opt', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01', 0.55, 19.2),
        ('2026-09-02', 'r2', '2026-09-02T00:00:00Z', 'opt_obj', 'Opt Ast', 'Opt', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01', NULL, 19.5);
    """)

    row = db_conn.execute("""
        SELECT
            estimated_diameter_km,
            absolute_magnitude,
            prev_estimated_diameter_km,
            prev_absolute_magnitude
        FROM nasa_asteroids.v_sentry_risk_metric_history
        WHERE sentry_id = 'opt_obj' AND snapshot_key = '2026-09-02'
    """).fetchone()

    assert row[0] is None      # current estimated_diameter_km is NULL
    assert row[1] == 19.5      # current absolute_magnitude
    assert row[2] == 0.55      # prev_estimated_diameter_km
    assert row[3] == 19.2      # prev_absolute_magnitude


def test_unresolved_sentry_objects_remaining_in_history(db_conn):
    """Verify that unresolved Sentry objects retain full history and asteroid_key is NULL."""
    # Insert bridge entry for resolved_obj only
    db_conn.execute("""
        INSERT INTO nasa_asteroids.bridge_asteroid_identifier (
            asteroid_key, source_system, identifier_name, identifier_value, is_primary_pivot, created_at, updated_at
        ) VALUES
        ('ast_e7b8c9d0-1234-5678-9abc-def012345678', 'sentry', 'sentry_id', 'resolved_id', FALSE, '2026-09-25T00:00:00Z', '2026-09-25T00:00:00Z');
    """)

    # Insert snapshot records for both resolved and unresolved objects
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'resolved_id', 'Resolved Ast', 'Res', 1e-6, 1, -2.5, -2.5, 0, 11.0, '2026-01-01'),
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'unresolved_id', 'Unresolved Ast', 'Unres', 5e-7, 2, -3.5, -3.5, 0, 14.0, '2026-01-01');
    """)

    rows = db_conn.execute("""
        SELECT sentry_id, asteroid_key, impact_probability, is_first_snapshot
        FROM nasa_asteroids.v_sentry_risk_metric_history
        ORDER BY sentry_id ASC
    """).fetchall()

    assert len(rows) == 2

    # Resolved object
    assert rows[0][0] == "resolved_id"
    assert rows[0][1] == "ast_e7b8c9d0-1234-5678-9abc-def012345678"

    # Unresolved object: fully preserved with NULL asteroid_key
    assert rows[1][0] == "unresolved_id"
    assert rows[1][1] is None
    assert rows[1][2] == 5e-7
    assert rows[1][3] is True


def test_scientific_safety_no_palermo_percentage_or_composite_danger_scores():
    """Verify that view SQL does not contain percentage changes on Palermo or composite danger scores."""
    with open(SQL_FILE_PATH, encoding="utf-8") as f:
        sql_text = f.read().lower()

    # Disallow percentage changes on Palermo scale
    assert "palermo_scale_percent" not in sql_text
    assert "delta_palermo" in sql_text  # linear delta is allowed

    # Disallow composite danger/risk score
    assert "danger_score" not in sql_text
    assert "threat_score" not in sql_text
    assert "composite_score" not in sql_text


def test_presence_new_entry(db_conn):
    """Scenario 1: First appearance of an object yields presence_state = 'NEW_ENTRY'."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES (
            '2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_entry', '1979 XB', '(1979 XB)',
            1e-6, 5, -2.5, -2.5, 0, 23.7, '1979-12-15'
        );
    """)

    row = db_conn.execute("""
        SELECT presence_state, is_exit_after_this_snapshot, coverage_gap_flag, seq_prev, prev_snapshot_key
        FROM nasa_asteroids.v_sentry_presence_history
        WHERE sentry_id = 'obj_entry'
    """).fetchone()

    assert row[0] == "NEW_ENTRY"
    assert row[1] is False
    assert row[2] is False
    assert row[3] is None
    assert row[4] is None


def test_presence_consecutive_persistent(db_conn):
    """Scenario 2: Object captured on consecutive calendar days yields presence_state = 'PERSISTENT'."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_pers', 'Ast Pers', '(Ast Pers)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-02', 'r2', '2026-09-02T00:00:00Z', 'obj_pers', 'Ast Pers', '(Ast Pers)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01');
    """)

    row = db_conn.execute("""
        SELECT presence_state, is_exit_after_this_snapshot, coverage_gap_flag, days_since_prev
        FROM nasa_asteroids.v_sentry_presence_history
        WHERE sentry_id = 'obj_pers' AND snapshot_key = '2026-09-02'
    """).fetchone()

    assert row[0] == "PERSISTENT"
    assert row[1] is False
    assert row[2] is False
    assert row[3] == 1


def test_presence_valid_re_entry(db_conn):
    """Scenario 3: Object reappearing after fully captured intervening absence yields 'RE_ENTRY'."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_re', 'Ast Re', '(Ast Re)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_filler', 'Filler', '(Filler)', 2e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-02', 'r2', '2026-09-02T00:00:00Z', 'obj_filler', 'Filler', '(Filler)', 2e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-03', 'r3', '2026-09-03T00:00:00Z', 'obj_re', 'Ast Re', '(Ast Re)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01');
    """)

    row = db_conn.execute("""
        SELECT presence_state, is_exit_after_this_snapshot, coverage_gap_flag, days_since_prev, seq_curr - seq_prev
        FROM nasa_asteroids.v_sentry_presence_history
        WHERE sentry_id = 'obj_re' AND snapshot_key = '2026-09-03'
    """).fetchone()

    assert row[0] == "RE_ENTRY"
    assert row[1] is False
    assert row[2] is False
    assert row[3] == 2
    assert row[4] == 2


def test_presence_validated_exit_after_this_snapshot(db_conn):
    """Scenario 4: Object absent in next contiguous captured day yields 'EXIT_AFTER_THIS_SNAPSHOT'."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_exit', 'Exit Ast', '(Exit Ast)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_f', 'Filler', '(Filler)', 2e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-02', 'r2', '2026-09-02T00:00:00Z', 'obj_f', 'Filler', '(Filler)', 2e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01');
    """)

    row = db_conn.execute("""
        SELECT presence_state, is_exit_after_this_snapshot, global_next_days_diff
        FROM nasa_asteroids.v_sentry_presence_history
        WHERE sentry_id = 'obj_exit' AND snapshot_key = '2026-09-01'
    """).fetchone()

    assert row[0] == "EXIT_AFTER_THIS_SNAPSHOT"
    assert row[1] is True
    assert row[2] == 1


def test_presence_uncaptured_date_no_exit_inferred(db_conn):
    """Scenario 5: Object absence across uncaptured date does NOT infer EXIT_AFTER_THIS_SNAPSHOT."""
    # Snapshot on Sep 1 and Sep 3. Sep 2 was UNCAPTURED (global_next_days_diff = 2)
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_gap_no_exit', 'Gap Ast', '(Gap Ast)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-03', 'r3', '2026-09-03T00:00:00Z', 'obj_f', 'Filler', '(Filler)', 2e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01');
    """)

    row = db_conn.execute("""
        SELECT presence_state, is_exit_after_this_snapshot, global_next_days_diff
        FROM nasa_asteroids.v_sentry_presence_history
        WHERE sentry_id = 'obj_gap_no_exit' AND snapshot_key = '2026-09-01'
    """).fetchone()

    assert row[0] == "NEW_ENTRY"
    assert row[1] is False
    assert row[2] == 2


def test_presence_resumed_after_gap_null_state(db_conn):
    """Scenario 6: Resumed observation after uncaptured date yields presence_state = NULL and coverage_gap_flag = TRUE."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_resumed', 'Resumed', '(Resumed)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-03', 'r3', '2026-09-03T00:00:00Z', 'obj_resumed', 'Resumed', '(Resumed)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01');
    """)

    row = db_conn.execute("""
        SELECT presence_state, is_exit_after_this_snapshot, coverage_gap_flag
        FROM nasa_asteroids.v_sentry_presence_history
        WHERE sentry_id = 'obj_resumed' AND snapshot_key = '2026-09-03'
    """).fetchone()

    assert row[0] is None
    assert row[1] is False
    assert row[2] is True


def test_lifecycle_currently_active_deterministic_clock():
    """Scenario 7: Proves tri-state active logic against frozen evaluation timestamp."""
    frozen_now = "2026-09-27T00:00:00Z"

    # Fresh catalog: latest snapshot time is 2026-09-26T23:00:00Z (1h diff <= 48h)
    fresh_conn = build_db_connection(frozen_now=frozen_now)
    fresh_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-25', 'r1', '2026-09-25T23:00:00Z', 'obj_absent', 'Absent', '(Absent)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-26', 'r2', '2026-09-26T23:00:00Z', 'obj_present', 'Present', '(Present)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01');
    """)

    row_present = fresh_conn.execute("""
        SELECT is_currently_active FROM nasa_asteroids.v_sentry_object_lifecycle WHERE sentry_id = 'obj_present'
    """).fetchone()
    row_absent = fresh_conn.execute("""
        SELECT is_currently_active FROM nasa_asteroids.v_sentry_object_lifecycle WHERE sentry_id = 'obj_absent'
    """).fetchone()

    assert row_present[0] is True
    assert row_absent[0] is False

    # Stale catalog: latest snapshot time is 2026-09-24T23:00:00Z (49h diff > 48h)
    stale_conn = build_db_connection(frozen_now=frozen_now)
    stale_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-24', 'r1', '2026-09-24T23:00:00Z', 'obj_stale', 'Stale', '(Stale)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01');
    """)

    row_stale = stale_conn.execute("""
        SELECT is_currently_active FROM nasa_asteroids.v_sentry_object_lifecycle WHERE sentry_id = 'obj_stale'
    """).fetchone()

    assert row_stale[0] is None


def test_lifecycle_optional_first_last_non_null_physical_metrics(db_conn):
    """Scenario 8: Preserves first and last non-null physical parameters across sparse observations."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date, estimated_diameter_km, absolute_magnitude
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_opt', 'Opt', '(Opt)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01', NULL, 20.1),
        ('2026-09-02', 'r2', '2026-09-02T00:00:00Z', 'obj_opt', 'Opt', '(Opt)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01', 0.45, NULL),
        ('2026-09-03', 'r3', '2026-09-03T00:00:00Z', 'obj_opt', 'Opt', '(Opt)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01', NULL, 20.5),
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'obj_all_null', 'Null', '(Null)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01', NULL, NULL);
    """)

    row_opt = db_conn.execute("""
        SELECT initial_estimated_diameter_km, latest_estimated_diameter_km, initial_absolute_magnitude, latest_absolute_magnitude
        FROM nasa_asteroids.v_sentry_object_lifecycle
        WHERE sentry_id = 'obj_opt'
    """).fetchone()

    assert row_opt[0] == 0.45
    assert row_opt[1] == 0.45
    assert row_opt[2] == 20.1
    assert row_opt[3] == 20.5

    row_null = db_conn.execute("""
        SELECT initial_estimated_diameter_km, latest_estimated_diameter_km, initial_absolute_magnitude, latest_absolute_magnitude
        FROM nasa_asteroids.v_sentry_object_lifecycle
        WHERE sentry_id = 'obj_all_null'
    """).fetchone()

    assert row_null[0] is None
    assert row_null[1] is None
    assert row_null[2] is None
    assert row_null[3] is None


def test_lifecycle_crosswalk_ambiguity_enforcement(db_conn):
    """Scenario 9: Enforces canonical key uniqueness and surfaces ambiguity without silent selection."""
    # Seed bridge table
    db_conn.execute("""
        INSERT INTO nasa_asteroids.bridge_asteroid_identifier VALUES
        ('ast_canonical_single', 'sentry', 'sentry_id', 'single_key_obj', FALSE, '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z'),
        ('ast_competing_1', 'sentry', 'sentry_id', 'multi_key_obj', FALSE, '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z'),
        ('ast_competing_2', 'sentry', 'sentry_id', 'multi_key_obj', FALSE, '2026-09-01T00:00:00Z', '2026-09-01T00:00:00Z');
    """)

    # Seed fact snapshots
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'single_key_obj', 'Single', '(Single)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'multi_key_obj', 'Multi', '(Multi)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'unres_obj', 'Unres', '(Unres)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01');
    """)

    # 1. Single valid canonical key
    row_single = db_conn.execute("""
        SELECT asteroid_key, is_crosswalk_ambiguous FROM nasa_asteroids.v_sentry_object_lifecycle WHERE sentry_id = 'single_key_obj'
    """).fetchone()
    assert row_single[0] == "ast_canonical_single"
    assert row_single[1] is False

    # 2. Competing canonical keys -> NULL + is_crosswalk_ambiguous = TRUE
    row_multi = db_conn.execute("""
        SELECT asteroid_key, is_crosswalk_ambiguous FROM nasa_asteroids.v_sentry_object_lifecycle WHERE sentry_id = 'multi_key_obj'
    """).fetchone()
    assert row_multi[0] is None
    assert row_multi[1] is True

    # 3. Unresolved object -> NULL + is_crosswalk_ambiguous = FALSE
    row_unres = db_conn.execute("""
        SELECT asteroid_key, is_crosswalk_ambiguous FROM nasa_asteroids.v_sentry_object_lifecycle WHERE sentry_id = 'unres_obj'
    """).fetchone()
    assert row_unres[0] is None
    assert row_unres[1] is False


def test_lifecycle_and_presence_duplicate_grain_protection(db_conn):
    """Scenario 10: Asserts primary grains of lifecycle (sentry_id) and presence (snapshot_key, sentry_id)."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date
        ) VALUES
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'ast_A', 'A', '(A)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-02', 'r2', '2026-09-02T00:00:00Z', 'ast_A', 'A', '(A)', 1e-6, 1, -3.0, -3.0, 0, 10.0, '2026-01-01'),
        ('2026-09-01', 'r1', '2026-09-01T00:00:00Z', 'ast_B', 'B', '(B)', 2e-6, 1, -4.0, -4.0, 0, 15.0, '2026-01-01'),
        ('2026-09-02', 'r2', '2026-09-02T00:00:00Z', 'ast_B', 'B', '(B)', 2e-6, 1, -4.0, -4.0, 0, 15.0, '2026-01-01');
    """)

    # Lifecycle grain check: total rows == distinct sentry_ids
    total_life, distinct_life = db_conn.execute("""
        SELECT COUNT(*), COUNT(DISTINCT sentry_id) FROM nasa_asteroids.v_sentry_object_lifecycle
    """).fetchone()
    assert total_life == 2
    assert total_life == distinct_life

    # Presence grain check: total rows == distinct (snapshot_key, sentry_id)
    total_pres, distinct_pres = db_conn.execute("""
        SELECT COUNT(*), COUNT(DISTINCT snapshot_key || ':' || sentry_id) FROM nasa_asteroids.v_sentry_presence_history
    """).fetchone()
    assert total_pres == 4
    assert total_pres == distinct_pres

