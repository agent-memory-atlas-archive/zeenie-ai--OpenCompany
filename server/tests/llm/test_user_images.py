"""Images the owner attaches to a chat message: carried as ref-only blocks on
the opening user message, hydrated only for a provider that declares
``vision.user_images`` (each encoder's own shape: Anthropic image blocks,
OpenAI image_url / input_image, Gemini inline_data), and named with their
workspace path for a model that cannot view them."""

from __future__ import annotations

import base64
from unittest.mock import patch

import pytest

from services.llm.media import hydrate_image_blocks, image_blocks, provider_supports_user_images
from services.llm.protocol import ContentBlock, Message, message_from_wire

REF = {
    "kind": "image",
    "path": "uploads/shelf.png",
    "workflow_id": "wf",
    "filename": "shelf.png",
    "mime_type": "image/png",
    "size_bytes": 64,
}
PIXEL = base64.b64encode(b"png-bytes").decode("ascii")


def owner_message(text: str = "Is this stock right?") -> Message:
    return Message(role="user", content=text, blocks=[ContentBlock(type="text", text=text), *image_blocks([REF])])


def hydrated_message(text: str = "Is this stock right?") -> Message:
    source = {"kind": "bytes", "media_type": "image/png", "data_b64": PIXEL, "detail": "auto"}
    return Message(role="user", content=text, blocks=[ContentBlock(type="text", text=text), ContentBlock(type="image", source=source)])


def test_only_attached_images_become_blocks():
    blocks = image_blocks([REF, {**REF, "mime_type": "text/csv"}, {**REF, "path": ""}, "nope"])
    assert [(block.type, block.source["kind"], block.source["ref"]["path"]) for block in blocks] == [("image", "file_ref", "uploads/shelf.png")]


def test_the_gate_names_the_providers_with_an_encoder():
    for provider in ("anthropic", "openai", "gemini"):
        assert provider_supports_user_images(provider) is True
    for provider in ("deepseek", "groq", "openrouter", "nope"):
        assert provider_supports_user_images(provider) is False


async def test_a_capable_provider_gets_bytes_on_the_owner_message(monkeypatch):
    async def fake_hydrate(ref, detail):
        return PIXEL, "image/png"

    monkeypatch.setattr("services.llm.media._hydrate_one", fake_hydrate)
    message = owner_message()
    [hydrated] = await hydrate_image_blocks([message], provider="openai", model="gpt-6")
    images = [block for block in hydrated.blocks if block.type == "image"]
    assert images[0].source == {"kind": "bytes", "media_type": "image/png", "data_b64": PIXEL, "detail": "auto"}
    # The durable message keeps its refs.
    assert any(block.source and block.source.get("kind") == "file_ref" for block in message.blocks)


async def test_a_model_that_cannot_view_images_reads_where_they_are(monkeypatch):
    async def never(ref, detail):
        raise AssertionError("no bytes for a model that cannot view them")

    monkeypatch.setattr("services.llm.media._hydrate_one", never)
    [hydrated] = await hydrate_image_blocks([owner_message()], provider="deepseek", model="deepseek-chat")
    assert all(block.type != "image" for block in hydrated.blocks)
    assert hydrated.content.startswith("Is this stock right?\n\n[Image attached: shelf.png")
    assert "uploads/shelf.png in the workspace" in hydrated.content


async def test_tool_results_keep_their_own_gate(monkeypatch):
    async def fake_hydrate(ref, detail):
        return PIXEL, "image/png"

    monkeypatch.setattr("services.llm.media._hydrate_one", fake_hydrate)
    tool = Message(role="tool", content="{}", tool_call_id="t1", blocks=[ContentBlock(type="text", text="{}"), *image_blocks([REF])])
    # OpenAI views the owner's images but not yet a tool's.
    [hydrated] = await hydrate_image_blocks([tool], provider="openai", model="gpt-6")
    assert all(block.type != "image" for block in hydrated.blocks)


def test_anthropic_puts_the_images_ahead_of_the_words():
    with patch("anthropic.AsyncAnthropic"):
        from services.llm.providers.anthropic import AnthropicProvider

        provider = AnthropicProvider("key")
    api = provider._to_api_message(hydrated_message())
    assert api == {
        "role": "user",
        "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": PIXEL}},
            {"type": "text", "text": "Is this stock right?"},
        ],
    }
    # Only images: no empty text block.
    assert provider._to_api_message(hydrated_message(""))["content"] == [api["content"][0]]
    # A message with nothing to view stays a string.
    assert provider._to_api_message(Message(role="user", content="Hi")) == {"role": "user", "content": "Hi"}


def test_openai_sends_data_urls_on_both_apis():
    with patch("openai.AsyncOpenAI"):
        from services.llm.providers.openai import OpenAIProvider

        provider = OpenAIProvider("key")
    url = f"data:image/png;base64,{PIXEL}"
    assert provider._to_api_message(hydrated_message()) == {
        "role": "user",
        "content": [
            {"type": "text", "text": "Is this stock right?"},
            {"type": "image_url", "image_url": {"url": url, "detail": "auto"}},
        ],
    }
    [item] = provider._to_responses_input([hydrated_message()])
    assert item == {
        "role": "user",
        "content": [
            {"type": "input_text", "text": "Is this stock right?"},
            {"type": "input_image", "image_url": url, "detail": "auto"},
        ],
    }
    assert provider._to_api_message(Message(role="user", content="Hi")) == {"role": "user", "content": "Hi"}


def test_gemini_sends_inline_data():
    with patch("google.genai.Client"):
        from services.llm.providers.gemini import GeminiProvider

        provider = GeminiProvider("key")
    _, contents = provider._split_system_and_contents([hydrated_message()])
    assert contents == [
        {
            "role": "user",
            "parts": [
                {"text": "Is this stock right?"},
                {"inline_data": {"mime_type": "image/png", "data": b"png-bytes"}},
            ],
        }
    ]
    _, plain = provider._split_system_and_contents([Message(role="user", content="Hi")])
    assert plain == [{"role": "user", "parts": [{"text": "Hi"}]}]


def test_the_opening_message_carries_the_images():
    from services.temporal.agent_workflow import _owner_message

    wire = _owner_message("Is this stock right?", [REF])
    message = message_from_wire(wire)
    assert message.content == "Is this stock right?"
    assert [(block.type, (block.source or {}).get("kind")) for block in message.blocks] == [("text", None), ("image", "file_ref")]
    # No images (or none the owner sent): the plain message as before.
    plain = message_from_wire(_owner_message("Hi", None))
    assert [block.type for block in plain.blocks] == ["text"]


@pytest.mark.parametrize("images", [[], [{"mime_type": "text/csv", "path": "uploads/a.csv", "workflow_id": "wf"}], "nope"])
def test_no_images_no_blocks(images):
    from services.temporal.agent_workflow import _owner_message

    assert [block.type for block in message_from_wire(_owner_message("Hi", images)).blocks] == ["text"]
