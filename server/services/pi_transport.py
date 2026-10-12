"""Verified, attempt-scoped files for JSONL values above the inline limit.

The Python driver chooses the directory and scope. The SDK can reference only
content-addressed regular files in that directory, never a workspace path.
These files are disposable transport buffers, not durable conversation state.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import stat

from services.temporal.pi_protocol import FRAME_LIMIT, INLINE_LIMIT, canonical_digest, canonical_json, conflict


class ScopedProtocolBlobs:
    def __init__(self, root: str | Path, scope: dict, epoch: int) -> None:
        self.root = Path(root).resolve()
        self.scope_digest = canonical_digest(scope)
        self.epoch = epoch

    def encode(self, value):
        raw = canonical_json(value).encode("utf-8")
        # The LF delimiter also occupies one byte of the bounded frame.
        if len(raw) < INLINE_LIMIT:
            return value
        if len(raw) > FRAME_LIMIT:
            raise conflict("Pi transport value exceeds its bounded blob limit")
        digest = hashlib.sha256(raw).hexdigest()
        name = f"{digest}.json"
        path = self.root / name
        # Each attempt has an exclusive directory. Never follow a file planted
        # by a peer, even when it happens to have a valid content-addressed name.
        try:
            with path.open("xb") as output:
                output.write(raw)
        except FileExistsError:
            self._read({"name": name, "sha256": digest, "bytes": len(raw),
                "scope_digest": self.scope_digest, "epoch": self.epoch})
        return {"pi_file": {"name": name, "sha256": digest, "bytes": len(raw),
            "scope_digest": self.scope_digest, "epoch": self.epoch}}

    def decode(self, value):
        if not isinstance(value, dict) or "pi_file" not in value:
            return value
        if set(value) != {"pi_file"} or not isinstance(value["pi_file"], dict):
            raise conflict("Malformed Pi transport blob reference")
        return self._read(value["pi_file"])

    def _read(self, reference: dict):
        digest = reference.get("sha256")
        size = reference.get("bytes")
        if (set(reference) != {"name", "sha256", "bytes", "scope_digest", "epoch"}
                or not isinstance(digest, str) or not re.fullmatch(r"[a-f0-9]{64}", digest)
                or reference.get("name") != f"{digest}.json"
                or reference.get("scope_digest") != self.scope_digest
                or type(reference.get("epoch")) is not int or reference["epoch"] != self.epoch
                or type(size) is not int or not INLINE_LIMIT <= size <= FRAME_LIMIT):
            raise conflict("Pi transport blob does not match its admitted scope")
        path = self.root / reference["name"]
        try:
            if not path.resolve().is_relative_to(self.root) or not stat.S_ISREG(path.lstat().st_mode) or path.is_symlink():
                raise conflict("Pi transport blob must be a scoped regular file")
            with path.open("rb") as source:
                raw = source.read(FRAME_LIMIT + 1)
        except (FileNotFoundError, IsADirectoryError) as exc:
            raise conflict("Pi transport blob is missing") from exc
        if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
            raise conflict("Pi transport blob failed integrity verification")
        try:
            decoded = json.loads(raw)
            if canonical_json(decoded).encode("utf-8") != raw:
                raise conflict("Pi transport blob is not canonical JSON")
            return decoded
        except (ValueError, UnicodeError, TypeError) as exc:
            raise conflict("Pi transport blob contains invalid JSON") from exc
