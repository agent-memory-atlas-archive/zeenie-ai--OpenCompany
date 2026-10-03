"""Generated UI in a chat reply: checking the spec an employee writes.

The ``show_ui`` tool (``nodes/chat/chat_ui``) hands the model's spec here. It
is json-render's flat shape, ``{root, state, elements}``, checked against the
catalog in ``config/chat_genui_catalog.json`` (docs-internal/chat_protocol.md,
"Generated UI"):

- **Refused** (:class:`SpecError`, every problem listed, so the tool fails and
  the model writes it again): a spec that is not an object; a root that is not
  an element; more elements, more depth or more bytes than the limits allow; a
  child that is not an element, or not one its parent may hold; a cycle; a
  prop its component's schema refuses; a state path through ``__proto__``,
  ``constructor`` or ``prototype``; an action the chat keeps for itself.
- **Dropped**, and reported back (``dropped``, ``notes``): elements of a type
  the catalog does not have, elements the root cannot reach, element fields
  json-render must not see (``watch``, ``repeat``, ...), props a component
  does not declare, and expressions that only ``repeat`` gives meaning to.

What comes back is the spec as it will be drawn, in canonical form: every
state path written ``/a/b``, every element keeping only ``type``, ``props``,
``children``, ``visible`` and ``on``, the elements the root reaches only. The
client sanitizes again before rendering (``client/src/lib/jsonRender``); this
side's job is to tell the model before anything is shown.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

CATALOG_PATH = Path(__file__).resolve().parents[2] / "config" / "chat_genui_catalog.json"

FORBIDDEN_SEGMENTS = frozenset({"__proto__", "constructor", "prototype"})
#: Element ids json-render can key on (client/src/lib/jsonRender/sanitize.ts).
_ID = re.compile(r"^[A-Za-z0-9_.:-]+$")
#: Action and event names.
_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
#: Fields an element keeps; anything else is dropped.
_ELEMENT_FIELDS = ("type", "props", "children", "visible", "on")
#: Expressions that only mean something inside ``repeat`` or call app code.
_DROPPED_EXPRESSIONS = ("$computed", "$item", "$index", "$bindItem")
_EXPRESSIONS = ("$state", "$bindState", "$cond", "$template", *_DROPPED_EXPRESSIONS)
#: Actions json-render runs itself that a reply may not use.
_RESERVED_ACTIONS = frozenset({"pushState", "removeState", "push", "pop", "validateForm"})
_COMPARISONS = ("eq", "neq")
_ORDERINGS = ("gt", "gte", "lt", "lte")
_TEMPLATE = re.compile(r"\$\{([^}]+)\}")


class SpecError(ValueError):
    """The spec cannot be shown; ``problems`` says every reason found."""

    def __init__(self, problems: List[str]) -> None:
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass(frozen=True)
class CheckedSpec:
    #: ``{root, state, elements}`` as it will be drawn.
    spec: Dict[str, Any]
    #: ``[{id, reason}]`` for each element left out.
    dropped: List[Dict[str, str]] = field(default_factory=list)
    #: Everything else changed on the way, in words.
    notes: List[str] = field(default_factory=list)

    @property
    def element_count(self) -> int:
        return len(self.spec["elements"])


@lru_cache(maxsize=1)
def load_catalog() -> Mapping[str, Any]:
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


# ----- paths -----------------------------------------------------------------


def _unescape(token: str) -> str:
    return token.replace("~1", "/").replace("~0", "~")


def _escape(token: str) -> str:
    return token.replace("~", "~0").replace("/", "~1")


def canonical_path(path: Any, max_segments: int) -> Optional[str]:
    """A usable state path as ``/a/b``, or None (the same rule as the
    client's ``paths.ts``: empty segments skipped, prototype keys refused)."""
    if not isinstance(path, str):
        return None
    segments = [_unescape(part) for part in path.split("/") if part]
    if not segments or len(segments) > max_segments or any(part in FORBIDDEN_SEGMENTS for part in segments):
        return None
    return "/" + "/".join(_escape(part) for part in segments)


# ----- the checker -----------------------------------------------------------


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _expression_key(value: Any) -> Optional[str]:
    if not _is_record(value):
        return None
    for key in _EXPRESSIONS:
        if key in value:
            return key
    return None


def _describe(error: Any) -> str:
    where = "/".join(str(part) for part in error.absolute_path)
    return f"{where}: {error.message}" if where else error.message


class _Checker:
    def __init__(self, catalog: Mapping[str, Any]) -> None:
        self.catalog = catalog
        self.components: Mapping[str, Any] = catalog["components"]
        self.actions: Mapping[str, Any] = catalog.get("actions") or {}
        limits = catalog["limits"]
        self.max_elements = int(limits["max_elements"])
        self.max_depth = int(limits["max_depth"])
        self.max_bytes = int(limits["max_bytes"])
        self.max_children = int(limits["max_children"])
        self.max_id = int(limits["max_id_length"])
        self.max_segments = int(limits["max_path_segments"])
        self.problems: List[str] = []
        self.notes: List[str] = []
        self.dropped: List[Dict[str, str]] = []

    # -- helpers ------------------------------------------------------------

    def refuse(self, message: str) -> None:
        self.problems.append(message)

    def path(self, raw: Any, where: str) -> Optional[str]:
        path = canonical_path(raw, self.max_segments)
        if path is None:
            self.refuse(
                f"{where}: {raw!r} is not a usable state path (\"/name\", at most {self.max_segments} "
                "parts, no __proto__, constructor or prototype)"
            )
        return path

    def usable_id(self, value: Any) -> bool:
        return (
            isinstance(value, str)
            and 0 < len(value) <= self.max_id
            and bool(_ID.match(value))
            and value not in FORBIDDEN_SEGMENTS
        )

    def json_value(self, value: Any, where: str, depth: int = 0) -> Any:
        """Plain JSON with prototype keys refused."""
        if depth > 8:
            self.refuse(f"{where}: nested too deep")
            return None
        if isinstance(value, dict):
            out: Dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str) or key in FORBIDDEN_SEGMENTS:
                    self.refuse(f"{where}: the key {key!r} is not allowed")
                    continue
                out[key] = self.json_value(item, f"{where}/{key}", depth + 1)
            return out
        if isinstance(value, list):
            return [self.json_value(item, f"{where}/{index}", depth + 1) for index, item in enumerate(value)]
        if isinstance(value, float) and value != value:  # NaN
            self.refuse(f"{where}: not a number")
            return None
        return value

    # -- expressions and conditions -----------------------------------------

    def expression(self, value: Dict[str, Any], where: str, *, bind_prop: bool) -> Tuple[bool, Any]:
        """``(keep, cleaned)`` for an expression-valued prop or param."""
        key = _expression_key(value)
        if key in _DROPPED_EXPRESSIONS:
            self.notes.append(f"{where}: dropped {key}, which only works inside repeat")
            return False, None
        if key == "$bindState":
            if not bind_prop:
                self.refuse(f"{where}: only an input's bound prop may use $bindState; read the state with $state")
                return False, None
            path = self.path(value["$bindState"], where)
            return path is not None, {"$bindState": path}
        if key == "$state":
            path = self.path(value["$state"], where)
            return path is not None, {"$state": path}
        if key == "$template":
            template = value["$template"]
            if not isinstance(template, str):
                self.refuse(f"{where}: $template must be a string")
                return False, None

            def placeholder(match: "re.Match[str]") -> str:
                path = self.path(match.group(1).strip(), where)
                return "${" + path + "}" if path else ""

            return True, {"$template": _TEMPLATE.sub(placeholder, template)}
        # $cond
        condition = self.condition(value.get("$cond"), where)
        branches = {}
        for branch in ("$then", "$else"):
            raw = value.get(branch)
            if _expression_key(raw):
                keep, cleaned = self.expression(raw, f"{where}{branch}", bind_prop=False)
                branches[branch] = cleaned if keep else None
            else:
                branches[branch] = self.json_value(raw, f"{where}{branch}")
        return True, {"$cond": condition, **branches}

    def condition(self, condition: Any, where: str) -> Any:
        if condition is None or isinstance(condition, bool):
            return condition if condition is not None else True
        if isinstance(condition, list):
            return {"$and": [self.condition(item, where) for item in condition]}
        if not _is_record(condition):
            self.refuse(f"{where}: a condition is true, false, a list, or {{\"$state\": \"/path\", ...}}")
            return False
        for joined in ("$and", "$or"):
            if joined in condition:
                items = condition[joined]
                if not isinstance(items, list):
                    self.refuse(f"{where}: {joined} takes a list")
                    return False
                return {joined: [self.condition(item, where) for item in items]}
        if "$state" not in condition:
            self.refuse(f"{where}: a condition reads the state with {{\"$state\": \"/path\"}}")
            return False
        path = self.path(condition["$state"], where)
        out: Dict[str, Any] = {"$state": path}
        for op in _COMPARISONS:
            if op in condition:
                operand = condition[op]
                if _is_record(operand) and "$state" in operand:
                    out[op] = {"$state": self.path(operand["$state"], where)}
                else:
                    out[op] = self.json_value(operand, f"{where}.{op}")
        for op in _ORDERINGS:
            if op in condition:
                operand = condition[op]
                if isinstance(operand, (int, float)) and not isinstance(operand, bool):
                    out[op] = operand
                elif _is_record(operand) and "$state" in operand:
                    out[op] = {"$state": self.path(operand["$state"], where)}
                else:
                    self.refuse(f"{where}: {op} compares with a number")
        if condition.get("not"):
            out["not"] = True
        unknown = sorted(set(condition) - {"$state", "not", *_COMPARISONS, *_ORDERINGS})
        if unknown:
            self.notes.append(f"{where}: ignored {', '.join(unknown)} in a condition")
        return out

    # -- one element --------------------------------------------------------

    def props(self, element_id: str, type_name: str, raw: Any) -> Dict[str, Any]:
        from jsonschema import Draft202012Validator

        component = self.components[type_name]
        schema = component["props"]
        declared: Mapping[str, Any] = schema.get("properties") or {}
        bind = component.get("bind")
        where = f"element {element_id!r} ({type_name})"
        if raw is None:
            raw = {}
        if not _is_record(raw):
            self.refuse(f"{where}: props must be an object")
            return {}
        literal: Dict[str, Any] = {}
        dynamic: Dict[str, Any] = {}
        for key, value in raw.items():
            if key not in declared:
                self.notes.append(f"{where}: dropped the prop {key!r}, which {type_name} does not have")
                continue
            if _expression_key(value):
                keep, cleaned = self.expression(value, f"{where}.{key}", bind_prop=key == bind)
                if keep:
                    dynamic[key] = cleaned
                continue
            literal[key] = self.json_value(value, f"{where}.{key}")
        required = [name for name in schema.get("required") or [] if name not in dynamic]
        checked = {**schema, "required": required}
        for error in sorted(Draft202012Validator(checked).iter_errors(literal), key=lambda item: list(item.absolute_path)):
            self.refuse(f"{where}: {_describe(error)}")
        return {**literal, **dynamic}

    def binding(self, element_id: str, raw: Any) -> Optional[Dict[str, Any]]:
        from jsonschema import Draft202012Validator

        where = f"element {element_id!r} on.press"
        if not _is_record(raw):
            self.refuse(f"{where}: an action binding is {{\"action\": \"name\", \"params\": {{...}}}}")
            return None
        action = raw.get("action")
        if not isinstance(action, str) or not _NAME.match(action) or action in FORBIDDEN_SEGMENTS:
            self.refuse(f"{where}: {action!r} is not an action name (a letter, then letters, digits or _)")
            return None
        if action in _RESERVED_ACTIONS:
            self.refuse(f"{where}: {action} is not available in a reply")
            return None
        params = raw.get("params", {})
        if params is None:
            params = {}
        if not _is_record(params):
            self.refuse(f"{where}: params must be an object")
            return None
        literal: Dict[str, Any] = {}
        dynamic: Dict[str, Any] = {}
        for key, value in params.items():
            if not isinstance(key, str) or key in FORBIDDEN_SEGMENTS or key.startswith("__"):
                self.refuse(f"{where}: the param {key!r} is not allowed")
                continue
            if _expression_key(value):
                keep, cleaned = self.expression(value, f"{where}.params.{key}", bind_prop=False)
                if keep:
                    dynamic[key] = cleaned
                continue
            literal[key] = self.json_value(value, f"{where}.params.{key}")
        declared = self.actions.get(action)
        if declared is not None:
            schema = declared.get("params") or {"type": "object"}
            required = [name for name in schema.get("required") or [] if name not in dynamic]
            for error in Draft202012Validator({**schema, "required": required}).iter_errors(literal):
                self.refuse(f"{where} ({action}): {_describe(error)}")
            if action == "setState" and "statePath" in literal:
                literal["statePath"] = self.path(literal["statePath"], f"{where}.params.statePath")
        unknown = sorted(set(raw) - {"action", "params"})
        if unknown:
            self.notes.append(f"{where}: dropped {', '.join(unknown)} from the action binding")
        binding: Dict[str, Any] = {"action": action}
        if literal or dynamic:
            binding["params"] = {**literal, **dynamic}
        return binding

    def element(self, element_id: str, raw: Any) -> Optional[Dict[str, Any]]:
        if not _is_record(raw):
            self.refuse(f"element {element_id!r}: an element is {{\"type\": ..., \"props\": {{...}}}}")
            return None
        type_name = raw.get("type")
        if type_name not in self.components:
            self.dropped.append({"id": element_id, "reason": f"unknown type {type_name!r}"})
            return None
        component = self.components[type_name]
        extra = sorted(set(raw) - set(_ELEMENT_FIELDS))
        if extra:
            self.notes.append(f"element {element_id!r}: dropped {', '.join(extra)}")
        element: Dict[str, Any] = {"type": type_name, "props": self.props(element_id, type_name, raw.get("props"))}

        children = raw.get("children")
        if children not in (None, []):
            if not component.get("children"):
                self.refuse(f"element {element_id!r} ({type_name}) cannot hold children")
            elif not isinstance(children, list) or not all(isinstance(child, str) for child in children):
                self.refuse(f"element {element_id!r}: children is a list of element ids")
            elif len(children) > self.max_children:
                self.refuse(f"element {element_id!r}: more than {self.max_children} children")
            elif len(set(children)) != len(children):
                self.refuse(f"element {element_id!r}: a child is listed twice")
            else:
                element["children"] = list(children)
        elif component.get("children"):
            element["children"] = []

        if "visible" in raw:
            element["visible"] = self.condition(raw["visible"], f"element {element_id!r} visible")

        on = raw.get("on")
        if on is not None:
            events = component.get("events") or []
            if not _is_record(on):
                self.refuse(f"element {element_id!r}: on maps an event to an action binding")
            else:
                kept = {}
                for event, binding in on.items():
                    if event not in events:
                        self.notes.append(f"element {element_id!r}: dropped the event {event!r}, which {type_name} does not have")
                        continue
                    cleaned = self.binding(element_id, binding)
                    if cleaned is not None:
                        kept[event] = cleaned
                if kept:
                    element["on"] = kept
        return element

    # -- the whole spec -----------------------------------------------------

    def check(self, raw: Any) -> CheckedSpec:
        if not _is_record(raw):
            raise SpecError(["the spec is an object: {\"root\": \"<id>\", \"state\": {...}, \"elements\": {...}}"])
        size = len(json.dumps(raw, ensure_ascii=False, default=str).encode("utf-8"))
        if size > self.max_bytes:
            raise SpecError([f"the spec is {size} bytes; at most {self.max_bytes}"])
        elements_raw = raw.get("elements")
        if not _is_record(elements_raw) or not elements_raw:
            raise SpecError(["elements must map element ids to elements"])
        root = raw.get("root")
        if not self.usable_id(root):
            raise SpecError([f"root must name an element (letters, digits and _.:-, at most {self.max_id})"])

        state_raw = raw.get("state", {})
        if state_raw is None:
            state_raw = {}
        if not _is_record(state_raw):
            self.refuse("state must be an object")
            state_raw = {}
        state = self.json_value(state_raw, "state")

        elements: Dict[str, Dict[str, Any]] = {}
        for element_id, element_raw in elements_raw.items():
            if not self.usable_id(element_id):
                self.refuse(f"{element_id!r} is not a usable element id (letters, digits and _.:-, at most {self.max_id})")
                continue
            element = self.element(element_id, element_raw)
            if element is not None:
                elements[element_id] = element

        dropped_ids = {item["id"] for item in self.dropped}
        if root not in elements:
            self.refuse(f"the root {root!r} is not an element" + (" of a known type" if root in dropped_ids else ""))
            raise SpecError(self.problems)

        # Reach the tree from the root: every child an element it may hold,
        # no cycles, not too deep.
        reached: List[str] = []
        visiting: set = set()

        def walk(element_id: str, depth: int, parent: Optional[str]) -> None:
            if element_id in visiting:
                self.refuse(f"element {element_id!r} contains itself")
                return
            if element_id in reached:
                self.refuse(f"element {element_id!r} has two parents")
                return
            if depth > self.max_depth:
                self.refuse(f"the UI is nested more than {self.max_depth} deep at {element_id!r}")
                return
            reached.append(element_id)
            visiting.add(element_id)
            element = elements[element_id]
            allowed = self.components[element["type"]].get("children")
            kept_children = []
            for child in element.get("children", []):
                if child in dropped_ids:
                    self.notes.append(f"element {element_id!r}: left out the child {child!r} (unknown type)")
                    continue
                if child not in elements:
                    self.refuse(f"element {element_id!r}: the child {child!r} is not an element")
                    continue
                child_type = elements[child]["type"]
                if allowed != "any" and child_type not in allowed:
                    self.refuse(f"element {element_id!r} ({element['type']}) cannot hold {child_type} {child!r}; it holds {', '.join(allowed)}")
                    continue
                kept_children.append(child)
                walk(child, depth + 1, element_id)
            if "children" in element:
                element["children"] = kept_children
            visiting.discard(element_id)

        walk(root, 1, None)
        if len(reached) > self.max_elements:
            self.refuse(f"the UI has {len(reached)} elements; at most {self.max_elements}")
        for element_id in elements:
            if element_id not in reached:
                self.dropped.append({"id": element_id, "reason": "not reachable from the root"})
        if self.problems:
            raise SpecError(self.problems)
        spec = {"root": root, "state": state, "elements": {element_id: elements[element_id] for element_id in reached}}
        return CheckedSpec(spec=spec, dropped=list(self.dropped), notes=list(self.notes))


def check_spec(raw: Any, catalog: Optional[Mapping[str, Any]] = None) -> CheckedSpec:
    """The spec as it will be drawn, or :class:`SpecError` saying why not."""
    return _Checker(catalog or load_catalog()).check(raw)


# ----- the tool description --------------------------------------------------


def _type_text(schema: Mapping[str, Any]) -> str:
    if "enum" in schema:
        return "|".join(json.dumps(value) for value in schema["enum"])
    kind = schema.get("type")
    if isinstance(kind, list):
        return "|".join(kind)
    if kind == "array":
        items = schema.get("items") or {}
        if items.get("type") == "object":
            return f"[{_object_text(items)}]"
        return f"[{_type_text(items)}]"
    if kind == "object":
        return _object_text(schema)
    return str(kind or "any")


def _object_text(schema: Mapping[str, Any]) -> str:
    required = set(schema.get("required") or [])
    parts = []
    for name, prop in (schema.get("properties") or {}).items():
        mark = "" if name in required else "?"
        parts.append(f"{name}{mark}: {_type_text(prop)}")
    return "{" + ", ".join(parts) + "}"


def describe_catalog(catalog: Optional[Mapping[str, Any]] = None) -> str:
    """The show_ui tool's description, from the catalog. The same catalog
    always gives the same text, byte for byte."""
    catalog = catalog or load_catalog()
    limits = catalog["limits"]
    lines = [
        "Show the owner a small interface inside your reply: a choice to make, a short form, numbers, a chart, "
        "buttons. Use it when that says it better than words; otherwise just answer. Write your reply as usual: "
        "the interface shows under it.",
        "",
        "The spec is json-render's flat shape: {\"root\": \"<id>\", \"state\": {...}, \"elements\": {\"<id>\": "
        "{\"type\": \"<Component>\", \"props\": {...}, \"children\": [\"<id>\", ...], \"visible\": <condition>, "
        "\"on\": {\"press\": {\"action\": \"<name>\", \"params\": {...}}}}}}. Element ids are letters, digits and _.:-.",
        "",
        "Components:",
    ]
    for name, component in catalog["components"].items():
        role = component["role"]
        children = component.get("children")
        held = ""
        if children == "any":
            held = "; holds any components"
        elif children:
            held = "; holds " + ", ".join(children)
        bind = f"; binds {component['bind']}" if component.get("bind") else ""
        lines.append(f"- {name} ({role}{held}{bind}): {component['description']} Props: {_object_text(component['props'])}.")
    lines += [
        "",
        "State: an input reads and writes its bound prop with {\"$bindState\": \"/path\"}; any prop may read the "
        "state with {\"$state\": \"/path\"} or {\"$template\": \"Hi ${/name}\"}. Put each bound value in \"state\" "
        "first. visible: {\"$state\": \"/path\"} (truthy), with \"eq\"/\"neq\": value, or \"not\": true; a list "
        "means all of them.",
        "",
        "Actions (on.press of a Button; params may read the state with {\"$state\": \"/path\"}, read at the press):",
    ]
    for name, action in (catalog.get("actions") or {}).items():
        lines.append(f"- {name} {_object_text(action.get('params') or {})}: {action['description']}")
    lines += [
        f"- {catalog['custom_actions']}",
        "",
        f"Limits: at most {limits['max_elements']} elements, nested at most {limits['max_depth']} deep, "
        f"{limits['max_children']} children each, {limits['max_bytes']} bytes. A spec that breaks a rule is refused "
        "with every reason; write it again.",
    ]
    return "\n".join(lines)


__all__ = [
    "CATALOG_PATH",
    "CheckedSpec",
    "SpecError",
    "canonical_path",
    "check_spec",
    "describe_catalog",
    "load_catalog",
]
