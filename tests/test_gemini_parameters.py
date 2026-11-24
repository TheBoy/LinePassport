"""Gemini chat models that refuse the sampling parameters (the temperature).

Google's documentation (read 2026-11-24) says that ``temperature``, ``top_p`` and
``top_k`` are ignored since Gemini 3.6 and will get an HTTP 400 from later models,
and gives the error body's shape, but not that error's wording. The error texts
below are therefore assumed wordings, not copies of real answers.

Nothing here talks to Google: the client gets a fake transport.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import pytest
import requests

from okline import ai_providers, assistant
from okline import webapp as webapp_module
from okline.ai_providers import make_ai_client
from okline.assistant import (
    GEMINI_API_BASE_URL,
    MAX_AI_RESPONSE_BYTES,
    AssistantError,
    GeminiClient,
    GeminiError,
)
from okline.webapp import WebAuth, WebConfig, WebState

KEY = "AIza-test-key-0001"
ANSWER = {"answer": "ร้านเปิด 9 โมงเช้า", "canAnswer": True, "confidence": 0.8}
ANSWER_TEXT = json.dumps(ANSWER, ensure_ascii=False)
MODEL = "gemini-next"
ROUNDING = 1e-6
"""Room for float rounding when a time limit is compared with its whole number."""


@pytest.fixture(autouse=True)
def _fresh_learning() -> Iterator[None]:
    ai_providers.reset_negotiation_cache()
    yield
    ai_providers.reset_negotiation_cache()


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


def answered(text: str = ANSWER_TEXT) -> tuple[int, dict[str, Any]]:
    return 200, {
        "candidates": [{"content": {"parts": [{"text": text}]}}],
        "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
    }


def refused(
    message: str, *, field: str = "", description: str = "", status: int = 400
) -> tuple[int, dict[str, Any]]:
    """An error in the documented shape: error.code, message, status and details."""
    error: dict[str, Any] = {"code": status, "message": message, "status": "INVALID_ARGUMENT"}
    if field:
        error["details"] = [
            {
                "@type": "type.googleapis.com/google.rpc.BadRequest",
                "fieldViolations": [{"field": field, "description": description or message}],
            }
        ]
    return status, {"error": error}


NO_SAMPLING = refused(
    "Sampling parameters (temperature, top_p, top_k) are not supported by this model."
)


def ask(client: Any, **overrides: Any) -> dict[str, Any]:
    params: dict[str, Any] = {
        "model": MODEL,
        "question": "ร้านเปิดกี่โมง",
        "context": "ร้านเปิดทุกวัน 9 โมงเช้า",
        "system_prompt": "You answer for the shop.",
        "strict_knowledge": True,
        "temperature": 0.2,
    }
    params.update(overrides)
    return client.chat(**params)


def config_of(call: dict[str, Any]) -> dict[str, Any]:
    return call["body"]["generationConfig"]


def with_temperature(transport: FakeTransport) -> list[bool]:
    """For each request sent, whether it carried a temperature."""
    return ["temperature" in config_of(call) for call in transport.calls]


# --- the first request is what it was -----------------------------------------


def test_the_first_request_is_unchanged():
    transport = FakeTransport(answered())
    ask(GeminiClient(KEY, transport=transport), temperature=0.7)
    (call,) = transport.calls
    assert call["method"] == "POST"
    assert call["url"] == f"{GEMINI_API_BASE_URL}/v1beta/models/{MODEL}:generateContent"
    assert call["headers"] == {"Accept": "application/json", "x-goog-api-key": KEY}
    prompt = assistant._assistant_prompt("ร้านเปิดกี่โมง", "ร้านเปิดทุกวัน 9 โมงเช้า", True)
    assert call["body"] == {
        "systemInstruction": {"parts": [{"text": "You answer for the shop."}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.7,
            "responseMimeType": "application/json",
            "responseSchema": {
                "type": "OBJECT",
                "properties": {
                    "answer": {"type": "STRING"},
                    "canAnswer": {"type": "BOOLEAN"},
                    "confidence": {"type": "NUMBER"},
                },
                "required": ["answer", "canAnswer", "confidence"],
            },
        },
    }
    # The order too, so the bytes sent are the same as before.
    assert list(call["body"]) == ["systemInstruction", "contents", "generationConfig"]
    assert list(config_of(call)) == ["temperature", "responseMimeType", "responseSchema"]
    assert call["max_bytes"] == MAX_AI_RESPONSE_BYTES


# --- one more request, without the temperature --------------------------------


def test_a_400_naming_temperature_is_asked_once_more_without_it():
    transport = FakeTransport(NO_SAMPLING, answered())
    result = ask(GeminiClient(KEY, transport=transport))
    assert result["answer"] == ANSWER["answer"] and result["canAnswer"] is True
    assert result["model"] == MODEL and result["promptEvalCount"] == 5
    first, second = transport.calls  # exactly one more request
    url = f"{GEMINI_API_BASE_URL}/v1beta/models/{MODEL}:generateContent"
    assert first["url"] == url and second["url"] == url
    assert config_of(first)["temperature"] == 0.2
    # The second request is the first without the temperature, and nothing else changed.
    expected = copy.deepcopy(first["body"])
    del expected["generationConfig"]["temperature"]
    assert second["body"] == expected
    assert config_of(second)["responseMimeType"] == "application/json"
    required = config_of(second)["responseSchema"]["required"]
    assert required == ["answer", "canAnswer", "confidence"]
    assert second["method"] == "POST" and second["max_bytes"] == MAX_AI_RESPONSE_BYTES
    assert second["headers"]["x-goog-api-key"] == KEY


def test_the_next_ask_for_that_model_sends_no_temperature():
    transport = FakeTransport(NO_SAMPLING, answered())
    client = GeminiClient(KEY, transport=transport)
    ask(client)
    assert len(transport.calls) == 2
    ask(client)
    # Another client in the same process, and another temperature.
    ask(GeminiClient(KEY, transport=transport), temperature=1.5)
    # One request each: nothing is tried first.
    assert with_temperature(transport) == [True, False, False, False]
    key = ("gemini", GEMINI_API_BASE_URL, MODEL)
    assert ai_providers._learned_flags(key) == {"no_temperature"}
    # Another model starts from the full request again.
    ask(client, model="gemini-3.8-flash")
    assert config_of(transport.calls[4])["temperature"] == 0.2
    # So does this model once the memory is cleared (a restart).
    ai_providers.reset_negotiation_cache()
    ask(client)
    assert config_of(transport.calls[5])["temperature"] == 0.2


def test_what_was_learned_belongs_to_one_address_and_one_model_name():
    transport = FakeTransport(NO_SAMPLING, answered())
    ask(GeminiClient(KEY, transport=transport, base_url="https://one.example"))
    ask(GeminiClient(KEY, transport=transport, base_url="https://two.example"))
    assert with_temperature(transport) == [True, False, True]
    # The factory's client and a "models/" prefix reach the same memory.
    learned = FakeTransport(NO_SAMPLING, answered())
    ask(make_ai_client("gemini", api_key=KEY, transport=learned), model=f"models/{MODEL}")
    ask(make_ai_client("gemini", api_key=KEY, transport=learned), model=MODEL)
    assert with_temperature(learned) == [True, False, False]


@pytest.mark.parametrize(
    "reply",
    [
        NO_SAMPLING,
        # How Google's APIs refuse a JSON field they do not know.
        refused(
            'Invalid JSON payload received. Unknown name "temperature" at '
            "'generation_config': Cannot find field.",
            field="generation_config",
        ),
        # Named only in the field violation, not in the message.
        refused(
            "Request contains an invalid argument.",
            field="generation_config.temperature",
            description="This field is no longer supported.",
        ),
        # Gemini 4 Neon and Hydrogen: a value out of range is a 400 validation error.
        refused(
            "* GenerateContentRequest.generation_config.temperature: value is out of range"
        ),
        refused("Custom sampling values are not supported for this model."),
        # A generationConfig field refused without its name.
        refused("generation_config contains a field that this model does not support."),
        refused("Unknown field in generationConfig."),
        refused("This generation config uses deprecated parameters."),
    ],
)
def test_wordings_that_mean_the_sampling_parameters(reply):
    transport = FakeTransport(reply, answered())
    assert ask(GeminiClient(KEY, transport=transport))["canAnswer"] is True
    assert with_temperature(transport) == [True, False]


def test_a_temperature_of_zero_is_left_out_the_same_way():
    # Save and test asks with temperature 0.
    transport = FakeTransport(NO_SAMPLING, answered())
    ask(GeminiClient(KEY, transport=transport), temperature=0.0)
    first, second = transport.calls
    assert config_of(first)["temperature"] == 0.0 and "temperature" not in config_of(second)


# --- everything else stays an error --------------------------------------------


def test_another_400_is_still_an_error():
    still = refused("Sampling parameters are not supported by this model. Really.")
    transport = FakeTransport(NO_SAMPLING, still)
    client = GeminiClient(KEY, transport=transport)
    with pytest.raises(GeminiError) as caught:
        ask(client)
    assert len(transport.calls) == 2  # never a third request
    assert caught.value.status_code == 400
    assert str(caught.value) == (
        "Gemini error 400: Sampling parameters are not supported by this model. Really."
    )
    # With nothing left to leave out, the next 400 is an error at once.
    with pytest.raises(GeminiError):
        ask(client)
    assert len(transport.calls) == 3
    assert "temperature" not in config_of(transport.calls[2])


@pytest.mark.parametrize(
    "reply",
    [
        refused("API key not valid. Please pass a valid API key."),
        refused(
            "The input token count (2000000) exceeds the maximum number of tokens allowed "
            "(1048576)."
        ),
        refused("JSON mode is not enabled for models/gemini-next"),
        # Another field of generationConfig: leaving the temperature out would not help.
        refused(
            'Invalid JSON payload received. Unknown name "responseSchema" at '
            "'generation_config': Cannot find field.",
            field="generation_config",
        ),
        refused("generation_config.response_mime_type: application/json is not supported."),
        refused("Structured output in generation_config is not supported by this model."),
        refused("Request contains an invalid argument."),
        (400, b"<html>Bad Request</html>"),
        (400, {"error": "bad request"}),
    ],
)
def test_a_400_about_something_else_is_not_asked_again(reply):
    transport = FakeTransport(reply)
    with pytest.raises(GeminiError) as caught:
        ask(GeminiClient(KEY, transport=transport))
    assert len(transport.calls) == 1
    assert caught.value.status_code == 400
    assert ai_providers._learned_flags(("gemini", GEMINI_API_BASE_URL, MODEL)) == frozenset()


def test_an_invalid_key_keeps_its_code():
    transport = FakeTransport(refused("API key not valid. Please pass a valid API key."))
    with pytest.raises(GeminiError) as caught:
        ask(GeminiClient(KEY, transport=transport))
    assert caught.value.code == "ai_key_invalid" and len(transport.calls) == 1


@pytest.mark.parametrize("status", [401, 403, 404, 422, 429, 500, 503])
def test_only_a_400_is_asked_again(status):
    reply = refused("temperature is not supported by this model.", status=status)
    transport = FakeTransport(reply, answered())
    with pytest.raises(GeminiError) as caught:
        ask(GeminiClient(KEY, transport=transport))
    assert len(transport.calls) == 1
    assert caught.value.status_code == status


def test_an_unusable_answer_to_the_second_request_is_an_error():
    transport = FakeTransport(NO_SAMPLING, answered("not json"))
    with pytest.raises(GeminiError, match="did not return a structured answer"):
        ask(GeminiClient(KEY, transport=transport))
    assert len(transport.calls) == 2


# --- never after a timeout or a lost connection -------------------------------


@pytest.mark.parametrize(
    "failure",
    [
        AssistantError("the provider did not answer in time"),
        # The 400's own body stopped coming: the transport's error carries the status.
        AssistantError("the provider did not answer in time", 400),
        AssistantError(
            "the connection broke while reading the answer (temperature is not supported)", 400
        ),
        requests.ConnectionError("temperature"),
        OSError("socket"),
        ValueError("temperature"),
    ],
)
def test_no_second_request_after_a_timeout_or_a_lost_connection(failure):
    transport = FakeTransport(failure, answered())
    client = GeminiClient(KEY, transport=transport)
    with pytest.raises(GeminiError) as caught:
        ask(client)
    assert str(caught.value).startswith("Gemini request failed")
    # The question may have reached Google and been answered and billed.
    assert len(transport.calls) == 1
    assert ai_providers._learned_flags(("gemini", GEMINI_API_BASE_URL, MODEL)) == frozenset()


def test_a_timeout_of_the_second_request_ends_the_ask():
    transport = FakeTransport(
        NO_SAMPLING, AssistantError("the provider did not answer in time"), answered()
    )
    client = GeminiClient(KEY, transport=transport)
    with pytest.raises(GeminiError, match=r"^Gemini request failed: the provider did not"):
        ask(client)
    assert len(transport.calls) == 2
    # The refusal itself was learned: the next ask goes out once, without the temperature.
    assert ask(client)["canAnswer"] is True
    assert len(transport.calls) == 3 and "temperature" not in config_of(transport.calls[2])


# --- one time limit for both requests -----------------------------------------


class FakeClock:
    """Stands in for the ``time`` module inside okline.assistant, and nowhere else."""

    def __init__(self) -> None:
        self.now = 1000.0

    def monotonic(self) -> float:
        return self.now


class TimedTransport(FakeTransport):
    """A fake transport whose replies take time on a clock the test owns."""

    def __init__(self, monkeypatch, *timed_replies: tuple[float, Any]) -> None:
        super().__init__(*(reply for _seconds, reply in timed_replies))
        self.durations = [seconds for seconds, _reply in timed_replies]
        self.clock = FakeClock()
        monkeypatch.setattr(assistant, "time", self.clock)

    def __call__(self, method, url, **kwargs):
        index = min(len(self.calls), len(self.durations) - 1)
        self.clock.now += self.durations[index]
        return super().__call__(method, url, **kwargs)


def test_the_second_request_gets_what_is_left_of_the_60_seconds(monkeypatch):
    transport = TimedTransport(monkeypatch, (20.0, NO_SAMPLING), (5.0, answered()))
    assert ask(GeminiClient(KEY, transport=transport))["canAnswer"] is True
    first, second = (call["timeout"] for call in transport.calls)
    # Ranges, not ==: on a real clock each limit is a moment under its whole number.
    assert 59.0 < first <= 60.0
    assert 39.0 < second <= 40.0
    assert first - second >= 20.0 - 1e-6  # the first request's time is not given again


def test_the_time_limit_set_for_the_client_covers_both_requests(monkeypatch):
    transport = TimedTransport(monkeypatch, (3.0, NO_SAMPLING), (1.0, answered()))
    ask(GeminiClient(KEY, transport=transport, timeout=10))
    first, second = (call["timeout"] for call in transport.calls)
    assert 9.0 < first <= 10.0
    assert 6.0 < second <= 7.0


def test_no_second_request_when_the_time_is_used_up(monkeypatch):
    transport = TimedTransport(monkeypatch, (59.5, NO_SAMPLING), (1.0, answered()))
    client = GeminiClient(KEY, transport=transport)
    with pytest.raises(GeminiError, match=r"^Gemini did not answer in time\.$"):
        ask(client)
    assert len(transport.calls) == 1
    # What the refusal taught is kept: the next ask is one request without the temperature.
    transport.durations = [1.0]
    assert ask(client)["canAnswer"] is True
    assert len(transport.calls) == 2 and "temperature" not in config_of(transport.calls[1])


def test_the_real_clock_gives_the_first_request_about_the_whole_limit():
    transport = FakeTransport(answered())
    ask(GeminiClient(KEY, transport=transport))
    ask(GeminiClient(KEY, transport=transport, timeout=30))
    whole, half = (call["timeout"] for call in transport.calls)
    # On a clock that has not ticked in between, (now + 60) - now can round to a few
    # picoseconds over 60, hence the hair of room.
    assert 59.0 < whole <= 60.0 + ROUNDING
    assert 29.0 < half <= 30.0 + ROUNDING


# --- embeddings and the model list --------------------------------------------


def test_embeddings_and_the_model_list_send_no_sampling_parameters():
    embeddings = FakeTransport((200, {"embeddings": [{"values": [0.1, 0.2]}]}))
    GeminiClient(KEY, transport=embeddings).embed("", ["hello"])
    (call,) = embeddings.calls
    assert call["body"] == {
        "requests": [
            {
                "model": "models/gemini-embedding-001",
                "content": {"parts": [{"text": "hello"}]},
            }
        ]
    }
    models = FakeTransport((200, {"models": []}))
    GeminiClient(KEY, transport=models).list_models()
    (call,) = models.calls
    assert call["method"] == "GET" and call["body"] is None
    assert call["url"] == f"{GEMINI_API_BASE_URL}/v1beta/models?pageSize=200"
    # So a 400 there has nothing to leave out: it is an error, with one request.
    for send in (
        lambda client: client.embed("", ["hello"]),
        lambda client: client.list_models(),
    ):
        refusing = FakeTransport(NO_SAMPLING, answered())
        with pytest.raises(GeminiError) as caught:
            send(GeminiClient(KEY, transport=refusing))
        assert caught.value.status_code == 400 and len(refusing.calls) == 1
    assert not assistant._NEGOTIATED  # and nothing was learned, for any model


# --- Save and test, and the asks after it --------------------------------------


@pytest.fixture
def web_state(tmp_path, monkeypatch) -> Iterator[WebState]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    root = tmp_path / ".okline"
    state = WebState(
        WebConfig(
            host="127.0.0.1",
            port=0,
            tokens_file=str(tmp_path / "tokens.json"),
            state_dir=str(root),
            accounts_file=str(root / "accounts.json"),
            accounts_dir=str(root / "accounts"),
            schedules_file=str(root / "schedules.json"),
            auth_file=str(root / "auth.json"),
        )
    )
    try:
        yield state
    finally:
        state.close()


def test_save_and_test_passes_for_a_model_that_refuses_the_temperature(web_state, monkeypatch):
    chats: list[dict[str, Any]] = []

    def fake_transport(method, url, *, headers, json_body, timeout, max_bytes):
        if not url.endswith(f"/models/{MODEL}:generateContent"):
            vectors = [{"values": [0.1, 0.2]} for _request in json_body["requests"]]
            return 200, {}, json.dumps({"embeddings": vectors}).encode()
        chats.append(copy.deepcopy(json_body))
        status, body = NO_SAMPLING if len(chats) == 1 else answered()
        return status, {}, json.dumps(body).encode()

    def make(provider_id, *, api_key, base_url="", transport=None, timeout=60.0):
        # The real Gemini client, on a transport that never leaves the process.
        assert provider_id == "gemini" and transport is None
        return GeminiClient(api_key, transport=fake_transport, timeout=timeout)

    monkeypatch.setattr(webapp_module, "make_ai_client", make)
    token = web_state.web_auth.setup("owner@example.com", "owner-pass")
    owner = web_state.web_auth.current_user(WebAuth.cookie_header(token))
    saved = web_state.save_assistant_settings(
        {"provider": "gemini", "apiKey": KEY, "chatModel": MODEL, "test": True}, owner
    )
    assert saved["test"] == {"ok": True, "error": "", "model": MODEL}
    assert [body["generationConfig"].get("temperature") for body in chats] == [0.0, None]
    # Asks after it, in the same process, go out once and without the temperature.
    result = web_state.ask_global_ai("ร้านเปิดกี่โมง", owner)
    assert result["question"]["answer"] == ANSWER["answer"]
    assert len(chats) == 3 and "temperature" not in chats[2]["generationConfig"]
