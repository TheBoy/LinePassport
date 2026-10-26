"""A personal Telegram account as a customer chat channel (MTProto via Telethon).

Telethon is optional (``pip install "okline[telegram]"``) and asyncio-based, so
each account gets its own event-loop thread. The rest of LinePassport talks to
it through the small synchronous ``TelethonUser`` adapter below, which the
tests replace with a fake.
"""

from __future__ import annotations

import asyncio
import io
import re
import threading
import time
import traceback
from collections.abc import Callable
from typing import Any, Protocol

from .channels import chat_id_for
from .errors import TelegramError

INSTALL_HINT = 'pip install "okline[telegram]"'
API_HASH_RE = re.compile(r"^[0-9a-f]{32}$")
PHONE_RE = re.compile(r"^\+?[0-9]{7,15}$")
CALL_TIMEOUT = 60.0
LOGIN_SECONDS = 600


def telethon_available() -> bool:
    try:
        import telethon  # noqa: F401
    except ImportError:
        return False
    return True


def _translate(exc: BaseException) -> TelegramError:
    """Telethon errors -> user-facing errors, without importing Telethon here."""
    name = type(exc).__name__
    seconds = int(getattr(exc, "seconds", 0) or 0)
    known = {
        "PhoneNumberInvalidError": (400, "That phone number is not valid on Telegram.",
                                    "telegram_phone_invalid"),
        "PhoneNumberBannedError": (400, "Telegram has banned this phone number.",
                                   "telegram_phone_banned"),
        "PhoneCodeInvalidError": (400, "The code is wrong. Check the latest code in Telegram.",
                                  "telegram_code_invalid"),
        "PhoneCodeExpiredError": (400, "The code expired. Start again for a new one.",
                                  "telegram_code_expired"),
        "PasswordHashInvalidError": (400, "The two-step password is wrong.",
                                     "telegram_password_invalid"),
        "ApiIdInvalidError": (400, "api_id / api_hash are not valid (check my.telegram.org).",
                              "telegram_api_id_invalid"),
        "AuthKeyUnregisteredError": (401, "This Telegram session was signed out.",
                                     "telegram_reauth"),
        "SessionRevokedError": (401, "This Telegram session was signed out.", "telegram_reauth"),
        "UserDeactivatedError": (401, "This Telegram account was deleted.", "telegram_reauth"),
        "UserIsBlockedError": (409, "This customer blocked your account.", "telegram_blocked"),
        "PeerIdInvalidError": (400, "Telegram does not know this chat yet.",
                               "telegram_chat_invalid"),
    }
    if name == "FloodWaitError" or name.startswith("FloodWait"):
        return TelegramError(
            429, f"Telegram asks to wait {seconds} seconds before trying again.",
            "telegram_flood_wait",
        )
    if name in known:
        status, message, code = known[name]
        return TelegramError(status, message, code)
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, OSError)):
        return TelegramError(502, "Could not reach Telegram.", "upstream_error")
    return TelegramError(400, (str(exc) or name)[:300], "telegram_failed")


def _file_name(message: Any) -> str:
    file = getattr(message, "file", None)
    name = getattr(file, "name", None) if file is not None else None
    if name:
        return str(name)
    ext = getattr(file, "ext", None) if file is not None else None
    return f"file{ext or ''}"


async def normalize_telethon(message: Any) -> dict[str, Any] | None:
    """Telethon ``Message`` of a private chat -> the shape the host stores."""
    if not getattr(message, "is_private", False):
        return None
    chat_id = getattr(message, "chat_id", None)
    if not chat_id:
        return None
    try:
        person = await message.get_chat()
    except Exception:
        person = None
    if getattr(person, "bot", False):
        return None
    text = str(getattr(message, "message", "") or "")
    content_type = 0
    meta: dict[str, Any] = {"mtproto": True}
    file = getattr(message, "file", None)
    if getattr(message, "photo", None):
        content_type = 1
    elif getattr(message, "sticker", None):
        mime = str(getattr(file, "mime_type", "") or "")
        content_type = 1 if mime == "image/webp" else 7
        if content_type == 7:
            text = text or str(getattr(file, "emoji", "") or "")
    elif getattr(message, "voice", None) or getattr(message, "audio", None):
        content_type = 3
        meta["duration"] = getattr(file, "duration", None)
    elif (
        getattr(message, "video", None)
        or getattr(message, "gif", None)
        or getattr(message, "video_note", None)
    ):
        content_type = 2
        meta["duration"] = getattr(file, "duration", None)
    elif getattr(message, "document", None):
        content_type = 14
        meta.update(fileName=_file_name(message), fileSize=getattr(file, "size", None))
    elif getattr(message, "contact", None):
        contact = message.contact
        content_type = 13
        name = " ".join(
            part for part in (getattr(contact, "first_name", ""), getattr(contact, "last_name", ""))
            if part
        )
        meta.update(contactName=name, phone=getattr(contact, "phone_number", ""))
        text = text or " ".join(p for p in (name, getattr(contact, "phone_number", "")) if p)
    elif getattr(message, "geo", None):
        geo = message.geo
        content_type = 15
        venue = getattr(message, "venue", None)
        meta["location"] = {
            "latitude": getattr(geo, "lat", None),
            "longitude": getattr(geo, "long", None),
            "title": str(getattr(venue, "title", "") or ""),
            "address": str(getattr(venue, "address", "") or ""),
        }
    elif not text and getattr(message, "media", None):
        text = "[unsupported]"
    date = getattr(message, "date", None)
    at = int(date.timestamp() * 1000) if date is not None else int(time.time() * 1000)
    name = " ".join(
        part for part in (getattr(person, "first_name", ""), getattr(person, "last_name", ""))
        if part
    )
    return {
        "externalId": str(message.id),
        "chatId": chat_id_for(chat_id),
        "userId": str(chat_id),
        "name": name,
        "username": str(getattr(person, "username", "") or ""),
        "text": text[:10_000],
        "contentType": content_type,
        "meta": {key: value for key, value in meta.items() if value not in (None, "")},
        "at": at,
        "outgoing": bool(getattr(message, "out", False)),
    }


class UserAdapter(Protocol):
    """What LinePassport needs from a logged-in (or logging-in) personal account."""

    def send_code(self, phone: str) -> None: ...

    def sign_in_code(self, code: str) -> str: ...

    def sign_in_password(self, password: str) -> None: ...

    def authorized(self) -> bool: ...

    def me(self) -> dict[str, Any]: ...

    def session_string(self) -> str: ...

    def recent(self, limit: int) -> list[dict[str, Any]]: ...

    def listen(
        self,
        on_message: Callable[[dict[str, Any]], None],
        on_signed_out: Callable[[], None],
    ) -> None: ...

    def send_text(self, user_id: int, text: str) -> dict[str, Any]: ...

    def send_file(self, user_id: int, data: bytes, filename: str, is_image: bool) -> dict[str, Any]: ...

    def download(self, user_id: int, message_id: int) -> tuple[bytes, str]: ...

    def close(self) -> None: ...


class TelethonUser:
    """Telethon client on its own event-loop thread, used synchronously."""

    def __init__(self, api_id: int, api_hash: str, session: str = "", proxy: str | None = None) -> None:
        try:
            from telethon import TelegramClient
            from telethon.sessions import StringSession
        except ImportError:
            raise TelegramError(
                501,
                f"Personal Telegram accounts need Telethon. Run: {INSTALL_HINT}",
                "telegram_mtproto_missing",
            ) from None
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._loop.run_forever, name="linepassport-mtproto", daemon=True
        )
        self._thread.start()
        self._phone = ""
        self._code_hash = ""
        proxy_arg = _telethon_proxy(proxy)

        async def make() -> Any:
            client = TelegramClient(
                StringSession(session or None),
                api_id,
                api_hash,
                proxy=proxy_arg,
                device_model="LinePassport",
                app_version="LinePassport",
            )
            await client.connect()
            return client

        try:
            self._client = self._call(make())
        except BaseException:
            self._loop.call_soon_threadsafe(self._loop.stop)
            raise

    def _call(self, coro: Any, timeout: float = CALL_TIMEOUT) -> Any:
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout)
        except TelegramError:
            raise
        except Exception as exc:
            raise _translate(exc) from None

    def send_code(self, phone: str) -> None:
        result = self._call(self._client.send_code_request(phone))
        self._phone = phone
        self._code_hash = str(getattr(result, "phone_code_hash", "") or "")

    def sign_in_code(self, code: str) -> str:
        future = asyncio.run_coroutine_threadsafe(
            self._client.sign_in(phone=self._phone, code=code, phone_code_hash=self._code_hash),
            self._loop,
        )
        try:
            future.result(CALL_TIMEOUT)
        except Exception as exc:
            if type(exc).__name__ == "SessionPasswordNeededError":
                return "password"
            raise _translate(exc) from None
        return "ok"

    def sign_in_password(self, password: str) -> None:
        self._call(self._client.sign_in(password=password))

    def authorized(self) -> bool:
        return bool(self._call(self._client.is_user_authorized()))

    def me(self) -> dict[str, Any]:
        user = self._call(self._client.get_me())
        return {
            "id": int(user.id),
            "first_name": getattr(user, "first_name", "") or "",
            "last_name": getattr(user, "last_name", "") or "",
            "username": getattr(user, "username", "") or "",
            "phone": getattr(user, "phone", "") or "",
        }

    def session_string(self) -> str:
        return str(self._client.session.save())

    def recent(self, limit: int) -> list[dict[str, Any]]:
        """Last message of recent private chats; also fills Telethon's entity cache."""

        async def collect() -> list[dict[str, Any]]:
            out = []
            for dialog in await self._client.get_dialogs(limit=limit):
                if not getattr(dialog, "is_user", False) or not getattr(dialog, "message", None):
                    continue
                normalized = await normalize_telethon(dialog.message)
                if normalized:
                    out.append(normalized)
            return out

        return list(self._call(collect(), timeout=120))

    def listen(
        self,
        on_message: Callable[[dict[str, Any]], None],
        on_signed_out: Callable[[], None],
    ) -> None:
        from telethon import events

        loop = self._loop

        async def handler(event: Any) -> None:
            try:
                normalized = await normalize_telethon(event.message)
                if normalized:
                    # Database work happens off the event loop.
                    await loop.run_in_executor(None, on_message, normalized)
            except Exception:
                traceback.print_exc()

        self._client.add_event_handler(handler, events.NewMessage())

        async def watch() -> None:
            try:
                await self._client.run_until_disconnected()
            except Exception as exc:
                if _translate(exc).code == "telegram_reauth":
                    await loop.run_in_executor(None, on_signed_out)
                else:
                    traceback.print_exc()

        asyncio.run_coroutine_threadsafe(watch(), loop)

    def send_text(self, user_id: int, text: str) -> dict[str, Any]:
        message = self._call(self._client.send_message(user_id, text))
        return {"message_id": message.id, "date": int(message.date.timestamp())}

    def send_file(self, user_id: int, data: bytes, filename: str, is_image: bool) -> dict[str, Any]:
        buffer = io.BytesIO(data)
        buffer.name = filename
        message = self._call(
            self._client.send_file(user_id, buffer, force_document=not is_image), timeout=300
        )
        return {"message_id": message.id, "date": int(message.date.timestamp())}

    def download(self, user_id: int, message_id: int) -> tuple[bytes, str]:
        async def fetch() -> tuple[bytes, str]:
            message = await self._client.get_messages(user_id, ids=message_id)
            if message is None or not getattr(message, "media", None):
                raise TelegramError(404, "Media not available.", "not_found")
            data = await self._client.download_media(message, file=bytes)
            return bytes(data or b""), _file_name(message)

        return self._call(fetch(), timeout=300)

    def close(self) -> None:
        try:
            self._call(self._client.disconnect(), timeout=10)
        except Exception:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


def _telethon_proxy(proxy: str | None) -> Any:
    if not proxy:
        return None
    from urllib.parse import urlparse

    parsed = urlparse(proxy)
    scheme = {"socks5": "socks5", "socks5h": "socks5", "http": "http", "https": "http"}.get(
        parsed.scheme
    )
    if not scheme or not parsed.hostname:
        return None
    return {
        "proxy_type": scheme,
        "addr": parsed.hostname,
        "port": parsed.port or (1080 if scheme == "socks5" else 8080),
        "username": parsed.username,
        "password": parsed.password,
        "rdns": True,
    }


class UsersHost(Protocol):
    def tgu_credentials(self, account_id: str) -> dict[str, Any]: ...

    def tg_receive(self, account_id: str, message: dict[str, Any]) -> None: ...

    def tg_backfill(self, account_id: str, messages: list[dict[str, Any]]) -> None: ...

    def tg_auth_failed(self, account_id: str) -> None: ...

    def notify_proxy(self) -> str | None: ...


class TelegramUsers:
    """Connected personal accounts, plus logins in progress."""

    def __init__(
        self,
        host: UsersHost,
        *,
        adapter_factory: Callable[..., UserAdapter] = TelethonUser,
    ) -> None:
        self.host = host
        self.adapter_factory = adapter_factory
        self.lock = threading.RLock()
        self.adapters: dict[str, UserAdapter] = {}
        self.logins: dict[str, dict[str, Any]] = {}

    # -- connected accounts --------------------------------------------------
    def start(self, account_id: str) -> None:
        threading.Thread(
            target=self._start, args=(account_id,), name="linepassport-mtproto-start", daemon=True
        ).start()

    def _start(self, account_id: str) -> None:
        self.stop(account_id)
        credentials = self.host.tgu_credentials(account_id)
        if not credentials.get("session"):
            return
        try:
            adapter = self.adapter_factory(
                int(credentials["apiId"]),
                str(credentials["apiHash"]),
                str(credentials["session"]),
                proxy=self.host.notify_proxy(),
            )
            if not adapter.authorized():
                adapter.close()
                self.host.tg_auth_failed(account_id)
                return
            with self.lock:
                self.adapters[account_id] = adapter
            try:
                self.host.tg_backfill(account_id, adapter.recent(100))
            except Exception:
                traceback.print_exc()
            adapter.listen(
                lambda message: self.host.tg_receive(account_id, message),
                lambda: self.host.tg_auth_failed(account_id),
            )
        except TelegramError as exc:
            if exc.code == "telegram_reauth":
                self.host.tg_auth_failed(account_id)
            else:
                print(f"Telegram account {account_id} could not start: {exc.message}")
        except Exception:
            traceback.print_exc()

    def running(self, account_id: str) -> bool:
        with self.lock:
            return account_id in self.adapters

    def adapter(self, account_id: str) -> UserAdapter:
        with self.lock:
            adapter = self.adapters.get(account_id)
        if adapter is None:
            raise TelegramError(
                503, "This Telegram account is still connecting. Try again in a moment.",
                "telegram_connecting",
            )
        return adapter

    def stop(self, account_id: str) -> None:
        with self.lock:
            adapter = self.adapters.pop(account_id, None)
        if adapter is not None:
            adapter.close()

    def close(self) -> None:
        with self.lock:
            ids = list(self.adapters)
            logins = list(self.logins.values())
            self.logins = {}
        for account_id in ids:
            self.stop(account_id)
        for login in logins:
            try:
                login["adapter"].close()
            except Exception:
                pass

    # -- logging in ----------------------------------------------------------
    def begin_login(
        self, owner_key: str, api_id: Any, api_hash: str, phone: str, extra: dict[str, Any]
    ) -> dict[str, Any]:
        try:
            api_id_int = int(str(api_id).strip())
        except ValueError:
            raise TelegramError(400, "api_id must be a number from my.telegram.org.",
                                "telegram_api_id_invalid") from None
        api_hash = str(api_hash or "").strip().lower()
        if api_id_int <= 0 or not API_HASH_RE.match(api_hash):
            raise TelegramError(400, "api_id / api_hash are not valid (check my.telegram.org).",
                                "telegram_api_id_invalid")
        phone = re.sub(r"[\s()-]", "", str(phone or ""))
        if not PHONE_RE.match(phone):
            raise TelegramError(400, "Enter the phone number with its country code, e.g. +66…",
                                "telegram_phone_invalid")
        self.cancel_login(owner_key)
        adapter = self.adapter_factory(api_id_int, api_hash, "", proxy=self.host.notify_proxy())
        try:
            adapter.send_code(phone)
        except BaseException:
            adapter.close()
            raise
        with self.lock:
            self.logins[owner_key] = {
                "adapter": adapter,
                "apiId": api_id_int,
                "apiHash": api_hash,
                "phone": phone,
                "state": "code",
                "startedAt": time.time(),
                **extra,
            }
        return {"ok": True, "state": "code"}

    def _login(self, owner_key: str) -> dict[str, Any]:
        with self.lock:
            login = self.logins.get(owner_key)
        if not login or time.time() - login["startedAt"] > LOGIN_SECONDS:
            self.cancel_login(owner_key)
            raise TelegramError(409, "The sign-in expired. Start again.", "telegram_login_expired")
        return login

    def submit_code(self, owner_key: str, code: str) -> dict[str, Any] | None:
        """Returns the finished login, or None when a two-step password is needed."""
        login = self._login(owner_key)
        code = re.sub(r"\D", "", str(code or ""))
        if not code:
            raise TelegramError(400, "Enter the code Telegram sent you.", "telegram_code_invalid")
        if login["adapter"].sign_in_code(code) == "password":
            login["state"] = "password"
            return None
        return self._finish(owner_key, login)

    def submit_password(self, owner_key: str, password: str) -> dict[str, Any]:
        login = self._login(owner_key)
        if login.get("state") != "password":
            raise TelegramError(409, "Enter the code first.", "telegram_login_order")
        login["adapter"].sign_in_password(str(password or ""))
        return self._finish(owner_key, login)

    def _finish(self, owner_key: str, login: dict[str, Any]) -> dict[str, Any]:
        adapter = login["adapter"]
        me = adapter.me()
        session = adapter.session_string()
        adapter.close()
        with self.lock:
            self.logins.pop(owner_key, None)
        return {**login, "me": me, "session": session, "adapter": None}

    def login_state(self, owner_key: str) -> str:
        with self.lock:
            login = self.logins.get(owner_key)
        return str(login.get("state")) if login else "idle"

    def cancel_login(self, owner_key: str) -> None:
        with self.lock:
            login = self.logins.pop(owner_key, None)
        if login and login.get("adapter") is not None:
            try:
                login["adapter"].close()
            except Exception:
                pass
