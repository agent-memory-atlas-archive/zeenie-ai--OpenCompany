"""All optional runtime state belongs to DATA_DIR, never the source bundle."""

from pathlib import Path


def mobile_root() -> Path:
    from core.config import Settings

    return Path(Settings().data_dir).expanduser().resolve() / "mobile"


def runtime_python() -> Path:
    import os

    return mobile_root() / "engine" / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def resource_manifest() -> Path:
    return mobile_root() / "resource.json"
