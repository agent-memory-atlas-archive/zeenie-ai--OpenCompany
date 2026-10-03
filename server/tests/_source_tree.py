"""The backend's own Python files, for tests that scan its source.

Several invariant tests read every backend module: no raw ``print()``, no
climb above ``server/``, no Node runtime lookup, no LangChain import, no raw
``send_custom_event`` payload. ``server/`` also holds trees that are not
backend source: the virtualenv and, in a source-dev install, the Mobile
workspace's phone runtime under ``.opencompany/mobile``. Each holds far more
``.py`` files than the backend itself, and scans that read them took minutes
and could trip over third-party code. Walking through this module prunes them
before anything is read, so a new scan test cannot forget one.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List

SERVER_ROOT = Path(__file__).resolve().parents[1]

# Directory names that are never backend source, wherever they appear.
NOT_SOURCE = frozenset({".venv", ".opencompany", ".machina", "__pycache__", "node_modules"})


def server_python_files(*skip_top: str) -> List[Path]:
    """Every ``.py`` file under ``server/`` outside :data:`NOT_SOURCE` and
    the named top-level directories (``"tests"``, ``"scripts"``, ...).

    The names in ``skip_top`` are pruned at the top level only:
    ``services/skills/`` is backend code, and pruning ``skills`` at every
    depth once hid it from two of these tests.
    """
    skipped = frozenset(skip_top)
    files: List[Path] = []
    for root, dirs, names in os.walk(SERVER_ROOT):
        at_top = Path(root) == SERVER_ROOT
        dirs[:] = [name for name in dirs if name not in NOT_SOURCE and not (at_top and name in skipped)]
        files.extend(Path(root) / name for name in names if name.endswith(".py"))
    return files
