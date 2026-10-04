-- ============================================================================
-- NASA Asteroid Intelligence Platform — Athena Analytical Queries
-- ============================================================================
--
-- COST & PERFORMANCE OPTIMIZATION NOTICE:
-- 1. Partition Pruning: The table `nasa_asteroids.asteroids` is partitioned
--    by `year`, `month`, and `day` corresponding to S3 storage layout:
--    s3://nasa-asteroid-intelligence/processed/year=YYYY/month=MM/day=DD/
-- 2. To avoid full table scans and minimize S3 data read costs in Athena,
--    always include `year`, `month`, and/or `day` predicates in the WHERE clause.
-- 3. Athena Cost Guard: A 10 MB per-query scan limit is enforced at the
--    Workgroup level. Using partition predicates ensures queries stay well
--    within this limit as historical data grows.
-- 4. Parquet Columnar Pruning: Athena only scans columns referenced in SELECT,
--    WHERE, and GROUP BY clauses. Avoid `SELECT *`.
-- ============================================================================


-- ============================================================================
-- PART 1: COST-OPTIMIZED PARTITION-PRUNED QUERIES (RECOMMENDED FOR PRODUCTION)
-- ============================================================================

-- 1.1 Single-Run / Daily Ingestion Query (Scans exactly ONE S3 partition)
-- Prunes all other partitions; reads only the specified day's Parquet file.
SELECT
    id,
    name,
    closest_approach_date,
    miss_distance_km,
    hazardous
FROM asteroids
WHERE year = '2026'
  AND month = '09'
  AND day = '23'
ORDER BY miss_distance_km ASC;

-- 1.2 Monthly Hazardous Asteroid Summary (Prunes to a single month's partitions)
-- Scans only `year=2026/month=09/*` prefixes instead of full table history.
SELECT
    hazardous,
    COUNT(*) AS asteroid_count,
    ROUND(MIN(miss_distance_km), 2) AS min_miss_distance_km,
    ROUND(AVG(miss_distance_km), 2) AS avg_miss_distance_km,
    ROUND(MAX(miss_distance_km), 2) AS max_miss_distance_km
FROM asteroids
WHERE year = '2026'
  AND month = '09'
GROUP BY hazardous;

-- 1.3 Date-Range Partition Pruning (7-day window)
-- Restricts Athena S3 scans to a specific 7-day ingestion bracket.
SELECT
    id,
    name,
    closest_approach_date,
    miss_distance_km
FROM asteroids
WHERE year = '2026'
  AND month = '09'
  AND day BETWEEN '17' AND '24'
  AND hazardous = true
ORDER BY miss_distance_km ASC
LIMIT 10;


-- ============================================================================
-- PART 2: FULL-DATASET ANALYTICS (GLOBAL SCANS ACROSS ALL HISTORICAL PARTITIONS)
-- ============================================================================

-- 1. Count total asteroids
SELECT COUNT(*) AS total_asteroids
FROM asteroids;

-- 2. Count potentially hazardous asteroids
SELECT COUNT(*) AS hazardous_asteroids
FROM asteroids
WHERE hazardous = true;

-- 3. Find the 10 closest asteroids
SELECT
    id,
    name,
    miss_distance_km
FROM asteroids
ORDER BY miss_distance_km ASC
LIMIT 10;

-- 4. Calculate average miss distance
SELECT
    AVG(miss_distance_km) AS average_miss_distance_km
FROM asteroids;

-- 5. Count asteroids by hazardous status
SELECT
    hazardous,
    COUNT(*) AS asteroid_count
FROM asteroids
GROUP BY hazardous;

-- 6. Find the 10 closest potentially hazardous asteroids
SELECT
    id,
    name,
    miss_distance_km
FROM asteroids
WHERE hazardous = true
ORDER BY miss_distance_km ASC
LIMIT 10;

-- 7. Count asteroid approaches by date
SELECT
    closest_approach_date,
    COUNT(*) AS asteroid_count
FROM asteroids
GROUP BY closest_approach_date
ORDER BY closest_approach_date;

-- 8. Find the 10 farthest asteroids
SELECT
    id,
    name,
    miss_distance_km
FROM asteroids
ORDER BY miss_distance_km DESC
LIMIT 10;

-- 9. Find the single closest asteroid
SELECT
    id,
    name,
    closest_approach_date,
    miss_distance_km,
    hazardous
FROM asteroids
ORDER BY miss_distance_km ASC
LIMIT 1;

-- 10. Find potentially hazardous asteroids more than 10 million km away
SELECT
    id,
    name,
    miss_distance_km
FROM asteroids
WHERE hazardous = true
  AND miss_distance_km > 10000000
ORDER BY miss_distance_km ASC;