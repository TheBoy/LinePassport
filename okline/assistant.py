"""AI clients and knowledge search helpers for the auto-reply assistant.

Every AI client (``GeminiClient`` here, ``ClaudeClient`` in :mod:`okline.ai_claude`,
``OpenAICompatibleClient`` in :mod:`okline.ai_providers`, which also has the
provider registry and the ``make_ai_client`` factory) has the same surface:
``list_models(timeout=None)``, ``embed(model, texts)`` and ``chat(...)`` returning
``{answer, canAnswer, confidence, model, promptEvalCount, evalCount}``.
Every failure surfaces as an :class:`AssistantError` subclass with a short
message that never contains the API key.

The knowledge helpers split shop text into chunks and rank them for a question,
by embedding vectors when they exist and by keyword overlap otherwise.

The transport helpers (:class:`DeadlineWatchdog`, :class:`DeadlineAdapter`,
:class:`WatchedHTTPConnection`) keep one HTTP request inside a total deadline in
every phase, connecting and the response headers included.
"""

from __future__ import annotations

import http
import json
import math
import re
import socket
import threading
import time
from collections.abc import Mapping
from types import TracebackType
from typing import Any, Protocol, TypeVar, cast

import requests
import urllib3
from requests.adapters import HTTPAdapter
from requests.structures import CaseInsensitiveDict
from urllib3.connection import HTTPConnection as _Urllib3HTTPConnection
from urllib3.connection import HTTPSConnection as _Urllib3HTTPSConnection
from urllib3.connectionpool import ConnectionPool, HTTPConnectionPool

MAX_AI_RESPONSE_BYTES = 2 * 1024 * 1024
"""Largest chat answer read from an AI provider."""
MAX_EMBEDDING_DIMENSIONS = 8192
"""Most numbers accepted in one search vector. Real models use 384 to 4096
(Gemini 3072, OpenAI 1536 or 3072, Mistral and Qwen 1024)."""
MAX_AI_EMBEDDING_BYTES = 8 * 1024 * 1024
"""Largest embeddings answer read from an AI provider: room for a batch of 32
vectors of :data:`MAX_EMBEDDING_DIMENSIONS` numbers at about 25 bytes each."""
MAX_AI_MODEL_LIST_BYTES = 8 * 1024 * 1024
"""Largest model list read from an AI provider (OpenRouter's lists run past 1 MB)."""

CONNECT_TIMEOUT_SECONDS = 5.0
CHAT_DEADLINE_SECONDS = 60.0
"""Total time one chat call may take, retries included."""
LONG_DEADLINE_SECONDS = 120.0
"""Total time one embeddings or model-list request may take."""

_READ_CHUNK_BYTES = 64 * 1024
_NO_ANSWER_IN_TIME = "the provider did not answer in time"


class AssistantError(RuntimeError):
    """An AI provider failed, or answered with something unusable.

    ``status_code`` is the provider's HTTP status when there was one. ``code`` is
    a stable error code (for example ``ai_key_required``) when the cause is known.
    """

    def __init__(
        self, message: str, status_code: int | None = None, code: str | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class GeminiError(AssistantError):
    """Raised by :class:`GeminiClient`."""


class ProviderError(AssistantError):
    """Raised by the Claude and OpenAI-compatible clients."""


ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "canAnswer": {"type": "boolean"},
        "confidence": {"type": "number"},
    },
    "required": ["answer", "canAnswer", "confidence"],
    "additionalProperties": False,
}
"""Strict answer schema for providers with JSON-schema output. Copy it before use."""


# --- HTTP transport -----------------------------------------------------------


class Transport(Protocol):
    """Sends one HTTP request for an AI client: ``(status, headers, body)``.

    A transport follows no redirect (a 3xx is an error), stops reading at
    ``max_bytes``, gives up after ``timeout`` seconds in total, never sends a
    request twice, and raises :class:`AssistantError` when the request fails.
    ``json_body`` is ``None`` for a request without a body.
    """

    def __call__(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str],
        json_body: Any,
        timeout: float,
        max_bytes: int,
    ) -> tuple[int, Mapping[str, str], bytes]: ...


def _keep_explicit_auth(request: requests.PreparedRequest) -> requests.PreparedRequest:
    # An explicit (no-op) auth stops requests from reading ~/.netrc, which would
    # otherwise replace the Authorization header the client set.
    return request


def _shutdown_socket(sock: Any) -> None:
    """Shut a socket down for reading and writing; safe from any thread.

    The base ``socket.socket.shutdown`` is called on purpose: ``ssl.SSLSocket``'s
    own ``shutdown`` drops its TLS state, which must not happen from another
    thread while a read is running. The blocked read simply ends.
    """
    target = sock if isinstance(sock, socket.socket) else getattr(sock, "socket", None)
    if not isinstance(target, socket.socket):
        return
    try:
        socket.socket.shutdown(target, socket.SHUT_RDWR)
    except OSError:
        pass  # already closed, or never connected


_PoolT = TypeVar("_PoolT", bound=HTTPConnectionPool)


def _capped_timeout(current: Any, left: float) -> float:
    """A socket timeout no longer than ``left`` seconds (at least 0.05)."""
    left = max(0.05, left)
    if isinstance(current, (int, float)) and not isinstance(current, bool) and current > 0:
        return min(float(current), left)
    return left


class DeadlineWatchdog:
    """Keeps one HTTP request inside its total deadline, in every phase.

    A read timeout in requests and urllib3 restarts on every byte that arrives,
    so a server that trickles its status line, its headers or a compressed body
    can hold a request far past any deadline. While the watchdog is armed
    (``with watchdog:``), a timer shuts down, at ``deadline`` (a
    :func:`time.monotonic` value), every socket the request's connections
    reported to it. The blocked read then fails at once, and leaving the
    ``with`` block raises :class:`AssistantError` ("the provider did not answer
    in time") in place of whatever error, or cut-short body, the shutdown caused.
    Nothing is retried or sent again.

    How to use it, one watchdog per request:

    1. ``watchdog = DeadlineWatchdog(deadline)``.
    2. Make the request's connection pools report to it: mount
       :class:`DeadlineAdapter` on a new ``requests`` session, or, in an adapter
       that builds its own pool, return ``watchdog.watch_pool(pool)``. Use a new
       session (new pools) for every request, so a timer never touches the
       socket of a later request.
    3. Send the request *and* read the whole body inside ``with watchdog:``
       (for example with :func:`_read_limited`); return the result after the
       block.

    A connection that reports to the watchdog also connects within the deadline:
    its connect timeout, and the TLS handshake after it, are capped at the time
    left, and so is its socket timeout when it starts waiting for the answer.

    On Linux the shutdown wakes a waiting read at once. On Windows it does not
    wake a read that waits on an idle server, but the connection is reset as
    soon as the server sends another byte, and the capped socket timeout ends a
    wait on a server that sends nothing at all.
    """

    def __init__(self, deadline: float) -> None:
        self.deadline = float(deadline)
        self._lock = threading.Lock()
        self._sockets: list[Any] = []
        self._timer: threading.Timer | None = None
        self._fired = False
        self._closed = False

    @property
    def fired(self) -> bool:
        """Whether the deadline passed and the request's sockets were shut down."""
        return self._fired

    def remaining(self) -> float:
        """Seconds left until the deadline (negative once it has passed)."""
        return self.deadline - time.monotonic()

    def watch(self, sock: Any) -> None:
        """Shut ``sock`` down at the deadline; at once if the deadline has passed."""
        with self._lock:
            if self._closed:
                return
            if self._fired:
                _shutdown_socket(sock)
            elif all(known is not sock for known in self._sockets):
                self._sockets.append(sock)

    def watch_pool(self, pool: _PoolT) -> _PoolT:
        """Make every connection ``pool`` opens from now on report to this watchdog.

        Works for any urllib3 pool (plain, TLS, through a proxy) and keeps the
        pool's own settings, such as a pinned address or ``server_hostname``.
        """
        watched = _watched_connection_class(pool.ConnectionCls)
        if issubclass(watched, WatchedHTTPConnection):
            pool.ConnectionCls = cast(Any, watched)
            pool.conn_kw["okline_watchdog"] = self
        return pool

    def start(self) -> None:
        """Arm the timer (``with watchdog:`` does this)."""
        with self._lock:
            if self._timer is not None or self._closed:
                return
            timer = threading.Timer(max(0.0, self.remaining()), self._fire)
            timer.daemon = True
            self._timer = timer
        timer.start()

    def cancel(self) -> None:
        """Disarm the timer for good and forget the sockets.

        If the timer is shutting sockets down at that moment, this waits until
        it is done, so the caller can close them safely afterwards.
        """
        with self._lock:
            self._closed = True
            self._sockets.clear()
            timer = self._timer
        if timer is not None:
            timer.cancel()

    def _fire(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._fired = True
            for sock in self._sockets:
                _shutdown_socket(sock)

    def __enter__(self) -> DeadlineWatchdog:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.cancel()
        if self._fired and (exc is None or isinstance(exc, Exception)):
            status = exc.status_code if isinstance(exc, AssistantError) else None
            raise AssistantError(_NO_ANSWER_IN_TIME, status) from exc


class WatchedHTTPConnection(_Urllib3HTTPConnection):
    """A urllib3 connection that reports its socket to a :class:`DeadlineWatchdog`.

    The watchdog arrives as the ``okline_watchdog`` keyword, which a pool passes
    on from its ``conn_kw``; :meth:`DeadlineWatchdog.watch_pool` sets that up.
    Without a watchdog it behaves exactly like the plain urllib3 connection.
    :meth:`DeadlineWatchdog.watch_pool` also combines this class with other
    connection classes (TLS, SOCKS proxy).
    """

    def __init__(
        self, *args: Any, okline_watchdog: DeadlineWatchdog | None = None, **kwargs: Any
    ) -> None:
        self.okline_watchdog = okline_watchdog
        super().__init__(*args, **kwargs)

    def _new_conn(self) -> socket.socket:
        watchdog = self.okline_watchdog
        if watchdog is not None:
            left = watchdog.remaining()
            if left <= 0:
                raise urllib3.exceptions.ConnectTimeoutError(
                    self, "the deadline passed before the connection was made"
                )
            self.timeout = _capped_timeout(self.timeout, left)
        sock = super()._new_conn()
        if watchdog is not None:
            # A TLS handshake takes the socket timeout as its total time limit.
            try:
                sock.settimeout(_capped_timeout(sock.gettimeout(), watchdog.remaining()))
            except OSError:
                pass
            watchdog.watch(sock)
        return sock

    def connect(self) -> None:
        super().connect()
        if self.okline_watchdog is not None and self.sock is not None:
            # After a TLS handshake this is the TLS socket that replaced the plain one.
            self.okline_watchdog.watch(self.sock)

    def getresponse(self, *args: Any, **kwargs: Any) -> Any:
        watchdog = self.okline_watchdog
        if watchdog is not None:
            # The wait for the status line ends at the deadline even where shutting
            # the socket down does not wake a waiting read (Windows, idle server).
            left = watchdog.remaining()
            self.timeout = _capped_timeout(self.timeout, left)
            if self.sock is not None:
                try:
                    self.sock.settimeout(_capped_timeout(self.sock.gettimeout(), left))
                except OSError:
                    pass
        return super().getresponse(*args, **kwargs)


class WatchedHTTPSConnection(WatchedHTTPConnection, _Urllib3HTTPSConnection):
    """:class:`WatchedHTTPConnection` for https; TLS still checks the host name."""


_WATCHED_CLASSES: dict[type, type] = {
    _Urllib3HTTPConnection: WatchedHTTPConnection,
    _Urllib3HTTPSConnection: WatchedHTTPSConnection,
}
_WATCHED_CLASSES_LOCK = threading.Lock()


def _watched_connection_class(base: type) -> type:
    """``base`` combined with :class:`WatchedHTTPConnection` (made once per class)."""
    if issubclass(base, WatchedHTTPConnection) or not issubclass(base, _Urllib3HTTPConnection):
        return base
    with _WATCHED_CLASSES_LOCK:
        watched = _WATCHED_CLASSES.get(base)
        if watched is None:
            watched = type(f"Watched{base.__name__}", (WatchedHTTPConnection, base), {})
            _WATCHED_CLASSES[base] = watched
        return watched


class DeadlineAdapter(HTTPAdapter):
    """A requests adapter whose connections report their sockets to ``watchdog``.

    Everything else is the standard :class:`requests.adapters.HTTPAdapter`
    (proxies from the environment included); it makes no retries.
    """

    def __init__(self, watchdog: DeadlineWatchdog, **kwargs: Any) -> None:
        self.watchdog = watchdog
        kwargs.setdefault("max_retries", 0)
        super().__init__(**kwargs)

    def get_connection_with_tls_context(
        self,
        request: requests.PreparedRequest,
        verify: Any,
        proxies: Mapping[str, str] | None = None,
        cert: Any = None,
    ) -> ConnectionPool:
        pool = super().get_connection_with_tls_context(
            request, verify, proxies=proxies, cert=cert
        )
        if isinstance(pool, HTTPConnectionPool):
            self.watchdog.watch_pool(pool)
        return pool


def default_transport(
    method: str,
    url: str,
    *,
    headers: Mapping[str, str],
    json_body: Any = None,
    timeout: float = CHAT_DEADLINE_SECONDS,
    max_bytes: int = MAX_AI_RESPONSE_BYTES,
) -> tuple[int, dict[str, str], bytes]:
    """Send one request to a fixed-origin AI provider with :mod:`requests`.

    Redirects are refused, the body is streamed and capped at ``max_bytes``, the
    connect timeout is 5 seconds and ``timeout`` is a hard total deadline for the
    whole request: connecting, the status line and headers, and the body. A
    :class:`DeadlineWatchdog` ends a provider that trickles any of them. No
    request is retried, so a POST is never sent again after a timeout. Proxies
    from the environment are used; ``~/.netrc`` is not.
    """
    total = max(1.0, _safe_float(timeout, CHAT_DEADLINE_SECONDS))
    deadline = time.monotonic() + total
    request_headers: CaseInsensitiveDict[str] = CaseInsensitiveDict(
        {"Accept": "application/json", "Accept-Encoding": "identity"}
    )
    request_headers.update(headers)
    data: bytes | None = None
    if json_body is not None:
        try:
            data = json.dumps(json_body, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise AssistantError("the request could not be written as JSON") from exc
        request_headers.setdefault("Content-Type", "application/json")
    watchdog = DeadlineWatchdog(deadline)
    adapter = DeadlineAdapter(watchdog)
    # requests.sessions.Session, not requests.Session: what requests.request uses.
    session = requests.sessions.Session()
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    response: requests.Response | None = None
    try:
        with watchdog:
            try:
                response = session.request(
                    method,
                    url,
                    headers=request_headers,
                    data=data,
                    timeout=(min(CONNECT_TIMEOUT_SECONDS, total), total),
                    allow_redirects=False,
                    stream=True,
                    auth=_keep_explicit_auth,
                )
            except requests.Timeout as exc:
                raise AssistantError(_NO_ANSWER_IN_TIME) from exc
            except (requests.RequestException, OSError, ValueError) as exc:
                raise AssistantError(f"cannot connect ({_exception_summary(exc)})") from exc
            status = int(response.status_code)
            if 300 <= status < 400:
                raise AssistantError(
                    f"the provider answered with a redirect (HTTP {status}), which is not followed",
                    status,
                )
            body = _read_limited(
                response, max_bytes=max_bytes, deadline=deadline, status=status
            )
        return status, dict(response.headers), body
    finally:
        if response is not None:
            response.close()
        session.close()


def _read_limited(
    response: requests.Response,
    *,
    max_bytes: int,
    deadline: float,
    status: int,
    decode: bool = True,
) -> bytes:
    """Read a streamed answer: at most ``max_bytes``, and only until ``deadline``.

    ``decode=False`` returns the body as sent, without undoing a
    Content-Encoding, for a caller that has refused compressed answers. Keep the
    call inside the request's ``with watchdog:`` block, so the watchdog also ends
    a read that one slow chunk would otherwise stretch.
    """
    limit = max(1, _safe_int(max_bytes))
    declared = str(response.headers.get("Content-Length") or "").strip()
    if declared.isdigit() and int(declared) > limit:
        raise AssistantError(_too_large(limit), status)
    raw = response.raw
    read = getattr(raw, "read1", None) or raw.read
    chunks: list[bytes] = []
    size = 0
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise AssistantError(_NO_ANSWER_IN_TIME, status)
        _limit_socket_wait(raw, remaining)
        try:
            chunk = read(_READ_CHUNK_BYTES, decode_content=decode)
        except (urllib3.exceptions.ReadTimeoutError, socket.timeout, TimeoutError) as exc:
            raise AssistantError(_NO_ANSWER_IN_TIME, status) from exc
        except (urllib3.exceptions.HTTPError, OSError, ValueError) as exc:
            raise AssistantError(
                f"the connection broke while reading the answer ({_exception_summary(exc)})",
                status,
            ) from exc
        if not chunk:
            break
        size += len(chunk)
        if size > limit:
            raise AssistantError(_too_large(limit), status)
        chunks.append(chunk)
    return b"".join(chunks)


def _limit_socket_wait(raw: Any, remaining: float) -> None:
    # Each socket read may wait only for what is left of the deadline, so a
    # provider that trickles bytes cannot stretch the call past it.
    sock = getattr(getattr(raw, "connection", None), "sock", None)
    settimeout = getattr(sock, "settimeout", None)
    if callable(settimeout):
        try:
            settimeout(max(0.05, remaining))
        except OSError:
            pass


def _too_large(limit: int) -> str:
    if limit >= 1024 * 1024:
        size = f"{limit / (1024 * 1024):g} MB"
    elif limit >= 1024:
        size = f"{limit / 1024:g} KB"
    else:
        size = f"{limit} bytes"
    return f"the answer is larger than {size}"


def _exception_summary(exc: BaseException) -> str:
    text = " ".join(str(exc).split())
    name = type(exc).__name__
    return f"{name}: {text[:200]}" if text else name


# --- shared response helpers --------------------------------------------------

_NOT_JSON: Any = object()
"""What :func:`_decode_json` returns for a body that is not JSON."""


def _decode_json(raw: bytes) -> Any:
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError):
        return _NOT_JSON


def _status_phrase(status: int) -> str:
    try:
        return http.HTTPStatus(status).phrase
    except ValueError:
        return "request failed"


def _error_detail(data: Any) -> tuple[str, str]:
    """The provider's own error message, and every text in the error body.

    The first is short and meant for people. The second is lower case and is
    used to recognise which request parameter a provider refused.
    """
    if data is _NOT_JSON:
        return "", ""
    texts: list[str] = []
    _collect_texts(data, texts, 0)
    full = " ".join(texts)[:4000].lower()
    try:
        short = " ".join(_first_message(data).split())
    except RecursionError:
        short = ""
    return short[:300], full


def _collect_texts(value: Any, out: list[str], depth: int) -> None:
    if depth > 6 or len(out) > 200:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, dict):
        for item in value.values():
            _collect_texts(item, out, depth + 1)
    elif isinstance(value, list):
        for item in value[:50]:
            _collect_texts(item, out, depth + 1)


def _first_message(data: Any) -> str:
    if isinstance(data, str):
        return data
    if not isinstance(data, dict):
        return ""
    error = data.get("error")
    if isinstance(error, str) and error.strip():
        return error
    if isinstance(error, dict):
        found = _message_from(error)
        if found:
            return found
    return _message_from(data)


def _message_from(container: dict[str, Any]) -> str:
    for key in ("message", "msg", "detail", "error_msg"):
        value = container.get(key)
        if isinstance(value, str) and value.strip():
            return value
        if isinstance(value, (dict, list)):
            flattened = _flatten_details(value)
            if flattened:
                return flattened
    return ""


_MAX_DETAIL_DEPTH = 4


def _flatten_details(value: Any, depth: int = 0) -> str:
    # FastAPI and Mistral answer 422 with a list of {"loc": [...], "msg": "..."}.
    # The depth limit keeps a deeply nested error body from exhausting the stack.
    if depth > _MAX_DETAIL_DEPTH:
        return ""
    items = value if isinstance(value, list) else [value]
    parts: list[str] = []
    for item in items[:3]:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            message = item.get("msg") or item.get("message") or item.get("detail")
            if isinstance(message, (dict, list)):
                message = _flatten_details(message, depth + 1)
            if not isinstance(message, str) or not message:
                continue
            loc = item.get("loc")
            if isinstance(loc, list) and loc:
                message = f"{'.'.join(str(part) for part in loc)}: {message}"
            parts.append(message)
    return "; ".join(parts)


def _scrub_secret(text: str, secret: str) -> str:
    return text.replace(secret, "****") if secret and len(secret) >= 4 else text


def _safe_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        try:
            number = int(float(value))
        except (TypeError, ValueError, OverflowError):
            return 0
    return max(0, number)


def _safe_float(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _clamp_timeout(value: Any) -> float:
    return max(5.0, min(_safe_float(value, CHAT_DEADLINE_SECONDS), 600.0))


def _call_timeout(value: float | None, default: float) -> float:
    """``value`` as one call's deadline (1 to 600 seconds), or ``default`` when None."""
    if value is None:
        return default
    return max(1.0, min(_safe_float(value, default), 600.0))


def _vector_size_problem(values: Any) -> str:
    """Why a search vector is refused for its size, or ``""`` when it is not."""
    if isinstance(values, list) and len(values) > MAX_EMBEDDING_DIMENSIONS:
        return (
            f"a search vector of {len(values)} numbers "
            f"(at most {MAX_EMBEDDING_DIMENSIONS} are accepted)"
        )
    return ""


def _float_vector(values: Any) -> list[float] | None:
    if not isinstance(values, list) or not values or len(values) > MAX_EMBEDDING_DIMENSIONS:
        return None
    vector: list[float] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        number = float(value)
        if not math.isfinite(number):
            return None
        vector.append(number)
    return vector


# --- Gemini -------------------------------------------------------------------

GEMINI_API_BASE_URL = "https://generativelanguage.googleapis.com"
DEFAULT_GEMINI_CHAT_MODEL = "gemini-3.8-flash"
DEFAULT_GEMINI_EMBEDDING_MODEL = "gemini-embedding-001"
GEMINI_EMBED_BATCH = 16
_GEMINI_BLOCKED_FINISH = frozenset(
    {"SAFETY", "PROHIBITED_CONTENT", "BLOCKLIST", "SPII", "RECITATION"}
)
"""finishReason values that mean Gemini's filters stopped the answer."""


class GeminiClient:
    """Google Gemini chat and embeddings over the generativelanguage API.

    Chat calls generateContent with a response schema. The key goes in the
    ``x-goog-api-key`` header, never in the URL. ``timeout`` is the total time
    for one chat call; embeddings and model lists get at least 120 seconds per
    request, unless ``embed(timeout=...)`` or ``list_models(timeout=...)`` sets
    their own deadline.
    """

    label = "Gemini"

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = GEMINI_API_BASE_URL,
        timeout: float = CHAT_DEADLINE_SECONDS,
        transport: Transport | None = None,
        embed_batch: int = GEMINI_EMBED_BATCH,
    ) -> None:
        self.api_key = (api_key or "").strip()
        self.base_url = (base_url or GEMINI_API_BASE_URL).rstrip("/")
        self.timeout = _clamp_timeout(timeout)
        self.long_timeout = max(self.timeout, LONG_DEADLINE_SECONDS)
        self.transport: Transport = transport or default_transport
        self.embed_batch = max(1, _safe_int(embed_batch) or GEMINI_EMBED_BATCH)

    def _request(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None = None,
        *,
        timeout: float,
        max_bytes: int,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise GeminiError("Gemini API key is not configured.", code="ai_key_required")
        url = f"{self.base_url}/v1beta/{path.lstrip('/')}"
        headers = {"Accept": "application/json", "x-goog-api-key": self.api_key}
        try:
            status, _headers, raw = self.transport(
                method,
                url,
                headers=headers,
                json_body=payload,
                timeout=timeout,
                max_bytes=max_bytes,
            )
            status = int(status)
        except AssistantError as exc:
            raise GeminiError(
                _scrub_secret(f"Gemini request failed: {exc}", self.api_key),
                exc.status_code,
                exc.code,
            ) from exc
        except Exception as exc:
            raise GeminiError(f"Gemini request failed ({type(exc).__name__}).") from exc
        data = _decode_json(raw)
        if not 200 <= status < 300:
            short, _full = _error_detail(data)
            detail = short or _status_phrase(status)
            lowered = detail.lower()
            code = None
            if status == 401 or (
                "api key" in lowered
                and any(word in lowered for word in ("not valid", "invalid", "expired"))
            ):
                code = "ai_key_invalid"
            raise GeminiError(
                _scrub_secret(f"Gemini error {status}: {detail}", self.api_key), status, code
            )
        if not isinstance(data, dict):
            raise GeminiError(f"Gemini returned invalid JSON (HTTP {status}).", status)
        return data

    def list_models(self, timeout: float | None = None) -> list[dict[str, Any]]:
        data = self._request(
            "GET",
            "models?pageSize=200",
            timeout=_call_timeout(timeout, self.long_timeout),
            max_bytes=MAX_AI_MODEL_LIST_BYTES,
        )
        models = data.get("models")
        result: list[dict[str, Any]] = []
        for item in models if isinstance(models, list) else []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").removeprefix("models/")
            raw_methods = item.get("supportedGenerationMethods")
            methods = raw_methods if isinstance(raw_methods, list) else []
            if not name:
                continue
            result.append(
                {
                    "name": name,
                    "size": 0,
                    "family": "gemini",
                    "parameterSize": "",
                    "quantization": "",
                    "chat": "generateContent" in methods,
                    "embedding": "embedContent" in methods,
                }
            )
        return result

    def embed(
        self, model: str, texts: list[str], timeout: float | None = None
    ) -> list[list[float]]:
        model = ((model or "").strip() or DEFAULT_GEMINI_EMBEDDING_MODEL).removeprefix(
            "models/"
        )
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.embed_batch):
            batch = texts[start : start + self.embed_batch]
            data = self._request(
                "POST",
                f"models/{model}:batchEmbedContents",
                {
                    "requests": [
                        {"model": f"models/{model}", "content": {"parts": [{"text": text}]}}
                        for text in batch
                    ]
                },
                timeout=_call_timeout(timeout, self.long_timeout),
                max_bytes=MAX_AI_EMBEDDING_BYTES,
            )
            embeddings = data.get("embeddings")
            if not isinstance(embeddings, list) or len(embeddings) != len(batch):
                raise GeminiError("Gemini returned invalid embeddings.")
            for item in embeddings:
                values = item.get("values") if isinstance(item, dict) else None
                problem = _vector_size_problem(values)
                if problem:
                    raise GeminiError(f"Gemini returned {problem}.")
                vector = _float_vector(values)
                if vector is None:
                    raise GeminiError("Gemini returned an empty or invalid embedding vector.")
                vectors.append(vector)
        return vectors

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
        model = ((model or "").strip() or DEFAULT_GEMINI_CHAT_MODEL).removeprefix("models/")
        prompt = _assistant_prompt(question, context, strict_knowledge)
        data = self._request(
            "POST",
            f"models/{model}:generateContent",
            {
                "systemInstruction": {"parts": [{"text": (system_prompt or "").strip()}]},
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {
                    "temperature": max(0.0, min(_safe_float(temperature, 0.2), 2.0)),
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
            },
            timeout=self.timeout,
            max_bytes=MAX_AI_RESPONSE_BYTES,
        )
        candidates = data.get("candidates")
        first = candidates[0] if isinstance(candidates, list) and candidates else {}
        content = first.get("content") if isinstance(first, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        text = "".join(
            str(part.get("text") or "")
            for part in (parts if isinstance(parts, list) else [])
            if isinstance(part, dict)
        )
        raw_usage = data.get("usageMetadata")
        usage: dict[str, Any] = raw_usage if isinstance(raw_usage, dict) else {}
        tokens = {
            "prompt_tokens": _safe_int(usage.get("promptTokenCount")),
            "output_tokens": _safe_int(usage.get("candidatesTokenCount")),
        }
        feedback = data.get("promptFeedback")
        prompt_blocked = isinstance(feedback, dict) and bool(feedback.get("blockReason"))
        finish = (
            str(first.get("finishReason") or "").upper() if isinstance(first, dict) else ""
        )
        blocked = finish in _GEMINI_BLOCKED_FINISH
        if prompt_blocked or (blocked and not text.strip()):
            # Gemini's filters stopped the question or the answer: like a refusal
            # from the other providers, the pipeline sends the shop's fallback text.
            return _structured_answer({}, model=model, **tokens)
        try:
            parsed = _parse_json_object(text)
        except AssistantError as exc:
            if blocked:
                return _structured_answer({}, model=model, **tokens)
            if finish == "MAX_TOKENS":
                raise GeminiError("Gemini stopped before finishing the answer.") from exc
            raise GeminiError("Gemini did not return a structured answer.") from exc
        return _structured_answer(parsed, model=model, **tokens)


# --- prompt and answer parsing ------------------------------------------------


def _assistant_prompt(question: str, context: str, strict_knowledge: bool) -> str:
    policy = (
        "Use only the supplied knowledge. If it is insufficient, set canAnswer to false."
        if strict_knowledge
        else "Use the supplied knowledge when relevant. You may use general knowledge."
    )
    return (
        f"{policy}\n\n"
        "Return JSON with answer (a string), canAnswer (true or false) and confidence "
        "(a number from 0 to 1). Do not include markdown fences.\n\n"
        f"KNOWLEDGE:\n{context or '(no matching knowledge)'}\n\n"
        f"QUESTION:\n{question}"
    )


def _strict_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "yes", "1"}
    return False


def _structured_answer(
    parsed: dict[str, Any], *, model: str, prompt_tokens: int, output_tokens: int
) -> dict[str, Any]:
    raw_answer = parsed.get("answer")
    if isinstance(raw_answer, str):
        answer = raw_answer.strip()
    elif isinstance(raw_answer, (int, float)) and not isinstance(raw_answer, bool):
        answer = str(raw_answer)
    else:
        answer = ""
    can_answer = _strict_true(parsed.get("canAnswer")) and bool(answer)
    confidence = max(0.0, min(_safe_float(parsed.get("confidence"), 0.0), 1.0))
    return {
        "answer": answer,
        "canAnswer": can_answer,
        "confidence": confidence,
        "model": model,
        "promptEvalCount": prompt_tokens,
        "evalCount": output_tokens,
    }


_NO_STRUCTURED_ANSWER = "The AI did not return a structured answer."
_FENCED_BLOCK = re.compile(
    r"```[ \t]*(?:json|javascript|js)?[ \t]*\n?(.*?)```", re.DOTALL | re.I
)


def _parse_json_object(value: str) -> dict[str, Any]:
    """The JSON object in a model's answer.

    Accepts plain JSON, a Markdown code fence anywhere in the text, or the first
    complete ``{...}`` object inside surrounding text.
    """
    text = (value or "").strip().lstrip("﻿")
    if not text:
        raise AssistantError(_NO_STRUCTURED_ANSWER)
    candidates = [text, *(match.group(1).strip() for match in _FENCED_BLOCK.finditer(text))]
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (ValueError, RecursionError):
            continue
        if isinstance(data, dict):
            return data
    found = _first_json_object(text)
    if found is None:
        raise AssistantError(_NO_STRUCTURED_ANSWER)
    return found


def _first_json_object(text: str) -> dict[str, Any] | None:
    decoder = json.JSONDecoder()
    start = text.find("{")
    attempts = 0
    while start != -1 and attempts < 64:
        attempts += 1
        try:
            data, _end = decoder.raw_decode(text, start)
        except (ValueError, RecursionError):
            data = None
        if isinstance(data, dict):
            return data
        start = text.find("{", start + 1)
    return None


# --- knowledge chunks and ranking ---------------------------------------------


def chunk_markdown(value: str, *, max_chars: int = 1200) -> list[str]:
    text = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    blocks = [part.strip() for part in re.split(r"\n\s*\n", text) if part.strip()]
    chunks: list[str] = []
    current = ""
    heading = ""
    for block in blocks:
        if block.startswith("#"):
            heading = block.splitlines()[0].strip()
        candidate = f"{current}\n\n{block}".strip() if current else block
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
        prefix = f"{heading}\n\n" if heading and not block.startswith("#") else ""
        remaining = block
        while len(prefix) + len(remaining) > max_chars:
            limit = max(200, max_chars - len(prefix))
            cut = remaining.rfind(" ", 0, limit)
            if cut < limit // 2:
                cut = limit
            chunks.append((prefix + remaining[:cut]).strip())
            remaining = remaining[cut:].strip()
        current = (prefix + remaining).strip()
    if current:
        chunks.append(current)
    return chunks


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    try:
        dot = sum(a * b for a, b in zip(left, right))
        left_norm = math.sqrt(sum(value * value for value in left))
        right_norm = math.sqrt(sum(value * value for value in right))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not left_norm or not right_norm or not math.isfinite(dot):
        return 0.0
    return dot / (left_norm * right_norm)


def lexical_score(query: str, text: str) -> float:
    query_terms = _lexical_terms(query)
    text_terms = _lexical_terms(text)
    if not query_terms or not text_terms:
        return 0.0
    return len(query_terms & text_terms) / math.sqrt(len(query_terms) * len(text_terms))


# Thai vowel and tone marks are combining characters (Unicode category Mn), which
# \w does not match. The whole Thai block counts as word characters, so a Thai
# word stays in one piece and its marks stay in the 3-letter groups.
_THAI_BLOCK = "฀-๿"
_WORD_RUN = re.compile(rf"(?:[^\W_]|[{_THAI_BLOCK}])+")
_NON_WORD_RUN = re.compile(rf"(?:[^\w{_THAI_BLOCK}]|_)+")


def _lexical_terms(value: str) -> set[str]:
    normalized = value.casefold()
    words = set(_WORD_RUN.findall(normalized))
    compact = _NON_WORD_RUN.sub("", normalized)
    grams = {compact[i : i + 3] for i in range(max(0, len(compact) - 2))}
    return words | grams


def rank_chunks(
    question: str,
    chunks: list[dict[str, Any]],
    *,
    query_embedding: list[float] | None = None,
    top_k: int = 5,
) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    for chunk in chunks:
        text = str(chunk.get("text") or "")
        embedding = chunk.get("embedding")
        score = (
            cosine_similarity(query_embedding, embedding)
            if query_embedding is not None and isinstance(embedding, list)
            else lexical_score(question, text)
        )
        if score <= 0:
            continue
        ranked.append({**chunk, "score": round(score, 6)})
    ranked.sort(key=lambda item: float(item.get("score") or 0), reverse=True)
    return ranked[: max(1, min(int(top_k), 12))]
