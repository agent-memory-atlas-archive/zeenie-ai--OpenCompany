"""Files the owner attaches to a chat message (docs-internal/chat_protocol.md,
"Attachments").

The chat uploads each file into the workflow's workspace first
(``POST /api/workspace/{workflow_id}/uploads``, which puts it under
``uploads/``) and sends the references it got back with the message.
``check_attachments`` trusts nothing in them but the path: each must name a
file under the workspace's ``uploads/`` folder that exists, and the
reference the message keeps is rebuilt here from the file itself (name,
type, size, address). At most ``MAX_ATTACHMENTS`` per message.

The employee reads them after the owner's words as one bracketed line
(``attachments_line``), with their workspace paths, so its file tools can
open them; images also travel with the message itself (``image_refs``) and
reach a model that can view them (``services/llm/media.py``).
"""

from __future__ import annotations

import json
import mimetypes
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional, Sequence

from services.media.workspace import UPLOAD_SUBDIR

#: The most files one message carries.
MAX_ATTACHMENTS = 6

_KINDS = {"image": "image", "video": "video", "pdf": "document"}


class AttachmentRefused(ValueError):
    """An attachment that cannot go with the message; the text says why."""


def _relative(raw: Any) -> str:
    path = raw.get("path") if isinstance(raw, dict) else raw
    if not isinstance(path, str) or not path.strip():
        raise AttachmentRefused("each attachment names a file you uploaded")
    parts = PurePosixPath(path.replace("\\", "/")).parts
    if len(parts) < 2 or parts[0] != UPLOAD_SUBDIR or any(part in ("..", "") for part in parts):
        raise AttachmentRefused("only files you uploaded to this chat can be attached")
    return "/".join(parts)


async def check_attachments(database: Any, workflow_id: str, raw: Any) -> List[Dict[str, Any]]:
    """The message's attachments, each rebuilt from its file. Raises
    :class:`AttachmentRefused`."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AttachmentRefused("attachments is a list of uploaded files")
    if len(raw) > MAX_ATTACHMENTS:
        raise AttachmentRefused(f"at most {MAX_ATTACHMENTS} files go with one message")
    if not raw:
        return []
    from services.media.preview import preview_kind
    from services.media.refs import FileRef
    from services.media.workspace import resolve_media, workspace_file_url
    from services.plugin import NodeUserError
    from services.workspace_locator import resolve_workspace_root

    try:
        root = await resolve_workspace_root(workflow_id, database, allow_default=False)
    except Exception as exc:  # noqa: BLE001 - no workspace, nothing to attach
        raise AttachmentRefused("this chat has no workspace to attach files from") from exc
    refs: List[Dict[str, Any]] = []
    seen = set()
    for item in raw:
        rel = _relative(item)
        if rel in seen:
            continue
        seen.add(rel)
        try:
            path = resolve_media(rel, workspace_dir=str(root))
        except NodeUserError as exc:
            raise AttachmentRefused(str(exc)) from exc
        if not path.is_file():
            raise AttachmentRefused(f"{path.name} is no longer there; attach it again")
        stat = path.stat()
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        refs.append(
            FileRef(
                kind=_KINDS.get(preview_kind(mime_type) or "", "file"),
                path=rel,
                workflow_id=workflow_id,
                filename=path.name,
                mime_type=mime_type,
                size_bytes=stat.st_size,
                modified_at=datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                url=workspace_file_url(workflow_id, rel),
            ).model_dump(mode="json")
        )
    return refs


def attachments_line(attachments: Sequence[Dict[str, Any]]) -> str:
    """What the employee reads about the files after the owner's words."""
    files = [
        {"path": ref.get("path"), "name": ref.get("filename"), "type": ref.get("mime_type"), "bytes": ref.get("size_bytes")}
        for ref in attachments
        if isinstance(ref, dict) and ref.get("path")
    ]
    if not files:
        return ""
    return f"[attachments]{json.dumps({'files': files}, ensure_ascii=False, separators=(', ', ': '))}[/attachments]"


def image_refs(attachments: Optional[Sequence[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """The attached images a model that can view them is shown."""
    from services.llm.media import IMAGE_MIME_ALLOWLIST

    return [dict(ref) for ref in attachments or [] if isinstance(ref, dict) and ref.get("mime_type") in IMAGE_MIME_ALLOWLIST]


__all__ = ["AttachmentRefused", "MAX_ATTACHMENTS", "attachments_line", "check_attachments", "image_refs"]
