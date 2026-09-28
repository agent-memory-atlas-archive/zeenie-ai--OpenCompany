"""services/employees/policy.py: what a hired employee may be given, the one
rule Hire and later additions share, and refusals an agent can relay."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.employees.apps import get_app
from services.employees.genui_catalog import load_genui_catalog
from services.employees.hire_request import HireEmployeeRequest
from services.employees.policy import BASE_TOOLS, asks_first, check_skill, check_tool

ASKING_FIRST = SimpleNamespace(rules={"ask_first": True, "items": []})
NOT_ASKING_FIRST = SimpleNamespace(rules={"ask_first": False})
EVERYTHING = lambda _node_type: True  # noqa: E731


def test_asks_first_reads_a_saved_row_or_a_hire_request_and_defaults_to_on():
    assert asks_first(ASKING_FIRST) is True
    assert asks_first(NOT_ASKING_FIRST) is False
    assert asks_first(SimpleNamespace(rules={})) is True
    request = HireEmployeeRequest.model_validate({"idempotency_key": "k", "job": "j", "name": "n", "role": "r", "steps": [], "rules": {"ask_first": False}})
    assert asks_first(request) is False


def test_the_tools_every_hire_gets_are_always_allowed():
    for base in BASE_TOOLS:
        decision = check_tool(base.type, employee=ASKING_FIRST, connected=set(), allowed=EVERYTHING)
        assert decision.allowed and not decision.code
        assert (decision.label, decision.role, dict(decision.params)) == (base.label, base.role, dict(base.params))
    refused = check_tool("simpleMemory", employee=ASKING_FIRST, connected=None, allowed=lambda node_type: node_type != "simpleMemory")
    assert (refused.allowed, refused.code) == (False, "not_allowed")
    assert refused.reason == "Memory isn't available to hired employees."


def test_an_app_tool_comes_with_its_registry_label_and_params():
    decision = check_tool("googleCalendar", employee=NOT_ASKING_FIRST, connected={"google_calendar"}, allowed=EVERYTHING)
    assert decision.allowed and decision.app is get_app("google_calendar")
    assert decision.label == "Calendar"
    assert dict(decision.params) == {"operation": "list", "calendar_id": "primary", "send_updates": "none"}
    assert decision.read_only is False


def test_asking_first_leaves_out_what_sends_or_spends():
    rule = load_genui_catalog()["ask_first_label"]
    calendar = check_tool("googleCalendar", employee=ASKING_FIRST, connected={"google_calendar"}, allowed=EVERYTHING)
    assert (calendar.allowed, calendar.code) == (False, "asks_first")
    assert calendar.reason == f'Google Calendar can send things on your behalf, so it stays off while "{rule}" is on.'
    stripe = check_tool("stripeAction", employee=ASKING_FIRST, connected={"stripe"}, allowed=EVERYTHING)
    assert stripe.reason == f'Stripe can spend money, so it stays off while "{rule}" is on.'
    sheets = check_tool("googleSheets", employee=ASKING_FIRST, connected={"google_sheets"}, allowed=EVERYTHING)
    assert sheets.allowed  # it writes, but sends nothing


def test_the_browser_stays_read_only_while_asking_first():
    decision = check_tool("browser", employee=ASKING_FIRST, connected={"web"}, allowed=EVERYTHING)
    assert decision.allowed and decision.read_only and decision.role == "browser"
    assert dict(decision.params) == {"interaction": "read_only"}
    full = check_tool("browser", employee=NOT_ASKING_FIRST, connected={"web"}, allowed=EVERYTHING)
    assert dict(full.params) == {"interaction": "full"} and not full.read_only


def test_asking_first_is_decided_before_the_allowlist():
    # Hire skips such a tool with a warning instead of failing the hire.
    decision = check_tool("stripeAction", employee=ASKING_FIRST, connected=None, allowed=lambda _node_type: False)
    assert decision.code == "asks_first"
    refused = check_tool("stripeAction", employee=NOT_ASKING_FIRST, connected=None, allowed=lambda _node_type: False)
    assert (refused.code, refused.reason) == ("not_allowed", "Stripe isn't available to hired employees.")


def test_an_app_must_be_connected_when_the_caller_says_what_is():
    refused = check_tool("googleSheets", employee=ASKING_FIRST, connected={"gmail"}, allowed=EVERYTHING)
    assert (refused.allowed, refused.code) == (False, "not_connected")
    assert refused.reason == "Google Sheets isn't connected yet. Connect it in Settings > Connectors first."
    assert check_tool("googleSheets", employee=ASKING_FIRST, connected=None, allowed=EVERYTHING).allowed


@pytest.mark.parametrize("node_type", ["whatsappSend", "httpRequest", "noSuchNode"])
def test_only_registry_tools_and_the_base_tools_can_be_given(node_type):
    decision = check_tool(node_type, employee=NOT_ASKING_FIRST, connected=None, allowed=EVERYTHING)
    assert (decision.allowed, decision.code) == (False, "not_a_tool")
    assert decision.reason.endswith("isn't something a hired employee can be given.")


def test_the_hire_allowlist_is_the_default(monkeypatch):
    from services import node_allowlist

    assert check_tool("googleSheets", employee=ASKING_FIRST, connected=None).allowed
    monkeypatch.setattr(node_allowlist, "is_hire_allowed", lambda _node_type: False)
    assert check_tool("googleSheets", employee=ASKING_FIRST, connected=None).code == "not_allowed"


@pytest.mark.parametrize(
    "name, code",
    [("", "invalid"), ("skill", "reserved"), ("pirate-personality", "personality")],
)
def test_skills_no_hired_employee_can_have(name, code):
    decision = check_skill(name)
    assert (decision.allowed, decision.code) == (False, code)
    assert decision.reason


def test_a_library_skill_is_allowed():
    assert check_skill("book-appointments").allowed
