"""An employee an older builder made, brought up to the live Ask first rule
(services/employees/upgrade.py): its ungated reply gets a gate (the edge
from the agent goes to the gate, the reply reads the gate), the tools asking
first left out come in for the worker and its talk agent, a read-only
browser is saved whole, and the talk agent gets its talk tools. A graph
already up to date gets nothing."""

from __future__ import annotations

from types import SimpleNamespace

import nodes  # noqa: F401 - the plugin registry, for the policy's lookups
from services.approvals.contract import approved_edge_condition, send_condition
from services.employees.apps import get_app
from services.employees.upgrade import plan_upgrade
from services.graph_build import add_to_graph

EVERYTHING = lambda _node_type: True  # noqa: E731
EMPLOYEE = SimpleNamespace(rules={"ask_first": False, "items": []})


def _node(node_id, node_type, label, x=0, y=0):
    return {"id": node_id, "type": node_type, "position": {"x": x, "y": y}, "data": {"label": label}}


def _edge(source, target, handle="input-main", source_handle="output-main", condition=None):
    edge = {"id": f"e-{source}-{source_handle}-{target}-{handle}", "source": source, "sourceHandle": source_handle, "target": target, "targetHandle": handle}
    if condition:
        edge["data"] = {"condition": condition}
    return edge


GRAPH = {
    "nodes": [
        _node("7:whatsappReceive:1", "whatsappReceive", "WhatsApp"),
        _node("7:aiAgent:1", "aiAgent", "Maya", 360),
        _node("7:whatsappSend:1", "whatsappSend", "Reply on WhatsApp", 720),
        _node("7:browser:1", "browser", "Browser", 120, 440),
        _node("7:chatTrigger:1", "chatTrigger", "Talk", 0, 700),
        _node("7:aiAgent:2", "aiAgent", "Talk with Maya", 360, 700),
        _node("7:chatReply:1", "chatReply", "Reply in Chat", 720, 700),
    ],
    "edges": [
        _edge("7:whatsappReceive:1", "7:aiAgent:1"),
        _edge("7:aiAgent:1", "7:whatsappSend:1", condition=send_condition()),
        _edge("7:whatsappReceive:1", "7:whatsappSend:1"),
        _edge("7:browser:1", "7:aiAgent:1", "input-tools", "output-tool"),
        _edge("7:browser:1", "7:aiAgent:2", "input-tools", "output-tool"),
        _edge("7:chatTrigger:1", "7:aiAgent:2"),
        _edge("7:aiAgent:2", "7:chatReply:1", condition=send_condition()),
    ],
}
ROLES = {
    "trigger": "7:whatsappReceive:1",
    "agent": "7:aiAgent:1",
    "reply": "7:whatsappSend:1",
    "browser": "7:browser:1",
    "talk_trigger": "7:chatTrigger:1",
    "talk_agent": "7:aiAgent:2",
    "talk_reply": "7:chatReply:1",
}
PARAMS = {
    "7:whatsappSend:1": {"recipient_type": "phone", "phone": "{{whatsapp.sender_phone}}", "message_type": "text", "message": "{{maya.response}}"},
    "7:browser:1": {"interaction": "read_only"},
}


def _plan(graph=GRAPH, roles=ROLES, params=PARAMS):
    return plan_upgrade(
        graph,
        employee=EMPLOYEE,
        roles=roles,
        apps=[get_app("whatsapp"), get_app("web"), get_app("google_calendar")],
        params=params,
        owner_values={},
        allowed=EVERYTHING,
    )


def test_an_ungated_reply_gets_a_gate_and_the_rest_comes_in():
    upgrade = _plan()
    placed = add_to_graph("7", GRAPH, upgrade.additions)
    by_ref = {ref: node_id for ref, node_id in placed.node_ids.items()}
    gate = by_ref["gate"]
    assert placed.parameters[gate] == {
        "channel": "WhatsApp",
        "recipient": "{{whatsapp.sender_phone}}",
        "recipient_label": "{{whatsapp.push_name}}",
        "draft": "{{maya.response}}",
        "context_excerpt": "{{whatsapp.text}}",
        "max_length": 4096,
    }
    # The agent's edge to the reply now goes to the gate.
    assert [edge["id"] for edge in placed.removed_edges] == ["e-7:aiAgent:1-output-main-7:whatsappSend:1-input-main"]
    wiring = {(edge["source"], edge["target"]): (edge.get("data") or {}).get("condition") for edge in placed.edges}
    assert wiring[("7:aiAgent:1", gate)] == send_condition()
    assert ("7:whatsappReceive:1", gate) in wiring
    assert wiring[(gate, "7:whatsappSend:1")] == approved_edge_condition()
    assert placed.merges["7:whatsappSend:1"] == {"phone": "{{checkbeforesending.recipient}}", "message": "{{checkbeforesending.text}}"}
    assert upgrade.roles["gate"] == "gate"

    # The calendar tool asking first left out, for the worker and its talk agent.
    calendar = next(node_id for node_id, params in placed.parameters.items() if params.get("calendar_id") == "primary")
    assert {target for source, target in wiring if source == calendar} == {"7:aiAgent:1", "7:aiAgent:2"}
    # The browser is saved whole.
    assert placed.merges["7:browser:1"] == {"interaction": "full"}
    # The talk agent alone gets generated UI and sending on WhatsApp.
    talk_only = {node["type"] for node in placed.nodes if {t for s, t in wiring if s == node["id"]} == {"7:aiAgent:2"}}
    assert talk_only == {"chatUi", "whatsappSend"}


def test_an_up_to_date_graph_gets_nothing_more():
    upgrade = _plan()
    placed = add_to_graph("7", GRAPH, upgrade.additions)
    roles = {**ROLES, "gate": placed.node_ids["gate"]}
    params = {**PARAMS, "7:browser:1": {"interaction": "full"}}
    again = _plan(placed.graph, roles, params)
    assert not again.additions, again.additions
