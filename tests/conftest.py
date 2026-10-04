"""Master test configuration and sys.path injection for APOLLO.

Ensures both modern hierarchical imports (backend.pipelines.ingestion.nasa_asteroids)
and flat backwards-compatible imports (nasa_asteroids, dashboard_data, api.main)
resolve seamlessly across all unit, integration, and API test suites.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

# Locate repository root and key module directories
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_LAKEHOUSE = PROJECT_ROOT / "data" / "lakehouse"

# Configure APOLLO_LAKEHOUSE_DIR environment variable for test runs
if DATA_LAKEHOUSE.exists() and "APOLLO_LAKEHOUSE_DIR" not in os.environ:
    os.environ["APOLLO_LAKEHOUSE_DIR"] = str(DATA_LAKEHOUSE)

# Inject all subsystem directories into sys.path
_SEARCH_PATHS = [
    BACKEND_DIR,
    PROJECT_ROOT,
    PROJECT_ROOT / "tests" / "integration",
    BACKEND_DIR / "api",
    BACKEND_DIR / "storage",
    BACKEND_DIR / "pipelines" / "ingestion",
    BACKEND_DIR / "pipelines" / "resolution",
    BACKEND_DIR / "pipelines" / "quality",
    BACKEND_DIR / "pipelines" / "utils",
]

for p in _SEARCH_PATHS:
    p_str = str(p)
    if p_str not in sys.path:
        sys.path.insert(0, p_str)
