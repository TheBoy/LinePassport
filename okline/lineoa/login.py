"""Sign in to LINE Official Account Manager by scanning a QR code with LINE.

OA Manager signs people in through LINE Business ID, which offers "log in
with your LINE account". That goes through LINE Login, whose QR flow is plain
HTTP: open a QR session, wait until LINE on the phone scans it, show the PIN
the phone asks for, wait until it is entered, then follow the redirects back
to chat.line.biz. Signing in with an email address is not offered here: that
page is protected by reCAPTCHA and asks for a code from email.
"""

from __future__ import annotations

import html
import json
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, urlencode, urljoin, urlsplit

import requests

from .client import CHAT_BASE, OAManagerClient, OAManagerError, export_cookies

ACCESS_BASE = "https://access.line.me"
# LINE Login holds each wait open for up to about three minutes.
LONG_POLL_SECONDS = 200
WAIT_RETRIES = 5


class OAManagerLoginError(Exception):
    def __init__(self, message: str, code: str = "login_failed") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


@dataclass
class OAManagerLoginResult:
    cookies: list[dict[str, Any]]
    me: dict[str, Any] = field(default_factory=dict)
    bots: list[dict[str, Any]] = field(default_factory=list)


class _FormParser(HTMLParser):
    """Collects each form's action and hidden inputs."""

    def __init__(self) -> None:
        super().__init__()
        self.forms: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {name: value or "" for name, value in attrs}
        if tag == "form":
            self._current = {"action": values.get("action", ""), "inputs": {}}
            self.forms.append(self._current)
        elif tag == "input" and self._current is not None and values.get("name"):
            self._current["inputs"][values["name"]] = values.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if tag == "form":
            self._current = None


def _page_value(text: str, key: str) -> Any:
    """A string or number a page embeds as JSON (``"key":"value"``), or None."""
    match = re.search(r'"' + re.escape(key) + r'"\s*:\s*("(?:[^"\\]|\\.)*"|-?\d+)', text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def line_login_form(page: str) -> tuple[str, dict[str, str]]:
    """Where the "LINE account" button of the LINE Business ID login page posts, and what.

    The page fills that form in the browser from settings it embeds as JSON
    (``lineAutoLoginPath``, ``csrfToken``); older pages render the form whole.
    """
    text = html.unescape(page)
    action = _page_value(text, "lineAutoLoginPath")
    if isinstance(action, str) and action.startswith("/login/line"):
        return action, {"_csrf": str(_page_value(text, "csrfToken") or "")}
    parser = _FormParser()
    parser.feed(page)
    candidates = [form for form in parser.forms if str(form["action"]).startswith("/login/line")]
    if not candidates:
        raise OAManagerLoginError(
            "LINE Business ID did not offer signing in with a LINE account.", "no_line_login"
        )
    preferred = [form for form in candidates if "disableLineAutoLogin=false" in form["action"]]
    form = (preferred or candidates)[0]
    return str(form["action"]), dict(form["inputs"])


def redirect_query_inputs(page: str) -> list[tuple[str, str]]:
    """The hidden ``serverside_redirect_query`` inputs LINE Login's page embeds."""
    text = html.unescape(page)
    match = re.search(r'"serverside_redirect_query"\s*:\s*\[', text)
    if not match:
        return []
    try:
        items, _end = json.JSONDecoder().raw_decode(text, match.end() - 1)
    except ValueError:
        return []
    return [
        (str(item["name"]), str(item.get("value") or ""))
        for item in items
        if isinstance(item, dict) and item.get("name")
    ]


def finish_url(redirect_path: str, page_url: str, inputs: list[tuple[str, str]]) -> str:
    """Where LINE Login's page goes once the scan (and PIN) is confirmed.

    Like the page's script: the redirect path keeps only its path; the query
    is the login page's own query with the embedded inputs laid over it.
    """
    params: dict[str, str] = {}
    for key, values in parse_qs(urlsplit(page_url).query, keep_blank_values=True).items():
        params[key] = values[-1]
    for key, value in inputs:
        params[key] = value
    return f"{urlsplit(redirect_path).path}?{urlencode(params)}"


def login_page_options(page: str) -> dict[str, Any]:
    """Options LINE Login embeds in its page (QR endpoints, channel id)."""
    text = html.unescape(page)
    options: dict[str, Any] = {}
    for key in (
        "channel-id",
        "return-uri",
        "qr-code-url",
        "wait-qr-code-verification-complete-url",
        "wait-pin-code-verification-complete-url",
    ):
        value = _page_value(text, key)
        if value is not None:
            options[key] = value
    return options


class OAManagerQrLogin:
    def __init__(
        self,
        *,
        proxy: str | None = None,
        session: requests.Session | None = None,
        cookies: list[dict[str, Any]] | None = None,
        chat_base: str = CHAT_BASE,
        access_base: str = ACCESS_BASE,
    ) -> None:
        self.chat_base = chat_base.rstrip("/")
        self.access_base = access_base.rstrip("/")
        self.proxy = proxy
        self.client = OAManagerClient(cookies, proxy=proxy, session=session, base_url=chat_base)
        self.session = self.client.session
        # Pages visited (status, host and path; never query strings), for diagnosis.
        self.trace: list[str] = []
        self.landed: requests.Response | None = None

    def _note(self, response: requests.Response) -> requests.Response:
        for hop in [*(getattr(response, "history", None) or []), response]:
            parts = urlsplit(str(getattr(hop, "url", "") or ""))
            self.trace.append(f"{getattr(hop, 'status_code', '?')} {parts.netloc}{parts.path}")
        return response

    def _get(self, url: str, **kwargs: Any) -> requests.Response:
        try:
            return self.session.get(url, timeout=kwargs.pop("timeout", 30), **kwargs)
        except requests.RequestException as exc:
            raise OAManagerLoginError(
                f"Could not reach LINE ({type(exc).__name__}).", "network"
            ) from None

    def _on_chat(self, url: str) -> bool:
        return urlsplit(url).netloc == urlsplit(self.chat_base).netloc

    def _finish(self) -> OAManagerLoginResult:
        try:
            me = self.client.me()
            bots = self.client.bots()
        except OAManagerError as exc:
            raise OAManagerLoginError(
                "Signed in to LINE, but OA Manager did not open.", "login_failed"
            ) from exc
        return OAManagerLoginResult(cookies=export_cookies(self.session.cookies), me=me, bots=bots)

    def _wait(self, url: str, channel_id: Any, should_stop: Callable[[], bool]) -> dict[str, Any]:
        """One long poll; ``{"expired": True}`` when LINE says the code timed out."""
        failures = 0
        while not should_stop():
            response = self._get(
                url,
                params={"_": int(time.time() * 1000), "channelId": channel_id},
                timeout=LONG_POLL_SECONDS,
            )
            if response.status_code == 504:
                return {"expired": True}
            if response.status_code == 200:
                try:
                    data = response.json()
                except ValueError:
                    data = {}
                if isinstance(data, dict) and int(data.get("errorCode") or 0) == 408:
                    return {"expired": True}
                return data if isinstance(data, dict) else {}
            failures += 1
            if failures > WAIT_RETRIES:
                raise OAManagerLoginError(
                    f"LINE stopped answering (HTTP {response.status_code}).", "login_failed"
                )
            time.sleep(0.5)
        raise OAManagerLoginError("Sign-in was cancelled.", "cancelled")

    def run(
        self,
        on_qr: Callable[[bytes, str], None],
        on_pin: Callable[[str], None],
        *,
        wait_seconds: float = 300.0,
        should_stop: Callable[[], bool] = lambda: False,
    ) -> OAManagerLoginResult:
        page = self._note(self._get(f"{self.chat_base}/"))
        if self._on_chat(page.url):
            return self._finish()
        action, inputs = line_login_form(page.text)
        try:
            page = self.session.post(
                urljoin(page.url, action),
                data={key: value for key, value in inputs.items() if key == "_csrf"},
                headers={"Origin": f"{urlsplit(page.url).scheme}://{urlsplit(page.url).netloc}",
                         "Referer": page.url},
                timeout=30,
            )
        except requests.RequestException as exc:
            raise OAManagerLoginError(
                f"Could not reach LINE ({type(exc).__name__}).", "network"
            ) from None
        self._note(page)
        if self._on_chat(page.url):
            return self._finish()
        options = login_page_options(page.text)
        qr_url = options.get("qr-code-url")
        wait_qr = options.get("wait-qr-code-verification-complete-url")
        wait_pin = options.get("wait-pin-code-verification-complete-url")
        channel_id = options.get("channel-id") or (
            parse_qs(urlsplit(page.url).query).get("loginChannelId") or [""]
        )[0]
        return_uri = options.get("return-uri") or (
            parse_qs(urlsplit(page.url).query).get("returnUri") or [""]
        )[0]
        if not (qr_url and wait_qr and wait_pin and channel_id):
            raise OAManagerLoginError("LINE Login did not offer a QR code.", "no_qr")
        login_url = page.url
        redirect_inputs = redirect_query_inputs(page.text)

        deadline = time.monotonic() + wait_seconds
        redirect = ""
        while not redirect:
            if should_stop():
                raise OAManagerLoginError("Sign-in was cancelled.", "cancelled")
            if time.monotonic() > deadline:
                raise OAManagerLoginError("The QR code was not scanned in time.", "qr_expired")
            opened = self._get(
                urljoin(self.access_base, str(qr_url)),
                params={"_": int(time.time() * 1000), "channelId": channel_id,
                        "returnUri": return_uri},
            )
            try:
                qr_path = str((opened.json() or {}).get("qrCodePath") or "")
            except ValueError:
                qr_path = ""
            if not qr_path:
                raise OAManagerLoginError("LINE Login did not create a QR code.", "no_qr")
            image = self._get(urljoin(self.access_base, qr_path))
            if image.status_code != 200 or not image.content:
                raise OAManagerLoginError("LINE Login did not send the QR image.", "no_qr")
            on_qr(image.content, str(image.headers.get("Content-Type") or "image/png"))
            scanned = self._wait(urljoin(self.access_base, str(wait_qr)), channel_id, should_stop)
            if scanned.get("expired"):
                continue  # a fresh QR code, until the deadline
            pin = str(scanned.get("pinCode") or "")
            redirect = str(scanned.get("redirectPath") or "")
            if pin:
                on_pin(pin)
                confirmed = self._wait(
                    urljoin(self.access_base, str(wait_pin)), channel_id, should_stop
                )
                if confirmed.get("expired"):
                    raise OAManagerLoginError(
                        "The PIN was not entered on the phone in time.", "pin_expired"
                    )
                redirect = str(confirmed.get("redirectPath") or redirect)
            if not redirect:
                raise OAManagerLoginError("LINE Login did not finish signing in.", "login_failed")

        target = finish_url(redirect, login_url, redirect_inputs)
        landed = self._note(self._get(urljoin(self.access_base, target)))
        self.landed = landed
        if not self._on_chat(landed.url):
            stopped = urlsplit(landed.url)
            if "data-line-signup-name-input" in landed.text:
                # LINE Business ID offers to create an account: this LINE
                # account has none, so it cannot be a member of any OA.
                raise OAManagerLoginError(
                    "This LINE account has no LINE Business ID, so it does not manage any "
                    "LINE OA yet. In LINE Official Account Manager, add it as a member "
                    "(Settings > Manage permissions > Add member), accept the invitation "
                    "with this LINE account, then scan again.",
                    "no_business_id",
                )
            if "consent" in stopped.path:
                raise OAManagerLoginError(
                    "LINE asks this account to agree to LINE Business ID first. Sign in to "
                    "LINE Official Account Manager once in a browser with it, then try again.",
                    "consent_needed",
                )
            raise OAManagerLoginError(
                "LINE did not hand the sign-in back to OA Manager "
                f"(stopped at {stopped.netloc}{stopped.path}).",
                "login_failed",
            )
        return self._finish()


__all__ = [
    "OAManagerLoginError",
    "OAManagerLoginResult",
    "OAManagerQrLogin",
    "finish_url",
    "line_login_form",
    "login_page_options",
    "redirect_query_inputs",
]
