"""A small Telegram Bot API client (https://core.telegram.org/bots/api).

Only what LinePassport needs: long polling, sending text/photos/documents,
inline buttons and file downloads. The bot token is part of every URL, so no
error message produced here ever includes a URL.
"""

from __future__ import annotations

import json
import re
from typing import Any

import requests

API_BASE = "https://api.telegram.org"
TOKEN_RE = re.compile(r"^\d{5,14}:[A-Za-z0-9_-]{30,60}$")
MAX_TEXT = 4096
MAX_CAPTION = 1024
MAX_DOWNLOAD_BYTES = 20 * 1024 * 1024  # the Bot API serves files up to 20 MB


class TelegramApiError(Exception):
    def __init__(
        self,
        message: str,
        *,
        error_code: int = 0,
        retry_after: int = 0,
        network: bool = False,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_code = error_code
        self.retry_after = retry_after
        self.network = network

    @property
    def unauthorized(self) -> bool:
        return self.error_code == 401

    @property
    def blocked(self) -> bool:
        """The person blocked the bot or never started it."""
        return self.error_code == 403


def valid_token(token: str) -> bool:
    return bool(TOKEN_RE.match(str(token or "").strip()))


def bot_id_from_token(token: str) -> str:
    return str(token or "").split(":", 1)[0]


class TelegramBotClient:
    def __init__(
        self,
        token: str,
        *,
        proxy: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        base_url: str = API_BASE,
    ) -> None:
        token = str(token or "").strip()
        if not valid_token(token):
            raise TelegramApiError("That does not look like a Telegram bot token.", error_code=400)
        self._token = token
        self.bot_id = bot_id_from_token(token)
        self.timeout = timeout
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}

    def __repr__(self) -> str:
        return f"TelegramBotClient(bot_id={self.bot_id!r})"

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def _call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        files: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        url = f"{self.base_url}/bot{self._token}/{method}"
        clean = {key: value for key, value in (params or {}).items() if value is not None}
        try:
            if files:
                data = {
                    key: json.dumps(value) if isinstance(value, (dict, list)) else value
                    for key, value in clean.items()
                }
                response = self.session.post(url, data=data, files=files, timeout=timeout or self.timeout)
            else:
                response = self.session.post(url, json=clean, timeout=timeout or self.timeout)
        except requests.RequestException as exc:
            raise TelegramApiError(
                f"Could not reach Telegram ({type(exc).__name__}).", network=True
            ) from None
        try:
            payload = response.json()
        except ValueError:
            raise TelegramApiError(
                f"Telegram answered with HTTP {response.status_code}.",
                error_code=int(response.status_code),
            ) from None
        if not isinstance(payload, dict) or not payload.get("ok"):
            description = str((payload or {}).get("description") or "Telegram request failed.")
            parameters = (payload or {}).get("parameters") or {}
            raise TelegramApiError(
                description[:300],
                error_code=int((payload or {}).get("error_code") or response.status_code or 0),
                retry_after=int(parameters.get("retry_after") or 0),
            )
        return payload.get("result")

    # -- methods -------------------------------------------------------------
    def get_me(self) -> dict[str, Any]:
        return dict(self._call("getMe") or {})

    def delete_webhook(self) -> bool:
        return bool(self._call("deleteWebhook", {"drop_pending_updates": False}))

    def get_updates(
        self, offset: int = 0, *, timeout: int = 25, allowed: list[str] | None = None
    ) -> list[dict[str, Any]]:
        result = self._call(
            "getUpdates",
            {
                "offset": offset or None,
                "timeout": timeout,
                "allowed_updates": allowed,
            },
            timeout=timeout + 15,
        )
        return [item for item in result or [] if isinstance(item, dict)]

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        buttons: list[list[dict[str, str]]] | None = None,
        reply_to: int | None = None,
        disable_preview: bool = True,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "chat_id": chat_id,
            "text": str(text or "")[:MAX_TEXT] or "-",
            "link_preview_options": {"is_disabled": disable_preview},
        }
        if buttons:
            params["reply_markup"] = {"inline_keyboard": buttons}
        if reply_to:
            params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
        return dict(self._call("sendMessage", params) or {})

    def send_photo(
        self, chat_id: int | str, data: bytes, *, filename: str = "image.jpg", caption: str = ""
    ) -> dict[str, Any]:
        return dict(
            self._call(
                "sendPhoto",
                {"chat_id": chat_id, "caption": caption[:MAX_CAPTION] or None},
                files={"photo": (filename, data)},
                timeout=120,
            )
            or {}
        )

    def send_document(
        self, chat_id: int | str, data: bytes, *, filename: str = "file", caption: str = ""
    ) -> dict[str, Any]:
        return dict(
            self._call(
                "sendDocument",
                {"chat_id": chat_id, "caption": caption[:MAX_CAPTION] or None},
                files={"document": (filename, data)},
                timeout=120,
            )
            or {}
        )

    def answer_callback(self, callback_id: str, text: str = "", *, alert: bool = False) -> None:
        self._call(
            "answerCallbackQuery",
            {"callback_query_id": callback_id, "text": text[:190] or None, "show_alert": alert},
        )

    def edit_buttons(
        self, chat_id: int | str, message_id: int, buttons: list[list[dict[str, str]]] | None
    ) -> None:
        self._call(
            "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": buttons or []},
            },
        )

    def get_file(self, file_id: str) -> dict[str, Any]:
        return dict(self._call("getFile", {"file_id": file_id}) or {})

    def download(self, file_path: str, *, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
        file_path = str(file_path or "").lstrip("/")
        if not file_path or ".." in file_path.split("/"):
            raise TelegramApiError("Invalid file path.", error_code=400)
        url = f"{self.base_url}/file/bot{self._token}/{file_path}"
        try:
            response = self.session.get(url, timeout=120, stream=True)
        except requests.RequestException as exc:
            raise TelegramApiError(
                f"Could not reach Telegram ({type(exc).__name__}).", network=True
            ) from None
        if response.status_code != 200:
            raise TelegramApiError(
                f"Telegram file request failed (HTTP {response.status_code}).",
                error_code=int(response.status_code),
            )
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise TelegramApiError("The file is too large.", error_code=413)
            chunks.append(chunk)
        return b"".join(chunks)

    def user_photo_file_id(self, user_id: int | str) -> str:
        result = self._call("getUserProfilePhotos", {"user_id": user_id, "limit": 1}) or {}
        photos = result.get("photos") or []
        if not photos or not photos[0]:
            return ""
        sizes = sorted(photos[0], key=lambda item: int(item.get("width") or 0))
        small = next((item for item in sizes if int(item.get("width") or 0) >= 160), sizes[-1])
        return str(small.get("file_id") or "")
