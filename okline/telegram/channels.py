"""Telegram as a customer chat channel.

A shop adds a Telegram bot as an account next to its LINE accounts. Customers
who start the bot chat with the shop; each private chat appears in the inbox
as ``tgu<telegram user id>``. The Bot API keeps no history for us, so every
message is handed to the host (which stores it in the CRM) as it arrives.
"""

from __future__ import annotations

import threading
import traceback
from collections.abc import Callable
from typing import Any, Protocol

from .bot_api import TelegramApiError, TelegramBotClient

CHAT_PREFIX = "tgu"
POLL_BACKOFF_SECONDS = (5, 10, 30, 60)


def chat_id_for(user_id: int | str) -> str:
    return f"{CHAT_PREFIX}{int(user_id)}"


def telegram_user_id(chat_id: str) -> int:
    chat_id = str(chat_id or "")
    if not chat_id.startswith(CHAT_PREFIX) or not chat_id[len(CHAT_PREFIX):].isdigit():
        raise ValueError("not a Telegram private chat id")
    return int(chat_id[len(CHAT_PREFIX):])


def _largest(photos: list[dict[str, Any]], limit: int = 1600) -> dict[str, Any]:
    fitting = [p for p in photos if int(p.get("width") or 0) <= limit] or photos
    return max(fitting, key=lambda p: int(p.get("width") or 0) * int(p.get("height") or 0))


def normalize_message(message: dict[str, Any], *, outgoing: bool = False) -> dict[str, Any] | None:
    """Bot API ``Message`` -> the shape the host stores; None for non-private chats."""
    chat = message.get("chat") or {}
    if chat.get("type") != "private" or not chat.get("id"):
        return None
    sender = chat if outgoing else (message.get("from") or chat)
    text = str(message.get("text") or message.get("caption") or "")
    content_type = 0
    meta: dict[str, Any] = {}
    if message.get("photo"):
        photo = _largest(message["photo"])
        content_type = 1
        meta.update(fileId=photo.get("file_id"), width=photo.get("width"),
                    height=photo.get("height"))
    elif message.get("sticker"):
        sticker = message["sticker"]
        if sticker.get("is_animated") or sticker.get("is_video"):
            content_type = 7
            text = text or str(sticker.get("emoji") or "")
            thumb = sticker.get("thumbnail") or sticker.get("thumb") or {}
            if thumb.get("file_id"):
                meta.update(thumbId=thumb.get("file_id"))
        else:
            content_type = 1
            meta.update(fileId=sticker.get("file_id"), sticker=True)
    elif message.get("video") or message.get("animation") or message.get("video_note"):
        video = dict(
            message.get("video") or message.get("animation") or message.get("video_note") or {}
        )
        content_type = 2
        meta.update(fileId=video.get("file_id"), duration=video.get("duration"),
                    fileSize=video.get("file_size"))
    elif message.get("voice") or message.get("audio"):
        audio = dict(message.get("voice") or message.get("audio") or {})
        content_type = 3
        meta.update(fileId=audio.get("file_id"), duration=audio.get("duration"),
                    fileName=audio.get("file_name"), fileSize=audio.get("file_size"))
    elif message.get("document"):
        document = message["document"]
        content_type = 14
        meta.update(fileId=document.get("file_id"), fileName=document.get("file_name") or "file",
                    fileSize=document.get("file_size"), mime=document.get("mime_type"))
    elif message.get("contact"):
        contact = message["contact"]
        content_type = 13
        name = " ".join(p for p in (contact.get("first_name"), contact.get("last_name")) if p)
        meta.update(contactName=name, phone=contact.get("phone_number"))
        text = text or " ".join(p for p in (name, contact.get("phone_number")) if p)
    elif message.get("location") or message.get("venue"):
        venue = message.get("venue") or {}
        location = venue.get("location") or message.get("location") or {}
        content_type = 15
        meta.update(location={
            "latitude": location.get("latitude"),
            "longitude": location.get("longitude"),
            "title": venue.get("title") or "",
            "address": venue.get("address") or "",
        })
    elif not text:
        text = "[unsupported]"
    name = " ".join(p for p in (sender.get("first_name"), sender.get("last_name")) if p)
    return {
        "externalId": str(message.get("message_id") or ""),
        "chatId": chat_id_for(chat["id"]),
        "userId": str(chat["id"]),
        "name": name,
        "username": str(sender.get("username") or chat.get("username") or ""),
        "text": text[:10_000],
        "contentType": content_type,
        "meta": {key: value for key, value in meta.items() if value not in (None, "")},
        "at": int(message.get("date") or 0) * 1000,
        "outgoing": outgoing,
    }


class ChannelHost(Protocol):
    def tg_token(self, account_id: str) -> str: ...

    def tg_receive(self, account_id: str, message: dict[str, Any]) -> None: ...

    def tg_auth_failed(self, account_id: str) -> None: ...

    def tg_offset(self, account_id: str) -> int: ...

    def tg_save_offset(self, account_id: str, offset: int) -> None: ...

    def notify_proxy(self) -> str | None: ...


class BotChannelWorker:
    def __init__(self, channels: TelegramChannels, account_id: str, client: TelegramBotClient) -> None:
        self.channels = channels
        self.account_id = account_id
        self.client = client
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name=f"linepassport-tg-{account_id[:16]}", daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.client.close()

    def _run(self) -> None:
        host = self.channels.host
        offset = host.tg_offset(self.account_id)
        failures = 0
        while not self.stop_event.is_set():
            try:
                updates = self.client.get_updates(offset, timeout=25, allowed=["message"])
                failures = 0
            except TelegramApiError as exc:
                if self.stop_event.is_set():
                    return
                if exc.unauthorized:
                    host.tg_auth_failed(self.account_id)
                    return
                if exc.error_code == 409:
                    try:
                        self.client.delete_webhook()
                    except TelegramApiError:
                        pass
                delay = exc.retry_after or POLL_BACKOFF_SECONDS[
                    min(failures, len(POLL_BACKOFF_SECONDS) - 1)
                ]
                failures += 1
                self.stop_event.wait(delay)
                continue
            except Exception:
                traceback.print_exc()
                self.stop_event.wait(30)
                continue
            for update in updates:
                offset = max(offset, int(update.get("update_id") or 0) + 1)
                message = update.get("message")
                if self.stop_event.is_set() or not isinstance(message, dict):
                    continue
                normalized = normalize_message(message)
                if normalized is None:
                    continue
                try:
                    host.tg_receive(self.account_id, normalized)
                except Exception:
                    traceback.print_exc()
            if updates:
                host.tg_save_offset(self.account_id, offset)


class TelegramChannels:
    """Runs one poller per Telegram bot account and hands out sending clients."""

    def __init__(
        self,
        host: ChannelHost,
        *,
        client_factory: Callable[..., TelegramBotClient] = TelegramBotClient,
    ) -> None:
        self.host = host
        self.client_factory = client_factory
        self.lock = threading.RLock()
        self.workers: dict[str, BotChannelWorker] = {}
        self.clients: dict[str, TelegramBotClient] = {}

    def start(self, account_id: str) -> None:
        with self.lock:
            old = self.workers.pop(account_id, None)
            if old:
                old.stop()
            token = self.host.tg_token(account_id)
            if not token:
                return
            poller = self.client_factory(token, proxy=self.host.notify_proxy())
            self.workers[account_id] = BotChannelWorker(self, account_id, poller)

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

    def client(self, account_id: str) -> TelegramBotClient:
        with self.lock:
            client = self.clients.get(account_id)
            if client is None:
                token = self.host.tg_token(account_id)
                if not token:
                    raise TelegramApiError("This Telegram bot is not connected.", error_code=401)
                client = self.client_factory(token, proxy=self.host.notify_proxy())
                self.clients[account_id] = client
            return client

    def close(self) -> None:
        with self.lock:
            ids = list(set(self.workers) | set(self.clients))
        for account_id in ids:
            self.stop(account_id)
