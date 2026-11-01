"""OA Manager's live event stream (server-sent events).

The OA Manager page asks for a short-lived stream token, then keeps one
EventSource open. Events are named (``init``, ``ping``, ``chat``,
``botUnreadChatCount`` ...) and carry JSON. Passing the last event id back
on reconnect makes the server replay what was missed.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

import requests

from .client import OAManagerClient, OAManagerError

STREAM_BASE = "https://chat-streaming-api.line.biz"
PING_SECONDS = 59
# Renew the stream token this long before LINE says it expires.
TOKEN_MARGIN_SECONDS = 60


@dataclass
class SseEvent:
    type: str
    data: str
    id: str


def parse_sse(lines: Iterable[str]) -> Iterator[SseEvent]:
    """Server-sent events from decoded lines (https://html.spec.whatwg.org/#event-stream)."""
    event_type = ""
    data: list[str] = []
    event_id = ""
    for raw in lines:
        line = raw.rstrip("\r") if isinstance(raw, str) else str(raw)
        if not line:
            if data or event_type:
                yield SseEvent(event_type or "message", "\n".join(data), event_id)
            event_type, data = "", []
            continue
        if line.startswith(":"):
            continue
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "event":
            event_type = value
        elif name == "data":
            data.append(value)
        elif name == "id" and "\0" not in value:
            event_id = value


class OAManagerStream:
    """One OA's events; ``last_event_id`` survives reconnects."""

    def __init__(
        self,
        client: OAManagerClient,
        bot_id: str,
        *,
        last_event_id: str = "",
        ping_seconds: int = PING_SECONDS,
    ) -> None:
        self.client = client
        self.bot_id = bot_id
        self.last_event_id = last_event_id
        self.ping_seconds = ping_seconds
        self.connection_id = ""
        self._response: requests.Response | None = None
        self._lock = threading.Lock()

    def close(self) -> None:
        with self._lock:
            response, self._response = self._response, None
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

    def events(self, stop: threading.Event) -> Iterator[dict[str, Any]]:
        """Events of one connection, until it ends, its token runs out or ``stop``."""
        token = self.client.streaming_token(self.bot_id)
        stream_token = str(token.get("streamingApiToken") or "")
        if not stream_token:
            raise OAManagerError("LINE OA Manager gave no stream token.", status=502)
        base = str(token.get("streamingApiBaseUrl") or STREAM_BASE).rstrip("/")
        version = str(token.get("streamingApiVersion") or "v1")
        expires_ms = int(token.get("expiredAt") or 0)
        renew_at = (expires_ms / 1000 - TOKEN_MARGIN_SECONDS) if expires_ms else None
        try:
            response = self.client.stream_session().get(
                f"{base}/api/{version}/sse",
                params={
                    "token": stream_token,
                    "deviceType": "",
                    "clientType": "PC",
                    "pingSecs": str(self.ping_seconds),
                    "lastEventId": self.last_event_id or "",
                },
                headers={"Accept": "text/event-stream", "Referer": f"{self.client.base_url}/"},
                stream=True,
                timeout=(15, self.ping_seconds * 2 + 15),
            )
        except requests.RequestException as exc:
            raise OAManagerError(
                f"Could not reach the LINE OA Manager stream ({type(exc).__name__}).",
                network=True,
            ) from None
        if response.status_code in (401, 403):
            response.close()
            raise OAManagerError("The LINE OA Manager session ended.", status=401)
        if response.status_code != 200:
            response.close()
            raise OAManagerError(
                f"The LINE OA Manager stream answered with HTTP {response.status_code}.",
                status=int(response.status_code),
            )
        response.encoding = "utf-8"
        with self._lock:
            self._response = response
        try:
            for event in parse_sse(response.iter_lines(decode_unicode=True)):
                if stop.is_set():
                    return
                if event.type != "ping" and event.id:
                    self.last_event_id = event.id
                try:
                    payload = json.loads(event.data) if event.data else {}
                except ValueError:
                    payload = {"raw": event.data}
                if event.type == "init" and isinstance(payload, dict):
                    self.connection_id = str(payload.get("connectionId") or "")
                    if self.connection_id:
                        try:
                            # Like a background tab: connected, nobody looking.
                            self.client.streaming_state(self.bot_id, self.connection_id, idle=True)
                        except OAManagerError:
                            pass
                if event.type != "ping":
                    yield {"type": event.type, "data": payload, "id": event.id}
                if renew_at and time.time() >= renew_at:
                    return
        except Exception as exc:
            if stop.is_set():
                return  # close() from another thread breaks the read; that is the stop
            if not isinstance(exc, (requests.RequestException, OSError, ValueError)):
                raise
            raise OAManagerError(
                f"The LINE OA Manager stream dropped ({type(exc).__name__}).", network=True
            ) from None
        finally:
            self.close()
