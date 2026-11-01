"""A client for the chat of LINE Official Account Manager (https://chat.line.biz).

LINE publishes no API for the OA Manager chat. This speaks the JSON endpoints
the OA Manager web page itself uses, signed in with that page's cookies: the
OAs a person manages, their chats and messages, sending text and files,
marking chats read, downloading content and the live event stream token.
The cookies are the credential, so no error message produced here includes
them or a URL that carries a token.
"""

from __future__ import annotations

import random
import threading
import time
from typing import Any
from urllib.parse import quote, urlsplit

import requests

CHAT_BASE = "https://chat.line.biz"
CONTENT_BASE = "https://chat-content.line.biz"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36"
)
MAX_TEXT = 5000
MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
# Cookies a stored session keeps; LINE's analytics cookies are not needed.
SESSION_COOKIE_DOMAINS = ("line.biz", "line.me")


class OAManagerError(Exception):
    def __init__(self, message: str, *, status: int = 0, network: bool = False) -> None:
        super().__init__(message)
        self.message = message
        self.status = status
        self.network = network

    @property
    def unauthorized(self) -> bool:
        """The OA Manager session ended; signing in again is needed."""
        return self.status == 401


def export_cookies(jar: requests.cookies.RequestsCookieJar) -> list[dict[str, Any]]:
    """The jar's LINE cookies as plain data, for storing encrypted."""
    now = time.time()
    cookies = []
    for cookie in jar:
        domain = str(cookie.domain or "").lstrip(".")
        if not domain.endswith(SESSION_COOKIE_DOMAINS):
            continue
        if cookie.expires and cookie.expires < now:
            continue
        cookies.append(
            {
                "name": cookie.name,
                "value": cookie.value,
                "domain": cookie.domain,
                "path": cookie.path or "/",
                "expires": cookie.expires,
                "secure": bool(cookie.secure),
            }
        )
    return cookies


def import_cookies(jar: requests.cookies.RequestsCookieJar, cookies: list[dict[str, Any]]) -> None:
    now = time.time()
    for item in cookies or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        expires = item.get("expires")
        if expires and float(expires) < now:
            continue
        jar.set(
            str(item["name"]),
            str(item.get("value") or ""),
            domain=str(item.get("domain") or ""),
            path=str(item.get("path") or "/"),
            expires=int(expires) if expires else None,
            secure=bool(item.get("secure")),
        )


def new_send_id(chat_id: str) -> str:
    """The id OA Manager pairs a sent message with its echo: chat, time, random."""
    return f"{chat_id}_{int(time.time() * 1000)}_{random.randint(0, 99_999_999):08d}"


def _segment(value: str) -> str:
    return quote(str(value or ""), safe="")


class OAManagerClient:
    """One signed-in OA Manager session; safe to share between threads."""

    def __init__(
        self,
        cookies: list[dict[str, Any]] | None = None,
        *,
        proxy: str | None = None,
        session: requests.Session | None = None,
        timeout: float = 30.0,
        base_url: str = CHAT_BASE,
        content_url: str = CONTENT_BASE,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.content_url = content_url.rstrip("/")
        self.timeout = timeout
        self.proxy = proxy
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)
        if proxy:
            self.session.proxies = {"http": proxy, "https": proxy}
        if cookies:
            import_cookies(self.session.cookies, cookies)
        self._csrf: str | None = None
        self._csrf_header = "X-XSRF-TOKEN"
        self._lock = threading.Lock()
        self._stream_session: requests.Session | None = None

    def __repr__(self) -> str:
        return "OAManagerClient()"

    def close(self) -> None:
        for session in (self.session, self._stream_session):
            if session is None:
                continue
            try:
                session.close()
            except Exception:
                pass

    def cookies(self) -> list[dict[str, Any]]:
        return export_cookies(self.session.cookies)

    def stream_session(self) -> requests.Session:
        """A second session sharing the cookies, for the long-lived event stream."""
        with self._lock:
            if self._stream_session is None:
                stream = requests.Session()
                stream.headers.update(self.session.headers)
                stream.proxies = dict(self.session.proxies)
                stream.cookies = self.session.cookies
                self._stream_session = stream
            return self._stream_session

    # -- plumbing ------------------------------------------------------------
    def _csrf_token(self, *, refresh: bool = False) -> str:
        with self._lock:
            token = None if refresh else self._csrf
        if token:
            return token
        data = self._request("GET", "/api/v1/csrfToken")
        token = str((data or {}).get("token") or "")
        header = str((data or {}).get("headerName") or "")
        with self._lock:
            self._csrf = token
            if header:
                self._csrf_header = header
        return token

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: Any = None,
        files: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        timeout: float | None = None,
        _retried: bool = False,
    ) -> Any:
        headers = {"Accept": "application/json, text/plain, */*", "Referer": f"{self.base_url}/"}
        if method != "GET":
            headers["Origin"] = self.base_url
            headers[self._csrf_header] = self._csrf_token()
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                params={key: value for key, value in (params or {}).items() if value is not None},
                json=json_body,
                files=files,
                data=data,
                headers=headers,
                timeout=timeout or self.timeout,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            raise OAManagerError(
                f"Could not reach LINE OA Manager ({type(exc).__name__}).", network=True
            ) from None
        status = int(response.status_code)
        if status in (301, 302, 303, 307, 308):
            # The page sends a signed-out browser to the login page.
            raise OAManagerError("The LINE OA Manager session ended.", status=401)
        if status == 403 and method != "GET" and not _retried:
            self._csrf_token(refresh=True)
            return self._request(
                method, path, params=params, json_body=json_body, files=files, data=data,
                timeout=timeout, _retried=True,
            )
        if status == 401:
            raise OAManagerError("The LINE OA Manager session ended.", status=401)
        if status >= 400:
            raise OAManagerError(
                f"LINE OA Manager answered with HTTP {status}.", status=status
            )
        if not response.content:
            return {}
        try:
            return response.json()
        except ValueError:
            raise OAManagerError(
                f"LINE OA Manager answered with HTTP {status} and no JSON.", status=status
            ) from None

    # -- account -------------------------------------------------------------
    def me(self) -> dict[str, Any]:
        return dict(self._request("GET", "/api/v1/me") or {})

    def bots(self) -> list[dict[str, Any]]:
        data = self._request("GET", "/api/v1/bots", params={"limit": 1000, "noFilter": "true"})
        return [item for item in (data or {}).get("list") or [] if isinstance(item, dict)]

    def refresh_session(self) -> bool:
        """Open chat.line.biz the way a browser does; LINE may sign it in again.

        LINE Business ID keeps its own longer session, so an expired chat
        session can come back without a QR scan. True when it did.
        """
        try:
            response = self.session.get(f"{self.base_url}/", timeout=self.timeout)
        except requests.RequestException:
            return False
        if urlsplit(response.url).netloc != urlsplit(self.base_url).netloc:
            return False
        with self._lock:
            self._csrf = None
        try:
            self.me()
        except OAManagerError:
            return False
        return True

    # -- chats ---------------------------------------------------------------
    def chats(
        self, bot_id: str, *, next_cursor: str | None = None, limit: int = 25
    ) -> tuple[list[dict[str, Any]], str]:
        data = self._request(
            "GET",
            f"/api/v2/bots/{_segment(bot_id)}/chats",
            params={
                "folderType": "ALL",
                "tagIds": "",
                "autoTagIds": "",
                "limit": limit,
                "prioritizePinnedChat": "true",
                "next": next_cursor or None,
            },
        )
        items = [item for item in (data or {}).get("list") or [] if isinstance(item, dict)]
        return items, str((data or {}).get("next") or "")

    def chat(self, bot_id: str, chat_id: str) -> dict[str, Any]:
        return dict(
            self._request("GET", f"/api/v1/bots/{_segment(bot_id)}/chats/{_segment(chat_id)}")
            or {}
        )

    def messages(
        self, bot_id: str, chat_id: str, *, backward: str | None = None
    ) -> tuple[list[dict[str, Any]], str]:
        """Newest first; ``backward`` pages further back."""
        data = self._request(
            "GET",
            f"/api/v3/bots/{_segment(bot_id)}/chats/{_segment(chat_id)}/messages",
            params={"backward": backward or None},
        )
        items = [item for item in (data or {}).get("list") or [] if isinstance(item, dict)]
        return items, str((data or {}).get("backward") or "")

    def send_text(
        self, bot_id: str, chat_id: str, text: str, *, send_id: str | None = None
    ) -> str:
        send_id = send_id or new_send_id(chat_id)
        self._request(
            "POST",
            f"/api/v1/bots/{_segment(bot_id)}/chats/{_segment(chat_id)}/messages/send",
            json_body={
                "type": "text",
                "text": str(text or "")[:MAX_TEXT],
                "id": "",
                "sendId": send_id,
            },
        )
        return send_id

    def send_file(
        self,
        bot_id: str,
        chat_id: str,
        data: bytes,
        filename: str,
        *,
        send_id: str | None = None,
    ) -> str:
        send_id = send_id or new_send_id(chat_id)
        self._request(
            "POST",
            f"/api/v1/bots/{_segment(bot_id)}/chats/{_segment(chat_id)}/messages/sendFile",
            files={"file": (filename or "file", data)},
            data={"sendId": send_id},
            timeout=120,
        )
        return send_id

    def mark_read(self, bot_id: str, chat_id: str, message_id: str, timestamp: int) -> None:
        self._request(
            "PUT",
            f"/api/v2/bots/{_segment(bot_id)}/chats/{_segment(chat_id)}/markAsRead",
            json_body={"lastMessage": {"messageId": str(message_id), "timestamp": int(timestamp)}},
        )

    def content(
        self,
        bot_id: str,
        content_hash: str,
        *,
        preview: bool = False,
        max_bytes: int = MAX_DOWNLOAD_BYTES,
    ) -> tuple[bytes, str]:
        """A message's image, video, audio or file, with its content type."""
        content_hash = str(content_hash or "")
        if not content_hash or "/" in content_hash or ".." in content_hash:
            raise OAManagerError("Invalid content id.", status=400)
        url = f"{self.content_url}/bot/{_segment(bot_id)}/{_segment(content_hash)}"
        if preview:
            url += "/preview"
        try:
            response = self.session.get(
                url, timeout=120, stream=True, headers={"Referer": f"{self.base_url}/"}
            )
        except requests.RequestException as exc:
            raise OAManagerError(
                f"Could not reach LINE OA Manager ({type(exc).__name__}).", network=True
            ) from None
        if response.status_code in (401, 403):
            raise OAManagerError("The LINE OA Manager session ended.", status=401)
        if response.status_code != 200:
            raise OAManagerError(
                f"LINE content request failed (HTTP {response.status_code}).",
                status=int(response.status_code),
            )
        chunks: list[bytes] = []
        total = 0
        for chunk in response.iter_content(64 * 1024):
            total += len(chunk)
            if total > max_bytes:
                raise OAManagerError("The file is too large.", status=413)
            chunks.append(chunk)
        return b"".join(chunks), str(response.headers.get("Content-Type") or "")

    # -- live events ---------------------------------------------------------
    def streaming_token(self, bot_id: str) -> dict[str, Any]:
        return dict(
            self._request("POST", f"/api/v1/bots/{_segment(bot_id)}/streamingApiToken") or {}
        )

    def streaming_state(self, bot_id: str, connection_id: str, *, idle: bool = True) -> None:
        self._request(
            "PUT",
            f"/api/v1/bots/{_segment(bot_id)}/streaming/state",
            json_body={"connectionId": str(connection_id), "idle": bool(idle)},
        )
