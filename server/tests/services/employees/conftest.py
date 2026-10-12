"""Fixtures for the Normal-mode employees package tests."""

from __future__ import annotations

import importlib.util
import os
import sys
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
async def real_database(tmp_path: Path):
    """A real ``core.database.Database`` on a throwaway SQLite file.

    The root conftest stubs ``core.database`` for fast plugin tests, so the
    real module is loaded privately (tests/services/agent_context does the
    same)."""
    module_name = f"tests._employees_database_{uuid.uuid4().hex}"
    spec = importlib.util.spec_from_file_location(
        module_name,
        Path(__file__).resolve().parents[3] / "core" / "database.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    db_path = tmp_path / f"employees-{uuid.uuid4().hex}.db"
    admin = None
    postgres_name = "employee_test_" + uuid.uuid4().hex
    postgres_url = os.environ.get("TEST_POSTGRES_URL")
    database_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
    if postgres_url:
        # The root test harness stubs core as a package without a search path.
        # Load this small production compatibility module explicitly, as above.
        if "core.shared_database" not in sys.modules:
            shared_spec = importlib.util.spec_from_file_location("core.shared_database",
                Path(__file__).resolve().parents[3] / "core" / "shared_database.py")
            shared = importlib.util.module_from_spec(shared_spec)
            sys.modules["core.shared_database"] = shared
            shared_spec.loader.exec_module(shared)
        from sqlalchemy.engine import make_url
        from sqlalchemy.ext.asyncio import create_async_engine
        from sqlalchemy import text
        admin = create_async_engine(postgres_url, isolation_level="AUTOCOMMIT")
        async with admin.connect() as connection:
            await connection.execute(text(f'CREATE DATABASE "{postgres_name}"'))
        database_url = make_url(postgres_url).set(database=postgres_name).render_as_string(hide_password=False)
    settings = SimpleNamespace(
        database_url=database_url,
        database_echo=False,
        database_pool_size=5,
        database_max_overflow=5,
    )
    database = module.Database(settings)
    try:
        await database.startup()
        yield database
    finally:
        await database.shutdown()
        if admin:
            async with admin.connect() as connection:
                await connection.execute(text(f'DROP DATABASE "{postgres_name}" WITH (FORCE)'))
            await admin.dispose()
        sys.modules.pop(module_name, None)
        for candidate in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
            candidate.unlink(missing_ok=True)
