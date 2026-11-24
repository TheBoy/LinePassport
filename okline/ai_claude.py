"""Claude (Anthropic) for the auto-reply assistant, on the official ``anthropic`` SDK.

The SDK needs Python 3.10 or newer and comes with the ``claude`` extra. It is
imported only when a Claude client talks to the API, so the rest of the app runs
without it and the provider reports itself unavailable.
"""

from __future__ import annotations

import copy
import importlib.metadata
import math
import os
import random
import re
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from .assistant import (
    ANSWER_SCHEMA,
    CHAT_DEADLINE_SECONDS,
    CONNECT_TIMEOUT_SECONDS,
    LONG_DEADLINE_SECONDS,
    AssistantError,
    ProviderError,
    _assistant_prompt,
    _call_timeout,
    _clamp_timeout,
    _parse_json_object,
    _safe_int,
    _scrub_secret,
    _status_phrase,
    _structured_answer,
)

if TYPE_CHECKING:
    import anthropic
    from anthropic.types import JSONOutputFormatParam, OutputConfigParam
    from anthropic.types.beta import BetaJSONOutputFormatParam, BetaOutputConfigParam

CLAUDE_API_BASE_URL = "https://api.anthropic.com"
DEFAULT_CLAUDE_CHAT_MODEL = "claude-opus-5-5"
CLAUDE_MAX_TOKENS = 4096
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
CLAUDE_FALLBACK_MODELS = frozenset(
    {
        "claude-opus-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5",
        "claude-fable-5-1",
        "claude-fable-5-2",
    }
)
"""Models whose refusals the API retries on another model (``fallbacks="default"``)."""

_MAX_MODELS = 2000
_MAX_ATTEMPTS = 4
"""Tries in one chat call: the effort and refusal-fallback steps, plus one retry
after a rate limit, an overload or a server error."""
_RETRY_STATUSES = frozenset({408, 409, 429})
"""Statuses retried once (and every 5xx), as the SDK would, unless x-should-retry is false."""
_MAX_RETRY_WAIT_SECONDS = 4.0
"""Longest wait before that retry. When the API asks for a longer wait there is no retry."""

# (base URL, model) pairs where the API refused output_config.effort, or the
# refusal-fallback beta. Remembered for the life of the process.
_NO_EFFORT: set[tuple[str, str]] = set()
_NO_FALLBACKS: set[tuple[str, str]] = set()
_LEARNED_LOCK = threading.Lock()


def _python_supported() -> bool:
    return sys.version_info >= (3, 10)


def _installed_sdk_version() -> str:
    try:
        return importlib.metadata.version("anthropic")
    except importlib.metadata.PackageNotFoundError:
        return ""


def anthropic_sdk_available() -> bool:
    """Whether the anthropic SDK this client needs (2.12 up to 3) is installed."""
    if not _python_supported():
        return False
    numbers = [int(part) for part in re.findall(r"\d+", _installed_sdk_version())[:2]]
    if len(numbers) < 2:
        return False
    return (2, 12) <= (numbers[0], numbers[1]) < (3, 0)


def reset_learned_settings() -> None:
    """Forget which models refused effort or refusal fallbacks (for tests)."""
    with _LEARNED_LOCK:
        _NO_EFFORT.clear()
        _NO_FALLBACKS.clear()


def _learn(store: set[tuple[str, str]], key: tuple[str, str]) -> None:
    with _LEARNED_LOCK:
        if len(store) >= 1024:
            store.clear()
        store.add(key)


def _unavailable() -> ProviderError:
    return ProviderError(
        "Claude needs the anthropic package on Python 3.10 or newer.",
        code="ai_provider_unavailable",
    )


def _env_header_overrides(api_key: str) -> dict[str, Any]:
    """Client headers that undo ``ANTHROPIC_CUSTOM_HEADERS``.

    With ``api_key`` and ``base_url`` passed, the SDK reads no credential and no
    address from the environment, but it still adds every header named in
    ``ANTHROPIC_CUSTOM_HEADERS`` to each request. On a server shared by many
    workspaces that could send another credential (``X-Api-Key``,
    ``Authorization``) or another workspace id, so each of those headers is
    removed again (``omit``), or set back to what the request needs. This has to
    happen in the client's ``default_headers``: a per-request ``omit`` of
    ``anthropic-workspace-id`` is dropped by the SDK to keep the client's value.
    """
    import anthropic

    overrides: dict[str, Any] = {}
    for line in (os.environ.get("ANTHROPIC_CUSTOM_HEADERS") or "").split("\n"):
        name, separator, _value = line.partition(":")
        name = name.strip()
        if not separator or not name:
            continue
        lowered = name.lower()
        if lowered == "authorization":
            overrides[name] = f"Bearer {api_key}"
        elif lowered in {"content-type", "accept"}:
            overrides[name] = "application/json"
        elif lowered != "anthropic-version":  # the API version is not a credential
            overrides[name] = anthropic.omit
    return overrides


def _retry_after_seconds(headers: Mapping[str, str]) -> float | None:
    """The wait the API asked for (``retry-after-ms``, else ``retry-after`` in seconds).

    ``None`` when there is none, or it is a date or something else unreadable.
    """
    for name, scale in (("retry-after-ms", 1000.0), ("retry-after", 1.0)):
        value = headers.get(name)
        if value is None:
            continue
        try:
            seconds = float(str(value).strip()) / scale
        except ValueError:
            continue
        if math.isfinite(seconds) and seconds > 0:
            return seconds
    return None


def _status_retry_wait(status: int, headers: Mapping[str, str]) -> float | None:
    """Seconds to wait before the one retry after this error status, or ``None``.

    Only 408, 409, 429 and 5xx (529 included) are retried, and not when the API
    says ``x-should-retry: false`` or asks for a longer wait than
    :data:`_MAX_RETRY_WAIT_SECONDS`. Without a retry-after header the wait is
    half a second to a second.
    """
    if status not in _RETRY_STATUSES and status < 500:
        return None
    if str(headers.get("x-should-retry") or "").strip().lower() == "false":
        return None
    wait = _retry_after_seconds(headers)
    if wait is None:
        return random.uniform(0.5, 1.0)
    return wait if wait <= _MAX_RETRY_WAIT_SECONDS else None


def _answer_text(blocks: Sequence[Any]) -> str:
    # Thinking blocks come before the answer, and after a refusal fallback the
    # answer is whatever follows the last "fallback" block.
    start = 0
    for index, block in enumerate(blocks):
        if getattr(block, "type", "") == "fallback":
            start = index + 1
    return "".join(
        str(getattr(block, "text", "") or "")
        for block in blocks[start:]
        if getattr(block, "type", "") == "text"
    )


class ClaudeClient:
    """Claude chat over the Messages API with structured JSON output.

    Claude has no embeddings API, so :meth:`embed` always fails with
    ``ai_no_embeddings`` (the registry marks the provider ``embeddings=False``).
    ``timeout`` is the total time for one chat call, its one retry included: the
    SDK's own retries are off, because the SDK would send the request again
    after a timeout (when it may already have reached the API) and wait out any
    retry-after. Model lists get at least 120 seconds and one SDK retry (a GET
    is safe to repeat), or exactly ``list_models(timeout=...)`` without retry.
    """

    label = "Claude"

    def __init__(
        self, api_key: str, *, base_url: str = "", timeout: float = CHAT_DEADLINE_SECONDS
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or CLAUDE_API_BASE_URL).strip().rstrip("/")
        self.timeout = _clamp_timeout(timeout)
        self.long_timeout = max(self.timeout, LONG_DEADLINE_SECONDS)

    def _client(self, *, max_retries: int = 0) -> anthropic.Anthropic:
        import anthropic

        if not self.api_key:
            # Never construct the SDK client without a key: it would fall back
            # to the server's own ANTHROPIC_API_KEY.
            raise ProviderError("Claude API key is not configured.", code="ai_key_required")
        return anthropic.Anthropic(
            api_key=self.api_key,
            base_url=self.base_url,
            max_retries=max_retries,
            timeout=anthropic.Timeout(self.timeout, connect=CONNECT_TIMEOUT_SECONDS),
            default_headers=_env_header_overrides(self.api_key),
            http_client=anthropic.DefaultHttpxClient(follow_redirects=False),
        )

    def list_models(self, timeout: float | None = None) -> list[dict[str, Any]]:
        try:
            import anthropic
        except ImportError as exc:
            raise _unavailable() from exc
        if timeout is None:
            limit, retries = self.long_timeout, 1  # a GET is safe to send again
        else:
            limit, retries = _call_timeout(timeout, self.long_timeout), 0
        result: list[dict[str, Any]] = []
        try:
            with self._client(max_retries=retries) as client:
                page = client.models.list(
                    limit=1000,
                    timeout=anthropic.Timeout(
                        limit, connect=min(CONNECT_TIMEOUT_SECONDS, limit)
                    ),
                )
                for item in page:
                    if len(result) >= _MAX_MODELS:
                        break
                    model_id = str(item.id or "").strip()
                    if model_id:
                        result.append(
                            {
                                "name": model_id,
                                "displayName": str(item.display_name or model_id),
                                "kind": "chat",
                            }
                        )
        except AssistantError:
            raise
        except anthropic.AnthropicError as exc:
            raise self._error(exc) from exc
        except Exception as exc:
            raise ProviderError(f"Claude request failed ({type(exc).__name__}).") from exc
        return result

    def embed(
        self, model: str, texts: list[str], timeout: float | None = None
    ) -> list[list[float]]:
        raise ProviderError("Claude does not offer embeddings.", code="ai_no_embeddings")

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
        del temperature  # current Claude models refuse temperature, top_p and top_k
        try:
            import anthropic
        except ImportError as exc:
            raise _unavailable() from exc
        model = (model or "").strip() or DEFAULT_CLAUDE_CHAT_MODEL
        prompt = _assistant_prompt(question, context, strict_knowledge)
        system = (system_prompt or "").strip()
        learned_key = (self.base_url, model)
        with _LEARNED_LOCK:
            use_effort = learned_key not in _NO_EFFORT
            use_fallbacks = (
                model in CLAUDE_FALLBACK_MODELS and learned_key not in _NO_FALLBACKS
            )
        deadline = time.monotonic() + self.timeout
        response: Any = None
        status_retried = False
        try:
            with self._client() as client:
                for _attempt in range(_MAX_ATTEMPTS):
                    remaining = deadline - time.monotonic()
                    if remaining < 1.0:
                        raise ProviderError("Claude did not answer in time.")
                    timeout = anthropic.Timeout(
                        remaining, connect=min(CONNECT_TIMEOUT_SECONDS, remaining)
                    )
                    try:
                        if use_fallbacks:
                            response = self._create_with_fallbacks(
                                client, model, system, prompt, use_effort, timeout
                            )
                        else:
                            response = self._create(
                                client, model, system, prompt, use_effort, timeout
                            )
                        break
                    except anthropic.BadRequestError as exc:
                        text = f"{exc.api_message or ''} {exc.message}".lower()
                        if use_effort and "effort" in text:
                            use_effort = False
                            _learn(_NO_EFFORT, learned_key)
                            continue
                        if use_fallbacks and ("fallback" in text or "anthropic-beta" in text):
                            use_fallbacks = False
                            _learn(_NO_FALLBACKS, learned_key)
                            continue
                        raise
                    except anthropic.APIStatusError as exc:
                        # One retry after a rate limit, overload or server error, and
                        # only while the wait still fits the deadline. A timeout or a
                        # broken connection is never retried: the request may have
                        # reached the API already.
                        wait = (
                            None
                            if status_retried
                            else _status_retry_wait(int(exc.status_code), exc.response.headers)
                        )
                        if wait is None or wait + 1.0 >= deadline - time.monotonic():
                            raise
                        status_retried = True
                        time.sleep(wait)
        except AssistantError:
            raise
        except anthropic.AnthropicError as exc:
            raise self._error(exc) from exc
        except Exception as exc:
            raise ProviderError(f"Claude request failed ({type(exc).__name__}).") from exc
        if response is None:
            raise ProviderError("Claude refused the request settings.")
        return self._answer(response, model)

    def _create(
        self,
        client: anthropic.Anthropic,
        model: str,
        system: str,
        prompt: str,
        use_effort: bool,
        timeout: anthropic.Timeout,
    ) -> anthropic.types.Message:
        import anthropic

        output_format: JSONOutputFormatParam = {
            "type": "json_schema",
            "schema": copy.deepcopy(ANSWER_SCHEMA),
        }
        output_config: OutputConfigParam = {"format": output_format}
        if use_effort:
            output_config["effort"] = "low"
        return client.messages.create(
            model=model,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=system or anthropic.omit,
            messages=[{"role": "user", "content": prompt}],
            output_config=output_config,
            timeout=timeout,
        )

    def _create_with_fallbacks(
        self,
        client: anthropic.Anthropic,
        model: str,
        system: str,
        prompt: str,
        use_effort: bool,
        timeout: anthropic.Timeout,
    ) -> anthropic.types.beta.BetaMessage:
        import anthropic

        output_format: BetaJSONOutputFormatParam = {
            "type": "json_schema",
            "schema": copy.deepcopy(ANSWER_SCHEMA),
        }
        output_config: BetaOutputConfigParam = {"format": output_format}
        if use_effort:
            output_config["effort"] = "low"
        return client.beta.messages.create(
            model=model,
            max_tokens=CLAUDE_MAX_TOKENS,
            system=system or anthropic.omit,
            messages=[{"role": "user", "content": prompt}],
            output_config=output_config,
            betas=[CLAUDE_FALLBACK_BETA],
            fallbacks="default",
            timeout=timeout,
        )

    def _answer(self, response: Any, model: str) -> dict[str, Any]:
        usage = getattr(response, "usage", None)
        prompt_tokens = _safe_int(getattr(usage, "input_tokens", 0))
        output_tokens = _safe_int(getattr(usage, "output_tokens", 0))
        served_by = str(getattr(response, "model", "") or model)
        stop_reason = getattr(response, "stop_reason", None)
        if stop_reason == "refusal":
            # The pipeline then sends the shop's own fallback text.
            return _structured_answer(
                {}, model=served_by, prompt_tokens=prompt_tokens, output_tokens=output_tokens
            )
        if stop_reason in {"max_tokens", "model_context_window_exceeded"}:
            raise ProviderError("Claude stopped before finishing the answer.")
        text = _answer_text(getattr(response, "content", None) or [])
        try:
            parsed = _parse_json_object(text)
        except AssistantError as exc:
            raise ProviderError("Claude did not return a structured answer.") from exc
        return _structured_answer(
            parsed, model=served_by, prompt_tokens=prompt_tokens, output_tokens=output_tokens
        )

    def _error(self, exc: Exception) -> ProviderError:
        import anthropic

        if isinstance(exc, anthropic.APIStatusError):
            status = int(exc.status_code)
            detail = " ".join(str(exc.api_message or "").split())[:300] or _status_phrase(
                status
            )
            code: str | None = None
            if isinstance(exc, anthropic.AuthenticationError):
                code = "ai_key_invalid"
                message = f"Claude error 401: {detail}"
            elif isinstance(exc, anthropic.PermissionDeniedError):
                message = f"Claude error 403: {detail}"
            elif isinstance(exc, anthropic.NotFoundError):
                message = (
                    f"Claude error 404 (model not found or not open to this key): {detail}"
                )
            elif isinstance(exc, anthropic.RateLimitError):
                message = f"Claude error 429 (rate or spend limit): {detail}"
            elif isinstance(exc, anthropic.BadRequestError):
                message = f"Claude error 400: {detail}"
            elif isinstance(exc, anthropic.OverloadedError) or status == 529:
                message = f"Claude is overloaded (HTTP 529): {detail}"
            else:
                message = f"Claude error {status}: {detail}"
            return ProviderError(_scrub_secret(message, self.api_key), status, code)
        if isinstance(exc, anthropic.APITimeoutError):
            return ProviderError("Claude did not answer in time.")
        if isinstance(exc, anthropic.APIConnectionError):
            return ProviderError("Cannot connect to Claude.")
        return ProviderError(f"Claude request failed ({type(exc).__name__}).")
