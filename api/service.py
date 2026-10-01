"""Serving adapter and readiness health service for M6.

Encapsulates data provider interaction, storage readiness probes,
and query engine health checks while adhering to the locked M6.2 contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import logging
import math
from pathlib import Path
from typing import Any, Iterable

import duckdb
from fastapi import Request
import pandas as pd

from api.schemas import (
    HealthChecks,
    HealthData,
    HealthResponse,
    MetaEnvelope,
    PaginationEnvelope,
    WatchlistAsteroid,
    WatchlistQueryParams,
    WatchlistResponse,
    AsteroidDetail,
    AsteroidDetailResponse,
    ResolutionEnvelope,
    SbdbProfile,
    SbdbResponse,
    SentryProfile,
    SentryResponse,
    SentryHistoryRecord,
    SentryHistoryResponse,
    HistoryResolutionEnvelope,
    CrosswalkRecord,
    CrosswalkResponse,
    AsteroidProfile,
    AsteroidProfileResponse,
    NeowsProvenance,
    ProfileEncounter,
    ProfileIdentity,
    ProfileNeowsPhysical,
    ProfileOrbit,
    ProfilePhysical,
    ProfileProvenance,
    ProfileSentryLinkage,
    ResolutionProvenance,
    SbdbProvenance,
    SectionAvailability,
    SentryAssessment,
    SentryProvenance,
    IllustrativeDirection,
    WorldAsteroid,
    WorldEncounter,
    WorldResolution,
    WorldResponse,
    WorldSbdbAvailability,
    WorldSentryAvailability,
    WorldSnapshotInfo,
    WorldSpatialModel,
)
from dashboard_data import _SENTRY_ASSESSMENT_COLUMNS, DashboardDataProvider, _nullable_bool

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent

REQUIRED_PARQUET_ASSETS: tuple[str, ...] = (
    "asteroids.parquet",
    "bridge_asteroid_identifier.parquet",
    "fact_sentry_risk_snapshot.parquet",
    "fact_sbdb_object_snapshot.parquet",
)

_default_provider: DashboardDataProvider | None = None


def get_default_provider() -> DashboardDataProvider:
    """Return or initialize the singleton local DashboardDataProvider."""
    global _default_provider
    if _default_provider is None:
        _default_provider = DashboardDataProvider(
            base_dir=PROJECT_ROOT,
            execution_mode="LOCAL",
        )
    return _default_provider


def get_provider(request: Request) -> DashboardDataProvider:
    """FastAPI dependency to retrieve the active DashboardDataProvider."""
    if hasattr(request.app.state, "provider") and request.app.state.provider is not None:
        return request.app.state.provider
    return get_default_provider()


def check_lakehouse_storage(provider: DashboardDataProvider) -> bool:
    """Verify that all four required local Parquet assets exist on disk.

    Only probes the active LOCAL execution mode storage without scanning data.
    """
    base_dir: Path
    underlying = getattr(provider, "_provider", None)
    if underlying is not None and hasattr(underlying, "base_dir"):
        base_dir = Path(underlying.base_dir).resolve()
    else:
        base_dir = PROJECT_ROOT

    for asset_name in REQUIRED_PARQUET_ASSETS:
        asset_file = base_dir / asset_name
        if not asset_file.is_file():
            logger.warning(
                "Health readiness probe failed: required storage asset '%s' not found at '%s'",
                asset_name,
                asset_file,
            )
            return False
    return True


def check_query_engine() -> bool:
    """Verify DuckDB query engine readiness using SELECT 1.

    Executes a deterministic, lightweight check and cleanly closes resources.
    """
    conn: Any = None
    try:
        conn = duckdb.connect(":memory:")
        res = conn.execute("SELECT 1").fetchall()
        return bool(res and res[0][0] == 1)
    except Exception as exc:
        logger.warning("Health readiness probe failed: query engine check error: %s", exc)
        return False
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def evaluate_health(provider: DashboardDataProvider) -> HealthResponse:
    """Evaluate subsystem readiness and construct the authoritative HealthResponse."""
    storage_ok = check_lakehouse_storage(provider)
    query_engine_ok = check_query_engine()

    is_healthy = storage_ok and query_engine_ok
    status_str = "healthy" if is_healthy else "unavailable"

    meta = MetaEnvelope(
        api_version="1.0.0",
        execution_mode=provider.get_execution_mode(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    data = HealthData(
        status=status_str,
        checks=HealthChecks(
            lakehouse_storage=storage_ok,
            query_engine=query_engine_ok,
        ),
    )

    return HealthResponse(meta=meta, data=data)


def get_watchlist(
    provider: DashboardDataProvider,
    params: WatchlistQueryParams,
) -> WatchlistResponse:
    """Retrieve, filter, deterministically order, and paginate threat watchlist encounters.

    Preserves the locked (closest_approach_date, neows_id) event grain.
    """
    df = provider.get_threat_watchlist()

    # 1. Apply approved filters
    if params.hazardous is not None:
        df = df[df["hazardous"] == params.hazardous]

    if params.sentry_monitored is not None:
        df = df[df["is_sentry_monitored"] == params.sentry_monitored]

    if params.horizon_mkm is not None:
        threshold_km = float(params.horizon_mkm) * 1_000_000.0
        df = df[df["miss_distance_km"] <= threshold_km]

    # 2. Deterministic ordering: miss_distance_km ASC, closest_approach_date ASC, neows_id ASC
    if not df.empty:
        df = df.sort_values(
            by=["miss_distance_km", "closest_approach_date", "neows_id"],
            ascending=[True, True, True],
            kind="mergesort",
        )

    # 3. Calculate total BEFORE pagination
    total = len(df)

    # 4. Apply pagination slice
    df_page = df.iloc[params.offset : params.offset + params.limit]

    # 5. Clean numpy NaN/NaT/pd.NA into Python None for genuine JSON nulls
    clean_df = df_page.astype(object).where(pd.notnull(df_page), None)
    records = [
        WatchlistAsteroid.model_validate(r)
        for r in clean_df.to_dict(orient="records")
    ]

    # 6. Assemble standard response envelope
    meta = MetaEnvelope(
        api_version="1.0.0",
        execution_mode=provider.get_execution_mode(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    pagination = PaginationEnvelope(
        total=total,
        limit=params.limit,
        offset=params.offset,
        returned=len(records),
    )

    return WatchlistResponse(meta=meta, data=records, pagination=pagination)


def get_asteroid_detail(
    provider: DashboardDataProvider,
    neows_id: str,
) -> AsteroidDetailResponse | None:
    """Retrieve single asteroid encounter and entity resolution state.

    Grain: (neows_id).
    Selects primary encounter via CLOSEST_OBSERVED_APPROACH (min miss_distance_km, tie-breaker closest_approach_date ASC).
    Returns None if neows_id is absent from authoritative NeoWs lakehouse telemetry.
    """
    df = provider.get_threat_watchlist()
    if df.empty:
        return None

    encounters = df[df["neows_id"] == str(neows_id)]
    if encounters.empty:
        return None

    # Step 1: Approaches recorded count
    approaches_count = len(encounters)

    # Step 2: Deterministic primary encounter selection:
    # miss_distance_km ASC, tie-breaker closest_approach_date ASC
    encounters_sorted = encounters.sort_values(
        by=["miss_distance_km", "closest_approach_date"],
        ascending=[True, True],
        kind="mergesort",
    )
    primary_row = encounters_sorted.iloc[0]

    # Step 3: Clean null values to Python None for valid JSON null serialization
    clean_dict = {
        k: (None if pd.isna(v) else v)
        for k, v in primary_row.to_dict().items()
    }

    # Step 4: Retrieve authoritative entity resolution state from provider
    res_state = provider.get_resolution_state(str(neows_id))
    resolution = ResolutionEnvelope(
        match_state=res_state.get("match_state", "UNRESOLVED"),
        asteroid_key=res_state.get("asteroid_key"),
        match_rule=res_state.get("match_rule"),
        evidence=res_state.get("evidence"),
        resolved_at=res_state.get("resolved_at"),
    )

    # Step 5: Construct primary encounter object detail, synchronizing cross-source resolution state
    detail_data = {
        "neows_id": clean_dict["neows_id"],
        "name": clean_dict["name"],
        "closest_approach_date": clean_dict["closest_approach_date"],
        "miss_distance_km": clean_dict["miss_distance_km"],
        "miss_distance_lunar": clean_dict["miss_distance_lunar"],
        "hazardous": clean_dict["hazardous"],
        "approaches_recorded_count": approaches_count,
        "selection_rule": "CLOSEST_OBSERVED_APPROACH",
        "asteroid_key": resolution.asteroid_key,
        "match_state": resolution.match_state,
        "is_sentry_monitored": clean_dict["is_sentry_monitored"],
        "is_sentry_ambiguous": clean_dict["is_sentry_ambiguous"],
        "sentry_id": clean_dict.get("sentry_id"),
        "has_sbdb_characterization": clean_dict["has_sbdb_characterization"],
        "sbdb_spkid": clean_dict.get("sbdb_spkid"),
        "sbdb_designation": clean_dict.get("sbdb_designation"),
        "sbdb_fullname": clean_dict.get("sbdb_fullname"),
        "sbdb_orbit_class_name": clean_dict.get("sbdb_orbit_class_name"),
    }
    detail = AsteroidDetail.model_validate(detail_data)

    # Step 6: Construct standard envelope
    meta = MetaEnvelope(
        api_version="1.0.0",
        execution_mode=provider.get_execution_mode(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    return AsteroidDetailResponse(meta=meta, data=detail, resolution=resolution)


def get_asteroid_sbdb(
    provider: DashboardDataProvider,
    neows_id: str,
) -> SbdbResponse | None:
    """Retrieve JPL SBDB physical and orbital profile for a specific NeoWs identifier.

    Canonical identity path: NeoWs ID -> internal asteroid_key -> SBDB SPKID -> SBDB profile.
    Returns None if neows_id is absent from authoritative NeoWs lakehouse telemetry (404).
    Returns data=None if identity is UNRESOLVED, AMBIGUOUS, or SBDB profile is unavailable (200).
    """
    df = provider.get_threat_watchlist()
    if df.empty:
        return None

    encounters = df[df["neows_id"] == str(neows_id)]
    if encounters.empty:
        return None

    # Retrieve authoritative resolution state from provider
    res_state = provider.get_resolution_state(str(neows_id))
    resolution = ResolutionEnvelope(
        match_state=res_state.get("match_state", "UNRESOLVED"),
        asteroid_key=res_state.get("asteroid_key"),
        match_rule=res_state.get("match_rule"),
        evidence=res_state.get("evidence"),
        resolved_at=res_state.get("resolved_at"),
    )

    sbdb_data: SbdbProfile | None = None
    if resolution.match_state == "RESOLVED" and resolution.asteroid_key:
        raw_profile = provider.get_sbdb_profile(resolution.asteroid_key)
        if raw_profile:
            clean_profile = {
                k: (None if pd.isna(v) else v)
                for k, v in raw_profile.items()
            }
            sbdb_data = SbdbProfile.model_validate(clean_profile)

    meta = MetaEnvelope(
        api_version="1.0.0",
        execution_mode=provider.get_execution_mode(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    return SbdbResponse(meta=meta, data=sbdb_data, resolution=resolution)


def get_asteroid_sentry(
    provider: DashboardDataProvider,
    neows_id: str,
) -> SentryResponse | None:
    """Retrieve NASA/JPL Sentry impact monitoring profile for a specific NeoWs identifier.

    Canonical identity path: NeoWs ID -> internal asteroid_key -> Sentry ID -> Sentry profile.
    Returns None if neows_id is absent from authoritative NeoWs lakehouse telemetry (404).
    Returns data=None if identity is UNRESOLVED, AMBIGUOUS, or Sentry profile is unavailable (200).
    """
    df = provider.get_threat_watchlist()
    if df.empty:
        return None

    encounters = df[df["neows_id"] == str(neows_id)]
    if encounters.empty:
        return None

    # Retrieve authoritative resolution state from provider
    res_state = provider.get_resolution_state(str(neows_id))
    resolution = ResolutionEnvelope(
        match_state=res_state.get("match_state", "UNRESOLVED"),
        asteroid_key=res_state.get("asteroid_key"),
        match_rule=res_state.get("match_rule"),
        evidence=res_state.get("evidence"),
        resolved_at=res_state.get("resolved_at"),
    )

    sentry_data: SentryProfile | None = None
    if resolution.match_state == "RESOLVED" and resolution.asteroid_key:
        raw_profile = provider.get_sentry_profile(resolution.asteroid_key)
        if raw_profile:
            clean_profile = {
                k: (None if pd.isna(v) else v)
                for k, v in raw_profile.items()
            }
            sentry_data = SentryProfile.model_validate(clean_profile)

    meta = MetaEnvelope(
        api_version="1.0.0",
        execution_mode=provider.get_execution_mode(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    return SentryResponse(meta=meta, data=sentry_data, resolution=resolution)


def get_asteroid_history(
    provider: DashboardDataProvider,
    neows_id: str,
) -> SentryHistoryResponse | None:
    """Retrieve authoritative historical Sentry risk trajectory for a specific NeoWs identifier.

    Canonical identity path:
        NeoWs ID -> Resolution State -> asteroid_key -> Sentry linkage -> sentry_id -> provider.get_historical_risk(sentry_id)

    Returns None if neows_id is absent from authoritative NeoWs lakehouse telemetry (404).
    Returns data=None if entity resolution is UNRESOLVED, AMBIGUOUS, unmonitored, or history unavailable (200).
    Returns data=[] if entity is resolved with unique Sentry linkage but has zero historical snapshots (200).
    Returns data=[records] if entity is resolved with unique Sentry linkage and snapshots exist (200).
    """
    df = provider.get_threat_watchlist()
    if df.empty:
        return None

    encounters = df[df["neows_id"] == str(neows_id)]
    if encounters.empty:
        return None

    # Step 1: Retrieve authoritative entity resolution state from provider
    res_state = provider.get_resolution_state(str(neows_id))
    match_state = res_state.get("match_state", "UNRESOLVED")
    asteroid_key = res_state.get("asteroid_key")

    meta = MetaEnvelope(
        api_version="1.0.0",
        execution_mode=provider.get_execution_mode(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Step 2: Handle UNRESOLVED or non-RESOLVED state
    if match_state != "RESOLVED" or not asteroid_key:
        resolution = HistoryResolutionEnvelope(
            match_state=match_state,
            asteroid_key=None,
            match_rule=res_state.get("match_rule"),
            evidence=res_state.get("evidence"),
            resolved_at=res_state.get("resolved_at"),
            is_sentry_ambiguous=False,
            warning=None,
            notes=None,
        )
        return SentryHistoryResponse(meta=meta, data=None, resolution=resolution)

    # Step 3: Entity is RESOLVED — query Sentry linkage from provider
    sentry_profile = provider.get_sentry_profile(asteroid_key)
    if sentry_profile is None:
        resolution = HistoryResolutionEnvelope(
            match_state="RESOLVED",
            asteroid_key=asteroid_key,
            match_rule=res_state.get("match_rule"),
            evidence=res_state.get("evidence"),
            resolved_at=res_state.get("resolved_at"),
            is_sentry_ambiguous=False,
            warning="Sentry profile unavailable for resolved entity.",
            notes="Sentry profile unavailable for resolved entity.",
        )
        return SentryHistoryResponse(meta=meta, data=None, resolution=resolution)

    # Step 4: Handle unmonitored entity
    if not sentry_profile.get("has_sentry_monitoring", False):
        resolution = HistoryResolutionEnvelope(
            match_state="RESOLVED",
            asteroid_key=asteroid_key,
            match_rule=res_state.get("match_rule"),
            evidence=res_state.get("evidence"),
            resolved_at=res_state.get("resolved_at"),
            is_sentry_ambiguous=False,
            warning=None,
            notes=None,
        )
        return SentryHistoryResponse(meta=meta, data=None, resolution=resolution)

    # Step 5: Handle ambiguous Sentry linkage (multiple Sentry IDs mapped)
    if sentry_profile.get("is_sentry_ambiguous", False) or sentry_profile.get("sentry_identifier_count", 0) > 1:
        ambiguity_msg = "Historical identity cannot be uniquely established due to ambiguous Sentry linkage."
        resolution = HistoryResolutionEnvelope(
            match_state="RESOLVED",
            asteroid_key=asteroid_key,
            match_rule=res_state.get("match_rule"),
            evidence=res_state.get("evidence"),
            resolved_at=res_state.get("resolved_at"),
            is_sentry_ambiguous=True,
            warning=ambiguity_msg,
            notes=ambiguity_msg,
        )
        return SentryHistoryResponse(meta=meta, data=None, resolution=resolution)

    # Step 6: Valid unique Sentry linkage — retrieve historical risk
    sentry_id = sentry_profile.get("sentry_id")
    if not sentry_id:
        resolution = HistoryResolutionEnvelope(
            match_state="RESOLVED",
            asteroid_key=asteroid_key,
            match_rule=res_state.get("match_rule"),
            evidence=res_state.get("evidence"),
            resolved_at=res_state.get("resolved_at"),
            is_sentry_ambiguous=False,
            warning="Sentry ID unavailable for resolved entity.",
            notes="Sentry ID unavailable for resolved entity.",
        )
        return SentryHistoryResponse(meta=meta, data=None, resolution=resolution)

    hist_df = provider.get_historical_risk(sentry_id)

    # Zero snapshots: return empty collection []
    if hist_df.empty:
        history_records: list[SentryHistoryRecord] = []
    else:
        # Clean Pandas nulls into genuine Python None for JSON null serialization
        clean_df = hist_df.astype(object).where(pd.notnull(hist_df), None)
        history_records = [
            SentryHistoryRecord.model_validate(r)
            for r in clean_df.to_dict(orient="records")
        ]

    resolution = HistoryResolutionEnvelope(
        match_state="RESOLVED",
        asteroid_key=asteroid_key,
        match_rule=res_state.get("match_rule"),
        evidence=res_state.get("evidence"),
        resolved_at=res_state.get("resolved_at"),
        is_sentry_ambiguous=False,
        warning=None,
        notes=None,
    )

    return SentryHistoryResponse(meta=meta, data=history_records, resolution=resolution)


def get_asteroid_crosswalk(
    provider: DashboardDataProvider,
    neows_id: str,
) -> CrosswalkResponse | None:
    """Retrieve authoritative multi-source identifier crosswalk for a specific NeoWs identifier.

    Canonical identity path:
        NeoWs ID -> Resolution State -> asteroid_key -> provider.get_crosswalk(asteroid_key)

    Returns None if neows_id is absent from authoritative NeoWs lakehouse telemetry (404).
    Returns data=[] if entity resolution is UNRESOLVED or AMBIGUOUS (200).
    Returns data=[] if entity is resolved but has zero crosswalk records or provider is unavailable (200).
    Returns data=[records] if entity is resolved with crosswalk mappings (200).
    """
    df = provider.get_threat_watchlist()
    if df.empty:
        return None

    encounters = df[df["neows_id"] == str(neows_id)]
    if encounters.empty:
        return None

    # Step 1: Retrieve authoritative entity resolution state from provider
    res_state = provider.get_resolution_state(str(neows_id))
    match_state = res_state.get("match_state", "UNRESOLVED")
    asteroid_key = res_state.get("asteroid_key")

    meta = MetaEnvelope(
        api_version="1.0.0",
        execution_mode=provider.get_execution_mode(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )

    # Step 2: Handle UNRESOLVED or non-RESOLVED state (e.g. AMBIGUOUS)
    if match_state != "RESOLVED" or not asteroid_key:
        resolution = ResolutionEnvelope(
            match_state=match_state,
            asteroid_key=None,
            match_rule=res_state.get("match_rule"),
            evidence=res_state.get("evidence"),
            resolved_at=res_state.get("resolved_at"),
        )
        return CrosswalkResponse(meta=meta, data=[], resolution=resolution)

    # Step 3: Entity is RESOLVED — query crosswalk from provider
    try:
        cw_df = provider.get_crosswalk(asteroid_key)
    except Exception:
        cw_df = pd.DataFrame()

    if cw_df is None or not isinstance(cw_df, pd.DataFrame) or cw_df.empty:
        records: list[CrosswalkRecord] = []
    else:
        # Clean Pandas nulls into Python None
        clean_df = cw_df.astype(object).where(pd.notnull(cw_df), None)
        records = [
            CrosswalkRecord.model_validate(r)
            for r in clean_df.to_dict(orient="records")
        ]

    resolution = ResolutionEnvelope(
        match_state="RESOLVED",
        asteroid_key=asteroid_key,
        match_rule=res_state.get("match_rule"),
        evidence=res_state.get("evidence"),
        resolved_at=res_state.get("resolved_at"),
    )

    return CrosswalkResponse(meta=meta, data=records, resolution=resolution)


ILLUSTRATIVE_DIRECTION_ALGORITHM = "sha256-uniform-sphere-v1"
_DIRECTION_SALT = "nasa-asteroid-world/illustrative-direction/v1:"


def illustrative_direction(neows_id: str) -> tuple[float, float, float]:
    """Map a NeoWs ID to a deterministic ILLUSTRATIVE unit vector, uniform on the sphere.

    NeoWs publishes no 3D direction, so this is a visualization device only. Two
    64-bit uniforms from SHA-256(salt + neows_id) feed Archimedes' equal-area map
    (z uniform in [-1, 1], azimuth uniform in [0, 2pi)). The seed is neows_id, not
    asteroid_key, so an object does not move when its identity later resolves.
    Changing this function changes every position: bump the algorithm version.
    """
    digest = hashlib.sha256((_DIRECTION_SALT + neows_id).encode("utf-8")).digest()
    u = int.from_bytes(digest[0:8], "big") / 2**64
    v = int.from_bytes(digest[8:16], "big") / 2**64
    z = 1.0 - 2.0 * u
    r = math.sqrt(max(0.0, 1.0 - z * z))
    phi = 2.0 * math.pi * v
    return (r * math.cos(phi), r * math.sin(phi), z)


def _world_sbdb(row: dict[str, Any]) -> WorldSbdbAvailability:
    if row["match_state"] != "RESOLVED":
        return WorldSbdbAvailability(status="not_resolved")
    if row["sbdb_spkid"] is None or row["sbdb_snapshot_key"] is None:
        return WorldSbdbAvailability(status="not_present", spkid=row["sbdb_spkid"])
    return WorldSbdbAvailability(
        status="available",
        spkid=row["sbdb_spkid"],
        snapshot_key=row["sbdb_snapshot_key"],
        run_id=row["sbdb_run_id"],
    )


def _world_sentry(row: dict[str, Any], latest_catalog_key: str | None) -> WorldSentryAvailability:
    if row["match_state"] != "RESOLVED":
        return WorldSentryAvailability(status="not_resolved")
    link_count = int(row["sentry_link_count"] or 0)
    if link_count == 0:
        return WorldSentryAvailability(status="not_present")
    if link_count > 1:
        return WorldSentryAvailability(status="ambiguous")
    if row["sentry_snapshot_key"] is None:
        return WorldSentryAvailability(status="linked_no_record", sentry_id=row["sentry_id"])
    return WorldSentryAvailability(
        status="available",
        sentry_id=row["sentry_id"],
        latest_snapshot_key=row["sentry_snapshot_key"],
        run_id=row["sentry_run_id"],
        in_latest_catalog=row["sentry_snapshot_key"] == latest_catalog_key,
    )


def _world_rows(snapshot: dict[str, Any]) -> tuple[list[dict[str, Any]], str | None]:
    """Normalize provider world rows to plain dicts with genuine None, plus the latest Sentry catalog key."""
    df = snapshot["records"]
    rows = [] if df.empty else df.astype(object).where(pd.notnull(df), None).to_dict(orient="records")
    latest_catalog_key = rows[0]["sentry_latest_catalog_snapshot_key"] if rows else None
    return rows, latest_catalog_key


def _world_record(row: dict[str, Any], latest_catalog_key: str | None) -> WorldAsteroid:
    """Single transformation of a world row; shared by the world snapshot and the profile."""
    x, y, z = illustrative_direction(str(row["neows_id"]))
    return WorldAsteroid(
        neows_id=row["neows_id"],
        name=row["name"],
        asteroid_key=row["asteroid_key"] if row["match_state"] == "RESOLVED" else None,
        encounter=WorldEncounter(
            closest_approach_date=row["closest_approach_date"],
            miss_distance_km=row["miss_distance_km"],
            is_potentially_hazardous=_nullable_bool(row["hazardous"]),
            close_approach_datetime=row["close_approach_datetime"],
            relative_velocity_km_s=row["relative_velocity_km_s"],
            estimated_diameter_min_km=row["estimated_diameter_min_km"],
            estimated_diameter_max_km=row["estimated_diameter_max_km"],
        ),
        resolution=WorldResolution(
            match_state=row["match_state"],
            match_rule=row["match_rule"],
            resolved_at=row["resolved_at"],
        ),
        sbdb=_world_sbdb(row),
        sentry=_world_sentry(row, latest_catalog_key),
        illustrative_direction=IllustrativeDirection(x=x, y=y, z=z),
    )


def get_world(provider: DashboardDataProvider) -> WorldResponse:
    """Build the world snapshot from ONE set-based provider retrieval.

    Pure in-memory transformation per row: no per-object provider calls or queries.
    """
    snapshot = provider.get_world_snapshot()
    rows, latest_catalog_key = _world_rows(snapshot)
    records = [_world_record(row, latest_catalog_key) for row in rows]

    meta = MetaEnvelope(
        api_version="1.0.0",
        execution_mode=provider.get_execution_mode(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    world = WorldSnapshotInfo(
        object_count=len(records),
        neows_run_id=snapshot["neows_run_id"],
        sentry_latest_catalog_snapshot_key=latest_catalog_key,
        neows_fields_not_in_dataset=snapshot["neows_missing_columns"],
        spatial_model=WorldSpatialModel(
            direction_algorithm=ILLUSTRATIVE_DIRECTION_ALGORITHM,
            note=(
                "Illustrative direction, real distance. Directions are derived deterministically from "
                "neows_id for visualization only and are not astronomical positions or trajectories."
            ),
        ),
    )
    return WorldResponse(meta=meta, world=world, data=records)


_PROFILE_ORBIT_FIELDS = (
    "orbit_class_code", "orbit_class_name", "is_neo", "is_pha", "orbit_id", "epoch_jd", "equinox",
    "semi_major_axis_au", "eccentricity", "perihelion_distance_au", "aphelion_distance_au", "inclination_deg",
    "ascending_node_longitude_deg", "argument_of_perihelion_deg", "mean_anomaly_deg", "mean_motion_deg_per_day",
    "orbital_period_days", "time_of_perihelion_jd_tdb", "soln_date", "first_obs", "last_obs", "data_arc_days",
    "n_obs_used", "condition_code", "rms", "earth_moid_au", "jupiter_moid_au", "t_jup",
)
_PROFILE_PHYSICAL_FIELDS = ("absolute_magnitude", "estimated_diameter_km", "albedo", "rotational_period_hr")


def _section_availability(
    values: dict[str, Any],
    missing_reason: str | None,
    not_in_contract: Iterable[str] = (),
) -> SectionAvailability:
    """Report null fields and why: `missing_reason` if the whole source is unlinked, else not_in_source.

    Fields in `not_in_contract` are null because the stored dataset predates them.
    """
    not_in_contract = set(not_in_contract)
    unavailable = {
        field: ("not_in_current_contract" if field in not_in_contract else (missing_reason or "not_in_source"))
        for field, value in values.items() if value is None
    }
    if not unavailable:
        status = "available"
    elif len(unavailable) == len(values):
        status = "unavailable"
    else:
        status = "partial"
    return SectionAvailability(status=status, unavailable=unavailable)


# Single definition lives with the query that selects them (dashboard_data._SENTRY_ASSESSMENT_COLUMNS).
_SENTRY_ASSESSMENT_FIELDS = tuple(_SENTRY_ASSESSMENT_COLUMNS)
_SENTRY_INT_FIELDS = tuple(col for col, sql_type in _SENTRY_ASSESSMENT_COLUMNS.items() if sql_type == "BIGINT")
# Why the assessment is empty, by linkage status. Membership comes only from the crosswalk.
_SENTRY_UNAVAILABLE_REASON = {
    "not_resolved": "not_resolved",
    "not_present": "not_in_source",
    "ambiguous": "ambiguous_linkage",
    "linked_no_record": "not_in_source",
}


def _sentry_assessment(row: dict[str, Any], status: str) -> SentryAssessment:
    """Published Mode S values from the single latest record already selected by the world query.

    Values are copied, never computed. Ingestion stores a missing 'range' as "", served here as null.
    """
    values: dict[str, Any] = dict.fromkeys(_SENTRY_ASSESSMENT_FIELDS)
    if status == "available":
        for field in _SENTRY_ASSESSMENT_FIELDS:
            value = row[f"sentry_{field}"]
            if value == "":
                value = None
            if value is not None and field in _SENTRY_INT_FIELDS:
                value = int(value)
            values[field] = value
    availability = _section_availability(values, _SENTRY_UNAVAILABLE_REASON.get(status))
    return SentryAssessment(**values, availability=availability)


def get_asteroid_profile(
    provider: DashboardDataProvider,
    neows_id: str,
) -> AsteroidProfileResponse | None:
    """Build the cross-source profile for one NeoWs object.

    Reuses, rather than re-implements: the world query (filtered to this ID) for
    identity, encounter, canonical resolution and SBDB/Sentry availability; the
    Step 3 coherent-snapshot SBDB profile for orbit + physical; and the crosswalk.
    The Sentry assessment is the Mode S record that same query selected, so its
    values and provenance come from one row with no extra retrieval.
    At most three provider retrievals, regardless of how many sources are linked.
    Returns None if neows_id is absent from NeoWs (404).
    """
    snapshot = provider.get_world_snapshot(neows_id)
    rows, latest_catalog_key = _world_rows(snapshot)
    if not rows:
        return None
    world = _world_record(rows[0], latest_catalog_key)
    resolved = world.resolution.match_state == "RESOLVED"
    sbdb_reason = None if world.sbdb.status == "available" else ("not_resolved" if not resolved else "not_in_source")

    sbdb: dict[str, Any] = {}
    if world.sbdb.status == "available":
        raw = provider.get_sbdb_profile(world.asteroid_key)
        if raw:
            sbdb = {k: (None if not isinstance(v, (list, dict)) and pd.isna(v) else v) for k, v in raw.items()}
        else:
            sbdb_reason = "not_in_source"

    crosswalk: list[CrosswalkRecord] = []
    if resolved:
        cw_df = provider.get_crosswalk(world.asteroid_key)
        if cw_df is not None and not cw_df.empty:
            clean = cw_df.astype(object).where(pd.notnull(cw_df), None)
            crosswalk = [CrosswalkRecord.model_validate(r) for r in clean.to_dict(orient="records")]

    identity_values = {
        "asteroid_key": world.asteroid_key,
        "sbdb_spkid": world.sbdb.spkid,
        "sbdb_designation": sbdb.get("designation"),
        "sbdb_fullname": sbdb.get("fullname"),
        "sentry_id": world.sentry.sentry_id,
    }
    identity_availability = _section_availability(identity_values, None if resolved else "not_resolved")
    if world.sbdb.spkid is not None and not sbdb:
        for field in ("sbdb_designation", "sbdb_fullname"):
            identity_availability.unavailable[field] = "not_in_source"
    if world.sentry.status == "ambiguous":
        identity_availability.unavailable["sentry_id"] = "ambiguous_linkage"

    orbit_values = {field: sbdb.get(field) for field in _PROFILE_ORBIT_FIELDS}
    physical_values = {field: sbdb.get(field) for field in _PROFILE_PHYSICAL_FIELDS}
    row = rows[0]
    neows_missing = snapshot["neows_missing_columns"]
    encounter_values = {
        "is_potentially_hazardous": world.encounter.is_potentially_hazardous,
        "close_approach_datetime": row["close_approach_datetime"],
        "close_approach_epoch_ms": None if row["close_approach_epoch_ms"] is None else int(row["close_approach_epoch_ms"]),
        "relative_velocity_km_s": row["relative_velocity_km_s"],
        "is_sentry_object": _nullable_bool(row["is_sentry_object"]),
    }
    neows_physical_values = {
        field: row[field] for field in ("absolute_magnitude_h", "estimated_diameter_min_km", "estimated_diameter_max_km")
    }

    linked = world.sentry.status in ("available", "linked_no_record")
    profile = AsteroidProfile(
        neows_id=world.neows_id,
        identity=ProfileIdentity(
            neows_id=world.neows_id,
            name=world.name,
            match_state=world.resolution.match_state,
            crosswalk=crosswalk,
            availability=identity_availability,
            **identity_values,
        ),
        orbit=ProfileOrbit(**orbit_values, availability=_section_availability(orbit_values, sbdb_reason)),
        physical=ProfilePhysical(**physical_values, availability=_section_availability(physical_values, sbdb_reason)),
        neows_physical=ProfileNeowsPhysical(
            **neows_physical_values,
            availability=_section_availability(neows_physical_values, None, neows_missing),
        ),
        encounter=ProfileEncounter(
            closest_approach_date=world.encounter.closest_approach_date,
            miss_distance_km=world.encounter.miss_distance_km,
            availability=_section_availability(encounter_values, None, neows_missing),
            **encounter_values,
        ),
        sentry=ProfileSentryLinkage(
            status=world.sentry.status,
            sentry_id=world.sentry.sentry_id,
            in_latest_catalog=world.sentry.in_latest_catalog,
            assessment=_sentry_assessment(rows[0], world.sentry.status),
            assessment_endpoint=f"/asteroids/{world.neows_id}/sentry" if linked else None,
        ),
        provenance=ProfileProvenance(
            neows=NeowsProvenance(dataset_run_id=snapshot["neows_run_id"]),
            resolution=ResolutionProvenance(
                match_rule=world.resolution.match_rule,
                resolved_at=world.resolution.resolved_at,
            ),
            sbdb=SbdbProvenance(
                spkid=sbdb.get("spkid") or world.sbdb.spkid,
                snapshot_key=sbdb.get("snapshot_key"),
                run_id=sbdb.get("run_id"),
                snapshot_time=sbdb.get("snapshot_time"),
            ),
            sentry=SentryProvenance(
                sentry_id=world.sentry.sentry_id,
                latest_snapshot_key=world.sentry.latest_snapshot_key,
                run_id=world.sentry.run_id,
                snapshot_time=rows[0]["sentry_snapshot_time"] if world.sentry.status == "available" else None,
                latest_catalog_snapshot_key=latest_catalog_key,
            ),
        ),
    )

    meta = MetaEnvelope(
        api_version="1.0.0",
        execution_mode=provider.get_execution_mode(),
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    return AsteroidProfileResponse(meta=meta, data=profile)
