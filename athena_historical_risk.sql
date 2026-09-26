-- ============================================================================
-- NASA Planetary Defense Risk Intelligence Platform — Phase 7 Historical Sentry
-- AWS Athena / Trino DDL: Sentry Snapshot Coverage & Risk Metric History
-- ============================================================================
-- Architecture: Serverless Lakehouse (S3 + Athena SQL Views)
-- Grain:
--   v_sentry_snapshot_coverage:     (snapshot_key)
--   v_sentry_risk_metric_history:    (snapshot_key, sentry_id)
-- Scientific Safety: Strictly linear arithmetic deltas; no Palermo percentage
--                   change; zero composite danger scores; zero causal claims.
-- ============================================================================

CREATE DATABASE IF NOT EXISTS nasa_asteroids;

-- ----------------------------------------------------------------------------
-- 1. External Table DDL: Sentry Risk Snapshots (Phase 4 Ingestion)
-- ----------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS nasa_asteroids.fact_sentry_risk_snapshot (
    snapshot_key STRING,
    run_id STRING,
    snapshot_time STRING,
    sentry_id STRING,
    designation STRING,
    fullname STRING,
    absolute_magnitude DOUBLE,
    estimated_diameter_km DOUBLE,
    impact_probability DOUBLE,
    potential_impacts_count BIGINT,
    palermo_scale_cum DOUBLE,
    palermo_scale_max DOUBLE,
    torino_scale_max BIGINT,
    v_infinity_km_s DOUBLE,
    impact_year_range STRING,
    last_obs_date STRING,
    last_obs_jd DOUBLE
)
PARTITIONED BY (
    year STRING,
    month STRING,
    day STRING
)
STORED AS PARQUET
LOCATION 's3://nasa-asteroid-intelligence/processed/sentry/risk_snapshot/'
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
    'storage.location.template'='s3://nasa-asteroid-intelligence/processed/sentry/risk_snapshot/year=${year}/month=${month}/day=${day}/'
);

-- ----------------------------------------------------------------------------
-- 2. External Table DDL: Canonical Asteroid Crosswalk Bridge (Phase 6 Resolution)
-- ----------------------------------------------------------------------------
CREATE EXTERNAL TABLE IF NOT EXISTS nasa_asteroids.bridge_asteroid_identifier (
    asteroid_key STRING,
    source_system STRING,
    identifier_name STRING,
    identifier_value STRING,
    is_primary_pivot BOOLEAN,
    created_at STRING,
    updated_at STRING
)
PARTITIONED BY (
    year STRING,
    month STRING,
    day STRING
)
STORED AS PARQUET
LOCATION 's3://nasa-asteroid-intelligence/reference/asteroid_crosswalk/bridge_asteroid_identifier/'
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
    'storage.location.template'='s3://nasa-asteroid-intelligence/reference/asteroid_crosswalk/bridge_asteroid_identifier/year=${year}/month=${month}/day=${day}/'
);

-- ----------------------------------------------------------------------------
-- 3. Analytical View 1: Sentry Snapshot Coverage (Phase 7 Locked Contract)
-- ----------------------------------------------------------------------------
-- Purpose: Authoritative fact-derived coverage abstraction layer.
-- Grain: (snapshot_key)
-- Note: Derived strictly from retained fact rows; uncaptured dates are inferred
--       through sequence and calendar gap analysis, not direct storage probing.
CREATE OR REPLACE VIEW nasa_asteroids.v_sentry_snapshot_coverage AS
SELECT
    snapshot_key,
    TRUE AS is_captured,
    COUNT(*) AS row_count,
    MIN(snapshot_time) AS first_snapshot_time,
    MAX(snapshot_time) AS last_snapshot_time,
    DENSE_RANK() OVER (ORDER BY snapshot_key ASC) AS snapshot_seq
FROM nasa_asteroids.fact_sentry_risk_snapshot
GROUP BY snapshot_key;

-- ----------------------------------------------------------------------------
-- 4. Analytical View 2: Sentry Risk Metric History (Phase 7 Locked Contract)
-- ----------------------------------------------------------------------------
-- Purpose: Snapshot-to-snapshot metric progression, observation arc updates,
--          and deterministic coverage gap detection.
-- Grain: (snapshot_key, sentry_id)
-- Temporal Partition: sentry_id ordered by snapshot_key ASC.
-- Crosswalk: LEFT JOIN against bridge_asteroid_identifier (unresolved preserved).
CREATE OR REPLACE VIEW nasa_asteroids.v_sentry_risk_metric_history AS
WITH bridge_sentry AS (
    SELECT DISTINCT
        identifier_value AS sentry_id,
        asteroid_key
    FROM nasa_asteroids.bridge_asteroid_identifier
    WHERE source_system = 'sentry'
      AND identifier_name = 'sentry_id'
),
sentry_with_coverage AS (
    SELECT
        s.snapshot_key,
        s.run_id,
        s.snapshot_time,
        s.sentry_id,
        s.designation,
        s.fullname,
        s.impact_probability,
        s.palermo_scale_cum,
        s.palermo_scale_max,
        s.torino_scale_max,
        s.potential_impacts_count,
        s.v_infinity_km_s,
        s.last_obs_date,
        s.last_obs_jd,
        s.estimated_diameter_km,
        s.absolute_magnitude,
        s.impact_year_range,
        cov.snapshot_seq AS seq_curr,
        b.asteroid_key
    FROM nasa_asteroids.fact_sentry_risk_snapshot s
    INNER JOIN nasa_asteroids.v_sentry_snapshot_coverage cov
        ON cov.snapshot_key = s.snapshot_key
    LEFT JOIN bridge_sentry b
        ON b.sentry_id = s.sentry_id
),
windowed AS (
    SELECT
        snapshot_key,
        run_id,
        snapshot_time,
        sentry_id,
        designation,
        fullname,
        asteroid_key,
        impact_probability,
        palermo_scale_cum,
        palermo_scale_max,
        torino_scale_max,
        potential_impacts_count,
        v_infinity_km_s,
        last_obs_date,
        last_obs_jd,
        estimated_diameter_km,
        absolute_magnitude,
        impact_year_range,
        seq_curr,
        LAG(seq_curr) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS seq_prev,
        LAG(snapshot_key) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS prev_snapshot_key,
        LAG(impact_probability) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS prev_impact_probability,
        LAG(palermo_scale_cum) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS prev_palermo_scale_cum,
        LAG(palermo_scale_max) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS prev_palermo_scale_max,
        LAG(torino_scale_max) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS prev_torino_scale_max,
        LAG(potential_impacts_count) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS prev_potential_impacts_count,
        LAG(v_infinity_km_s) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS prev_v_infinity_km_s,
        LAG(last_obs_date) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS prev_last_obs_date,
        LAG(estimated_diameter_km) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS prev_estimated_diameter_km,
        LAG(absolute_magnitude) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS prev_absolute_magnitude
    FROM sentry_with_coverage
)
SELECT
    snapshot_key,
    snapshot_time,
    sentry_id,
    designation,
    fullname,
    asteroid_key,
    -- Current mandatory metrics
    impact_probability,
    palermo_scale_cum,
    palermo_scale_max,
    torino_scale_max,
    potential_impacts_count,
    v_infinity_km_s,
    last_obs_date,
    -- Current optional physical metrics (exposed to support downstream lifecycle derivations)
    estimated_diameter_km,
    absolute_magnitude,
    -- Chronological prior observation metrics (LAG)
    prev_snapshot_key,
    prev_impact_probability,
    prev_palermo_scale_cum,
    prev_palermo_scale_max,
    prev_torino_scale_max,
    prev_potential_impacts_count,
    prev_v_infinity_km_s,
    prev_last_obs_date,
    prev_estimated_diameter_km,
    prev_absolute_magnitude,
    -- Arithmetic deltas (strictly linear; zero Palermo percentage calculations)
    (impact_probability - prev_impact_probability) AS delta_impact_probability,
    (palermo_scale_max - prev_palermo_scale_max) AS delta_palermo_scale_max,
    (palermo_scale_cum - prev_palermo_scale_cum) AS delta_palermo_scale_cum,
    (torino_scale_max - prev_torino_scale_max) AS delta_torino_scale_max,
    (potential_impacts_count - prev_potential_impacts_count) AS delta_potential_impacts_count,
    (v_infinity_km_s - prev_v_infinity_km_s) AS delta_v_infinity_km_s,
    CASE
        WHEN prev_last_obs_date IS NULL OR last_obs_date IS NULL THEN NULL
        ELSE DATE_DIFF('day', CAST(prev_last_obs_date AS DATE), CAST(last_obs_date AS DATE))
    END AS delta_last_obs_days,
    -- Analytical and lifecycle flags
    CASE
        WHEN prev_snapshot_key IS NULL THEN TRUE
        ELSE FALSE
    END AS is_first_snapshot,
    CASE
        WHEN prev_last_obs_date IS NULL THEN FALSE
        WHEN last_obs_date > prev_last_obs_date THEN TRUE
        ELSE FALSE
    END AS has_new_observations,
    CASE
        WHEN prev_snapshot_key IS NULL THEN FALSE
        WHEN impact_probability != prev_impact_probability
          OR palermo_scale_max != prev_palermo_scale_max
          OR torino_scale_max != prev_torino_scale_max
          OR potential_impacts_count != prev_potential_impacts_count THEN TRUE
        ELSE FALSE
    END AS is_metric_changed,
    -- Deterministic coverage gap flag
    CASE
        WHEN prev_snapshot_key IS NULL THEN FALSE
        WHEN DATE_DIFF('day', CAST(prev_snapshot_key AS DATE), CAST(snapshot_key AS DATE)) = 1 THEN FALSE
        WHEN (seq_curr - seq_prev) = DATE_DIFF('day', CAST(prev_snapshot_key AS DATE), CAST(snapshot_key AS DATE)) THEN FALSE
        WHEN (seq_curr - seq_prev) < DATE_DIFF('day', CAST(prev_snapshot_key AS DATE), CAST(snapshot_key AS DATE)) THEN TRUE
        ELSE FALSE
    END AS coverage_gap_flag
FROM windowed;

-- ----------------------------------------------------------------------------
-- 5. Analytical View 3: Sentry Presence History (Phase 7 Locked Contract)
-- ----------------------------------------------------------------------------
-- Purpose: Object presence, entry, exit, and persistence tracking across
--          the authoritative platform snapshot sequence.
-- Grain: (snapshot_key, sentry_id)
-- Presence States:
--   - 'NEW_ENTRY': Initial appearance in the platform archive.
--   - 'PERSISTENT': Consecutively captured in the previous calendar day.
--   - 'RE_ENTRY': Re-appearance after verified absence across fully captured days.
--   - 'EXIT_AFTER_THIS_SNAPSHOT': Verified departure on immediate next contiguous day.
--   - NULL: Observation resumes across an uncaptured platform date (coverage gap).
CREATE OR REPLACE VIEW nasa_asteroids.v_sentry_presence_history AS
WITH coverage_timeline AS (
    SELECT
        snapshot_key,
        snapshot_seq,
        LEAD(snapshot_key) OVER (ORDER BY snapshot_seq ASC) AS global_next_snapshot_key
    FROM nasa_asteroids.v_sentry_snapshot_coverage
),
sentry_with_coverage AS (
    SELECT
        s.snapshot_key,
        s.snapshot_time,
        s.sentry_id,
        s.designation,
        s.fullname,
        cov.snapshot_seq AS seq_curr,
        cov.global_next_snapshot_key,
        CASE
            WHEN cov.global_next_snapshot_key IS NULL THEN NULL
            ELSE DATE_DIFF('day', CAST(s.snapshot_key AS DATE), CAST(cov.global_next_snapshot_key AS DATE))
        END AS global_next_days_diff
    FROM nasa_asteroids.fact_sentry_risk_snapshot s
    INNER JOIN coverage_timeline cov
        ON cov.snapshot_key = s.snapshot_key
),
windowed AS (
    SELECT
        snapshot_key,
        snapshot_time,
        sentry_id,
        designation,
        fullname,
        seq_curr,
        global_next_snapshot_key,
        global_next_days_diff,
        LAG(seq_curr) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS seq_prev,
        LAG(snapshot_key) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS prev_snapshot_key,
        LEAD(seq_curr) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS seq_next,
        LEAD(snapshot_key) OVER (
            PARTITION BY sentry_id
            ORDER BY snapshot_key ASC
        ) AS next_snapshot_key
    FROM sentry_with_coverage
),
classified AS (
    SELECT
        snapshot_key,
        snapshot_time,
        sentry_id,
        designation,
        fullname,
        seq_curr,
        seq_prev,
        prev_snapshot_key,
        seq_next,
        next_snapshot_key,
        global_next_snapshot_key,
        global_next_days_diff,
        CASE
            WHEN prev_snapshot_key IS NULL THEN NULL
            ELSE DATE_DIFF('day', CAST(prev_snapshot_key AS DATE), CAST(snapshot_key AS DATE))
        END AS days_since_prev,
        CASE
            WHEN prev_snapshot_key IS NULL THEN FALSE
            WHEN DATE_DIFF('day', CAST(prev_snapshot_key AS DATE), CAST(snapshot_key AS DATE)) = 1 THEN FALSE
            WHEN (seq_curr - seq_prev) = DATE_DIFF('day', CAST(prev_snapshot_key AS DATE), CAST(snapshot_key AS DATE)) THEN FALSE
            WHEN (seq_curr - seq_prev) < DATE_DIFF('day', CAST(prev_snapshot_key AS DATE), CAST(snapshot_key AS DATE)) THEN TRUE
            ELSE FALSE
        END AS coverage_gap_flag
    FROM windowed
),
presence_computed AS (
    SELECT
        snapshot_key,
        snapshot_time,
        sentry_id,
        designation,
        fullname,
        seq_curr,
        seq_prev,
        prev_snapshot_key,
        seq_next,
        next_snapshot_key,
        global_next_snapshot_key,
        global_next_days_diff,
        days_since_prev,
        coverage_gap_flag,
        CASE
            -- 1. Uncaptured-Date Gap: Observation resumes after missing platform coverage
            WHEN prev_snapshot_key IS NOT NULL AND (seq_curr - seq_prev) < days_since_prev THEN NULL
            -- 2. Validated Exit: Next global day was captured, contiguous, and object is absent
            WHEN global_next_snapshot_key IS NOT NULL
                 AND global_next_days_diff = 1
                 AND (seq_next IS NULL OR seq_next > seq_curr + 1)
                 THEN 'EXIT_AFTER_THIS_SNAPSHOT'
            -- 3. Initial Appearance
            WHEN prev_snapshot_key IS NULL THEN 'NEW_ENTRY'
            -- 4. Consecutive Daily Persistence
            WHEN (seq_curr - seq_prev) = 1 AND days_since_prev = 1 THEN 'PERSISTENT'
            -- 5. Validated Re-Entry (Source absence across fully captured calendar days)
            WHEN (seq_curr - seq_prev) = days_since_prev AND (seq_curr - seq_prev) > 1 THEN 'RE_ENTRY'
            ELSE NULL
        END AS presence_state
    FROM classified
)
SELECT
    snapshot_key,
    snapshot_time,
    sentry_id,
    designation,
    fullname,
    seq_curr,
    seq_prev,
    prev_snapshot_key,
    seq_next,
    next_snapshot_key,
    global_next_snapshot_key,
    global_next_days_diff,
    days_since_prev,
    coverage_gap_flag,
    presence_state,
    CASE
        WHEN presence_state = 'EXIT_AFTER_THIS_SNAPSHOT' THEN TRUE
        ELSE FALSE
    END AS is_exit_after_this_snapshot
FROM presence_computed;

-- ----------------------------------------------------------------------------
-- 6. Analytical View 4: Sentry Object Lifecycle (Phase 7 Locked Contract)
-- ----------------------------------------------------------------------------
-- Purpose: Longitudinal object lifecycle, multi-snapshot metric progression,
--          all-time maximum reported metrics, canonical crosswalk resolution,
--          and tri-state operational activity tracking.
-- Grain: (sentry_id)
CREATE OR REPLACE VIEW nasa_asteroids.v_sentry_object_lifecycle AS
WITH latest_global AS (
    SELECT
        snapshot_key AS latest_global_snapshot_key,
        last_snapshot_time AS latest_global_snapshot_time
    FROM nasa_asteroids.v_sentry_snapshot_coverage
    ORDER BY snapshot_seq DESC
    LIMIT 1
),
ranked_metrics AS (
    SELECT
        m.snapshot_key,
        m.snapshot_time,
        m.sentry_id,
        m.designation,
        m.fullname,
        m.asteroid_key,
        m.impact_probability,
        m.palermo_scale_cum,
        m.palermo_scale_max,
        m.torino_scale_max,
        m.potential_impacts_count,
        m.v_infinity_km_s,
        m.last_obs_date,
        m.estimated_diameter_km,
        m.absolute_magnitude,
        ROW_NUMBER() OVER (
            PARTITION BY m.sentry_id
            ORDER BY m.snapshot_key ASC
        ) AS rn_asc,
        ROW_NUMBER() OVER (
            PARTITION BY m.sentry_id
            ORDER BY m.snapshot_key DESC
        ) AS rn_desc,
        ROW_NUMBER() OVER (
            PARTITION BY m.sentry_id
            ORDER BY CASE WHEN m.estimated_diameter_km IS NOT NULL THEN 0 ELSE 1 END, m.snapshot_key ASC
        ) AS rn_diam_asc,
        ROW_NUMBER() OVER (
            PARTITION BY m.sentry_id
            ORDER BY CASE WHEN m.estimated_diameter_km IS NOT NULL THEN 0 ELSE 1 END, m.snapshot_key DESC
        ) AS rn_diam_desc,
        ROW_NUMBER() OVER (
            PARTITION BY m.sentry_id
            ORDER BY CASE WHEN m.absolute_magnitude IS NOT NULL THEN 0 ELSE 1 END, m.snapshot_key ASC
        ) AS rn_mag_asc,
        ROW_NUMBER() OVER (
            PARTITION BY m.sentry_id
            ORDER BY CASE WHEN m.absolute_magnitude IS NOT NULL THEN 0 ELSE 1 END, m.snapshot_key DESC
        ) AS rn_mag_desc
    FROM nasa_asteroids.v_sentry_risk_metric_history m
),
aggregated AS (
    SELECT
        r.sentry_id,
        MAX(CASE WHEN r.rn_desc = 1 THEN r.designation END) AS designation,
        MAX(CASE WHEN r.rn_desc = 1 THEN r.fullname END) AS fullname,
        -- Canonical Crosswalk Identity Semantics
        CASE
            WHEN COUNT(DISTINCT r.asteroid_key) = 1 THEN MAX(r.asteroid_key)
            ELSE NULL
        END AS asteroid_key,
        CASE
            WHEN COUNT(DISTINCT r.asteroid_key) > 1 THEN TRUE
            ELSE FALSE
        END AS is_crosswalk_ambiguous,
        -- First & Last Snapshot Timeline
        MAX(CASE WHEN r.rn_asc = 1 THEN r.snapshot_key END) AS first_snapshot_key,
        MAX(CASE WHEN r.rn_asc = 1 THEN r.snapshot_time END) AS first_snapshot_time,
        MAX(CASE WHEN r.rn_desc = 1 THEN r.snapshot_key END) AS last_snapshot_key,
        MAX(CASE WHEN r.rn_desc = 1 THEN r.snapshot_time END) AS last_snapshot_time,
        COUNT(DISTINCT r.snapshot_key) AS total_snapshots_observed,
        DATE_DIFF(
            'day',
            CAST(MAX(CASE WHEN r.rn_asc = 1 THEN r.snapshot_key END) AS DATE),
            CAST(MAX(CASE WHEN r.rn_desc = 1 THEN r.snapshot_key END) AS DATE)
        ) + 1 AS archive_observation_span_days,
        -- Mandatory Initial & Latest Risk Metrics
        MAX(CASE WHEN r.rn_asc = 1 THEN r.impact_probability END) AS initial_impact_probability,
        MAX(CASE WHEN r.rn_desc = 1 THEN r.impact_probability END) AS latest_impact_probability,
        MAX(CASE WHEN r.rn_asc = 1 THEN r.palermo_scale_max END) AS initial_palermo_scale_max,
        MAX(CASE WHEN r.rn_desc = 1 THEN r.palermo_scale_max END) AS latest_palermo_scale_max,
        MAX(CASE WHEN r.rn_asc = 1 THEN r.palermo_scale_cum END) AS initial_palermo_scale_cum,
        MAX(CASE WHEN r.rn_desc = 1 THEN r.palermo_scale_cum END) AS latest_palermo_scale_cum,
        MAX(CASE WHEN r.rn_asc = 1 THEN r.torino_scale_max END) AS initial_torino_scale_max,
        MAX(CASE WHEN r.rn_desc = 1 THEN r.torino_scale_max END) AS latest_torino_scale_max,
        MAX(CASE WHEN r.rn_asc = 1 THEN r.potential_impacts_count END) AS initial_potential_impacts_count,
        MAX(CASE WHEN r.rn_desc = 1 THEN r.potential_impacts_count END) AS latest_potential_impacts_count,
        MAX(CASE WHEN r.rn_asc = 1 THEN r.v_infinity_km_s END) AS initial_v_infinity_km_s,
        MAX(CASE WHEN r.rn_desc = 1 THEN r.v_infinity_km_s END) AS latest_v_infinity_km_s,
        MAX(CASE WHEN r.rn_asc = 1 THEN r.last_obs_date END) AS initial_last_obs_date,
        MAX(CASE WHEN r.rn_desc = 1 THEN r.last_obs_date END) AS latest_last_obs_date,
        -- Optional Initial & Latest Physical Metrics (First/Last Non-Null)
        MAX(CASE WHEN r.rn_diam_asc = 1 THEN r.estimated_diameter_km END) AS initial_estimated_diameter_km,
        MAX(CASE WHEN r.rn_diam_desc = 1 THEN r.estimated_diameter_km END) AS latest_estimated_diameter_km,
        MAX(CASE WHEN r.rn_mag_asc = 1 THEN r.absolute_magnitude END) AS initial_absolute_magnitude,
        MAX(CASE WHEN r.rn_mag_desc = 1 THEN r.absolute_magnitude END) AS latest_absolute_magnitude,
        -- All-Time Maximum Reported Metrics
        MAX(r.impact_probability) AS all_time_max_impact_probability,
        MAX(r.palermo_scale_max) AS all_time_max_palermo_scale_max,
        MAX(r.palermo_scale_cum) AS all_time_max_palermo_scale_cum,
        MAX(r.torino_scale_max) AS all_time_max_torino_scale_max,
        MAX(r.potential_impacts_count) AS all_time_max_potential_impacts_count
    FROM ranked_metrics r
    GROUP BY r.sentry_id
)
SELECT
    a.sentry_id,
    a.designation,
    a.fullname,
    a.asteroid_key,
    a.is_crosswalk_ambiguous,
    a.first_snapshot_key,
    a.first_snapshot_time,
    a.last_snapshot_key,
    a.last_snapshot_time,
    a.total_snapshots_observed,
    a.archive_observation_span_days,
    -- Operational Freshness & Tri-State Activity
    CASE
        WHEN DATE_DIFF('hour', from_iso8601_timestamp(g.latest_global_snapshot_time), NOW()) > 48 THEN NULL
        WHEN a.last_snapshot_key = g.latest_global_snapshot_key THEN TRUE
        ELSE FALSE
    END AS is_currently_active,
    a.initial_impact_probability,
    a.latest_impact_probability,
    a.initial_palermo_scale_max,
    a.latest_palermo_scale_max,
    a.initial_palermo_scale_cum,
    a.latest_palermo_scale_cum,
    a.initial_torino_scale_max,
    a.latest_torino_scale_max,
    a.initial_potential_impacts_count,
    a.latest_potential_impacts_count,
    a.initial_v_infinity_km_s,
    a.latest_v_infinity_km_s,
    a.initial_last_obs_date,
    a.latest_last_obs_date,
    a.initial_estimated_diameter_km,
    a.latest_estimated_diameter_km,
    a.initial_absolute_magnitude,
    a.latest_absolute_magnitude,
    a.all_time_max_impact_probability,
    a.all_time_max_palermo_scale_max,
    a.all_time_max_palermo_scale_cum,
    a.all_time_max_torino_scale_max,
    a.all_time_max_potential_impacts_count
FROM aggregated a
CROSS JOIN latest_global g;

