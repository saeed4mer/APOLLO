"""NASA Planetary Defense Risk Intelligence Platform — Phase 8 Intelligence Layer Tests.

Validates multi-source Athena analytical views, cross-source characterization profiles,
operational watchlists, canonical entity dossiers, coverage audit rollups,
strict grain invariants, reverse-cardinality defense, and scientific safety guardrails.
"""

from pathlib import Path
import re
import duckdb
import pytest

SCHEMA_SQL_PATH = Path(__file__).parent / "athena_schema.sql"
HIST_SQL_PATH = Path(__file__).parent / "athena_historical_risk.sql"
INTEL_SQL_PATH = Path(__file__).parent / "athena_intelligence_layer.sql"


def execute_sql_file(conn, file_path: Path, frozen_now: str = "2026-09-27T00:00:00Z"):
    """Parse and execute Athena SQL DDL within an in-memory DuckDB connection."""
    with open(file_path, encoding="utf-8") as f:
        sql = f.read()

    clean_lines = [re.sub(r"--.*$", "", line) for line in sql.splitlines()]
    clean_sql = "\n".join(clean_lines)

    statements = [s.strip() for s in clean_sql.split(";") if s.strip()]
    for stmt in statements:
        clean_stmt = re.sub(r"CREATE\s+DATABASE", "CREATE SCHEMA", stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"TBLPROPERTIES\s*\([^)]*\)", "", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"LOCATION\s*'[^']*'", "", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"STORED\s+AS\s+PARQUET", "", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"STORED\s+AS\s+TEXTFILE", "", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"ROW\s+FORMAT\s+DELIMITED", "", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"FIELDS\s+TERMINATED\s+BY\s*'[^']*'", "", clean_stmt, flags=re.IGNORECASE)
        # Convert Athena PARTITIONED BY (col1, col2) into regular columns for offline DuckDB table simulation
        clean_stmt = re.sub(r"\)\s*PARTITIONED\s+BY\s*\(([^)]*)\)", r", \1)", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"PARTITIONED\s+BY\s*\([^)]*\)", "", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"\bEXTERNAL\b", "", clean_stmt, flags=re.IGNORECASE)
        clean_stmt = re.sub(r"\bNOW\(\)", f"CAST('{frozen_now}' AS TIMESTAMP)", clean_stmt, flags=re.IGNORECASE)
        # DuckDB compatibility: quote reserved keyword identifier 'desc' in table column definitions
        clean_stmt = re.sub(
            r"(?:`desc`|(?<![\"`])\bdesc\b(?![\"`]))\s+STRING\b",
            r'"desc" STRING',
            clean_stmt,
            flags=re.IGNORECASE,
        )
        clean_stmt = clean_stmt.strip()

        if clean_stmt:
            conn.execute(clean_stmt)


def build_db_connection(frozen_now: str = "2026-09-27T00:00:00Z"):
    """Create a fresh in-memory DuckDB connection with Athena schema, historical, and intelligence layers."""
    conn = duckdb.connect(":memory:")
    conn.execute("CREATE SCHEMA IF NOT EXISTS nasa_asteroids;")
    # Compatibility macro for Athena/Trino from_iso8601_timestamp
    conn.execute(
        "CREATE OR REPLACE MACRO from_iso8601_timestamp(x) AS "
        "(CAST(x AS TIMESTAMPTZ) AT TIME ZONE 'UTC');"
    )

    # 1. Load NeoWs base table DDL
    execute_sql_file(conn, SCHEMA_SQL_PATH, frozen_now=frozen_now)
    # 2. Load Phase 7 Historical Sentry & Bridge layer
    execute_sql_file(conn, HIST_SQL_PATH, frozen_now=frozen_now)
    # 3. Load Phase 8 Multi-Source Intelligence layer
    execute_sql_file(conn, INTEL_SQL_PATH, frozen_now=frozen_now)

    return conn


@pytest.fixture
def db_conn():
    """Create a fresh in-memory DuckDB connection per test."""
    return build_db_connection()


def test_intelligence_ddl_registration(db_conn):
    """Test 1: Verifies all 5 external tables and 4 analytical views parse and register cleanly."""
    tables = [
        row[0]
        for row in db_conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'nasa_asteroids'"
        ).fetchall()
    ]
    # Phase 8 External Tables
    assert "fact_sbdb_object_snapshot" in tables
    assert "fact_sbdb_orbit" in tables
    assert "fact_sbdb_orbit_element" in tables
    assert "fact_sbdb_physical_parameter" in tables
    assert "fact_entity_resolution" in tables
    # Phase 8 Analytical Views
    assert "v_sbdb_characterization_profile" in tables
    assert "v_neows_sentry_threat_watchlist" in tables
    assert "v_asteroid_cross_source_profile" in tables
    assert "v_crosswalk_coverage_audit" in tables


def test_sbdb_characterization_keplerian_and_physical_pivot(db_conn):
    """Test 2: Validates pivoting of Keplerian elements (e, a, q, i) and physical parameters (D, H, albedo, rot_per)."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.fact_sbdb_object_snapshot (
            snapshot_key, run_id, snapshot_time, spkid, designation, fullname, shortname,
            object_kind, is_neo, is_pha, orbit_class_code, orbit_class_name, orbit_id, prefix
        ) VALUES (
            '2026-09-20', 'r1', '2026-09-20T00:00:00Z', '50548689', '2010 TW54', '(2010 TW54)', '2010 TW54',
            'Asteroid', TRUE, FALSE, 'APO', 'Apollo', '25', NULL
        );

        INSERT INTO nasa_asteroids.fact_sbdb_orbit (
            snapshot_key, run_id, snapshot_time, spkid, orbit_id, epoch_jd, equinox, soln_date,
            orbit_source, producer, first_obs, last_obs, data_arc_days, n_obs_used, condition_code,
            rms, earth_moid_au, jupiter_moid_au, t_jup
        ) VALUES (
            '2026-09-20', 'r1', '2026-09-20T00:00:00Z', '50548689', '25', 2459000.5, 'J2000', '2021-04-15',
            'JPL', 'Giorgini', '2010-10-01', '2021-04-10', 3844, 150, '1',
            0.35, 0.052, 2.85, 3.42
        );

        INSERT INTO nasa_asteroids.fact_sbdb_orbit_element (spkid, element_name, element_value)
        VALUES
            ('50548689', 'e', 0.452),
            ('50548689', 'a', 1.854),
            ('50548689', 'q', 1.016),
            ('50548689', 'i', 14.25);

        INSERT INTO nasa_asteroids.fact_sbdb_physical_parameter (spkid, param_name, param_value_numeric)
        VALUES
            ('50548689', 'diameter', 0.85),
            ('50548689', 'H', 18.2),
            ('50548689', 'albedo', 0.18),
            ('50548689', 'rot_per', 4.52);

        INSERT INTO nasa_asteroids.bridge_asteroid_identifier (
            asteroid_key, source_system, identifier_name, identifier_value, is_primary_pivot
        ) VALUES (
            'ast_2010_tw54', 'sbdb', 'spkid', '50548689', TRUE
        );
    """)

    row = db_conn.execute("""
        SELECT
            spkid, asteroid_key, designation, orbit_class_name,
            eccentricity, semi_major_axis_au, perihelion_distance_au, inclination_deg, orbital_period_yr,
            estimated_diameter_km, absolute_magnitude, albedo, rotational_period_hr,
            earth_moid_au, condition_code, astrometric_data_quality_tier
        FROM nasa_asteroids.v_sbdb_characterization_profile
        WHERE spkid = '50548689'
    """).fetchone()

    assert row[0] == "50548689"
    assert row[1] == "ast_2010_tw54"
    assert row[2] == "2010 TW54"
    assert row[3] == "Apollo"
    assert row[4] == 0.452
    assert row[5] == 1.854
    assert row[6] == 1.016
    assert row[7] == 14.25
    assert round(row[8], 3) == round(1.854**1.5, 3)
    assert row[9] == 0.85
    assert row[10] == 18.2
    assert row[11] == 0.18
    assert row[12] == 4.52
    assert row[13] == 0.052
    assert row[14] == "1"
    assert row[15] == "ADEQUATELY_CONSTRAINED"


def test_sbdb_characterization_astrometric_quality_tier(db_conn):
    """Test 3: Tests ADEQUATELY_CONSTRAINED, FOLLOWUP_PRIORITY_LIMITED_ARC, and UNREPORTED tiers with TRY_CAST safety."""
    db_conn.execute("""
        -- Object 1: Adequate (code 1, arc 50, obs 30)
        INSERT INTO nasa_asteroids.fact_sbdb_object_snapshot (spkid, designation) VALUES ('obj_adeq', 'Obj Adeq');
        INSERT INTO nasa_asteroids.fact_sbdb_orbit (spkid, condition_code, data_arc_days, n_obs_used)
        VALUES ('obj_adeq', '1', 50, 30);

        -- Object 2: High uncertainty condition code 6 (limited)
        INSERT INTO nasa_asteroids.fact_sbdb_object_snapshot (spkid, designation) VALUES ('obj_high_u', 'Obj High U');
        INSERT INTO nasa_asteroids.fact_sbdb_orbit (spkid, condition_code, data_arc_days, n_obs_used)
        VALUES ('obj_high_u', '6', 100, 50);

        -- Object 3: Short arc 15 days (limited)
        INSERT INTO nasa_asteroids.fact_sbdb_object_snapshot (spkid, designation) VALUES ('obj_short_arc', 'Obj Short Arc');
        INSERT INTO nasa_asteroids.fact_sbdb_orbit (spkid, condition_code, data_arc_days, n_obs_used)
        VALUES ('obj_short_arc', '2', 15, 50);

        -- Object 4: Non-numeric condition_code 'unknown' (TRY_CAST -> NULL, falls back to limited)
        INSERT INTO nasa_asteroids.fact_sbdb_object_snapshot (spkid, designation) VALUES ('obj_unknown', 'Obj Unknown');
        INSERT INTO nasa_asteroids.fact_sbdb_orbit (spkid, condition_code, data_arc_days, n_obs_used)
        VALUES ('obj_unknown', 'unknown', 100, 50);

        -- Object 5: Unreported orbital fit (no orbit record)
        INSERT INTO nasa_asteroids.fact_sbdb_object_snapshot (spkid, designation) VALUES ('obj_unrep', 'Obj Unreported');
    """)

    results = dict(
        db_conn.execute("""
            SELECT spkid, astrometric_data_quality_tier
            FROM nasa_asteroids.v_sbdb_characterization_profile
            WHERE spkid IN ('obj_adeq', 'obj_high_u', 'obj_short_arc', 'obj_unknown', 'obj_unrep')
        """).fetchall()
    )

    assert results["obj_adeq"] == "ADEQUATELY_CONSTRAINED"
    assert results["obj_high_u"] == "FOLLOWUP_PRIORITY_LIMITED_ARC"
    assert results["obj_short_arc"] == "FOLLOWUP_PRIORITY_LIMITED_ARC"
    assert results["obj_unknown"] == "FOLLOWUP_PRIORITY_LIMITED_ARC"
    assert results["obj_unrep"] == "UNREPORTED"


def test_watchlist_grain_and_unmonitored_approach(db_conn):
    """Test 4: Unmonitored NeoWs encounters preserve grain and set is_sentry_monitored = FALSE."""
    db_conn.execute("""
        -- Duplicate partition loads across consecutive ingestion dates
        INSERT INTO nasa_asteroids.asteroids (id, name, closest_approach_date, miss_distance_km, hazardous, year, month, day)
        VALUES
            ('neo_101', 'Asteroid 101', '2026-10-01', 12500000.0, FALSE, '2026', '09', '20'),
            ('neo_101', 'Asteroid 101', '2026-10-01', 12500000.0, FALSE, '2026', '09', '21'),
            ('neo_101', 'Asteroid 101', '2026-11-15', 35000000.0, FALSE, '2026', '09', '21');
    """)

    rows = db_conn.execute("""
        SELECT closest_approach_date, neows_id, neo_name, is_sentry_monitored, sentry_id, sentry_impact_probability
        FROM nasa_asteroids.v_neows_sentry_threat_watchlist
        WHERE neows_id = 'neo_101'
        ORDER BY closest_approach_date ASC
    """).fetchall()

    # Partition deduplication ensures exactly 2 distinct encounter encounters
    assert len(rows) == 2
    assert rows[0][0] == "2026-10-01"
    assert rows[0][1] == "neo_101"
    assert rows[0][3] is False
    assert rows[0][4] is None
    assert rows[0][5] is None

    assert rows[1][0] == "2026-11-15"
    assert rows[1][1] == "neo_101"
    assert rows[1][3] is False


def test_watchlist_monitored_threat_enrichment(db_conn):
    """Test 5: Verified cross-source linkage enriches impact probability, Palermo, and SBDB MOID."""
    db_conn.execute("""
        -- 1. NeoWs close encounter
        INSERT INTO nasa_asteroids.asteroids (id, name, closest_approach_date, miss_distance_km, hazardous, year, month, day)
        VALUES ('neo_2010', '(2010 TW54)', '2026-09-30', 17457205.0, FALSE, '2026', '09', '27');

        -- 2. Bridge links NeoWs ID and Sentry ID to same canonical key
        INSERT INTO nasa_asteroids.bridge_asteroid_identifier (asteroid_key, source_system, identifier_name, identifier_value, is_primary_pivot)
        VALUES
            ('ast_canon_2010', 'neows', 'id', 'neo_2010', FALSE),
            ('ast_canon_2010', 'sentry', 'sentry_id', 'bK10T54W', FALSE),
            ('ast_canon_2010', 'sbdb', 'spkid', '50548689', TRUE);

        -- 3. Sentry snapshot facts
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date, impact_year_range
        ) VALUES (
            '2026-09-27', 'r1', '2026-09-27T00:00:00Z', 'bK10T54W', '2010 TW54', '(2010 TW54)',
            1.2e-6, 8, -2.1, -2.1, 0, 18.5, '2026-01-01', '2032-2110'
        );

        -- 4. SBDB characterization
        INSERT INTO nasa_asteroids.fact_sbdb_object_snapshot (spkid, designation, fullname, orbit_class_name, is_pha)
        VALUES ('50548689', '2010 TW54', '(2010 TW54)', 'Apollo', FALSE);
        INSERT INTO nasa_asteroids.fact_sbdb_orbit (spkid, earth_moid_au, condition_code, data_arc_days, n_obs_used)
        VALUES ('50548689', 0.052, '1', 3500, 120);
    """)

    row = db_conn.execute("""
        SELECT
            closest_approach_date, neows_id, neo_name, asteroid_key, is_sentry_monitored,
            sentry_id, sentry_impact_probability, sentry_palermo_scale_max, sentry_impact_year_range,
            sbdb_spkid, sbdb_orbit_class_name, sbdb_earth_moid_au, sbdb_astrometric_quality_tier
        FROM nasa_asteroids.v_neows_sentry_threat_watchlist
        WHERE neows_id = 'neo_2010'
    """).fetchone()

    assert row[0] == "2026-09-30"
    assert row[1] == "neo_2010"
    assert row[3] == "ast_canon_2010"
    assert row[4] is True
    assert row[5] == "bK10T54W"
    assert row[6] == 1.2e-6
    assert row[7] == -2.1
    assert row[8] == "2032-2110"
    assert row[9] == "50548689"
    assert row[10] == "Apollo"
    assert row[11] == 0.052
    assert row[12] == "ADEQUATELY_CONSTRAINED"


def test_watchlist_latest_impact_year_range_resolution(db_conn):
    """Test 6: Validates deterministic latest impact_year_range resolution across multiple historical snapshots."""
    db_conn.execute("""
        INSERT INTO nasa_asteroids.asteroids (id, name, closest_approach_date, miss_distance_km, hazardous, year, month, day)
        VALUES ('neo_range', 'Range Obj', '2026-10-15', 5000000.0, FALSE, '2026', '09', '27');

        INSERT INTO nasa_asteroids.bridge_asteroid_identifier (asteroid_key, source_system, identifier_name, identifier_value, is_primary_pivot)
        VALUES
            ('ast_range', 'neows', 'id', 'neo_range', FALSE),
            ('ast_range', 'sentry', 'sentry_id', 'sentry_range_obj', FALSE);

        -- Two historical Sentry snapshots with updated impact year range
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date, impact_year_range
        ) VALUES
            ('2026-09-20', 'r1', '2026-09-20T00:00:00Z', 'sentry_range_obj', 'Range', '(Range)', 1e-6, 5, -2.5, -2.5, 0, 15.0, '2026-01-01', '2030-2050'),
            ('2026-09-27', 'r2', '2026-09-27T00:00:00Z', 'sentry_range_obj', 'Range', '(Range)', 2e-6, 7, -2.2, -2.2, 0, 15.0, '2026-01-01', '2030-2075');
    """)

    row = db_conn.execute("""
        SELECT sentry_impact_year_range, sentry_impact_probability
        FROM nasa_asteroids.v_neows_sentry_threat_watchlist
        WHERE neows_id = 'neo_range'
    """).fetchone()

    # Must resolve from latest snapshot '2026-09-27'
    assert row[0] == "2030-2075"
    assert row[1] == 2e-6


def test_cross_source_profile_grain_and_presence_flags(db_conn):
    """Test 7: Asserts exactly 1 row per asteroid_key and verified source presence flags."""
    db_conn.execute("""
        -- Key 1: Present in all 3 systems
        INSERT INTO nasa_asteroids.bridge_asteroid_identifier (asteroid_key, source_system, identifier_name, identifier_value, is_primary_pivot)
        VALUES
            ('ast_full', 'neows', 'id', 'neo_full', FALSE),
            ('ast_full', 'sentry', 'sentry_id', 'sentry_full', FALSE),
            ('ast_full', 'sbdb', 'spkid', 'spk_full', TRUE);

        INSERT INTO nasa_asteroids.asteroids (id, name, closest_approach_date, miss_distance_km, hazardous, year, month, day)
        VALUES ('neo_full', 'Full', '2026-10-01', 1000000.0, TRUE, '2026', '09', '27');

        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date, impact_year_range
        ) VALUES ('2026-09-27', 'r1', '2026-09-27T00:00:00Z', 'sentry_full', 'Full', '(Full)', 1e-5, 3, -1.8, -1.8, 0, 12.0, '2026-01-01', '2040-2080');

        INSERT INTO nasa_asteroids.fact_sbdb_object_snapshot (spkid, designation, fullname, orbit_class_name, is_pha)
        VALUES ('spk_full', 'Full', '(Full)', 'Aten', TRUE);

        -- Key 2: SBDB only (catalog object never seen on NeoWs or Sentry)
        INSERT INTO nasa_asteroids.bridge_asteroid_identifier (asteroid_key, source_system, identifier_name, identifier_value, is_primary_pivot)
        VALUES ('ast_sbdb_only', 'sbdb', 'spkid', 'spk_solo', TRUE);

        INSERT INTO nasa_asteroids.fact_sbdb_object_snapshot (spkid, designation, fullname, orbit_class_name, is_pha)
        VALUES ('spk_solo', 'Solo', '(Solo)', 'Main Belt', FALSE);
    """)

    rows = {
        r[0]: r[1:]
        for r in db_conn.execute("""
            SELECT
                asteroid_key,
                has_neows_telemetry, has_sbdb_characterization, has_sentry_monitoring,
                neows_total_approaches_recorded, sbdb_orbit_class_name, sentry_id
            FROM nasa_asteroids.v_asteroid_cross_source_profile
            WHERE asteroid_key IN ('ast_full', 'ast_sbdb_only')
        """).fetchall()
    }

    full = rows["ast_full"]
    assert full[0] is True
    assert full[1] is True
    assert full[2] is True
    assert full[3] == 1
    assert full[4] == "Aten"
    assert full[5] == "sentry_full"

    solo = rows["ast_sbdb_only"]
    assert solo[0] is False
    assert solo[1] is True
    assert solo[2] is False
    assert solo[3] is None
    assert solo[4] == "Main Belt"
    assert solo[5] is None


def test_crosswalk_coverage_audit_current_run_isolation(db_conn):
    """Test 8: Confirms audit strictly isolates the latest resolution run and computes exact 100% denominator math."""
    db_conn.execute("""
        -- Run 1 (older run: 2 evaluations)
        INSERT INTO nasa_asteroids.fact_entity_resolution (
            resolution_run_id, resolved_at, source_system, identifier_name, source_identifier_value,
            assigned_asteroid_key, match_state, match_rule
        ) VALUES
            ('run_old', '2026-09-01T00:00:00Z', 'neows', 'id', '101', 'ast_1', 'RESOLVED', 'EXACT_SPKID_MATCH'),
            ('run_old', '2026-09-01T00:00:00Z', 'neows', 'id', '102', NULL, 'UNRESOLVED', 'NO_CROSS_SOURCE_MATCH');

        -- Run 2 (latest run: 3 evaluations for neows, 2 for sentry)
        INSERT INTO nasa_asteroids.fact_entity_resolution (
            resolution_run_id, resolved_at, source_system, identifier_name, source_identifier_value,
            assigned_asteroid_key, match_state, match_rule
        ) VALUES
            ('run_new', '2026-09-27T12:00:00Z', 'neows', 'id', '101', 'ast_1', 'RESOLVED', 'EXACT_SPKID_MATCH'),
            ('run_new', '2026-09-27T12:00:00Z', 'neows', 'id', '102', NULL, 'UNRESOLVED', 'NO_CROSS_SOURCE_MATCH'),
            ('run_new', '2026-09-27T12:00:00Z', 'neows', 'id', '103', NULL, 'AMBIGUOUS', 'DUPLICATE_COLLISION_QUARANTINE'),
            ('run_new', '2026-09-27T12:00:00Z', 'sentry', 'sentry_id', 's_1', 'ast_1', 'RESOLVED', 'EXACT_DESIGNATION_MATCH'),
            ('run_new', '2026-09-27T12:00:00Z', 'sentry', 'sentry_id', 's_2', NULL, 'UNRESOLVED', 'NO_CROSS_SOURCE_MATCH');
    """)

    rows = db_conn.execute("""
        SELECT source_system, match_state, evaluation_count, total_source_system_evaluations, pct_of_source_system, resolution_run_id
        FROM nasa_asteroids.v_crosswalk_coverage_audit
        ORDER BY source_system ASC, match_state ASC
    """).fetchall()

    # Assert only 'run_new' is present
    for r in rows:
        assert r[5] == "run_new"

    # Group by source_system and sum percentages
    pct_by_sys = {}
    for r in rows:
        src = r[0]
        pct_by_sys[src] = pct_by_sys.get(src, 0.0) + r[4]

    assert round(pct_by_sys["neows"], 2) == 100.0
    assert round(pct_by_sys["sentry"], 2) == 100.0


def test_partial_sbdb_coverage_preserves_population(db_conn):
    """Test 9: Verifies that partial or missing SBDB records never drop NeoWs encounters or Sentry objects."""
    db_conn.execute("""
        -- NeoWs encounter with bridge key, but ZERO SBDB rows exist in database
        INSERT INTO nasa_asteroids.asteroids (id, name, closest_approach_date, miss_distance_km, hazardous, year, month, day)
        VALUES ('neo_no_sbdb', 'No SBDB Asteroid', '2026-10-05', 8500000.0, FALSE, '2026', '09', '27');

        INSERT INTO nasa_asteroids.bridge_asteroid_identifier (asteroid_key, source_system, identifier_name, identifier_value, is_primary_pivot)
        VALUES ('ast_no_sbdb', 'neows', 'id', 'neo_no_sbdb', FALSE);
    """)

    row = db_conn.execute("""
        SELECT neows_id, closest_approach_date, sbdb_spkid, sbdb_orbit_class_name, sbdb_astrometric_quality_tier
        FROM nasa_asteroids.v_neows_sentry_threat_watchlist
        WHERE neows_id = 'neo_no_sbdb'
    """).fetchone()

    assert row[0] == "neo_no_sbdb"
    assert row[1] == "2026-10-05"
    assert row[2] is None
    assert row[3] is None
    assert row[4] == "UNREPORTED"


def test_scientific_safety_constraints():
    """Test 10: Asserts SQL code contains zero composite threat formulas, zero percentage Palermo metrics, and zero causal terms."""
    sql_text = INTEL_SQL_PATH.read_text(encoding="utf-8").lower()

    # Zero composite danger scores
    assert "danger_score" not in sql_text
    assert "threat_score" not in sql_text
    assert "composite_score" not in sql_text
    assert "threat_index" not in sql_text

    # Zero percentage calculations across logarithmic Palermo values
    assert "palermo" not in sql_text or "pct_palermo" not in sql_text
    assert "palermo_pct" not in sql_text

    # Zero causal trajectory claims
    assert "caused" not in sql_text
    assert "perturbation_event" not in sql_text


def test_watchlist_reverse_cardinality_multiple_sentry_ids(db_conn):
    """Test 11: Reverse-cardinality defense - asserts multiple Sentry IDs per asteroid_key do not multiply watchlist grain and preserve ambiguity."""
    db_conn.execute("""
        -- 1. Single NeoWs approach
        INSERT INTO nasa_asteroids.asteroids (id, name, closest_approach_date, miss_distance_km, hazardous, year, month, day)
        VALUES ('neo_multi_sentry', 'Multi Sentry Target', '2026-10-20', 4500000.0, TRUE, '2026', '09', '27');

        -- 2. One asteroid_key mapping to TWO distinct Sentry objects
        INSERT INTO nasa_asteroids.bridge_asteroid_identifier (asteroid_key, source_system, identifier_name, identifier_value, is_primary_pivot)
        VALUES
            ('ast_multi_sen', 'neows', 'id', 'neo_multi_sentry', FALSE),
            ('ast_multi_sen', 'sentry', 'sentry_id', 'sentry_sol_A', FALSE),
            ('ast_multi_sen', 'sentry', 'sentry_id', 'sentry_sol_B', FALSE);

        -- 3. Both Sentry objects have snapshots
        INSERT INTO nasa_asteroids.fact_sentry_risk_snapshot (
            snapshot_key, run_id, snapshot_time, sentry_id, designation, fullname,
            impact_probability, potential_impacts_count, palermo_scale_cum, palermo_scale_max,
            torino_scale_max, v_infinity_km_s, last_obs_date, impact_year_range
        ) VALUES
            ('2026-09-27', 'r1', '2026-09-27T00:00:00Z', 'sentry_sol_A', 'Sol A', '(Sol A)', 1e-5, 3, -1.5, -1.5, 0, 14.0, '2026-01-01', '2030-2040'),
            ('2026-09-27', 'r1', '2026-09-27T00:00:00Z', 'sentry_sol_B', 'Sol B', '(Sol B)', 5e-5, 6, -1.2, -1.2, 1, 14.5, '2026-01-01', '2035-2060');
    """)

    # Query Watchlist
    watchlist_rows = db_conn.execute("""
        SELECT
            closest_approach_date, neows_id, asteroid_key, is_sentry_monitored,
            is_sentry_ambiguous, sentry_identifier_count, sentry_id,
            sentry_impact_probability, sentry_all_time_max_impact_probability
        FROM nasa_asteroids.v_neows_sentry_threat_watchlist
        WHERE neows_id = 'neo_multi_sentry'
    """).fetchall()

    # Grain MUST NOT multiply (remains exactly 1 row)
    assert len(watchlist_rows) == 1
    row = watchlist_rows[0]
    assert row[0] == "2026-10-20"
    assert row[1] == "neo_multi_sentry"
    assert row[2] == "ast_multi_sen"
    assert row[3] is True  # Monitored on Sentry
    assert row[4] is True  # Ambiguous (competing Sentry solutions)
    assert row[5] == 2  # 2 Sentry IDs linked
    assert row[6] is None  # Single sentry_id evaluates to NULL rather than arbitrary selection
    assert row[7] is None  # Point-in-time metrics evaluate to NULL
    assert row[8] == 5e-5  # Safe mathematical upper bound is preserved

    # Query Cross-Source Profile
    profile_rows = db_conn.execute("""
        SELECT
            asteroid_key, has_sentry_monitoring, is_sentry_ambiguous, sentry_identifier_count,
            sentry_id, sentry_latest_impact_probability, sentry_all_time_max_impact_probability
        FROM nasa_asteroids.v_asteroid_cross_source_profile
        WHERE asteroid_key = 'ast_multi_sen'
    """).fetchall()

    assert len(profile_rows) == 1
    p = profile_rows[0]
    assert p[0] == "ast_multi_sen"
    assert p[1] is True
    assert p[2] is True
    assert p[3] == 2
    assert p[4] is None
    assert p[5] is None
    assert p[6] == 5e-5
