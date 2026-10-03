"""Per-workflow workspace file access.

Two routes: serve a file out of a workflow's workspace, and accept an
upload into it. Both exist because audio cannot travel through the workflow
engine as bytes (see ``services/media/limits.py``) — the UI needs a URL to
play generated audio from, and the file parameter needs somewhere to put an
uploaded clip other than base64 inside the node's parameters.

**The id/slug asymmetry is the thing to understand here.** The URL carries
``workflow_id`` because an ``AudioRef`` deliberately stores the immutable id,
so a reference keeps working after the workflow is renamed. But the
directory on disk is named by ``Workflow.slug``, which the rename path
moves. This router owns the id → slug lookup, because it is the layer that
has a database; ``services.media`` stays synchronous and takes the resolved
directory.

Containment is delegated to ``resolve_within``, which rejects ``..`` / ``~``
/ drive-prefixed input before touching the filesystem and re-checks
containment after resolution so a symlink or Windows junction cannot
redirect the result outside the root.

An upload is a write, so it is stricter than the file route: only the
workflow's owner may upload, and an id that does not resolve is a 404 rather
than a write into the shared anonymous workspace.
"""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Union

from fastapi import APIRouter, Depends, File, HTTPException, Path as PathParam, Request, UploadFile
from fastapi.responses import FileResponse

from core.container import container
from core.database import Database
from core.logging import get_logger
from services.media import MEDIA_MAX_UPLOAD_BYTES, AudioRef, write_audio
from services.media.preview import preview_kind, serves_inline
from services.media.refs import FileRef
from services.media.workspace import UPLOAD_SUBDIR, write_media

logger = get_logger(__name__)

router = APIRouter(prefix="/api/workspace", tags=["workspace"])

# Bounded so a hostile Content-Length cannot make us allocate; the running
# total is what enforces the cap, not the declared length.
_UPLOAD_CHUNK_BYTES = 1024 * 1024


def _db() -> Database:
    return container.database()


async def _workspace_root(workflow_id: str, database: Database) -> Path:
    """Resolve a workflow id to its on-disk workspace directory.

    Delegates to :mod:`services.workspace_locator`, which owns the id->slug
    translation for every consumer. Only the file route uses this: it is a
    read, so the ``"default"`` fallback (the anonymous workspace a one-off run
    without a saved row writes into) stays enabled. Uploads go through
    :func:`_writable_workspace_root`.
    """
    from services.workspace_locator import resolve_workspace_root

    return await resolve_workspace_root(workflow_id, database)


def _owner_of(saved: Any) -> str:
    """The workflow's owner from its saved graph, or "" when none is recorded."""
    data = getattr(saved, "data", None)
    if data is None and isinstance(saved, dict):
        data = saved.get("data", saved)
    return str(data.get("owner_id") or "") if isinstance(data, dict) else ""


async def _writable_workspace_root(workflow_id: str, request: Request, database: Database) -> Path:
    """Resolve the workspace an upload writes into, or 404.

    Unlike reads, a write never falls back to the shared anonymous workspace:
    an id that does not resolve (a stale tab, a deleted workflow) would
    otherwise drop the file into a different context than the caller
    believes. And only the workflow's owner may write into it. Both answer
    404, like the read route, so a refusal does not confirm what exists.
    """
    from constants import OWNER_PRINCIPAL_ID
    from services.plugin import NodeUserError
    from services.workspace_locator import resolve_workspace_root

    owner = _owner_of(await database.get_workflow(workflow_id))
    principal = str(getattr(request.state, "user_id", None) or OWNER_PRINCIPAL_ID)
    if owner and owner != principal:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        return await resolve_workspace_root(workflow_id, database, allow_default=False)
    except NodeUserError:
        raise HTTPException(status_code=404, detail="Not found")


def _upload_mime(name: str, declared: str | None) -> str:
    """The upload's media type, from its stored name first.

    The serve route decides how a file is displayed from its name, so the
    reference reports the same type; the client's declared type only fills
    in for an unknown extension.
    """
    guessed = mimetypes.guess_type(name)[0]
    if guessed:
        return guessed
    declared = (declared or "").split(";", 1)[0].strip().lower()
    return declared or "application/octet-stream"


def _resolve(root: Path, rel_path: str) -> Path:
    """Contain ``rel_path`` under ``root`` or 404.

    404 rather than 403 throughout: a different status for "exists but
    forbidden" would confirm the existence of files outside the workspace.
    """
    from nodes.filesystem._backend import resolve_within

    try:
        target = resolve_within(root, rel_path)
    except ValueError:
        raise HTTPException(status_code=404, detail="Not found")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="Not found")
    return target


@router.get("/{workflow_id}/files/{file_path:path}")
async def serve_workspace_file(
    workflow_id: str = PathParam(..., min_length=1, max_length=128),
    file_path: str = PathParam(..., min_length=1),
    database: Database = Depends(_db),
) -> FileResponse:
    """Serve one file from a workflow's workspace.

    Range requests work without any code here: Starlette's ``FileResponse``
    already implements ``Accept-Ranges``, ``206``, ``Content-Range``,
    ``If-Range`` and ``416``. Wrapping this in a ``StreamingResponse`` would
    lose all of that and break seeking in an ``<audio>`` element.
    """
    root = await _workspace_root(workflow_id, database)
    target = _resolve(root, file_path)

    media_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
    disposition = "inline" if serves_inline(media_type) else "attachment"

    return FileResponse(
        target,
        media_type=media_type,
        headers={
            "Content-Disposition": f'{disposition}; filename="{target.name}"',
            # Workspace files are mutable, so no immutable caching. The
            # filename already carries a random suffix, which makes a short
            # revalidating cache safe.
            "Cache-Control": "private, max-age=0, must-revalidate",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/{workflow_id}/uploads")
async def upload_workspace_file(
    request: Request,
    workflow_id: str = PathParam(..., min_length=1, max_length=128),
    file: UploadFile = File(...),
    database: Database = Depends(_db),
) -> Union[AudioRef, FileRef]:
    """Accept a file into the workflow's workspace and return a reference.

    Read in bounded chunks with a running total rather than
    ``await file.read()``: the declared ``Content-Length`` is attacker
    controlled, so the only trustworthy limit is what has actually been
    read. Exceeding the cap aborts immediately, before the whole body has
    been received.

    Audio comes back as an ``AudioRef``, its container probed; anything else
    as a ``FileRef`` whose kind is only a rendering hint (image, video, a PDF
    as a document, otherwise file). ``coerce_file_param`` accepts both, so
    the file parameter on a node takes either with no further plumbing.
    """
    root = await _writable_workspace_root(workflow_id, request, database)

    digest = hashlib.sha256()
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > MEDIA_MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"Upload exceeds the {MEDIA_MAX_UPLOAD_BYTES // (1024 * 1024)} MB "
                    "limit."
                ),
            )
        digest.update(chunk)
        chunks.append(chunk)

    if not total:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")

    payload = b"".join(chunks)
    name = Path(file.filename or "upload.bin").name
    stem, _, ext = name.rpartition(".")
    mime_type = _upload_mime(name, file.content_type)
    shown_as = preview_kind(mime_type)
    ctx = SimpleNamespace(workspace_dir=str(root), node_id="upload", workflow_id=workflow_id)

    # write_audio / write_media own filename sanitization (Windows reserved
    # device names, slugging, a random suffix so nothing collides or
    # overwrites) and the atomic write. Reimplementing any of that here would
    # be a second, worse copy.
    if shown_as == "audio":
        ref: FileRef = write_audio(
            payload,
            ctx=ctx,
            stem=stem or name,
            ext=ext or "bin",
            mime_type=mime_type,
            subdir=UPLOAD_SUBDIR,
        )
    else:
        ref = write_media(
            payload,
            ctx=ctx,
            stem=stem or name,
            ext=ext or "bin",
            kind={"image": "image", "video": "video", "pdf": "document"}.get(shown_as, "file"),
            mime_type=mime_type,
            subdir=UPLOAD_SUBDIR,
        )

    logger.info(
        "workspace upload stored",
        workflow_id=workflow_id,
        path=ref.path,
        size_bytes=ref.size_bytes,
    )
    return ref
