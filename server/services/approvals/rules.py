"""A workflow's Ask first rule (``workflow_rules``), read when something would
send.

One row per workflow. An employee's row is seeded from its ground rules the
first time anything asks (``register_rule_seeder``: the employees package
registers how; this package never imports it), and the chat's Ask first chip
changes it (``set_ask_first``, a compare-and-swap on ``revision``), with no
restart: approval gates and held tool calls read it each time.

A workflow with no row and no seed (one built in the editor) has no rule:
its tool calls are never held, and its approval gates wait for the owner as
they always did.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Union

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError

from core.logging import get_logger
from models.approvals import WorkflowRule

logger = get_logger(__name__)

#: ``seed(database, workflow_id)`` -> the workflow's Ask first value when it
#: has a default (an employee's ground rules), else None.
RuleSeeder = Callable[[Any, str], Union[Optional[bool], Awaitable[Optional[bool]]]]
_SEEDERS: List[RuleSeeder] = []
#: ``listener(database, workflow_id, rule)``, told when the owner changes a
#: rule; it may answer what the change means for the workflow
#: (``{replies_gated, needs_apply}``).
RuleListener = Callable[[Any, str, Any], Union[Optional[Dict[str, Any]], Awaitable[Optional[Dict[str, Any]]]]]
_LISTENERS: List[RuleListener] = []


class RuleConflict(ValueError):
    """The rule changed since the caller read it."""

    def __init__(self, rule: Optional[WorkflowRule]) -> None:
        super().__init__("rule_conflict")
        self.rule = rule


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def register_rule_seeder(seeder: RuleSeeder) -> None:
    if seeder not in _SEEDERS:
        _SEEDERS.append(seeder)


def register_rule_listener(listener: RuleListener) -> None:
    if listener not in _LISTENERS:
        _LISTENERS.append(listener)


async def notify_rule_changed(database: Any, workflow_id: str, rule: Any) -> Dict[str, Any]:
    """Tell the listeners; returns what they answered, merged. A failing
    listener is logged and never undoes the change."""
    effects: Dict[str, Any] = {}
    for listener in list(_LISTENERS):
        try:
            answer = listener(database, workflow_id, rule)
            if inspect.isawaitable(answer):
                answer = await answer
        except Exception:
            logger.warning("Ask first listener failed", workflow_id=workflow_id, exc_info=True)
            continue
        if isinstance(answer, dict):
            effects.update(answer)
    return effects


async def get_rule(database: Any, workflow_id: str) -> Optional[WorkflowRule]:
    """The workflow's rule row, seeding it when it has a default."""
    if not workflow_id:
        return None
    async with database.get_session() as session:
        rule = await session.get(WorkflowRule, workflow_id)
    if rule is not None:
        return rule
    for seeder in list(_SEEDERS):
        try:
            value = seeder(database, workflow_id)
            if inspect.isawaitable(value):
                value = await value
        except Exception:
            logger.warning("Could not read a workflow's default Ask first rule", workflow_id=workflow_id, exc_info=True)
            continue
        if value is None:
            continue
        return await _insert(database, workflow_id, bool(value), source="hire")
    return None


async def ask_first(database: Any, workflow_id: Optional[str]) -> Optional[bool]:
    """Whether the workflow asks before sending: True, False, or None when
    it has no rule."""
    rule = await get_rule(database, workflow_id or "")
    return None if rule is None else bool(rule.ask_first)


async def _insert(database: Any, workflow_id: str, value: bool, *, source: str) -> WorkflowRule:
    row = WorkflowRule(workflow_id=workflow_id, ask_first=value, revision=0, source=source, updated_at=_utcnow())
    try:
        async with database.get_session() as session:
            session.add(row)
            await session.commit()
        return row
    except IntegrityError:
        async with database.get_session() as session:
            existing = await session.get(WorkflowRule, workflow_id)
        if existing is None:
            raise
        return existing


async def set_ask_first(database: Any, workflow_id: str, value: bool, *, expected_revision: Optional[int] = None) -> WorkflowRule:
    """Change the rule. ``expected_revision`` (the revision the caller read)
    makes it a compare-and-swap; :class:`RuleConflict` when it moved."""
    current = await get_rule(database, workflow_id)
    if current is None:
        if expected_revision not in (None, 0):
            raise RuleConflict(None)
        return await _insert(database, workflow_id, bool(value), source="owner")
    if expected_revision is not None and current.revision != expected_revision:
        raise RuleConflict(current)
    async with database.get_session() as session:
        result = await session.execute(
            update(WorkflowRule)
            .where(WorkflowRule.workflow_id == workflow_id, WorkflowRule.revision == current.revision)
            .values(ask_first=bool(value), revision=current.revision + 1, source="owner", updated_at=_utcnow())
        )
        await session.commit()
        if not result.rowcount:
            async with database.get_session() as again:
                raise RuleConflict(await again.get(WorkflowRule, workflow_id))
    async with database.get_session() as session:
        updated = await session.get(WorkflowRule, workflow_id)
    assert updated is not None
    return updated


async def delete_rule(database: Any, workflow_id: str) -> None:
    from sqlalchemy import delete

    async with database.get_session() as session:
        await session.execute(delete(WorkflowRule).where(WorkflowRule.workflow_id == workflow_id))
        await session.commit()


__all__ = [
    "RuleConflict",
    "ask_first",
    "delete_rule",
    "get_rule",
    "notify_rule_changed",
    "register_rule_listener",
    "register_rule_seeder",
    "set_ask_first",
]
