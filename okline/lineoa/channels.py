"""LINE Official Accounts as a customer chat channel, through OA Manager.

A shop signs in to OA Manager once by QR; each OA it manages can become an
account next to its LINE and Telegram accounts. One worker per OA keeps the
OA Manager event stream open, the way an open OA Manager tab does, and hands
every one-to-one message to the host, which stores it in the CRM. On first
connect, and whenever the stream had to be rebuilt, recent chats are copied
in without alerts. Chat ids are the ids OA Manager uses (``U`` + 32 hex).
"""

from __future__ import annotations

import re
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any, Protocol

from .client import OAManagerClient, OAManagerError
from .stream import OAManagerStream

USER_CHAT_RE = re.compile(r"^U[0-9a-f]{32}$")
AUTO_RESPONSE_BIZ_ID = "__AUTO_RESPONSE"
CONTENT_TYPES = {
    "text": 0,
    "textV2": 0,
    "image": 1,
    "video": 2,
    "audio": 3,
    "sticker": 7,
    "file": 14,
    "location": 15,
}
SKIPPED_TYPES = frozenset({"unsent", "unread"})
STREAM_BACKOFF_SECONDS = (5, 15, 30, 60)
# The same failure is logged again only after this long.
FAILURE_LOG_SECONDS = 600.0
# An OA with chat turned off is asked again this rarely.
CHAT_OFF_RETRY_SECONDS = 600.0
BACKFILL_CHATS = 50
BACKFILL_MESSAGES = 20
# Pause between per-chat history requests so a sync looks like someone scrolling.
BACKFILL_PAUSE_SECONDS = 0.3
CURSOR_SAVE_EVERY = 20


def is_user_chat(chat_id: str) -> bool:
    return bool(USER_CHAT_RE.match(str(chat_id or "")))


def normalize_event(
    event: dict[str, Any], *, names: dict[str, str] | None = None
) -> dict[str, Any] | None:
    """An OA Manager message event -> the shape the host stores; None otherwise.

    Only one-to-one chats; groups and rooms are left out for now.
    """
    kind = str(event.get("type") or "")
    if kind not in ("message", "messageSent"):
        return None
    source = event.get("source") or {}
    chat_id = str(source.get("chatId") or "")
    if not is_user_chat(chat_id):
        return None
    message = event.get("message") or {}
    message_type = str(message.get("type") or "")
    if message_type in SKIPPED_TYPES:
        return None
    content_type = CONTENT_TYPES.get(message_type, 0)
    text = str(message.get("text") or "")
    meta: dict[str, Any] = {"messageId": str(message.get("id") or "")}
    provider = message.get("contentProvider") or {}
    content_hash = str(provider.get("contentHash") or message.get("contentHash") or "")
    if content_hash:
        meta["contentHash"] = content_hash
    if message_type in ("video", "audio") and message.get("duration"):
        meta["duration"] = round(int(message["duration"]) / 1000)
    if message_type == "file":
        meta.update(fileName=message.get("fileName") or "file", fileSize=message.get("fileSize"))
    elif message_type == "sticker":
        meta.update(
            stickerId=str(message.get("stickerId") or ""),
            packageId=str(message.get("packageId") or ""),
        )
    elif message_type == "location":
        meta["location"] = {
            "latitude": message.get("latitude"),
            "longitude": message.get("longitude"),
            "title": message.get("title") or "",
            "address": message.get("address") or "",
        }
        text = text or str(message.get("title") or message.get("address") or "")
    if content_type == 0 and not text:
        text = f"[{message_type or 'unsupported'}]"
    biz_id = str(event.get("bizId") or "")
    send_id = str(event.get("sendId") or "")
    meta.update(bizId=biz_id, sendId=send_id)
    profile = event.get("profile") or {}
    return {
        # A message sent from OA Manager comes back with the id it was sent
        # under; keying on it keeps our own sends from appearing twice.
        "externalId": send_id or str(message.get("id") or ""),
        "chatId": chat_id,
        "userId": chat_id,
        "name": (names or {}).get(chat_id) or str(profile.get("name") or ""),
        "username": "",
        "text": text[:10_000],
        "contentType": content_type,
        "meta": {key: value for key, value in meta.items() if value not in (None, "")},
        "at": int(event.get("timestamp") or 0),
        "outgoing": kind == "messageSent",
        "autoResponse": biz_id == AUTO_RESPONSE_BIZ_ID,
    }


class ChannelHost(Protocol):
    def oa_bot_id(self, account_id: str) -> str: ...

    def oa_cookies(self, account_id: str) -> list[dict[str, Any]]: ...

    def oa_save_cookies(self, account_id: str, cookies: list[dict[str, Any]]) -> None: ...

    def oa_proxy(self, account_id: str) -> str | None: ...

    def oa_receive(self, account_id: str, message: dict[str, Any]) -> None: ...

    def oa_backfill(self, account_id: str, messages: list[dict[str, Any]]) -> None: ...

    def oa_auth_failed(self, account_id: str) -> None: ...

    def oa_problem(self, account_id: str, problem: str) -> None: ...

    def oa_cursor(self, account_id: str) -> str: ...

    def oa_save_cursor(self, account_id: str, cursor: str) -> None: ...


class OAChannelWorker:
    def __init__(
        self, channels: LineOAChannels, account_id: str, client: OAManagerClient, bot_id: str
    ) -> None:
        self.channels = channels
        self.account_id = account_id
        self.client = client
        self.bot_id = bot_id
        self.names: dict[str, str] = {}
        self.stop_event = threading.Event()
        self.stream: OAManagerStream | None = None
        self.logged: dict[str, float] = {}
        self.thread = threading.Thread(
            target=self._run, name=f"linepassport-oa-{account_id[:16]}", daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        stream = self.stream
        if stream is not None:
            stream.close()

    def _log(self, text: str) -> None:
        now = time.monotonic()
        if now - self.logged.get(text, -FAILURE_LOG_SECONDS) < FAILURE_LOG_SECONDS:
            return
        self.logged[text] = now
        print(f"[lineoa] {self.account_id}: {text}", flush=True)

    def _name_for(self, chat_id: str) -> str:
        if chat_id not in self.names:
            try:
                chat = self.client.chat(self.bot_id, chat_id)
            except OAManagerError:
                return ""
            profile = chat.get("profile") or {}
            self.names[chat_id] = str(profile.get("name") or chat.get("name") or "")
        return self.names[chat_id]

    def sync(self) -> None:
        """Copy recent chats in, without alerts."""
        chats: list[dict[str, Any]] = []
        cursor: str | None = None
        while len(chats) < BACKFILL_CHATS and not self.stop_event.is_set():
            page, cursor = self.client.chats(self.bot_id, next_cursor=cursor)
            chats.extend(chat for chat in page if is_user_chat(str(chat.get("chatId") or "")))
            if not page or not cursor:
                break
        messages: list[dict[str, Any]] = []
        for chat in chats[:BACKFILL_CHATS]:
            if self.stop_event.wait(BACKFILL_PAUSE_SECONDS):
                return
            chat_id = str(chat["chatId"])
            profile = chat.get("profile") or {}
            self.names[chat_id] = str(profile.get("name") or "")
            items, _backward = self.client.messages(self.bot_id, chat_id)
            for item in reversed(items[:BACKFILL_MESSAGES]):
                normalized = normalize_event(item, names=self.names)
                if normalized is not None:
                    messages.append(normalized)
        if messages and not self.stop_event.is_set():
            self.channels.host.oa_backfill(self.account_id, messages)
        self._log(f"copied {len(messages)} recent messages from {len(chats)} chats")

    def _handle(self, event: dict[str, Any]) -> None:
        data = event.get("data") or {}
        if event.get("type") in ("reload", "fail"):
            self.sync()
            return
        if event.get("type") != "chat" or not isinstance(data, dict):
            return
        if data.get("subEvent") not in ("message", "messageSent"):
            return
        payload = data.get("payload") or {}
        if not isinstance(payload, dict):
            return
        chat_id = str((payload.get("source") or {}).get("chatId") or "")
        if is_user_chat(chat_id) and chat_id not in self.names:
            profile = payload.get("profile") or {}
            if profile.get("name"):
                self.names[chat_id] = str(profile["name"])
            else:
                self._name_for(chat_id)
        normalized = normalize_event(payload, names=self.names)
        if normalized is not None:
            self.channels.host.oa_receive(self.account_id, normalized)

    def _run(self) -> None:
        host = self.channels.host
        cursor = host.oa_cursor(self.account_id)
        needs_sync = not cursor
        failures = 0
        problem: str | None = None  # unknown until OA Manager answers

        def working() -> None:
            nonlocal problem
            if problem != "":
                problem = ""
                host.oa_problem(self.account_id, "")

        while not self.stop_event.is_set():
            try:
                if needs_sync:
                    self.sync()
                    needs_sync = False
                    working()
                stream = OAManagerStream(self.client, self.bot_id, last_event_id=cursor)
                self.stream = stream
                for seen, event in enumerate(stream.events(self.stop_event), start=1):
                    failures = 0
                    if seen == 1:
                        working()
                    try:
                        self._handle(event)
                    except Exception:
                        traceback.print_exc()
                    if stream.last_event_id and seen % CURSOR_SAVE_EVERY == 0:
                        cursor = stream.last_event_id
                        host.oa_save_cursor(self.account_id, cursor)
                if stream.last_event_id and stream.last_event_id != cursor:
                    cursor = stream.last_event_id
                    host.oa_save_cursor(self.account_id, cursor)
            except OAManagerError as exc:
                if self.stop_event.is_set():
                    return
                if exc.unauthorized:
                    if self.client.refresh_session():
                        host.oa_save_cookies(self.account_id, self.client.cookies())
                        continue
                    self._log("OA Manager signed this account out")
                    host.oa_auth_failed(self.account_id)
                    return
                self._log(exc.message)
                delay: float = STREAM_BACKOFF_SECONDS[
                    min(failures, len(STREAM_BACKOFF_SECONDS) - 1)
                ]
                if exc.status == 403:
                    # OA Manager serves chats only to OAs in chat mode.
                    delay = CHAT_OFF_RETRY_SECONDS
                    if problem != "chat_off":
                        problem = "chat_off"
                        host.oa_problem(self.account_id, problem)
                failures += 1
                # After a long gap the server may not replay everything.
                needs_sync = needs_sync or failures >= 3
                self.stop_event.wait(delay)
            except Exception:
                traceback.print_exc()
                self.stop_event.wait(30)


class LineOAChannels:
    """Runs one OA Manager stream per LINE OA account and hands out clients."""

    def __init__(
        self,
        host: ChannelHost,
        *,
        client_factory: Callable[..., OAManagerClient] = OAManagerClient,
    ) -> None:
        self.host = host
        self.client_factory = client_factory
        self.lock = threading.RLock()
        self.workers: dict[str, OAChannelWorker] = {}
        self.clients: dict[str, OAManagerClient] = {}

    def client(self, account_id: str) -> OAManagerClient:
        """The account's signed-in client; the worker and senders share it."""
        with self.lock:
            client = self.clients.get(account_id)
            if client is None:
                cookies = self.host.oa_cookies(account_id)
                if not cookies:
                    raise OAManagerError("This LINE OA is not signed in.", status=401)
                client = self.client_factory(cookies, proxy=self.host.oa_proxy(account_id))
                self.clients[account_id] = client
            return client

    def start(self, account_id: str) -> None:
        with self.lock:
            old = self.workers.pop(account_id, None)
            if old:
                old.stop()
            bot_id = self.host.oa_bot_id(account_id)
            if not bot_id:
                return
            try:
                client = self.client(account_id)
            except OAManagerError:
                return
            self.workers[account_id] = OAChannelWorker(self, account_id, client, bot_id)

    def running(self, account_id: str) -> bool:
        with self.lock:
            worker = self.workers.get(account_id)
            return bool(worker and worker.thread.is_alive())

    def stop(self, account_id: str) -> None:
        with self.lock:
            worker = self.workers.pop(account_id, None)
            client = self.clients.pop(account_id, None)
        if worker:
            worker.stop()
        if client:
            client.close()

    def close(self) -> None:
        with self.lock:
            ids = list(set(self.workers) | set(self.clients))
        for account_id in ids:
            self.stop(account_id)
