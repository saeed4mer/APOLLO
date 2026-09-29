"""FastAPI application entry point for the NASA Planetary Defense Platform.

Provides clean importable `app` object and factory `create_app` for local serving.
"""

from __future__ import annotations

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import FastAPI

from api.routes.asteroids import router as asteroids_router
from api.routes.health import router as health_router
from dashboard_data import DashboardDataProvider
from entity_resolution import BRIDGE_ASTEROID_IDENTIFIER_SCHEMA
from nasa_asteroids import ASTEROID_SCHEMA
from nasa_sbdb import SBDB_OBJECT_SCHEMA
from nasa_sentry import SENTRY_RISK_SNAPSHOT_SCHEMA


def ensure_default_lakehouse(base_dir: Path | str | None = None) -> Path:
    """Create the minimum local Parquet lakehouse required for a healthy default app.

    CI and fresh clones do not ship the generated Parquet artifacts. The default
    app instance is expected to be importable without an explicit provider, so we
    create the required empty-but-schema-valid files the first time the module is
    imported. This keeps the health endpoint deterministic and avoids a false
    503 for a clean repository checkout.
    """
    resolved_dir = Path(base_dir).resolve() if base_dir is not None else Path(__file__).resolve().parent.parent
    resolved_dir.mkdir(parents=True, exist_ok=True)

    required_tables = {
        "asteroids.parquet": ASTEROID_SCHEMA,
        "bridge_asteroid_identifier.parquet": BRIDGE_ASTEROID_IDENTIFIER_SCHEMA,
        "fact_sentry_risk_snapshot.parquet": SENTRY_RISK_SNAPSHOT_SCHEMA,
        "fact_sbdb_object_snapshot.parquet": SBDB_OBJECT_SCHEMA,
    }

    for filename, schema in required_tables.items():
        path = resolved_dir / filename
        if path.exists():
            continue
        columns = [
            pa.array([], type=field.type)
            for field in schema
        ]
        pq.write_table(pa.Table.from_arrays(columns, schema=schema), path)

    return resolved_dir


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


_default_lakehouse_dir = ensure_default_lakehouse()
app = create_app(
    provider=DashboardDataProvider(base_dir=_default_lakehouse_dir, execution_mode="LOCAL")
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=True)
