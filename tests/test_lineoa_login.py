"""Signing in to OA Manager with a QR code, against a scripted LINE."""

from __future__ import annotations

import json
from urllib.parse import parse_qsl

import pytest
from lineoa_fakes import FakeResponse, FakeSession

from okline.lineoa.login import (
    OAManagerLoginError,
    OAManagerQrLogin,
    finish_url,
    line_login_form,
    login_page_options,
    redirect_query_inputs,
)

BIZ_LOGIN = "https://account.line.biz/login?redirectUri=x"
LINE_LOGIN = (
    "https://access.line.me/oauth2/v2.1/login?returnUri=%2Foauth2%2Fv2.1%2Fauthorize%3Fa%3D1"
    "&loginChannelId=1576775644&loginState=s"
)
# Today's page: the form is filled in by script from settings the page embeds.
BIZ_SETTINGS = {
    "global": {"language": "en", "csrfToken": "c-1"},
    "page": {
        "lineAutoLoginPath": "/login/line?type=login&redirectUri=r&disableLineAutoLogin=false",
        "lineWebLoginPath": "/login/line?type=login&redirectUri=r&disableLineAutoLogin=true",
    },
}
BIZ_PAGE = (
    '<div data-variables="' + json.dumps(BIZ_SETTINGS).replace("&", "&amp;").replace('"', "&quot;")
    + '"></div><form data-login-with-line-form method="POST">'
    '<input data-csrf-input type="hidden" name="_csrf"><button>LINE account</button></form>'
)
# An older page that rendered the form whole.
OLD_BIZ_PAGE = """
<form action="/profile/back" method="POST"><input type="hidden" name="_csrf" value="zzz"></form>
<form action="/login/line?type=login&amp;redirectUri=r&amp;disableLineAutoLogin=true" method="POST">
  <input type="hidden" name="_csrf" value="c-2"></form>
<form action="/login/line?type=login&amp;redirectUri=r&amp;disableLineAutoLogin=false" method="POST">
  <input type="hidden" name="_csrf" value="c-1"><button>LINE account</button></form>
"""
OPTIONS = {
    "channel-id": 1576775644,
    "qr-code-url": "/qrlogin/v1/session",
    "wait-qr-code-verification-complete-url": "/qrlogin/v1/qr/wait",
    "wait-pin-code-verification-complete-url": "/qrlogin/v1/pin/wait",
}
SLOTS = {
    "serverside_redirect_query": [
        {"type": "hidden", "name": "loginChannelId", "value": "1576775644"},
        {"type": "hidden", "name": "returnUri", "value": "/oauth2/v2.1/authorize/consent?x=1&y=2"},
    ]
}
LINE_PAGE = (
    '<div id="app" data-options="' + json.dumps(OPTIONS).replace('"', "&quot;") + '"'
    ' data-slots="' + json.dumps(SLOTS).replace("&", "&amp;").replace('"', "&quot;") + '"></div>'
)
FINISH = "/oauth2/v2.1/qr/authn"


@pytest.mark.parametrize("page", [BIZ_PAGE, OLD_BIZ_PAGE])
def test_the_line_account_form_is_found(page):
    action, inputs = line_login_form(page)
    assert action == "/login/line?type=login&redirectUri=r&disableLineAutoLogin=false"
    assert inputs == {"_csrf": "c-1"}
    with pytest.raises(OAManagerLoginError) as caught:
        line_login_form("<form action='/login/email'></form>")
    assert caught.value.code == "no_line_login"


def test_login_page_options_are_read():
    options = login_page_options(LINE_PAGE)
    assert options["channel-id"] == 1576775644
    assert options["wait-pin-code-verification-complete-url"] == "/qrlogin/v1/pin/wait"


def test_the_finish_url_is_built_like_the_page_script():
    url = finish_url(f"{FINISH}?dropped=1", LINE_LOGIN, redirect_query_inputs(LINE_PAGE))
    path, _, query = url.partition("?")
    params = dict(parse_qsl(query))
    assert path == FINISH and "dropped" not in params
    assert params == {
        "returnUri": "/oauth2/v2.1/authorize/consent?x=1&y=2",
        "loginChannelId": "1576775644",
        "loginState": "s",
    }


class ScriptedLine:
    """A LINE that walks the QR flow; ``qr_waits`` are the scan results in order."""

    def __init__(self, qr_waits, *, final_url="https://chat.line.biz/", final_page="<html></html>",
                 signed_in=False):
        self.qr_waits = list(qr_waits)
        self.final_url = final_url
        self.final_page = final_page
        self.signed_in = signed_in
        self.qr_sessions = 0

    def __call__(self, call):
        url, method = call["url"], call["method"]
        if url == "https://chat.line.biz/" and method == "GET":
            return FakeResponse(url="https://chat.line.biz/" if self.signed_in else BIZ_LOGIN,
                                content=BIZ_PAGE.encode())
        if url.startswith("https://account.line.biz/login/line") and method == "POST":
            assert call["data"] == {"_csrf": "c-1"}
            return FakeResponse(url=LINE_LOGIN, content=LINE_PAGE.encode())
        if url == "https://access.line.me/qrlogin/v1/session":
            assert call["params"]["returnUri"] == "/oauth2/v2.1/authorize?a=1"
            assert call["params"]["channelId"] == 1576775644
            self.qr_sessions += 1
            return FakeResponse(body={"qrCodePath": f"/qrlogin/v1/qr/Q{self.qr_sessions}"})
        if url.startswith("https://access.line.me/qrlogin/v1/qr/Q"):
            return FakeResponse(content=b"PNG" + url[-1].encode(), headers={"Content-Type": "image/png"})
        if url == "https://access.line.me/qrlogin/v1/qr/wait":
            result = self.qr_waits.pop(0)
            return result if isinstance(result, FakeResponse) else FakeResponse(body=result)
        if url == "https://access.line.me/qrlogin/v1/pin/wait":
            return FakeResponse(body={"redirectPath": f"{FINISH}?dropped=1"})
        if url.startswith(f"https://access.line.me{FINISH}?"):
            params = dict(parse_qsl(url.partition("?")[2]))
            if params.get("loginState") != "s" or "dropped" in params:
                return FakeResponse(400, url=url, content=b"<title>LINE Login</title>")
            return FakeResponse(url=self.final_url, content=self.final_page.encode())
        if url == "https://chat.line.biz/api/v1/me":
            return FakeResponse(body={"bizId": "biz-1", "name": "Toon"})
        if url == "https://chat.line.biz/api/v1/bots":
            return FakeResponse(body={"list": [{"botId": "U" + "a" * 32, "name": "Hotman"}]})
        raise AssertionError(f"unexpected {method} {url}")


def _run(line: ScriptedLine):
    session = FakeSession(line)
    session.cookies.set("ses", "1", domain="chat.line.biz", path="/")
    qrs, pins = [], []
    login = OAManagerQrLogin(session=session)
    result = login.run(lambda data, kind: qrs.append((data, kind)), pins.append, wait_seconds=30)
    return result, qrs, pins


def test_qr_scan_with_pin_signs_in_and_lists_the_oas():
    result, qrs, pins = _run(ScriptedLine([{"pinCode": "4821", "redirectPath": ""}]))
    assert qrs == [(b"PNG1", "image/png")] and pins == ["4821"]
    assert result.me["name"] == "Toon" and result.bots[0]["name"] == "Hotman"
    assert any(cookie["name"] == "ses" for cookie in result.cookies)


def test_an_expired_qr_code_is_replaced():
    line = ScriptedLine([{"errorCode": 408}, FakeResponse(504),
                         {"redirectPath": f"{FINISH}?dropped=1"}])
    _result, qrs, pins = _run(line)
    assert [data for data, _kind in qrs] == [b"PNG1", b"PNG2", b"PNG3"] and pins == []


def test_an_open_session_needs_no_qr():
    _result, qrs, _pins = _run(ScriptedLine([], signed_in=True))
    assert qrs == []


def test_a_consent_page_is_explained():
    line = ScriptedLine([{"redirectPath": f"{FINISH}?dropped=1"}],
                        final_url="https://access.line.me/oauth2/v2.1/authorize/consent?x=1")
    with pytest.raises(OAManagerLoginError) as caught:
        _run(line)
    assert caught.value.code == "consent_needed"


def test_a_line_account_without_business_id_is_explained():
    # LINE Business ID offers to create an account; we never do that for people.
    signup = ('<input data-line-signup-name-input type="text">'
              '<button data-create-account-button>Create</button>')
    line = ScriptedLine([{"redirectPath": f"{FINISH}?dropped=1"}],
                        final_url="https://account.line.biz/login/line-callback?code=c&state=s",
                        final_page=signup)
    with pytest.raises(OAManagerLoginError) as caught:
        _run(line)
    assert caught.value.code == "no_business_id"
    assert "Add member" in caught.value.message


def test_cancelling_stops_waiting():
    session = FakeSession(ScriptedLine([{"pinCode": "1"}]))
    login = OAManagerQrLogin(session=session)
    with pytest.raises(OAManagerLoginError) as caught:
        login.run(lambda *_: None, lambda _pin: None, should_stop=lambda: True)
    assert caught.value.code == "cancelled"
