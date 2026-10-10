"""A spent allowance is BILLING whatever the status, and gateway calls get
gateway wording (services/llm/protocol.py, services/gateway/errors.py)."""

import httpx
import openai
import pytest

from services.gateway.errors import describe, explain
from services.llm.protocol import LLMError, LLMErrorCategory, error_type


def _gateway_error(status: int, error_type_: str, message: str = "refused") -> openai.APIStatusError:
    """What the OpenAI SDK raises for a LiteLLM error body."""
    body = {"message": message, "type": error_type_, "param": None, "code": str(status)}
    response = httpx.Response(
        status,
        request=httpx.Request("POST", "https://gw.example/v1/chat/completions"),
        json={"error": body},
    )
    return openai.APIStatusError(message, response=response, body=body)


@pytest.mark.parametrize("status", [402, 422, 429])
def test_a_spent_allowance_is_billing_whatever_the_status(status):
    """LiteLLM answers 429 before 1.104, 402 with our setting, 422 by default after."""
    error = LLMError.from_exception("openai", _gateway_error(status, "budget_exceeded", "Budget has been exceeded!"))
    assert error.category is LLMErrorCategory.BILLING
    assert error.retryable is False
    assert error.requires_user_action is True


def test_the_body_type_is_readable_although_provider_code_is_the_status():
    exc = _gateway_error(403, "model_cost_map_missing")
    assert LLMError.from_exception("openai", exc).provider_code == "403"
    assert error_type(exc) == "model_cost_map_missing"


def test_a_spent_allowance_is_worded_as_the_opencompany_allowance():
    exc = _gateway_error(429, "budget_exceeded")
    error = explain(LLMError.from_exception("openai", exc), exc)
    assert error.user_message == "Your OpenCompany allowance for this month is used up."
    assert "raise it" in error.hint
    node_error = error.as_node_error()
    assert node_error.requires_user_action is True


def test_an_unpriced_model_says_so():
    exc = _gateway_error(403, "model_cost_map_missing")
    error = explain(LLMError.from_exception("openai", exc), exc)
    assert error.category is LLMErrorCategory.PERMISSION
    assert error.user_message == "This model isn't available on the OpenCompany Gateway yet."
    assert error.hint.startswith("Choose another model")


def test_a_rejected_gateway_key_points_at_the_gateway_card():
    exc = _gateway_error(401, "auth_error")
    error = explain(LLMError.from_exception("openai", exc), exc)
    assert error.category is LLMErrorCategory.AUTHENTICATION
    assert "OpenCompany Gateway" in error.user_message
    assert "Reconnect the OpenCompany Gateway" in error.hint


def test_wording_never_changes_retry_behaviour():
    exc = _gateway_error(503, "service_unavailable")
    error = LLMError.from_exception("openai", exc)
    before = (error.category, error.retryable)
    explain(error, exc)
    assert (error.category, error.retryable) == before
    assert error.user_message == "The OpenCompany Gateway is temporarily unavailable."


def test_public_hint_replaces_the_category_hint():
    error = LLMError(message="x", provider="openai", category=LLMErrorCategory.BILLING, public_hint="custom")
    assert error.hint == "custom"
    assert LLMError(message="x", provider="openai", category=LLMErrorCategory.BILLING).hint.startswith("Review the provider")


def _http_error(status: int, error_type_: str) -> httpx.HTTPStatusError:
    """What a speech provider's ``raise_for_status`` raises for a LiteLLM error body."""
    request = httpx.Request("POST", "https://gw.example/v1/audio/speech")
    response = httpx.Response(
        status, request=request, json={"error": {"message": "refused", "type": error_type_, "code": str(status)}}
    )
    return httpx.HTTPStatusError("refused", request=request, response=response)


@pytest.mark.parametrize("make", [_gateway_error, _http_error], ids=["sdk", "httpx"])
def test_described_failures_read_the_body_type_of_sdk_and_httpx_errors(make):
    """Embeddings and vision raise SDK errors; speech raises httpx ones."""
    assert describe(make(429, "budget_exceeded")).startswith("Your OpenCompany allowance for this month is used up.")
    assert describe(make(403, "model_cost_map_missing")).startswith("This model isn't available")


@pytest.mark.parametrize(
    "status, expected",
    [
        (401, "The OpenCompany Gateway rejected the gateway key. Reconnect"),
        (403, "The OpenCompany Gateway denied this request. Reconnect"),
        (429, "The OpenCompany Gateway is rate-limiting requests."),
        (503, "The OpenCompany Gateway is temporarily unavailable."),
    ],
)
def test_described_failures_fall_back_to_the_status(status, expected):
    assert describe(_http_error(status, "other")).startswith(expected)


def test_an_unreachable_gateway_and_a_rejected_request_are_told_apart():
    request = httpx.Request("POST", "https://gw.example/v1/embeddings")
    assert describe(httpx.ConnectError("refused", request=request)) == "Could not reach the OpenCompany Gateway."
    # A request the gateway rejected for its content keeps the caller's wording.
    assert describe(_http_error(400, "invalid_request_error")) is None
    assert describe(ValueError("bad")) is None
