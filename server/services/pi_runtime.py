"""Verify and unpack the privately shipped Pi dependency tree, entirely offline."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import tarfile
import tempfile
import time

from temporalio.exceptions import ApplicationError


def _missing(message: str):
    return ApplicationError(message, type="PiRuntimeMissing", non_retryable=True)


def resolve_pi_host() -> Path:
    try:
        return _resolve_pi_host()
    except ApplicationError:
        raise
    except (OSError, ValueError, TypeError, KeyError, tarfile.TarError) as exc:
        raise _missing("The packaged Pi runtime is unreadable or incompatible. Rebuild OpenCompany.") from exc


def _publish_cache(temporary: Path, target: Path, verified) -> None:
    for attempt in range(12):
        try:
            os.replace(temporary, target)
            return
        except OSError as exc:
            # Another worker can publish this immutable cache first. Windows
            # scanners can also briefly hold handles in the extracted tree.
            if verified():
                return
            if getattr(exc, "winerror", None) not in {5, 32, 33} or attempt == 11:
                raise
            time.sleep(0.1 * min(attempt + 1, 5))


def _resolve_pi_host() -> Path:
    root = Path(__file__).resolve().parent.parent / "pi"
    distribution = root / "dist"
    manifest_path = distribution / "runtime-manifest.json"
    if not manifest_path.is_file():
        source = root / "src" / "index.ts"
        package = root / "node_modules" / "@earendil-works" / "pi-coding-agent" / "package.json"
        if source.is_file() and package.is_file():
            metadata = json.loads(package.read_text(encoding="utf-8"))
            if isinstance(metadata, dict) and metadata.get("version") == "1.1.0":
                return source
        raise _missing("Pi runtime assets are missing. Rebuild OpenCompany before running Coding Agent.")
    raw = manifest_path.read_bytes()
    manifest = json.loads(raw)
    if (not isinstance(manifest, dict) or manifest.get("sdk_version") != "1.1.0" or manifest.get("protocol") != 1
            or manifest.get("archive") != "runtime.tgz" or manifest.get("entrypoint") != "index.js"
            or manifest.get("sdk_package_dir") != "node_modules/@earendil-works/pi-coding-agent"
            or not isinstance(manifest.get("sha256"), str) or not re.fullmatch(r"[a-f0-9]{64}", manifest["sha256"])
            or type(manifest.get("bytes")) is not int or manifest["bytes"] <= 0
            or not isinstance(manifest.get("executables", []), list)
            or any(not isinstance(path, str) for path in manifest.get("executables", []))):
        raise _missing("The packaged Pi runtime version is incompatible")
    archive = distribution / "runtime.tgz"
    if not archive.is_file() or archive.stat().st_size != manifest["bytes"]:
        raise _missing("The packaged Pi runtime failed its integrity check")
    sha = hashlib.sha256()
    with archive.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            sha.update(chunk)
    digest = sha.hexdigest()
    if digest != manifest.get("sha256"):
        raise _missing("The packaged Pi runtime failed its integrity check")
    from core.paths import data_path
    cache = Path(data_path("pi-runtime"))
    cache.mkdir(parents=True, exist_ok=True)
    target = cache / hashlib.sha256(raw).hexdigest()
    entry = target / "index.js"
    private_package = target / "node_modules" / "@earendil-works" / "pi-coding-agent" / "package.json"
    def verified() -> bool:
        if not ((target / ".verified").is_file() and (target / ".verified").read_text(encoding="ascii") == digest
                and entry.is_file() and private_package.is_file()):
            return False
        metadata = json.loads(private_package.read_text(encoding="utf-8"))
        return isinstance(metadata, dict) and metadata.get("version") == "1.1.0"
    if verified():
        return entry
    temporary = Path(tempfile.mkdtemp(prefix=".pi-", dir=cache))
    try:
        with tarfile.open(archive, "r:gz") as bundle:
            members = bundle.getmembers()
            for member in members:
                path = PurePosixPath(member.name.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts) or member.isdev() or member.isfifo():
                    raise _missing("The packaged Pi runtime contains an unsafe archive path")
                if member.issym() or member.islnk():
                    resolved = ((temporary / member.name).parent / member.linkname).resolve() if member.issym() else (temporary / member.linkname).resolve()
                    if not resolved.is_relative_to(temporary.resolve()):
                        raise _missing("The packaged Pi runtime contains an unsafe archive link")
            bundle.extractall(temporary, members=members, filter="data")
        package = temporary / "node_modules" / "@earendil-works" / "pi-coding-agent" / "package.json"
        metadata = json.loads(package.read_text(encoding="utf-8")) if package.is_file() else None
        if not (temporary / "index.js").is_file() or not isinstance(metadata, dict) or metadata.get("version") != "1.1.0":
            raise _missing("The packaged Pi runtime is incomplete")
        for name in manifest.get("executables", []):
            path = PurePosixPath(name.replace("\\", "/"))
            executable = temporary / str(path)
            if (path.is_absolute() or ".." in path.parts or any(":" in part for part in path.parts)
                    or not executable.resolve().is_relative_to(temporary.resolve())
                    or executable.is_symlink() or not executable.is_file()):
                raise _missing("The packaged Pi runtime contains an unsafe executable path")
            if os.name != "nt":
                executable.chmod(0o755)
        (temporary / ".verified").write_text(digest, encoding="ascii")
        _publish_cache(temporary, target, verified)
        return entry
    finally:
        if temporary.is_dir() and temporary.resolve().is_relative_to(cache.resolve()):
            shutil.rmtree(temporary)
