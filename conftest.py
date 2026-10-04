"""Root conftest.py for pytest.

Ensures sys.path is configured before collecting any tests in tests/ or subdirectories.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_LAKEHOUSE = PROJECT_ROOT / "data" / "lakehouse"

if DATA_LAKEHOUSE.exists() and "APOLLO_LAKEHOUSE_DIR" not in os.environ:
    os.environ["APOLLO_LAKEHOUSE_DIR"] = str(DATA_LAKEHOUSE)

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
