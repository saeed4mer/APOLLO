"""FastAPI application entry point for the NASA Planetary Defense Platform.

Provides clean importable `app` object and factory `create_app` for local serving.
"""

from __future__ import annotations

import atexit
import logging
from pathlib import Path
import shutil
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq
from fastapi import FastAPI

from api.routes.asteroids import router as asteroids_router
from api.routes.health import router as health_router
from api.service import PROJECT_ROOT, REQUIRED_PARQUET_ASSETS
from dashboard_data import DashboardDataProvider
from entity_resolution import BRIDGE_ASTEROID_IDENTIFIER_SCHEMA
from nasa_asteroids import ASTEROID_SCHEMA
from nasa_sbdb import SBDB_OBJECT_SCHEMA
from nasa_sentry import SENTRY_RISK_SNAPSHOT_SCHEMA

logger = logging.getLogger(__name__)

_PLACEHOLDER_SCHEMAS = {
    "asteroids.parquet": ASTEROID_SCHEMA,
    "bridge_asteroid_identifier.parquet": BRIDGE_ASTEROID_IDENTIFIER_SCHEMA,
    "fact_sentry_risk_snapshot.parquet": SENTRY_RISK_SNAPSHOT_SCHEMA,
    "fact_sbdb_object_snapshot.parquet": SBDB_OBJECT_SCHEMA,
}
if set(_PLACEHOLDER_SCHEMAS) != set(REQUIRED_PARQUET_ASSETS):
    raise RuntimeError("placeholder lakehouse tables must match the health probe's required assets")


def resolve_default_lakehouse(project_root: Path | str = PROJECT_ROOT) -> Path:
    """Return the lakehouse directory the module-level app serves.

    The real local lakehouse (the generated Parquet files in the project root) is always used when
    any of its required tables exist, unchanged; a partially present lakehouse is served as-is, so
    the health probe keeps reporting it as not ready.

    Only when NONE of the required tables exist (a fresh clone or CI: generated Parquet is never
    committed) does the default app fall back to an empty, schema-valid placeholder lakehouse, so
    /health is 200 on a clean checkout. The placeholder lives in a private temporary directory
    OUTSIDE the repository (removed at interpreter exit): importing the API never writes into the
    working tree.
    """
    root = Path(project_root).resolve()
    if any((root / name).exists() for name in REQUIRED_PARQUET_ASSETS):
        return root

    placeholder = Path(tempfile.mkdtemp(prefix="apollo-empty-lakehouse-"))
    atexit.register(shutil.rmtree, placeholder, ignore_errors=True)
    for filename, schema in _PLACEHOLDER_SCHEMAS.items():
        columns = [pa.array([], type=field.type) for field in schema]
        pq.write_table(pa.Table.from_arrays(columns, schema=schema), placeholder / filename)
    logger.warning(
        "No local lakehouse found in %s; serving an empty placeholder lakehouse from %s. "
        "Run the ingestion pipeline to populate real data.",
        root,
        placeholder,
    )
    return placeholder


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


app = create_app(
    provider=DashboardDataProvider(base_dir=resolve_default_lakehouse(), execution_mode="LOCAL")
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api.main:app", host="127.0.0.1", port=8000, reload=True)
