"""Serving adapter and readiness health service for M6.

Encapsulates data provider interaction, storage readiness probes,
and query engine health checks while adhering to the locked M6.2 contract.
"""

from __future__ import annotations

from datetime import datetime, timezone
import logging
from pathlib import Path
from typing import Any

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
)
from dashboard_data import DashboardDataProvider

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
