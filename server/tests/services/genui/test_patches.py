"""A generated UI as JSON patches (services/genui/patches.py): the stream
matches the design handoff's examples line for line, folding it back builds
the spec, and applying patches never reaches a prototype, never changes its
input, and skips what does not apply."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from services.genui.patches import apply_patch, apply_patch_op, escape_pointer, spec_to_patches, unescape_pointer

FIXTURES = Path(__file__).resolve().parents[4] / "client" / "src" / "features" / "chat" / "__fixtures__"
EMPTY = {"root": "", "state": {}, "elements": {}}


@pytest.mark.parametrize("name", ["saturday-booking", "reply-insights", "reminders"])
def test_the_stream_matches_the_handoff_line_for_line(name):
    spec = json.loads((FIXTURES / f"{name}.spec.json").read_text(encoding="utf-8"))
    golden = [json.loads(line) for line in (FIXTURES / f"{name}.patches.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert spec_to_patches(spec) == golden
    assert apply_patch(copy.deepcopy(EMPTY), golden) == {"root": spec["root"], "state": spec["state"], "elements": spec["elements"]}


def test_ids_are_escaped_and_unreachable_elements_left_out():
    spec = {
        "root": "a/b",
        "state": {},
        "elements": {"a/b": {"type": "Stack", "children": ["x~y"]}, "x~y": {"type": "Text"}, "lost": {"type": "Text"}},
    }
    paths = [patch["path"] for patch in spec_to_patches(spec)]
    assert paths == ["/root", "/state", "/elements/a~1b", "/elements/x~0y"]
    assert unescape_pointer(escape_pointer("a/~b")) == "a/~b"


def test_add_replace_remove():
    content = {"a": {"b": [1, 2]}}
    assert apply_patch_op(content, {"op": "add", "path": "/a/c", "value": 3}) == {"a": {"b": [1, 2], "c": 3}}
    assert apply_patch_op(content, {"op": "add", "path": "/a/b/1", "value": 9}) == {"a": {"b": [1, 9, 2]}}
    assert apply_patch_op(content, {"op": "add", "path": "/a/b/-", "value": 9}) == {"a": {"b": [1, 2, 9]}}
    assert apply_patch_op(content, {"op": "replace", "path": "/a/b/0", "value": 7}) == {"a": {"b": [7, 2]}}
    assert apply_patch_op(content, {"op": "remove", "path": "/a/b/0"}) == {"a": {"b": [2]}}
    assert apply_patch_op(content, {"op": "remove", "path": "/a"}) == {}
    assert apply_patch_op(content, {"op": "replace", "path": "", "value": {"new": 1}}) == {"new": 1}
    assert content == {"a": {"b": [1, 2]}}, "the input changed"


@pytest.mark.parametrize(
    "op",
    [
        {"op": "add", "path": "/__proto__/polluted", "value": True},
        {"op": "add", "path": "/a/constructor", "value": 1},
        {"op": "replace", "path": "/missing", "value": 1},
        {"op": "remove", "path": "/missing"},
        {"op": "add", "path": "/a/x/y", "value": 1},
        {"op": "add", "path": "/a/b/5", "value": 1},
        {"op": "replace", "path": "/a/b/-", "value": 1},
        {"op": "move", "from": "/a", "path": "/b"},
        {"op": "add", "path": "no-slash", "value": 1},
        {"op": "add", "path": "/a/b/x", "value": 1},
    ],
)
def test_what_does_not_apply_is_skipped(op):
    content = {"a": {"b": [1, 2]}}
    assert apply_patch_op(content, op) is content
