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

logger = logging.getLogger(__name__)

UUID5_PATTERN = re.compile(
    r"^ast_[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


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

        conn = self._get_connection()

        # Build DuckDB query over local Parquet assets
        ast_path = str(self._asteroids_file).replace("\\", "/")
        bridge_path = str(self._bridge_file).replace("\\", "/") if self._bridge_file.exists() else None
        sentry_path = str(self._sentry_risk_file).replace("\\", "/") if self._sentry_risk_file.exists() else None
        sbdb_obj_path = str(self._sbdb_object_file).replace("\\", "/") if self._sbdb_object_file.exists() else None

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
                FROM '{ast_path}'
            )
            WHERE rn = 1
        )
        """

        if bridge_path and sentry_path and sbdb_obj_path:
            query += f""",
            bridge_neows AS (
                SELECT
                    identifier_value AS neows_id,
                    MAX(asteroid_key) AS asteroid_key
                FROM '{bridge_path}'
                WHERE source_system = 'neows'
                  AND identifier_name = 'id'
                GROUP BY identifier_value
            ),
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
                FROM '{sentry_path}'
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
                FROM '{bridge_path}' b
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
                FROM '{bridge_path}'
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
                    FROM '{sbdb_obj_path}'
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
                bn.asteroid_key,
                CASE WHEN bn.asteroid_key IS NOT NULL THEN 'RESOLVED' ELSE 'UNRESOLVED' END AS match_state,
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
            LEFT JOIN bridge_neows bn ON bn.neows_id = n.neows_id
            LEFT JOIN sentry_by_asteroid_key s ON s.asteroid_key = bn.asteroid_key
            LEFT JOIN bridge_sbdb_pivot b_sbdb ON b_sbdb.asteroid_key = bn.asteroid_key
            LEFT JOIN sbdb_info sbdb ON sbdb.spkid = b_sbdb.spkid
            ORDER BY n.miss_distance_km ASC
            """
        else:
            # Fallback if bridge or Sentry files are missing
            query += """
            SELECT
                n.closest_approach_date,
                n.neows_id,
                n.name,
                n.miss_distance_km,
                (n.miss_distance_km / 384400.0) AS miss_distance_lunar,
                n.hazardous,
                CAST(NULL AS VARCHAR) AS asteroid_key,
                'UNRESOLVED' AS match_state,
                FALSE AS is_sentry_monitored,
                FALSE AS is_sentry_ambiguous,
                CAST(NULL AS VARCHAR) AS sentry_id,
                CAST(NULL AS DOUBLE) AS sentry_impact_probability,
                CAST(NULL AS DOUBLE) AS sentry_palermo_scale_max,
                CAST(NULL AS BIGINT) AS sentry_torino_scale_max,
                CAST(NULL AS BIGINT) AS sentry_potential_impacts_count,
                CAST(NULL AS VARCHAR) AS sentry_impact_year_range,
                FALSE AS has_sbdb_characterization,
                CAST(NULL AS VARCHAR) AS sbdb_spkid,
                CAST(NULL AS VARCHAR) AS sbdb_designation,
                CAST(NULL AS VARCHAR) AS sbdb_fullname,
                CAST(NULL AS VARCHAR) AS sbdb_orbit_class_name
            FROM deduped_neows n
            ORDER BY n.miss_distance_km ASC
            """

        df = conn.execute(query).df()
        conn.close()
        return df

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

        # Step 1: Check bridge_asteroid_identifier
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
                    "evidence": "Deterministic exact match in canonical entity bridge.",
                }
            elif len(bridge_rows) > 1:
                conn.close()
                return {
                    "neows_id": clean_id,
                    "match_state": "AMBIGUOUS",
                    "asteroid_key": None,
                    "match_rule": "BRIDGE_MULTIPLE_CANDIDATE_KEYS",
                    "evidence": f"Identifier maps to {len(bridge_rows)} distinct candidate entity keys in bridge.",
                }

        # Step 2: Check fact_entity_resolution audit table
        if self._resolution_file.exists():
            res_path = str(self._resolution_file).replace("\\", "/")
            res_rows = conn.execute(
                f"""
                SELECT match_state, assigned_asteroid_key, match_rule, evidence_json
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
                state, assigned_key, rule, evidence = res_rows[0]
                conn.close()
                if state == "RESOLVED" and assigned_key:
                    return {
                        "neows_id": clean_id,
                        "match_state": "RESOLVED",
                        "asteroid_key": assigned_key,
                        "match_rule": rule,
                        "evidence": evidence or "Resolved via entity resolution audit log.",
                    }
                elif state == "AMBIGUOUS":
                    return {
                        "neows_id": clean_id,
                        "match_state": "AMBIGUOUS",
                        "asteroid_key": None,
                        "match_rule": rule,
                        "evidence": evidence or "Ambiguous multi-source match detected in audit log.",
                    }
                elif state == "INVALID":
                    return {
                        "neows_id": clean_id,
                        "match_state": "INVALID",
                        "asteroid_key": None,
                        "match_rule": rule,
                        "evidence": evidence or "Identifier evaluated as invalid in audit log.",
                    }
                else:
                    return {
                        "neows_id": clean_id,
                        "match_state": "UNRESOLVED",
                        "asteroid_key": None,
                        "match_rule": rule or "NO_CROSS_SOURCE_MATCH",
                        "evidence": "No cross-source link established in entity resolution catalog.",
                    }

        conn.close()
        return {
            "neows_id": clean_id,
            "match_state": "UNRESOLVED",
            "asteroid_key": None,
            "match_rule": "NO_RESOLUTION_RECORD",
            "evidence": "No crosswalk record found for this NeoWs identifier.",
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

        # Step 2: Query latest object snapshot
        obj_data: dict[str, Any] = {}
        if self._sbdb_object_file.exists():
            obj_path = str(self._sbdb_object_file).replace("\\", "/")
            obj_df = conn.execute(
                f"""
                SELECT
                    spkid, designation, fullname, shortname, object_kind,
                    is_neo, is_pha, orbit_class_code, orbit_class_name, orbit_id
                FROM '{obj_path}'
                WHERE spkid = ?
                ORDER BY snapshot_key DESC, snapshot_time DESC, run_id DESC
                LIMIT 1
                """,
                [spkid],
            ).df()
            if not obj_df.empty:
                obj_data = obj_df.iloc[0].to_dict()

        # Step 3: Query latest orbit solution
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
                WHERE spkid = ?
                ORDER BY snapshot_key DESC, snapshot_time DESC, run_id DESC, orbit_id DESC
                LIMIT 1
                """,
                [spkid],
            ).df()
            if not orb_df.empty:
                orb_data = orb_df.iloc[0].to_dict()

        # Step 4: Query Keplerian orbital elements
        elem_data: dict[str, Any] = {
            "eccentricity": None,
            "semi_major_axis_au": None,
            "perihelion_distance_au": None,
            "inclination_deg": None,
        }
        if self._sbdb_elements_file.exists():
            elem_path = str(self._sbdb_elements_file).replace("\\", "/")
            elem_df = conn.execute(
                f"""
                SELECT
                    MAX(CASE WHEN element_name = 'e' THEN element_value END) AS eccentricity,
                    MAX(CASE WHEN element_name = 'a' THEN element_value END) AS semi_major_axis_au,
                    MAX(CASE WHEN element_name = 'q' THEN element_value END) AS perihelion_distance_au,
                    MAX(CASE WHEN element_name = 'i' THEN element_value END) AS inclination_deg
                FROM '{elem_path}'
                WHERE spkid = ?
                """,
                [spkid],
            ).df()
            if not elem_df.empty:
                for col in elem_data:
                    val = elem_df.iloc[0][col]
                    elem_data[col] = float(val) if pd.notna(val) else None

        # Calculate orbital period in years (Kepler's Third Law: T = a^1.5)
        a_au = elem_data.get("semi_major_axis_au")
        orbital_period_yr = float(a_au**1.5) if a_au is not None and a_au > 0 else None

        # Step 5: Query physical parameters
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
                WHERE spkid = ?
                """,
                [spkid],
            ).df()
            if not phys_df.empty:
                for col in phys_data:
                    val = phys_df.iloc[0][col]
                    phys_data[col] = float(val) if pd.notna(val) else None

        # Step 6: Evaluate Astrometric Data Quality Tier
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
            "is_neo": bool(obj_data.get("is_neo", False)),
            "is_pha": bool(obj_data.get("is_pha", False)),
            "orbit_class_code": obj_data.get("orbit_class_code"),
            "orbit_class_name": obj_data.get("orbit_class_name"),
            "orbit_id": orb_data.get("orbit_id") or obj_data.get("orbit_id"),
            "epoch_jd": orb_data.get("epoch_jd"),
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
            "eccentricity": elem_data["eccentricity"],
            "semi_major_axis_au": elem_data["semi_major_axis_au"],
            "perihelion_distance_au": elem_data["perihelion_distance_au"],
            "inclination_deg": elem_data["inclination_deg"],
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
