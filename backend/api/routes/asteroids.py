"""Asteroids threat watchlist endpoint for M6 API.

Adheres strictly to the locked M6.2 contract and M6.4 Slice 1 specifications:
- GET /asteroids
- Query parameters: limit, offset, hazardous, sentry_monitored, horizon_mkm
- Authoritative WatchlistResponse envelope
- Preserves (closest_approach_date, neows_id) event grain
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status
from fastapi.responses import JSONResponse

try:
    from backend.api.schemas import (
        AsteroidDetailResponse,
        AsteroidProfileResponse,
        ErrorDetail,
        ErrorResponse,
        MetaEnvelope,
        SbdbResponse,
        SentryResponse,
        SentryHistoryResponse,
        CrosswalkResponse,
        WatchlistQueryParams,
        WatchlistResponse,
        WorldResponse,
    )
    from backend.api.service import (
        get_asteroid_crosswalk,
        get_asteroid_profile,
        get_asteroid_detail,
        get_asteroid_history,
        get_asteroid_sbdb,
        get_asteroid_sentry,
        get_provider,
        get_watchlist,
        get_world,
    )
    from backend.storage.dashboard_data import DashboardDataProvider
except ImportError:
    from api.schemas import (
        AsteroidDetailResponse,
        AsteroidProfileResponse,
        ErrorDetail,
        ErrorResponse,
        MetaEnvelope,
        SbdbResponse,
        SentryResponse,
        SentryHistoryResponse,
        CrosswalkResponse,
        WatchlistQueryParams,
        WatchlistResponse,
        WorldResponse,
    )
    from api.service import (
        get_asteroid_crosswalk,
        get_asteroid_profile,
        get_asteroid_detail,
        get_asteroid_history,
        get_asteroid_sbdb,
        get_asteroid_sentry,
        get_provider,
        get_watchlist,
        get_world,
    )
    from dashboard_data import DashboardDataProvider

router = APIRouter(tags=["Asteroids"])

# Single source of truth for NeoWs path IDs: positive integers, no leading zeros.
NeowsIdPath = Annotated[str, Path(pattern=r"^[1-9]\d*$", description="NeoWs positive numeric identifier")]


@router.get(
    "/asteroids",
    response_model=WatchlistResponse,
    status_code=status.HTTP_200_OK,
    summary="Threat watchlist close-approach encounters",
    description="Returns filtered, deterministically ordered, and paginated close-approach encounter events.",
)
def get_asteroids(
    params: Annotated[WatchlistQueryParams, Query()],
    provider: DashboardDataProvider = Depends(get_provider),
) -> WatchlistResponse:
    """Return threat watchlist close-approach encounter records."""
    return get_watchlist(provider, params)


# Must be registered before /asteroids/{neows_id}: Starlette matches routes in
# order, so the ID route would otherwise capture "world" and reject it with 422.
@router.get(
    "/asteroids/world",
    response_model=WorldResponse,
    status_code=status.HTTP_200_OK,
    summary="World snapshot: every current NeoWs object in one response",
    description=(
        "Returns one record per NeoWs object with real encounter facts, identity resolution, "
        "SBDB/Sentry availability with provenance, and a deterministic ILLUSTRATIVE direction. "
        "Retrieved with a single set-based query."
    ),
)
def get_asteroids_world(
    provider: DashboardDataProvider = Depends(get_provider),
) -> WorldResponse:
    """Return the full world snapshot for the renderer's initial load."""
    return get_world(provider)


@router.get(
    "/asteroids/{neows_id}",
    response_model=AsteroidDetailResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "model": AsteroidDetailResponse,
            "description": "Authoritative single-object encounter and resolution dossier.",
        },
        404: {
            "model": ErrorResponse,
            "description": "Identifier not found in authoritative NeoWs lakehouse telemetry.",
        },
    },
    summary="Retrieve single asteroid encounter and resolution state",
    description="Returns primary close-approach encounter telemetry and entity resolution state for a given NeoWs identifier.",
)
def get_asteroid(
    neows_id: NeowsIdPath,
    provider: DashboardDataProvider = Depends(get_provider),
) -> AsteroidDetailResponse | JSONResponse:
    """Return primary encounter dossier and resolution state for a specific NeoWs identifier."""
    result = get_asteroid_detail(provider, neows_id)
    if result is None:
        error_payload = ErrorResponse(
            meta=MetaEnvelope(
                api_version="1.0.0",
                execution_mode=provider.get_execution_mode(),
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            error=ErrorDetail(
                code="TARGET_NOT_FOUND",
                message=f"NeoWs asteroid with identifier '{neows_id}' was not found in authoritative lakehouse telemetry.",
            ),
        )
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=error_payload.model_dump())
    return result


@router.get(
    "/asteroids/{neows_id}/sbdb",
    response_model=SbdbResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "model": SbdbResponse,
            "description": "Authoritative JPL SBDB physical and orbital characterization.",
        },
        404: {
            "model": ErrorResponse,
            "description": "Identifier not found in authoritative NeoWs lakehouse telemetry.",
        },
    },
    summary="Retrieve SBDB physical and orbital characterization",
    description="Returns JPL Small-Body Database physical parameters, orbital elements, and quality tier for a NeoWs identifier.",
)
def get_asteroid_sbdb_route(
    neows_id: NeowsIdPath,
    provider: DashboardDataProvider = Depends(get_provider),
) -> SbdbResponse | JSONResponse:
    """Return SBDB physical and orbital characterization for a specific NeoWs identifier."""
    result = get_asteroid_sbdb(provider, neows_id)
    if result is None:
        error_payload = ErrorResponse(
            meta=MetaEnvelope(
                api_version="1.0.0",
                execution_mode=provider.get_execution_mode(),
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            error=ErrorDetail(
                code="TARGET_NOT_FOUND",
                message=f"NeoWs asteroid with identifier '{neows_id}' was not found in authoritative lakehouse telemetry.",
            ),
        )
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=error_payload.model_dump())
    return result


@router.get(
    "/asteroids/{neows_id}/sentry",
    response_model=SentryResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "model": SentryResponse,
            "description": "Authoritative NASA/JPL Sentry Mode S impact risk monitoring profile.",
        },
        404: {
            "model": ErrorResponse,
            "description": "Identifier not found in authoritative NeoWs lakehouse telemetry.",
        },
    },
    summary="Retrieve Sentry impact risk monitoring profile",
    description="Returns NASA/JPL Sentry Mode S impact risk monitoring profile, technical risk scales, and reverse-cardinality ambiguity protection for a NeoWs identifier.",
)
def get_asteroid_sentry_route(
    neows_id: NeowsIdPath,
    provider: DashboardDataProvider = Depends(get_provider),
) -> SentryResponse | JSONResponse:
    """Return Sentry impact risk monitoring profile for a specific NeoWs identifier."""
    result = get_asteroid_sentry(provider, neows_id)
    if result is None:
        error_payload = ErrorResponse(
            meta=MetaEnvelope(
                api_version="1.0.0",
                execution_mode=provider.get_execution_mode(),
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            error=ErrorDetail(
                code="TARGET_NOT_FOUND",
                message=f"NeoWs asteroid with identifier '{neows_id}' was not found in authoritative lakehouse telemetry.",
            ),
        )
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=error_payload.model_dump())
    return result


@router.get(
    "/asteroids/{neows_id}/history",
    response_model=SentryHistoryResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "model": SentryHistoryResponse,
            "description": "Authoritative historical JPL Sentry risk and observation snapshot trajectory.",
        },
        404: {
            "model": ErrorResponse,
            "description": "Identifier not found in authoritative NeoWs lakehouse telemetry.",
        },
    },
    summary="Retrieve Sentry historical risk observation trajectory",
    description="Returns chronological Sentry catalog snapshots and non-causal metric change tracking for a NeoWs identifier.",
)
def get_asteroid_history_route(
    neows_id: NeowsIdPath,
    provider: DashboardDataProvider = Depends(get_provider),
) -> SentryHistoryResponse | JSONResponse:
    """Return Sentry historical risk trajectory for a specific NeoWs identifier."""
    result = get_asteroid_history(provider, neows_id)
    if result is None:
        error_payload = ErrorResponse(
            meta=MetaEnvelope(
                api_version="1.0.0",
                execution_mode=provider.get_execution_mode(),
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            error=ErrorDetail(
                code="TARGET_NOT_FOUND",
                message=f"NeoWs asteroid with identifier '{neows_id}' was not found in authoritative lakehouse telemetry.",
            ),
        )
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=error_payload.model_dump())
    return result


@router.get(
    "/asteroids/{neows_id}/crosswalk",
    response_model=CrosswalkResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "model": CrosswalkResponse,
            "description": "Authoritative multi-source identifier crosswalk mappings.",
        },
        404: {
            "model": ErrorResponse,
            "description": "Identifier not found in authoritative NeoWs lakehouse telemetry.",
        },
    },
    summary="Retrieve multi-source identifier crosswalk",
    description="Returns crosswalk mappings across NeoWs, JPL SBDB, and JPL Sentry for a resolved entity.",
)
def get_asteroid_crosswalk_route(
    neows_id: NeowsIdPath,
    provider: DashboardDataProvider = Depends(get_provider),
) -> CrosswalkResponse | JSONResponse:
    """Return multi-source identifier crosswalk for a specific NeoWs identifier."""
    result = get_asteroid_crosswalk(provider, neows_id)
    if result is None:
        error_payload = ErrorResponse(
            meta=MetaEnvelope(
                api_version="1.0.0",
                execution_mode=provider.get_execution_mode(),
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            error=ErrorDetail(
                code="TARGET_NOT_FOUND",
                message=f"NeoWs asteroid with identifier '{neows_id}' was not found in authoritative lakehouse telemetry.",
            ),
        )
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=error_payload.model_dump())
    return result


@router.get(
    "/asteroids/{neows_id}/profile",
    response_model=AsteroidProfileResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {"model": AsteroidProfileResponse, "description": "Cross-source asteroid profile."},
        404: {"model": ErrorResponse, "description": "Identifier not found in authoritative NeoWs lakehouse telemetry."},
    },
    summary="Retrieve the cross-source asteroid profile",
    description=(
        "Returns identity, orbit, physical, encounter, Sentry linkage and provenance sections for a NeoWs "
        "identifier. Every section names its source; unavailable fields are null with a machine-readable reason."
    ),
)
def get_asteroid_profile_route(
    neows_id: NeowsIdPath,
    provider: DashboardDataProvider = Depends(get_provider),
) -> AsteroidProfileResponse | JSONResponse:
    """Return the cross-source profile for a specific NeoWs identifier."""
    result = get_asteroid_profile(provider, neows_id)
    if result is None:
        error_payload = ErrorResponse(
            meta=MetaEnvelope(
                api_version="1.0.0",
                execution_mode=provider.get_execution_mode(),
                timestamp=datetime.now(timezone.utc).isoformat(),
            ),
            error=ErrorDetail(
                code="TARGET_NOT_FOUND",
                message=f"NeoWs asteroid with identifier '{neows_id}' was not found in authoritative lakehouse telemetry.",
            ),
        )
        return JSONResponse(status_code=status.HTTP_404_NOT_FOUND, content=error_payload.model_dump())
    return result
