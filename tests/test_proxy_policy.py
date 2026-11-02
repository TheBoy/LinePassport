"""The God page can require a proxy for LINE and LINE OA accounts."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
import requests

from okline import webapp
from okline.webapp import OkLineWebHandler, OkLineWebServer, WebConfig, WebError, WebState

BOT = "U" + "a" * 32
PROXY = {"proxyEnabled": True, "proxyScheme": "http", "proxyHost": "th.proxy.test",
         "proxyPort": "8000", "proxyUsername": "shop", "proxyPassword": "pw"}


def _make_config(tmp_path) -> WebConfig:
    root = tmp_path / ".okline"
    return WebConfig(
        host="127.0.0.1",
        port=0,
        tokens_file=str(tmp_path / "tokens.json"),
        state_dir=str(root),
        accounts_file=str(root / "accounts.json"),
        accounts_dir=str(root / "accounts"),
        schedules_file=str(root / "schedules.json"),
        auth_file=str(root / "auth.json"),
    )


class QuietOA:
    def __init__(self, cookies=None, proxy=None):
        self.base_url = "https://chat.line.biz"

    def chats(self, bot_id, *, next_cursor=None, limit=25):
        return [], ""

    def streaming_token(self, bot_id):
        raise webapp.OAManagerError("offline", network=True)

    def refresh_session(self):
        return False

    def cookies(self):
        return []

    def close(self):
        pass


class FakeLogin:
    proxies: list = []

    def __init__(self, *, cookies=None, proxy=None, **kwargs):
        FakeLogin.proxies.append(proxy)
        self.trace = []
        self.client = QuietOA()

    def run(self, on_qr, on_pin, *, wait_seconds=0, should_stop=lambda: False):
        return SimpleNamespace(
            cookies=[{"name": "ses", "value": "1", "domain": "chat.line.biz", "path": "/"}],
            me={}, bots=[{"botId": BOT, "name": "Hotman", "basicSearchId": "@065mkrte"}])


@pytest.fixture
def state(tmp_path, monkeypatch) -> Iterator[WebState]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(webapp, "OAManagerQrLogin", FakeLogin)
    FakeLogin.proxies = []
    web = WebState(_make_config(tmp_path))
    web.line_oa.client_factory = QuietOA
    # A LINE QR sign-in would reach LINE: the worker is not needed here.
    monkeypatch.setattr(web, "_login_worker", lambda *args, **kwargs: None)
    web.web_auth.setup("owner@example.com", "owner-pass")
    try:
        yield web
    finally:
        web.close()


def _owner(state):
    return state.web_auth._find_user(state.web_auth.data["users"][0]["id"])


def _require(state, line=False, line_oa=False):
    return state.save_god_system_settings({"proxyRequired": {"line": line, "line_oa": line_oa}})


def test_the_policy_is_off_until_the_god_page_turns_it_on(state):
    assert state.proxy_policy() == {"line": False, "line_oa": False}
    saved = _require(state, line=True)
    assert saved["proxyRequired"] == {"line": True, "line_oa": False}
    assert state.proxy_policy() == {"line": True, "line_oa": False}
    with pytest.raises(WebError):
        state.save_god_system_settings({"proxyRequired": "yes"})


def test_adding_or_reconnecting_line_needs_a_proxy_when_required(state):
    owner = _owner(state)
    state.start_login(user=owner)  # nothing required yet
    state.cancel_login(owner)
    _require(state, line=True)
    with pytest.raises(WebError) as exc:
        state.start_login(user=owner)
    assert exc.value.code == "proxy_required"
    state.start_login(user=owner, proxy_url="http://th.proxy.test:8000")


def _wait_done(state, owner):
    import time

    deadline = time.time() + 5
    while time.time() < deadline:
        login = state.oa_login_state(owner)
        if login["state"] in {"done", "error", "pick"}:
            return login
        time.sleep(0.02)
    raise AssertionError(state.oa_login_state(owner))


def test_line_oa_sign_in_needs_a_proxy_when_required(state):
    owner = _owner(state)
    _require(state, line_oa=True)
    with pytest.raises(WebError) as exc:
        state.oa_login_start({}, owner)
    assert exc.value.code == "proxy_required"
    state.oa_login_start(dict(PROXY), owner)
    login = _wait_done(state, owner)
    assert login["state"] == "done" and FakeLogin.proxies[-1].startswith("http://shop:pw@")
    account_id = login["accountId"]
    # Its proxy cannot be switched off, renaming still works.
    with pytest.raises(WebError) as exc:
        state.update_account(account_id, "Hotman", owner, proxy_enabled=False)
    assert exc.value.code == "proxy_required"
    state.update_account(account_id, "Hotman Shop", owner)
    assert state.account_store.get(account_id)["label"] == "Hotman Shop"


def test_an_old_account_without_proxy_must_get_one_before_proxy_edits(state):
    owner = _owner(state)
    state.oa_login_start({}, owner)  # added before the rule
    account_id = _wait_done(state, owner)["accountId"]
    _require(state, line_oa=True)
    assert state.god_system_settings()["withoutProxy"] == {"line": 0, "line_oa": 1}
    with pytest.raises(WebError) as exc:
        state.update_account(account_id, "Hotman", owner, proxy_enabled=True)
    assert exc.value.code == "proxy_required"
    with pytest.raises(WebError) as exc:  # reconnecting goes through its (missing) proxy
        state.oa_login_start({"reconnectAccountId": account_id}, owner)
    assert exc.value.code == "proxy_required"
    state.update_account(account_id, "Hotman", owner, proxy_enabled=True,
                         proxy_url="http://th.proxy.test:8000")
    assert state.god_system_settings()["withoutProxy"]["line_oa"] == 0


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    server = OkLineWebServer(("127.0.0.1", 0), OkLineWebHandler)
    server.state = WebState(_make_config(tmp_path))
    server.state.web_auth.setup("owner@example.com", "owner-pass")
    server.state.web_auth.ensure_god("god", "god-password")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.state.close()
        server.server_close()


def test_only_the_god_page_changes_the_policy_and_pages_learn_it(live):
    owner = requests.Session()
    owner.post(f"{live}/api/auth/login", json={"email": "owner@example.com", "password": "owner-pass"},
               timeout=5).raise_for_status()
    assert owner.get(f"{live}/api/god/settings", timeout=5).status_code in (401, 403)
    assert owner.get(f"{live}/api/auth/status", timeout=5).json()["proxyPolicy"] == {
        "line": False, "line_oa": False}

    god = requests.Session()
    god.post(f"{live}/api/god/login", json={"username": "god", "password": "god-password"},
             timeout=5).raise_for_status()
    assert god.get(f"{live}/api/god/settings", timeout=5).json()["proxyRequired"]["line"] is False
    saved = god.post(f"{live}/api/god/settings",
                     json={"proxyRequired": {"line": True, "line_oa": True}}, timeout=5)
    assert saved.status_code == 200 and saved.json()["proxyRequired"] == {"line": True, "line_oa": True}
    assert owner.get(f"{live}/api/auth/status", timeout=5).json()["proxyPolicy"] == {
        "line": True, "line_oa": True}
