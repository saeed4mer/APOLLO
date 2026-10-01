"""Phase 9 Data Access Layer for the NASA Planetary Defense Platform.

Provides a unified, decoupled data-access abstraction between the Streamlit UI
and the lakehouse storage (Local DuckDB / Parquet and AWS Athena).

Strict Scientific Safety Guardrails:
- Prohibition of synthetic formulas or composite ranking indexes.
- Factual, non-causal reporting across historical risk snapshots.
- Strict isolation of identifier namespaces (NeoWs ID != SBDB SPK-ID != Sentry ID).
- Explicit representation of entity resolution missingness and ambiguity.
"""

from __future__ import annotations

import logging
from pathlib import Path
import re
from typing import Any

import duckdb
import pandas as pd
import pyarrow.parquet as pq

logger = logging.getLogger(__name__)

UUID5_PATTERN = re.compile(
    r"^ast_[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _parquet_relation(path: Path, columns: dict[str, str]) -> str:
    """Return a SQL relation for a Parquet file, or an empty typed relation if it is absent.

    Lets set-based queries keep one shape regardless of which lakehouse assets exist.
    """
    if path.exists():
        return "read_parquet('" + str(path).replace("\\", "/") + "')"
    casts = ", ".join(f"CAST(NULL AS {sql_type}) AS {name}" for name, sql_type in columns.items())
    return f"(SELECT {casts} WHERE FALSE)"


_SNAPSHOT_COLUMNS = {"spkid": "VARCHAR", "snapshot_key": "VARCHAR", "snapshot_time": "VARCHAR", "run_id": "VARCHAR"}
# Mode S summary fields persisted by nasa_sentry.SENTRY_RISK_SNAPSHOT_SCHEMA.
_SENTRY_ASSESSMENT_COLUMNS = {
    "designation": "VARCHAR", "fullname": "VARCHAR", "absolute_magnitude": "DOUBLE",
    "estimated_diameter_km": "DOUBLE", "impact_probability": "DOUBLE", "potential_impacts_count": "BIGINT",
    "palermo_scale_cum": "DOUBLE", "palermo_scale_max": "DOUBLE", "torino_scale_max": "BIGINT",
    "v_infinity_km_s": "DOUBLE", "impact_year_range": "VARCHAR", "last_obs_date": "VARCHAR",
    "last_obs_jd": "DOUBLE",
}
_SENTRY_COLUMNS = {
    "sentry_id": "VARCHAR", "snapshot_key": "VARCHAR", "snapshot_time": "VARCHAR", "run_id": "VARCHAR",
    **_SENTRY_ASSESSMENT_COLUMNS,
}
_BRIDGE_COLUMNS = {
    "asteroid_key": "VARCHAR", "source_system": "VARCHAR", "identifier_name": "VARCHAR",
    "identifier_value": "VARCHAR", "is_primary_pivot": "BOOLEAN",
}


def _nullable_bool(value: Any) -> bool | None:
    """Preserve source tri-state: True/False as published, None when unknown."""
    if value is None or pd.isna(value):
        return None
    return bool(value)


class LocalDuckDBDataProvider:
    """Local offline provider querying Lakehouse Parquet files via DuckDB."""

    def __init__(self, base_dir: Path | str | None = None) -> None:
        if base_dir is None:
            self.base_dir = Path(__file__).resolve().parent
        else:
            self.base_dir = Path(base_dir).resolve()

        self._asteroids_file = self.base_dir / "asteroids.parquet"
        self._bridge_file = self.base_dir / "bridge_asteroid_identifier.parquet"
        self._resolution_file = self.base_dir / "fact_entity_resolution.parquet"
        self._sbdb_object_file = self.base_dir / "fact_sbdb_object_snapshot.parquet"
        self._sbdb_orbit_file = self.base_dir / "fact_sbdb_orbit.parquet"
        self._sbdb_elements_file = self.base_dir / "fact_sbdb_orbit_element.parquet"
        self._sbdb_physical_file = self.base_dir / "fact_sbdb_physical_parameter.parquet"
        self._sentry_risk_file = self.base_dir / "fact_sentry_risk_snapshot.parquet"

    def _get_connection(self) -> duckdb.DuckDBPyConnection:
        """Create a fresh in-memory DuckDB connection."""
        conn = duckdb.connect(":memory:")
        return conn

    def get_execution_mode(self) -> str:
        return "LOCAL (DUCKDB / PARQUET LAKEHOUSE)"

    def _sbdb_latest_snapshot_sql(self) -> str:
        """SQL selecting ONE coherent SBDB snapshot (snapshot_key, run_id) per SPK-ID.

        Ingestion writes object/orbit/element/physical rows for a target in a single
        run under one (snapshot_key, run_id), so that pair identifies a coherent
        snapshot. The latest is the newest run that wrote an object or orbit row.
        Shared by the per-object profile and the world snapshot so both agree.
        """
        obj = _parquet_relation(self._sbdb_object_file, _SNAPSHOT_COLUMNS)
        orb = _parquet_relation(self._sbdb_orbit_file, _SNAPSHOT_COLUMNS)
        return f"""
            SELECT spkid, snapshot_key, run_id, snapshot_time
            FROM (
                SELECT
                    spkid, snapshot_key, run_id, snapshot_time,
                    ROW_NUMBER() OVER (
                        PARTITION BY spkid
                        ORDER BY snapshot_key DESC, snapshot_time DESC, run_id DESC
                    ) AS rn
                FROM (
                    SELECT spkid, snapshot_key, snapshot_time, run_id FROM {obj}
                    UNION ALL
                    SELECT spkid, snapshot_key, snapshot_time, run_id FROM {orb}
                )
            )
            WHERE rn = 1
        """

    def _neows_resolution_sql(self, neows_ids_sql: str) -> str:
        """SQL resolving every NeoWs ID in `neows_ids_sql` (one column: neows_id) in bulk.

        Canonical set-based form of get_resolution_state(), branch for branch: the
        latest entity-resolution audit record wins; without one, the bridge is the
        fallback (one key -> RESOLVED, several -> AMBIGUOUS). Shared by the
        watchlist (GET /asteroids) and the world snapshot so all routes agree.
        Returns: neows_id, match_state, asteroid_key, match_rule, resolved_at.
        """
        bridge = _parquet_relation(self._bridge_file, _BRIDGE_COLUMNS)
        audit = _parquet_relation(self._resolution_file, {
            "source_system": "VARCHAR", "identifier_name": "VARCHAR", "source_identifier_value": "VARCHAR",
            "match_state": "VARCHAR", "assigned_asteroid_key": "VARCHAR", "match_rule": "VARCHAR",
            "resolved_at": "VARCHAR",
        })
        return f"""
            SELECT
                ids.neows_id,
                CASE
                    WHEN NOT regexp_full_match(ids.neows_id, '[0-9]+') THEN 'INVALID'
                    WHEN a.neows_id IS NOT NULL THEN
                        CASE
                            WHEN a.match_state = 'RESOLVED' AND a.assigned_asteroid_key IS NOT NULL THEN 'RESOLVED'
                            WHEN a.match_state IN ('AMBIGUOUS', 'INVALID') THEN a.match_state
                            ELSE 'UNRESOLVED'
                        END
                    WHEN b.key_count = 1 THEN 'RESOLVED'
                    WHEN b.key_count > 1 THEN 'AMBIGUOUS'
                    ELSE 'UNRESOLVED'
                END AS match_state,
                CASE
                    WHEN NOT regexp_full_match(ids.neows_id, '[0-9]+') THEN NULL
                    WHEN a.neows_id IS NOT NULL THEN
                        CASE WHEN a.match_state = 'RESOLVED' THEN a.assigned_asteroid_key END
                    WHEN b.key_count = 1 THEN b.asteroid_key
                END AS asteroid_key,
                CASE
                    WHEN NOT regexp_full_match(ids.neows_id, '[0-9]+') THEN 'INVALID_SYNTAX'
                    WHEN a.neows_id IS NOT NULL THEN
                        CASE
                            WHEN a.match_state = 'RESOLVED' AND a.assigned_asteroid_key IS NOT NULL THEN a.match_rule
                            WHEN a.match_state IN ('AMBIGUOUS', 'INVALID') THEN a.match_rule
                            ELSE COALESCE(a.match_rule, 'NO_CROSS_SOURCE_MATCH')
                        END
                    WHEN b.key_count = 1 THEN 'BRIDGE_EXACT_NEOWS_ID'
                    WHEN b.key_count > 1 THEN 'BRIDGE_MULTIPLE_CANDIDATE_KEYS'
                    ELSE 'NO_RESOLUTION_RECORD'
                END AS match_rule,
                a.resolved_at
            FROM ({neows_ids_sql}) ids
            LEFT JOIN (
                SELECT
                    source_identifier_value AS neows_id, match_state, assigned_asteroid_key,
                    match_rule, resolved_at,
                    ROW_NUMBER() OVER (
                        PARTITION BY source_identifier_value ORDER BY resolved_at DESC
                    ) AS rn
                FROM {audit}
                WHERE source_system = 'neows' AND identifier_name = 'id'
            ) a ON a.neows_id = ids.neows_id AND a.rn = 1
            LEFT JOIN (
                SELECT
                    identifier_value AS neows_id,
                    COUNT(DISTINCT asteroid_key) AS key_count,
                    MIN(asteroid_key) AS asteroid_key
                FROM {bridge}
                WHERE source_system = 'neows' AND identifier_name = 'id'
                GROUP BY identifier_value
            ) b ON b.neows_id = ids.neows_id
        """

    def get_threat_watchlist(self) -> pd.DataFrame:
        """Produce the operational close-approach threat watchlist.

        Grain: (closest_approach_date, neows_id)
        Reflects Phase 8 v_neows_sentry_threat_watchlist analytical semantics.
        """
        if not self._asteroids_file.exists():
            return pd.DataFrame(
                columns=[
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
            )

        ast = _parquet_relation(self._asteroids_file, {})
        bridge = _parquet_relation(self._bridge_file, _BRIDGE_COLUMNS)
        sentry = _parquet_relation(self._sentry_risk_file, {
            "sentry_id": "VARCHAR", "impact_probability": "DOUBLE", "palermo_scale_max": "DOUBLE",
            "torino_scale_max": "BIGINT", "potential_impacts_count": "BIGINT", "impact_year_range": "VARCHAR",
            "snapshot_key": "VARCHAR", "snapshot_time": "VARCHAR", "run_id": "VARCHAR",
        })
        sbdb_obj = _parquet_relation(self._sbdb_object_file, {
            **_SNAPSHOT_COLUMNS, "designation": "VARCHAR", "fullname": "VARCHAR",
            "is_pha": "BOOLEAN", "orbit_class_name": "VARCHAR",
        })

        # Missing enrichment assets become empty relations, so resolution always
        # follows the canonical rules and enrichment columns are simply NULL/FALSE.
        query = f"""
        WITH deduped_neows AS (
            SELECT
                id AS neows_id,
                name,
                closest_approach_date,
                miss_distance_km,
                hazardous
            FROM (
                SELECT
                    id,
                    name,
                    closest_approach_date,
                    miss_distance_km,
                    hazardous,
                    ROW_NUMBER() OVER (
                        PARTITION BY id, closest_approach_date
                        ORDER BY miss_distance_km ASC
                    ) AS rn
                FROM {ast}
            )
            WHERE rn = 1
        ),
        resolution AS ({self._neows_resolution_sql("SELECT DISTINCT neows_id FROM deduped_neows")}),
        latest_sentry AS (
            SELECT
                sentry_id,
                impact_probability,
                palermo_scale_max,
                torino_scale_max,
                potential_impacts_count,
                impact_year_range,
                ROW_NUMBER() OVER (
                    PARTITION BY sentry_id
                    ORDER BY snapshot_key DESC, snapshot_time DESC, run_id DESC
                ) AS rn
            FROM {sentry}
        ),
        sentry_by_asteroid_key AS (
            SELECT
                b.asteroid_key,
                COUNT(DISTINCT b.identifier_value) AS sentry_identifier_count,
                CASE WHEN COUNT(DISTINCT b.identifier_value) > 1 THEN TRUE ELSE FALSE END AS is_sentry_ambiguous,
                CASE WHEN COUNT(DISTINCT b.identifier_value) = 1 THEN MAX(b.identifier_value) ELSE NULL END AS sentry_id,
                CASE WHEN COUNT(DISTINCT b.identifier_value) = 1 THEN MAX(s.impact_probability) ELSE NULL END AS sentry_impact_probability,
                CASE WHEN COUNT(DISTINCT b.identifier_value) = 1 THEN MAX(s.palermo_scale_max) ELSE NULL END AS sentry_palermo_scale_max,
                CASE WHEN COUNT(DISTINCT b.identifier_value) = 1 THEN MAX(s.torino_scale_max) ELSE NULL END AS sentry_torino_scale_max,
                CASE WHEN COUNT(DISTINCT b.identifier_value) = 1 THEN MAX(s.potential_impacts_count) ELSE NULL END AS sentry_potential_impacts_count,
                CASE WHEN COUNT(DISTINCT b.identifier_value) = 1 THEN MAX(s.impact_year_range) ELSE NULL END AS sentry_impact_year_range
            FROM {bridge} b
            LEFT JOIN latest_sentry s
                ON s.sentry_id = b.identifier_value AND s.rn = 1
            WHERE b.source_system = 'sentry'
              AND b.identifier_name = 'sentry_id'
            GROUP BY b.asteroid_key
        ),
        bridge_sbdb_pivot AS (
            SELECT
                asteroid_key,
                MAX(identifier_value) AS spkid
            FROM {bridge}
            WHERE source_system = 'sbdb'
              AND identifier_name = 'spkid'
              AND is_primary_pivot = TRUE
            GROUP BY asteroid_key
        ),
        sbdb_info AS (
            SELECT
                spkid,
                designation AS sbdb_designation,
                fullname AS sbdb_fullname,
                is_pha AS sbdb_is_pha,
                orbit_class_name AS sbdb_orbit_class_name
            FROM (
                SELECT
                    spkid,
                    designation,
                    fullname,
                    is_pha,
                    orbit_class_name,
                    ROW_NUMBER() OVER (
                        PARTITION BY spkid
                        ORDER BY snapshot_key DESC, snapshot_time DESC, run_id DESC
                    ) AS rn
                FROM {sbdb_obj}
            )
            WHERE rn = 1
        )
        SELECT
            n.closest_approach_date,
            n.neows_id,
            n.name,
            n.miss_distance_km,
            (n.miss_distance_km / 384400.0) AS miss_distance_lunar,
            n.hazardous,
            res.asteroid_key,
            res.match_state,
            CASE
                WHEN s.asteroid_key IS NOT NULL AND s.sentry_identifier_count > 0 THEN TRUE
                ELSE FALSE
            END AS is_sentry_monitored,
            COALESCE(s.is_sentry_ambiguous, FALSE) AS is_sentry_ambiguous,
            s.sentry_id,
            s.sentry_impact_probability,
            s.sentry_palermo_scale_max,
            s.sentry_torino_scale_max,
            s.sentry_potential_impacts_count,
            s.sentry_impact_year_range,
            CASE
                WHEN b_sbdb.spkid IS NOT NULL THEN TRUE
                ELSE FALSE
            END AS has_sbdb_characterization,
            b_sbdb.spkid AS sbdb_spkid,
            sbdb.sbdb_designation,
            sbdb.sbdb_fullname,
            sbdb.sbdb_orbit_class_name
        FROM deduped_neows n
        JOIN resolution res ON res.neows_id = n.neows_id
        LEFT JOIN sentry_by_asteroid_key s ON s.asteroid_key = res.asteroid_key
        LEFT JOIN bridge_sbdb_pivot b_sbdb ON b_sbdb.asteroid_key = res.asteroid_key
        LEFT JOIN sbdb_info sbdb ON sbdb.spkid = b_sbdb.spkid
        ORDER BY n.miss_distance_km ASC
        """

        conn = self._get_connection()
        df = conn.execute(query).df()
        conn.close()
        return df

    def get_world_snapshot(self, neows_id: str | None = None) -> dict[str, Any]:
        """Retrieve the world population: one row per NeoWs object, in ONE set-based query.

        With `neows_id`, the same query is restricted to that object (used by the
        per-object profile so it shares this exact identity/availability logic).

        Grain: (neows_id). Encounter selected via CLOSEST_OBSERVED_APPROACH (min
        miss_distance_km, tie-breaker closest_approach_date ASC), as in the detail route.
        Resolution mirrors get_resolution_state (audit log first, bridge fallback);
        SBDB availability uses the shared latest-snapshot selection; Sentry
        availability comes from crosswalk membership, never from the NeoWs PHA flag.
        """
        if not self._asteroids_file.exists():
            return {"records": pd.DataFrame(), "neows_run_id": None}

        ast = _parquet_relation(self._asteroids_file, {})
        bridge = _parquet_relation(self._bridge_file, _BRIDGE_COLUMNS)
        sentry = _parquet_relation(self._sentry_risk_file, _SENTRY_COLUMNS)

        query = f"""
        WITH neows AS (
            SELECT
                id AS neows_id, name, closest_approach_date, miss_distance_km, hazardous,
                ROW_NUMBER() OVER (
                    PARTITION BY id
                    ORDER BY miss_distance_km ASC, closest_approach_date ASC
                ) AS rn
            FROM {ast}
            {"WHERE id = ?" if neows_id is not None else ""}
        ),
        resolved AS (
            SELECT
                n.neows_id, n.name, n.closest_approach_date, n.miss_distance_km, n.hazardous,
                res.match_state, res.asteroid_key, res.match_rule, res.resolved_at
            FROM neows n
            JOIN ({self._neows_resolution_sql("SELECT neows_id FROM neows WHERE rn = 1")}) res
                ON res.neows_id = n.neows_id
            WHERE n.rn = 1
        ),
        sbdb_spkid AS (
            SELECT
                asteroid_key, identifier_value AS spkid,
                ROW_NUMBER() OVER (
                    PARTITION BY asteroid_key ORDER BY is_primary_pivot DESC, identifier_value ASC
                ) AS rn
            FROM {bridge}
            WHERE source_system = 'sbdb' AND identifier_name = 'spkid'
        ),
        sbdb_snapshot AS ({self._sbdb_latest_snapshot_sql()}),
        sentry_links AS (
            SELECT
                asteroid_key,
                COUNT(DISTINCT identifier_value) AS sentry_link_count,
                MIN(identifier_value) AS sentry_id
            FROM {bridge}
            WHERE source_system = 'sentry' AND identifier_name = 'sentry_id'
              AND identifier_value IS NOT NULL AND identifier_value <> ''
            GROUP BY asteroid_key
        ),
        sentry_latest AS (
            -- One whole Mode S row per sentry_id: assessment values and their
            -- snapshot/run provenance always come from the same record.
            SELECT
                {", ".join(_SENTRY_COLUMNS)},
                ROW_NUMBER() OVER (
                    PARTITION BY sentry_id ORDER BY snapshot_key DESC, snapshot_time DESC, run_id DESC
                ) AS rn
            FROM {sentry}
        ),
        sentry_catalog AS (
            SELECT MAX(snapshot_key) AS latest_catalog_snapshot_key FROM {sentry}
        )
        SELECT
            r.neows_id, r.name, r.closest_approach_date, r.miss_distance_km, r.hazardous,
            r.match_state, r.asteroid_key, r.match_rule, r.resolved_at,
            sp.spkid AS sbdb_spkid,
            ss.snapshot_key AS sbdb_snapshot_key,
            ss.run_id AS sbdb_run_id,
            COALESCE(sl.sentry_link_count, 0) AS sentry_link_count,
            CASE WHEN sl.sentry_link_count = 1 THEN sl.sentry_id END AS sentry_id,
            sx.snapshot_key AS sentry_snapshot_key,
            sx.run_id AS sentry_run_id,
            sx.snapshot_time AS sentry_snapshot_time,
            {", ".join(f"sx.{col} AS sentry_{col}" for col in _SENTRY_ASSESSMENT_COLUMNS)},
            sc.latest_catalog_snapshot_key AS sentry_latest_catalog_snapshot_key
        FROM resolved r
        LEFT JOIN sbdb_spkid sp
            ON r.match_state = 'RESOLVED' AND sp.asteroid_key = r.asteroid_key AND sp.rn = 1
        LEFT JOIN sbdb_snapshot ss ON ss.spkid = sp.spkid
        LEFT JOIN sentry_links sl
            ON r.match_state = 'RESOLVED' AND sl.asteroid_key = r.asteroid_key
        LEFT JOIN sentry_latest sx
            ON sl.sentry_link_count = 1 AND sx.sentry_id = sl.sentry_id AND sx.rn = 1
        CROSS JOIN sentry_catalog sc
        ORDER BY r.miss_distance_km ASC, r.neows_id ASC
        """

        conn = self._get_connection()
        try:
            df = conn.execute(query, [neows_id] if neows_id is not None else []).df()
        finally:
            conn.close()

        # NeoWs dataset provenance: save_to_parquet stamps run_id into file metadata
        # when available; older files carry none, which is reported as None.
        neows_meta = pq.read_schema(self._asteroids_file).metadata or {}
        neows_run_id = neows_meta.get(b"run_id")
        return {
            "records": df,
            "neows_run_id": neows_run_id.decode("utf-8") if neows_run_id else None,
        }

    def get_resolution_state(self, neows_id: str | None) -> dict[str, Any]:
        """Resolve a NeoWs source identifier to its entity-resolution state.

        Returns a dictionary with:
        - neows_id: source identifier
        - match_state: 'RESOLVED' | 'UNRESOLVED' | 'AMBIGUOUS' | 'INVALID'
        - asteroid_key: UUID5 canonical key if RESOLVED, else None
        - match_rule: matching rule if available
        - evidence: explanation / lineage details
        """
        if not neows_id or not isinstance(neows_id, str) or not neows_id.strip():
            return {
                "neows_id": neows_id,
                "match_state": "INVALID",
                "asteroid_key": None,
                "match_rule": "INVALID_INPUT",
                "evidence": "NeoWs identifier is missing or malformed.",
            }

        clean_id = neows_id.strip()
        if not clean_id.isdigit():
            return {
                "neows_id": clean_id,
                "match_state": "INVALID",
                "asteroid_key": None,
                "match_rule": "INVALID_SYNTAX",
                "evidence": "NeoWs identifier must contain only numeric digits.",
            }

        conn = self._get_connection()

        # Step 1: Check fact_entity_resolution audit table (Authoritative Lineage)
        if self._resolution_file.exists():
            res_path = str(self._resolution_file).replace("\\", "/")
            res_rows = conn.execute(
                f"""
                SELECT match_state, assigned_asteroid_key, match_rule, evidence_json, resolved_at
                FROM '{res_path}'
                WHERE source_system = 'neows'
                  AND identifier_name = 'id'
                  AND source_identifier_value = ?
                ORDER BY resolved_at DESC
                LIMIT 1
                """,
                [clean_id],
            ).fetchall()

            if res_rows:
                state, assigned_key, rule, evidence, resolved_at = res_rows[0]
                conn.close()
                if state == "RESOLVED" and assigned_key:
                    return {
                        "neows_id": clean_id,
                        "match_state": "RESOLVED",
                        "asteroid_key": assigned_key,
                        "match_rule": rule,
                        "evidence": evidence or "Resolved via entity resolution audit log.",
                        "resolved_at": str(resolved_at) if resolved_at else None,
                    }
                elif state == "AMBIGUOUS":
                    return {
                        "neows_id": clean_id,
                        "match_state": "AMBIGUOUS",
                        "asteroid_key": None,
                        "match_rule": rule,
                        "evidence": evidence or "Ambiguous multi-source match detected in audit log.",
                        "resolved_at": str(resolved_at) if resolved_at else None,
                    }
                elif state == "INVALID":
                    return {
                        "neows_id": clean_id,
                        "match_state": "INVALID",
                        "asteroid_key": None,
                        "match_rule": rule,
                        "evidence": evidence or "Identifier evaluated as invalid in audit log.",
                        "resolved_at": str(resolved_at) if resolved_at else None,
                    }
                else:
                    return {
                        "neows_id": clean_id,
                        "match_state": "UNRESOLVED",
                        "asteroid_key": None,
                        "match_rule": rule or "NO_CROSS_SOURCE_MATCH",
                        "evidence": evidence or "No cross-source link established in entity resolution catalog.",
                        "resolved_at": str(resolved_at) if resolved_at else None,
                    }

        # Step 2: Fallback to bridge_asteroid_identifier index only if no audit record exists
        if self._bridge_file.exists():
            bridge_path = str(self._bridge_file).replace("\\", "/")
            bridge_rows = conn.execute(
                f"""
                SELECT DISTINCT asteroid_key
                FROM '{bridge_path}'
                WHERE source_system = 'neows'
                  AND identifier_name = 'id'
                  AND identifier_value = ?
                """,
                [clean_id],
            ).fetchall()

            if len(bridge_rows) == 1:
                key = bridge_rows[0][0]
                conn.close()
                return {
                    "neows_id": clean_id,
                    "match_state": "RESOLVED",
                    "asteroid_key": key,
                    "match_rule": "BRIDGE_EXACT_NEOWS_ID",
                    "evidence": "Deterministic exact match in canonical entity bridge fallback.",
                    "resolved_at": None,
                }
            elif len(bridge_rows) > 1:
                conn.close()
                return {
                    "neows_id": clean_id,
                    "match_state": "AMBIGUOUS",
                    "asteroid_key": None,
                    "match_rule": "BRIDGE_MULTIPLE_CANDIDATE_KEYS",
                    "evidence": f"Identifier maps to {len(bridge_rows)} distinct candidate entity keys in bridge fallback.",
                    "resolved_at": None,
                }

        conn.close()
        return {
            "neows_id": clean_id,
            "match_state": "UNRESOLVED",
            "asteroid_key": None,
            "match_rule": "NO_RESOLUTION_RECORD",
            "evidence": "No crosswalk record found for this NeoWs identifier.",
            "resolved_at": None,
        }

    def get_sbdb_profile(self, asteroid_key: str | None) -> dict[str, Any] | None:
        """Retrieve SBDB physical and orbital characterization for an entity.

        Grain: (spkid) resolved via canonical asteroid_key.
        """
        if not asteroid_key or not isinstance(asteroid_key, str) or not asteroid_key.strip():
            return None

        clean_key = asteroid_key.strip()
        conn = self._get_connection()

        # Step 1: Resolve canonical SPKID from bridge
        if not self._bridge_file.exists():
            conn.close()
            return None

        bridge_path = str(self._bridge_file).replace("\\", "/")
        spkid_rows = conn.execute(
            f"""
            SELECT identifier_value
            FROM '{bridge_path}'
            WHERE asteroid_key = ?
              AND source_system = 'sbdb'
              AND identifier_name = 'spkid'
            ORDER BY is_primary_pivot DESC
            LIMIT 1
            """,
            [clean_key],
        ).fetchall()

        if not spkid_rows:
            conn.close()
            return None

        spkid = spkid_rows[0][0]

        # Step 2: Select ONE coherent SBDB snapshot for this SPK-ID (see
        # _sbdb_latest_snapshot_sql). Every table below is pinned to it; nothing
        # falls back to an older snapshot, so absent fields stay None rather than mixing.
        anchor_rows = conn.execute(
            f"""
            SELECT snapshot_key, run_id, snapshot_time
            FROM ({self._sbdb_latest_snapshot_sql()})
            WHERE spkid = ?
            """,
            [spkid],
        ).fetchall()
        if not anchor_rows:
            conn.close()
            return None
        snapshot_key, run_id, snapshot_time = anchor_rows[0]
        snapshot_params = [spkid, snapshot_key, run_id]
        snapshot_filter = "spkid = ? AND snapshot_key = ? AND run_id = ?"

        # Step 3: Object metadata from the selected snapshot
        obj_data: dict[str, Any] = {}
        if self._sbdb_object_file.exists():
            obj_path = str(self._sbdb_object_file).replace("\\", "/")
            obj_df = conn.execute(
                f"""
                SELECT
                    spkid, designation, fullname, shortname, object_kind,
                    is_neo, is_pha, orbit_class_code, orbit_class_name, orbit_id
                FROM '{obj_path}'
                WHERE {snapshot_filter}
                LIMIT 1
                """,
                snapshot_params,
            ).df()
            if not obj_df.empty:
                obj_data = obj_df.iloc[0].to_dict()

        # Step 4: Orbit solution from the selected snapshot
        orb_data: dict[str, Any] = {}
        if self._sbdb_orbit_file.exists():
            orb_path = str(self._sbdb_orbit_file).replace("\\", "/")
            orb_df = conn.execute(
                f"""
                SELECT
                    orbit_id, epoch_jd, equinox, soln_date, orbit_source,
                    producer, first_obs, last_obs, data_arc_days, n_obs_used,
                    condition_code, rms, earth_moid_au, jupiter_moid_au, t_jup
                FROM '{orb_path}'
                WHERE {snapshot_filter}
                ORDER BY orbit_id DESC
                LIMIT 1
                """,
                snapshot_params,
            ).df()
            if not orb_df.empty:
                orb_data = orb_df.iloc[0].to_dict()

        # Step 5: Osculating orbital elements from the selected snapshot (and the
        # selected orbit solution, when known). Pivot is safe: one row per element.
        # Units as published by SBDB: a/q/ad au; e unitless; i/om/w/ma deg;
        # n deg/d; per d; tp Julian Date (TDB).
        element_columns = {
            "eccentricity": "e",
            "semi_major_axis_au": "a",
            "perihelion_distance_au": "q",
            "aphelion_distance_au": "ad",
            "inclination_deg": "i",
            "ascending_node_longitude_deg": "om",
            "argument_of_perihelion_deg": "w",
            "mean_anomaly_deg": "ma",
            "mean_motion_deg_per_day": "n",
            "orbital_period_days": "per",
            "time_of_perihelion_jd_tdb": "tp",
        }
        elem_data: dict[str, Any] = {col: None for col in element_columns}
        elem_equinox = None
        if self._sbdb_elements_file.exists():
            elem_path = str(self._sbdb_elements_file).replace("\\", "/")
            elem_filter = snapshot_filter
            elem_params = list(snapshot_params)
            if orb_data.get("orbit_id") is not None:
                elem_filter += " AND orbit_id = ?"
                elem_params.append(orb_data["orbit_id"])
            pivot_sql = ",\n                    ".join(
                f"MAX(CASE WHEN element_name = '{name}' THEN element_value END) AS {col}"
                for col, name in element_columns.items()
            )
            elem_df = conn.execute(
                f"""
                SELECT
                    {pivot_sql},
                    MAX(equinox) AS equinox
                FROM '{elem_path}'
                WHERE {elem_filter}
                """,
                elem_params,
            ).df()
            if not elem_df.empty:
                for col in elem_data:
                    val = elem_df.iloc[0][col]
                    elem_data[col] = float(val) if pd.notna(val) else None
                eq_val = elem_df.iloc[0]["equinox"]
                elem_equinox = eq_val if pd.notna(eq_val) else None

        # DEPRECATED compatibility field: a unit conversion of the SBDB source
        # period (days -> Julian years), not a source value and not a^1.5.
        period_days = elem_data["orbital_period_days"]
        orbital_period_yr = period_days / 365.25 if period_days is not None else None

        # Step 6: Physical parameters from the selected snapshot
        phys_data: dict[str, Any] = {
            "estimated_diameter_km": None,
            "absolute_magnitude": None,
            "albedo": None,
            "rotational_period_hr": None,
        }
        if self._sbdb_physical_file.exists():
            phys_path = str(self._sbdb_physical_file).replace("\\", "/")
            phys_df = conn.execute(
                f"""
                SELECT
                    MAX(CASE WHEN param_name = 'diameter' THEN param_value_numeric END) AS estimated_diameter_km,
                    MAX(CASE WHEN param_name = 'H' THEN param_value_numeric END) AS absolute_magnitude,
                    MAX(CASE WHEN param_name = 'albedo' THEN param_value_numeric END) AS albedo,
                    MAX(CASE WHEN param_name = 'rot_per' THEN param_value_numeric END) AS rotational_period_hr
                FROM '{phys_path}'
                WHERE {snapshot_filter}
                """,
                snapshot_params,
            ).df()
            if not phys_df.empty:
                for col in phys_data:
                    val = phys_df.iloc[0][col]
                    phys_data[col] = float(val) if pd.notna(val) else None

        # Step 7: Evaluate Astrometric Data Quality Tier
        arc_days = orb_data.get("data_arc_days")
        n_obs = orb_data.get("n_obs_used")
        cond_code = orb_data.get("condition_code")

        if arc_days is None and n_obs is None and cond_code is None:
            tier = "UNREPORTED"
        else:
            try:
                cond_int = int(cond_code) if cond_code is not None else 99
                arc_int = int(arc_days) if arc_days is not None else 0
                obs_int = int(n_obs) if n_obs is not None else 0
                if cond_int < 5 and arc_int >= 30 and obs_int >= 20:
                    tier = "ADEQUATELY_CONSTRAINED"
                else:
                    tier = "FOLLOWUP_PRIORITY_LIMITED_ARC"
            except (ValueError, TypeError):
                tier = "FOLLOWUP_PRIORITY_LIMITED_ARC"

        conn.close()

        profile = {
            "spkid": spkid,
            "asteroid_key": clean_key,
            "designation": obj_data.get("designation"),
            "fullname": obj_data.get("fullname"),
            "shortname": obj_data.get("shortname"),
            "object_kind": obj_data.get("object_kind"),
            "is_neo": _nullable_bool(obj_data.get("is_neo")),
            "is_pha": _nullable_bool(obj_data.get("is_pha")),
            "orbit_class_code": obj_data.get("orbit_class_code"),
            "orbit_class_name": obj_data.get("orbit_class_name"),
            "snapshot_key": snapshot_key,
            "run_id": run_id,
            "snapshot_time": snapshot_time,
            "orbit_id": orb_data.get("orbit_id") or obj_data.get("orbit_id"),
            "epoch_jd": orb_data.get("epoch_jd"),
            "equinox": orb_data.get("equinox") or elem_equinox,
            "soln_date": orb_data.get("soln_date"),
            "orbit_source": orb_data.get("orbit_source"),
            "producer": orb_data.get("producer"),
            "first_obs": orb_data.get("first_obs"),
            "last_obs": orb_data.get("last_obs"),
            "data_arc_days": orb_data.get("data_arc_days"),
            "n_obs_used": orb_data.get("n_obs_used"),
            "condition_code": orb_data.get("condition_code"),
            "rms": orb_data.get("rms"),
            "earth_moid_au": orb_data.get("earth_moid_au"),
            "jupiter_moid_au": orb_data.get("jupiter_moid_au"),
            "t_jup": orb_data.get("t_jup"),
            **elem_data,
            "orbital_period_yr": orbital_period_yr,
            "estimated_diameter_km": phys_data["estimated_diameter_km"],
            "absolute_magnitude": phys_data["absolute_magnitude"],
            "albedo": phys_data["albedo"],
            "rotational_period_hr": phys_data["rotational_period_hr"],
            "astrometric_data_quality_tier": tier,
        }
        return profile

    def get_sentry_profile(self, asteroid_key: str | None) -> dict[str, Any] | None:
        """Retrieve current Sentry impact-monitoring profile for an entity.

        Grain: (asteroid_key) with reverse-cardinality defense.
        """
        if not asteroid_key or not isinstance(asteroid_key, str) or not asteroid_key.strip():
            return None

        clean_key = asteroid_key.strip()
        conn = self._get_connection()

        if not self._bridge_file.exists():
            conn.close()
            return None

        bridge_path = str(self._bridge_file).replace("\\", "/")
        sentry_rows = conn.execute(
            f"""
            SELECT DISTINCT identifier_value
            FROM '{bridge_path}'
            WHERE asteroid_key = ?
              AND source_system = 'sentry'
              AND identifier_name = 'sentry_id'
            """,
            [clean_key],
        ).fetchall()

        sentry_ids = [r[0] for r in sentry_rows if r[0]]
        sentry_count = len(sentry_ids)

        # Case 1: Not monitored in Sentry
        if sentry_count == 0:
            conn.close()
            return {
                "asteroid_key": clean_key,
                "has_sentry_monitoring": False,
                "is_sentry_ambiguous": False,
                "sentry_identifier_count": 0,
                "sentry_id": None,
                "designation": None,
                "fullname": None,
                "latest_impact_probability": None,
                "latest_palermo_scale_max": None,
                "latest_palermo_scale_cum": None,
                "latest_torino_scale_max": None,
                "latest_potential_impacts_count": None,
                "v_infinity_km_s": None,
                "impact_year_range": None,
                "last_obs_date": None,
                "latest_snapshot_key": None,
                "total_snapshots_observed": 0,
                "all_time_max_impact_probability": None,
                "all_time_max_palermo_scale_max": None,
                "all_time_max_torino_scale_max": None,
                "is_currently_active": False,
            }

        # Case 2: Ambiguous linkage (multiple Sentry IDs mapped to this asteroid_key)
        # Suppress scalar metrics to prevent false cross-source assignment!
        if sentry_count > 1:
            conn.close()
            return {
                "asteroid_key": clean_key,
                "has_sentry_monitoring": True,
                "is_sentry_ambiguous": True,
                "sentry_identifier_count": sentry_count,
                "sentry_id": None,
                "designation": None,
                "fullname": None,
                "latest_impact_probability": None,
                "latest_palermo_scale_max": None,
                "latest_palermo_scale_cum": None,
                "latest_torino_scale_max": None,
                "latest_potential_impacts_count": None,
                "v_infinity_km_s": None,
                "impact_year_range": None,
                "last_obs_date": None,
                "latest_snapshot_key": None,
                "total_snapshots_observed": 0,
                "all_time_max_impact_probability": None,
                "all_time_max_palermo_scale_max": None,
                "all_time_max_torino_scale_max": None,
                "is_currently_active": False,
            }

        # Case 3: Exactly one Sentry ID mapped (safe one-to-one relationship)
        sentry_id = sentry_ids[0]

        if not self._sentry_risk_file.exists():
            conn.close()
            return {
                "asteroid_key": clean_key,
                "has_sentry_monitoring": True,
                "is_sentry_ambiguous": False,
                "sentry_identifier_count": 1,
                "sentry_id": sentry_id,
                "designation": None,
                "fullname": None,
                "latest_impact_probability": None,
                "latest_palermo_scale_max": None,
                "latest_palermo_scale_cum": None,
                "latest_torino_scale_max": None,
                "latest_potential_impacts_count": None,
                "v_infinity_km_s": None,
                "impact_year_range": None,
                "last_obs_date": None,
                "latest_snapshot_key": None,
                "total_snapshots_observed": 0,
                "all_time_max_impact_probability": None,
                "all_time_max_palermo_scale_max": None,
                "all_time_max_torino_scale_max": None,
                "is_currently_active": False,
            }

        sentry_path = str(self._sentry_risk_file).replace("\\", "/")

        # Query latest snapshot and historical aggregations
        latest_df = conn.execute(
            f"""
            SELECT
                sentry_id, designation, fullname, impact_probability,
                palermo_scale_max, palermo_scale_cum, torino_scale_max,
                potential_impacts_count, v_infinity_km_s, impact_year_range,
                last_obs_date, snapshot_key
            FROM '{sentry_path}'
            WHERE sentry_id = ?
            ORDER BY snapshot_key DESC, snapshot_time DESC, run_id DESC
            LIMIT 1
            """,
            [sentry_id],
        ).df()

        agg_df = conn.execute(
            f"""
            SELECT
                COUNT(DISTINCT snapshot_key) AS total_snapshots_observed,
                MAX(impact_probability) AS all_time_max_impact_probability,
                MAX(palermo_scale_max) AS all_time_max_palermo_scale_max,
                MAX(torino_scale_max) AS all_time_max_torino_scale_max
            FROM '{sentry_path}'
            WHERE sentry_id = ?
            """,
            [sentry_id],
        ).df()

        # Check if active in latest global snapshot
        latest_global_key = conn.execute(
            f"SELECT MAX(snapshot_key) FROM '{sentry_path}'"
        ).fetchone()[0]

        is_active = (
            latest_df.iloc[0]["snapshot_key"] == latest_global_key
            if not latest_df.empty and latest_global_key is not None
            else False
        )

        conn.close()

        if latest_df.empty:
            return {
                "asteroid_key": clean_key,
                "has_sentry_monitoring": True,
                "is_sentry_ambiguous": False,
                "sentry_identifier_count": 1,
                "sentry_id": sentry_id,
                "designation": None,
                "fullname": None,
                "latest_impact_probability": None,
                "latest_palermo_scale_max": None,
                "latest_palermo_scale_cum": None,
                "latest_torino_scale_max": None,
                "latest_potential_impacts_count": None,
                "v_infinity_km_s": None,
                "impact_year_range": None,
                "last_obs_date": None,
                "latest_snapshot_key": None,
                "total_snapshots_observed": 0,
                "all_time_max_impact_probability": None,
                "all_time_max_palermo_scale_max": None,
                "all_time_max_torino_scale_max": None,
                "is_currently_active": False,
            }

        latest_row = latest_df.iloc[0]
        agg_row = agg_df.iloc[0]

        return {
            "asteroid_key": clean_key,
            "has_sentry_monitoring": True,
            "is_sentry_ambiguous": False,
            "sentry_identifier_count": 1,
            "sentry_id": sentry_id,
            "designation": latest_row["designation"],
            "fullname": latest_row["fullname"],
            "latest_impact_probability": float(latest_row["impact_probability"]) if pd.notna(latest_row["impact_probability"]) else None,
            "latest_palermo_scale_max": float(latest_row["palermo_scale_max"]) if pd.notna(latest_row["palermo_scale_max"]) else None,
            "latest_palermo_scale_cum": float(latest_row["palermo_scale_cum"]) if pd.notna(latest_row["palermo_scale_cum"]) else None,
            "latest_torino_scale_max": int(latest_row["torino_scale_max"]) if pd.notna(latest_row["torino_scale_max"]) else None,
            "latest_potential_impacts_count": int(latest_row["potential_impacts_count"]) if pd.notna(latest_row["potential_impacts_count"]) else None,
            "v_infinity_km_s": float(latest_row["v_infinity_km_s"]) if pd.notna(latest_row["v_infinity_km_s"]) else None,
            "impact_year_range": latest_row["impact_year_range"],
            "last_obs_date": latest_row["last_obs_date"],
            "latest_snapshot_key": latest_row["snapshot_key"],
            "total_snapshots_observed": int(agg_row["total_snapshots_observed"]),
            "all_time_max_impact_probability": float(agg_row["all_time_max_impact_probability"]) if pd.notna(agg_row["all_time_max_impact_probability"]) else None,
            "all_time_max_palermo_scale_max": float(agg_row["all_time_max_palermo_scale_max"]) if pd.notna(agg_row["all_time_max_palermo_scale_max"]) else None,
            "all_time_max_torino_scale_max": int(agg_row["all_time_max_torino_scale_max"]) if pd.notna(agg_row["all_time_max_torino_scale_max"]) else None,
            "is_currently_active": bool(is_active),
        }

    def get_historical_risk(self, sentry_id: str | None) -> pd.DataFrame:
        """Retrieve snapshot-level historical risk trajectory for a Sentry object.

        Grain: (snapshot_key, sentry_id)
        Reflects Phase 7 v_sentry_risk_metric_history analytical semantics.
        Strictly non-causal: reports metric changes without speculating on causes.
        """
        cols = [
            "snapshot_key",
            "snapshot_time",
            "sentry_id",
            "designation",
            "impact_probability",
            "palermo_scale_max",
            "palermo_scale_cum",
            "torino_scale_max",
            "potential_impacts_count",
            "v_infinity_km_s",
            "estimated_diameter_km",
            "absolute_magnitude",
            "impact_year_range",
            "last_obs_date",
            "is_impact_probability_changed",
            "is_palermo_scale_max_changed",
        ]

        if not sentry_id or not isinstance(sentry_id, str) or not sentry_id.strip():
            return pd.DataFrame(columns=cols)

        if not self._sentry_risk_file.exists():
            return pd.DataFrame(columns=cols)

        clean_id = sentry_id.strip()
        conn = self._get_connection()
        sentry_path = str(self._sentry_risk_file).replace("\\", "/")

        query = f"""
        WITH deduped AS (
            SELECT
                snapshot_key,
                snapshot_time,
                sentry_id,
                designation,
                impact_probability,
                palermo_scale_max,
                palermo_scale_cum,
                torino_scale_max,
                potential_impacts_count,
                v_infinity_km_s,
                estimated_diameter_km,
                absolute_magnitude,
                impact_year_range,
                last_obs_date,
                ROW_NUMBER() OVER (
                    PARTITION BY snapshot_key, sentry_id
                    ORDER BY snapshot_time DESC, run_id DESC
                ) AS rn
            FROM '{sentry_path}'
            WHERE sentry_id = ?
        ),
        sequenced AS (
            SELECT
                snapshot_key,
                snapshot_time,
                sentry_id,
                designation,
                impact_probability,
                palermo_scale_max,
                palermo_scale_cum,
                torino_scale_max,
                potential_impacts_count,
                v_infinity_km_s,
                estimated_diameter_km,
                absolute_magnitude,
                impact_year_range,
                last_obs_date,
                LAG(impact_probability) OVER (ORDER BY snapshot_key ASC) AS prev_impact_probability,
                LAG(palermo_scale_max) OVER (ORDER BY snapshot_key ASC) AS prev_palermo_scale_max
            FROM deduped
            WHERE rn = 1
        )
        SELECT
            snapshot_key,
            snapshot_time,
            sentry_id,
            designation,
            impact_probability,
            palermo_scale_max,
            palermo_scale_cum,
            torino_scale_max,
            potential_impacts_count,
            v_infinity_km_s,
            estimated_diameter_km,
            absolute_magnitude,
            impact_year_range,
            last_obs_date,
            CASE
                WHEN prev_impact_probability IS NULL THEN FALSE
                WHEN impact_probability != prev_impact_probability THEN TRUE
                ELSE FALSE
            END AS is_impact_probability_changed,
            CASE
                WHEN prev_palermo_scale_max IS NULL THEN FALSE
                WHEN palermo_scale_max != prev_palermo_scale_max THEN TRUE
                ELSE FALSE
            END AS is_palermo_scale_max_changed
        FROM sequenced
        ORDER BY snapshot_key ASC
        """

        df = conn.execute(query, [clean_id]).df()
        conn.close()
        return df

    def get_crosswalk(self, asteroid_key: str | None) -> pd.DataFrame:
        """Retrieve multi-source identifier mappings for an entity.

        Grain: (asteroid_key, source_system, identifier_name, identifier_value)
        """
        cols = [
            "asteroid_key",
            "source_system",
            "identifier_name",
            "identifier_value",
            "is_primary_pivot",
            "created_at",
            "updated_at",
        ]

        if not asteroid_key or not isinstance(asteroid_key, str) or not asteroid_key.strip():
            return pd.DataFrame(columns=cols)

        if not self._bridge_file.exists():
            return pd.DataFrame(columns=cols)

        clean_key = asteroid_key.strip()
        conn = self._get_connection()
        bridge_path = str(self._bridge_file).replace("\\", "/")

        query = f"""
        SELECT
            asteroid_key,
            source_system,
            identifier_name,
            identifier_value,
            is_primary_pivot,
            created_at,
            updated_at
        FROM '{bridge_path}'
        WHERE asteroid_key = ?
        ORDER BY is_primary_pivot DESC, source_system ASC, identifier_name ASC
        """

        df = conn.execute(query, [clean_key]).df()
        conn.close()
        return df


class AthenaDataProvider:
    """Cloud provider querying AWS Athena SQL Lakehouse Views via Boto3.

    Placeholder for future live cloud execution.
    """

    def __init__(self, region_name: str = "us-east-1", s3_output: str | None = None) -> None:
        self.region_name = region_name
        self.s3_output = s3_output

    def get_execution_mode(self) -> str:
        return "ATHENA (LIVE AWS S3 LAKEHOUSE)"

    def get_threat_watchlist(self) -> pd.DataFrame:
        raise NotImplementedError(
            "Athena cloud queries are not active in this offline execution step. Use LOCAL provider."
        )

    def get_world_snapshot(self, neows_id: str | None = None) -> dict[str, Any]:
        raise NotImplementedError("Athena cloud queries are not active in this offline execution step.")

    def get_resolution_state(self, neows_id: str | None) -> dict[str, Any]:
        raise NotImplementedError("Athena cloud queries are not active in this offline execution step.")

    def get_sbdb_profile(self, asteroid_key: str | None) -> dict[str, Any] | None:
        raise NotImplementedError("Athena cloud queries are not active in this offline execution step.")

    def get_sentry_profile(self, asteroid_key: str | None) -> dict[str, Any] | None:
        raise NotImplementedError("Athena cloud queries are not active in this offline execution step.")

    def get_historical_risk(self, sentry_id: str | None) -> pd.DataFrame:
        raise NotImplementedError("Athena cloud queries are not active in this offline execution step.")

    def get_crosswalk(self, asteroid_key: str | None) -> pd.DataFrame:
        raise NotImplementedError("Athena cloud queries are not active in this offline execution step.")


class DashboardDataProvider:
    """Unified Facade Provider selecting Local DuckDB or Athena Cloud based on mode."""

    def __init__(
        self,
        base_dir: Path | str | None = None,
        execution_mode: str = "LOCAL",
        athena_region: str = "us-east-1",
        athena_s3_output: str | None = None,
    ) -> None:
        self.execution_mode = execution_mode.upper()
        if self.execution_mode == "ATHENA":
            self._provider = AthenaDataProvider(
                region_name=athena_region,
                s3_output=athena_s3_output,
            )
        else:
            self._provider = LocalDuckDBDataProvider(base_dir=base_dir)

    def get_execution_mode(self) -> str:
        return self._provider.get_execution_mode()

    def get_threat_watchlist(self) -> pd.DataFrame:
        return self._provider.get_threat_watchlist()

    def get_world_snapshot(self, neows_id: str | None = None) -> dict[str, Any]:
        return self._provider.get_world_snapshot(neows_id)

    def get_resolution_state(self, neows_id: str | None) -> dict[str, Any]:
        return self._provider.get_resolution_state(neows_id)

    def get_sbdb_profile(self, asteroid_key: str | None) -> dict[str, Any] | None:
        return self._provider.get_sbdb_profile(asteroid_key)

    def get_sentry_profile(self, asteroid_key: str | None) -> dict[str, Any] | None:
        return self._provider.get_sentry_profile(asteroid_key)

    def get_historical_risk(self, sentry_id: str | None) -> pd.DataFrame:
        return self._provider.get_historical_risk(sentry_id)

    def get_crosswalk(self, asteroid_key: str | None) -> pd.DataFrame:
        return self._provider.get_crosswalk(asteroid_key)
