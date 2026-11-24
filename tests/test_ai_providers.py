"""AI providers: registry, request bodies, negotiation, errors, transport, Claude, parsing.

Nothing here talks to a real provider. OpenAI-compatible and Gemini clients get a
fake transport; the default transport and the Claude SDK client talk to a local
HTTP server on 127.0.0.1.
"""

from __future__ import annotations

import copy
import datetime
import http.server
import ipaddress
import json
import socket
import ssl
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

import pytest
import requests
import urllib3

from okline import ai_claude, ai_providers, assistant
from okline.ai_claude import ClaudeClient
from okline.ai_providers import (
    AIProvider,
    OpenAICompatibleClient,
    get_provider,
    make_ai_client,
    normalize_custom_base_url,
    provider_available,
    provider_ids,
    provider_registry,
)
from okline.assistant import (
    ANSWER_SCHEMA,
    MAX_AI_EMBEDDING_BYTES,
    MAX_AI_MODEL_LIST_BYTES,
    MAX_AI_RESPONSE_BYTES,
    MAX_EMBEDDING_DIMENSIONS,
    AssistantError,
    DeadlineAdapter,
    DeadlineWatchdog,
    GeminiClient,
    GeminiError,
    ProviderError,
    WatchedHTTPConnection,
    WatchedHTTPSConnection,
    default_transport,
)

ANSWER = {"answer": "ร้านเปิด 9 โมงเช้า", "canAnswer": True, "confidence": 0.8}
ANSWER_TEXT = json.dumps(ANSWER, ensure_ascii=False)
KEY = "sk-test-key-1234"


@pytest.fixture(autouse=True)
def _fresh_learning():
    ai_providers.reset_negotiation_cache()
    ai_claude.reset_learned_settings()
    yield
    ai_providers.reset_negotiation_cache()
    ai_claude.reset_learned_settings()


class FakeTransport:
    """Records every request and answers from a script; the last reply repeats."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def __call__(self, method, url, *, headers, json_body, timeout, max_bytes):
        self.calls.append(
            {
                "method": method,
                "url": url,
                "headers": dict(headers),
                "body": copy.deepcopy(json_body),
                "timeout": timeout,
                "max_bytes": max_bytes,
            }
        )
        reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        if isinstance(reply, BaseException):
            raise reply
        status, body = reply
        raw = (
            body if isinstance(body, bytes) else json.dumps(body, ensure_ascii=False).encode()
        )
        return status, {"Content-Type": "application/json"}, raw


def completion(content: Any, *, finish: str = "stop", refusal: Any = None, usage: Any = None):
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if refusal is not None:
        message["refusal"] = refusal
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "model": "served-model",
        "choices": [{"index": 0, "message": message, "finish_reason": finish}],
        "usage": usage if usage is not None else {"prompt_tokens": 12, "completion_tokens": 5},
    }


def ask(client: Any, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": "",
        "question": "ร้านเปิดกี่โมง",
        "context": "ร้านเปิดทุกวัน 9 โมงเช้า",
        "system_prompt": "You answer for the shop.",
        "strict_knowledge": True,
        "temperature": 0.2,
    }
    params.update(overrides)
    return client.chat(**params)


def client_for(provider_id: str, transport: FakeTransport, **kwargs: Any) -> Any:
    kwargs.setdefault("api_key", KEY)
    if provider_id == "custom":
        kwargs.setdefault("base_url", "https://llm.example.com/v1")
    return make_ai_client(provider_id, transport=transport, **kwargs)


# --- registry -----------------------------------------------------------------


def test_registry_lists_every_provider_in_ui_order():
    assert provider_ids() == (
        "gemini",
        "anthropic",
        "openai",
        "zai",
        "deepseek",
        "qwen",
        "xai",
        "mistral",
        "moonshot",
        "openrouter",
        "custom",
    )
    groups = {provider.id: provider.group for provider in provider_registry()}
    assert [key for key, group in groups.items() if group == "main"] == [
        "gemini",
        "anthropic",
        "openai",
    ]
    assert groups["custom"] == "custom"
    assert all(groups[key] == "more" for key in ("zai", "deepseek", "qwen", "xai", "mistral"))


@pytest.mark.parametrize(
    ("provider_id", "label", "kind", "base_url", "chat_model", "embedding_model"),
    [
        (
            "gemini",
            "Gemini (Google)",
            "gemini",
            "https://generativelanguage.googleapis.com",
            "gemini-3.8-flash",
            "gemini-embedding-001",
        ),
        (
            "anthropic",
            "Claude (Anthropic)",
            "anthropic",
            "https://api.anthropic.com",
            "claude-opus-5-5",
            "",
        ),
        (
            "openai",
            "ChatGPT (OpenAI)",
            "openai_compat",
            "https://api.openai.com/v1",
            "gpt-6-luna",
            "text-embedding-3-small",
        ),
        ("zai", "GLM (Z.ai)", "openai_compat", "https://api.z.ai/api/paas/v4", "glm-4.7", ""),
        (
            "deepseek",
            "DeepSeek",
            "openai_compat",
            "https://api.deepseek.com",
            "deepseek-flash",
            "",
        ),
        (
            "qwen",
            "Qwen (Alibaba Cloud)",
            "openai_compat",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
            "qwen3.8-flash",
            "text-embedding-v4",
        ),
        (
            "xai",
            "Grok (xAI)",
            "openai_compat",
            "https://api.x.ai/v1",
            "grok-4.20-non-reasoning",
            "",
        ),
        (
            "mistral",
            "Mistral AI",
            "openai_compat",
            "https://api.mistral.ai/v1",
            "mistral-small-latest",
            "mistral-embed",
        ),
        (
            "moonshot",
            "Kimi (Moonshot AI)",
            "openai_compat",
            "https://api.moonshot.ai/v1",
            "kimi-k3.1-pro-highspeed",
            "",
        ),
        (
            "openrouter",
            "OpenRouter",
            "openai_compat",
            "https://openrouter.ai/api/v1",
            "openai/gpt-6-luna",
            "openai/text-embedding-3-small",
        ),
        ("custom", "Custom API (OpenAI-compatible)", "openai_compat", "", "", ""),
    ],
)
def test_registry_entries(provider_id, label, kind, base_url, chat_model, embedding_model):
    provider = get_provider(provider_id)
    assert provider is not None
    assert (provider.label, provider.kind, provider.base_url) == (label, kind, base_url)
    assert provider.default_chat_model == chat_model
    assert provider.default_embedding_model == embedding_model
    assert provider.key_required is (provider_id != "custom")
    assert provider.needs_base_url is (provider_id == "custom")
    # Plain-text hints only: no clickable links. The Custom API has none: the
    # page says in the reader's language that its key is optional.
    assert "://" not in provider.key_hint and "<" not in provider.key_hint
    assert bool(provider.key_hint) is (provider_id != "custom")
    assert bool(provider.key_placeholder) is (provider_id != "custom")
    assert provider.embed_batch > 0


def test_key_hints_are_place_names_not_sentences():
    # The page writes "Get a key at {hint}" around the hint, in Thai or English,
    # so a hint names a place and carries no sentence of its own.
    assert get_provider("zai").key_hint == "z.ai -> API Keys"
    assert get_provider("custom").key_hint == ""
    assert get_provider("custom").key_placeholder == ""
    for provider in provider_registry():
        hint = provider.key_hint
        assert "(" not in hint and ". " not in hint and not hint.endswith("."), provider.id


def test_registry_request_rules_are_data():
    rules = {
        provider.id: (
            provider.json_mode,
            provider.send_temperature,
            provider.temperature_max,
            provider.max_tokens_field,
            provider.max_tokens,
            dict(provider.extra_body),
            provider.embeddings,
            provider.embed_batch,
            provider.models_source,
        )
        for provider in provider_registry()
        if provider.kind == "openai_compat"
    }
    assert rules["openai"] == (
        "schema", True, 2.0, "max_completion_tokens", 1024,
        {"reasoning_effort": "none", "store": False}, True, 32, "openai",
    )  # fmt: skip
    assert rules["zai"][:6] == (
        "object", True, 1.0, "max_tokens", 1024, {"thinking": {"type": "disabled"}},
    )  # fmt: skip
    assert rules["zai"][8] == "static" and rules["zai"][6] is False
    assert rules["deepseek"][:6] == (
        "object", True, 2.0, "max_tokens", 1024, {"thinking": {"type": "disabled"}},
    )  # fmt: skip
    assert rules["qwen"][0] == "object" and rules["qwen"][3] is None
    assert rules["qwen"][5] == {"enable_thinking": False} and rules["qwen"][7] == 10
    assert rules["xai"][:6] == ("schema", True, 2.0, "max_tokens", 1024, {})
    assert rules["mistral"][5] == {"reasoning_effort": "none"} and rules["mistral"][7] == 32
    assert rules["moonshot"][:6] == (
        "schema", False, 2.0, "max_tokens", 4096, {"reasoning_effort": "low"},
    )  # fmt: skip
    assert rules["openrouter"][5] == {
        "provider": {"require_parameters": True, "sort": "latency"},
        "reasoning": {"effort": "none"},
    }
    assert rules["openrouter"][8] == "openrouter_user"
    assert rules["custom"][:5] == ("object", True, 2.0, "max_tokens", 1024)
    assert rules["custom"][6:] == (True, 16, "openai")
    assert get_provider("zai").static_models[0] == "glm-4.7"
    assert get_provider("qwen").static_models == (
        "qwen3.8-flash",
        "qwen3.7-plus",
        "qwen4-max",
        "qwen-flash",
    )
    assert get_provider("gemini").embed_batch == 16
    assert get_provider("anthropic").embeddings is False
    assert {
        provider.id: provider.refusal_error_codes
        for provider in provider_registry()
        if provider.refusal_error_codes
    } == {"zai": ("1301",)}


def test_registry_extra_body_cannot_be_changed_through_a_request():
    provider = get_provider("zai")
    with pytest.raises(TypeError):
        provider.extra_body["thinking"] = {}
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    ask(client_for("zai", transport))
    transport.calls[0]["body"]["thinking"]["type"] = "enabled"
    sent = client_for("zai", transport)._chat_body("glm-4.7", [], 0.2, frozenset())
    sent["thinking"]["type"] = "enabled"
    sent["response_format"]["type"] = "text"
    assert dict(provider.extra_body) == {"thinking": {"type": "disabled"}}
    openai_body = client_for("openai", transport)._chat_body(
        "gpt-6-luna", [], 0.2, frozenset()
    )
    openai_body["response_format"]["json_schema"]["schema"]["required"].append("extra")
    assert ANSWER_SCHEMA["required"] == ["answer", "canAnswer", "confidence"]


def test_get_provider_and_availability():
    assert get_provider("ollama") is None
    assert get_provider("global") is None
    assert get_provider("") is None
    assert get_provider(" OpenAI ").id == "openai"
    assert provider_available("ollama") == (False, "ai_settings_invalid")
    assert provider_available("openai") == (True, "")
    assert provider_available("custom") == (True, "")


def test_claude_is_unavailable_without_the_sdk(monkeypatch):
    monkeypatch.setattr(ai_providers, "anthropic_sdk_available", lambda: False)
    assert provider_available("anthropic") == (False, "ai_provider_unavailable")
    with pytest.raises(AssistantError) as caught:
        make_ai_client("anthropic", api_key="sk-ant-x")
    assert caught.value.code == "ai_provider_unavailable"


@pytest.mark.parametrize(
    ("python_ok", "version", "expected"),
    [
        (True, "2.12.0", True),
        (True, "2.30.1", True),
        (True, "2.11.9", False),
        (True, "3.0.0", False),
        (True, "0.125.0", False),
        (True, "", False),
        (False, "2.12.0", False),
    ],
)
def test_sdk_version_check(monkeypatch, python_ok, version, expected):
    monkeypatch.setattr(ai_claude, "_python_supported", lambda: python_ok)
    monkeypatch.setattr(ai_claude, "_installed_sdk_version", lambda: version)
    assert ai_claude.anthropic_sdk_available() is expected


def test_public_fields_for_the_settings_page():
    fields = get_provider("custom").public_fields()
    assert fields == {
        "id": "custom",
        "label": "Custom API (OpenAI-compatible)",
        "group": "custom",
        "keyRequired": False,
        "keyHint": "",
        "keyPlaceholder": "",
        "defaultChatModel": "",
        "defaultEmbeddingModel": "",
        "embeddings": True,
        "needsBaseUrl": True,
    }
    assert (
        get_provider("gemini").public_fields()["keyHint"]
        == "aistudio.google.com -> Get API key"
    )


# --- factory ------------------------------------------------------------------


def test_factory_builds_the_right_client():
    transport = FakeTransport((200, {}))
    assert isinstance(make_ai_client("gemini", api_key=KEY, transport=transport), GeminiClient)
    for provider_id in (
        "openai",
        "zai",
        "deepseek",
        "qwen",
        "xai",
        "mistral",
        "moonshot",
        "openrouter",
    ):
        client = make_ai_client(provider_id, api_key=KEY, transport=transport)
        assert isinstance(client, OpenAICompatibleClient)
        assert client.provider.id == provider_id
    if ai_claude.anthropic_sdk_available():
        assert isinstance(make_ai_client("anthropic", api_key="sk-ant-x"), ClaudeClient)


@pytest.mark.parametrize("provider_id", ["ollama", "global", "", "nope"])
def test_factory_refuses_unknown_providers(provider_id):
    with pytest.raises(AssistantError) as caught:
        make_ai_client(provider_id, api_key=KEY)
    assert caught.value.code == "ai_settings_invalid"


def test_factory_keeps_named_providers_on_their_own_address():
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    client = make_ai_client(
        "openai", api_key=KEY, base_url="https://evil.example/v1", transport=transport
    )
    ask(client)
    assert transport.calls[0]["url"] == "https://api.openai.com/v1/chat/completions"


def test_factory_custom_api_needs_base_url_and_transport():
    with pytest.raises(AssistantError) as caught:
        make_ai_client("custom", api_key="", base_url="  ", transport=FakeTransport((200, {})))
    assert caught.value.code == "ai_base_url_invalid"
    with pytest.raises(AssistantError):
        make_ai_client("custom", api_key="", base_url="https://llm.example.com/v1")
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    client = make_ai_client(
        "custom",
        api_key="",
        base_url="https://llm.example.com/v1/chat/completions/",
        transport=transport,
    )
    ask(client, model="my-model")
    assert transport.calls[0]["url"] == "https://llm.example.com/v1/chat/completions"


def test_normalize_custom_base_url():
    assert normalize_custom_base_url(" https://a.example/v1/ ") == "https://a.example/v1"
    assert (
        normalize_custom_base_url("https://a.example/v1/chat/completions")
        == "https://a.example/v1"
    )
    assert normalize_custom_base_url("https://a.example/v1/models") == "https://a.example/v1"
    assert (
        normalize_custom_base_url("https://a.example/v1/Embeddings") == "https://a.example/v1"
    )
    assert normalize_custom_base_url("https://a.example/openai") == "https://a.example/openai"
    assert normalize_custom_base_url("") == ""


# --- request bodies -----------------------------------------------------------

SCHEMA_FORMAT = {
    "type": "json_schema",
    "json_schema": {"name": "answer", "strict": True, "schema": ANSWER_SCHEMA},
}
OBJECT_FORMAT = {"type": "json_object"}


@pytest.mark.parametrize(
    ("provider_id", "url", "expected", "absent"),
    [
        (
            "openai",
            "https://api.openai.com/v1/chat/completions",
            {
                "response_format": SCHEMA_FORMAT,
                "temperature": 0.2,
                "max_completion_tokens": 1024,
                "reasoning_effort": "none",
                "store": False,
            },
            ["max_tokens", "n", "presence_penalty", "frequency_penalty", "stop", "user"],
        ),
        (
            "zai",
            "https://api.z.ai/api/paas/v4/chat/completions",
            {
                "response_format": OBJECT_FORMAT,
                "temperature": 0.2,
                "max_tokens": 1024,
                "thinking": {"type": "disabled"},
            },
            ["max_completion_tokens", "store"],
        ),
        (
            "deepseek",
            "https://api.deepseek.com/chat/completions",
            {
                "response_format": OBJECT_FORMAT,
                "temperature": 0.2,
                "max_tokens": 1024,
                "thinking": {"type": "disabled"},
            },
            ["store", "reasoning_effort"],
        ),
        (
            "qwen",
            "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
            {"response_format": OBJECT_FORMAT, "temperature": 0.2, "enable_thinking": False},
            ["max_tokens", "max_completion_tokens"],
        ),
        (
            "xai",
            "https://api.x.ai/v1/chat/completions",
            # "the-model" is not a non-reasoning Grok, so it gets room to reason.
            {"response_format": SCHEMA_FORMAT, "temperature": 0.2, "max_tokens": 4096},
            ["presence_penalty", "frequency_penalty", "stop", "store", "reasoning_effort"],
        ),
        (
            "mistral",
            "https://api.mistral.ai/v1/chat/completions",
            {
                "response_format": SCHEMA_FORMAT,
                "temperature": 0.2,
                "max_tokens": 1024,
                "reasoning_effort": "none",
            },
            ["store", "user", "n", "max_completion_tokens"],
        ),
        (
            "moonshot",
            "https://api.moonshot.ai/v1/chat/completions",
            {"response_format": SCHEMA_FORMAT, "max_tokens": 4096, "reasoning_effort": "low"},
            ["temperature", "top_p", "n"],
        ),
        (
            "openrouter",
            "https://openrouter.ai/api/v1/chat/completions",
            {
                "response_format": SCHEMA_FORMAT,
                "temperature": 0.2,
                "max_tokens": 1024,
                "provider": {"require_parameters": True, "sort": "latency"},
                "reasoning": {"effort": "none"},
            },
            ["store"],
        ),
        (
            "custom",
            "https://llm.example.com/v1/chat/completions",
            {"response_format": OBJECT_FORMAT, "temperature": 0.2, "max_tokens": 1024},
            ["store", "reasoning_effort", "thinking"],
        ),
    ],
)
def test_chat_request_body_per_provider(provider_id, url, expected, absent):
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    client = client_for(provider_id, transport)
    result = ask(client, model="the-model")
    assert result == {
        "answer": ANSWER["answer"],
        "canAnswer": True,
        "confidence": 0.8,
        "model": "served-model",
        "promptEvalCount": 12,
        "evalCount": 5,
    }
    (call,) = transport.calls
    assert call["method"] == "POST" and call["url"] == url
    assert call["headers"]["Authorization"] == f"Bearer {KEY}"
    assert call["max_bytes"] == MAX_AI_RESPONSE_BYTES
    assert 0 < call["timeout"] <= 60
    body = call["body"]
    assert body["model"] == "the-model"
    assert body["messages"][0] == {"role": "system", "content": "You answer for the shop."}
    assert body["messages"][1]["role"] == "user"
    assert "JSON" in body["messages"][1]["content"]
    assert "ร้านเปิดกี่โมง" in body["messages"][1]["content"]
    for key, value in expected.items():
        assert body[key] == value, key
    for key in absent:
        assert key not in body, key
    assert set(body) == {"model", "messages", *expected}


def test_grok_reasoning_models_get_room_to_reason():
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    ask(client_for("xai", transport))  # the default grok-4.20-non-reasoning
    ask(client_for("xai", transport), model="grok-4.7")
    ask(client_for("xai", transport), model="Grok-4.20-Non-Reasoning")
    assert [call["body"]["max_tokens"] for call in transport.calls] == [1024, 4096, 1024]


def test_default_models_start_with_the_small_token_cap():
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    caps = {}
    for provider in provider_registry():
        if provider.kind != "openai_compat" or provider.id == "custom":
            continue
        ask(client_for(provider.id, transport))
        body = transport.calls[-1]["body"]
        caps[provider.id] = body.get(provider.max_tokens_field or "", None)
    assert caps == {
        "openai": 1024,
        "zai": 1024,
        "deepseek": 1024,
        "qwen": None,
        "xai": 1024,
        "mistral": 1024,
        "moonshot": 4096,
        "openrouter": 1024,
    }


def test_openrouter_sends_no_attribution_headers():
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    ask(client_for("openrouter", transport))
    headers = {name.lower() for name in transport.calls[0]["headers"]}
    assert headers == {"accept", "authorization"}


def test_temperature_is_clamped_per_provider():
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    ask(client_for("zai", transport), temperature=1.7)
    ask(client_for("openai", transport), temperature=5)
    ask(client_for("openai", transport), temperature=-3)
    ask(client_for("openai", transport), temperature=float("nan"))
    ask(client_for("openai", transport), temperature=0)
    assert [call["body"]["temperature"] for call in transport.calls] == [
        1.0,
        2.0,
        0.0,
        0.2,
        0.0,
    ]


def test_default_chat_model_and_empty_system_prompt():
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    ask(client_for("deepseek", transport), system_prompt="  ")
    body = transport.calls[0]["body"]
    assert body["model"] == "deepseek-flash"
    assert [message["role"] for message in body["messages"]] == ["user"]


def test_custom_api_needs_a_chat_model_and_sends_no_key_when_empty():
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    client = client_for("custom", transport, api_key="")
    with pytest.raises(ProviderError) as caught:
        ask(client)
    assert caught.value.code == "ai_model_required"
    assert transport.calls == []
    ask(client, model="local-model")
    assert "Authorization" not in transport.calls[0]["headers"]


def test_missing_key_fails_before_any_request():
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    with pytest.raises(ProviderError) as caught:
        ask(client_for("openai", transport, api_key="  "))
    assert caught.value.code == "ai_key_required"
    assert transport.calls == []


# --- capability negotiation ---------------------------------------------------


def error_body(message: str, param: str | None = None) -> dict[str, Any]:
    return {"error": {"message": message, "type": "invalid_request_error", "param": param}}


def test_negotiation_drops_temperature_and_remembers_it():
    transport = FakeTransport(
        (
            400,
            error_body("Unsupported value: 'temperature' does not support 0.2", "temperature"),
        ),
        (200, completion(ANSWER_TEXT)),
    )
    client = client_for("xai", transport)
    assert ask(client, model="grok-x")["canAnswer"] is True
    first, second = (call["body"] for call in transport.calls)
    assert first["temperature"] == 0.2 and "temperature" not in second
    ask(client, model="grok-x")
    assert len(transport.calls) == 3
    assert "temperature" not in transport.calls[2]["body"]
    # Another model starts again from the full body.
    ask(client, model="grok-y")
    assert transport.calls[3]["body"]["temperature"] == 0.2


def test_negotiation_openai_moves_reasoning_effort_to_low_without_temperature():
    transport = FakeTransport(
        (
            400,
            error_body(
                "Unsupported value: 'reasoning_effort' does not support 'none' with this model."
            ),
        ),
        (200, completion(ANSWER_TEXT)),
    )
    ask(client_for("openai", transport), model="gpt-6.2-sol")
    first, retry = (call["body"] for call in transport.calls)
    assert first["max_completion_tokens"] == 1024
    assert retry["reasoning_effort"] == "low"
    assert "temperature" not in retry
    # Reasoning tokens count toward the cap, so it rises with reasoning back on.
    assert retry["store"] is False and retry["max_completion_tokens"] == 4096


def test_negotiation_openai_drops_reasoning_effort_when_low_also_fails():
    transport = FakeTransport(
        (400, error_body("Unrecognized request argument supplied: reasoning_effort")),
        (400, error_body("Unrecognized request argument supplied: reasoning_effort")),
        (200, completion(ANSWER_TEXT)),
    )
    ask(client_for("openai", transport), model="gpt-4o-mini")
    bodies = [call["body"] for call in transport.calls]
    assert bodies[1]["reasoning_effort"] == "low" and "temperature" not in bodies[1]
    assert "reasoning_effort" not in bodies[2]
    assert bodies[2]["temperature"] == 0.2


def test_negotiation_drops_a_named_extra_key():
    transport = FakeTransport(
        (400, {"error": {"code": "1214", "message": "Parameter thinking.type is invalid"}}),
        (200, completion(ANSWER_TEXT)),
    )
    ask(client_for("zai", transport))
    assert "thinking" in transport.calls[0]["body"]
    assert "thinking" not in transport.calls[1]["body"]
    assert transport.calls[1]["body"]["temperature"] == 0.2
    # Without "thinking disabled" the model thinks, so the cap leaves room for it.
    assert transport.calls[0]["body"]["max_tokens"] == 1024
    assert transport.calls[1]["body"]["max_tokens"] == 4096


def test_openrouter_mandatory_reasoning_drops_the_setting_and_raises_the_cap():
    transport = FakeTransport(
        (400, error_body("Reasoning is mandatory for this endpoint and cannot be disabled.")),
        (200, completion(ANSWER_TEXT)),
    )
    ask(client_for("openrouter", transport), model="some/thinking-model")
    first, retry = (call["body"] for call in transport.calls)
    assert first["reasoning"] == {"effort": "none"} and first["max_tokens"] == 1024
    assert "reasoning" not in retry and retry["max_tokens"] == 4096
    assert retry["provider"] == {"require_parameters": True, "sort": "latency"}


def test_negotiation_drops_temperature_first_when_the_text_names_more():
    transport = FakeTransport(
        (400, error_body("temperature and response_format cannot be used with this model")),
        (200, completion(ANSWER_TEXT)),
    )
    ask(client_for("openai", transport), model="odd-model")
    first, retry = (call["body"] for call in transport.calls)
    assert first["temperature"] == 0.2
    assert "temperature" not in retry
    assert retry["response_format"] == SCHEMA_FORMAT
    assert retry["reasoning_effort"] == "none"


def test_negotiation_reads_a_mistral_422_detail_list():
    transport = FakeTransport(
        (
            422,
            {
                "object": "error",
                "message": {
                    "detail": [
                        {
                            "type": "extra_forbidden",
                            "loc": ["body", "reasoning_effort"],
                            "msg": "Extra inputs are not permitted",
                        }
                    ]
                },
                "type": "invalid_request_message_error",
            },
        ),
        (200, completion(ANSWER_TEXT)),
    )
    ask(client_for("mistral", transport), model="mistral-large-latest")
    assert transport.calls[1]["body"]["reasoning_effort"] == "low"


def test_negotiation_steps_down_the_response_format():
    transport = FakeTransport(
        (400, error_body("response_format json_schema is not supported for this model")),
        (
            400,
            error_body(
                "Invalid parameter: response_format of type 'json_object' is not supported"
            ),
        ),
        (200, completion(ANSWER_TEXT)),
    )
    ask(client_for("openrouter", transport), model="some/model")
    formats = [call["body"].get("response_format") for call in transport.calls]
    assert formats == [SCHEMA_FORMAT, OBJECT_FORMAT, None]


def test_negotiation_gives_up_after_three_retries():
    transport = FakeTransport(
        (400, error_body("temperature is not supported")),
        (400, error_body("reasoning_effort 'none' is not supported")),
        (400, error_body("reasoning_effort is not supported")),
        (400, error_body("response_format is not supported")),
    )
    with pytest.raises(ProviderError) as caught:
        ask(client_for("openai", transport), model="odd-model")
    assert len(transport.calls) == 4
    assert caught.value.status_code == 400
    assert "OpenAI error 400" in str(caught.value)


@pytest.mark.parametrize("status", [401, 403, 404, 429, 500, 503])
def test_negotiation_never_retries_these_statuses(status):
    transport = FakeTransport(
        (status, error_body("temperature reasoning_effort response_format"))
    )
    with pytest.raises(ProviderError) as caught:
        ask(client_for("openai", transport))
    assert len(transport.calls) == 1
    assert caught.value.status_code == status


def test_negotiation_cache_is_keyed_by_base_url():
    transport = FakeTransport(
        (400, error_body("temperature is not supported")),
        (200, completion(ANSWER_TEXT)),
    )
    first = client_for("custom", transport, base_url="https://one.example/v1")
    ask(first, model="m")
    second = client_for("custom", transport, base_url="https://two.example/v1")
    ask(second, model="m")
    assert "temperature" not in transport.calls[1]["body"]
    assert transport.calls[2]["body"]["temperature"] == 0.2


def test_a_400_about_something_else_is_not_retried():
    transport = FakeTransport(
        (400, error_body("This model's maximum context length is 8192 tokens."))
    )
    with pytest.raises(ProviderError):
        ask(client_for("openai", transport))
    assert len(transport.calls) == 1


NO_ENDPOINTS = {
    "error": {
        "message": "No endpoints found that can handle the requested parameters.",
        "code": 404,
    }
}


def test_openrouter_no_endpoints_steps_the_json_mode_down_and_remembers_it():
    transport = FakeTransport((404, NO_ENDPOINTS), (200, completion(ANSWER_TEXT)))
    client = client_for("openrouter", transport)
    assert ask(client, model="vendor/model")["canAnswer"] is True
    formats = [call["body"].get("response_format") for call in transport.calls]
    assert formats == [SCHEMA_FORMAT, OBJECT_FORMAT]
    ask(client, model="vendor/model")
    assert transport.calls[2]["body"]["response_format"] == OBJECT_FORMAT
    assert len(transport.calls) == 3


def test_openrouter_no_endpoints_gives_up_after_three_steps():
    transport = FakeTransport((404, NO_ENDPOINTS))
    with pytest.raises(ProviderError) as caught:
        ask(client_for("openrouter", transport), model="vendor/model")
    assert caught.value.status_code == 404
    bodies = [call["body"] for call in transport.calls]
    assert len(bodies) == 4
    assert [body.get("response_format") for body in bodies] == [
        SCHEMA_FORMAT,
        OBJECT_FORMAT,
        None,
        None,
    ]
    assert "reasoning" in bodies[2] and "reasoning" not in bodies[3]


@pytest.mark.parametrize(
    ("provider_id", "status", "body"),
    [
        ("openrouter", 404, error_body("Model vendor/nope not found")),
        (
            "openrouter",
            404,
            {"error": {"message": "No endpoints found matching your data policy"}},
        ),
        ("openai", 404, NO_ENDPOINTS),
        ("custom", 404, NO_ENDPOINTS),
    ],
)
def test_other_404s_are_never_retried(provider_id, status, body):
    transport = FakeTransport((status, body))
    with pytest.raises(ProviderError) as caught:
        ask(client_for(provider_id, transport), model="vendor/model")
    assert caught.value.status_code == 404
    assert len(transport.calls) == 1


def test_concurrent_negotiations_keep_every_learned_step():
    barrier = threading.Barrier(2, timeout=10)
    thinking_refused = {
        "error": {"code": "1214", "message": "Parameter thinking.type is invalid"}
    }

    def transport_for(first_reply):
        calls: list[dict[str, Any]] = []

        def transport(method, url, *, headers, json_body, timeout, max_bytes):
            calls.append(json_body)
            if len(calls) == 1:
                barrier.wait()  # both calls have read the (still empty) learned steps
                return 400, {}, json.dumps(first_reply).encode()
            return 200, {}, json.dumps(completion(ANSWER_TEXT)).encode()

        return transport, calls

    first, first_calls = transport_for(error_body("temperature is not supported"))
    second, second_calls = transport_for(thinking_refused)
    errors: list[BaseException] = []

    def run(transport):
        try:
            ask(make_ai_client("zai", api_key=KEY, transport=transport))
        except BaseException as exc:
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(t,)) for t in (first, second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert errors == [] and len(first_calls) == 2 and len(second_calls) == 2
    key = ("zai", "https://api.z.ai/api/paas/v4", "glm-4.7")
    assert ai_providers._learned_flags(key) == {"no_temperature", "drop:thinking"}
    # The next call starts from both steps at once.
    transport = FakeTransport((200, completion(ANSWER_TEXT)))
    ask(client_for("zai", transport))
    (call,) = transport.calls
    assert "temperature" not in call["body"] and "thinking" not in call["body"]


# --- errors -------------------------------------------------------------------


def test_http_error_is_short_and_never_contains_the_key():
    transport = FakeTransport(
        (401, error_body(f"Incorrect API key provided: {KEY}. You can find your key at ..."))
    )
    with pytest.raises(ProviderError) as caught:
        ask(client_for("openai", transport))
    error = caught.value
    assert isinstance(error, AssistantError)
    assert error.status_code == 401 and error.code == "ai_key_invalid"
    assert KEY not in str(error) and "****" in str(error)
    assert str(error).startswith("OpenAI error 401: Incorrect API key provided")


def test_zai_balance_429_keeps_the_provider_message():
    transport = FakeTransport(
        (
            429,
            {
                "error": {
                    "code": "1113",
                    "message": "Insufficient balance or no resource package. Please recharge.",
                }
            },
        )
    )
    with pytest.raises(ProviderError) as caught:
        ask(client_for("zai", transport))
    assert caught.value.status_code == 429
    assert "Insufficient balance" in str(caught.value)


def test_non_json_error_body_uses_the_status_phrase():
    transport = FakeTransport((502, b"<html>bad gateway</html>"))
    with pytest.raises(ProviderError) as caught:
        ask(client_for("deepseek", transport))
    assert str(caught.value) == "DeepSeek error 502: Bad Gateway"


@pytest.mark.parametrize(
    "failure",
    [
        AssistantError("the provider did not answer in time"),
        requests.ConnectionError("boom"),
        ValueError("bad"),
        KeyError("x"),
        OSError("socket"),
    ],
)
def test_transport_failures_become_provider_errors(failure):
    transport = FakeTransport(failure)
    with pytest.raises(ProviderError) as caught:
        ask(client_for("mistral", transport))
    assert str(caught.value).startswith("Mistral request failed")
    # The question may have reached the provider: it is never sent again.
    assert len(transport.calls) == 1


@pytest.mark.parametrize("provider_id", ["openai", "custom"])
def test_a_transport_error_never_shows_the_key(provider_id):
    # requests repeats a bad header value in its error, the key included.
    transport = FakeTransport(AssistantError(f"Invalid header value b'Bearer {KEY}'"))
    with pytest.raises(ProviderError) as caught:
        ask(client_for(provider_id, transport), model="m")
    assert KEY not in str(caught.value) and "****" in str(caught.value)


def test_a_gemini_transport_error_never_shows_the_key():
    transport = FakeTransport(AssistantError("Invalid header value b'AIza-secret-key-9'"))
    with pytest.raises(GeminiError) as caught:
        ask(GeminiClient("AIza-secret-key-9", transport=transport))
    assert "AIza-secret-key-9" not in str(caught.value) and "****" in str(caught.value)
    assert str(caught.value).startswith("Gemini request failed")


def nested_error(depth: int) -> bytes:
    """An error body nested ``depth`` levels deep: {"detail": {"detail": ...}}."""
    return ('{"detail": ' * depth + '"deep"' + "}" * depth).encode()


@pytest.mark.parametrize("depth", [5, 900, 1400])
def test_a_deeply_nested_error_body_is_an_ordinary_error(depth):
    transport = FakeTransport((400, nested_error(depth)))
    with pytest.raises(ProviderError) as caught:
        ask(client_for("custom", transport), model="m")
    assert caught.value.status_code == 400
    expected = "deep" if depth == 5 else "Bad Request"
    assert str(caught.value) == f"Custom API error 400: {expected}"
    gemini = FakeTransport((400, nested_error(depth)))
    with pytest.raises(GeminiError) as caught_gemini:
        ask(GeminiClient("AIza-x", transport=gemini))
    assert caught_gemini.value.status_code == 400
    assert str(caught_gemini.value) == f"Gemini error 400: {expected}"


def test_zai_input_filter_means_cannot_answer():
    blocked = {
        "error": {
            "code": "1301",
            "message": "System detected potentially unsafe or sensitive content in input.",
        }
    }
    transport = FakeTransport((400, blocked))
    result = ask(client_for("zai", transport))
    assert result["answer"] == "" and result["canAnswer"] is False
    assert result["confidence"] == 0.0 and result["model"] == "glm-4.7"
    assert len(transport.calls) == 1
    numeric = FakeTransport((400, {"error": {"code": 1301, "message": "unsafe"}}))
    assert ask(client_for("zai", numeric))["canAnswer"] is False


def test_the_1301_code_is_an_error_for_other_providers():
    transport = FakeTransport((400, {"error": {"code": "1301", "message": "unsafe"}}))
    with pytest.raises(ProviderError) as caught:
        ask(client_for("openai", transport))
    assert caught.value.status_code == 400
    assert len(transport.calls) == 1
    # And for Z.ai only an HTTP 400 counts.
    server_side = FakeTransport((500, {"error": {"code": "1301", "message": "unsafe"}}))
    with pytest.raises(ProviderError):
        ask(client_for("zai", server_side))


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        b"[1, 2]",
        {"choices": []},
        {"choices": [{"message": "text"}]},
        {"object": "chat.completion"},
    ],
)
def test_unusable_answers_become_provider_errors(body):
    transport = FakeTransport((200, body))
    with pytest.raises(ProviderError):
        ask(client_for("openai", transport))


def test_odd_usage_numbers_do_not_break_the_answer():
    transport = FakeTransport(
        (
            200,
            completion(
                ANSWER_TEXT, usage={"prompt_tokens": "lots", "completion_tokens": None}
            ),
        )
    )
    result = ask(client_for("openai", transport))
    assert result["promptEvalCount"] == 0 and result["evalCount"] == 0


# --- answer parsing -----------------------------------------------------------


def test_mistral_chunk_list_uses_only_text_chunks():
    content = [
        {
            "type": "thinking",
            "thinking": [{"type": "text", "text": '{"answer": "no", "canAnswer": true}'}],
        },
        {"type": "text", "text": ANSWER_TEXT},
    ]
    transport = FakeTransport((200, completion(content)))
    assert ask(client_for("mistral", transport))["answer"] == ANSWER["answer"]


def test_kimi_fenced_json_is_read():
    transport = FakeTransport((200, completion(f"```json\n{ANSWER_TEXT}\n```")))
    assert ask(client_for("moonshot", transport))["answer"] == ANSWER["answer"]


def test_json_inside_text_is_read():
    transport = FakeTransport(
        (200, completion(f"Here you go: {ANSWER_TEXT} Hope it helps {{x}}"))
    )
    assert ask(client_for("custom", transport), model="m")["canAnswer"] is True


def test_deepseek_blank_answer_is_asked_again_once():
    transport = FakeTransport((200, completion("  \n ")), (200, completion(ANSWER_TEXT)))
    assert ask(client_for("deepseek", transport))["canAnswer"] is True
    assert len(transport.calls) == 2
    blank = FakeTransport((200, completion("   ")))
    with pytest.raises(ProviderError) as caught:
        ask(client_for("deepseek", blank))
    assert len(blank.calls) == 2
    assert "empty answer" in str(caught.value)


def test_other_providers_do_not_retry_a_blank_answer():
    transport = FakeTransport((200, completion("")))
    with pytest.raises(ProviderError):
        ask(client_for("openai", transport))
    assert len(transport.calls) == 1


def test_deepseek_blank_answer_cut_off_by_the_cap_is_not_asked_again():
    # A blank answer that hit the token cap would hit it again.
    transport = FakeTransport((200, completion("", finish="length")))
    with pytest.raises(ProviderError) as caught:
        ask(client_for("deepseek", transport))
    assert len(transport.calls) == 1
    assert "stopped before finishing" in str(caught.value)


def test_refusals_and_filtered_answers_mean_cannot_answer():
    refused = FakeTransport((200, completion(None, refusal="I can't help with that.")))
    result = ask(client_for("openai", refused))
    assert (
        result["answer"] == "" and result["canAnswer"] is False and result["confidence"] == 0.0
    )
    filtered = FakeTransport((200, completion("", finish="sensitive")))
    assert ask(client_for("zai", filtered))["canAnswer"] is False


def test_truncated_answer_says_it_stopped_early():
    transport = FakeTransport((200, completion('{"answer": "ร้านเปิด', finish="length")))
    with pytest.raises(ProviderError) as caught:
        ask(client_for("openai", transport))
    assert "stopped before finishing" in str(caught.value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        ("true", True),
        ("TRUE", True),
        (" yes ", True),
        ("1", True),
        (False, False),
        ("false", False),
        ("no", False),
        ("0", False),
        (1, False),
        (None, False),
        ([], False),
    ],
)
def test_can_answer_is_read_strictly(value, expected):
    result = assistant._structured_answer(
        {"answer": "ok", "canAnswer": value, "confidence": 0.5},
        model="m",
        prompt_tokens=0,
        output_tokens=0,
    )
    assert result["canAnswer"] is expected


def test_can_answer_needs_an_answer_and_confidence_is_clamped():
    def build(payload):
        return assistant._structured_answer(
            payload, model="m", prompt_tokens=0, output_tokens=0
        )

    assert build({"answer": "  ", "canAnswer": True})["canAnswer"] is False
    assert build({"answer": "ok", "canAnswer": True, "confidence": 7})["confidence"] == 1.0
    assert build({"answer": "ok", "canAnswer": True, "confidence": -2})["confidence"] == 0.0
    assert (
        build({"answer": "ok", "canAnswer": True, "confidence": "0.65"})["confidence"] == 0.65
    )
    assert (
        build({"answer": "ok", "canAnswer": True, "confidence": "high"})["confidence"] == 0.0
    )
    assert (
        build({"answer": "ok", "canAnswer": True, "confidence": float("nan")})["confidence"]
        == 0.0
    )
    assert build({"answer": {"x": 1}, "canAnswer": True})["answer"] == ""


def test_string_false_from_a_provider_is_not_an_answer():
    payload = json.dumps({"answer": "ไม่ทราบ", "canAnswer": "false", "confidence": 0.9})
    transport = FakeTransport((200, completion(payload)))
    assert ask(client_for("deepseek", transport))["canAnswer"] is False


@pytest.mark.parametrize(
    "text",
    [
        ANSWER_TEXT,
        f"```json\n{ANSWER_TEXT}\n```",
        f"```\n{ANSWER_TEXT}\n```",
        f"Answer:\n```JSON\n{ANSWER_TEXT}\n```\nThanks",
        f"﻿{ANSWER_TEXT}",
        f"The shop says {ANSWER_TEXT}.",
        f"Use {{name}} braces. Then {ANSWER_TEXT}",
        f"```json\n{ANSWER_TEXT}",
    ],
)
def test_parse_json_object_finds_the_object(text):
    assert assistant._parse_json_object(text) == ANSWER


def test_parse_json_object_keeps_braces_inside_strings():
    text = 'ok {"answer": "use {curly} and } brackets", "canAnswer": true, "confidence": 1}'
    assert assistant._parse_json_object(text)["answer"] == "use {curly} and } brackets"


@pytest.mark.parametrize(
    "text", ["", "   ", "no json here", "[1, 2, 3]", "{broken", "{" * 5000]
)
def test_parse_json_object_raises_the_neutral_error(text):
    with pytest.raises(AssistantError) as caught:
        assistant._parse_json_object(text)
    assert "Ollama" not in str(caught.value)
    assert str(caught.value) == "The AI did not return a structured answer."


def test_error_classes():
    assert issubclass(GeminiError, AssistantError)
    assert issubclass(ProviderError, AssistantError)
    assert issubclass(AssistantError, RuntimeError)
    error = ProviderError("x", 418, "ai_teapot")
    assert (error.status_code, error.code, str(error)) == (418, "ai_teapot", "x")
    assert AssistantError("y").status_code is None and AssistantError("y").code is None


def test_prompt_keeps_the_word_json():
    prompt = assistant._assistant_prompt("q", "", strict_knowledge=False)
    assert "JSON" in prompt and "canAnswer" in prompt and "confidence" in prompt
    assert "(no matching knowledge)" in prompt


# --- model lists --------------------------------------------------------------


def test_openai_model_list_is_classified_by_id():
    models = [
        {"id": "gpt-6-luna", "object": "model", "shutdown_date": None},
        {"id": "gpt-6.2-sol", "shutdown_date": "2099-01-01"},
        {"id": "gpt-5-mini", "shutdown_date": "2026-01-01"},
        {"id": "text-embedding-3-small"},
        {"id": "dall-e-3"},
        {"id": "gpt-image-2"},
        {"id": "sora-2"},
        {"id": "tts-1-hd"},
        {"id": "gpt-realtime-2.1"},
        {"id": "gpt-transcribe"},
        {"id": "whisper-1"},
        {"id": "omni-moderation-latest"},
        {"id": "gpt-4o-search-preview"},
        {"id": "computer-use-preview"},
        {"id": "gpt-5.5-pro"},
        {"id": "babbage-002"},
        {"id": "gpt-6-luna"},
    ]
    transport = FakeTransport((200, {"object": "list", "data": models}))
    listed = client_for("openai", transport).list_models()
    (call,) = transport.calls
    assert call["method"] == "GET" and call["url"] == "https://api.openai.com/v1/models"
    assert call["body"] is None and call["max_bytes"] == MAX_AI_MODEL_LIST_BYTES
    assert call["timeout"] >= 120
    kinds = {item["name"]: item["kind"] for item in listed}
    assert kinds.pop("gpt-6-luna") == "chat"
    assert kinds.pop("gpt-6.2-sol") == "chat"
    assert kinds.pop("text-embedding-3-small") == "embedding"
    assert "gpt-5-mini" not in kinds  # shut down
    assert set(kinds.values()) == {"other"}
    assert all(set(item) >= {"name", "displayName", "kind"} for item in listed)
    assert [item["name"] for item in listed].count("gpt-6-luna") == 1


def test_openai_model_list_reads_a_numeric_shutdown_date():
    models = [
        {"id": "gpt-retired", "shutdown_date": 1700000000},  # 2023-11-14
        {"id": "gpt-later", "shutdown_date": 4102444800},  # 2100-01-01
        {"id": "gpt-odd", "shutdown_date": 10**20},  # unreadable: kept
    ]
    transport = FakeTransport((200, {"data": models}))
    kinds = {
        item["name"]: item["kind"] for item in client_for("openai", transport).list_models()
    }
    assert kinds == {"gpt-later": "chat", "gpt-odd": "chat"}


@pytest.mark.parametrize(
    ("model_id", "kind"),
    [
        ("gpt-6-cyber", "other"),
        ("gpt-daybreak-1", "other"),
        ("gpt-rosalind", "other"),
        ("gpt-rosalind-research", "other"),
        ("gpt-6-luna", "chat"),
        ("gpt-6.2-sol", "chat"),
        ("gpt-6-mini", "chat"),
    ],
)
def test_openai_gated_models_are_not_offered_for_chat(model_id, kind):
    provider = get_provider("openai")
    today = datetime.date.today()
    assert ai_providers._classify_model(provider, {"id": model_id}, model_id, today) == kind


def test_model_lists_take_the_deadline_they_are_given():
    openai = FakeTransport((200, {"data": [{"id": "gpt-6-luna"}]}))
    client_for("openai", openai).list_models(timeout=30)
    client_for("openai", openai).list_models()
    client_for("openai", openai).list_models(timeout=0)  # at least a second
    assert [call["timeout"] for call in openai.calls] == [30, 120, 1.0]
    custom = FakeTransport((200, {"data": [{"id": "llama"}]}))
    client_for("custom", custom).list_models(timeout=30)
    assert custom.calls[0]["timeout"] == 30
    gemini = FakeTransport((200, {"models": []}))
    GeminiClient("AIza-x", transport=gemini).list_models(timeout=30)
    GeminiClient("AIza-x", transport=gemini).list_models()
    assert [call["timeout"] for call in gemini.calls] == [30, 120]
    # A built-in list needs no request, deadline or not.
    static = FakeTransport((500, {}))
    assert client_for("zai", static).list_models(timeout=30)
    assert static.calls == []


def test_embeddings_take_the_deadline_they_are_given():
    openai = FakeTransport((200, {"data": [{"index": 0, "embedding": [0.1, 0.2]}]}))
    client_for("openai", openai).embed("", ["a"], timeout=15)
    client_for("openai", openai).embed("", ["a"])
    assert [call["timeout"] for call in openai.calls] == [15, 120]
    gemini = FakeTransport((200, {"embeddings": [{"values": [0.1, 0.2]}]}))
    GeminiClient("AIza-x", transport=gemini).embed("", ["a"], timeout=15)
    GeminiClient("AIza-x", transport=gemini).embed("", ["a"])
    assert [call["timeout"] for call in gemini.calls] == [15, 120]


def test_mistral_model_list_uses_capabilities():
    data = {
        "object": "list",
        "data": [
            {"id": "mistral-small-latest", "capabilities": {"completion_chat": True}},
            {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
            {"id": "mistral-ocr-latest", "capabilities": {"completion_chat": False}},
            {"id": "codestral-latest", "capabilities": {"completion_chat": True}},
        ],
    }
    transport = FakeTransport((200, data))
    kinds = {
        item["name"]: item["kind"] for item in client_for("mistral", transport).list_models()
    }
    assert kinds == {
        "mistral-small-latest": "chat",
        "mistral-embed": "embedding",
        "mistral-ocr-latest": "other",
        "codestral-latest": "chat",
    }


def test_openrouter_lists_the_models_this_key_can_use():
    data = {
        "data": [
            {
                "id": "openai/gpt-6-luna",
                "name": "OpenAI: GPT-6 Luna",
                "architecture": {"output_modalities": ["text"]},
            },
            {
                "id": "google/some-image-model",
                "architecture": {"output_modalities": ["image"]},
            },
            {"id": "openai/text-embedding-3-small"},
        ]
    }
    transport = FakeTransport((200, data))
    listed = client_for("openrouter", transport).list_models()
    assert transport.calls[0]["url"] == "https://openrouter.ai/api/v1/models/user"
    assert transport.calls[0]["headers"]["Authorization"] == f"Bearer {KEY}"
    assert listed == [
        {"name": "openai/gpt-6-luna", "displayName": "OpenAI: GPT-6 Luna", "kind": "chat"},
        {
            "name": "google/some-image-model",
            "displayName": "google/some-image-model",
            "kind": "other",
        },
        {
            "name": "openai/text-embedding-3-small",
            "displayName": "openai/text-embedding-3-small",
            "kind": "embedding",
        },
    ]


def test_static_model_lists_need_no_request():
    transport = FakeTransport((500, {}))
    zai = client_for("zai", transport, api_key="").list_models()
    assert [item["name"] for item in zai] == list(get_provider("zai").static_models)
    assert {item["kind"] for item in zai} == {"chat"}
    qwen = client_for("qwen", transport).list_models()
    assert qwen[-1] == {
        "name": "text-embedding-v4",
        "displayName": "text-embedding-v4",
        "kind": "embedding",
    }
    assert transport.calls == []


def test_custom_model_list_and_failure():
    transport = FakeTransport(
        (200, ["llama-4-scout", {"id": "bge-m3-embed"}, {"name": "qwen-local"}])
    )
    listed = client_for("custom", transport, api_key="").list_models()
    assert transport.calls[0]["url"] == "https://llm.example.com/v1/models"
    assert [(item["name"], item["kind"]) for item in listed] == [
        ("llama-4-scout", "chat"),
        ("bge-m3-embed", "embedding"),
        ("qwen-local", "chat"),
    ]
    missing = FakeTransport((404, {"detail": "Not Found"}))
    with pytest.raises(ProviderError) as caught:
        client_for("custom", missing).list_models()
    assert caught.value.status_code == 404
    odd = FakeTransport((200, {"unexpected": True}))
    with pytest.raises(ProviderError):
        client_for("custom", odd).list_models()


# --- embeddings ---------------------------------------------------------------


def embeddings_reply(count: int, *, start: int = 0, reverse: bool = False) -> dict[str, Any]:
    items = [
        {"object": "embedding", "index": index, "embedding": [float(start + index), 1.0]}
        for index in range(count)
    ]
    return {"object": "list", "data": list(reversed(items)) if reverse else items}


def test_openai_embeddings_are_batched_by_32():
    texts = [f"chunk {index}" for index in range(70)]
    transport = FakeTransport(
        (200, embeddings_reply(32, start=0)),
        (200, embeddings_reply(32, start=32)),
        (200, embeddings_reply(6, start=64)),
    )
    vectors = client_for("openai", transport).embed("", texts)
    assert [len(call["body"]["input"]) for call in transport.calls] == [32, 32, 6]
    assert transport.calls[0]["url"] == "https://api.openai.com/v1/embeddings"
    assert transport.calls[0]["body"]["model"] == "text-embedding-3-small"
    assert transport.calls[2]["body"]["input"][0] == "chunk 64"
    assert all(call["max_bytes"] == MAX_AI_EMBEDDING_BYTES for call in transport.calls)
    assert all(call["timeout"] >= 120 for call in transport.calls)
    assert [vector[0] for vector in vectors] == [float(index) for index in range(70)]


def test_qwen_embeddings_are_batched_by_10_and_reordered_by_index():
    transport = FakeTransport(
        (200, embeddings_reply(10, reverse=True)),
        (200, embeddings_reply(10, start=10, reverse=True)),
        (200, embeddings_reply(5, start=20, reverse=True)),
    )
    vectors = client_for("qwen", transport).embed(
        "text-embedding-v4", [str(i) for i in range(25)]
    )
    assert [len(call["body"]["input"]) for call in transport.calls] == [10, 10, 5]
    assert [vector[0] for vector in vectors] == [float(index) for index in range(25)]


@pytest.mark.parametrize(
    "reply",
    [
        {"data": [{"embedding": [0.1]}]},
        {"data": [{"embedding": [0.1]}, {"embedding": []}]},
        {"data": [{"embedding": [0.1]}, {"embedding": ["0.2"]}]},
        {"data": [{"embedding": [0.1]}, {"embedding": [float("nan")]}]},
        {"data": "nope"},
    ],
)
def test_bad_embeddings_become_provider_errors(reply):
    transport = FakeTransport((200, reply))
    with pytest.raises(ProviderError):
        client_for("mistral", transport).embed("mistral-embed", ["a", "b"])


def test_providers_without_embeddings_refuse_before_any_request():
    transport = FakeTransport((200, embeddings_reply(1)))
    with pytest.raises(ProviderError) as caught:
        client_for("deepseek", transport).embed("x", ["a"])
    assert caught.value.code == "ai_no_embeddings"
    with pytest.raises(ProviderError) as caught:
        client_for("custom", transport).embed("", ["a"])
    assert caught.value.code == "ai_no_embeddings"
    assert transport.calls == []
    assert client_for("openai", transport).embed("", []) == []


def test_search_vectors_longer_than_the_cap_are_refused():
    assert MAX_EMBEDDING_DIMENSIONS == 8192
    too_long = [0.25] * (MAX_EMBEDDING_DIMENSIONS + 1)
    custom = FakeTransport((200, {"data": [{"index": 0, "embedding": too_long}]}))
    with pytest.raises(ProviderError, match="8193 numbers"):
        client_for("custom", custom, api_key="").embed("bge-m3", ["a"])
    gemini = FakeTransport((200, {"embeddings": [{"values": too_long}]}))
    with pytest.raises(GeminiError, match="8193 numbers"):
        GeminiClient("AIza-x", transport=gemini).embed("", ["a"])
    huge = [1] * 480_000  # what a hostile server sent in the review: refused before parsing
    with pytest.raises(ProviderError, match="480000 numbers"):
        client_for("openai", FakeTransport((200, {"data": [{"embedding": huge}]}))).embed(
            "", ["a"]
        )
    assert assistant._float_vector(too_long) is None


@pytest.mark.parametrize("size", [768, 1024, 1536, 3072, MAX_EMBEDDING_DIMENSIONS])
def test_real_search_vector_sizes_pass(size):
    vector = [0.001 * (index % 7) for index in range(size)]
    custom = FakeTransport((200, {"data": [{"index": 0, "embedding": vector}]}))
    assert len(client_for("custom", custom, api_key="").embed("m", ["a"])[0]) == size
    gemini = FakeTransport((200, {"embeddings": [{"values": vector}]}))
    assert len(GeminiClient("AIza-x", transport=gemini).embed("", ["a"])[0]) == size


def test_the_embeddings_answer_cap_fits_the_largest_real_batch():
    largest_batch = max(provider.embed_batch for provider in provider_registry())
    assert largest_batch == 32
    # A full batch of the longest vectors, written the long way (17 digits each).
    vector = [-0.012345678901234567] * MAX_EMBEDDING_DIMENSIONS
    answer = {
        "object": "list",
        "data": [
            {"object": "embedding", "index": index, "embedding": vector}
            for index in range(largest_batch)
        ],
    }
    size = len(json.dumps(answer).encode())
    assert size < MAX_AI_EMBEDDING_BYTES <= 8 * 1024 * 1024


def test_custom_embeddings_use_the_typed_model():
    transport = FakeTransport((200, embeddings_reply(1)))
    vectors = client_for("custom", transport, api_key="").embed("bge-m3", ["hello"])
    assert vectors == [[0.0, 1.0]]
    assert transport.calls[0]["url"] == "https://llm.example.com/v1/embeddings"
    assert transport.calls[0]["body"] == {"model": "bge-m3", "input": ["hello"]}


# --- Gemini -------------------------------------------------------------------


def gemini_answer(text: str, usage: Any = None) -> dict[str, Any]:
    return {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": usage
        if usage is not None
        else {"promptTokenCount": 5, "candidatesTokenCount": 2},
    }


def test_gemini_chat_request_and_answer():
    assert assistant.DEFAULT_GEMINI_CHAT_MODEL == "gemini-3.8-flash"
    transport = FakeTransport((200, gemini_answer(ANSWER_TEXT)))
    client = GeminiClient("AIza-secret", transport=transport)
    result = ask(client)
    (call,) = transport.calls
    assert call["url"] == (
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent"
    )
    assert call["headers"]["x-goog-api-key"] == "AIza-secret"
    assert "AIza-secret" not in call["url"]
    config = call["body"]["generationConfig"]
    assert config["responseMimeType"] == "application/json"
    assert config["responseSchema"]["required"] == ["answer", "canAnswer", "confidence"]
    assert config["temperature"] == 0.2
    assert call["max_bytes"] == MAX_AI_RESPONSE_BYTES and call["timeout"] == 60
    assert result["answer"] == ANSWER["answer"] and result["promptEvalCount"] == 5
    assert result["model"] == "gemini-3.8-flash"


def test_gemini_embeddings_are_batched_by_16():
    def reply(count):
        return (
            200,
            {"embeddings": [{"values": [float(index), 0.5]} for index in range(count)]},
        )

    transport = FakeTransport(reply(16), reply(4))
    vectors = make_ai_client("gemini", api_key="AIza-x", transport=transport).embed(
        "", ["t"] * 20
    )
    assert len(vectors) == 20
    assert [len(call["body"]["requests"]) for call in transport.calls] == [16, 4]
    assert transport.calls[0]["url"].endswith(
        "/v1beta/models/gemini-embedding-001:batchEmbedContents"
    )
    assert all(call["max_bytes"] == MAX_AI_EMBEDDING_BYTES for call in transport.calls)


def test_gemini_errors():
    transport = FakeTransport(
        (
            400,
            {
                "error": {
                    "code": 400,
                    "message": "API key not valid. Please pass a valid API key.",
                    "status": "INVALID_ARGUMENT",
                }
            },
        )
    )
    with pytest.raises(GeminiError) as caught:
        ask(GeminiClient("AIza-bad-key", transport=transport))
    assert caught.value.status_code == 400 and caught.value.code == "ai_key_invalid"
    assert (
        str(caught.value)
        == "Gemini error 400: API key not valid. Please pass a valid API key."
    )
    with pytest.raises(GeminiError) as caught:
        ask(GeminiClient("", transport=transport))
    assert caught.value.code == "ai_key_required"
    assert len(transport.calls) == 1
    garbled = FakeTransport((200, gemini_answer("not json", usage={"promptTokenCount": "x"})))
    with pytest.raises(GeminiError, match="did not return a structured answer"):
        ask(GeminiClient("AIza-x", transport=garbled))
    broken = FakeTransport(ValueError("boom"))
    with pytest.raises(GeminiError, match="Gemini request failed"):
        GeminiClient("AIza-x", transport=broken).list_models()


def test_gemini_blocked_question_means_cannot_answer():
    blocked = {
        "promptFeedback": {"blockReason": "SAFETY", "safetyRatings": []},
        "usageMetadata": {"promptTokenCount": 7},
    }
    transport = FakeTransport((200, blocked))
    result = ask(GeminiClient("AIza-x", transport=transport))
    assert result["answer"] == "" and result["canAnswer"] is False
    assert result["promptEvalCount"] == 7 and result["model"] == "gemini-3.8-flash"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "candidate",
    [
        {"finishReason": "SAFETY", "safetyRatings": []},
        {"content": {"parts": []}, "finishReason": "PROHIBITED_CONTENT"},
        {"content": {"parts": [{"text": "  "}]}, "finishReason": "BLOCKLIST"},
        {"content": {"parts": [{"text": '{"answer": "ร้าน'}]}, "finishReason": "RECITATION"},
    ],
)
def test_gemini_blocked_answer_means_cannot_answer(candidate):
    transport = FakeTransport((200, {"candidates": [candidate]}))
    result = ask(GeminiClient("AIza-x", transport=transport))
    assert result["answer"] == "" and result["canAnswer"] is False


def test_gemini_answer_cut_off_by_the_token_limit_says_so():
    cut = {"content": {"parts": [{"text": '{"answer": "ร้าน'}]}, "finishReason": "MAX_TOKENS"}
    transport = FakeTransport((200, {"candidates": [cut]}))
    with pytest.raises(GeminiError, match=r"Gemini stopped before finishing the answer\."):
        ask(GeminiClient("AIza-x", transport=transport))
    # A full answer with any finish reason still counts.
    whole = {"content": {"parts": [{"text": ANSWER_TEXT}]}, "finishReason": "SAFETY"}
    assert ask(
        GeminiClient("AIza-x", transport=FakeTransport((200, {"candidates": [whole]})))
    )["canAnswer"]


def test_gemini_model_list_keeps_its_shape():
    data = {
        "models": [
            {
                "name": "models/gemini-3.8-flash",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/gemini-embedding-001",
                "supportedGenerationMethods": ["embedContent"],
            },
        ]
    }
    transport = FakeTransport((200, data))
    listed = GeminiClient("AIza-x", transport=transport).list_models()
    assert listed[0]["name"] == "gemini-3.8-flash" and listed[0]["chat"] is True
    assert listed[1]["embedding"] is True and "kind" not in listed[1]
    assert transport.calls[0]["max_bytes"] == MAX_AI_MODEL_LIST_BYTES


# --- default transport (local HTTP server) -------------------------------------


class _QuietServer(http.server.ThreadingHTTPServer):
    def handle_error(self, request: Any, client_address: Any) -> None:
        return  # a client that hangs up early (as the deadline tests do) is expected


class FakeServer:
    """A local HTTP server; ``handler(request)`` returns a reply or writes one itself."""

    def __init__(self, handler: Callable[[dict[str, Any]], Any]) -> None:
        self.handler = handler
        self.requests: list[dict[str, Any]] = []
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _serve(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                request = {
                    "method": self.command,
                    "path": self.path,
                    "headers": {name.lower(): value for name, value in self.headers.items()},
                    "header_names": [name.lower() for name in self.headers],
                    "body": body,
                }
                outer.requests.append(request)
                reply = outer.handler(request)
                try:
                    if callable(reply):
                        reply(self)
                        return
                    status, headers, payload = reply
                    self.send_response(status)
                    for name, value in headers.items():
                        self.send_header(name, value)
                    if "Content-Length" not in headers:
                        self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                except OSError:
                    self.close_connection = True  # the client gave up first

            do_GET = _serve
            do_POST = _serve

            def log_message(self, format: str, *args: Any) -> None:
                return

        self.httpd = _QuietServer(("127.0.0.1", 0), Handler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self.thread.start()

    @property
    def url(self) -> str:
        host, port = self.httpd.server_address[:2]
        return f"http://{host}:{port}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


def _no_proxies(monkeypatch) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "REQUESTS_CA_BUNDLE",
        "CURL_CA_BUNDLE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


class RawServer:
    """A bare TCP server on 127.0.0.1; ``handler(conn)`` runs once per connection."""

    def __init__(self, handler: Callable[[socket.socket], None]) -> None:
        self.handler = handler
        self.connections = 0
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self._stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def port(self) -> int:
        return int(self.listener.getsockname()[1])

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        try:
            conn.settimeout(15)
            self.handler(conn)
        except OSError:
            pass  # the client gave up first, as the deadline tests make it
        finally:
            conn.close()

    def close(self) -> None:
        self._stop.set()
        self.thread.join(2)
        self.listener.close()


@pytest.fixture
def raw_server(monkeypatch):
    _no_proxies(monkeypatch)
    servers: list[RawServer] = []

    def start(handler: Callable[[socket.socket], None]) -> RawServer:
        server = RawServer(handler)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def read_request_head(conn: Any) -> bytes:
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            break
        data += chunk
    return data


TRICKLED_HEAD = (
    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Pad: "
    + b"a" * 60
    + b"\r\nContent-Length: 2\r\n\r\n{}"
)
"""About 11 seconds of status line and headers at one byte every 0.1 s."""


def trickle_head(conn: Any) -> None:
    read_request_head(conn)
    for byte in TRICKLED_HEAD:
        conn.sendall(bytes([byte]))
        time.sleep(0.1)


def stay_silent(conn: Any) -> None:
    """Read the request, never answer, and wait until the client hangs up."""
    while conn.recv(65536):
        pass


@pytest.fixture
def fake_server(monkeypatch):
    _no_proxies(monkeypatch)
    servers: list[FakeServer] = []

    def start(handler: Callable[[dict[str, Any]], Any]) -> FakeServer:
        server = FakeServer(handler)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def json_reply(status: int, payload: Any, headers: dict[str, str] | None = None):
    return (
        status,
        {"Content-Type": "application/json", **(headers or {})},
        json.dumps(payload).encode(),
    )


def test_default_transport_sends_json_and_returns_the_answer(fake_server):
    server = fake_server(lambda request: json_reply(200, {"ok": True}))
    status, headers, body = default_transport(
        "POST",
        f"{server.url}/v1/chat/completions",
        headers={"Authorization": "Bearer abc"},
        json_body={"question": "ร้านเปิดกี่โมง"},
        timeout=5,
        max_bytes=1000,
    )
    assert status == 200 and json.loads(body) == {"ok": True}
    assert headers["Content-Type"] == "application/json"
    (request,) = server.requests
    assert request["headers"]["authorization"] == "Bearer abc"
    assert request["headers"]["content-type"] == "application/json"
    assert request["headers"]["accept-encoding"] == "identity"
    assert "ร้านเปิดกี่โมง".encode() in request["body"]  # UTF-8, not \u escapes
    assert json.loads(request["body"]) == {"question": "ร้านเปิดกี่โมง"}


def test_default_transport_refuses_redirects(fake_server):
    def handler(request):
        if request["path"] == "/start":
            return 302, {"Location": "/elsewhere"}, b""
        return json_reply(200, {"followed": True})

    server = fake_server(handler)
    with pytest.raises(AssistantError) as caught:
        default_transport(
            "POST",
            f"{server.url}/start",
            headers={},
            json_body={"a": 1},
            timeout=5,
            max_bytes=1000,
        )
    assert caught.value.status_code == 302
    assert "redirect" in str(caught.value)
    assert [request["path"] for request in server.requests] == ["/start"]


def test_default_transport_caps_a_declared_large_body(fake_server):
    server = fake_server(
        lambda request: (200, {"Content-Type": "application/json"}, b"x" * 5000)
    )
    with pytest.raises(AssistantError, match="larger than 1000 bytes"):
        default_transport(
            "GET", server.url, headers={}, json_body=None, timeout=5, max_bytes=1000
        )


def test_default_transport_caps_a_chunked_body(fake_server):
    def chunked(writer):
        writer.send_response(200)
        writer.send_header("Content-Type", "application/json")
        writer.send_header("Transfer-Encoding", "chunked")
        writer.end_headers()
        try:
            for _ in range(20):
                piece = b"y" * 700
                writer.wfile.write(b"%x\r\n%s\r\n" % (len(piece), piece))
            writer.wfile.write(b"0\r\n\r\n")
        except OSError:
            pass

    server = fake_server(lambda request: chunked)
    with pytest.raises(AssistantError, match="larger than"):
        default_transport(
            "GET", server.url, headers={}, json_body=None, timeout=5, max_bytes=2048
        )


def test_default_transport_enforces_a_total_deadline(fake_server):
    def trickle(writer):
        writer.send_response(200)
        writer.send_header("Content-Type", "application/json")
        writer.send_header("Transfer-Encoding", "chunked")
        writer.end_headers()
        try:
            for _ in range(100):
                writer.wfile.write(b"1\r\n \r\n")
                writer.wfile.flush()
                time.sleep(0.1)
        except OSError:
            pass

    server = fake_server(lambda request: trickle)
    started = time.monotonic()
    with pytest.raises(AssistantError, match="did not answer in time"):
        default_transport(
            "GET", server.url, headers={}, json_body=None, timeout=1, max_bytes=10_000
        )
    assert time.monotonic() - started < 4


def test_default_transport_does_not_send_a_post_twice_after_a_read_timeout(fake_server):
    def slow(request):
        time.sleep(2.5)
        return json_reply(200, {"late": True})

    server = fake_server(slow)
    with pytest.raises(AssistantError, match="did not answer in time"):
        default_transport(
            "POST", server.url, headers={}, json_body={"q": 1}, timeout=1, max_bytes=1000
        )
    time.sleep(0.3)
    assert len(server.requests) == 1


def test_default_transport_ignores_netrc(fake_server, tmp_path, monkeypatch):
    netrc = tmp_path / "netrc"
    netrc.write_text("machine 127.0.0.1 login someone password hunter2\n")
    monkeypatch.setenv("NETRC", str(netrc))
    server = fake_server(lambda request: json_reply(200, {}))
    requests.get(f"{server.url}/probe", timeout=5)
    assert server.requests[0]["headers"].get("authorization", "").startswith("Basic ")
    default_transport("GET", server.url, headers={}, json_body=None, timeout=5, max_bytes=100)
    assert "authorization" not in server.requests[1]["headers"]
    default_transport(
        "GET",
        server.url,
        headers={"Authorization": "Bearer mine"},
        json_body=None,
        timeout=5,
        max_bytes=100,
    )
    assert server.requests[2]["headers"]["authorization"] == "Bearer mine"


def test_default_transport_connection_failure():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    # Windows can take a few seconds to refuse a closed local port.
    with pytest.raises(AssistantError, match=r"cannot connect|did not answer in time"):
        default_transport(
            "GET",
            f"http://127.0.0.1:{port}/",
            headers={},
            json_body=None,
            timeout=10,
            max_bytes=100,
        )


def test_default_transport_keeps_its_deadline_while_headers_trickle_in(raw_server):
    server = raw_server(trickle_head)
    started = time.monotonic()
    with pytest.raises(AssistantError, match="did not answer in time"):
        default_transport(
            "POST",
            f"http://127.0.0.1:{server.port}/v1/chat/completions",
            headers={},
            json_body={"q": 1},
            timeout=1,
            max_bytes=1000,
        )
    assert time.monotonic() - started < 3
    time.sleep(0.3)
    assert server.connections == 1  # never sent again


def test_default_transport_keeps_its_deadline_inside_a_compressed_body(raw_server):
    # A provider that ignores "Accept-Encoding: identity" and trickles a gzip header
    # whose comment never ends: the decoder has nothing to hand back, so a single
    # read of the body would otherwise keep waiting for more.
    def trickle_gzip(conn):
        read_request_head(conn)
        conn.sendall(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Encoding: gzip\r\nConnection: close\r\n\r\n"
            b"\x1f\x8b\x08\x10\x00\x00\x00\x00\x00\xff"  # FLG.FCOMMENT set
        )
        for _ in range(200):
            conn.sendall(b"a")
            time.sleep(0.05)

    server = raw_server(trickle_gzip)
    started = time.monotonic()
    with pytest.raises(AssistantError, match="did not answer in time"):
        default_transport(
            "GET",
            f"http://127.0.0.1:{server.port}/v1/models",
            headers={},
            json_body=None,
            timeout=1,
            max_bytes=10_000,
        )
    assert time.monotonic() - started < 3


def test_default_transport_gives_up_on_a_silent_server_at_the_deadline(raw_server):
    server = raw_server(stay_silent)
    started = time.monotonic()
    with pytest.raises(AssistantError, match="did not answer in time"):
        default_transport(
            "POST",
            f"http://127.0.0.1:{server.port}/",
            headers={},
            json_body={"q": 1},
            timeout=1,
            max_bytes=100,
        )
    assert time.monotonic() - started < 3
    time.sleep(0.3)
    assert server.connections == 1


# --- deadline helpers ---------------------------------------------------------


def test_the_watchdog_ends_a_trickling_read_at_the_deadline():
    left, right = socket.socketpair()
    stop = threading.Event()

    def trickle():
        try:
            while not stop.is_set():
                right.sendall(b"x")
                time.sleep(0.1)
        except OSError:
            pass  # the watchdog's shutdown reset the connection

    sender = threading.Thread(target=trickle, daemon=True)
    try:
        left.settimeout(10)  # each byte comes well inside this
        watchdog = DeadlineWatchdog(time.monotonic() + 0.5)
        watchdog.watch(left)
        sender.start()
        started = time.monotonic()
        with pytest.raises(AssistantError, match="did not answer in time"), watchdog:
            while left.recv(10):
                pass
        assert time.monotonic() - started < 2.5
        assert watchdog.fired
    finally:
        stop.set()
        left.close()
        right.close()
        sender.join(2)


def test_the_watchdog_shuts_a_late_socket_at_once():
    left, right = socket.socketpair()
    try:
        watchdog = DeadlineWatchdog(time.monotonic() - 1)
        with pytest.raises(AssistantError, match="did not answer in time"), watchdog:
            for _ in range(200):
                if watchdog.fired:
                    break
                time.sleep(0.01)
            watchdog.watch(left)
        assert watchdog.fired
        with pytest.raises(OSError):
            left.sendall(b"x")  # shut down for writing too
    finally:
        left.close()
        right.close()


def test_a_watched_connection_waits_for_the_answer_only_until_the_deadline(raw_server):
    def ignore(conn):
        read_request_head(conn)
        time.sleep(6)  # never answers, and does not notice the client leave

    server = raw_server(ignore)
    # Not armed: only the capped socket timeout is at work here, which is what
    # ends this wait on Windows, where a shutdown does not wake an idle read.
    watchdog = DeadlineWatchdog(time.monotonic() + 1.2)
    conn = WatchedHTTPConnection("127.0.0.1", server.port, okline_watchdog=watchdog)
    started = time.monotonic()
    try:
        conn.connect()
        conn.timeout = 30  # the read timeout a pool sets before it waits for the answer
        conn.request("GET", "/")
        with pytest.raises(OSError):  # socket.timeout
            conn.getresponse()
    finally:
        conn.close()
    assert time.monotonic() - started < 3


def test_the_watchdog_leaves_everything_alone_before_the_deadline():
    left, right = socket.socketpair()
    try:
        watchdog = DeadlineWatchdog(time.monotonic() + 0.3)
        with watchdog:
            watchdog.watch(left)
        time.sleep(0.5)  # past the deadline, but the watchdog was disarmed
        assert not watchdog.fired
        right.sendall(b"ok")
        left.settimeout(2)
        assert left.recv(10) == b"ok"
        with pytest.raises(ValueError, match="mine"), DeadlineWatchdog(time.monotonic() + 30):
            raise ValueError("mine")
    finally:
        left.close()
        right.close()


def test_the_watchdog_turns_any_error_after_it_fired_into_a_timeout():
    watchdog = DeadlineWatchdog(time.monotonic() + 0.1)
    with pytest.raises(AssistantError) as caught, watchdog:
        time.sleep(0.4)
        raise AssistantError("the connection broke while reading the answer", 200)
    assert str(caught.value) == "the provider did not answer in time"
    assert caught.value.status_code == 200
    assert isinstance(caught.value.__cause__, AssistantError)


def test_watch_pool_makes_new_connections_report_to_the_watchdog():
    watchdog = DeadlineWatchdog(time.monotonic() + 30)
    pool = urllib3.HTTPSConnectionPool(
        "93.184.216.34",
        443,
        server_hostname="api.example.com",
        assert_hostname="api.example.com",
    )
    assert watchdog.watch_pool(pool) is pool
    assert pool.ConnectionCls is WatchedHTTPSConnection
    assert pool.conn_kw["okline_watchdog"] is watchdog
    assert pool.conn_kw["server_hostname"] == "api.example.com"  # the pool keeps its settings
    plain = watchdog.watch_pool(urllib3.HTTPConnectionPool("127.0.0.1", 80))
    assert plain.ConnectionCls is WatchedHTTPConnection
    assert issubclass(WatchedHTTPSConnection, urllib3.connection.HTTPSConnection)


def test_watched_classes_are_made_once_per_connection_class():
    pytest.importorskip("socks")
    from urllib3.contrib.socks import SOCKSConnection, SOCKSHTTPSConnection

    for base in (SOCKSConnection, SOCKSHTTPSConnection):
        watched = assistant._watched_connection_class(base)
        assert issubclass(watched, base) and issubclass(watched, WatchedHTTPConnection)
        assert assistant._watched_connection_class(base) is watched
        assert assistant._watched_connection_class(watched) is watched


def test_a_watched_connection_reports_its_socket_and_caps_its_timeouts(fake_server):
    server = fake_server(lambda request: json_reply(200, {"ok": True}))
    port = int(server.url.rsplit(":", 1)[1])
    watchdog = DeadlineWatchdog(time.monotonic() + 2)
    conn = WatchedHTTPConnection("127.0.0.1", port, timeout=30, okline_watchdog=watchdog)
    try:
        conn.connect()
        assert conn.timeout is not None and conn.timeout <= 2
        assert 0 < (conn.sock.gettimeout() or 0) <= 2
        assert watchdog._sockets == [conn.sock]
    finally:
        conn.close()
        watchdog.cancel()


def test_a_watched_connection_does_not_connect_after_the_deadline(fake_server):
    server = fake_server(lambda request: json_reply(200, {"ok": True}))
    port = int(server.url.rsplit(":", 1)[1])
    conn = WatchedHTTPConnection(
        "127.0.0.1", port, okline_watchdog=DeadlineWatchdog(time.monotonic() - 1)
    )
    with pytest.raises(urllib3.exceptions.ConnectTimeoutError):
        conn.connect()
    assert conn.sock is None and server.requests == []
    unwatched = WatchedHTTPConnection("127.0.0.1", port, timeout=5)
    try:
        unwatched.request("GET", "/plain")
        assert unwatched.getresponse().status == 200
    finally:
        unwatched.close()
    assert [request["path"] for request in server.requests] == ["/plain"]


def test_the_deadline_adapter_keeps_proxies_and_makes_no_retries(raw_server):
    watchdog = DeadlineWatchdog(time.monotonic() + 30)
    adapter = DeadlineAdapter(watchdog)
    assert adapter.max_retries.total == 0
    server = raw_server(stay_silent)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    session = requests.sessions.Session()
    session.mount("http://", adapter)
    try:
        with pytest.raises(requests.ConnectionError), watchdog:
            session.get(
                f"http://127.0.0.1:{server.port}/",
                proxies={"http": f"http://127.0.0.1:{closed_port}"},
                timeout=(3, 3),
            )
    finally:
        session.close()
    assert server.connections == 0  # the request went to the (missing) proxy


# --- TLS (a throwaway test CA, 127.0.0.1 only) ---------------------------------


@pytest.fixture
def tls_files(tmp_path: Path) -> dict[str, str]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = datetime.datetime.now(datetime.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "OkLine test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False
        )
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(
            x509.SubjectAlternativeName(
                [x509.IPAddress(ipaddress.ip_address("127.0.0.1")), x509.DNSName("localhost")]
            ),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    paths = {
        "ca": tmp_path / "ca.pem",
        "cert": tmp_path / "cert.pem",
        "key": tmp_path / "key.pem",
    }
    paths["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    paths["cert"].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    paths["key"].write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    return {name: str(path) for name, path in paths.items()}


def tls_handler(files: dict[str, str], serve: Callable[[Any], None]):
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(files["cert"], files["key"])

    def handler(conn: socket.socket) -> None:
        with context.wrap_socket(conn, server_side=True) as tls:
            serve(tls)

    return handler


def answer_json(conn: Any) -> None:
    read_request_head(conn)
    conn.sendall(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
        b'Content-Length: 11\r\nConnection: close\r\n\r\n{"ok":true}'
    )


def test_default_transport_over_tls(raw_server, tls_files, monkeypatch):
    server = raw_server(tls_handler(tls_files, answer_json))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", tls_files["ca"])
    status, _headers, body = default_transport(
        "GET",
        f"https://127.0.0.1:{server.port}/v1/models",
        headers={},
        json_body=None,
        timeout=5,
        max_bytes=1000,
    )
    assert status == 200 and json.loads(body) == {"ok": True}


def test_default_transport_over_tls_keeps_its_deadline_while_headers_trickle_in(
    raw_server, tls_files, monkeypatch
):
    server = raw_server(tls_handler(tls_files, trickle_head))  # one TLS record per byte
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", tls_files["ca"])
    started = time.monotonic()
    with pytest.raises(AssistantError, match="did not answer in time"):
        default_transport(
            "POST",
            f"https://127.0.0.1:{server.port}/v1/chat/completions",
            headers={},
            json_body={"q": 1},
            timeout=1.5,
            max_bytes=1000,
        )
    assert time.monotonic() - started < 3.5
    time.sleep(0.3)
    assert server.connections == 1


def test_default_transport_still_checks_the_tls_certificate(raw_server, tls_files):
    server = raw_server(tls_handler(tls_files, answer_json))
    # No REQUESTS_CA_BUNDLE: the test CA is not trusted.
    with pytest.raises(AssistantError, match="cannot connect"):
        default_transport(
            "GET",
            f"https://127.0.0.1:{server.port}/",
            headers={},
            json_body=None,
            timeout=5,
            max_bytes=1000,
        )


def test_openai_client_over_the_real_transport(fake_server):
    server = fake_server(lambda request: json_reply(200, completion(ANSWER_TEXT)))
    provider = get_provider("custom")
    client = OpenAICompatibleClient(provider, api_key="local-key", base_url=f"{server.url}/v1")
    assert ask(client, model="local")["answer"] == ANSWER["answer"]
    (request,) = server.requests
    assert request["path"] == "/v1/chat/completions"
    assert json.loads(request["body"])["response_format"] == OBJECT_FORMAT


# --- Claude (official SDK against a local server) -----------------------------


@pytest.fixture
def sdk():
    return pytest.importorskip("anthropic", minversion="2.12")


def claude_message(
    blocks: list[dict[str, Any]],
    *,
    stop_reason: str = "end_turn",
    model: str = "claude-opus-5-5",
):
    return {
        "id": "msg_01",
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks,
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 21, "output_tokens": 9},
    }


def claude_error(status: int, error_type: str, message: str):
    return json_reply(
        status,
        {
            "type": "error",
            "error": {"type": error_type, "message": message},
            "request_id": "req_1",
        },
        {"x-should-retry": "false"},
    )


THINKING = {"type": "thinking", "thinking": "", "signature": "c2ln"}
TEXT_ANSWER = {"type": "text", "text": ANSWER_TEXT}


def claude_server(fake_server, *replies: Any) -> FakeServer:
    queue = list(replies)

    def handler(request):
        return queue.pop(0) if len(queue) > 1 else queue[0]

    return fake_server(handler)


def sent_body(request: dict[str, Any]) -> dict[str, Any]:
    return json.loads(request["body"])


def test_claude_request_body_for_a_fallback_model(sdk, fake_server):
    server = claude_server(
        fake_server, json_reply(200, claude_message([THINKING, TEXT_ANSWER]))
    )
    client = ClaudeClient("sk-ant-tenant", base_url=server.url)
    result = ask(client, model="claude-opus-5-5", temperature=0.7)
    assert result == {
        "answer": ANSWER["answer"],
        "canAnswer": True,
        "confidence": 0.8,
        "model": "claude-opus-5-5",
        "promptEvalCount": 21,
        "evalCount": 9,
    }
    (request,) = server.requests
    assert request["method"] == "POST" and request["path"].startswith("/v1/messages")
    body = sent_body(request)
    assert body["model"] == "claude-opus-5-5"
    assert body["max_tokens"] == 4096
    assert body["system"] == "You answer for the shop."
    assert body["messages"][0]["role"] == "user"
    assert "ร้านเปิดกี่โมง" in body["messages"][0]["content"]
    assert body["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
    }
    assert body["fallbacks"] == "default"
    for key in ("temperature", "top_p", "top_k", "thinking"):
        assert key not in body
    assert "server-side-fallback-2026-07-01" in request["headers"]["anthropic-beta"]
    assert request["headers"]["authorization"] == "Bearer sk-ant-tenant"


def test_claude_other_models_use_the_plain_endpoint(sdk, fake_server):
    server = claude_server(
        fake_server, json_reply(200, claude_message([TEXT_ANSWER], model="claude-haiku-5-5"))
    )
    ask(ClaudeClient("sk-ant-tenant", base_url=server.url), model="claude-haiku-5-5")
    (request,) = server.requests
    body = sent_body(request)
    assert "fallbacks" not in body
    assert "anthropic-beta" not in request["headers"]
    assert body["output_config"]["effort"] == "low"


def test_claude_default_model(sdk, fake_server):
    server = claude_server(fake_server, json_reply(200, claude_message([TEXT_ANSWER])))
    ask(ClaudeClient("sk-ant-tenant", base_url=server.url), model="  ")
    assert sent_body(server.requests[0])["model"] == "claude-opus-5-5"


def test_claude_answer_after_a_fallback_block(sdk, fake_server):
    fallback = {
        "type": "fallback",
        "from": {"model": "claude-opus-5-5"},
        "to": {"model": "claude-opus-4-8"},
        "trigger": {"type": "refusal", "category": "cyber"},
    }
    early = {
        "type": "text",
        "text": '{"answer": "partial", "canAnswer": true, "confidence": 1}',
    }
    message = claude_message([early, fallback, THINKING, TEXT_ANSWER], model="claude-opus-4-8")
    server = claude_server(fake_server, json_reply(200, message))
    result = ask(ClaudeClient("sk-ant-tenant", base_url=server.url), model="claude-opus-5-5")
    assert result["answer"] == ANSWER["answer"]
    assert result["model"] == "claude-opus-4-8"


def test_claude_refusal_means_cannot_answer(sdk, fake_server):
    message = claude_message([], stop_reason="refusal")
    message["stop_details"] = {
        "type": "refusal",
        "category": "cyber",
        "explanation": "declined",
    }
    server = claude_server(fake_server, json_reply(200, message))
    result = ask(ClaudeClient("sk-ant-tenant", base_url=server.url), model="claude-opus-5-5")
    assert (
        result["answer"] == "" and result["canAnswer"] is False and result["confidence"] == 0.0
    )


def test_claude_max_tokens_is_an_error(sdk, fake_server):
    partial = {"type": "text", "text": '{"answer": "ร้าน'}
    server = claude_server(
        fake_server, json_reply(200, claude_message([partial], stop_reason="max_tokens"))
    )
    with pytest.raises(ProviderError, match=r"Claude stopped before finishing the answer\."):
        ask(ClaudeClient("sk-ant-tenant", base_url=server.url))


def test_claude_text_that_is_not_json_is_an_error(sdk, fake_server):
    server = claude_server(
        fake_server, json_reply(200, claude_message([{"type": "text", "text": "Sorry"}]))
    )
    with pytest.raises(ProviderError, match="did not return a structured answer"):
        ask(ClaudeClient("sk-ant-tenant", base_url=server.url))


@pytest.mark.parametrize(
    ("reply", "status", "code", "text"),
    [
        (
            claude_error(401, "authentication_error", "invalid x-api-key"),
            401,
            "ai_key_invalid",
            "Claude error 401: invalid x-api-key",
        ),
        (
            claude_error(403, "permission_error", "not allowed"),
            403,
            None,
            "Claude error 403: not allowed",
        ),
        (
            claude_error(404, "not_found_error", "model: claude-nope"),
            404,
            None,
            "Claude error 404",
        ),
        (claude_error(429, "rate_limit_error", "slow down"), 429, None, "Claude error 429"),
        (
            claude_error(400, "invalid_request_error", "messages: bad"),
            400,
            None,
            "Claude error 400: messages: bad",
        ),
        (
            claude_error(529, "overloaded_error", "Overloaded"),
            529,
            None,
            "Claude is overloaded (HTTP 529): Overloaded",
        ),
        (claude_error(500, "api_error", "Internal"), 500, None, "Claude error 500: Internal"),
    ],
)
def test_claude_error_mapping(sdk, fake_server, reply, status, code, text):
    server = claude_server(fake_server, reply)
    with pytest.raises(ProviderError) as caught:
        ask(ClaudeClient("sk-ant-tenant", base_url=server.url), model="claude-haiku-5-5")
    assert caught.value.status_code == status
    assert caught.value.code == code
    assert str(caught.value).startswith(text)
    assert "sk-ant-tenant" not in str(caught.value)
    assert len(server.requests) == 1


def test_claude_retries_an_overload_once(sdk, fake_server):
    server = claude_server(
        fake_server,
        json_reply(
            529,
            {"type": "error", "error": {"type": "overloaded_error", "message": "busy"}},
            {"retry-after-ms": "1"},
        ),
        json_reply(200, claude_message([TEXT_ANSWER])),
    )
    assert ask(ClaudeClient("sk-ant-tenant", base_url=server.url))["canAnswer"] is True
    assert len(server.requests) == 2


def claude_status(status: int, error_type: str, headers: dict[str, str] | None = None):
    """A Claude error reply without x-should-retry (the API leaves it out at times)."""
    return json_reply(
        status, {"type": "error", "error": {"type": error_type, "message": "busy"}}, headers
    )


def test_claude_sdk_client_makes_no_retries_of_its_own(sdk):
    client = ClaudeClient("sk-ant-tenant", base_url="http://127.0.0.1:9")
    with client._client() as sdk_client:
        assert sdk_client.max_retries == 0
    with client._client(max_retries=1) as sdk_client:
        assert sdk_client.max_retries == 1


def test_claude_does_not_send_the_question_again_after_a_timeout(sdk, fake_server):
    def slow(request):
        time.sleep(3)
        return json_reply(200, claude_message([TEXT_ANSWER]))

    server = fake_server(slow)
    client = ClaudeClient("sk-ant-tenant", base_url=server.url)
    client.timeout = 1.5  # shorter than the constructor allows, to keep the test quick
    started = time.monotonic()
    with pytest.raises(ProviderError, match=r"^Claude did not answer in time\.$"):
        ask(client, model="claude-haiku-5-5")
    elapsed = time.monotonic() - started
    time.sleep(0.8)  # the SDK's own retry would have arrived by now
    assert len(server.requests) == 1
    assert elapsed < 2.8


def test_claude_does_not_wait_out_a_long_retry_after(sdk, fake_server):
    server = claude_server(
        fake_server,
        claude_status(429, "rate_limit_error", {"retry-after": "30"}),
        json_reply(200, claude_message([TEXT_ANSWER])),
    )
    started = time.monotonic()
    with pytest.raises(ProviderError) as caught:
        ask(ClaudeClient("sk-ant-tenant", base_url=server.url), model="claude-haiku-5-5")
    assert caught.value.status_code == 429
    assert time.monotonic() - started < 1.5
    assert len(server.requests) == 1


def test_claude_waits_a_short_retry_after_and_tries_once_more(sdk, fake_server):
    server = claude_server(
        fake_server,
        claude_status(529, "overloaded_error", {"retry-after": "1"}),
        json_reply(200, claude_message([TEXT_ANSWER])),
    )
    started = time.monotonic()
    result = ask(ClaudeClient("sk-ant-tenant", base_url=server.url), model="claude-haiku-5-5")
    assert result["answer"] == ANSWER["answer"]
    assert len(server.requests) == 2
    assert time.monotonic() - started >= 0.9


def test_claude_retries_a_server_error_once_only(sdk, fake_server):
    recovered = claude_server(
        fake_server,
        claude_status(500, "api_error"),
        json_reply(200, claude_message([TEXT_ANSWER])),
    )
    client = ClaudeClient("sk-ant-tenant", base_url=recovered.url)
    assert ask(client, model="claude-haiku-5-5")["canAnswer"] is True
    assert len(recovered.requests) == 2
    failing = claude_server(fake_server, claude_status(503, "api_error"))
    with pytest.raises(ProviderError) as caught:
        ask(ClaudeClient("sk-ant-tenant", base_url=failing.url), model="claude-haiku-5-5")
    assert caught.value.status_code == 503
    assert len(failing.requests) == 2


def test_claude_does_not_retry_when_the_wait_would_pass_the_deadline(sdk, fake_server):
    server = claude_server(
        fake_server,
        claude_status(529, "overloaded_error", {"retry-after": "1"}),
        json_reply(200, claude_message([TEXT_ANSWER])),
    )
    client = ClaudeClient("sk-ant-tenant", base_url=server.url)
    client.timeout = 1.8  # a 1 s wait plus a second for the answer does not fit
    with pytest.raises(ProviderError) as caught:
        ask(client, model="claude-haiku-5-5")
    assert caught.value.status_code == 529
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (529, {"retry-after": "1"}, 1.0),
        (429, {"retry-after-ms": "250"}, 0.25),
        (429, {"retry-after-ms": "soon", "retry-after": "2"}, 2.0),
        (429, {"retry-after": "30"}, None),
        (503, {"x-should-retry": "false"}, None),
        (500, {"x-should-retry": "False", "retry-after": "1"}, None),
        (400, {}, None),
        (401, {"x-should-retry": "true"}, None),
        (404, {}, None),
    ],
)
def test_claude_retry_wait(status, headers, expected):
    assert ai_claude._status_retry_wait(status, headers) == expected


@pytest.mark.parametrize(
    ("status", "headers"),
    [
        (500, {}),
        (408, {}),
        (409, {}),
        (429, {"retry-after": "Wed, 21 Oct 2015 07:28:00 GMT"}),  # a date: unknown
        (529, {"retry-after": "0"}),
        (502, {"retry-after": "nan"}),
    ],
)
def test_claude_retry_wait_without_a_usable_retry_after(status, headers):
    wait = ai_claude._status_retry_wait(status, headers)
    assert wait is not None and 0.5 <= wait <= 1.0


@pytest.mark.parametrize(
    "reply",
    [
        claude_error(401, "authentication_error", "invalid x-api-key: sk-ant-tenant-secret"),
        claude_error(400, "invalid_request_error", "bad header value sk-ant-tenant-secret"),
    ],
)
def test_claude_errors_never_show_the_key(sdk, fake_server, reply):
    server = claude_server(fake_server, reply)
    with pytest.raises(ProviderError) as caught:
        ask(
            ClaudeClient("sk-ant-tenant-secret", base_url=server.url), model="claude-haiku-5-5"
        )
    assert "sk-ant-tenant-secret" not in str(caught.value)
    assert "****" in str(caught.value)


def test_claude_retries_without_effort_and_remembers_it(sdk, fake_server):
    server = claude_server(
        fake_server,
        claude_error(
            400,
            "invalid_request_error",
            "output_config.effort: effort is not supported for this model",
        ),
        json_reply(200, claude_message([TEXT_ANSWER], model="claude-haiku-4-5")),
    )
    client = ClaudeClient("sk-ant-tenant", base_url=server.url)
    assert ask(client, model="claude-haiku-4-5")["canAnswer"] is True
    first, second = (sent_body(request) for request in server.requests)
    assert first["output_config"]["effort"] == "low"
    assert "effort" not in second["output_config"]
    assert second["output_config"]["format"]["type"] == "json_schema"
    ask(client, model="claude-haiku-4-5")
    assert len(server.requests) == 3
    assert "effort" not in sent_body(server.requests[2])["output_config"]


def test_claude_falls_back_to_the_plain_endpoint_when_the_beta_is_refused(sdk, fake_server):
    server = claude_server(
        fake_server,
        claude_error(
            400,
            "invalid_request_error",
            "Unexpected value(s) `server-side-fallback-2026-07-01` for the `anthropic-beta` header.",
        ),
        json_reply(200, claude_message([TEXT_ANSWER])),
    )
    client = ClaudeClient("sk-ant-tenant", base_url=server.url)
    assert ask(client, model="claude-sonnet-5-5")["canAnswer"] is True
    retry = server.requests[1]
    assert "fallbacks" not in sent_body(retry) and "anthropic-beta" not in retry["headers"]
    assert sent_body(retry)["output_config"]["effort"] == "low"


def test_claude_ignores_credentials_and_headers_from_the_environment(
    sdk, fake_server, monkeypatch
):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-env")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "server-token")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://127.0.0.1:9/")
    monkeypatch.setenv("ANTHROPIC_PROFILE", "missing-profile")
    monkeypatch.setenv(
        "ANTHROPIC_CUSTOM_HEADERS",
        "X-Api-Key: sk-ant-server-custom\nAuthorization: Bearer server-custom\n"
        "anthropic-workspace-id: wrkspc_server\nanthropic-beta: some-beta\nX-Extra: 1",
    )
    server = claude_server(fake_server, json_reply(200, claude_message([TEXT_ANSWER])))
    client = ClaudeClient("sk-ant-tenant", base_url=server.url)
    ask(client, model="claude-haiku-5-5")
    ask(client, model="claude-opus-5-5")
    plain, beta = server.requests
    for request in (plain, beta):
        headers = request["headers"]
        assert headers["authorization"] == "Bearer sk-ant-tenant"
        assert request["header_names"].count("authorization") == 1
        for name in ("x-api-key", "anthropic-workspace-id", "x-extra"):
            assert name not in headers
    assert "anthropic-beta" not in plain["headers"]
    assert beta["headers"]["anthropic-beta"] == "server-side-fallback-2026-07-01"


def test_claude_without_a_key_sends_nothing(sdk, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-server-env")
    client = ClaudeClient("  ", base_url="http://127.0.0.1:9")
    with pytest.raises(ProviderError) as caught:
        ask(client)
    assert caught.value.code == "ai_key_required"
    with pytest.raises(ProviderError) as caught:
        client.list_models()
    assert caught.value.code == "ai_key_required"


def test_claude_does_not_follow_redirects(sdk, fake_server):
    def handler(request):
        if request["path"].startswith("/v1/messages"):
            return (
                307,
                {"Location": "/v1/elsewhere", "Content-Type": "application/json"},
                b"{}",
            )
        return json_reply(200, claude_message([TEXT_ANSWER]))

    server = fake_server(handler)
    with pytest.raises(ProviderError) as caught:
        ask(ClaudeClient("sk-ant-tenant", base_url=server.url), model="claude-haiku-5-5")
    assert caught.value.status_code == 307
    assert [request["path"].split("?")[0] for request in server.requests] == ["/v1/messages"]


def test_claude_model_list_pages_through(sdk, fake_server):
    pages = [
        json_reply(
            200,
            {
                "data": [
                    {
                        "type": "model",
                        "id": "claude-opus-5-5",
                        "display_name": "Claude Opus 5.5",
                        "created_at": "2026-10-01T00:00:00Z",
                    }
                ],
                "has_more": True,
                "first_id": "claude-opus-5-5",
                "last_id": "claude-opus-5-5",
            },
        ),
        json_reply(
            200,
            {
                "data": [
                    {
                        "type": "model",
                        "id": "claude-haiku-5-5",
                        "display_name": "Claude Haiku 5.5",
                        "created_at": "2026-09-01T00:00:00Z",
                    }
                ],
                "has_more": False,
                "first_id": "claude-haiku-5-5",
                "last_id": "claude-haiku-5-5",
            },
        ),
    ]
    server = claude_server(fake_server, *pages)
    listed = ClaudeClient("sk-ant-tenant", base_url=server.url).list_models()
    assert listed == [
        {"name": "claude-opus-5-5", "displayName": "Claude Opus 5.5", "kind": "chat"},
        {"name": "claude-haiku-5-5", "displayName": "Claude Haiku 5.5", "kind": "chat"},
    ]
    first, second = server.requests
    assert first["method"] == "GET" and first["path"].startswith("/v1/models")
    assert "limit=1000" in first["path"]
    assert "after_id=claude-opus-5-5" in second["path"]


def test_claude_model_list_error(sdk, fake_server):
    server = claude_server(
        fake_server, claude_error(401, "authentication_error", "invalid x-api-key")
    )
    with pytest.raises(ProviderError) as caught:
        ClaudeClient("sk-ant-tenant", base_url=server.url).list_models()
    assert caught.value.status_code == 401 and caught.value.code == "ai_key_invalid"


def test_claude_model_list_with_a_deadline_is_tried_once(sdk, raw_server):
    server = raw_server(stay_silent)
    client = ClaudeClient("sk-ant-tenant", base_url=f"http://127.0.0.1:{server.port}")
    started = time.monotonic()
    with pytest.raises(ProviderError, match=r"^Claude did not answer in time\.$"):
        client.list_models(timeout=1)
    assert time.monotonic() - started < 3
    time.sleep(0.8)  # an SDK retry would have connected again by now
    assert server.connections == 1


def test_claude_model_list_retries_only_without_a_deadline(sdk, fake_server, monkeypatch):
    built: list[int] = []
    original = ClaudeClient._client

    def recording(self, *, max_retries=0):
        built.append(max_retries)
        return original(self, max_retries=max_retries)

    monkeypatch.setattr(ClaudeClient, "_client", recording)
    empty = {"data": [], "has_more": False, "first_id": None, "last_id": None}
    server = claude_server(fake_server, json_reply(200, empty))
    client = ClaudeClient("sk-ant-tenant", base_url=server.url)
    assert client.list_models() == [] and client.list_models(timeout=30) == []
    assert built == [1, 0]


def test_claude_reports_a_missing_sdk(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # import anthropic now fails
    client = ClaudeClient("sk-ant-tenant")
    with pytest.raises(ProviderError) as caught:
        ask(client)
    assert caught.value.code == "ai_provider_unavailable"
    with pytest.raises(ProviderError) as caught:
        client.list_models()
    assert caught.value.code == "ai_provider_unavailable"


def test_claude_has_no_embeddings():
    with pytest.raises(ProviderError) as caught:
        ClaudeClient("sk-ant-tenant").embed("any", ["text"])
    assert caught.value.code == "ai_no_embeddings"


def test_claude_network_errors_are_mapped(sdk):
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    client = ClaudeClient("sk-ant-tenant")
    assert (
        str(client._error(sdk.APITimeoutError(request=request)))
        == "Claude did not answer in time."
    )
    assert (
        str(client._error(sdk.APIConnectionError(request=request)))
        == "Cannot connect to Claude."
    )
    assert (
        str(client._error(sdk.AnthropicError("x")))
        == "Claude request failed (AnthropicError)."
    )


def test_claude_answer_text_skips_thinking_and_fallback_blocks():
    class Block:
        def __init__(self, kind, text=""):
            self.type = kind
            self.text = text

    blocks = [Block("text", "old"), Block("fallback"), Block("thinking"), Block("text", "new")]
    assert ai_claude._answer_text(blocks) == "new"
    assert (
        ai_claude._answer_text([Block("thinking"), Block("text", "a"), Block("text", "b")])
        == "ab"
    )


# --- Thai keyword matching ----------------------------------------------------


def test_thai_words_keep_their_vowel_and_tone_marks():
    terms = assistant._lexical_terms("ค่าส่งเท่าไหร่")
    assert "ค่าส่งเท่าไหร่" in terms
    assert {"ค่า", "่าส", "เท่"} <= terms
    # The old split broke the word at every mark and left single letters.
    assert "ค" not in terms and "าส" not in terms


def test_thai_question_ranks_the_relevant_chunk_first():
    shirt = {"id": "shirt", "text": "เสื้อยืดสีดำ ไซส์ M L XL ราคา 290 บาท"}
    tiger = {"id": "tiger", "text": "ตุ๊กตาเสือสีดำ ขนนุ่ม ราคา 350 บาท"}
    ranked = assistant.rank_chunks("มีเสื้อสีดำไหม", [tiger, shirt], top_k=2)
    assert [item["id"] for item in ranked] == ["shirt", "tiger"]
    assert ranked[0]["score"] > ranked[1]["score"]


@pytest.mark.parametrize(
    ("question", "relevant", "unrelated"),
    [
        ("ร้านเปิดกี่โมง", "ร้านเปิดทุกวัน 9 โมงเช้าถึง 6 โมงเย็น", "ส่งสินค้าทั่วประเทศภายใน 3 วัน"),
        ("สินค้าเปลี่ยนคืนได้ไหม", "เปลี่ยนคืนสินค้าได้ภายใน 7 วัน", "สินค้าใหม่เข้าทุกสัปดาห์"),
        ("ขายไข่ไก่ไหม", "ไข่ไก่สด แผงละ 30 ฟอง", "ไก่ย่างไม้ละ 25 บาท"),
        ("ค่าส่งกี่บาท", "ค่าส่ง 40 บาททั่วประเทศ", "ค่าสมาชิกปีละ 199 บาท"),
    ],
)
def test_thai_keyword_score_prefers_the_related_text(question, relevant, unrelated):
    assert assistant.lexical_score(question, relevant) > assistant.lexical_score(
        question, unrelated
    )
    assert assistant.lexical_score(question, relevant) > 0.1


def test_keyword_terms_for_latin_text_are_unchanged():
    terms = assistant._lexical_terms("Opening_hours: 9am!")
    assert {"opening", "hours", "9am"} <= terms
    assert "ope" in terms and "s9a" in terms
    assert "_" not in "".join(terms)


def test_cosine_similarity_tolerates_bad_vectors():
    assert assistant.cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
    assert assistant.cosine_similarity([1.0, "x"], [1.0, 0.0]) == 0.0
    assert assistant.cosine_similarity([1.0], [1.0, 2.0]) == 0.0
    ranked = assistant.rank_chunks(
        "q", [{"text": "a", "embedding": ["bad", 1]}], query_embedding=[1.0, 0.0]
    )
    assert ranked == []


def test_providers_are_frozen_dataclasses():
    provider = get_provider("openai")
    assert isinstance(provider, AIProvider)
    with pytest.raises(AttributeError):
        provider.label = "x"
