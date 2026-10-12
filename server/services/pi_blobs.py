"""Scoped content-addressed immutable storage for Pi's derived runtime state."""

from __future__ import annotations

from sqlalchemy.exc import IntegrityError

from models.pi_checkpoint import PiRuntimeBlob
from services.plugin.deps import get_database
from services.temporal.pi_protocol import canonical_digest, conflict


async def put_blob(scope: dict, kind: str, payload) -> dict:
    scope_digest = canonical_digest(scope)
    digest = canonical_digest(payload)
    identity = canonical_digest({"scope": scope_digest, "kind": kind, "digest": digest})
    database = get_database()
    async with database.get_session() as session:
        row = await session.get(PiRuntimeBlob, identity)
        if row is None:
            session.add(PiRuntimeBlob(id=identity, scope_digest=scope_digest, kind=kind,
                digest=digest, payload=payload))
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                row = await session.get(PiRuntimeBlob, identity)
                if row is None or row.digest != digest or row.scope_digest != scope_digest:
                    raise conflict("Pi immutable blob write conflicted")
    return {"pi_blob": identity, "scope_digest": scope_digest, "digest": digest, "kind": kind}


async def get_blob(scope: dict, reference: dict):
    expected_scope = canonical_digest(scope)
    if reference.get("scope_digest") != expected_scope:
        raise conflict("Pi blob reference belongs to a different execution")
    async with get_database().get_session() as session:
        row = await session.get(PiRuntimeBlob, reference.get("pi_blob"))
        if row is None or row.scope_digest != expected_scope or row.digest != reference.get("digest") or row.kind != reference.get("kind"):
            raise conflict("Pi blob reference could not be verified")
        payload = row.payload
    if canonical_digest(payload) != reference["digest"]:
        raise conflict("Pi blob contents failed integrity verification")
    return payload
