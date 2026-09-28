"""Bounded operational logs. Never record prompts, screenshots, keys or input text."""

import json
import logging
import sys
import traceback
from pathlib import Path
from logging.handlers import RotatingFileHandler
from collections import deque
from datetime import datetime, timezone
from ._paths import mobile_root

_recent: deque = deque(maxlen=40)
_logger = logging.Logger("opencompany.mobile.diagnostics")
_logger.setLevel(logging.INFO)
_logger.propagate = False
_fields = {"operation", "duration_ms", "error_type", "code", "serial", "run_id", "node_id", "workflow_id", "state",
           "execution_id", "provider", "model", "model_backend", "stage", "http_status", "provider_status", "steps", "exit_code", "engine_trace"}


def event(name: str, *, failed: bool = False, **fields) -> None:
    record = {"at": datetime.now(timezone.utc).isoformat(), "event": name,
              "level": "error" if failed else "info", **{k: v for k, v in fields.items() if k in _fields}}
    if failed and sys.exc_info()[2] is not None:
        # Include code locations for diagnosis, never exception text or locals.
        record["trace"] = [f"{Path(frame.filename).name}:{frame.lineno}:{frame.name}"
                           for frame in traceback.extract_tb(sys.exc_info()[2])[-12:]]
    _recent.append(record)
    # Use the application's configured pipeline as well as the bounded mobile
    # file. This feeds both the supervisor console and WebSocket Terminal.
    try:
        from core.logging import get_logger
        logger = get_logger("nodes.mobile")
        getattr(logger, "error" if failed else "info")(
            "Android: " + name, **{k: v for k, v in record.items() if k not in {"event", "level", "at"}}
        )
    except Exception:
        pass  # Logging cannot break device operations during startup/shutdown.
    try:
        path = mobile_root() / "mobile.log"
        if not _logger.handlers or _logger.handlers[0].baseFilename != str(path.resolve()):
            for handler in list(_logger.handlers):
                _logger.removeHandler(handler)
                handler.close()
            path.parent.mkdir(parents=True, exist_ok=True)
            handler = RotatingFileHandler(path, maxBytes=1024 * 1024, backupCount=3, encoding="utf-8")
            handler.setFormatter(logging.Formatter("%(message)s"))
            _logger.addHandler(handler)
        _logger.log(logging.ERROR if failed else logging.INFO, json.dumps(record))
    except OSError:
        # Diagnostics must never prevent phone operation (e.g. a full disk).
        pass


def recent() -> list[dict]:
    return list(_recent)
