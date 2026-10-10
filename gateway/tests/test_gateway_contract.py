"""Contract tests for the OpenCompany Gateway (LiteLLM Proxy).

They run against the local stack in gateway/local (mock models, no real keys)
and pin the LiteLLM behaviours the OpenCompany integration relies on: budget
refusals, key scope, attribution, streamed pricing, retry replay, and the
native Anthropic and Gemini routes.

    docker compose -f gateway/local/docker-compose.yml up -d --wait
    uv run --project gateway pytest gateway/tests -v

Set GATEWAY_URL and GATEWAY_MASTER_KEY to point the suite at another stack.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
import pytest

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
MASTER_KEY = os.environ.get("GATEWAY_MASTER_KEY", "sk-local-master-key")
CHAT_MODEL = "openai/gpt-4o-mini"
# A dotted alias that is not a price-list key, streaming from a fake Anthropic
# upstream (gateway/local/config.local.yaml, gateway/local/fake_anthropic.py).
ALIAS_MODEL = "claude-opus-4.8-20261010"
# 1.103.x always answers a spent allowance with 429; budget_exceeded_status_code
# makes it 402 from 1.104. OpenCompany reads the error type either way.
BUDGET_STATUSES = {402, 429}
PING = [{"role": "user", "content": "ping"}]


def _gateway_up() -> bool:
    try:
        return httpx.get(f"{GATEWAY_URL}/health/liveliness", timeout=3).status_code == 200
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _gateway_up(), reason=f"gateway not reachable at {GATEWAY_URL}")


@dataclass(frozen=True)
class Person:
    user_id: str
    key: str


def _auth(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _chat(key: str, *, headers: dict[str, str] | None = None, body: dict | None = None) -> httpx.Response:
    payload = {"model": CHAT_MODEL, "max_tokens": 16, "messages": PING}
    payload.update(body or {})
    return httpx.post(
        f"{GATEWAY_URL}/v1/chat/completions",
        headers={**_auth(key), **(headers or {})},
        json=payload,
        timeout=30,
    )


def _messages(key: str, *, headers: dict[str, str] | None = None, body: dict | None = None) -> httpx.Response:
    payload = {"model": "claude-haiku-4-5", "max_tokens": 16, "messages": PING}
    payload.update(body or {})
    return httpx.post(
        f"{GATEWAY_URL}/v1/messages",
        headers={"x-api-key": key, "anthropic-version": "2023-06-01", **(headers or {})},
        json=payload,
        timeout=30,
    )


def _generate_content(key: str, *, body: dict | None = None) -> httpx.Response:
    payload = {"contents": [{"role": "user", "parts": [{"text": "ping"}]}]}
    payload.update(body or {})
    return httpx.post(
        f"{GATEWAY_URL}/v1beta/models/gemini-2.5-flash:generateContent",
        headers={"x-goog-api-key": key},
        json=payload,
        timeout=30,
    )


def _error_type(resp: httpx.Response) -> str | None:
    """The error type in OpenAI-shaped ({"error": {...}}) and Anthropic-shaped bodies."""
    try:
        body = resp.json()
    except ValueError:
        return None
    error = body.get("error", body) if isinstance(body, dict) else None
    return error.get("type") if isinstance(error, dict) else None


def _spend_rows(admin: httpx.Client, **filters: str) -> list[dict]:
    """Poll /spend/logs/v2 until rows matching the filters appear (spend is batch-written)."""
    today = date.today()
    params = {
        "start_date": (today - timedelta(days=1)).isoformat(),
        "end_date": (today + timedelta(days=1)).isoformat(),
        **filters,
    }
    for _ in range(20):
        resp = admin.get("/spend/logs/v2", params=params)
        resp.raise_for_status()
        body = resp.json()
        rows = body.get("data", []) if isinstance(body, dict) else body
        if rows:
            return rows
        time.sleep(1)
    return []


@pytest.fixture(scope="module")
def admin() -> Iterator[httpx.Client]:
    with httpx.Client(base_url=GATEWAY_URL, headers=_auth(MASTER_KEY), timeout=30) as client:
        yield client


@pytest.fixture
def person(admin: httpx.Client) -> Iterator[Callable[..., Person]]:
    """Create a fresh person (internal user) with a personal key; delete them afterwards.

    Keys are Default-type unless key_type says otherwise: that is what the
    console creates, and OpenCompany needs it to read the allowance.
    """
    created: list[str] = []

    def make(max_budget: float = 10.0, key_type: str | None = None) -> Person:
        user_id = f"contract-{uuid.uuid4().hex[:12]}"
        admin.post(
            "/user/new",
            json={"user_id": user_id, "user_role": "internal_user", "max_budget": max_budget, "budget_duration": "1mo"},
        ).raise_for_status()
        created.append(user_id)
        request = {"user_id": user_id, **({"key_type": key_type} if key_type else {})}
        resp = admin.post("/key/generate", json=request)
        resp.raise_for_status()
        return Person(user_id=user_id, key=resp.json()["key"])

    yield make
    if created:
        admin.post("/user/delete", json={"user_ids": created})


def test_unpriced_model_is_refused(person: Callable[..., Person]) -> None:
    """block_requests_for_models_without_pricing: an unpriced model never bills $0."""
    resp = _chat(person().key, body={"model": "openai/oc-contract-unpriced-model"})
    assert resp.status_code == 403, resp.text
    assert _error_type(resp) == "model_cost_map_missing", resp.text


def test_budget_refusal_is_typed_on_every_route(person: Callable[..., Person]) -> None:
    """OpenCompany recognises a spent allowance by its error type, on each route it uses."""
    key = person(max_budget=0.0000001).key
    calls: dict[str, Callable[[], httpx.Response]] = {
        "chat/completions": lambda: _chat(key, body={"max_tokens": 1000}),
        "messages": lambda: _messages(key, body={"max_tokens": 1000}),
        "generateContent": lambda: _generate_content(key, body={"generationConfig": {"maxOutputTokens": 1000}}),
    }
    for route, call in calls.items():
        resp = call()
        for _ in range(3):
            if resp.status_code != 200:
                break
            time.sleep(2)
            resp = call()
        assert resp.status_code in BUDGET_STATUSES, (route, resp.status_code, resp.text)
        assert _error_type(resp) == "budget_exceeded", (route, resp.text)


def test_user_info_needs_a_default_key_and_stays_scoped(person: Callable[..., Person]) -> None:
    """OpenCompany shows the allowance from /user/info: a Default key reads only its own."""
    me, other = person(), person()
    own = httpx.get(f"{GATEWAY_URL}/user/info", headers=_auth(me.key), timeout=30)
    assert own.status_code == 200, own.text
    assert own.json().get("user_id") == me.user_id, own.text
    info = own.json().get("user_info") or {}
    assert {"spend", "max_budget", "budget_reset_at"} <= set(info), own.text

    theirs = httpx.get(f"{GATEWAY_URL}/user/info", params={"user_id": other.user_id}, headers=_auth(me.key), timeout=30)
    assert theirs.status_code in (401, 403), theirs.text

    llm_only = person(key_type="llm_api")
    refused = httpx.get(f"{GATEWAY_URL}/user/info", headers=_auth(llm_only.key), timeout=30)
    assert refused.status_code in (401, 403), refused.text


def test_attribution_headers_reach_spend_logs(person: Callable[..., Person], admin: httpx.Client) -> None:
    """x-litellm-session-id / -tags / -spend-logs-metadata land on the spend row."""
    session = f"r_{uuid.uuid4().hex}"
    resp = _chat(
        person().key,
        headers={
            "x-litellm-session-id": session,
            "x-litellm-tags": "workflow:wf-contract,source:chat,user:owner",
            "x-litellm-spend-logs-metadata": json.dumps({"root_execution_id": "exec-1", "node_id": "agent-1"}),
        },
    )
    assert resp.status_code == 200, resp.text
    rows = _spend_rows(admin, session_id=session)
    assert rows, "no spend row for the session id"
    row = rows[0]
    assert {"workflow:wf-contract", "source:chat", "user:owner"} <= set(row.get("request_tags") or []), row
    stored = (row.get("metadata") or {}).get("spend_logs_metadata") or {}
    assert stored.get("root_execution_id") == "exec-1", row


def test_streamed_alias_is_priced_at_the_deployment(person: Callable[..., Person], admin: httpx.Client) -> None:
    """A real upstream stream through a dotted alias is billed at the deployment's price.

    The shape of LiteLLM PR #44341 ($0 for streamed aliases). With this config
    and the pinned price list, 1.103.2 (without the fix) already passes, so this
    guards streamed pricing on upgrades rather than proving the fix is present.
    """
    session = f"alias-{uuid.uuid4().hex}"
    resp = _chat(
        person().key,
        headers={"x-litellm-session-id": session},
        body={"model": ALIAS_MODEL, "stream": True, "stream_options": {"include_usage": True}},
    )
    assert resp.status_code == 200, resp.text
    rows = _spend_rows(admin, session_id=session)
    assert rows, "no spend row for the streamed call"
    assert float(rows[0].get("spend") or 0) > 0, rows[0]


def test_cache_namespace_replays_retry_for_free(person: Callable[..., Person], admin: httpx.Client) -> None:
    """Opt-in cache with a per-request namespace: the replayed retry costs $0."""
    key = person().key
    namespace = f"oc1-{uuid.uuid4().hex}"
    cache = {"cache": {"use-cache": True, "namespace": namespace, "ttl": 600}}
    first = _chat(key, headers={"x-litellm-session-id": f"{namespace}-first"}, body=cache)
    second = _chat(key, headers={"x-litellm-session-id": f"{namespace}-retry"}, body=cache)
    assert first.status_code == second.status_code == 200, (first.text, second.text)
    rows = _spend_rows(admin, session_id=f"{namespace}-retry")
    assert rows, "no spend row for the retry"
    assert str(rows[0].get("cache_hit")).lower() == "true", rows[0]
    assert float(rows[0].get("spend") or 0) == 0.0, rows[0]


def test_streamed_retry_replays_for_free(person: Callable[..., Person], admin: httpx.Client) -> None:
    """The same replay for streamed calls, which chat runs use."""
    key = person().key
    namespace = f"oc1-{uuid.uuid4().hex}"
    body = {
        "stream": True,
        "stream_options": {"include_usage": True},
        "cache": {"use-cache": True, "namespace": namespace, "ttl": 600},
    }
    first = _chat(key, headers={"x-litellm-session-id": f"{namespace}-first"}, body=body)
    second = _chat(key, headers={"x-litellm-session-id": f"{namespace}-retry"}, body=body)
    assert first.status_code == second.status_code == 200, (first.text, second.text)
    rows = _spend_rows(admin, session_id=f"{namespace}-retry")
    assert rows, "no spend row for the streamed retry"
    assert str(rows[0].get("cache_hit")).lower() == "true", rows[0]
    assert float(rows[0].get("spend") or 0) == 0.0, rows[0]


def test_anthropic_messages_route_accepts_bare_model_ids(person: Callable[..., Person]) -> None:
    """The claude-* wildcard serves the Anthropic SDK and Claude Code on /v1/messages."""
    resp = _messages(person().key)
    assert resp.status_code == 200, resp.text
    assert resp.json().get("type") == "message", resp.text


def test_anthropic_messages_cache_namespace(person: Callable[..., Person], admin: httpx.Client) -> None:
    """The same opt-in cache controls work on /v1/messages."""
    key = person().key
    namespace = f"oc1-{uuid.uuid4().hex}"
    body = {"cache": {"use-cache": True, "namespace": namespace, "ttl": 600}}
    for suffix in ("first", "retry"):
        resp = _messages(key, headers={"x-litellm-session-id": f"{namespace}-{suffix}"}, body=body)
        assert resp.status_code == 200, resp.text
    rows = _spend_rows(admin, session_id=f"{namespace}-retry")
    assert rows, "no spend row for the retry"
    assert str(rows[0].get("cache_hit")).lower() == "true", rows[0]


def test_gemini_native_route_accepts_virtual_key(person: Callable[..., Person]) -> None:
    """The gemini-* wildcard serves the Gemini SDK, which sends its key as x-goog-api-key."""
    resp = _generate_content(person().key)
    assert resp.status_code == 200, resp.text
