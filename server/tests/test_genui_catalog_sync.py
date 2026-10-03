"""The setup-screen vocabulary is written twice: the server's manifest
(config/genui_catalog.json), which the prompt is generated from, and the
client's renderer (client/src/features/home/genui/catalog.ts, plus the hire
payload's trigger lists in hirePayload.ts). A component the prompt offers
but the renderer lacks is silently dropped from every screen; one the
renderer knows but the prompt never mentions is dead code. This reads the
TypeScript off disk and holds the two to each other.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from services.employees.genui_catalog import load_genui_catalog

REPO_ROOT = Path(__file__).resolve().parents[2]
GENUI = REPO_ROOT / "client" / "src" / "features" / "home" / "genui"
CATALOG_TS = GENUI / "catalog.ts"
HIRE_PAYLOAD_TS = GENUI / "hirePayload.ts"


def _source(path: Path) -> str:
    if not path.exists():
        pytest.skip(f"client source not present: {path}")
    return path.read_text(encoding="utf-8")


def _string_list(source: str, name: str) -> list:
    match = re.search(rf"export const {name} = \[(.*?)\] as const;", source, re.DOTALL)
    assert match, f"could not find the literal list {name}"
    return re.findall(r"'([^']*)'", match.group(1))


def _object_literal(source: str, name: str) -> dict:
    match = re.search(rf"export const {name}[^=]*= \{{(.*?)\}}", source, re.DOTALL)
    assert match, f"could not find the literal object {name}"
    body = match.group(1)
    pairs = re.findall(r"(\w+):\s*('([^']*)'|(\d+))", body)
    return {key: (int(number) if number else text) for key, _whole, text, number in pairs}


@pytest.fixture(scope="module")
def manifest():
    return load_genui_catalog()


@pytest.fixture(scope="module")
def catalog_ts():
    return _source(CATALOG_TS)


def test_components_match_in_order(manifest, catalog_ts):
    assert _string_list(catalog_ts, "COMPONENT_TYPES") == list(manifest["components"])


def test_container_and_control_types_match(manifest, catalog_ts):
    assert _string_list(catalog_ts, "CONTAINER_TYPES") == manifest["container_types"]
    assert _string_list(catalog_ts, "CONTROL_TYPES") == manifest["control_types"]


def test_actions_and_aliases_match(manifest, catalog_ts):
    assert _string_list(catalog_ts, "ACTION_TYPES") == list(manifest["actions"])
    assert _object_literal(catalog_ts, "ACTION_ALIASES") == manifest["action_aliases"]


def test_tones_and_roles_match(manifest, catalog_ts):
    assert _string_list(catalog_ts, "TONES") == manifest["tones"]
    assert _string_list(catalog_ts, "STEP_ROLES") == manifest["step_roles"]


def test_state_paths_and_the_ask_first_label_match(manifest, catalog_ts):
    assert _object_literal(catalog_ts, "STATE_PATHS") == manifest["state_paths"]
    label = re.search(r"export const ASK_FIRST_LABEL = '([^']*)';", catalog_ts)
    assert label and label.group(1) == manifest["ask_first_label"]


def test_limits_match(manifest, catalog_ts):
    assert _object_literal(catalog_ts, "LIMITS") == manifest["limits"]


def test_trigger_lists_match():
    source = _source(HIRE_PAYLOAD_TS)
    manifest = load_genui_catalog()
    assert _string_list(source, "TRIGGER_KINDS") == manifest["trigger"]["kinds"]
    assert _string_list(source, "SCHEDULE_EVERY") == manifest["trigger"]["every"]
    assert _string_list(source, "SCHEDULE_TIMES") == manifest["trigger"]["times"]


def test_schedule_choices_are_the_ones_the_builder_runs():
    """The setup screen offers only times and weekdays the employee's
    schedule can run at, so what the owner picks is never moved to another
    on the way in (services/employees/builder.py snaps anything else)."""
    from services.employees.builder import SCHEDULE_TIMES, WEEKDAYS

    assert load_genui_catalog()["trigger"]["times"] == list(SCHEDULE_TIMES)
    assert sorted(_string_list(_source(HIRE_PAYLOAD_TS), "WEEKDAYS")) == sorted(WEEKDAYS)


def test_inserted_components_are_plain_leaves(manifest):
    """The client inserts these itself; neither the model nor the rules
    card's control filing may own them."""
    inserted = [name for name, spec in manifest["components"].items() if spec.get("inserted")]
    assert inserted == ["Schedule"]
    for name in inserted:
        assert name not in manifest["container_types"] and name not in manifest["control_types"]


def test_every_component_has_a_prop_schema(catalog_ts):
    schemas = re.search(r"export const PROP_SCHEMAS = \{(.*)\} satisfies", catalog_ts, re.DOTALL)
    assert schemas, "could not find PROP_SCHEMAS"
    declared = set(re.findall(r"^  (\w+): z\.", schemas.group(1), re.MULTILINE))
    assert declared == set(_string_list(catalog_ts, "COMPONENT_TYPES"))


def _prop_schema(catalog_ts: str, name: str) -> str:
    """One component's schema in PROP_SCHEMAS, up to the next component."""
    schemas = re.search(r"export const PROP_SCHEMAS = \{(.*)\} satisfies", catalog_ts, re.DOTALL)
    assert schemas, "could not find PROP_SCHEMAS"
    match = re.search(rf"^  {name}: z\.(.*?)(?=^  \w+: z\.|\Z)", schemas.group(1), re.MULTILINE | re.DOTALL)
    assert match, f"could not find the {name} schema"
    return match.group(1)


def test_the_spec_is_in_json_renders_shape_on_both_sides(manifest, catalog_ts):
    """spec_version 2: a Toggle binds `checked`, and a Button's action is
    the element's on.press, never a prop. The prompt says so and the
    renderer reads the same props."""
    assert "checked:" in manifest["components"]["Toggle"]["props"]
    assert re.search(r"\bchecked:", _prop_schema(catalog_ts, "Toggle"))
    assert not re.search(r"\bvalue:", _prop_schema(catalog_ts, "Toggle"))
    assert "action" not in manifest["components"]["Button"]["props"]
    assert '"on":{"press":' in manifest["components"]["Button"]["description"]
    assert not re.search(r"\baction(Params)?:", _prop_schema(catalog_ts, "Button"))


def test_manifest_is_plain_json():
    raw = (Path(__file__).resolve().parents[1] / "config" / "genui_catalog.json").read_text(encoding="utf-8")
    assert json.loads(raw)["spec_version"] == 2
