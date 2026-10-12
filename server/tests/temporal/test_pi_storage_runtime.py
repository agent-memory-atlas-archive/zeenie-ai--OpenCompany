from __future__ import annotations

from contextlib import asynccontextmanager
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from temporalio.exceptions import ApplicationError

from models.pi_checkpoint import PiRuntimeBlob
from services import pi_blobs, pi_runtime


@pytest.mark.asyncio
async def test_immutable_sqlite_blob_cache_is_scoped_and_integrity_checked(tmp_path, monkeypatch):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'pi.db'}")
    async with engine.begin() as connection:
        await connection.run_sync(PiRuntimeBlob.__table__.create)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    @asynccontextmanager
    async def session():
        async with maker() as value:
            yield value
    monkeypatch.setattr(pi_blobs, "get_database", lambda: SimpleNamespace(get_session=session))
    scope = {"workflow_id": "root", "generation_id": "1", "node_id": "coding"}
    payload = {"header": {"version": 3}, "entries": [{"signature": "exact"}], "leaf_id": "id"}
    try:
        first = await pi_blobs.put_blob(scope, "snapshot", payload)
        assert await pi_blobs.put_blob(scope, "snapshot", payload) == first
        assert await pi_blobs.get_blob(scope, first) == payload
        other = await pi_blobs.put_blob({**scope, "generation_id": "2"}, "snapshot", payload)
        assert first["pi_blob"] != other["pi_blob"]
        with pytest.raises(ApplicationError, match="different execution"):
            await pi_blobs.get_blob({**scope, "generation_id": "2"}, first)
        async with session() as db:
            row = await db.get(PiRuntimeBlob, first["pi_blob"])
            row.payload = {"tampered": True}
            await db.commit()
        with pytest.raises(ApplicationError, match="integrity"):
            await pi_blobs.get_blob(scope, first)
    finally:
        await engine.dispose()


def _distribution(tmp_path, monkeypatch, extra=None):
    root = tmp_path / "server"
    distribution = root / "pi" / "dist"
    distribution.mkdir(parents=True)
    monkeypatch.setattr(pi_runtime, "__file__", str(root / "services" / "pi_runtime.py"))
    monkeypatch.setitem(sys.modules, "core.paths", SimpleNamespace(data_path=lambda _: tmp_path / "runtime"))
    archive = distribution / "runtime.tgz"
    with tarfile.open(archive, "w:gz") as bundle:
        for name, value in {"index.js": b"export {};", "node_modules/@earendil-works/pi-coding-agent/package.json": b'{"version":"1.1.0"}'}.items():
            member = tarfile.TarInfo(name)
            member.size = len(value)
            bundle.addfile(member, io.BytesIO(value))
        if extra:
            bundle.addfile(extra)
    manifest = {"protocol": 1, "sdk_version": "1.1.0", "archive": "runtime.tgz",
        "entrypoint": "index.js", "sdk_package_dir": "node_modules/@earendil-works/pi-coding-agent",
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(), "bytes": archive.stat().st_size,
        "executables": []}
    (distribution / "runtime-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return archive


def test_private_runtime_is_verified_and_extracted_offline_once(tmp_path, monkeypatch):
    _distribution(tmp_path, monkeypatch)
    first = pi_runtime.resolve_pi_host()
    assert first.name == "index.js" and first.read_text() == "export {};"
    assert pi_runtime.resolve_pi_host() == first
    assert (first.parent / ".verified").is_file()


@pytest.mark.parametrize("winerror", [5, 32, 33])
def test_private_runtime_retries_transient_windows_publication_locks(tmp_path, monkeypatch, winerror):
    _distribution(tmp_path, monkeypatch)
    replace = pi_runtime.os.replace
    attempts = []
    sleeps = []
    def publish(source, target):
        attempts.append((source, target))
        if len(attempts) < 3:
            error = PermissionError("Runtime temporarily locked")
            error.winerror = winerror
            raise error
        replace(source, target)
    monkeypatch.setattr(pi_runtime.os, "replace", publish)
    monkeypatch.setattr(pi_runtime.time, "sleep", sleeps.append)
    host = pi_runtime.resolve_pi_host()
    assert host.read_text() == "export {};" and (host.parent / ".verified").is_file()
    assert len(attempts) == 3 and sleeps == [0.1, 0.2]


def test_private_runtime_persistent_publication_failure_is_terminal_and_cleans_scratch(tmp_path, monkeypatch):
    _distribution(tmp_path, monkeypatch)
    attempts = []
    def locked(source, target):
        attempts.append(source)
        error = PermissionError("Runtime permanently inaccessible")
        error.winerror = 5
        raise error
    monkeypatch.setattr(pi_runtime.os, "replace", locked)
    monkeypatch.setattr(pi_runtime.time, "sleep", lambda _: None)
    with pytest.raises(ApplicationError) as error:
        pi_runtime.resolve_pi_host()
    assert error.value.type == "PiRuntimeMissing" and error.value.non_retryable
    assert len(attempts) == 12 and not list((tmp_path / "runtime").iterdir())


def test_private_runtime_rejects_modified_archive(tmp_path, monkeypatch):
    archive = _distribution(tmp_path, monkeypatch)
    archive.write_bytes(archive.read_bytes() + b"modified")
    with pytest.raises(ApplicationError, match="integrity"):
        pi_runtime.resolve_pi_host()


@pytest.mark.parametrize("change", [{"bytes": 1}, {"sha256": "invalid"}, {"executables": ["../../escape"]}])
def test_private_runtime_rejects_invalid_manifest_metadata(tmp_path, monkeypatch, change):
    archive = _distribution(tmp_path, monkeypatch)
    manifest_path = archive.parent / "runtime-manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(change)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ApplicationError) as error:
        pi_runtime.resolve_pi_host()
    assert error.value.type == "PiRuntimeMissing" and error.value.non_retryable


def test_private_runtime_corrupt_manifest_is_terminal(tmp_path, monkeypatch):
    archive = _distribution(tmp_path, monkeypatch)
    (archive.parent / "runtime-manifest.json").write_text("invalid JSON", encoding="utf-8")
    with pytest.raises(ApplicationError) as error:
        pi_runtime.resolve_pi_host()
    assert error.value.type == "PiRuntimeMissing" and error.value.non_retryable


@pytest.mark.parametrize("name,link", [("../../escape", None), ("C:/escape", None),
    ("node_modules/unsafe-link", "../../../escape")])
def test_private_runtime_rejects_archive_path_and_link_escapes(tmp_path, monkeypatch, name, link):
    member = tarfile.TarInfo(name)
    if link:
        member.type = tarfile.SYMTYPE
        member.linkname = link
    _distribution(tmp_path, monkeypatch, member)
    with pytest.raises(ApplicationError, match="unsafe archive"):
        pi_runtime.resolve_pi_host()
    assert not (tmp_path.parent / "escape").exists()
