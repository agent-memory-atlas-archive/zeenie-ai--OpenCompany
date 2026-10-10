"""Contract tests for the OpenCompany Gateway (LiteLLM Proxy).

They run against the local stack in gateway/local (mock models, no real keys)
and pin the LiteLLM behaviours the OpenCompany integration relies on: budget
refusals, key scope, attribution, streamed pricing, retry replay, the native
Anthropic and Gemini routes, and the embedding and speech routes.

    docker compose -f gateway/local/docker-compose.yml up -d --wait
    uv run --project gateway pytest gateway/tests -v

Set GATEWAY_URL and GATEWAY_MASTER_KEY to point the suite at another stack.
"""

from __future__ import annotations

import io
import json
import os
import time
import uuid
import wave
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, timedelta

import httpx
import pytest

GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://127.0.0.1:4000")
MASTER_KEY = os.environ.get("GATEWAY_MASTER_KEY", "sk-local-master-key")
CHAT_MODEL = "openai/gpt-4o-mini"
# Models answered by gateway/local/fake_upstream.py (gateway/local/config.local.yaml).
# Mocks reuse one response id on /v1/messages and carry no HTTP response on the
# native Gemini route, and LiteLLM writes no spend row for either, so every
# spend-row check on those routes uses these. ALIAS_MODEL is also a dotted
# alias that is not a price-list key.
ALIAS_MODEL = "claude-opus-4.8-20261010"
GEMINI_LIVE_MODEL = "gemini-2.5-flash-live"
# OpenCompany's default speech models under their wire names, answered by
# fake_upstream.py too: mocks don't apply on the audio routes.
TTS_MODEL = "openai/gpt-4o-mini-tts"
STT_MODEL = "openai/whisper-1"
GROQ_STT_MODEL = "groq/whisper-large-v3"
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


def _attribution(session: str) -> dict[str, str]:
    """The attribution headers OpenCompany sends (services/gateway/attribution.py)."""
    return {
        "x-litellm-session-id": session,
        "x-litellm-tags": "source:chat",
        "x-litellm-spend-logs-metadata": json.dumps({"chat_run_id": session}),
    }


def _attributed_row(admin: httpx.Client, session: str) -> dict:
    """The session's spend row, priced and carrying all three attribution headers."""
    rows = _spend_rows(admin, session_id=session)
    assert rows, f"no spend row for session {session}"
    row = rows[0]
    assert float(row.get("spend") or 0) > 0, row
    assert "source:chat" in (row.get("request_tags") or []), row
    assert ((row.get("metadata") or {}).get("spend_logs_metadata") or {}).get("chat_run_id") == session, row
    return row


def _wav(seconds: float = 1.0, rate: int = 16000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


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


def test_messages_route_writes_a_priced_row_for_the_session(person: Callable[..., Person], admin: httpx.Client) -> None:
    """The Anthropic SDK's own route bills the person and carries attribution, streamed or not."""
    key = person().key
    for stream in (False, True):
        session = f"r_{uuid.uuid4().hex}"
        resp = _messages(
            key,
            headers={"x-litellm-session-id": session, "x-litellm-tags": "source:chat"},
            body={"model": ALIAS_MODEL, "temperature": 1, **({"stream": True} if stream else {})},
        )
        assert resp.status_code == 200, resp.text
        rows = _spend_rows(admin, session_id=session)
        assert rows, f"no spend row for the {'streamed' if stream else 'plain'} /v1/messages call"
        assert float(rows[0].get("spend") or 0) > 0, rows[0]
        assert "source:chat" in (rows[0].get("request_tags") or []), rows[0]


def test_gemini_native_route_writes_a_priced_row_for_the_session(person: Callable[..., Person], admin: httpx.Client) -> None:
    """The Gemini SDK's native route bills the person and keeps the session id and tags.

    It is the only route where 1.103.4 drops x-litellm-spend-logs-metadata (router metadata
    goes to litellm_metadata there and wins) and ignores the opt-in cache body.
    """
    session = f"r_{uuid.uuid4().hex}"
    resp = httpx.post(
        f"{GATEWAY_URL}/v1beta/models/{GEMINI_LIVE_MODEL}:generateContent",
        headers={"x-goog-api-key": person().key, "x-litellm-session-id": session, "x-litellm-tags": "source:chat"},
        json={"contents": [{"role": "user", "parts": [{"text": "ping"}]}]},
        timeout=30,
    )
    assert resp.status_code == 200, resp.text
    rows = _spend_rows(admin, session_id=session)
    assert rows, "no spend row for the native Gemini call"
    assert float(rows[0].get("spend") or 0) > 0, rows[0]
    assert "source:chat" in (rows[0].get("request_tags") or []), rows[0]


def test_embeddings_route_writes_an_attributed_row(person: Callable[..., Person], admin: httpx.Client) -> None:
    """OpenAI embeddings (long-term memory, the Embedding Generator) bill the person with attribution."""
    session = f"r_{uuid.uuid4().hex}"
    resp = httpx.post(
        f"{GATEWAY_URL}/v1/embeddings",
        headers={**_auth(person().key), **_attribution(session)},
        json={"model": "openai/text-embedding-3-small", "input": ["ping"]},
        timeout=30,
    )
    assert resp.status_code == 200, resp.text
    _attributed_row(admin, session)


def test_speech_routes_write_attributed_rows(person: Callable[..., Person], admin: httpx.Client) -> None:
    """Text to Speech and Speech to Text bill the person with attribution.

    Transcription is billed per second of audio, measured from the file.
    gpt-4o-mini-tts is billed per input token at the config's override: the
    speech route returns no usage for the audio, which the price list prices.
    """
    key = person().key
    speech_session, transcription_session = f"r_{uuid.uuid4().hex}", f"r_{uuid.uuid4().hex}"
    speech = httpx.post(
        f"{GATEWAY_URL}/v1/audio/speech",
        headers={**_auth(key), **_attribution(speech_session)},
        json={"model": TTS_MODEL, "input": "ping", "voice": "alloy"},
        timeout=30,
    )
    assert speech.status_code == 200 and speech.content, speech.text
    transcription = httpx.post(
        f"{GATEWAY_URL}/v1/audio/transcriptions",
        headers={**_auth(key), **_attribution(transcription_session)},
        data={"model": STT_MODEL},
        files={"file": ("ping.wav", _wav(), "audio/wav")},
        timeout=30,
    )
    assert transcription.status_code == 200, transcription.text
    assert transcription.json().get("text") == "pong", transcription.text
    speech_row = _attributed_row(admin, speech_session)
    # At least one input token at the override's $0.000075, not the price
    # list's $0.0000006.
    assert float(speech_row["spend"]) >= 0.000075, speech_row
    _attributed_row(admin, transcription_session)


def test_the_gateway_has_no_audio_translation_route(person: Callable[..., Person]) -> None:
    """Why OpenCompany never asks the gateway to translate audio (nodes/speech/speech_to_text.py)."""
    resp = httpx.post(
        f"{GATEWAY_URL}/v1/audio/translations",
        headers=_auth(person().key),
        data={"model": STT_MODEL},
        files={"file": ("ping.wav", _wav(), "audio/wav")},
        timeout=30,
    )
    assert resp.status_code in (404, 405), resp.text


# Gaps in 1.103.4 that OpenCompany works around. Each test passes once LiteLLM
# fixes its gap, and strict xfail then fails the suite: lift the workaround
# and drop the marker.


@pytest.mark.xfail(strict=True, reason="1.103.4 bills Groq transcription at $0, so Groq speech isn't served")
def test_groq_transcription_is_billed(person: Callable[..., Person], admin: httpx.Client) -> None:
    """Serve Groq speech ("gateway": true in server/config/speech_defaults.json) once this passes."""
    session = f"r_{uuid.uuid4().hex}"
    resp = httpx.post(
        f"{GATEWAY_URL}/v1/audio/transcriptions",
        headers={**_auth(person().key), **_attribution(session)},
        data={"model": GROQ_STT_MODEL},
        files={"file": ("ping.wav", _wav(), "audio/wav")},
        timeout=30,
    )
    assert resp.status_code == 200, resp.text
    _attributed_row(admin, session)


@pytest.mark.xfail(strict=True, reason="1.103.4 drops x-litellm-spend-logs-metadata on the native Gemini route")
def test_gemini_native_route_keeps_the_spend_logs_metadata(person: Callable[..., Person], admin: httpx.Client) -> None:
    session = f"r_{uuid.uuid4().hex}"
    resp = httpx.post(
        f"{GATEWAY_URL}/v1beta/models/{GEMINI_LIVE_MODEL}:generateContent",
        headers={"x-goog-api-key": person().key, **_attribution(session)},
        json={"contents": [{"role": "user", "parts": [{"text": "ping"}]}]},
        timeout=30,
    )
    assert resp.status_code == 200, resp.text
    _attributed_row(admin, session)


@pytest.mark.xfail(strict=True, reason="1.103.4 ignores the cache body on the native Gemini route")
def test_gemini_native_route_replays_retry_for_free(person: Callable[..., Person], admin: httpx.Client) -> None:
    """Send the replay-cache body from services/llm/providers/gemini.py once this passes."""
    key = person().key
    namespace = f"oc1-{uuid.uuid4().hex}"
    for suffix in ("first", "retry"):
        resp = httpx.post(
            f"{GATEWAY_URL}/v1beta/models/{GEMINI_LIVE_MODEL}:generateContent",
            headers={"x-goog-api-key": key, "x-litellm-session-id": f"{namespace}-{suffix}"},
            json={
                "contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                "cache": {"use-cache": True, "namespace": namespace, "ttl": 600},
            },
            timeout=30,
        )
        assert resp.status_code == 200, resp.text
    rows = _spend_rows(admin, session_id=f"{namespace}-retry")
    assert rows, "no spend row for the retry"
    assert float(rows[0].get("spend") or 0) == 0.0, rows[0]
