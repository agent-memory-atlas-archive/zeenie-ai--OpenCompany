"""Speech runs on the gateway only for providers speech_defaults.json marks
("gateway": true), and only with no key of the install's own (nodes/speech)."""

import io
import json
import struct
import wave
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import respx
from structlog.contextvars import bound_contextvars

from nodes.speech import _handlers
from nodes.speech._base import gateway_serves
from services.gateway.routing import GatewayRouter
from services.llm.config import LLM_DEFAULTS
from tests.nodes._mocks import patched_container, patched_pricing

GATEWAY = "https://gw.example"


@contextmanager
def gateway_container(keys):
    """The node test container with a real, enabled GatewayRouter."""
    with patched_container(auth_api_keys=keys) as container, patched_pricing():
        settings = SimpleNamespace(gateway_enabled=True, distributed_mode=False, gateway_url=GATEWAY)
        router = GatewayRouter(settings=settings, auth_service=container.auth_service(), defaults=LLM_DEFAULTS)
        container.gateway_router = MagicMock(return_value=router)
        yield container


def _clip(harness, tmp_path):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(struct.pack("<h", 0) * 4000)
    (tmp_path / "a.wav").write_bytes(buffer.getvalue())
    return harness.build_context(workspace_dir=str(tmp_path))


def test_only_openai_speech_is_served():
    """Groq's would bill $0 on LiteLLM 1.103.4 (gateway/tests/test_gateway_contract.py)."""
    assert gateway_serves("openai")
    assert not any(gateway_serves(p) for p in ("groq", "elevenlabs", "deepgram", "sarvam"))


@respx.mock
async def test_speech_without_an_own_key_runs_on_the_gateway(harness, tmp_path):
    route = respx.post(f"{GATEWAY}/v1/audio/speech").mock(return_value=httpx.Response(200, content=b"ID3audio"))
    # BaseNode.execute binds the run's ids, but tests/conftest.py stubs
    # log_context out, so the test binds them.
    with gateway_container({"opencompany_gateway": "sk-gateway"}), bound_contextvars(workflow_id="wf-1"):
        result = await harness.execute(
            "textToSpeech",
            {"provider": "openai", "text": "hello"},
            context=harness.build_context(workspace_dir=str(tmp_path)),
        )
    harness.assert_envelope(result, success=True)
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-gateway"
    assert json.loads(request.content)["model"] == "openai/gpt-4o-mini-tts"
    assert "workflow:wf-1" in request.headers["x-litellm-tags"]
    # The prefix stays on the wire.
    assert result["result"]["tts_model"] == "gpt-4o-mini-tts"


@respx.mock
async def test_an_own_key_wins_over_the_gateway(harness, tmp_path):
    route = respx.post("https://api.openai.com/v1/audio/speech").mock(
        return_value=httpx.Response(200, content=b"ID3audio")
    )
    with gateway_container({"openai": "sk-own", "opencompany_gateway": "sk-gateway"}):
        result = await harness.execute(
            "textToSpeech",
            {"provider": "openai", "text": "hello"},
            context=harness.build_context(workspace_dir=str(tmp_path)),
        )
    harness.assert_envelope(result, success=True)
    request = route.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-own"
    assert json.loads(request.content)["model"] == "gpt-4o-mini-tts"
    assert "x-litellm-tags" not in request.headers


@respx.mock
async def test_gateway_transcription_never_asks_for_a_translation(harness, tmp_path):
    """LiteLLM has no /audio/translations route: translate falls back to
    in-language transcription, as for a model that cannot translate."""
    route = respx.post(f"{GATEWAY}/v1/audio/transcriptions").mock(
        return_value=httpx.Response(200, json={"text": "hi", "duration": 3.0})
    )
    with gateway_container({"opencompany_gateway": "sk-gateway"}):
        result = await harness.execute(
            "speechToText",
            {"provider": "openai", "audio_file": "a.wav", "stt_model": "whisper-1", "translate": True},
            context=_clip(harness, tmp_path),
        )
    harness.assert_envelope(result, success=True)
    assert b'name="model"\r\n\r\nopenai/whisper-1\r\n' in route.calls.last.request.content


@respx.mock
async def test_a_spent_allowance_is_worded_as_the_opencompany_allowance(harness, tmp_path):
    respx.post(f"{GATEWAY}/v1/audio/speech").mock(
        return_value=httpx.Response(429, json={"error": {"type": "budget_exceeded", "message": "Budget has been exceeded!"}})
    )
    with gateway_container({"opencompany_gateway": "sk-gateway"}):
        result = await harness.execute(
            "textToSpeech",
            {"provider": "openai", "text": "hello"},
            context=harness.build_context(workspace_dir=str(tmp_path)),
        )
    harness.assert_envelope(result, success=False)
    assert "Your OpenCompany allowance for this month is used up." in result["error"]


@respx.mock
async def test_unmarked_providers_never_run_on_the_gateway(harness, tmp_path):
    """Groq serves chat on the gateway, but not speech: no own key is still no key."""
    route = respx.post(f"{GATEWAY}/v1/audio/transcriptions").mock(return_value=httpx.Response(200, json={"text": "hi"}))
    with gateway_container({"opencompany_gateway": "sk-gateway"}):
        result = await harness.execute(
            "speechToText", {"provider": "groq", "audio_file": "a.wav"}, context=_clip(harness, tmp_path)
        )
    harness.assert_envelope(result, success=False)
    assert not route.called


async def test_dictation_falls_back_to_the_gateway_after_every_own_key():
    with gateway_container({"deepgram": "dg-own", "opencompany_gateway": "sk-gateway"}):
        assert await _handlers.dictation_provider() == "deepgram"
    with gateway_container({"opencompany_gateway": "sk-gateway"}):
        assert await _handlers.dictation_provider() == "openai"
    with gateway_container({}):
        assert await _handlers.dictation_provider() is None
