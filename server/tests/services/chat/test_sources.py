"""Sources an answer can cite (services/chat/sources.py): the web results of
a search the answering agent ran are numbered for the conversation (never
repeating, across calls and turns), each result the model reads carries its
number, and the reply carries the sources it may cite."""

from __future__ import annotations

from services.chat import ledger, parts, sources

CLAIM = dict(temporal_workflow_id="tw-1", temporal_run_id="tr-1")


async def running(database):
    admission = await ledger.admit_message(database, session_id="wf", workflow_id="wf", execution_id="g", text="Find a florist", track=True)
    return await ledger.start_run(database, run_id=admission.run.run_id, **CLAIM)


def stream_of(run):
    return {"run_id": run.run_id, "session_id": run.session_id, "workflow_id": run.workflow_id}


def results(*names):
    return {"results": [{"title": f"{name} Flowers", "snippet": f"{name} delivers.", "url": f"https://{name}.test/"} for name in names]}


async def test_results_are_numbered_for_the_conversation(database, hub):
    run = await running(database)
    first = results("bloom", "petal")
    saved = await sources.number_tool_sources(database, stream_of(run), tool_call_id="call_1", payload=first)
    assert [item["n"] for item in first["results"]] == [1, 2]
    assert saved == [
        {"n": 1, "title": "bloom Flowers", "url": "https://bloom.test/", "detail": "bloom delivers."},
        {"n": 2, "title": "petal Flowers", "url": "https://petal.test/", "detail": "petal delivers."},
    ]
    second = results("stem")
    await sources.number_tool_sources(database, stream_of(run), tool_call_id="call_2", payload=second)
    assert second["results"][0]["n"] == 3

    reply = await ledger.post_reply(database, run=run, node_id="n", text="Try Bloom [1] or Stem [3].", execution_id="g")
    assert [item["n"] for item in reply["parts"]["sources"]] == [1, 2, 3]


async def test_results_without_an_address_are_not_sources(database, hub):
    run = await running(database)
    payload = {"results": [{"title": "No link"}, {"title": "Bad", "url": "javascript:alert(1)"}, {"url": "https://ok.test"}]}
    saved = await sources.number_tool_sources(database, stream_of(run), tool_call_id="call_1", payload=payload)
    assert saved == [{"n": 1, "title": "ok.test", "url": "https://ok.test"}]
    assert "n" not in payload["results"][0] and payload["results"][2]["n"] == 1
    assert await sources.number_tool_sources(database, stream_of(run), tool_call_id="call_2", payload={"answer": "x"}) == []


async def test_sources_and_ui_seal_side_by_side(database, hub):
    run = await running(database)
    await sources.number_tool_sources(database, stream_of(run), tool_call_id="call_1", payload=results("bloom"))
    grouped = parts.grouped_parts(await parts.run_parts(database, run.run_id))
    assert grouped == {"sources": [{"n": 1, "title": "bloom Flowers", "url": "https://bloom.test/", "detail": "bloom delivers."}]}
