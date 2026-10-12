"""Immutable Pi reconstruction caches; Context remains the conversation head."""

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Column, DateTime, JSON
from sqlmodel import Field, SQLModel


class PiRuntimeBlob(SQLModel, table=True):
    __tablename__ = "pi_runtime_blobs"

    id: str = Field(primary_key=True, max_length=64)
    scope_digest: str = Field(index=True, max_length=64)
    kind: str = Field(max_length=32)
    digest: str = Field(max_length=64)
    payload: Any = Field(sa_column=Column(JSON, nullable=False))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc),
        sa_column=Column(DateTime(timezone=True), nullable=False))
