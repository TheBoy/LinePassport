"""Request/response recording — capture the *full* exchange for every endpoint.

Every call OkLine makes can be recorded as an :class:`Exchange` (method, URL,
endpoint, request headers + body, status, response headers + body, timing).
You can then paste / inspect / export them:

>>> api = OkLine(access_token="...")          # record=True by default
>>> api.get_profile()
>>> print(api.last.pretty())                  # one HTTP-transcript
>>> print(api.dump())                         # every call this session
>>> api.save_log("session.har", fmt="har")    # import into a browser devtools

Sensitive values (access token, X-Hmac, passwords, refresh tokens) are masked
by default; pass ``redact=False`` to reveal them.
"""

from __future__ import annotations

import json
import os
import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Header / body keys whose values are masked unless redaction is disabled.
_SECRET_HEADERS = {
    "x-line-access",
    "x-hmac",
    "authorization",
    "x-line-channeltoken",
    "cookie",
    "set-cookie",
}
_SECRET_BODY_KEYS = {
    "password",
    "passwordconfirm",
    "currentpassword",
    "newpassword",
    "accesstoken",
    "refreshtoken",
    "encryptedaccesstoken",
    "apikey",
    "api_key",
    "token",
    "secret",
    "encryptedkeychain",
    "enc_km",
    "verifier",
    "certificate",
}

_MASK = "<redacted>"


def _redact_headers(headers: dict[str, str], redact: bool) -> dict[str, str]:
    if not redact:
        return dict(headers)
    out = {}
    for k, v in headers.items():
        out[k] = _MASK if k.lower() in _SECRET_HEADERS and v else v
    return out


def _redact_body(body: Any, redact: bool) -> Any:
    if not redact:
        return body
    if isinstance(body, dict):
        return {
            k: (
                _MASK
                if str(k).casefold() in _SECRET_BODY_KEYS and v
                else _redact_body(v, redact)
            )
            for k, v in body.items()
        }
    if isinstance(body, list):
        return [_redact_body(x, redact) for x in body]
    return body


def _redact_url(url: str, redact: bool) -> str:
    if not redact:
        return url
    try:
        parsed = urlsplit(url)
        query = urlencode(
            [
                (key, _MASK if key.casefold() in _SECRET_BODY_KEYS and value else value)
                for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            ]
        )
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, query, parsed.fragment))
    except (TypeError, ValueError):
        return url


def _redact_text(text: str, redact: bool) -> str:
    if not redact or not text:
        return text
    try:
        return _fmt(_redact_body(json.loads(text), True))
    except (TypeError, ValueError):
        value = re.sub(
            r"(?i)\b(Bearer|Key)\s+[A-Za-z0-9._~+/=-]+",
            lambda match: f"{match.group(1)} {_MASK}",
            text,
        )
        for key in sorted(_SECRET_BODY_KEYS, key=len, reverse=True):
            value = re.sub(
                rf'(?i)(["\']?{re.escape(key)}["\']?\s*[:=]\s*["\']?)([^"\'\s,;&}}]+)',
                rf"\1{_MASK}",
                value,
            )
        value = re.sub(
            r"https?://[^\s<>\"']+",
            lambda match: _redact_url(match.group(0), True),
            value,
        )
        return value


@dataclass
class Exchange:
    """A single recorded HTTP request/response round-trip."""

    seq: int
    method: str
    url: str
    path: str
    endpoint: str | None  # Namespace.Service.method
    request_headers: dict[str, str] = field(default_factory=dict)
    request_body: Any = None  # parsed (list/dict) or str
    status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    response_body: Any = None  # decoded/unwrapped result
    response_text: str = ""  # raw response body text
    duration_ms: float = 0.0
    ok: bool = True
    error: str | None = None
    started_at: float | None = None  # epoch seconds

    # -- formatting ----------------------------------------------------------
    def pretty(self, *, redact: bool = True, max_body: int = 20000) -> str:
        """Return a human-readable HTTP-transcript for this exchange."""
        rh = _redact_headers(self.request_headers, redact)
        rb = _redact_body(self.request_body, redact)
        lines = []
        tag = "OK" if self.ok else "ERR"
        head = f"#{self.seq} [{tag}] {self.method} {self.path}"
        if self.endpoint:
            head += f"   ({self.endpoint})"
        lines.append(head)
        lines.append("=" * min(len(head), 70))
        lines.append(f"  -> {self.method} {_redact_url(self.url, redact)}")
        for k, v in rh.items():
            lines.append(f"  >  {k}: {v}")
        if rb is not None and rb != "":
            lines.append("  >  body: " + _truncate(_fmt(rb), max_body))
        status = self.status if self.status is not None else "-"
        lines.append(f"  <- HTTP {status}   {self.duration_ms:.0f} ms")
        for k, v in self.response_headers.items():
            if k.lower() in ("content-type", "x-line-resp-code", "content-length"):
                lines.append(f"  <  {k}: {v}")
        body_out = (
            _fmt(_redact_body(self.response_body, redact))
            if self.response_body is not None
            else _redact_text(self.response_text, redact)
        )
        lines.append("  <  resp: " + _truncate(body_out, max_body))
        if self.error:
            lines.append(f"  !  error: {_redact_text(self.error, redact)}")
        lines.append("")
        return "\n".join(lines)

    def to_dict(self, *, redact: bool = True) -> dict:
        return {
            "seq": self.seq,
            "method": self.method,
            "url": _redact_url(self.url, redact),
            "path": self.path,
            "endpoint": self.endpoint,
            "request_headers": _redact_headers(self.request_headers, redact),
            "request_body": _redact_body(self.request_body, redact),
            "status": self.status,
            "response_headers": _redact_headers(self.response_headers, redact),
            "response_body": _redact_body(self.response_body, redact),
            "duration_ms": self.duration_ms,
            "ok": self.ok,
            "error": _redact_text(self.error, redact) if self.error else self.error,
            "started_at": self.started_at,
        }

    def to_har_entry(self, *, redact: bool = True) -> dict:
        rh = _redact_headers(self.request_headers, redact)
        response_headers = _redact_headers(self.response_headers, redact)
        body_s = _fmt(_redact_body(self.request_body, redact))
        response_s = (
            _fmt(_redact_body(self.response_body, redact))
            if self.response_body is not None
            else _redact_text(self.response_text, redact)
        )
        return {
            "startedDateTime": _iso(self.started_at),
            "time": self.duration_ms,
            "request": {
                "method": self.method,
                "url": _redact_url(self.url, redact),
                "httpVersion": "HTTP/2",
                "headers": [{"name": k, "value": str(v)} for k, v in rh.items()],
                "queryString": [],
                "cookies": [],
                "postData": {"mimeType": "application/json", "text": body_s},
                "headersSize": -1,
                "bodySize": len(body_s),
            },
            "response": {
                "status": self.status or 0,
                "statusText": "" if self.ok else "ERROR",
                "httpVersion": "HTTP/2",
                "headers": [
                    {"name": k, "value": str(v)} for k, v in response_headers.items()
                ],
                "cookies": [],
                "content": {
                    "size": len(response_s),
                    "mimeType": "application/json",
                    "text": response_s,
                },
                "redirectURL": "",
                "headersSize": -1,
                "bodySize": len(response_s),
            },
            "cache": {},
            "timings": {"send": 0, "wait": self.duration_ms, "receive": 0},
        }


class Recorder:
    """Ring buffer of :class:`Exchange` objects."""

    def __init__(self, capacity: int = 500, redact: bool = True) -> None:
        self.capacity = capacity
        self.redact = redact
        # a bounded deque drops the oldest entry in O(1) on append — no per-record
        # O(n) list re-slice once we hit capacity.
        self._entries: deque[Exchange] = deque(maxlen=capacity)

    def record(self, ex: Exchange) -> None:
        self._entries.append(ex)

    @property
    def entries(self) -> list[Exchange]:
        return list(self._entries)

    @property
    def last(self) -> Exchange | None:
        return self._entries[-1] if self._entries else None

    def clear(self) -> None:
        self._entries.clear()

    def find(self, endpoint: str) -> list[Exchange]:
        return [e for e in self._entries if e.endpoint == endpoint]

    # -- exports -------------------------------------------------------------
    def dump_text(self, *, redact: bool | None = None) -> str:
        r = self.redact if redact is None else redact
        return "\n".join(e.pretty(redact=r) for e in self._entries)

    def to_har(self, *, redact: bool | None = None) -> dict:
        r = self.redact if redact is None else redact
        return {
            "log": {
                "version": "1.2",
                "creator": {"name": "OkLine", "version": "1.0.0"},
                "entries": [e.to_har_entry(redact=r) for e in self._entries],
            }
        }

    def to_json(self, *, redact: bool | None = None) -> str:
        r = self.redact if redact is None else redact
        return json.dumps(
            [e.to_dict(redact=r) for e in self._entries], ensure_ascii=False, indent=2
        )

    def save(self, path: str, *, fmt: str = "text", redact: bool | None = None) -> None:
        if fmt == "har":
            data = json.dumps(self.to_har(redact=redact), ensure_ascii=False, indent=2)
        elif fmt == "json":
            data = self.to_json(redact=redact)
        else:
            data = self.dump_text(redact=redact)
        parent = os.path.dirname(os.path.abspath(path))
        os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


# -- helpers ----------------------------------------------------------------
def _fmt(value: Any) -> str:
    if isinstance(value, (dict, list)):
        try:
            return json.dumps(value, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            return str(value)
    return "" if value is None else str(value)


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f"... [{len(s) - limit} more chars]"


def _iso(epoch: float | None) -> str:
    if not epoch:
        return "1970-01-01T00:00:00.000Z"
    import datetime

    return (
        datetime.datetime.fromtimestamp(epoch, datetime.timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S."
        )
        + f"{int((epoch % 1) * 1000):03d}Z"
    )
