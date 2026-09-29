"""FastAPI application entry point for the NASA Planetary Defense Platform.

Provides clean importable `app` object and factory `create_app` for local serving.
"""

from __future__ import annotations

from fastapi import FastAPI

from api.routes.asteroids import router as asteroids_router
from api.routes.health import router as health_router
from dashboard_data import DashboardDataProvider


def create_app(provider: DashboardDataProvider | None = None) -> FastAPI:
    """Create and configure the FastAPI application instance."""
    application = FastAPI(
        title="NASA Planetary Defense Platform API",
        description="Data serving API for Near-Earth Object intelligence, SBDB physical parameters, and Sentry risk data.",
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
    )

    if provider is not None:
        application.state.provider = provider

    application.include_router(health_router)
    application.include_router(asteroids_router)

    return application


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=True)
