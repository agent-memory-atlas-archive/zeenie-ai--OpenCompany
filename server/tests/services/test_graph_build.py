"""services/graph_build.py: labels, canonical ids, edges and the placement
of a batch of additions in an existing graph."""

from __future__ import annotations

from copy import deepcopy

import pytest

from services.graph_build import (
    GraphAdditions,
    Labels,
    NewNode,
    NodeIds,
    ParamMerge,
    add_to_graph,
    context_edge,
    label_key,
    main_edge,
    merge_params,
    ref,
    skill_edge,
    template_key,
    tool_edge,
)
from services.workflow_naming import canonicalize_node_ids


def _node(node_id: str, node_type: str, label: str | None = None) -> dict:
    return {"id": node_id, "type": node_type, "position": {"x": 0, "y": 0}, "data": {"label": label} if label else {}}


def _graph() -> dict:
    """A saved employee: a Chat trigger and an agent with two tools."""
    return {
        "graphVersion": 2,
        "owner_id": "owner",
        "nodes": [
            _node("7:chatTrigger:1", "chatTrigger", "Chat"),
            _node("7:aiAgent:1", "aiAgent", "Maya"),
            _node("7:duckduckgoSearch:1", "duckduckgoSearch", "Web search"),
            _node("7:canvas:1", "canvas", "Canvas"),
        ],
        "edges": [
            main_edge("7:chatTrigger:1", "7:aiAgent:1").to_dict(),
            tool_edge("7:duckduckgoSearch:1", "7:aiAgent:1").to_dict(),
            tool_edge("7:canvas:1", "7:aiAgent:1").to_dict(),
        ],
    }


def test_labels_are_numbered_past_the_ones_already_taken():
    labels = Labels(["Chat", label_key("Activity log")])
    assert labels.take("Chat") == "Chat 2"
    assert labels.take("chat") == "chat 3"
    assert labels.take("Activity Log") == "Activity Log 2"
    assert labels.take("Talk", clashes=lambda candidate: candidate == "Talk") == "Talk 2"


def test_template_key_falls_back_to_the_type():
    assert template_key(_node("1", "aiAgent", "Maya Lee")) == "mayalee"
    assert template_key(_node("1", "aiAgent")) == "aiagent"


def test_node_ids_count_past_what_canonicalizing_the_graph_gives():
    # An id an older writer minted canonicalizes to the next free ordinal,
    # so new ids must count past it too.
    nodes = [_node("7:googleCalendar:1", "googleCalendar"), _node("googleCalendar-1727-abc", "googleCalendar")]
    ids = NodeIds("7", nodes)
    new_id = ids.next("googleCalendar")
    assert new_id == "7:googleCalendar:3"
    assert ids.next("canvas") == "7:canvas:1"
    canonical, _edges, aliases = canonicalize_node_ids("7", [*nodes, _node(new_id, "googleCalendar")], [])
    assert new_id not in aliases and len({node["id"] for node in canonical}) == 3


def test_edges_use_each_connection_kinds_handles():
    assert tool_edge("t", "a").to_dict() == {
        "id": "e-t-output-tool-a-input-tools",
        "source": "t",
        "sourceHandle": "output-tool",
        "target": "a",
        "targetHandle": "input-tools",
    }
    assert (skill_edge("s", "a").source_handle, skill_edge("s", "a").target_handle) == ("output-tool", "input-skill")
    assert (context_edge("c", "a").source_handle, context_edge("c", "a").target_handle) == ("output-context", "input-context")
    conditional = main_edge("a", "r", {"field": "result.response", "operator": "neq", "value": "NO_REPLY"}).to_dict()
    assert conditional["data"] == {"condition": {"field": "result.response", "operator": "neq", "value": "NO_REPLY"}}
    assert "data" not in main_edge("a", "r").to_dict()


def test_merge_params_merges_mappings_at_any_depth():
    current = {"skill_folder": "assistant", "skills_config": {"skill": {"enabled": True}, "a": {"enabled": True}}}
    patch = {"skills_config": {"b": {"enabled": True, "instructions": "B"}, "a": {"enabled": False}}, "skill_folder": "other"}
    before = deepcopy(current)
    merged = merge_params(current, patch)
    assert merged == {
        "skill_folder": "other",
        "skills_config": {"skill": {"enabled": True}, "a": {"enabled": False}, "b": {"enabled": True, "instructions": "B"}},
    }
    assert current == before


def test_a_talk_line_is_placed_against_the_graph():
    graph = _graph()
    before = deepcopy(graph)
    placed = add_to_graph(
        "7",
        graph,
        GraphAdditions(
            nodes=(
                NewNode("talk", "chatTrigger", "Talk", {"session_id": "7"}, position=(0, 600)),
                NewNode("agent", "aiAgent", "Talk with Maya", {"prompt": ref("talk", "message")}, position=(360, 600)),
                NewNode("context", "context", "Context", position=(360, 420), context_of="agent"),
                NewNode("reply", "chatReply", "Reply in Chat", {"message": ref(label_key("Talk with Maya"), "response")}, (720, 600)),
            ),
            edges=(
                main_edge("talk", "agent"),
                main_edge("agent", "reply", {"field": "result.response", "operator": "neq", "value": "NO_REPLY"}),
                tool_edge("7:duckduckgoSearch:1", "agent"),
                tool_edge("7:canvas:1", "agent"),
            ),
        ),
    )
    assert graph == before
    assert placed.node_ids == {"talk": "7:chatTrigger:2", "agent": "7:aiAgent:2", "context": "7:context:1", "reply": "7:chatReply:1"}
    assert placed.labels == {"talk": "Talk", "agent": "Talk with Maya", "context": "Context", "reply": "Reply in Chat"}
    context = next(node for node in placed.nodes if node["id"] == "7:context:1")
    assert context["data"] == {"label": "Context", "systemManaged": True, "agentNodeId": "7:aiAgent:2"}
    assert context["position"] == {"x": 360, "y": 420}
    assert [edge["id"] for edge in placed.edges] == [
        "e-7:context:1-output-context-7:aiAgent:2-input-context",
        "e-7:chatTrigger:2-output-main-7:aiAgent:2-input-main",
        "e-7:aiAgent:2-output-main-7:chatReply:1-input-main",
        "e-7:duckduckgoSearch:1-output-tool-7:aiAgent:2-input-tools",
        "e-7:canvas:1-output-tool-7:aiAgent:2-input-tools",
    ]
    assert placed.parameters["7:chatReply:1"] == {"message": "{{talkwithmaya.response}}"}
    assert placed.graph["nodes"][: len(before["nodes"])] == before["nodes"]
    assert len(placed.graph["nodes"]) == 8 and len(placed.graph["edges"]) == 8
    assert placed.graph["owner_id"] == "owner"


def test_a_label_taken_in_the_meantime_gets_a_number_and_the_templates_follow():
    graph = _graph()
    graph["nodes"].append(_node("7:console:1", "console", "Talk with Maya"))
    placed = add_to_graph(
        "7",
        graph,
        GraphAdditions(
            nodes=(
                NewNode("agent", "aiAgent", "Talk with Maya"),
                NewNode("reply", "chatReply", "Reply in Chat", {"message": "Said: {{talkwithmaya.response}} to {{maya.response}}"}),
            ),
            merges=(ParamMerge("7:aiAgent:1", {"prompt": "{{TalkWithMaya.response}}"}),),
        ),
    )
    assert placed.labels["agent"] == "Talk with Maya 2"
    assert placed.parameters["7:chatReply:1"] == {"message": "Said: {{talkwithmaya2.response}} to {{maya.response}}"}
    assert placed.merges == {"7:aiAgent:1": {"prompt": "{{talkwithmaya2.response}}"}}


def test_templates_stay_put_when_two_new_nodes_wanted_one_label():
    placed = add_to_graph(
        "7",
        _graph(),
        GraphAdditions(
            nodes=(
                NewNode("a", "chatReply", "Reply", {"message": "{{reply.x}}"}),
                NewNode("b", "chatReply", "Reply", {"message": "{{reply.x}}"}),
            )
        ),
    )
    assert placed.labels == {"a": "Reply", "b": "Reply 2"}
    assert placed.parameters["7:chatReply:2"] == {"message": "{{reply.x}}"}


def test_a_new_trigger_does_not_share_a_label_slug_with_another_trigger():
    graph = _graph()
    graph["nodes"][0]["data"]["label"] = "Talk!"
    placed = add_to_graph("7", graph, GraphAdditions(nodes=(NewNode("talk", "chatTrigger", "Talk"),)))
    # "talk" is a free template key, but "Talk!" and "Talk" slug alike, and
    # a deployment registers one listener per trigger label slug.
    assert placed.labels["talk"] == "Talk 2"
    other = add_to_graph("7", graph, GraphAdditions(nodes=(NewNode("tool", "canvas", "Talk"),)))
    assert other.labels["tool"] == "Talk"


def test_an_edge_the_graph_already_has_is_not_added_again():
    placed = add_to_graph(
        "7",
        _graph(),
        GraphAdditions(edges=(tool_edge("7:canvas:1", "7:aiAgent:1"), tool_edge("7:canvas:1", "7:aiAgent:1"), main_edge("7:aiAgent:1", "7:canvas:1"))),
    )
    assert [edge["id"] for edge in placed.edges] == ["e-7:aiAgent:1-output-main-7:canvas:1-input-main"]


@pytest.mark.parametrize(
    "additions, message",
    [
        (GraphAdditions(edges=(tool_edge("missing", "7:aiAgent:1"),)), "neither a ref"),
        (GraphAdditions(nodes=(NewNode("a", "canvas", "A"), NewNode("a", "canvas", "B"))), "already used"),
        (GraphAdditions(nodes=(NewNode("7:canvas:1", "canvas", "A"),)), "already used"),
        (GraphAdditions(nodes=(NewNode("a", "canvas", "  "),)), "needs a ref"),
        (GraphAdditions(nodes=(NewNode("a", "canvas", "A", context_of="7:aiAgent:1"),)), "Context companion"),
        (GraphAdditions(merges=(ParamMerge("nope", {"x": 1}),)), "neither a ref"),
    ],
)
def test_a_batch_that_does_not_fit_is_refused(additions, message):
    with pytest.raises(ValueError, match=message):
        add_to_graph("7", _graph(), additions)


def test_an_agent_keeps_one_context():
    graph = _graph()
    graph["nodes"].append(_node("7:context:1", "context", "Context"))
    graph["edges"].append(context_edge("7:context:1", "7:aiAgent:1").to_dict())
    with pytest.raises(ValueError, match="already has a Context"):
        add_to_graph("7", graph, GraphAdditions(nodes=(NewNode("c", "context", "Context", context_of="7:aiAgent:1"),)))
    with pytest.raises(ValueError, match="already has a Context"):
        add_to_graph(
            "7",
            _graph(),
            GraphAdditions(
                nodes=(
                    NewNode("c1", "context", "Context", context_of="7:aiAgent:1"),
                    NewNode("c2", "context", "Context", context_of="7:aiAgent:1"),
                )
            ),
        )
