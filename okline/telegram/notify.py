"""Staff alerts on Telegram, and answering customers by replying to an alert.

Each shop (tenant) may connect one Telegram bot of its own. Staff link their
personal Telegram chat to their LinePassport login with a one-time ``/start``
code, then receive alerts for waiting customers, chats handed to them and
follow-ups that come due. Replying to an alert sends the reply to the
customer as that staff member; the buttons take or close the chat.
"""

from __future__ import annotations

import hashlib
import secrets
import threading
import time
import traceback
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Protocol

from .bot_api import TelegramApiError, TelegramBotClient, valid_token
from .errors import TelegramError as NotifyError

NOTIFY_KEY = "telegram_notify"
MAP_KEY = "telegram_notify_map"
OFFSET_KEY = "telegram_runtime"
LINK_CODE_SECONDS = 600
MAP_KEEP_SECONDS = 7 * 86_400
MAX_MAP_ITEMS = 3000
REPLY_MAX_AGE_SECONDS = 15 * 60
MODES = ("away", "always")
POLL_BACKOFF_SECONDS = (5, 10, 30, 60)
PREVIEW_LABELS = {
    "[image]": "[รูปภาพ]",
    "[video]": "[วิดีโอ]",
    "[audio]": "[ข้อความเสียง]",
    "[sticker]": "[สติกเกอร์]",
    "[contact]": "[รายชื่อติดต่อ]",
    "[file]": "[ไฟล์]",
    "[location]": "[ตำแหน่ง]",
    "[media]": "[สื่อ]",
}


class NotifyHost(Protocol):
    store: Any

    def notify_encrypt(self, value: str) -> str: ...

    def notify_decrypt(self, value: str) -> str: ...

    def notify_proxy(self) -> str | None: ...

    def notify_user_record(self, user_id: str) -> dict[str, Any] | None: ...

    def notify_reply(
        self, user: dict[str, Any], account_id: str, chat_id: str, text: str
    ) -> str: ...

    def notify_claim(self, user: dict[str, Any], account_id: str, chat_id: str) -> str: ...

    def notify_close(self, user: dict[str, Any], account_id: str, chat_id: str) -> str: ...


def _hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def preview_label(text: str) -> str:
    text = " ".join(str(text or "").split())
    return PREVIEW_LABELS.get(text, text[:300])


class NotifyWorker:
    """Long-polls one shop's bot and hands each update to the notifier."""

    def __init__(self, notifier: StaffNotifier, owner_id: str, client: TelegramBotClient) -> None:
        self.notifier = notifier
        self.owner_id = owner_id
        self.client = client
        self.stop_event = threading.Event()
        self.thread = threading.Thread(
            target=self._run, name=f"linepassport-tg-notify-{owner_id[:8]}", daemon=True
        )
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.client.close()

    def _run(self) -> None:
        offset = self.notifier.load_offset(self.owner_id)
        failures = 0
        while not self.stop_event.is_set():
            try:
                updates = self.client.get_updates(
                    offset, timeout=25, allowed=["message", "callback_query"]
                )
                failures = 0
            except TelegramApiError as exc:
                if self.stop_event.is_set():
                    return
                if exc.unauthorized:
                    self.notifier.mark_broken(self.owner_id, "token_revoked")
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
                if self.stop_event.is_set():
                    break
                try:
                    self.notifier.handle_update(self.owner_id, self.client, update)
                except Exception:
                    traceback.print_exc()
            if updates:
                self.notifier.save_offset(self.owner_id, offset)


class StaffNotifier:
    def __init__(
        self,
        host: NotifyHost,
        *,
        client_factory: Callable[..., TelegramBotClient] = TelegramBotClient,
        start: bool = True,
    ) -> None:
        self.host = host
        self.client_factory = client_factory
        self.lock = threading.RLock()
        self.workers: dict[str, NotifyWorker] = {}
        self.clients: dict[str, TelegramBotClient] = {}
        # One sender keeps alerts in order.
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="linepassport-tg-notify")
        self._links_cache: bool | None = None
        if start:
            for owner_id, tenant in self._data()["tenants"].items():
                if tenant.get("enabled") and not tenant.get("broken"):
                    self._start_worker(owner_id)

    def close(self) -> None:
        with self.lock:
            workers = list(self.workers.values())
            self.workers = {}
        for worker in workers:
            worker.stop()
        self.executor.shutdown(wait=False, cancel_futures=True)

    # -- storage -------------------------------------------------------------
    def _data(self) -> dict[str, Any]:
        raw = self.host.store.get(NOTIFY_KEY, {})
        data = dict(raw) if isinstance(raw, dict) else {}
        for key in ("tenants", "links", "codes"):
            if not isinstance(data.get(key), dict):
                data[key] = {}
        return data

    def _save(self, data: dict[str, Any]) -> None:
        self.host.store.set(NOTIFY_KEY, data)
        self._links_cache = None

    def tenant(self, owner_id: str) -> dict[str, Any] | None:
        tenant = self._data()["tenants"].get(owner_id)
        return tenant if isinstance(tenant, dict) and tenant.get("enabled") else None

    def link_for(self, user_id: str) -> dict[str, Any] | None:
        link = self._data()["links"].get(user_id)
        return link if isinstance(link, dict) and link.get("chatId") else None

    def has_links(self) -> bool:
        if self._links_cache is None:
            self._links_cache = bool(self._data()["links"])
        return self._links_cache

    def load_offset(self, owner_id: str) -> int:
        raw = self.host.store.get(f"{OFFSET_KEY}:notify_{owner_id}", {})
        return int((raw or {}).get("offset") or 0) if isinstance(raw, dict) else 0

    def save_offset(self, owner_id: str, offset: int) -> None:
        self.host.store.set(f"{OFFSET_KEY}:notify_{owner_id}", {"offset": int(offset)})

    def _client(self, owner_id: str) -> TelegramBotClient | None:
        with self.lock:
            client = self.clients.get(owner_id)
            if client is not None:
                return client
            tenant = self.tenant(owner_id)
            if not tenant:
                return None
            token = self.host.notify_decrypt(str(tenant.get("token") or ""))
            client = self.client_factory(token, proxy=self.host.notify_proxy())
            self.clients[owner_id] = client
            return client

    def _start_worker(self, owner_id: str) -> None:
        with self.lock:
            old = self.workers.pop(owner_id, None)
            if old:
                old.stop()
            tenant = self.tenant(owner_id)
            if not tenant:
                return
            token = self.host.notify_decrypt(str(tenant.get("token") or ""))
            # The poller gets its own client: a long poll must not block alerts.
            client = self.client_factory(token, proxy=self.host.notify_proxy())
            self.workers[owner_id] = NotifyWorker(self, owner_id, client)

    def mark_broken(self, owner_id: str, reason: str) -> None:
        with self.lock:
            data = self._data()
            tenant = data["tenants"].get(owner_id)
            if isinstance(tenant, dict):
                tenant["broken"] = reason
                self._save(data)
            self.workers.pop(owner_id, None)
            self.clients.pop(owner_id, None)

    # -- shop settings -------------------------------------------------------
    def settings(self, owner_id: str, user_id: str, *, can_manage: bool) -> dict[str, Any]:
        data = self._data()
        tenant = data["tenants"].get(owner_id) if isinstance(data["tenants"].get(owner_id), dict) else None
        link = data["links"].get(user_id) if isinstance(data["links"].get(user_id), dict) else None
        return {
            "ok": True,
            "configured": bool(tenant and tenant.get("enabled")),
            "botUsername": str((tenant or {}).get("username") or ""),
            "botName": str((tenant or {}).get("name") or ""),
            "broken": str((tenant or {}).get("broken") or ""),
            "canManage": can_manage,
            "link": {
                "linked": bool(link and link.get("chatId") and link.get("ownerId") == owner_id),
                "name": str((link or {}).get("name") or ""),
                "username": str((link or {}).get("username") or ""),
                "mode": str((link or {}).get("mode") or "away"),
                "linkedAt": int((link or {}).get("linkedAt") or 0),
            },
        }

    def configure(self, owner_id: str, token: str) -> dict[str, Any]:
        token = str(token or "").strip()
        if not valid_token(token):
            raise NotifyError(400, "That does not look like a bot token from @BotFather.",
                              "telegram_token_invalid")
        client = self.client_factory(token, proxy=self.host.notify_proxy())
        try:
            me = client.get_me()
            try:
                client.delete_webhook()
            except TelegramApiError:
                pass
        except TelegramApiError as exc:
            code = "telegram_token_rejected" if exc.unauthorized else "telegram_unreachable"
            raise NotifyError(400 if exc.unauthorized else 502, exc.message, code) from None
        finally:
            client.close()
        if not me.get("is_bot") or not me.get("username"):
            raise NotifyError(400, "That token does not belong to a bot.", "telegram_token_invalid")
        with self.lock:
            data = self._data()
            previous = data["tenants"].get(owner_id) or {}
            same_bot = str(previous.get("botId") or "") == str(me.get("id"))
            data["tenants"][owner_id] = {
                "token": self.host.notify_encrypt(token),
                "botId": str(me.get("id")),
                "username": str(me.get("username")),
                "name": str(me.get("first_name") or ""),
                "enabled": True,
                "updatedAt": int(time.time()),
            }
            if not same_bot:
                # Chats with the old bot cannot be reached through a new one.
                data["links"] = {
                    uid: link for uid, link in data["links"].items()
                    if not isinstance(link, dict) or link.get("ownerId") != owner_id
                }
                self.host.store.set(f"{OFFSET_KEY}:notify_{owner_id}", {"offset": 0})
            self._save(data)
            self.clients.pop(owner_id, None)
            self._start_worker(owner_id)
        return data["tenants"][owner_id]

    def remove(self, owner_id: str) -> None:
        with self.lock:
            worker = self.workers.pop(owner_id, None)
            if worker:
                worker.stop()
            self.clients.pop(owner_id, None)
            data = self._data()
            data["tenants"].pop(owner_id, None)
            data["links"] = {
                uid: link for uid, link in data["links"].items()
                if not isinstance(link, dict) or link.get("ownerId") != owner_id
            }
            self._save(data)

    # -- linking a person ----------------------------------------------------
    def create_link(self, owner_id: str, user_id: str) -> dict[str, Any]:
        tenant = self.tenant(owner_id)
        if not tenant:
            raise NotifyError(409, "The shop has not connected a Telegram bot yet.",
                              "telegram_not_configured")
        code = secrets.token_urlsafe(18)
        now = time.time()
        with self.lock:
            data = self._data()
            data["codes"] = {
                key: item for key, item in data["codes"].items()
                if isinstance(item, dict) and item.get("expiresAt", 0) > now
                and item.get("userId") != user_id
            }
            data["codes"][_hash(code)] = {
                "userId": user_id,
                "ownerId": owner_id,
                "expiresAt": now + LINK_CODE_SECONDS,
            }
            self._save(data)
        return {
            "ok": True,
            "url": f"https://t.me/{tenant['username']}?start={code}",
            "expiresAt": int((now + LINK_CODE_SECONDS) * 1000),
        }

    def unlink(self, user_id: str) -> None:
        with self.lock:
            data = self._data()
            if data["links"].pop(user_id, None) is not None:
                self._save(data)

    def set_mode(self, user_id: str, mode: str) -> None:
        if mode not in MODES:
            raise NotifyError(400, "Unknown alert mode.", "telegram_mode_invalid")
        with self.lock:
            data = self._data()
            link = data["links"].get(user_id)
            if not isinstance(link, dict):
                raise NotifyError(409, "Link your Telegram first.", "telegram_not_linked")
            link["mode"] = mode
            self._save(data)

    def _user_for_chat(self, owner_id: str, chat_id: Any) -> str:
        for user_id, link in self._data()["links"].items():
            if (
                isinstance(link, dict)
                and link.get("ownerId") == owner_id
                and str(link.get("chatId")) == str(chat_id)
            ):
                return str(user_id)
        return ""

    # -- alert map: which Telegram message points at which customer chat -----
    def _map(self, owner_id: str) -> list[dict[str, Any]]:
        raw = self.host.store.get(f"{MAP_KEY}:{owner_id}", {})
        items = raw.get("items") if isinstance(raw, dict) else None
        return [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []

    def _remember(self, owner_id: str, entry: dict[str, Any]) -> None:
        with self.lock:
            cutoff = time.time() - MAP_KEEP_SECONDS
            items = [item for item in self._map(owner_id) if item.get("at", 0) >= cutoff]
            items.append(entry)
            self.host.store.set(f"{MAP_KEY}:{owner_id}", {"items": items[-MAX_MAP_ITEMS:]})

    def _find(self, owner_id: str, **match: Any) -> dict[str, Any] | None:
        for item in reversed(self._map(owner_id)):
            if all(str(item.get(key)) == str(value) for key, value in match.items()):
                return item
        return None

    # -- sending -------------------------------------------------------------
    def send_alert(
        self,
        owner_id: str,
        user_id: str,
        *,
        title: str,
        lines: list[str],
        account_id: str = "",
        chat_id: str = "",
        replyable: bool = True,
    ) -> None:
        """Queue one alert; the network call happens off the caller's thread."""
        link = self.link_for(user_id)
        if not link or link.get("ownerId") != owner_id or not self.tenant(owner_id):
            return
        try:
            self.executor.submit(
                self._send_alert, owner_id, user_id, link, title, lines, account_id, chat_id,
                replyable,
            )
        except RuntimeError:
            return

    def _send_alert(
        self,
        owner_id: str,
        user_id: str,
        link: dict[str, Any],
        title: str,
        lines: list[str],
        account_id: str,
        chat_id: str,
        replyable: bool,
    ) -> None:
        client = self._client(owner_id)
        if client is None:
            return
        key = secrets.token_urlsafe(9)
        text_lines = [title, *[line for line in lines if line]]
        buttons = None
        if account_id and chat_id:
            if replyable:
                text_lines.append("")
                text_lines.append("↩️ กด Reply ที่ข้อความนี้เพื่อตอบลูกค้า")
            buttons = [[
                {"text": "✋ ฉันรับเรื่อง", "callback_data": f"c:{key}"},
                {"text": "✅ ปิดแชท", "callback_data": f"x:{key}"},
            ]]
        try:
            sent = client.send_message(link["chatId"], "\n".join(text_lines), buttons=buttons)
        except TelegramApiError as exc:
            if exc.blocked:
                self.unlink(user_id)
            elif exc.unauthorized:
                self.mark_broken(owner_id, "token_revoked")
            return
        if account_id and chat_id:
            self._remember(
                owner_id,
                {
                    "key": key,
                    "accountId": account_id,
                    "chatId": chat_id,
                    "tgChatId": link["chatId"],
                    "messageId": sent.get("message_id"),
                    "userId": user_id,
                    "at": time.time(),
                },
            )

    def send_test(self, owner_id: str, user_id: str) -> None:
        link = self.link_for(user_id)
        client = self._client(owner_id)
        if not link or link.get("ownerId") != owner_id or client is None:
            raise NotifyError(409, "Link your Telegram first.", "telegram_not_linked")
        try:
            client.send_message(link["chatId"], "🔔 ทดสอบการแจ้งเตือนจาก LinePassport\nถ้าเห็นข้อความนี้ แปลว่าใช้งานได้แล้ว")
        except TelegramApiError as exc:
            raise NotifyError(502, exc.message, "telegram_send_failed") from None

    # -- incoming updates ----------------------------------------------------
    def handle_update(self, owner_id: str, client: TelegramBotClient, update: dict[str, Any]) -> None:
        callback = update.get("callback_query")
        if isinstance(callback, dict):
            self._handle_callback(owner_id, client, callback)
            return
        message = update.get("message")
        if not isinstance(message, dict):
            return
        chat = message.get("chat") or {}
        if chat.get("type") != "private":
            return
        tg_chat: int | str = chat.get("id") or 0
        text = str(message.get("text") or "").strip()
        message_id = message.get("message_id")

        def say(reply: str) -> None:
            try:
                client.send_message(tg_chat, reply, reply_to=message_id)
            except TelegramApiError:
                pass

        if text.startswith("/start"):
            parts = text.split(maxsplit=1)
            say(self._link_from_code(owner_id, parts[1] if len(parts) > 1 else "", message))
            return
        if text in ("/stop", "/unlink"):
            user_id = self._user_for_chat(owner_id, tg_chat)
            if user_id:
                self.unlink(user_id)
            say("เลิกเชื่อมบัญชีแล้ว จะไม่ได้รับแจ้งเตือนอีก")
            return
        user_id = self._user_for_chat(owner_id, tg_chat)
        if not user_id:
            say("ยังไม่ได้เชื่อมกับ LinePassport\nไปที่ ตั้งค่า → Telegram แล้วกด \"เชื่อม Telegram ของฉัน\"")
            return
        reply_to = message.get("reply_to_message")
        if not isinstance(reply_to, dict):
            say("ตอบลูกค้าได้โดยกด Reply ที่ข้อความแจ้งเตือนของลูกค้าคนนั้น")
            return
        entry = self._find(owner_id, tgChatId=tg_chat, messageId=reply_to.get("message_id"))
        if not entry:
            say("หาแชทของข้อความนี้ไม่เจอ (เก่าเกิน 7 วันหรือไม่ใช่ข้อความแจ้งเตือน)")
            return
        if not text:
            say("ตอนนี้ตอบจาก Telegram ได้เฉพาะข้อความตัวอักษร")
            return
        if time.time() - float(message.get("date") or 0) > REPLY_MAX_AGE_SECONDS:
            say("ข้อความนี้ค้างนานเกินไป จึงไม่ได้ส่งให้ลูกค้า ลองส่งใหม่อีกครั้ง")
            return
        user = self.host.notify_user_record(user_id)
        if not user:
            say("บัญชี LinePassport นี้ถูกปิดแล้ว")
            return
        try:
            result = self.host.notify_reply(user, entry["accountId"], entry["chatId"], text)
        except Exception as exc:
            say("ส่งไม่สำเร็จ: " + (str(getattr(exc, "message", "") or exc) or "เกิดข้อผิดพลาด")[:200])
            return
        say(result or "✓ ส่งถึงลูกค้าแล้ว")

    def _link_from_code(self, owner_id: str, code: str, message: dict[str, Any]) -> str:
        if not code:
            return ("สวัสดีครับ บอทนี้ส่งแจ้งเตือนจาก LinePassport\n"
                    "ไปที่ ตั้งค่า → Telegram แล้วกด \"เชื่อม Telegram ของฉัน\" เพื่อเริ่มใช้งาน")
        now = time.time()
        sender = message.get("from") or {}
        with self.lock:
            data = self._data()
            entry = data["codes"].pop(_hash(code), None)
            if not isinstance(entry, dict) or entry.get("expiresAt", 0) < now or entry.get("ownerId") != owner_id:
                self._save(data)
                return "ลิงก์นี้หมดอายุแล้ว กลับไปกด \"เชื่อม Telegram ของฉัน\" ใหม่อีกครั้ง"
            user_id = str(entry["userId"])
            old = data["links"].get(user_id) if isinstance(data["links"].get(user_id), dict) else {}
            data["links"][user_id] = {
                "ownerId": owner_id,
                "chatId": (message.get("chat") or {}).get("id"),
                "name": " ".join(
                    part for part in (sender.get("first_name"), sender.get("last_name")) if part
                ),
                "username": str(sender.get("username") or ""),
                "mode": str((old or {}).get("mode") or "away"),
                "linkedAt": int(now * 1000),
            }
            self._save(data)
        return ("✅ เชื่อมกับ LinePassport แล้ว\n"
                "ต่อจากนี้จะได้รับแจ้งเตือนเมื่อลูกค้ารอตอบ เมื่อมีแชทมอบหมายให้ และเมื่อถึงเวลางานติดตาม\n"
                "พิมพ์ /stop เพื่อเลิกรับแจ้งเตือน")

    def _handle_callback(self, owner_id: str, client: TelegramBotClient, callback: dict[str, Any]) -> None:
        callback_id = str(callback.get("id") or "")
        data = str(callback.get("data") or "")
        action, _, key = data.partition(":")
        sender = (callback.get("from") or {}).get("id")
        user_id = self._user_for_chat(owner_id, sender)
        entry = self._find(owner_id, key=key) if key else None

        def answer(text: str) -> None:
            try:
                client.answer_callback(callback_id, text, alert=False)
            except TelegramApiError:
                pass

        if not user_id or not entry:
            answer("ใช้ปุ่มนี้ไม่ได้แล้ว")
            return
        user = self.host.notify_user_record(user_id)
        if not user:
            answer("บัญชี LinePassport นี้ถูกปิดแล้ว")
            return
        try:
            if action == "c":
                answer(self.host.notify_claim(user, entry["accountId"], entry["chatId"]))
            elif action == "x":
                answer(self.host.notify_close(user, entry["accountId"], entry["chatId"]))
            else:
                answer("ใช้ปุ่มนี้ไม่ได้แล้ว")
        except Exception as exc:
            answer((str(getattr(exc, "message", "") or exc) or "ทำไม่สำเร็จ")[:190])
