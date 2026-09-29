"""Health and readiness probe endpoint for M6 API.

Adheres strictly to the locked M6.2 contract:
- GET /health
- HTTP 200 when active backend is fully operational
- HTTP 503 when critical lakehouse storage or query engine is unavailable
- Uniform response envelope matching HealthResponse schema
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Response, status

from api.schemas import HealthResponse
from api.service import evaluate_health, get_provider
from dashboard_data import DashboardDataProvider

router = APIRouter(tags=["Health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    responses={
        200: {
            "model": HealthResponse,
            "description": "Active backend storage and query engine are ready.",
        },
        503: {
            "model": HealthResponse,
            "description": "Active backend storage or query engine is unavailable.",
        },
    },
    summary="Readiness health check",
    description="Probes local Lakehouse Parquet storage assets and DuckDB query engine readiness.",
)
def get_health(
    response: Response,
    provider: DashboardDataProvider = Depends(get_provider),
) -> HealthResponse:
    """Readiness probe evaluating Lakehouse Parquet assets and DuckDB engine."""
    result = evaluate_health(provider)
    if result.data.status != "healthy":
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return result
