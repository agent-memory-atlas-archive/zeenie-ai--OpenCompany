"""A generated UI as a stream of JSON patches, and applying them.

A reply's UI reaches the chat as RFC 6902 patches on an empty spec
(docs-internal/chat_protocol.md, "Generated UI"): ``add /root``, ``add
/state``, then one ``add /elements/<id>`` per element, depth first from the
root, each id escaped as RFC 6901 says. ``spec_to_patches`` writes that
stream; the design handoff's examples hold it line for line
(``client/src/features/chat/__fixtures__/*.patches.jsonl``), and the client
writes the same one (``client/src/lib/jsonRender/reveal.ts``).

``apply_patch`` folds patches into an activity's content for the run
snapshot (``services/chat/reducer.py``): ``add``, ``replace`` and ``remove``
only, a copy each time, nothing through ``__proto__``, ``constructor`` or
``prototype``, and an operation that does not apply is skipped. The client
applies them the same way (``client/src/lib/agui/patch.ts``).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Mapping, Optional

FORBIDDEN_SEGMENTS = frozenset({"__proto__", "constructor", "prototype"})


def escape_pointer(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def unescape_pointer(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def spec_to_patches(spec: Mapping[str, Any]) -> List[Dict[str, Any]]:
    """The patch stream that builds ``spec`` from ``{root: "", state: {},
    elements: {}}``: root, state, then every element the root reaches,
    depth first, each once."""
    elements: Mapping[str, Any] = spec.get("elements") or {}
    patches: List[Dict[str, Any]] = [
        {"op": "add", "path": "/root", "value": spec.get("root")},
        {"op": "add", "path": "/state", "value": spec.get("state") or {}},
    ]
    seen = set()

    def walk(element_id: str) -> None:
        element = elements.get(element_id)
        if element is None or element_id in seen:
            return
        seen.add(element_id)
        patches.append({"op": "add", "path": f"/elements/{escape_pointer(element_id)}", "value": element})
        for child in element.get("children") or []:
            walk(child)

    root = spec.get("root")
    if isinstance(root, str):
        walk(root)
    return patches


def _segments(path: Any) -> Optional[List[str]]:
    if not isinstance(path, str):
        return None
    if path == "":
        return []
    if not path.startswith("/"):
        return None
    segments = [unescape_pointer(part) for part in path[1:].split("/")]
    if any(part in FORBIDDEN_SEGMENTS for part in segments):
        return None
    return segments


class _Refused(Exception):
    pass


def _write(node: Any, segments: List[str], index: int, op: str, value: Any) -> Any:
    key = segments[index]
    last = index == len(segments) - 1
    if isinstance(node, list):
        if key == "-":
            if op != "add" or not last:
                raise _Refused
            at = len(node)
        else:
            try:
                at = int(key)
            except ValueError:
                raise _Refused from None
            if at < 0 or at > len(node) or (not key.isdigit()):
                raise _Refused
        out = list(node)
        if not last:
            if at >= len(node):
                raise _Refused
            out[at] = _write(node[at], segments, index + 1, op, value)
            return out
        if op == "add":
            out.insert(at, copy.deepcopy(value))
        elif at >= len(node):
            raise _Refused
        elif op == "replace":
            out[at] = copy.deepcopy(value)
        else:
            del out[at]
        return out
    if not isinstance(node, dict):
        raise _Refused
    if not last:
        if key not in node:
            raise _Refused
        return {**node, key: _write(node[key], segments, index + 1, op, value)}
    if op == "remove":
        if key not in node:
            raise _Refused
        return {name: item for name, item in node.items() if name != key}
    if op == "replace" and key not in node:
        raise _Refused
    return {**node, key: copy.deepcopy(value)}


def apply_patch_op(content: Any, op: Mapping[str, Any]) -> Any:
    """``content`` with one operation applied; ``content`` itself when the
    operation does not apply."""
    kind = op.get("op") if isinstance(op, Mapping) else None
    if kind not in ("add", "replace", "remove"):
        return content
    segments = _segments(op.get("path"))
    if segments is None:
        return content
    if not segments:
        return content if kind == "remove" else copy.deepcopy(op.get("value"))
    try:
        return _write(content, segments, 0, kind, op.get("value"))
    except _Refused:
        return content


def apply_patch(content: Any, ops: Iterable[Mapping[str, Any]]) -> Any:
    """``content`` with every operation applied in order, skipping the ones
    that do not apply."""
    for op in ops:
        content = apply_patch_op(content, op)
    return content


__all__ = ["apply_patch", "apply_patch_op", "escape_pointer", "spec_to_patches", "unescape_pointer"]
