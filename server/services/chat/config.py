"""Chat runtime settings (``config/chat_defaults.json``), loaded once."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "chat_defaults.json"


@lru_cache(maxsize=1)
def load_chat_config() -> Mapping[str, Any]:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def runs_setting(name: str) -> float:
    """``runs.<name>``: seconds, from the config file."""
    return float(load_chat_config()["runs"][name])


def hub_setting(name: str) -> int:
    return int(load_chat_config()["hub"][name])


__all__ = ["CONFIG_PATH", "hub_setting", "load_chat_config", "runs_setting"]
