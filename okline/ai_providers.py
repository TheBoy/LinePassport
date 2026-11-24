"""AI providers for the auto-reply assistant: registry, clients and factory.

The registry lists every chat provider in the order the Settings page shows
them, with each provider's request rules kept as data. :func:`make_ai_client`
builds the client for one provider. Every client has the surface described in
:mod:`okline.assistant`: ``list_models(timeout=None)``, ``embed(model, texts)``
and ``chat(...)``, and fails only with :class:`~okline.assistant.AssistantError`
subclasses.
"""

from __future__ import annotations

import copy
import threading
import time
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from types import MappingProxyType
from typing import Any, Protocol

from .ai_claude import (
    CLAUDE_API_BASE_URL,
    DEFAULT_CLAUDE_CHAT_MODEL,
    ClaudeClient,
    anthropic_sdk_available,
)
from .assistant import (
    _NOT_JSON,
    ANSWER_SCHEMA,
    CHAT_DEADLINE_SECONDS,
    DEFAULT_GEMINI_CHAT_MODEL,
    DEFAULT_GEMINI_EMBEDDING_MODEL,
    GEMINI_API_BASE_URL,
    LONG_DEADLINE_SECONDS,
    MAX_AI_EMBEDDING_BYTES,
    MAX_AI_MODEL_LIST_BYTES,
    MAX_AI_RESPONSE_BYTES,
    AssistantError,
    GeminiClient,
    GeminiError,
    ProviderError,
    Transport,
    _assistant_prompt,
    _call_timeout,
    _clamp_timeout,
    _decode_json,
    _error_detail,
    _float_vector,
    _parse_json_object,
    _safe_float,
    _safe_int,
    _scrub_secret,
    _status_phrase,
    _structured_answer,
    _vector_size_problem,
    default_transport,
)

__all__ = [
    "ANSWER_SCHEMA",
    "AIClient",
    "AIProvider",
    "AssistantError",
    "ClaudeClient",
    "GeminiClient",
    "GeminiError",
    "OpenAICompatibleClient",
    "ProviderError",
    "Transport",
    "default_transport",
    "get_provider",
    "make_ai_client",
    "normalize_custom_base_url",
    "provider_available",
    "provider_ids",
    "provider_registry",
    "reset_negotiation_cache",
]


@dataclass(frozen=True)
class AIProvider:
    """One chat provider and the rules its requests follow.

    ``kind`` is ``"gemini"`` (:class:`GeminiClient`), ``"anthropic"``
    (:class:`ClaudeClient`) or ``"openai_compat"`` (:class:`OpenAICompatibleClient`).
    ``models_source`` says where Refresh models looks: ``"gemini"``,
    ``"anthropic"``, ``"openai"`` (``GET {base}/models``), ``"openrouter_user"``
    (``GET {base}/models/user``) or ``"static"`` (``static_models``, no request).
    ``json_mode`` is ``"schema"`` (strict ``json_schema``) or ``"object"``
    (``json_object``). ``max_tokens_field`` is ``None`` when the provider must
    not get one; ``max_tokens`` rises to :data:`REASONING_MAX_TOKENS` when the
    model may reason. ``extra_body`` is sent with every chat request.
    ``refusal_error_codes`` are error codes of an HTTP 400 that mean the
    provider's safety filter refused the question (answered like a refusal).
    """

    id: str
    label: str
    short_label: str
    group: str
    kind: str
    base_url: str
    default_chat_model: str
    default_embedding_model: str
    embeddings: bool
    key_required: bool
    key_hint: str
    key_placeholder: str
    models_source: str
    static_models: tuple[str, ...] = ()
    json_mode: str = "schema"
    send_temperature: bool = True
    temperature_max: float = 2.0
    max_tokens_field: str | None = "max_tokens"
    max_tokens: int = 1024
    extra_body: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({}), compare=False
    )
    embed_batch: int = 16
    retry_blank: bool = False
    """A blank answer counts as a failure and is asked for once more (DeepSeek)."""
    refusal_error_codes: tuple[str, ...] = ()

    @property
    def needs_base_url(self) -> bool:
        return self.kind == "openai_compat" and not self.base_url

    def public_fields(self) -> dict[str, Any]:
        """The registry fields the settings page reads, in the API's camelCase."""
        return {
            "id": self.id,
            "label": self.label,
            "group": self.group,
            "keyRequired": self.key_required,
            "keyHint": self.key_hint,
            "keyPlaceholder": self.key_placeholder,
            "defaultChatModel": self.default_chat_model,
            "defaultEmbeddingModel": self.default_embedding_model,
            "embeddings": self.embeddings,
            "needsBaseUrl": self.needs_base_url,
        }


def _frozen(values: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(copy.deepcopy(dict(values)))


_PROVIDERS: tuple[AIProvider, ...] = (
    AIProvider(
        id="gemini",
        label="Gemini (Google)",
        short_label="Gemini",
        group="main",
        kind="gemini",
        base_url=GEMINI_API_BASE_URL,
        default_chat_model=DEFAULT_GEMINI_CHAT_MODEL,
        default_embedding_model=DEFAULT_GEMINI_EMBEDDING_MODEL,
        embeddings=True,
        key_required=True,
        key_hint="aistudio.google.com -> Get API key",
        key_placeholder="AIza...",
        models_source="gemini",
        embed_batch=16,
    ),
    AIProvider(
        id="anthropic",
        label="Claude (Anthropic)",
        short_label="Claude",
        group="main",
        kind="anthropic",
        base_url=CLAUDE_API_BASE_URL,
        default_chat_model=DEFAULT_CLAUDE_CHAT_MODEL,
        default_embedding_model="",
        embeddings=False,
        key_required=True,
        key_hint="platform.claude.com -> API keys",
        key_placeholder="sk-ant-...",
        models_source="anthropic",
        send_temperature=False,
        max_tokens=4096,
    ),
    AIProvider(
        id="openai",
        label="ChatGPT (OpenAI)",
        short_label="OpenAI",
        group="main",
        kind="openai_compat",
        base_url="https://api.openai.com/v1",
        default_chat_model="gpt-6-luna",
        default_embedding_model="text-embedding-3-small",
        embeddings=True,
        key_required=True,
        key_hint="platform.openai.com -> API keys",
        key_placeholder="sk-...",
        models_source="openai",
        max_tokens_field="max_completion_tokens",
        extra_body=_frozen({"reasoning_effort": "none", "store": False}),
        embed_batch=32,
    ),
    AIProvider(
        id="zai",
        label="GLM (Z.ai)",
        short_label="Z.ai",
        group="more",
        kind="openai_compat",
        base_url="https://api.z.ai/api/paas/v4",
        default_chat_model="glm-4.7",
        default_embedding_model="",
        embeddings=False,
        key_required=True,
        key_hint="z.ai -> API Keys",
        key_placeholder="API key",
        models_source="static",
        static_models=(
            "glm-4.7",
            "glm-4.7-flashx",
            "glm-4.7-flash",
            "glm-5.2",
            "glm-5.1",
            "glm-5",
            "glm-4.6",
        ),
        json_mode="object",
        temperature_max=1.0,
        extra_body=_frozen({"thinking": {"type": "disabled"}}),
        refusal_error_codes=("1301",),  # the input safety filter
    ),
    AIProvider(
        id="deepseek",
        label="DeepSeek",
        short_label="DeepSeek",
        group="more",
        kind="openai_compat",
        base_url="https://api.deepseek.com",
        default_chat_model="deepseek-flash",
        default_embedding_model="",
        embeddings=False,
        key_required=True,
        key_hint="platform.deepseek.com -> API keys",
        key_placeholder="sk-...",
        models_source="openai",
        json_mode="object",
        extra_body=_frozen({"thinking": {"type": "disabled"}}),
        retry_blank=True,
    ),
    AIProvider(
        id="qwen",
        label="Qwen (Alibaba Cloud)",
        short_label="Qwen",
        group="more",
        kind="openai_compat",
        base_url="https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        default_chat_model="qwen3.8-flash",
        default_embedding_model="text-embedding-v4",
        embeddings=True,
        key_required=True,
        key_hint="Alibaba Cloud Model Studio, Singapore region -> API Key",
        key_placeholder="sk-...",
        models_source="static",
        static_models=("qwen3.8-flash", "qwen3.7-plus", "qwen4-max", "qwen-flash"),
        json_mode="object",
        temperature_max=1.9,
        max_tokens_field=None,
        extra_body=_frozen({"enable_thinking": False}),
        embed_batch=10,
    ),
    AIProvider(
        id="xai",
        label="Grok (xAI)",
        short_label="Grok",
        group="more",
        kind="openai_compat",
        base_url="https://api.x.ai/v1",
        default_chat_model="grok-4.20-non-reasoning",
        default_embedding_model="",
        embeddings=False,
        key_required=True,
        key_hint="console.x.ai -> API Keys",
        key_placeholder="xai-...",
        models_source="openai",
    ),
    AIProvider(
        id="mistral",
        label="Mistral AI",
        short_label="Mistral",
        group="more",
        kind="openai_compat",
        base_url="https://api.mistral.ai/v1",
        default_chat_model="mistral-small-latest",
        default_embedding_model="mistral-embed",
        embeddings=True,
        key_required=True,
        key_hint="console.mistral.ai -> API Keys",
        key_placeholder="API key",
        models_source="openai",
        temperature_max=1.0,
        extra_body=_frozen({"reasoning_effort": "none"}),
        embed_batch=32,
    ),
    AIProvider(
        id="moonshot",
        label="Kimi (Moonshot AI)",
        short_label="Kimi",
        group="more",
        kind="openai_compat",
        base_url="https://api.moonshot.ai/v1",
        default_chat_model="kimi-k3.1-pro-highspeed",
        default_embedding_model="",
        embeddings=False,
        key_required=True,
        key_hint="platform.kimi.ai -> API Keys",
        key_placeholder="sk-...",
        models_source="openai",
        send_temperature=False,
        max_tokens=4096,
        extra_body=_frozen({"reasoning_effort": "low"}),
    ),
    AIProvider(
        id="openrouter",
        label="OpenRouter",
        short_label="OpenRouter",
        group="more",
        kind="openai_compat",
        base_url="https://openrouter.ai/api/v1",
        default_chat_model="openai/gpt-6-luna",
        default_embedding_model="openai/text-embedding-3-small",
        embeddings=True,
        key_required=True,
        key_hint="openrouter.ai -> Keys",
        key_placeholder="sk-or-...",
        models_source="openrouter_user",
        extra_body=_frozen(
            {
                "provider": {"require_parameters": True, "sort": "latency"},
                "reasoning": {"effort": "none"},
            }
        ),
        embed_batch=32,
    ),
    AIProvider(
        id="custom",
        label="Custom API (OpenAI-compatible)",
        short_label="Custom API",
        group="custom",
        kind="openai_compat",
        base_url="",
        default_chat_model="",
        default_embedding_model="",
        embeddings=True,
        key_required=False,
        # The page says the key is optional in the reader's language.
        key_hint="",
        key_placeholder="",
        models_source="openai",
        json_mode="object",
        embed_batch=16,
    ),
)
_BY_ID: dict[str, AIProvider] = {provider.id: provider for provider in _PROVIDERS}


def provider_registry() -> tuple[AIProvider, ...]:
    """Every chat provider, in the order the Settings page shows them."""
    return _PROVIDERS


def provider_ids() -> tuple[str, ...]:
    return tuple(provider.id for provider in _PROVIDERS)


def get_provider(provider_id: str) -> AIProvider | None:
    """The provider with this id, or ``None`` (``"ollama"`` and ``"global"`` are gone)."""
    return _BY_ID.get(str(provider_id or "").strip().lower())


def provider_available(provider_id: str) -> tuple[bool, str]:
    """``(available, reason_code)``. Claude needs the anthropic SDK (Python 3.10+)."""
    provider = get_provider(provider_id)
    if provider is None:
        return False, "ai_settings_invalid"
    if provider.kind == "anthropic" and not anthropic_sdk_available():
        return False, "ai_provider_unavailable"
    return True, ""


class AIClient(Protocol):
    """What every client returned by :func:`make_ai_client` offers.

    ``list_models(timeout=...)`` gives the model list its own deadline (in
    seconds, with no retry for Claude); without it the list gets at least 120
    seconds. ``embed(timeout=...)`` gives each embedding request its own
    deadline the same way; without it each request gets at least 120 seconds.
    """

    def list_models(self, timeout: float | None = None) -> list[dict[str, Any]]: ...

    def embed(
        self, model: str, texts: list[str], timeout: float | None = None
    ) -> list[list[float]]: ...

    def chat(
        self,
        *,
        model: str,
        question: str,
        context: str,
        system_prompt: str,
        strict_knowledge: bool,
        temperature: float,
    ) -> dict[str, Any]: ...


# --- capability negotiation -------------------------------------------------
# When a chat request gets HTTP 400 or 422 whose error text names a request
# parameter, the client tries again with a smaller body (at most 3 times per
# call; never for any other status, except OpenRouter's 404 "No endpoints found
# that can handle the requested parameters") and remembers each step per
# (provider id, base URL, model) for the life of the process. Flags (a step only
# ever adds one, so flags learned by concurrent calls can be merged):
#   no_temperature      send no temperature
#   effort_low          reasoning_effort "low" instead of "none", no temperature
#   drop:<key>          leave out that extra_body key
#   json_object         json_object instead of json_schema
#   json_none           no response_format at all

MAX_NEGOTIATION_RETRIES = 3
REASONING_MAX_TOKENS = 4096
"""Output token cap when a model may reason: reasoning tokens count toward it."""
_NEGOTIATED: OrderedDict[tuple[str, str, str], frozenset[str]] = OrderedDict()
_NEGOTIATED_LOCK = threading.Lock()
_NEGOTIATED_LIMIT = 512
_EFFORT_LOW_PROVIDERS = frozenset({"openai", "mistral"})
_REASONING_KEYS = ("reasoning_effort", "enable_thinking", "thinking", "reasoning")


def reset_negotiation_cache() -> None:
    """Forget every parameter a provider refused (for tests)."""
    with _NEGOTIATED_LOCK:
        _NEGOTIATED.clear()


def _learned_flags(key: tuple[str, str, str]) -> frozenset[str]:
    with _NEGOTIATED_LOCK:
        return _NEGOTIATED.get(key, frozenset())


def _remember_flags(key: tuple[str, str, str], flags: frozenset[str]) -> None:
    with _NEGOTIATED_LOCK:
        # Merged, not replaced: another call may have learned a different step meanwhile.
        _NEGOTIATED[key] = _NEGOTIATED.get(key, frozenset()) | flags
        _NEGOTIATED.move_to_end(key)
        while len(_NEGOTIATED) > _NEGOTIATED_LIMIT:
            _NEGOTIATED.popitem(last=False)


def _mentions_quoted_none(text: str) -> bool:
    return any(f"{quote}none{quote}" in text for quote in ("'", '"', "`"))


def _no_endpoints_for_parameters(provider: AIProvider, text: str) -> bool:
    """OpenRouter found no endpoint for the request's parameters (require_parameters)."""
    return provider.id == "openrouter" and "no endpoints found" in text and "parameter" in text


def _smaller_response_format(
    body: dict[str, Any], flags: frozenset[str]
) -> frozenset[str] | None:
    response_format = body.get("response_format")
    if not isinstance(response_format, dict):
        return None
    if response_format.get("type") == "json_schema":
        return flags | {"json_object"}
    return flags | {"json_none"}


def _degrade(
    provider: AIProvider, body: dict[str, Any], flags: frozenset[str], text: str
) -> frozenset[str] | None:
    """The flags for the next try after a refused request whose error text is ``text``."""
    if _no_endpoints_for_parameters(provider, text):
        # The text names no parameter: step the JSON mode down first, then reasoning.
        smaller = _smaller_response_format(body, flags)
        if smaller is not None:
            return smaller
        return flags | {"drop:reasoning"} if "reasoning" in body else None
    if ("temperature" in text or "top_p" in text) and "temperature" in body:
        return flags | {"no_temperature"}
    if any(key in text for key in _REASONING_KEYS) or _mentions_quoted_none(text):
        present = [key for key in _REASONING_KEYS if key in body]
        if provider.id in _EFFORT_LOW_PROVIDERS and body.get("reasoning_effort") == "none":
            return flags | {"effort_low"}
        named = [key for key in present if key in text]
        for key in named or present:
            return flags | {f"drop:{key}"}
    if any(word in text for word in ("response_format", "json_schema", "json_object")):
        return _smaller_response_format(body, flags)
    return None


def _error_code(data: Any) -> str:
    """The ``error.code`` of an OpenAI-style error body, as text (``""`` if none)."""
    error = data.get("error") if isinstance(data, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    if code is None or isinstance(code, bool) or not isinstance(code, (str, int)):
        return ""
    return str(code).strip()


# --- model lists --------------------------------------------------------------

_MAX_LISTED_MODELS = 5000
_OPENAI_NOT_CHAT = (
    "image",
    "dall-e",
    "sora",
    "tts",
    "audio",
    "realtime",
    "transcribe",
    "whisper",
    "moderation",
    "search",
    "computer-use",
    "embedding",
    # Not served by Chat Completions:
    "babbage",
    "davinci",
    "-instruct",
    "codex",
    "deep-research",
    "gpt-live",
    "-pro",
    # Gated: open only to approved organisations.
    "-cyber",
    "gpt-daybreak-",
    "rosalind",
)
_GENERIC_NOT_CHAT = (
    "image",
    "imagine",
    "dall-e",
    "sora",
    "video",
    "tts",
    "audio",
    "realtime",
    "transcribe",
    "whisper",
    "moderation",
    "rerank",
    "ocr",
)


def _shut_down(value: Any, today: date) -> bool:
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            ends = datetime.fromtimestamp(value, timezone.utc).date()
        elif isinstance(value, str) and len(value.strip()) >= 10:
            ends = date.fromisoformat(value.strip()[:10])
        else:
            return False
    except (OverflowError, OSError, ValueError):
        return False
    return ends < today


def _classify_model(
    provider: AIProvider, item: dict[str, Any], model_id: str, today: date
) -> str | None:
    """``"chat"``, ``"embedding"``, ``"other"``, or ``None`` to leave the model out."""
    lowered = model_id.lower()
    if provider.id == "openai":
        if _shut_down(item.get("shutdown_date"), today):
            return None
        if lowered.startswith("text-embedding-"):
            return "embedding"
        if any(word in lowered for word in _OPENAI_NOT_CHAT):
            return "other"
        return "chat"
    if provider.id == "mistral":
        capabilities = item.get("capabilities")
        if "embed" in lowered:
            return "embedding"
        if isinstance(capabilities, dict):
            return "chat" if capabilities.get("completion_chat") is True else "other"
    if "embed" in lowered:
        return "embedding"
    architecture = item.get("architecture")
    outputs = architecture.get("output_modalities") if isinstance(architecture, dict) else None
    if isinstance(outputs, list) and outputs and "text" not in outputs:
        return "other"
    if any(word in lowered for word in _GENERIC_NOT_CHAT):
        return "other"
    return "chat"


def _model_items(data: Any) -> list[Any] | None:
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "models"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return None


# --- answers ------------------------------------------------------------------

_FILTERED_FINISH = frozenset({"content_filter", "sensitive"})


class _BlankAnswer(Exception):
    """A 2xx chat answer with no text (DeepSeek's JSON mode does this)."""


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Mistral (with reasoning) answers with a list of chunks; only the
        # "text" chunks are the answer, "thinking" chunks are not.
        return "".join(
            str(chunk.get("text") or "")
            for chunk in content
            if isinstance(chunk, dict) and chunk.get("type") == "text"
        )
    return ""


_ENDPOINT_SUFFIXES = ("/chat/completions", "/completions", "/models", "/embeddings")


def normalize_custom_base_url(value: str) -> str:
    """Trim spaces, trailing slashes and a pasted endpoint path. Checks nothing else."""
    url = (value or "").strip().rstrip("/")
    lowered = url.lower()
    for suffix in _ENDPOINT_SUFFIXES:
        if lowered.endswith(suffix):
            return url[: -len(suffix)].rstrip("/")
    return url


class OpenAICompatibleClient:
    """Chat, embeddings and model lists for an OpenAI-compatible Chat Completions API.

    The provider's request rules (JSON mode, temperature, max tokens field,
    extra body fields, embedding batch size) come from its :class:`AIProvider`
    entry. ``transport`` sends each request; the default one is only for fixed
    provider addresses (the Custom API needs the web app's public-address
    transport). ``timeout`` is the total time for one chat call, retries
    included; embeddings and model lists get at least 120 seconds per request,
    unless ``embed(timeout=...)`` or ``list_models(timeout=...)`` sets their own
    deadline.
    """

    def __init__(
        self,
        provider: AIProvider,
        *,
        api_key: str = "",
        base_url: str = "",
        transport: Transport | None = None,
        timeout: float = CHAT_DEADLINE_SECONDS,
    ) -> None:
        if provider.kind != "openai_compat":
            raise AssistantError(
                f"{provider.label} does not use the OpenAI-compatible API.",
                code="ai_settings_invalid",
            )
        self.provider = provider
        self.label = provider.short_label
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or provider.base_url).strip().rstrip("/")
        self.transport: Transport = transport or default_transport
        self.timeout = _clamp_timeout(timeout)
        self.long_timeout = max(self.timeout, LONG_DEADLINE_SECONDS)

    # -- HTTP --

    def _send(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None,
        *,
        timeout: float,
        max_bytes: int,
    ) -> tuple[int, Any]:
        if self.provider.key_required and not self.api_key:
            raise ProviderError(
                f"{self.label} API key is not configured.", code="ai_key_required"
            )
        if not self.base_url:
            raise ProviderError(f"{self.label} has no base URL.", code="ai_base_url_invalid")
        headers = {"Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            status, _headers, raw = self.transport(
                method,
                f"{self.base_url}/{path}",
                headers=headers,
                json_body=body,
                timeout=timeout,
                max_bytes=max_bytes,
            )
            status = int(status)
        except AssistantError as exc:
            raise ProviderError(
                _scrub_secret(f"{self.label} request failed: {exc}", self.api_key),
                exc.status_code,
                exc.code,
            ) from exc
        except Exception as exc:
            raise ProviderError(
                f"{self.label} request failed ({type(exc).__name__})."
            ) from exc
        return status, _decode_json(raw)

    def _http_error(self, status: int, data: Any) -> ProviderError:
        short, _full = _error_detail(data)
        detail = short or _status_phrase(status)
        message = _scrub_secret(f"{self.label} error {status}: {detail}", self.api_key)
        return ProviderError(message, status, "ai_key_invalid" if status == 401 else None)

    def _ok_json(self, status: int, data: Any) -> Any:
        if not 200 <= status < 300:
            raise self._http_error(status, data)
        if data is _NOT_JSON:
            raise ProviderError(f"{self.label} returned invalid JSON (HTTP {status}).", status)
        return data

    # -- models --

    def list_models(self, timeout: float | None = None) -> list[dict[str, Any]]:
        provider = self.provider
        if provider.models_source == "static":
            models = [
                {"name": name, "displayName": name, "kind": "chat"}
                for name in provider.static_models
            ]
            if provider.embeddings and provider.default_embedding_model:
                name = provider.default_embedding_model
                models.append({"name": name, "displayName": name, "kind": "embedding"})
            return models
        path = "models/user" if provider.models_source == "openrouter_user" else "models"
        status, data = self._send(
            "GET",
            path,
            None,
            timeout=_call_timeout(timeout, self.long_timeout),
            max_bytes=MAX_AI_MODEL_LIST_BYTES,
        )
        items = _model_items(self._ok_json(status, data))
        if items is None:
            raise ProviderError(f"{self.label} returned an unexpected model list.", status)
        today = datetime.now(timezone.utc).date()
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for raw_item in items[:_MAX_LISTED_MODELS]:
            item = {"id": raw_item} if isinstance(raw_item, str) else raw_item
            if not isinstance(item, dict):
                continue
            model_id = str(
                item.get("id") or item.get("model") or item.get("name") or ""
            ).strip()
            if not model_id or model_id in seen:
                continue
            kind = _classify_model(provider, item, model_id, today)
            if kind is None:
                continue
            seen.add(model_id)
            display = item.get("name") if isinstance(item.get("name"), str) else ""
            result.append({"name": model_id, "displayName": display or model_id, "kind": kind})
        return result

    # -- embeddings --

    def embed(
        self, model: str, texts: list[str], timeout: float | None = None
    ) -> list[list[float]]:
        provider = self.provider
        if not provider.embeddings:
            raise ProviderError(
                f"{self.label} does not offer embeddings.", code="ai_no_embeddings"
            )
        model = (model or "").strip() or provider.default_embedding_model
        if not model:
            raise ProviderError(
                f"{self.label} has no search model set.", code="ai_no_embeddings"
            )
        if not texts:
            return []
        batch_size = max(1, provider.embed_batch)
        vectors: list[list[float]] = []
        for start in range(0, len(texts), batch_size):
            batch = list(texts[start : start + batch_size])
            status, data = self._send(
                "POST",
                "embeddings",
                {"model": model, "input": batch},
                timeout=_call_timeout(timeout, self.long_timeout),
                max_bytes=MAX_AI_EMBEDDING_BYTES,
            )
            vectors.extend(self._batch_vectors(self._ok_json(status, data), len(batch)))
        return vectors

    def _batch_vectors(self, data: Any, expected: int) -> list[list[float]]:
        items = data.get("data") if isinstance(data, dict) else None
        if not isinstance(items, list) or len(items) != expected:
            raise ProviderError(f"{self.label} returned the wrong number of embeddings.")
        by_index: dict[int, Any] = {}
        for item in items:
            index = item.get("index") if isinstance(item, dict) else None
            if (
                isinstance(index, int)
                and not isinstance(index, bool)
                and 0 <= index < expected
            ):
                by_index[index] = item
        if len(by_index) == expected:
            items = [by_index[position] for position in range(expected)]
        vectors: list[list[float]] = []
        for item in items:
            values = item.get("embedding") if isinstance(item, dict) else None
            problem = _vector_size_problem(values)
            if problem:
                raise ProviderError(f"{self.label} returned {problem}.")
            vector = _float_vector(values)
            if vector is None:
                raise ProviderError(f"{self.label} returned an empty or invalid embedding.")
            vectors.append(vector)
        return vectors

    # -- chat --

    def _chat_body(
        self,
        model: str,
        messages: list[dict[str, str]],
        temperature: float,
        flags: frozenset[str],
    ) -> dict[str, Any]:
        provider = self.provider
        body: dict[str, Any] = {"model": model, "messages": copy.deepcopy(messages)}
        json_mode = provider.json_mode
        if "json_none" in flags:
            json_mode = "none"
        elif "json_object" in flags and json_mode == "schema":
            json_mode = "object"
        if json_mode == "schema":
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "answer",
                    "strict": True,
                    "schema": copy.deepcopy(ANSWER_SCHEMA),
                },
            }
        elif json_mode == "object":
            body["response_format"] = {"type": "json_object"}
        effort_low = "effort_low" in flags and "drop:reasoning_effort" not in flags
        if provider.send_temperature and "no_temperature" not in flags and not effort_low:
            value = max(0.0, min(_safe_float(temperature, 0.2), provider.temperature_max))
            body["temperature"] = round(value, 3)
        if provider.max_tokens_field:
            limit = provider.max_tokens
            if self._may_reason(model, flags):
                limit = max(limit, REASONING_MAX_TOKENS)
            body[provider.max_tokens_field] = limit
        for key, value in provider.extra_body.items():
            if f"drop:{key}" not in flags:
                body[key] = copy.deepcopy(value)
        if effort_low:
            body["reasoning_effort"] = "low"
        return body

    def _may_reason(self, model: str, flags: frozenset[str]) -> bool:
        """Whether the model may reason before it answers (the cap then has to leave room).

        That is the case once negotiation moved reasoning back to "low" or left
        out the setting that turned it off, and for every Grok model but the
        non-reasoning ones (xAI takes no setting for it).
        """
        if "effort_low" in flags or any(f"drop:{key}" in flags for key in _REASONING_KEYS):
            return True
        return self.provider.id == "xai" and "non-reasoning" not in model.lower()

    def chat(
        self,
        *,
        model: str,
        question: str,
        context: str,
        system_prompt: str,
        strict_knowledge: bool,
        temperature: float,
    ) -> dict[str, Any]:
        model = (model or "").strip() or self.provider.default_chat_model
        if not model:
            raise ProviderError(
                f"{self.label} needs a chat model name.", code="ai_model_required"
            )
        messages: list[dict[str, str]] = []
        system = (system_prompt or "").strip()
        if system:
            messages.append({"role": "system", "content": system})
        messages.append(
            {"role": "user", "content": _assistant_prompt(question, context, strict_knowledge)}
        )
        key = (self.provider.id, self.base_url, model)
        flags = _learned_flags(key)
        deadline = time.monotonic() + self.timeout
        retries = 0
        asked_again = False
        while True:
            remaining = deadline - time.monotonic()
            if remaining < 1.0:
                raise ProviderError(f"{self.label} did not answer in time.")
            body = self._chat_body(model, messages, temperature, flags)
            status, data = self._send(
                "POST",
                "chat/completions",
                body,
                timeout=remaining,
                max_bytes=MAX_AI_RESPONSE_BYTES,
            )
            if 200 <= status < 300:
                try:
                    return self._chat_answer(status, data, model)
                except _BlankAnswer:
                    if self.provider.retry_blank and not asked_again:
                        asked_again = True
                        continue
                    raise ProviderError(
                        f"{self.label} returned an empty answer.", status
                    ) from None
            if status == 400 and _error_code(data) in self.provider.refusal_error_codes:
                # The provider's safety filter refused the question, as it may refuse
                # an answer: the pipeline sends the shop's own fallback text.
                return _structured_answer({}, model=model, prompt_tokens=0, output_tokens=0)
            if status in (400, 404, 422) and retries < MAX_NEGOTIATION_RETRIES:
                _short, text = _error_detail(data)
                if status != 404 or _no_endpoints_for_parameters(self.provider, text):
                    degraded = _degrade(self.provider, body, flags, text)
                    if degraded is not None and degraded != flags:
                        retries += 1
                        flags = degraded
                        _remember_flags(key, flags)
                        continue
            raise self._http_error(status, data)

    def _chat_answer(self, status: int, data: Any, model: str) -> dict[str, Any]:
        if not isinstance(data, dict):
            raise ProviderError(f"{self.label} returned invalid JSON (HTTP {status}).", status)
        choices = data.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        if not isinstance(first, dict) or not isinstance(message, dict):
            raise ProviderError(f"{self.label} returned no answer.", status)
        raw_usage = data.get("usage")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        served_by = str(data.get("model") or model)
        tokens = {
            "prompt_tokens": _safe_int(usage.get("prompt_tokens")),
            "output_tokens": _safe_int(usage.get("completion_tokens")),
        }
        refusal = message.get("refusal")
        finish = str(first.get("finish_reason") or "").lower()
        if (isinstance(refusal, str) and refusal.strip()) or (
            finish in _FILTERED_FINISH and not _message_text(message.get("content")).strip()
        ):
            # A refusal or a filtered answer: the pipeline sends the shop's own fallback text.
            return _structured_answer({}, model=served_by, **tokens)
        text = _message_text(message.get("content"))
        if not text.strip():
            if finish == "length":
                raise ProviderError(
                    f"{self.label} stopped before finishing the answer.", status
                )
            raise _BlankAnswer
        try:
            parsed = _parse_json_object(text)
        except AssistantError:
            if finish in _FILTERED_FINISH:
                return _structured_answer({}, model=served_by, **tokens)
            if finish == "length":
                raise ProviderError(
                    f"{self.label} stopped before finishing the answer.", status
                ) from None
            raise ProviderError(
                f"{self.label} did not return a structured answer.", status
            ) from None
        return _structured_answer(parsed, model=served_by, **tokens)


def make_ai_client(
    provider_id: str,
    *,
    api_key: str,
    base_url: str = "",
    transport: Transport | None = None,
    timeout: float = CHAT_DEADLINE_SECONDS,
) -> AIClient:
    """The client for one provider.

    Named providers always use their fixed address; ``base_url`` is read only
    for the Custom API, which also needs ``transport`` (the web app's
    public-address transport; there is no default for it). The Claude client
    talks through the anthropic SDK and does not use ``transport``.
    """
    provider = get_provider(provider_id)
    if provider is None:
        raise AssistantError("Unknown AI provider.", code="ai_settings_invalid")
    if provider.kind == "gemini":
        return GeminiClient(
            api_key, timeout=timeout, transport=transport, embed_batch=provider.embed_batch
        )
    if provider.kind == "anthropic":
        available, reason = provider_available(provider.id)
        if not available:
            raise AssistantError(
                "Claude needs the anthropic package on Python 3.10 or newer.", code=reason
            )
        return ClaudeClient(api_key, timeout=timeout)
    url = provider.base_url
    if provider.needs_base_url:
        url = normalize_custom_base_url(base_url)
        if not url:
            raise AssistantError(
                "The Custom API needs a base URL.", code="ai_base_url_invalid"
            )
        if transport is None:
            raise AssistantError(
                "The Custom API must go through the web app's public-address transport."
            )
    return OpenAICompatibleClient(
        provider, api_key=api_key, base_url=url, transport=transport, timeout=timeout
    )
