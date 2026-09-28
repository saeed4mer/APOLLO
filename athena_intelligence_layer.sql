-- ============================================================================
-- NASA Planetary Defense Risk Intelligence Platform — Phase 8 Intelligence Layer
-- AWS Athena / Trino DDL: Multi-Source Intelligence & Characterization Layer
-- ============================================================================
-- Architecture: Serverless Lakehouse (S3 + Athena SQL Views)
-- Grains:
--   v_sbdb_characterization_profile:   (spkid)
--   v_neows_sentry_threat_watchlist:   (closest_approach_date, neows_id)
--   v_asteroid_cross_source_profile:   (asteroid_key)
--   v_crosswalk_coverage_audit:        (source_system, match_state, match_rule)
-- Scientific Safety: Zero composite danger scores; zero synthetic threat formulas;
--                   zero percentage changes on logarithmic scales; zero causal claims.
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 1. External Table DDL: SBDB Object Snapshots (Phase 5 Ingestion)
-- ----------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS nasa_asteroids.fact_sbdb_object_snapshot (
    snapshot_key STRING,
    run_id STRING,
    snapshot_time STRING,
    spkid STRING,
    designation STRING,
    fullname STRING,
    shortname STRING,
    object_kind STRING,
    is_neo BOOLEAN,
    is_pha BOOLEAN,
    orbit_class_code STRING,
    orbit_class_name STRING,
    orbit_id STRING,
    prefix STRING
)
PARTITIONED BY (
    year STRING,
    month STRING,
    day STRING
)
STORED AS PARQUET
LOCATION 's3://nasa-asteroid-intelligence/processed/sbdb/fact_sbdb_object_snapshot/'
TBLPROPERTIES (
    'parquet.compression'='SNAPPY',
    'projection.enabled'='true',
    'projection.year.type'='integer',
    'projection.year.range'='2020,2030',
    'projection.year.digits'='4',
    'projection.month.type'='integer',
    'projection.month.range'='1,12',
    'projection.month.digits'='2',
    'projection.day.type'='integer',
    'projection.day.range'='1,31',
    'projection.day.digits'='2',
    'storage.location.template'='s3://nasa-asteroid-intelligence/processed/sbdb/fact_sbdb_object_snapshot/year=${year}/month=${month}/day=${day}/'
);

-- ----------------------------------------------------------------------------
-- 2. External Table DDL: SBDB Orbit Solutions (Phase 5 Ingestion)
-- ----------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS nasa_asteroids.fact_sbdb_orbit (
    snapshot_key STRING,
    run_id STRING,
    snapshot_time STRING,
    spkid STRING,
    orbit_id STRING,
    epoch_jd DOUBLE,
    equinox STRING,
    soln_date STRING,
    orbit_source STRING,
    producer STRING,
    first_obs STRING,
    last_obs STRING,
    data_arc_days BIGINT,
    n_obs_used BIGINT,
    condition_code STRING,
    rms DOUBLE,
    earth_moid_au DOUBLE,
    jupiter_moid_au DOUBLE,
    t_jup DOUBLE,
    pe_used STRING,
    sb_used STRING
)
PARTITIONED BY (
    year STRING,
    month STRING,
    day STRING
)
STORED AS PARQUET
LOCATION 's3://nasa-asteroid-intelligence/processed/sbdb/fact_sbdb_orbit/'
TBLPROPERTIES (
    'parquet.compression'='SNAPPY',
    'projection.enabled'='true',
    'projection.year.type'='integer',
    'projection.year.range'='2020,2030',
    'projection.year.digits'='4',
    'projection.month.type'='integer',
    'projection.month.range'='1,12',
    'projection.month.digits'='2',
    'projection.day.type'='integer',
    'projection.day.range'='1,31',
    'projection.day.digits'='2',
    'storage.location.template'='s3://nasa-asteroid-intelligence/processed/sbdb/fact_sbdb_orbit/year=${year}/month=${month}/day=${day}/'
);

-- ----------------------------------------------------------------------------
-- 3. External Table DDL: SBDB Keplerian Orbit Elements (Phase 5 Ingestion)
-- ----------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS nasa_asteroids.fact_sbdb_orbit_element (
    snapshot_key STRING,
    run_id STRING,
    snapshot_time STRING,
    spkid STRING,
    orbit_id STRING,
    epoch_jd DOUBLE,
    equinox STRING,
    element_name STRING,
    element_value DOUBLE,
    sigma DOUBLE,
    units STRING,
    title STRING,
    label STRING
)
PARTITIONED BY (
    year STRING,
    month STRING,
    day STRING
)
STORED AS PARQUET
LOCATION 's3://nasa-asteroid-intelligence/processed/sbdb/fact_sbdb_orbit_element/'
TBLPROPERTIES (
    'parquet.compression'='SNAPPY',
    'projection.enabled'='true',
    'projection.year.type'='integer',
    'projection.year.range'='2020,2030',
    'projection.year.digits'='4',
    'projection.month.type'='integer',
    'projection.month.range'='1,12',
    'projection.month.digits'='2',
    'projection.day.type'='integer',
    'projection.day.range'='1,31',
    'projection.day.digits'='2',
    'storage.location.template'='s3://nasa-asteroid-intelligence/processed/sbdb/fact_sbdb_orbit_element/year=${year}/month=${month}/day=${day}/'
);

-- ----------------------------------------------------------------------------
-- 4. External Table DDL: SBDB Physical Parameters (Phase 5 Ingestion)
-- ----------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS nasa_asteroids.fact_sbdb_physical_parameter (
    snapshot_key STRING,
    run_id STRING,
    snapshot_time STRING,
    spkid STRING,
    param_name STRING,
    param_value_numeric DOUBLE,
    param_value_raw STRING,
    sigma DOUBLE,
    units STRING,
    bib_reference STRING,
    notes STRING,
    title STRING,
    desc STRING
)
PARTITIONED BY (
    year STRING,
    month STRING,
    day STRING
)
STORED AS PARQUET
LOCATION 's3://nasa-asteroid-intelligence/processed/sbdb/fact_sbdb_physical_parameter/'
TBLPROPERTIES (
    'parquet.compression'='SNAPPY',
    'projection.enabled'='true',
    'projection.year.type'='integer',
    'projection.year.range'='2020,2030',
    'projection.year.digits'='4',
    'projection.month.type'='integer',
    'projection.month.range'='1,12',
    'projection.month.digits'='2',
    'projection.day.type'='integer',
    'projection.day.range'='1,31',
    'projection.day.digits'='2',
    'storage.location.template'='s3://nasa-asteroid-intelligence/processed/sbdb/fact_sbdb_physical_parameter/year=${year}/month=${month}/day=${day}/'
);

-- ----------------------------------------------------------------------------
-- 5. External Table DDL: Entity Resolution Audit Trail (Phase 6 Resolution)
-- ----------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS nasa_asteroids.fact_entity_resolution (
    resolution_run_id STRING,
    resolved_at STRING,
    source_system STRING,
    identifier_name STRING,
    source_identifier_value STRING,
    matched_target_system STRING,
    matched_target_identifier_name STRING,
    matched_target_identifier_value STRING,
    assigned_asteroid_key STRING,
    match_state STRING,
    match_rule STRING,
    evidence_json STRING
)
PARTITIONED BY (
    year STRING,
    month STRING,
    day STRING
)
STORED AS PARQUET
LOCATION 's3://nasa-asteroid-intelligence/reference/asteroid_crosswalk/fact_entity_resolution/'
TBLPROPERTIES (
    'parquet.compression'='SNAPPY',
    'projection.enabled'='true',
    'projection.year.type'='integer',
    'projection.year.range'='2020,2030',
    'projection.year.digits'='4',
    'projection.month.type'='integer',
    'projection.month.range'='1,12',
    'projection.month.digits'='2',
    'projection.day.type'='integer',
    'projection.day.range'='1,31',
    'projection.day.digits'='2',
    'storage.location.template'='s3://nasa-asteroid-intelligence/reference/asteroid_crosswalk/fact_entity_resolution/year=${year}/month=${month}/day=${day}/'
);

-- ----------------------------------------------------------------------------
-- 6. Analytical View 1: SBDB Astronomical & Orbital Characterization Profile
-- ----------------------------------------------------------------------------
-- Purpose: Unified astronomical, Keplerian, and physical characterization.
-- Grain: (spkid)
CREATE OR REPLACE VIEW nasa_asteroids.v_sbdb_characterization_profile AS
WITH latest_obj AS (
    SELECT *
    FROM (
        SELECT
            spkid,
            designation,
            fullname,
            shortname,
            object_kind,
            is_neo,
            is_pha,
            orbit_class_code,
            orbit_class_name,
            orbit_id,
            ROW_NUMBER() OVER (
                PARTITION BY spkid
                ORDER BY snapshot_key DESC, snapshot_time DESC, run_id DESC
            ) AS rn
        FROM nasa_asteroids.fact_sbdb_object_snapshot
    )
    WHERE rn = 1
),
latest_orb AS (
    SELECT *
    FROM (
        SELECT
            spkid,
            orbit_id,
            epoch_jd,
            equinox,
            soln_date,
            orbit_source,
            producer,
            data_arc_days,
            n_obs_used,
            condition_code,
            rms,
            earth_moid_au,
            jupiter_moid_au,
            t_jup,
            ROW_NUMBER() OVER (
                PARTITION BY spkid
                ORDER BY snapshot_key DESC, snapshot_time DESC, run_id DESC, orbit_id DESC
            ) AS rn
        FROM nasa_asteroids.fact_sbdb_orbit
    )
    WHERE rn = 1
),
elements_pivoted AS (
    SELECT
        spkid,
        MAX(CASE WHEN element_name = 'e' THEN element_value END) AS eccentricity,
        MAX(CASE WHEN element_name = 'a' THEN element_value END) AS semi_major_axis_au,
        MAX(CASE WHEN element_name = 'q' THEN element_value END) AS perihelion_distance_au,
        MAX(CASE WHEN element_name = 'i' THEN element_value END) AS inclination_deg
    FROM nasa_asteroids.fact_sbdb_orbit_element
    GROUP BY spkid
),
phys_pivoted AS (
    SELECT
        spkid,
        MAX(CASE WHEN param_name = 'diameter' THEN param_value_numeric END) AS estimated_diameter_km,
        MAX(CASE WHEN param_name = 'H' THEN param_value_numeric END) AS absolute_magnitude,
        MAX(CASE WHEN param_name = 'albedo' THEN param_value_numeric END) AS albedo,
        MAX(CASE WHEN param_name = 'rot_per' THEN param_value_numeric END) AS rotational_period_hr
    FROM nasa_asteroids.fact_sbdb_physical_parameter
    GROUP BY spkid
),
bridge_sbdb AS (
    SELECT
        identifier_value AS spkid,
        MAX(asteroid_key) AS asteroid_key
    FROM nasa_asteroids.bridge_asteroid_identifier
    WHERE source_system = 'sbdb'
      AND identifier_name = 'spkid'
      AND is_primary_pivot = TRUE
    GROUP BY identifier_value
)
SELECT
    o.spkid,
    b.asteroid_key,
    o.designation,
    o.fullname,
    o.shortname,
    o.object_kind,
    o.is_neo,
    o.is_pha,
    o.orbit_class_code,
    o.orbit_class_name,
    o.orbit_id,
    orb.epoch_jd,
    orb.soln_date,
    orb.data_arc_days,
    orb.n_obs_used,
    orb.condition_code,
    orb.rms,
    orb.earth_moid_au,
    orb.jupiter_moid_au,
    orb.t_jup,
    elem.eccentricity,
    elem.semi_major_axis_au,
    elem.perihelion_distance_au,
    elem.inclination_deg,
    CASE
        WHEN elem.semi_major_axis_au IS NOT NULL AND elem.semi_major_axis_au > 0
        THEN POWER(elem.semi_major_axis_au, 1.5)
        ELSE NULL
    END AS orbital_period_yr,
    phys.estimated_diameter_km,
    phys.absolute_magnitude,
    phys.albedo,
    phys.rotational_period_hr,
    CASE
        WHEN orb.data_arc_days IS NULL AND orb.n_obs_used IS NULL AND orb.condition_code IS NULL THEN 'UNREPORTED'
        WHEN TRY_CAST(orb.condition_code AS INTEGER) IS NOT NULL
         AND TRY_CAST(orb.condition_code AS INTEGER) < 5
         AND orb.data_arc_days >= 30
         AND orb.n_obs_used >= 20 THEN 'ADEQUATELY_CONSTRAINED'
        ELSE 'FOLLOWUP_PRIORITY_LIMITED_ARC'
    END AS astrometric_data_quality_tier
FROM latest_obj o
LEFT JOIN latest_orb orb ON orb.spkid = o.spkid
LEFT JOIN elements_pivoted elem ON elem.spkid = o.spkid
LEFT JOIN phys_pivoted phys ON phys.spkid = o.spkid
LEFT JOIN bridge_sbdb b ON b.spkid = o.spkid;

-- ----------------------------------------------------------------------------
-- 7. Analytical View 2: NeoWs Sentry Threat Watchlist
-- ----------------------------------------------------------------------------
-- Purpose: Operational correlation of Earth close encounters with Sentry risk monitoring.
-- Grain: (closest_approach_date, neows_id)
CREATE OR REPLACE VIEW nasa_asteroids.v_neows_sentry_threat_watchlist AS
WITH deduped_neows AS (
    SELECT
        id AS neows_id,
        name AS neo_name,
        closest_approach_date,
        miss_distance_km,
        hazardous AS is_neo_hazardous
    FROM (
        SELECT
            id,
            name,
            closest_approach_date,
            miss_distance_km,
            hazardous,
            ROW_NUMBER() OVER (
                PARTITION BY id, closest_approach_date
                ORDER BY year DESC, month DESC, day DESC
            ) AS rn
        FROM nasa_asteroids.asteroids
    )
    WHERE rn = 1
),
bridge_neows AS (
    SELECT
        identifier_value AS neows_id,
        MAX(asteroid_key) AS asteroid_key
    FROM nasa_asteroids.bridge_asteroid_identifier
    WHERE source_system = 'neows'
      AND identifier_name = 'id'
    GROUP BY identifier_value
),
latest_sentry_year_range AS (
    SELECT
        sentry_id,
        impact_year_range
    FROM (
        SELECT
            s.sentry_id,
            s.impact_year_range,
            ROW_NUMBER() OVER (
                PARTITION BY s.sentry_id
                ORDER BY s.snapshot_key DESC, s.snapshot_time DESC, s.run_id DESC
            ) AS rn
        FROM nasa_asteroids.fact_sentry_risk_snapshot s
        INNER JOIN nasa_asteroids.v_sentry_object_lifecycle l
            ON s.sentry_id = l.sentry_id
           AND s.snapshot_key = l.last_snapshot_key
    )
    WHERE rn = 1
),
sentry_by_asteroid_key AS (
    SELECT
        l.asteroid_key,
        COUNT(DISTINCT l.sentry_id) AS sentry_identifier_count,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) > 1 THEN TRUE
            ELSE FALSE
        END AS is_sentry_ambiguous,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.sentry_id)
            ELSE NULL
        END AS sentry_id,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.latest_impact_probability)
            ELSE NULL
        END AS sentry_impact_probability,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.latest_palermo_scale_max)
            ELSE NULL
        END AS sentry_palermo_scale_max,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.latest_torino_scale_max)
            ELSE NULL
        END AS sentry_torino_scale_max,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.latest_potential_impacts_count)
            ELSE NULL
        END AS sentry_potential_impacts_count,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(yr.impact_year_range)
            ELSE NULL
        END AS sentry_impact_year_range,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.is_currently_active)
            ELSE NULL
        END AS sentry_is_currently_active,
        MAX(l.all_time_max_impact_probability) AS sentry_all_time_max_impact_probability,
        MAX(l.all_time_max_palermo_scale_max) AS sentry_all_time_max_palermo_scale_max,
        MAX(l.all_time_max_torino_scale_max) AS sentry_all_time_max_torino_scale_max
    FROM nasa_asteroids.v_sentry_object_lifecycle l
    LEFT JOIN latest_sentry_year_range yr
        ON yr.sentry_id = l.sentry_id
    WHERE l.asteroid_key IS NOT NULL
    GROUP BY l.asteroid_key
),
bridge_sbdb_pivot AS (
    SELECT
        asteroid_key,
        MAX(identifier_value) AS spkid
    FROM nasa_asteroids.bridge_asteroid_identifier
    WHERE source_system = 'sbdb'
      AND identifier_name = 'spkid'
      AND is_primary_pivot = TRUE
    GROUP BY asteroid_key
)
SELECT
    n.closest_approach_date,
    n.neows_id,
    n.neo_name,
    n.miss_distance_km,
    (n.miss_distance_km / 384400.0) AS miss_distance_lunar,
    n.is_neo_hazardous,
    bn.asteroid_key,
    CASE
        WHEN s.asteroid_key IS NOT NULL AND s.sentry_identifier_count > 0 THEN TRUE
        ELSE FALSE
    END AS is_sentry_monitored,
    COALESCE(s.is_sentry_ambiguous, FALSE) AS is_sentry_ambiguous,
    COALESCE(s.sentry_identifier_count, 0) AS sentry_identifier_count,
    s.sentry_id,
    s.sentry_impact_probability,
    s.sentry_palermo_scale_max,
    s.sentry_torino_scale_max,
    s.sentry_potential_impacts_count,
    s.sentry_impact_year_range,
    s.sentry_is_currently_active,
    s.sentry_all_time_max_impact_probability,
    s.sentry_all_time_max_palermo_scale_max,
    s.sentry_all_time_max_torino_scale_max,
    sbdb.spkid AS sbdb_spkid,
    sbdb.designation AS sbdb_designation,
    sbdb.fullname AS sbdb_fullname,
    sbdb.orbit_class_name AS sbdb_orbit_class_name,
    sbdb.is_pha AS sbdb_is_pha,
    sbdb.earth_moid_au AS sbdb_earth_moid_au,
    sbdb.condition_code AS sbdb_condition_code,
    sbdb.data_arc_days AS sbdb_data_arc_days,
    sbdb.n_obs_used AS sbdb_n_obs_used,
    COALESCE(sbdb.astrometric_data_quality_tier, 'UNREPORTED') AS sbdb_astrometric_quality_tier
FROM deduped_neows n
LEFT JOIN bridge_neows bn ON bn.neows_id = n.neows_id
LEFT JOIN sentry_by_asteroid_key s ON s.asteroid_key = bn.asteroid_key
LEFT JOIN bridge_sbdb_pivot b_sbdb ON b_sbdb.asteroid_key = bn.asteroid_key
LEFT JOIN nasa_asteroids.v_sbdb_characterization_profile sbdb ON sbdb.spkid = b_sbdb.spkid;

-- ----------------------------------------------------------------------------
-- 8. Analytical View 3: Asteroid Cross-Source Profile
-- ----------------------------------------------------------------------------
-- Purpose: Canonical entity dossier uniting NeoWs, SBDB, and Sentry risk data.
-- Grain: (asteroid_key)
CREATE OR REPLACE VIEW nasa_asteroids.v_asteroid_cross_source_profile AS
WITH base_entities AS (
    SELECT DISTINCT asteroid_key
    FROM nasa_asteroids.bridge_asteroid_identifier
),
canonical_sbdb AS (
    SELECT
        b.asteroid_key,
        b.identifier_value AS canonical_spkid,
        sbdb.designation AS canonical_designation,
        sbdb.fullname AS canonical_fullname
    FROM nasa_asteroids.bridge_asteroid_identifier b
    LEFT JOIN nasa_asteroids.v_sbdb_characterization_profile sbdb
        ON sbdb.spkid = b.identifier_value
    WHERE b.source_system = 'sbdb'
      AND b.identifier_name = 'spkid'
      AND b.is_primary_pivot = TRUE
),
neows_summary AS (
    SELECT
        b.asteroid_key,
        COUNT(DISTINCT n.closest_approach_date || ':' || n.id) AS neows_total_approaches_recorded,
        MIN(n.closest_approach_date) AS neows_earliest_approach_date,
        MAX(n.closest_approach_date) AS neows_latest_approach_date,
        MIN(n.miss_distance_km) AS neows_min_miss_distance_km,
        MAX(CASE WHEN n.hazardous = TRUE THEN 1 ELSE 0 END) = 1 AS is_classified_hazardous_neows
    FROM nasa_asteroids.asteroids n
    INNER JOIN nasa_asteroids.bridge_asteroid_identifier b
        ON b.source_system = 'neows'
       AND b.identifier_name = 'id'
       AND b.identifier_value = n.id
    GROUP BY b.asteroid_key
),
latest_sentry_year_range AS (
    SELECT
        sentry_id,
        impact_year_range
    FROM (
        SELECT
            s.sentry_id,
            s.impact_year_range,
            ROW_NUMBER() OVER (
                PARTITION BY s.sentry_id
                ORDER BY s.snapshot_key DESC, s.snapshot_time DESC, s.run_id DESC
            ) AS rn
        FROM nasa_asteroids.fact_sentry_risk_snapshot s
        INNER JOIN nasa_asteroids.v_sentry_object_lifecycle l
            ON s.sentry_id = l.sentry_id
           AND s.snapshot_key = l.last_snapshot_key
    )
    WHERE rn = 1
),
sentry_by_asteroid_key AS (
    SELECT
        l.asteroid_key,
        COUNT(DISTINCT l.sentry_id) AS sentry_identifier_count,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) > 1 THEN TRUE
            ELSE FALSE
        END AS is_sentry_ambiguous,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.sentry_id)
            ELSE NULL
        END AS sentry_id,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.latest_impact_probability)
            ELSE NULL
        END AS sentry_latest_impact_probability,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.latest_palermo_scale_max)
            ELSE NULL
        END AS sentry_latest_palermo_scale_max,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.latest_torino_scale_max)
            ELSE NULL
        END AS sentry_latest_torino_scale_max,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.latest_potential_impacts_count)
            ELSE NULL
        END AS sentry_potential_impacts_count,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(yr.impact_year_range)
            ELSE NULL
        END AS sentry_impact_year_range,
        CASE
            WHEN COUNT(DISTINCT l.sentry_id) = 1 THEN MAX(l.is_currently_active)
            ELSE NULL
        END AS sentry_is_currently_active,
        MAX(l.total_snapshots_observed) AS sentry_total_snapshots_observed,
        MAX(l.all_time_max_impact_probability) AS sentry_all_time_max_impact_probability,
        MAX(l.all_time_max_palermo_scale_max) AS sentry_all_time_max_palermo_scale_max,
        MAX(l.all_time_max_torino_scale_max) AS sentry_all_time_max_torino_scale_max,
        MAX(CASE WHEN l.is_crosswalk_ambiguous = TRUE THEN 1 ELSE 0 END) = 1 AS is_crosswalk_ambiguous
    FROM nasa_asteroids.v_sentry_object_lifecycle l
    LEFT JOIN latest_sentry_year_range yr
        ON yr.sentry_id = l.sentry_id
    WHERE l.asteroid_key IS NOT NULL
    GROUP BY l.asteroid_key
)
SELECT
    e.asteroid_key,
    cs.canonical_spkid,
    cs.canonical_designation,
    cs.canonical_fullname,
    CASE WHEN ns.asteroid_key IS NOT NULL THEN TRUE ELSE FALSE END AS has_neows_telemetry,
    CASE WHEN sbdb.spkid IS NOT NULL THEN TRUE ELSE FALSE END AS has_sbdb_characterization,
    CASE WHEN s.asteroid_key IS NOT NULL AND s.sentry_identifier_count > 0 THEN TRUE ELSE FALSE END AS has_sentry_monitoring,
    ns.neows_total_approaches_recorded,
    ns.neows_earliest_approach_date,
    ns.neows_latest_approach_date,
    ns.neows_min_miss_distance_km,
    COALESCE(ns.is_classified_hazardous_neows, FALSE) AS is_classified_hazardous_neows,
    sbdb.orbit_class_name AS sbdb_orbit_class_name,
    sbdb.is_pha AS sbdb_is_pha,
    sbdb.earth_moid_au AS sbdb_earth_moid_au,
    sbdb.condition_code AS sbdb_condition_code,
    sbdb.data_arc_days AS sbdb_data_arc_days,
    sbdb.estimated_diameter_km AS sbdb_estimated_diameter_km,
    sbdb.absolute_magnitude AS sbdb_absolute_magnitude,
    COALESCE(sbdb.astrometric_data_quality_tier, 'UNREPORTED') AS sbdb_astrometric_quality_tier,
    COALESCE(s.is_sentry_ambiguous, FALSE) AS is_sentry_ambiguous,
    COALESCE(s.sentry_identifier_count, 0) AS sentry_identifier_count,
    s.sentry_id,
    s.sentry_is_currently_active,
    s.sentry_total_snapshots_observed,
    s.sentry_latest_impact_probability,
    s.sentry_all_time_max_impact_probability,
    s.sentry_latest_palermo_scale_max,
    s.sentry_all_time_max_palermo_scale_max,
    s.sentry_latest_torino_scale_max,
    s.sentry_all_time_max_torino_scale_max,
    s.sentry_potential_impacts_count,
    s.sentry_impact_year_range,
    COALESCE(s.is_crosswalk_ambiguous, FALSE) AS is_crosswalk_ambiguous
FROM base_entities e
LEFT JOIN canonical_sbdb cs ON cs.asteroid_key = e.asteroid_key
LEFT JOIN nasa_asteroids.v_sbdb_characterization_profile sbdb ON sbdb.spkid = cs.canonical_spkid
LEFT JOIN neows_summary ns ON ns.asteroid_key = e.asteroid_key
LEFT JOIN sentry_by_asteroid_key s ON s.asteroid_key = e.asteroid_key;

-- ----------------------------------------------------------------------------
-- 9. Analytical View 4: Entity Resolution & Coverage Audit
-- ----------------------------------------------------------------------------
-- Purpose: Quality control audit quantifying resolution, ambiguity, and catalog completeness.
-- Grain: (source_system, match_state, match_rule)
CREATE OR REPLACE VIEW nasa_asteroids.v_crosswalk_coverage_audit AS
WITH latest_run AS (
    SELECT resolution_run_id
    FROM (
        SELECT
            resolution_run_id,
            ROW_NUMBER() OVER (
                ORDER BY resolved_at DESC, resolution_run_id DESC
            ) AS rn
        FROM nasa_asteroids.fact_entity_resolution
    )
    WHERE rn = 1
),
current_population AS (
    SELECT f.*
    FROM nasa_asteroids.fact_entity_resolution f
    INNER JOIN latest_run lr
        ON f.resolution_run_id = lr.resolution_run_id
),
system_totals AS (
    SELECT
        source_system,
        COUNT(*) AS total_evaluations
    FROM current_population
    GROUP BY source_system
),
grouped_stats AS (
    SELECT
        p.source_system,
        p.match_state,
        p.match_rule,
        COUNT(*) AS evaluation_count,
        COUNT(DISTINCT p.assigned_asteroid_key) AS resolved_to_canonical_key_count,
        MAX(p.resolution_run_id) AS resolution_run_id,
        MAX(p.resolved_at) AS resolved_at
    FROM current_population p
    GROUP BY p.source_system, p.match_state, p.match_rule
)
SELECT
    g.source_system,
    g.match_state,
    g.match_rule,
    g.evaluation_count,
    t.total_evaluations AS total_source_system_evaluations,
    ROUND(CAST(g.evaluation_count AS DOUBLE) / CAST(t.total_evaluations AS DOUBLE) * 100.0, 4) AS pct_of_source_system,
    g.resolved_to_canonical_key_count,
    g.resolution_run_id,
    g.resolved_at
FROM grouped_stats g
INNER JOIN system_totals t
    ON t.source_system = g.source_system;
