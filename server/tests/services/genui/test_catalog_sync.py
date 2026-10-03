"""The chat's generated-UI vocabulary on both sides: the manifest the server
checks every spec against (config/chat_genui_catalog.json) and the client's
catalogue (client/src/features/chat/genui/catalog.ts). A component the
server accepts but the client cannot draw would show as nothing; one the
client knows but the server refuses is dead code. This reads the TypeScript
off disk; the client's own test reads the manifest the same way."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from services.genui.spec import load_catalog

CATALOG_TS = Path(__file__).resolve().parents[4] / "client" / "src" / "features" / "chat" / "genui" / "catalog.ts"


@pytest.fixture(scope="module")
def catalog_ts() -> str:
    if not CATALOG_TS.exists():
        pytest.skip(f"client source not present: {CATALOG_TS}")
    return CATALOG_TS.read_text(encoding="utf-8")


def _block(source: str, name: str) -> str:
    match = re.search(rf"export const {name}\b[^=]*= (\[.*?\]|\{{.*?\}}) (?:as const|satisfies)", source, re.DOTALL)
    assert match, f"could not find {name}"
    return match.group(1)


def test_the_same_components_in_the_same_order(catalog_ts):
    assert re.findall(r"'([^']*)'", _block(catalog_ts, "CHAT_COMPONENT_TYPES")) == list(load_catalog()["components"])


def test_roles_bound_props_and_children(catalog_ts):
    components = load_catalog()["components"]
    roles = dict(re.findall(r"(\w+): '(\w+)'", _block(catalog_ts, "COMPONENT_ROLES")))
    assert roles == {name: component["role"] for name, component in components.items()}
    binds = dict(re.findall(r"(\w+): '(\w+)'", _block(catalog_ts, "BIND_PROPS")))
    assert binds == {name: component["bind"] for name, component in components.items() if component.get("bind")}
    children_block = _block(catalog_ts, "COMPONENT_CHILDREN")
    children = {}
    for name, value in re.findall(r"(\w+): ('any'|\[[^\]]*\])", children_block):
        children[name] = "any" if value == "'any'" else re.findall(r"'([^']*)'", value)
    assert children == {name: component["children"] for name, component in components.items() if component.get("children")}


def test_actions_and_limits(catalog_ts):
    catalog = load_catalog()
    assert sorted(re.findall(r"'([^']*)'", _block(catalog_ts, "CHAT_ACTIONS"))) == sorted(catalog["actions"])
    limits = {key: int(value) for key, value in re.findall(r"(\w+): (\d+)", _block(catalog_ts, "CHAT_LIMITS"))}
    names = {
        "maxElements": "max_elements",
        "maxDepth": "max_depth",
        "maxBytes": "max_bytes",
        "maxChildren": "max_children",
        "maxIdLength": "max_id_length",
        "maxPathSegments": "max_path_segments",
    }
    assert {names[key]: value for key, value in limits.items()} == catalog["limits"]
