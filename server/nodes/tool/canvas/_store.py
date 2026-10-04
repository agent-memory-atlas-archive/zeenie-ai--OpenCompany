"""Durable content board backing the Canvas node.

This module deliberately owns its tables instead of adding canvas-specific
methods to :mod:`core.database` — the same shape as
:mod:`services.data.mount_store` and :mod:`services.memory.tool_store`.
Importing the Canvas plugin registers the SQLModel tables before
``Database.startup()`` calls ``create_all``; :meth:`CanvasStore.ensure_schema`
covers standalone workers/tests that import later.

A board holds *references* (serialized ``FileRef`` dicts, URLs) and small
markdown notes — never file bytes. Content bodies for workspace files are
fetched by the client over the existing workspace HTTP route; the board rows
stay small so ``canvas_list`` is one cheap fetch.

Items have versions. An item starts at version 1; ``update`` makes what a
later call shows its next version, and ``canvas_item_versions`` keeps every
version (the current one too), at most ``CANVAS_MAX_VERSIONS`` per item,
oldest dropped first. Removing an item, clearing the board or evicting an
item drops its versions with it.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Column, Text, delete, func, select
from sqlmodel import Field, SQLModel

from core.logging import get_logger

logger = get_logger(__name__)

# Version-prefixed hash material — bump ``canvas:v1`` if the scope shape ever
# changes (the ``todo_session_key`` lesson: version the key from day one).
_KEY_VERSION = "canvas:v1"
UNSAVED_WORKFLOW_ID = "unsaved"

CANVAS_MAX_ITEMS = 200
#: Versions kept per item (its current one included).
CANVAS_MAX_VERSIONS = 50
CANVAS_NOTE_MAX_BYTES = 64 * 1024
CANVAS_MAX_PATHS_PER_CALL = 20

_TRUNCATION_MARKER = "\n… [truncated]"

CANVAS_ITEM_KINDS = ("file", "url", "note")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def truncate_note(content: str) -> tuple[str, bool]:
    """Cap a note at :data:`CANVAS_NOTE_MAX_BYTES` with a visible marker.

    A workflow run must never die on an oversized note — degrade instead.
    """
    encoded = content.encode("utf-8")
    if len(encoded) <= CANVAS_NOTE_MAX_BYTES:
        return content, False
    keep = CANVAS_NOTE_MAX_BYTES - len(_TRUNCATION_MARKER.encode("utf-8"))
    clipped = encoded[: max(0, keep)].decode("utf-8", errors="ignore")
    return clipped + _TRUNCATION_MARKER, True


@dataclass(frozen=True)
class CanvasScope:
    """One board: (authenticated owner, workflow, canvas node)."""

    owner_id: str
    workflow_id: str
    node_id: str

    @property
    def board_id(self) -> str:
        material = "\0".join(
            (_KEY_VERSION, self.owner_id, self.workflow_id, self.node_id)
        )
        digest = hashlib.sha256(material.encode("utf-8")).hexdigest()
        return f"canv_{digest[:48]}"


class CanvasBoard(SQLModel, table=True):
    """Per-scope board head: revision counter + identity columns."""

    __tablename__ = "canvas_boards"

    id: str = Field(primary_key=True, max_length=80)
    owner_id: str = Field(default="owner", index=True, max_length=255)
    workflow_id: str = Field(index=True, max_length=255)
    node_id: str = Field(index=True, max_length=255)
    revision: int = Field(default=0)
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


class CanvasItemRow(SQLModel, table=True):
    """One displayed item. ``ref`` is a serialized FileRef — never bytes."""

    __tablename__ = "canvas_items"

    id: str = Field(primary_key=True, max_length=64)
    board_id: str = Field(index=True, max_length=80)
    # Literal["file", "url", "note"] — SQLModel cannot map Literal on table
    # models, so the service layer constrains it (same note as tool_store).
    kind: str = Field(max_length=10)
    title: Optional[str] = Field(default=None, max_length=300)
    ref: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    url: Optional[str] = Field(default=None, max_length=2048)
    content: Optional[str] = Field(default=None, sa_column=Column(Text))
    language: Optional[str] = Field(default=None, max_length=40)
    source: str = Field(default="workflow", max_length=10)
    position: int = Field(default=0, index=True)
    created_at: datetime = Field(default_factory=_utcnow)
    #: 1 for a new item; each update adds one.
    version: int = Field(default=1)
    updated_at: Optional[datetime] = Field(default=None)


class CanvasItemVersion(SQLModel, table=True):
    """One version of an item, as it was shown (the current one too)."""

    __tablename__ = "canvas_item_versions"

    id: str = Field(primary_key=True, max_length=64)
    item_id: str = Field(index=True, max_length=64)
    board_id: str = Field(index=True, max_length=80)
    version: int = Field(default=1)
    kind: str = Field(max_length=10)
    title: Optional[str] = Field(default=None, max_length=300)
    ref: Optional[Dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    url: Optional[str] = Field(default=None, max_length=2048)
    content: Optional[str] = Field(default=None, sa_column=Column(Text))
    language: Optional[str] = Field(default=None, max_length=40)
    source: str = Field(default="workflow", max_length=10)
    created_at: datetime = Field(default_factory=_utcnow)


class CanvasStoreError(ValueError):
    """User-correctable store failure (unknown item id, bad kind)."""


def _iso(moment: Optional[datetime]) -> Optional[str]:
    if moment is None:
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)).isoformat()


def _serialize(row: CanvasItemRow) -> Dict[str, Any]:
    """Row -> the fixed CanvasItem wire shape (stable key set, null-filled)."""
    return {
        "id": row.id,
        "kind": row.kind,
        "title": row.title,
        "ref": row.ref,
        "url": row.url,
        "content": row.content,
        "language": row.language,
        "source": row.source,
        "created_at": _iso(row.created_at),
        "version": int(row.version or 1),
        "updated_at": _iso(row.updated_at),
    }


def _version_row(row: CanvasItemRow) -> CanvasItemVersion:
    return CanvasItemVersion(
        id=uuid.uuid4().hex,
        item_id=row.id,
        board_id=row.board_id,
        version=int(row.version or 1),
        kind=row.kind,
        title=row.title,
        ref=row.ref,
        url=row.url,
        content=row.content,
        language=row.language,
        source=row.source,
        created_at=row.updated_at or row.created_at or _utcnow(),
    )


def _version_size(version: CanvasItemVersion) -> int:
    if version.content is not None:
        return len(version.content.encode("utf-8"))
    if isinstance(version.ref, dict) and isinstance(version.ref.get("size_bytes"), int):
        return int(version.ref["size_bytes"])
    return 0


class CanvasStore:
    """Append/list/remove/clear over one board, each a single transaction."""

    _schema_lock = asyncio.Lock()
    _initialized_engines: set[Any] = set()

    def __init__(self, database: Any) -> None:
        self.database = database

    async def ensure_schema(self) -> None:
        engine = getattr(self.database, "engine", None)
        if engine is None:
            raise RuntimeError("Database is not initialized")
        if engine in self._initialized_engines:
            return
        async with self._schema_lock:
            if engine in self._initialized_engines:
                return
            async with engine.begin() as connection:
                for table in (CanvasBoard.__table__, CanvasItemRow.__table__, CanvasItemVersion.__table__):
                    await connection.run_sync(
                        lambda sync_connection, table=table: table.create(sync_connection, checkfirst=True)
                    )
                # Boards saved before items had versions.
                result = await connection.exec_driver_sql("PRAGMA table_info(canvas_items)")
                columns = {row[1] for row in result.fetchall()}
                if columns and "version" not in columns:
                    await connection.exec_driver_sql("ALTER TABLE canvas_items ADD COLUMN version INTEGER NOT NULL DEFAULT 1")
                if columns and "updated_at" not in columns:
                    await connection.exec_driver_sql("ALTER TABLE canvas_items ADD COLUMN updated_at DATETIME")
            self._initialized_engines.add(engine)

    async def append(
        self,
        scope: CanvasScope,
        items: List[Dict[str, Any]],
        *,
        mode: str = "append",
    ) -> tuple[List[Dict[str, Any]], int, int]:
        """Add items (``mode="replace"`` clears first). Returns
        ``(added_wire_items, revision, total)`` after FIFO eviction."""
        await self.ensure_schema()
        for item in items:
            if item.get("kind") not in CANVAS_ITEM_KINDS:
                raise CanvasStoreError(
                    f"Unknown canvas item kind: {item.get('kind')!r}"
                )
        async with self.database.get_session() as session:
            board = await self._board(session, scope, create=True)
            if mode == "replace":
                await session.execute(
                    delete(CanvasItemRow).where(
                        CanvasItemRow.board_id == board.id
                    )
                )
                await session.execute(delete(CanvasItemVersion).where(CanvasItemVersion.board_id == board.id))
                next_position = 0
            else:
                result = await session.execute(
                    select(func.max(CanvasItemRow.position)).where(
                        CanvasItemRow.board_id == board.id
                    )
                )
                current_max = result.scalar()
                next_position = (current_max + 1) if current_max is not None else 0

            added: List[CanvasItemRow] = []
            for offset, item in enumerate(items):
                content = item.get("content")
                if isinstance(content, str):
                    content, _ = truncate_note(content)
                row = CanvasItemRow(
                    id=uuid.uuid4().hex,
                    board_id=board.id,
                    kind=str(item["kind"]),
                    title=item.get("title"),
                    ref=item.get("ref"),
                    url=item.get("url"),
                    content=content,
                    language=item.get("language"),
                    source=str(item.get("source") or "workflow"),
                    position=next_position + offset,
                )
                session.add(row)
                session.add(_version_row(row))
                added.append(row)

            # FIFO eviction: keep the newest CANVAS_MAX_ITEMS by position.
            await session.flush()
            count_result = await session.execute(
                select(func.count())
                .select_from(CanvasItemRow)
                .where(CanvasItemRow.board_id == board.id)
            )
            total = int(count_result.scalar() or 0)
            if total > CANVAS_MAX_ITEMS:
                overflow = total - CANVAS_MAX_ITEMS
                oldest = await session.execute(
                    select(CanvasItemRow.id)
                    .where(CanvasItemRow.board_id == board.id)
                    .order_by(CanvasItemRow.position)
                    .limit(overflow)
                )
                evicted_ids = [value for (value,) in oldest.all()]
                if evicted_ids:
                    await session.execute(
                        delete(CanvasItemRow).where(
                            CanvasItemRow.id.in_(evicted_ids)
                        )
                    )
                    await session.execute(delete(CanvasItemVersion).where(CanvasItemVersion.item_id.in_(evicted_ids)))
                total = CANVAS_MAX_ITEMS

            board.revision += 1
            board.updated_at = _utcnow()
            session.add(board)
            await session.commit()
            return [_serialize(row) for row in added], board.revision, total

    async def list(self, scope: CanvasScope) -> Dict[str, Any]:
        await self.ensure_schema()
        async with self.database.get_session() as session:
            board = await self._board(session, scope, create=False)
            if board is None:
                return {"items": [], "revision": 0}
            result = await session.execute(
                select(CanvasItemRow)
                .where(CanvasItemRow.board_id == board.id)
                .order_by(CanvasItemRow.position)
            )
            return {
                "items": [_serialize(row) for row in result.scalars().all()],
                "revision": board.revision,
            }

    async def remove(self, scope: CanvasScope, item_id: str) -> Dict[str, Any]:
        await self.ensure_schema()
        async with self.database.get_session() as session:
            board = await self._board(session, scope, create=False)
            if board is None:
                raise CanvasStoreError("No canvas content for this node")
            result = await session.execute(
                select(CanvasItemRow).where(
                    CanvasItemRow.board_id == board.id,
                    CanvasItemRow.id == str(item_id or "").strip(),
                )
            )
            row = result.scalars().first()
            if row is None:
                raise CanvasStoreError(f"No canvas item with id '{item_id}'")
            await session.delete(row)
            await session.execute(delete(CanvasItemVersion).where(CanvasItemVersion.item_id == row.id))
            board.revision += 1
            board.updated_at = _utcnow()
            session.add(board)
            await session.commit()
            return {"removed": True, "revision": board.revision}

    async def clear(self, scope: CanvasScope) -> Dict[str, Any]:
        await self.ensure_schema()
        async with self.database.get_session() as session:
            board = await self._board(session, scope, create=False)
            if board is None:
                return {"cleared": 0, "revision": 0}
            result = await session.execute(
                delete(CanvasItemRow).where(CanvasItemRow.board_id == board.id)
            )
            await session.execute(delete(CanvasItemVersion).where(CanvasItemVersion.board_id == board.id))
            board.revision += 1
            board.updated_at = _utcnow()
            session.add(board)
            await session.commit()
            return {
                "cleared": int(getattr(result, "rowcount", 0) or 0),
                "revision": board.revision,
            }

    async def update(self, scope: CanvasScope, item_id: str, item: Dict[str, Any]) -> tuple[Dict[str, Any], int]:
        """Make ``item`` the next version of the board's item ``item_id``
        (same kind). Returns ``(wire_item, revision)``."""
        await self.ensure_schema()
        if item.get("kind") not in CANVAS_ITEM_KINDS:
            raise CanvasStoreError(f"Unknown canvas item kind: {item.get('kind')!r}")
        async with self.database.get_session() as session:
            board = await self._board(session, scope, create=False)
            row = await self._item(session, board, item_id)
            if row.kind != item["kind"]:
                raise CanvasStoreError(f"Item '{item_id}' is a {row.kind}; update it with a {row.kind} too")
            content = item.get("content")
            if isinstance(content, str):
                content, _ = truncate_note(content)
            row.title = item.get("title") or row.title
            row.ref = item.get("ref")
            row.url = item.get("url")
            row.content = content
            row.language = item.get("language")
            row.source = str(item.get("source") or row.source)
            row.version = int(row.version or 1) + 1
            row.updated_at = _utcnow()
            session.add(row)
            session.add(_version_row(row))
            await session.flush()
            # Keep the newest CANVAS_MAX_VERSIONS.
            old = await session.execute(
                select(CanvasItemVersion.id)
                .where(CanvasItemVersion.item_id == row.id)
                .order_by(CanvasItemVersion.version.desc())
                .offset(CANVAS_MAX_VERSIONS)
            )
            dropped = [value for (value,) in old.all()]
            if dropped:
                await session.execute(delete(CanvasItemVersion).where(CanvasItemVersion.id.in_(dropped)))
            board.revision += 1
            board.updated_at = _utcnow()
            session.add(board)
            await session.commit()
            return _serialize(row), board.revision

    async def versions(self, scope: CanvasScope, item_id: str) -> Dict[str, Any]:
        """An item's versions, newest first: ``{versions: [{version, title,
        created_at, source, size_bytes}], latest}``."""
        await self.ensure_schema()
        async with self.database.get_session() as session:
            board = await self._board(session, scope, create=False)
            row = await self._item(session, board, item_id)
            result = await session.execute(
                select(CanvasItemVersion).where(CanvasItemVersion.item_id == row.id).order_by(CanvasItemVersion.version.desc())
            )
            versions = [
                {
                    "version": version.version,
                    "title": version.title,
                    "created_at": _iso(version.created_at),
                    "source": version.source,
                    "size_bytes": _version_size(version),
                }
                for version in result.scalars().all()
            ]
            return {"item_id": row.id, "versions": versions, "latest": int(row.version or 1)}

    async def version(self, scope: CanvasScope, item_id: str, version: int) -> Dict[str, Any]:
        """One version of an item, in the CanvasItem wire shape."""
        await self.ensure_schema()
        async with self.database.get_session() as session:
            board = await self._board(session, scope, create=False)
            row = await self._item(session, board, item_id)
            result = await session.execute(
                select(CanvasItemVersion).where(CanvasItemVersion.item_id == row.id, CanvasItemVersion.version == int(version))
            )
            found = result.scalars().first()
            if found is None:
                raise CanvasStoreError(f"Item '{item_id}' has no version {version}")
            return {
                "id": row.id,
                "kind": found.kind,
                "title": found.title,
                "ref": found.ref,
                "url": found.url,
                "content": found.content,
                "language": found.language,
                "source": found.source,
                "created_at": _iso(found.created_at),
                "version": found.version,
                "latest": int(row.version or 1),
            }

    async def _item(self, session: Any, board: Optional[CanvasBoard], item_id: str) -> CanvasItemRow:
        if board is None:
            raise CanvasStoreError("No canvas content for this node")
        result = await session.execute(
            select(CanvasItemRow).where(CanvasItemRow.board_id == board.id, CanvasItemRow.id == str(item_id or "").strip())
        )
        row = result.scalars().first()
        if row is None:
            raise CanvasStoreError(f"No canvas item with id '{item_id}'")
        return row

    async def _board(
        self, session: Any, scope: CanvasScope, *, create: bool
    ) -> Optional[CanvasBoard]:
        result = await session.execute(
            select(CanvasBoard).where(CanvasBoard.id == scope.board_id)
        )
        board = result.scalars().first()
        if board is None and create:
            board = CanvasBoard(
                id=scope.board_id,
                owner_id=scope.owner_id,
                workflow_id=scope.workflow_id,
                node_id=scope.node_id,
            )
            session.add(board)
        return board


__all__ = [
    "CANVAS_ITEM_KINDS",
    "CANVAS_MAX_ITEMS",
    "CANVAS_MAX_PATHS_PER_CALL",
    "CANVAS_MAX_VERSIONS",
    "CANVAS_NOTE_MAX_BYTES",
    "CanvasBoard",
    "CanvasItemRow",
    "CanvasItemVersion",
    "CanvasScope",
    "CanvasStore",
    "CanvasStoreError",
    "UNSAVED_WORKFLOW_ID",
    "truncate_note",
]
