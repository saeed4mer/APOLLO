"""Backend package initialization for APOLLO Planetary Defense Platform.

Ensures all backend subsystems (api, storage, pipelines) are accessible
on sys.path regardless of execution context or working directory.
"""

from __future__ import annotations

from pathlib import Path
import sys

_BACKEND_ROOT = Path(__file__).resolve().parent
_SUBSYSTEMS = [
    _BACKEND_ROOT,
    _BACKEND_ROOT / "api",
    _BACKEND_ROOT / "storage",
    _BACKEND_ROOT / "pipelines" / "ingestion",
    _BACKEND_ROOT / "pipelines" / "resolution",
    _BACKEND_ROOT / "pipelines" / "quality",
    _BACKEND_ROOT / "pipelines" / "utils",
]

for _path in _SUBSYSTEMS:
    _p_str = str(_path)
    if _p_str not in sys.path:
        sys.path.insert(0, _p_str)
