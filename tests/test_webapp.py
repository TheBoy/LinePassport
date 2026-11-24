from __future__ import annotations

import base64
import datetime as dt
import json
import os
import struct
import sys
import threading
import time
from collections import Counter, deque
from collections.abc import Iterator
from html.parser import HTMLParser
from http import HTTPStatus

import pytest
import requests

from okline import OkLine
from okline import webapp as webapp_module
from okline.crm.errors import CrmError
from okline.exceptions import LineAuthError, LineLoginRequired
from okline.obs import encode_message_talk_meta
from okline.webapp import (
    GOD_HTML,
    GOD_SIGN_IN_PATH,
    INDEX_HTML,
    OkLineWebHandler,
    OkLineWebServer,
    WebAuth,
    WebConfig,
    WebError,
    WebState,
    _advance_after_success,
    _contents_from_api_json,
    _next_repeat_epoch,
    _schedule_from_body,
)


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


@pytest.fixture
def web_state(tmp_path, monkeypatch) -> Iterator[WebState]:
    # Never let an ambient DATABASE_URL redirect file-backed test state.
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    state = WebState(_make_config(tmp_path))
    try:
        yield state
    finally:
        state.close()


@pytest.fixture
def live_server(tmp_path, monkeypatch) -> Iterator[str]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    server = OkLineWebServer(("127.0.0.1", 0), OkLineWebHandler)
    server.state = WebState(_make_config(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base
    finally:
        server.shutdown()
        server.state.close()
        server.server_close()


@pytest.fixture
def god_live_server(tmp_path, monkeypatch) -> Iterator[str]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    server = OkLineWebServer(("127.0.0.1", 0), OkLineWebHandler)
    server.state = WebState(_make_config(tmp_path))
    server.state.web_auth.setup("owner@example.com", "owner-pass")
    server.state.web_auth.ensure_god("god", "god-password")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        yield base
    finally:
        server.shutdown()
        server.state.close()
        server.server_close()


def _seed_account(state: WebState, account_id: str = "acct") -> None:
    now = "2026-07-11T12:00:00"
    state.account_store.data["accounts"] = [
        {
            "id": account_id,
            "label": "Test LINE",
            "tokenFile": "",
            "createdAt": now,
            "updatedAt": now,
        }
    ]
    state.account_store.data["activeAccountId"] = account_id
    state.account_store.save()


@pytest.mark.parametrize(
    "url",
    [
        "http://proxy.example:8080",
        "https://user:password@proxy.example:8443",
        "socks5://127.0.0.1:1080",
        "socks5h://user:password@proxy.example:1080",
    ],
)
def test_proxy_url_validation_accepts_supported_schemes(url):
    assert webapp_module._validate_proxy_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "",
        "ftp://proxy.example:21",
        "http://",
        "http://proxy.example:bad",
        "http://proxy.example:8080/path",
        "http://proxy.example:8080?token=secret",
    ],
)
def test_proxy_url_validation_rejects_unsafe_shapes(url):
    with pytest.raises(WebError):
        webapp_module._validate_proxy_url(url)


@pytest.mark.parametrize(
    ("fields", "expected"),
    [
        (
            {
                "proxyScheme": "http",
                "proxyHost": "proxy.example.com",
                "proxyPort": "8080",
                "proxyUsername": "user@example.com",
                "proxyPassword": "p:a/ss",
            },
            "http://user%40example.com:p%3Aa%2Fss@proxy.example.com:8080",
        ),
        (
            {
                "proxyScheme": "socks5h",
                "proxyHost": "2001:db8::1",
                "proxyPort": 1080,
                "proxyUsername": "",
                "proxyPassword": "",
            },
            "socks5h://[2001:db8::1]:1080",
        ),
    ],
)
def test_proxy_fields_build_valid_encoded_url(fields, expected):
    assert webapp_module._proxy_url_from_fields(fields) == expected


@pytest.mark.parametrize(
    "fields",
    [
        {"proxyScheme": "ftp", "proxyHost": "proxy.example", "proxyPort": "21"},
        {"proxyScheme": "http", "proxyHost": "", "proxyPort": "8080"},
        {"proxyScheme": "http", "proxyHost": "http://proxy.example", "proxyPort": "8080"},
        {"proxyScheme": "http", "proxyHost": "proxy.example:8080", "proxyPort": "8080"},
        {"proxyScheme": "http", "proxyHost": "proxy.example", "proxyPort": "0"},
        {"proxyScheme": "http", "proxyHost": "proxy.example", "proxyPort": "65536"},
    ],
)
def test_proxy_fields_reject_invalid_values(fields):
    with pytest.raises(WebError):
        webapp_module._proxy_url_from_fields(fields)


def test_proxy_fields_keep_legacy_api_and_ignore_disabled_values():
    legacy = "socks5://proxy.example:1080"
    assert webapp_module._proxy_url_from_fields({"proxyUrl": legacy}) == legacy
    assert (
        webapp_module._proxy_url_from_fields(
            {
                "proxyEnabled": False,
                "proxyScheme": "http",
                "proxyHost": "bad host",
                "proxyPort": "bad",
            }
        )
        is None
    )
    assert webapp_module._proxy_url_from_fields({"proxyHost": "", "proxyPort": ""}) is None


def test_account_proxy_is_encrypted_hidden_and_applied(web_state):
    _seed_account(web_state)
    proxy_url = "socks5h://proxy-user:proxy-pass@proxy.example:1080"
    stored = web_state._encrypt_secret(proxy_url)
    web_state.account_store.data["accounts"][0]["proxyUrl"] = stored
    web_state.account_store.save()

    account = web_state.account_store.list_accounts()[0]
    assert account["proxyConfigured"] is True
    assert "proxyUrl" not in account
    assert proxy_url not in json.dumps(web_state.account_store.data)

    api = web_state.get_api("acct", require_auth=False)
    try:
        assert api.transport.session.proxies == {"http": proxy_url, "https": proxy_url}
    finally:
        web_state.close_api("acct")


def test_updating_account_proxy_replaces_cached_client(web_state):
    _seed_account(web_state)
    first_api = web_state.get_api("acct", require_auth=False)
    assert web_state.apis["acct"] is first_api

    result = web_state.update_account(
        "acct",
        "Proxy Account",
        proxy_enabled=True,
        proxy_url="http://user:secret@proxy.example:8080",
    )

    assert result["account"]["proxyConfigured"] is True
    assert "proxyUrl" not in result["account"]
    assert "acct" not in web_state.apis
    encrypted = web_state.account_store.data["accounts"][0]["proxyUrl"]
    assert encrypted.startswith("fernet:")

    web_state.update_account("acct", "Proxy Account", proxy_enabled=True)
    assert web_state.account_store.data["accounts"][0]["proxyUrl"] == encrypted

    web_state.update_account("acct", "Proxy Account", proxy_enabled=False)
    assert "proxyUrl" not in web_state.account_store.data["accounts"][0]


def test_proxy_credentials_are_redacted_from_log_details():
    detail = webapp_module._safe_log_detail(
        "request failed through socks5://proxy-user:proxy-pass@proxy.example:1080"
    )
    assert "proxy-user" not in detail
    assert "proxy-pass" not in detail
    assert "socks5://proxy.example:1080" in detail


def test_a_url_the_parser_refuses_keeps_no_secrets_in_log_details():
    # An error text from an upstream server can hold a URL with an unbalanced bracket.
    for text, kept in (
        ("proxy said http://user:secret@10.0.0.5:8080] refused", "http://10.0.0.5:8080]"),
        ("upstream [http://admin:s3cret@10.0.0.5:11434] timed out", "http://10.0.0.5:11434]"),
        ("see http://[fd00::1/v1/models?token=SECRET123 now", "http://[fd00::1/v1/models?..."),
        ("[https://gw.example.test]/v1?token=TOPSECRET", "https://gw.example.test]/v1?..."),
        ("[https://u:p@ss@gw.example.test]/x#frag-SECRET", "https://gw.example.test]/x?..."),
        ("https://obs.line-apps.com]/r/talk/m/123?x=1", "https://obs.line-apps.com]/..."),
        # An "@" after the host (an e-mail in the query, a nested URL) hides nothing.
        ("see http://[fd00::1/cb?email=a@b.com&token=QSECRET1 now", "see http://[fd00::1/cb?... now"),
        ("GET https://[2001:db8::1:8443/data?email=me@shop.example&apikey=QSECRET2 failed",
         "https://[2001:db8::1:8443/data?..."),
        ("upstream [http://10.0.0.5:11434]#state=1&mail=a@FSECRET3", "http://10.0.0.5:11434]?..."),
        ("https://obs.line-apps.com]/r/talk/m/x@y/MEDIASECRET6", "https://obs.line-apps.com]/..."),
        ("see [https://api.example.com](https://api.example.com/v1?email=a@b.com&key=MDSECRET7) ok",
         "see [https://...) ok"),
        # A password cut short by ",", ")", ">" or a space next to a bracket.
        ("proxy http://us[er:PWHEAD8,PWTAIL8@10.0.0.5:8080 failed", "proxy http://...,"),
        ("proxy http://user:PW[HEAD9)PWTAIL9@10.0.0.5:8080 failed", "proxy http://...)"),
        ("proxy socks5h://user:PW[HEAD11>PWTAIL11@10.0.0.5:1080 failed", "proxy socks5h://...>"),
        # Fullwidth "@" and "/" are delimiters too.
        ("proxy http://user:PWSECRET13\uff20proxy.example:8080 refused", "proxy http://proxy.example:8080 refused"),
        ("proxy http://user:PWSECRET14\ufe6bproxy.example:8080 refused", "proxy http://proxy.example:8080 refused"),
        ("https://obs.line-apps.com\uff0fr\uff0ftalk\uff0fMEDIASECRET15", "https://obs.line-apps.com/..."),
    ):
        detail = webapp_module._safe_log_detail(text)
        assert kept in detail, (text, detail)
        for secret in ("user:", "secret", "s3cret", "SECRET", "u:p", "/r/talk", "PWHEAD", "HEAD9",
                       "HEAD11", "MEDIA"):
            assert secret not in detail, (text, detail)
    # A direct caller gets a hidden URL rather than a crash for anything odd.
    for odd in ("ht\ttps://user:Pa55word@[2001:db8::1/feed", "http://user:PW\nTAIL@[fd00::1/x",
                "\ud800://x", "http://[" + "@" * 5000):
        assert "Pa55word" not in webapp_module._safe_url(odd)
        assert "PW" not in webapp_module._safe_url(odd).replace("http", "")
    # A URL that parses is shown exactly as before.
    assert webapp_module._safe_log_detail("see http://user:pw@10.0.0.5:8080/x?y=1 now") == (
        "see http://10.0.0.5:8080/x?... now"
    )


def test_web_ui_configures_proxy_per_line_account():
    assert 'id="loginProxyEnabled"' in INDEX_HTML
    assert 'id="loginProxyScheme"' in INDEX_HTML
    assert 'id="loginProxyHost" type="text"' in INDEX_HTML
    assert 'id="loginProxyPort" type="number"' in INDEX_HTML
    assert 'id="loginProxyUsername" type="text"' in INDEX_HTML
    assert 'id="loginProxyPassword" type="password"' in INDEX_HTML
    assert 'id="loginProxyUrl"' not in INDEX_HTML
    assert 'accountId: state.reconnectAccountId || ""' in INDEX_HTML
    assert 'await post("/api/login/start", {' in INDEX_HTML
    assert "proxyEnabled: enabled.checked" in INDEX_HTML
    assert "proxyScheme: scheme.value" in INDEX_HTML
    assert "proxyHost: host.input.value.trim()" in INDEX_HTML
    assert "proxyPort: port.input.value.trim()" in INDEX_HTML
    assert "proxyUsername: username.input.value.trim()" in INDEX_HTML
    assert "proxyPassword: password.input.value" in INDEX_HTML
    assert "account.proxyConfigured" in INDEX_HTML


def test_file_state_store_serializes_writes(tmp_path, monkeypatch):
    store = webapp_module.FileStateStore(_make_config(tmp_path))
    original = webapp_module._write_json_file
    guard = threading.Lock()
    start = threading.Barrier(6)
    active = 0
    max_active = 0
    errors = []

    def tracked_write(path, value):
        nonlocal active, max_active
        with guard:
            active += 1
            max_active = max(max_active, active)
        threading.Event().wait(0.01)
        try:
            original(path, value)
        finally:
            with guard:
                active -= 1

    monkeypatch.setattr(webapp_module, "_write_json_file", tracked_write)

    def worker(index):
        try:
            start.wait()
            store.set("bot_logs", {"logs": [{"index": index}]})
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=2)

    assert errors == []
    assert max_active == 1


def test_file_state_store_set_many_rolls_back_all_files(tmp_path, monkeypatch):
    store = webapp_module.FileStateStore(_make_config(tmp_path))
    store.set("auth", {"value": "old-auth"})
    store.set("accounts", {"value": "old-accounts"})
    original = webapp_module._write_json_file

    def fail_second_write(path, value):
        if path == store.paths["accounts"]:
            raise OSError("simulated storage failure")
        original(path, value)

    monkeypatch.setattr(webapp_module, "_write_json_file", fail_second_write)
    with pytest.raises(OSError, match="simulated storage failure"):
        store.set_many(
            {
                "auth": {"value": "new-auth"},
                "accounts": {"value": "new-accounts"},
            }
        )

    assert store.get("auth", {}) == {"value": "old-auth"}
    assert store.get("accounts", {}) == {"value": "old-accounts"}


def test_health_endpoint_is_public(live_server):
    response = requests.get(f"{live_server}/healthz", timeout=5)

    assert response.status_code == HTTPStatus.OK
    assert response.json() == {"ok": True}


def test_add_account_wizard_uses_line_name_and_large_pin_display():
    assert "<title>LinePassport</title>" in INDEX_HTML
    assert "versionLabel" not in INDEX_HTML
    assert "OkLine Web" not in INDEX_HTML
    assert "OkLine God" not in GOD_HTML
    assert "LinePassport God" in GOD_HTML
    assert "brand-mark" not in GOD_HTML
    assert 'id="accountNameInput"' not in INDEX_HTML
    assert 'id="loginPin"' not in INDEX_HTML
    assert 'class="pin-display mono" id="loginPinReview"' in INDEX_HTML
    assert "LinePassport will use the display name from your LINE account." in INDEX_HTML
    assert "await refreshStatus(true);" in INDEX_HTML

    success_start = INDEX_HTML.index('if (data.state === "success")')
    success_end = INDEX_HTML.index('if (data.state === "error")', success_start)
    success_block = INDEX_HTML[success_start:success_end]
    assert 'setTab("line")' in success_block
    assert 'setView("workspace")' in success_block
    assert success_block.index('setView("workspace")') < success_block.index(
        "await refreshStatus(true)"
    )


def test_web_ui_has_tab_bar_subtabs_and_menu_toggles():
    # Top-level tab bar: LINE / Tools / Bot.
    for tab in ("line", "tools", "bot"):
        assert f'data-tab="{tab}"' in INDEX_HTML
        assert f'data-tab-panel="{tab}"' in INDEX_HTML
    for key in ('"tabs.line"', '"tabs.tools"', '"tabs.bot"'):
        assert key in INDEX_HTML

    # Contacts sub-tabs replace the old header buttons.
    assert '"contacts.tab_people"' in INDEX_HTML
    assert '"contacts.tab_groups"' in INDEX_HTML
    assert 'class="line-list-tabbar"' in INDEX_HTML
    assert 'id="contactsRefreshButton"' in INDEX_HTML
    assert 'class="line-list-refresh"' not in INDEX_HTML
    assert (
        INDEX_HTML.index('id="loadGroupsButton"')
        < INDEX_HTML.index('id="contactsRefreshButton"')
        < INDEX_HTML.index('id="contactSearchRow"')
    )


def test_account_switch_loading_overlay_returns_to_line_tab():
    assert 'id="accountSwitchLoading"' in INDEX_HTML
    assert 'class="account-switch-overlay hidden"' in INDEX_HTML
    assert "account-switch-card" in INDEX_HTML
    assert 'data-i18n="accounts.switching"' in INDEX_HTML
    assert 'role="status" aria-live="polite"' in INDEX_HTML
    assert "function setAccountSwitchLoading" in INDEX_HTML

    start = INDEX_HTML.index("async function selectAccount")
    end = INDEX_HTML.index("async function refreshStatus", start)
    block = INDEX_HTML[start:end]
    assert 'setTab("line")' in block
    assert "setAccountSwitchLoading(true)" in block
    assert '$("appShell").setAttribute("aria-busy", String(on))' in INDEX_HTML
    assert "finally" in block
    assert "setAccountSwitchLoading(false)" in block


def test_profile_details_are_collapsed_until_username_is_opened():
    assert 'id="profileDetailsToggle"' in INDEX_HTML
    assert 'aria-expanded="false" aria-controls="profileDetailsPanel"' in INDEX_HTML
    assert 'id="profileDetailsPanel" aria-hidden="true"' in INDEX_HTML
    assert ".line-me-detail-panel.expanded" in INDEX_HTML
    assert "function setProfileDetailsExpanded(expanded)" in INDEX_HTML
    assert "function toggleProfileDetails()" in INDEX_HTML
    assert '$("profileDetailsToggle").addEventListener("click", toggleProfileDetails);' in INDEX_HTML
    assert "setProfileDetailsExpanded(false);" in INDEX_HTML
    assert '"profile.show_details"' in INDEX_HTML
    assert '"profile.hide_details"' in INDEX_HTML
    assert 'grid-area: info;' in INDEX_HTML
    assert 'grid-area: pills;' in INDEX_HTML
    assert '.line-me > .avatar {' in INDEX_HTML
    assert 'cursor: pointer;' in INDEX_HTML


def test_profile_avatar_loads_through_the_selected_account_session(web_state):
    assert 'class="avatar avatar-lg" id="profileAvatar"' in INDEX_HTML
    assert 'image.src = `/api/profile-avatar?${accountQuery(accountId)}' in INDEX_HTML
    assert 'function renderProfileAvatar(profile, displayName)' in INDEX_HTML
    assert 'image.addEventListener("error"' in INDEX_HTML
    assert '.avatar-image {' in INDEX_HTML
    assert hasattr(OkLineWebHandler, "_profile_avatar")

    _seed_account(web_state)
    web_state.account_store.update_profile(
        "acct",
        {
            "displayName": "Photo account",
            "picturePath": "https://profile.line-scdn.net/photo.jpg",
            "pictureStatus": "v2",
        },
    )
    account = web_state.account_store.get("acct")
    assert account["picturePath"] == "https://profile.line-scdn.net/photo.jpg"
    assert account["pictureStatus"] == "v2"


def test_profile_avatar_endpoint_uses_the_line_transport_session(web_state, monkeypatch):
    _seed_account(web_state)
    picture_url = "https://profile.line-scdn.net/photo.jpg"
    web_state.account_store.update_profile("acct", {"picturePath": picture_url})
    line_session = object()
    seen = {}

    class Transport:
        session = line_session

    class Api:
        transport = Transport()

    class Server:
        state = web_state

    def fake_download(url, *, session=None):
        seen["download"] = (url, session)
        return b"\x89PNG\r\n\x1a\nprofile"

    handler = object.__new__(OkLineWebHandler)
    handler.server = Server()
    handler.current_user = {}
    handler._require_permission = lambda permission: seen.setdefault(
        "permission", permission
    )
    handler._require_account_access = lambda account_id: seen.setdefault(
        "accountId", account_id
    )
    handler._with_api = lambda source, callback: callback(Api())
    handler._bytes = lambda data, content_type, status: seen.update(
        data=data, contentType=content_type, status=status
    )
    monkeypatch.setattr(webapp_module, "_download_line_media_url", fake_download)

    handler._profile_avatar({"accountId": ["acct"]})

    assert seen["permission"] == "read"
    assert seen["accountId"] == "acct"
    assert seen["download"] == (picture_url, line_session)
    assert seen["contentType"] == "image/png"
    assert seen["status"] == HTTPStatus.OK


def test_web_ui_has_incoming_api_wizard_and_management_actions():
    assert 'id="tabIncomingApi"' in INDEX_HTML
    assert 'data-tab-panel="incoming-api"' in INDEX_HTML
    assert 'id="incomingApiWizardPage"' in INDEX_HTML
    assert 'data-incoming-api-step="1"' in INDEX_HTML
    assert 'data-incoming-api-step="4"' in INDEX_HTML
    assert 'id="incomingApiContactsTab"' in INDEX_HTML
    assert 'id="incomingApiGroupsTab"' in INDEX_HTML
    assert 'id="incomingApiStatusOnButton"' in INDEX_HTML
    assert 'id="incomingApiStatusOffButton"' in INDEX_HTML
    assert 'id="incomingApiEncryptState"' in INDEX_HTML
    assert 'id="incomingApiGuidePage"' in INDEX_HTML
    assert 'id="incomingApiGuideLinkSelect"' in INDEX_HTML
    assert 'id="incomingApiGuideGetCode"' in INDEX_HTML
    assert 'id="incomingApiGuidePostTextCode"' in INDEX_HTML
    assert 'id="incomingApiGuideImageUrlCode"' in INDEX_HTML
    assert 'id="incomingApiGuideImageBase64Code"' in INDEX_HTML
    assert "function updateIncomingApiGuideExamples" in INDEX_HTML
    assert 'data-copy-incoming-api-guide="incomingApiGuidePostTextCode"' in INDEX_HTML
    assert "60 requests per minute" in INDEX_HTML
    assert "maximum image size is 30 MB" in INDEX_HTML
    assert "GET {API_URL}?text=" in INDEX_HTML
    assert '"imageUrl": "https://example.com/photo.jpg"' in INDEX_HTML
    assert '"imageBase64": "data:image/png;base64,..."' in INDEX_HTML
    assert '"/api/incoming-links/create"' in INDEX_HTML
    assert '"/api/incoming-links/rotate"' in INDEX_HTML
    assert '"/api/incoming-links/delete"' in INDEX_HTML
    assert "state.incomingApiSelectedTargets.size >= 20" in INDEX_HTML
    assert "active: incomingApiIsActive()" in INDEX_HTML


def test_web_ui_has_clear_stuck_schedule_action():
    assert 'id="clearStuckSchedulesButton"' in INDEX_HTML
    assert '"/api/schedules/clear-stuck"' in INDEX_HTML
    assert "function clearStuckSchedules" in INDEX_HTML
    assert "function isStuckSchedule" in INDEX_HTML
    assert '"scheduler.clear_stuck"' in INDEX_HTML
    assert '"botlog.action.schedule.clear_stuck"' in INDEX_HTML


def test_web_ui_has_pattern_category_management():
    assert 'id="patternCategoryFilter"' in INDEX_HTML
    assert 'id="newPatternCategory"' in INDEX_HTML
    assert 'id="addPatternCategoryButton"' in INDEX_HTML
    assert 'id="openPatternCategoriesButton"' in INDEX_HTML
    assert 'id="closePatternCategoriesButton"' in INDEX_HTML
    assert 'id="patternCategoryManageList"' in INDEX_HTML
    assert 'data-bot-page="pattern-categories"' in INDEX_HTML
    assert '"/api/pattern-categories/create"' in INDEX_HTML
    assert '"/api/pattern-categories/update"' in INDEX_HTML
    assert '"/api/pattern-categories/delete"' in INDEX_HTML
    assert "function populatePatternCategoryControls" in INDEX_HTML
    assert "function renderPatternCategoryManageList" in INDEX_HTML
    assert "function editPatternCategory" in INDEX_HTML
    assert "pattern-category-heading" in INDEX_HTML
    assert '"common.add": {th: "เพิ่ม", en: "Add"}' in INDEX_HTML
    assert 'class="section pattern-filter-panel"' in INDEX_HTML
    assert 'class="pattern-settings-button"' in INDEX_HTML
    assert 'id="patternFormSection"' in INDEX_HTML
    assert 'id="cancelPatternFormButton"' in INDEX_HTML
    assert 'id="patternManageCount"' in INDEX_HTML
    assert "function setPatternFormOpen" in INDEX_HTML
    assert ".pattern-filter-row {" in INDEX_HTML
    assert "#patternCategoryManageList { max-height: none; overflow: visible; }" in INDEX_HTML
    assert "pattern-category-toolbar" not in INDEX_HTML
    assert "pattern-page-grid" not in INDEX_HTML


def test_web_ui_has_pattern_edit_and_delete_actions():
    assert "editingPatternId: null" in INDEX_HTML
    assert 'id="patternFormTitle"' in INDEX_HTML
    assert '"/api/patterns/update"' in INDEX_HTML
    assert "function editPattern(pattern)" in INDEX_HTML
    assert 'actions.className = "item-actions pattern-item-actions";' in INDEX_HTML
    assert "actions.append(use, edit, del);" in INDEX_HTML
    assert '"botlog.action.pattern.update"' in INDEX_HTML


def test_web_ui_has_account_scoped_activity_log_panel():
    assert 'id="tabLogs"' in INDEX_HTML
    assert 'id="tabLogs" type="button" role="tab" aria-selected="false" aria-controls="tabPanelLogs" data-tab="logs" data-requires-permission="read"' in INDEX_HTML
    assert 'id="tabPanelLogs"' in INDEX_HTML
    assert 'data-tab-panel="logs"' in INDEX_HTML
    assert 'id="botNavLogs"' not in INDEX_HTML
    assert 'id="botLogList"' in INDEX_HTML
    assert 'id="botLogRefreshButton"' in INDEX_HTML
    assert 'id="botLogClearButton"' in INDEX_HTML
    assert 'id="botLogSearch"' in INDEX_HTML
    assert 'id="botLogCategoryFilter"' in INDEX_HTML
    assert 'id="botLogStatusFilter"' in INDEX_HTML
    assert 'id="botLogSuccessCount"' in INDEX_HTML
    assert 'id="botLogErrorCount"' in INDEX_HTML
    assert "compactLogValue" in INDEX_HTML
    assert "log-detail" in INDEX_HTML
    assert 'class="bot-terminal"' in INDEX_HTML
    assert 'role="log"' in INDEX_HTML
    assert "linepassport/activity.log" in INDEX_HTML
    assert 'status.textContent = log.ok === false ? "[ERR]" : "[OK]";' in INDEX_HTML
    assert ".bot-log-list {\n      display: block;" in INDEX_HTML
    assert "-webkit-line-clamp" not in INDEX_HTML
    assert "/api/logs" in INDEX_HTML
    assert 'setTab("logs")' in INDEX_HTML
    assert 'activeTab.scrollIntoView({block: "nearest", inline: "nearest"})' in INDEX_HTML

    # Language lives inside the Settings menu; Advanced is always enabled and
    # no longer appears as a menu toggle.
    idx_menu = INDEX_HTML.index('id="settingsMenu"')
    idx_lang = INDEX_HTML.index('id="langToggle"')
    idx_change = INDEX_HTML.index('id="changePasswordMenuButton"')
    assert idx_menu < idx_lang < idx_change
    assert 'id="advancedToggle"' not in INDEX_HTML
    assert '<body class="advanced">' in INDEX_HTML
    assert "state.advanced = true" in INDEX_HTML

    # The redundant single-user identity pills are gone from the header.
    assert 'id="userNamePill"' not in INDEX_HTML
    assert 'id="userRolePill"' not in INDEX_HTML


def test_mobile_workspace_keeps_tabs_inline_and_contacts_scrollable():
    assert ".tabbar,\n      .bot-page-nav {\n        flex-wrap: nowrap;" in INDEX_HTML
    assert ".tabbar .tab,\n      .bot-page-nav button {\n        width: auto;" in INDEX_HTML
    assert '.tab-panel[data-tab-panel="line"].active .line-list {' in INDEX_HTML
    assert "max-height: none;\n        overflow: visible;" in INDEX_HTML
    assert '.tab-panel[data-tab-panel="line"].active .line-rows {' in INDEX_HTML
    assert "min-height: 260px;\n        max-height: min(58dvh, 520px);" in INDEX_HTML


def test_mobile_scheduler_uses_primary_tabs_and_compact_job_actions():
    assert 'class="section-head scheduler-list-head"' in INDEX_HTML
    assert 'class="row compact scheduler-list-actions"' in INDEX_HTML
    assert ".scheduler-list-actions #openBotLogsButton," in INDEX_HTML
    assert (
        ".scheduler-list-actions #scheduleFormToggle {\n        display: none;" in INDEX_HTML
    )
    assert ".schedule-item .item-actions {\n        display: grid;" in INDEX_HTML
    assert 'item.className = "item schedule-item";' in INDEX_HTML


def test_placeholder_chips_stay_in_one_scrollable_row_on_mobile():
    # Schedule form, pattern form, the wizard's message and its AI prompt.
    assert INDEX_HTML.count('class="ph-chips"') == 4
    assert ".ph-chips {\n      display: flex;\n      flex-wrap: nowrap;" in INDEX_HTML
    assert "overflow-x: auto;" in INDEX_HTML
    assert ".ph-chip {\n      flex: 0 0 auto;\n      width: auto;" in INDEX_HTML
    assert (
        '$("scheduleTextHelp").style.display = source === "text" ? "grid" : "none";'
        in INDEX_HTML
    )


def test_web_ui_tab_panels_are_siblings():
    class PanelParser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.stack = []
            self.parents = {}

        def handle_starttag(self, tag, attrs):
            data = dict(attrs)
            node_id = data.get("id", "")
            classes = (data.get("class") or "").split()
            self.stack.append((tag, node_id, classes))
            if node_id in {
                "tabPanelLine",
                "tabPanelAssistant",
                "tabPanelTools",
                "tabPanelBot",
                "tabPanelLogs",
            }:
                self.parents[node_id] = [
                    n_id or ".".join(n_classes) or n_tag
                    for n_tag, n_id, n_classes in self.stack[-4:]
                ]

        def handle_endtag(self, tag):
            for idx in range(len(self.stack) - 1, -1, -1):
                if self.stack[idx][0] == tag:
                    del self.stack[idx:]
                    return

    parser = PanelParser()
    parser.feed(INDEX_HTML)

    assert parser.parents["tabPanelLine"][-2] == "tab-panels"
    assert parser.parents["tabPanelAssistant"][-2] == "tab-panels"
    assert parser.parents["tabPanelBot"][-2] == "tab-panels"
    assert parser.parents["tabPanelLogs"][-2] == "tab-panels"
    assert parser.parents["tabPanelTools"][-2] == "tab-panels"
    assert 'id="authModeToggle"' in INDEX_HTML
    assert '"/api/auth/register"' in INDEX_HTML
    assert 'id="webUsernameInput" type="email"' in INDEX_HTML
    assert 'id="registrationNameInput"' in INDEX_HTML
    assert 'id="newUserEmail" type="email"' in INDEX_HTML
    assert 'displayName: mode === "register"' in INDEX_HTML
    assert 'select.value = "member"' in INDEX_HTML
    assert "accountIds: checkedAccountIds" not in INDEX_HTML
    assert '"Each member has a private workspace' in INDEX_HTML

    focus_start = INDEX_HTML.index("function showAuthPanel")
    focus_end = INDEX_HTML.index("function setView", focus_start)
    focus_block = INDEX_HTML[focus_start:focus_end]
    assert 'const wasHidden = panel.classList.contains("hidden")' in focus_block
    assert "panel.contains(document.activeElement)" in focus_block
    assert "show && wasHidden && !userIsEditing" in focus_block
    assert 'if (show) $("webUsernameInput").focus()' not in focus_block
    assert ".toast.show {" in INDEX_HTML
    assert "pointer-events: auto;" in INDEX_HTML
    assert "visibility: hidden;" in INDEX_HTML
    assert "visibility: visible;" in INDEX_HTML
    assert "body.login-page-scroll" in INDEX_HTML
    assert "body.login-page-scroll .app-shell" in INDEX_HTML
    assert "body.login-page-scroll .main-stage" in INDEX_HTML
    assert (
        'document.body.classList.toggle("login-page-scroll", view === "login")' in INDEX_HTML
    )

    require_start = INDEX_HTML.index("function requireAccount")
    require_end = INDEX_HTML.index("function accountQuery", require_start)
    require_block = INDEX_HTML[require_start:require_end]
    assert 'if (!state.accounts.length) return ""' in require_block
    assert require_block.index('if (!state.accounts.length) return ""') < require_block.index(
        "showGate(false)"
    )
    assert 'code === "no_account" && state.accounts.length === 0' in INDEX_HTML
    assert 'id="confirmNewPasswordInput"' in INDEX_HTML
    assert "newPassword !== confirmNewPassword" in INDEX_HTML
    assert '$("confirmNewPasswordInput").focus()' in INDEX_HTML
    assert 'editPasswordConfirm.id = "editPasswordConfirm"' in GOD_HTML
    assert '$("editPassword").value !== $("editPasswordConfirm").value' in GOD_HTML
    assert "const duration = isError ? 8000 : 3200" in INDEX_HTML
    assert "el._timer = setTimeout(hideToast, duration)" in INDEX_HTML


def test_web_auth_setup_login_and_logout(tmp_path):
    auth = WebAuth(str(tmp_path / "auth.json"))

    token = auth.setup("admin@example.com", "secret-pass")
    cookie = WebAuth.cookie_header(token)

    assert auth.configured() is True
    assert auth.is_authenticated(cookie) is True
    assert auth.current_user(cookie)["role"] == "admin"

    auth.logout(cookie)
    assert auth.is_authenticated(cookie) is False

    new_token = auth.login("admin@example.com", "secret-pass")
    assert auth.is_authenticated(WebAuth.cookie_header(new_token)) is True


def test_web_auth_rejects_wrong_password(tmp_path):
    auth = WebAuth(str(tmp_path / "auth.json"))
    auth.setup("admin@example.com", "secret-pass")

    with pytest.raises(WebError):
        auth.login("admin@example.com", "wrong-pass")


def test_web_auth_register_creates_private_member_session(tmp_path):
    auth = WebAuth(str(tmp_path / "auth.json"))
    auth.setup("admin@example.com", "secret-pass")

    token = auth.register("New.User+Line@Example.com", "new-pass", "New User")
    user = auth.current_user(WebAuth.cookie_header(token))

    assert user is not None
    assert user["username"] == "new.user+line@example.com"
    assert user["email"] == "new.user+line@example.com"
    assert user["displayName"] == "New User"
    assert user["role"] == "member"
    assert user["accountIds"] == []
    assert auth.has_permission(user, "read") is True
    assert auth.has_permission(user, "send") is True
    assert auth.has_permission(user, "manage_accounts") is True
    assert auth.has_permission(user, "manage_users") is False


def test_web_auth_registration_requires_unique_valid_email(tmp_path):
    auth = WebAuth(str(tmp_path / "auth.json"))
    auth.setup("admin@example.com", "secret-pass")
    auth.register("member@example.com", "member-pass")

    with pytest.raises(WebError) as duplicate:
        auth.register("MEMBER@example.com", "another-pass")
    assert duplicate.value.code == "email_exists"

    with pytest.raises(WebError) as invalid:
        auth.register("not-an-email", "member-pass")
    assert invalid.value.code == "email_invalid"


def test_auth_does_not_lock_out_valid_login_after_repeated_failures(tmp_path):
    auth = WebAuth(str(tmp_path / "auth.json"))
    auth.setup("owner@example.com", "secret-pass")

    for _ in range(10):
        with pytest.raises(WebError) as caught:
            auth.login("owner@example.com", "wrong-password", source="203.0.113.10")
        assert caught.value.status == HTTPStatus.UNAUTHORIZED

    token = auth.login("owner@example.com", "secret-pass", source="203.0.113.10")

    assert auth.is_authenticated(WebAuth.cookie_header(token)) is True


def test_registration_rate_limit_is_per_source(tmp_path, monkeypatch):
    auth = WebAuth(str(tmp_path / "auth.json"))
    auth.setup("owner@example.com", "secret-pass")
    monkeypatch.setattr(auth, "registration_max_attempts", 2)
    auth.register("one@example.com", "member-pass", source="203.0.113.11")
    auth.register("two@example.com", "member-pass", source="203.0.113.11")

    with pytest.raises(WebError) as caught:
        auth.register("three@example.com", "member-pass", source="203.0.113.11")
    assert caught.value.status == HTTPStatus.TOO_MANY_REQUESTS


def test_authenticated_api_rate_limit_is_scoped_by_user_and_account(
    web_state, monkeypatch
):
    clock = [100.0]
    monkeypatch.setattr(webapp_module.time, "monotonic", lambda: clock[0])
    first_user = {
        "id": "user-one", "username": "one@example.com", "accountIds": ["line-one", "line-two"]
    }
    second_user = {
        "id": "user-two", "username": "two@example.com", "staffAccountIds": ["line-one"]
    }

    for _ in range(20):
        web_state.enforce_api_rate_limit(
            first_user, "GET", "/api/messages", "line-one"
        )

    with pytest.raises(WebError) as caught:
        web_state.enforce_api_rate_limit(
            first_user, "GET", "/api/messages", "line-one"
        )

    assert caught.value.status == HTTPStatus.TOO_MANY_REQUESTS
    assert caught.value.code == "api_rate_limited"
    assert caught.value.headers == [("Retry-After", "10")]

    web_state.enforce_api_rate_limit(first_user, "GET", "/api/messages", "line-two")
    web_state.enforce_api_rate_limit(second_user, "GET", "/api/messages", "line-one")

    clock[0] += 10.1
    web_state.enforce_api_rate_limit(first_user, "GET", "/api/messages", "line-one")


@pytest.mark.parametrize(
    ("path", "limit"),
    [
        ("/api/assistant/ask", 20),
        ("/api/assistant/settings", 6),
        ("/api/assistant/knowledge/reindex", 4),
        ("/api/ai/models", 12),
    ],
)
def test_a_made_up_account_id_does_not_reset_an_ai_route_limit(web_state, monkeypatch, path, limit):
    monkeypatch.setattr(webapp_module.time, "monotonic", lambda: 100.0)
    # A staff member of one account; these routes never read accountId at all.
    staff = {"id": "staff-one", "username": "s@example.com", "staffAccountIds": ["line-one"]}
    for index in range(limit):
        web_state.enforce_api_rate_limit(staff, "POST", path, f"made-up-{index}")
    for account_id in ("made-up-again", "line-one", ""):
        with pytest.raises(WebError) as caught:
            web_state.enforce_api_rate_limit(staff, "POST", path, account_id)
        assert caught.value.code == "api_rate_limited"


def test_only_accounts_the_user_can_open_get_their_own_bucket(web_state, monkeypatch):
    monkeypatch.setattr(webapp_module.time, "monotonic", lambda: 100.0)
    owner = {"id": "owner-one", "username": "o@example.com", "accountIds": ["line-a", "line-b"]}
    for account_id in ("line-a", "line-b"):
        for _ in range(20):
            web_state.enforce_api_rate_limit(owner, "GET", "/api/messages", account_id)
    # A made-up accountId shares the bucket of requests without one.
    for index in range(20):
        web_state.enforce_api_rate_limit(owner, "GET", "/api/messages", f"other-{index}" if index % 2 else "")
    for account_id in ("line-a", "line-b", "", "other-x"):
        with pytest.raises(WebError) as caught:
            web_state.enforce_api_rate_limit(owner, "GET", "/api/messages", account_id)
        assert caught.value.code == "api_rate_limited"
    assert ("GET", "/api/messages") in webapp_module.ACCOUNT_SCOPED_RATE_LIMIT_ROUTES
    # Every account-scoped route is a limited GET route that acts on ?accountId=.
    assert webapp_module.ACCOUNT_SCOPED_RATE_LIMIT_ROUTES.issubset(
        webapp_module.AUTHENTICATED_API_RATE_LIMITS
    )
    assert all(method == "GET" for method, _path in webapp_module.ACCOUNT_SCOPED_RATE_LIMIT_ROUTES)
    assert ("GET", "/api/inbox") not in webapp_module.ACCOUNT_SCOPED_RATE_LIMIT_ROUTES


def test_ai_ask_route_limit_holds_over_http_with_made_up_account_ids(live_server, monkeypatch):
    monkeypatch.setitem(
        webapp_module.AUTHENTICATED_API_RATE_LIMITS, ("POST", "/api/assistant/ask"), (2, 60.0)
    )
    session = requests.Session()
    session.get(f"{live_server}/api/auth/status", timeout=5)  # simple admin session
    codes = [
        session.post(
            f"{live_server}/api/assistant/ask?accountId=made-up-{index}",
            json={"question": "hello"},
            timeout=5,
        ).status_code
        for index in range(3)
    ]
    # No AI is set up: the first two are refused for that (409), the third is limited.
    assert codes == [HTTPStatus.CONFLICT, HTTPStatus.CONFLICT, HTTPStatus.TOO_MANY_REQUESTS]


def test_web_auth_legacy_username_can_still_login(tmp_path):
    auth = WebAuth(str(tmp_path / "auth.json"))
    user = auth._new_user("legacyadmin", "secret-pass", "admin", [], "Legacy Admin")
    auth.data = {"users": [user], "sessions": {}}
    auth._save()

    token = auth.login("legacyadmin", "secret-pass")

    assert auth.is_authenticated(WebAuth.cookie_header(token)) is True
    assert auth.public_user(user)["email"] == ""


def test_god_login_and_user_management_guards(tmp_path):
    auth = WebAuth(str(tmp_path / "auth.json"))
    auth.setup("admin@example.com", "admin-pass")
    god = auth.ensure_god("god", "god-password")
    admin = next(user for user in auth.data["users"] if user.get("role") == "admin")

    # A workspace sign-in refuses a God account; the shared form tells the two apart.
    with pytest.raises(WebError) as regular_login:
        auth.login("god", "god-password")
    assert regular_login.value.status == HTTPStatus.UNAUTHORIZED
    assert auth.sign_in("admin@example.com", "admin-pass")[1] is False
    with pytest.raises(WebError) as wrong_password:
        auth.sign_in("god", "not-the-password")
    assert wrong_password.value.status == HTTPStatus.UNAUTHORIZED

    token, is_god = auth.sign_in("god", "god-password")
    assert is_god is True
    signed_in = auth.current_user(
        WebAuth.cookie_header(token, cookie_name=WebAuth.god_cookie_name),
        cookie_name=WebAuth.god_cookie_name,
    )
    assert signed_in is not None
    assert signed_in["role"] == "god"
    assert auth.has_permission(signed_in, "manage_users") is True
    assert auth.can_access_account(signed_in, "any-line-account") is True

    with pytest.raises(WebError) as forbidden:
        auth.update_user(god["id"], active=False, actor=admin)
    assert forbidden.value.status == HTTPStatus.FORBIDDEN

    member = auth.create_user(
        "member@example.com", "member-pass", "viewer", [], actor=signed_in
    )
    promoted = auth.update_user(member["id"], role="admin", actor=signed_in)
    assert promoted["role"] == "admin"

    with pytest.raises(WebError):
        auth.update_user(god["id"], active=False, actor=signed_in)
    with pytest.raises(WebError):
        auth.delete_user(god["id"], actor=admin)


def test_god_signs_in_on_the_main_page_and_works_only_on_the_god_page(god_live_server):
    # Signed out, the God page sends people to the main page's sign-in form, with a
    # marker that shows the form even over a workspace session.
    signed_out = requests.get(f"{god_live_server}/god", timeout=5, allow_redirects=False)
    assert signed_out.status_code == HTTPStatus.FOUND
    assert signed_out.headers["Location"] == GOD_SIGN_IN_PATH == "/?signin=god"
    assert "LinePassport God" not in signed_out.text

    # A workspace user stays in the workspace and gets no way into the God page.
    owner = requests.Session()
    workspace = owner.post(
        f"{god_live_server}/api/auth/login",
        json={"email": "owner@example.com", "password": "owner-pass"},
        timeout=5,
    )
    assert workspace.status_code == 200 and "portal" not in workspace.json()
    assert WebAuth.god_cookie_name not in owner.cookies
    assert owner.get(f"{god_live_server}/god", timeout=5, allow_redirects=False).status_code == 302
    assert owner.get(f"{god_live_server}/api/god/users", timeout=5).status_code == 401

    wrong = requests.post(
        f"{god_live_server}/api/auth/login",
        json={"username": "god", "password": "not-the-password"},
        timeout=5,
    )
    assert wrong.status_code == HTTPStatus.UNAUTHORIZED and "Set-Cookie" not in wrong.headers
    # Its own code, so the form says the password is wrong, not that a session ended.
    assert wrong.json()["code"] == "invalid_credentials"
    assert '"errors.invalid_credentials"' in INDEX_HTML
    # A blank name never matches an account whose email is empty (this God's is).
    blank = requests.post(
        f"{god_live_server}/api/auth/login",
        json={"email": "", "password": "god-password"},
        timeout=5,
    )
    assert (blank.status_code, blank.json()["code"]) == (401, "invalid_credentials")

    # The same form signs a God account in: its session lives in the God cookie only.
    god = requests.Session()
    logged_in = god.post(
        f"{god_live_server}/api/auth/login",
        json={"username": "god", "password": "god-password"},
        timeout=5,
    )
    assert logged_in.status_code == 200
    assert logged_in.json() == {"ok": True, "authenticated": True, "portal": "god"}
    assert WebAuth.god_cookie_name in god.cookies
    assert WebAuth.cookie_name not in god.cookies

    page = god.get(f"{god_live_server}/god", timeout=5, allow_redirects=False)
    assert page.status_code == 200
    assert "LinePassport God" in page.text
    assert "/api/god/users/detail" in page.text
    assert "/api/accounts/switch" not in page.text
    # The God page's own sign-in is gone; a God page opened before the update still
    # posts there and shows this answer under its old form.
    for client in (requests, god):
        old_login = client.post(
            f"{god_live_server}/api/god/login",
            json={"username": "god", "password": "god-password"},
            timeout=5,
        )
        assert (old_login.status_code, old_login.json()["code"]) == (410, "god_login_moved")
        assert "Set-Cookie" not in old_login.headers

    users = god.get(f"{god_live_server}/api/god/users", timeout=5)
    assert users.status_code == 200
    payload = users.json()
    assert [user["email"] for user in payload["users"]] == ["owner@example.com"]
    assert "god" not in payload["roles"]

    main_api = god.get(f"{god_live_server}/api/accounts", timeout=5)
    assert main_api.status_code == HTTPStatus.UNAUTHORIZED

    # The role checks, not just a missing cookie, keep the two apart: each session is
    # refused under the other cookie's name.
    as_god = {WebAuth.god_cookie_name: owner.cookies[WebAuth.cookie_name]}
    crossed_page = requests.get(
        f"{god_live_server}/god", cookies=as_god, timeout=5, allow_redirects=False
    )
    assert crossed_page.status_code == HTTPStatus.FOUND
    crossed_api = requests.get(f"{god_live_server}/api/god/users", cookies=as_god, timeout=5)
    assert (crossed_api.status_code, crossed_api.json()["code"]) == (403, "god_auth_required")
    as_workspace = {WebAuth.cookie_name: god.cookies[WebAuth.god_cookie_name]}
    crossed_main = requests.get(
        f"{god_live_server}/api/accounts", cookies=as_workspace, timeout=5
    )
    assert (crossed_main.status_code, crossed_main.json()["code"]) == (403, "god_portal_only")

    # A God signing in on a browser that holds a workspace session keeps both, as with
    # the old God form: the workspace stays signed in next to the God page.
    both = requests.Session()
    both.post(
        f"{god_live_server}/api/auth/login",
        json={"email": "owner@example.com", "password": "owner-pass"},
        timeout=5,
    ).raise_for_status()
    both.post(
        f"{god_live_server}/api/auth/login",
        json={"username": "god", "password": "god-password"},
        timeout=5,
    ).raise_for_status()
    assert {WebAuth.cookie_name, WebAuth.god_cookie_name} <= set(both.cookies.keys())
    assert both.get(f"{god_live_server}/api/auth/status", timeout=5).json()["authenticated"]
    assert both.get(f"{god_live_server}/api/god/users", timeout=5).status_code == 200


def test_god_changes_its_own_password_on_the_god_page(god_live_server):
    god = requests.Session()
    god.post(
        f"{god_live_server}/api/auth/login",
        json={"username": "god", "password": "god-password"},
        timeout=5,
    ).raise_for_status()
    first_session = god.cookies[WebAuth.god_cookie_name]
    url = f"{god_live_server}/api/god/change-password"

    # Nobody else can use it: signed out, or signed in to the workspace.
    # (No body: the gate answers before reading one, and an unread body can reset
    # the connection on Windows.)
    assert requests.post(url, timeout=5).status_code == HTTPStatus.UNAUTHORIZED
    owner = requests.Session()
    owner.post(
        f"{god_live_server}/api/auth/login",
        json={"email": "owner@example.com", "password": "owner-pass"},
        timeout=5,
    ).raise_for_status()
    assert owner.post(url, timeout=5).status_code == HTTPStatus.UNAUTHORIZED

    def change(current, new, confirm):
        return god.post(
            url,
            json={"currentPassword": current, "newPassword": new, "confirmPassword": confirm},
            timeout=5,
        )

    wrong = change("not-the-password", "new-god-password", "new-god-password")
    assert (wrong.status_code, wrong.json()["code"]) == (401, "current_password_incorrect")
    assert '"errors.current_password_incorrect"' in INDEX_HTML
    mismatch = change("god-password", "new-god-password", "another-password")
    assert (mismatch.status_code, mismatch.json()["code"]) == (400, "password_mismatch")
    short = change("god-password", "short", "short")
    assert (short.status_code, short.json()["code"]) == (400, "password_too_short")
    # None of those signed the God out or changed anything.
    assert god.get(f"{god_live_server}/api/god/users", timeout=5).status_code == 200

    changed = change("god-password", "new-god-password", "new-god-password")
    assert changed.status_code == 200
    # This browser stays signed in on a new session; the old one no longer works.
    assert god.cookies[WebAuth.god_cookie_name] != first_session
    assert WebAuth.cookie_name not in god.cookies
    assert god.get(f"{god_live_server}/api/god/users", timeout=5).status_code == 200
    stale = requests.get(
        f"{god_live_server}/api/god/users",
        cookies={WebAuth.god_cookie_name: first_session},
        timeout=5,
    )
    assert stale.status_code == HTTPStatus.UNAUTHORIZED

    def sign_in(password):
        return requests.post(
            f"{god_live_server}/api/auth/login",
            json={"username": "god", "password": password},
            timeout=5,
        )

    assert sign_in("god-password").status_code == HTTPStatus.UNAUTHORIZED
    assert sign_in("new-god-password").json()["portal"] == "god"


def test_god_portal_html_has_only_user_management_actions():
    # No sign-in form of its own: the main page signs God accounts in.
    assert 'id="loginView"' not in GOD_HTML and "godUsername" not in GOD_HTML
    assert '"/api/god/login"' not in GOD_HTML
    # Signed out it goes to the marked sign-in form; Log out goes to the plain main page.
    assert f'window.location.replace("{GOD_SIGN_IN_PATH}");' in GOD_HTML
    assert 'window.location.replace("/");' in GOD_HTML
    # A failed first load that is not a sign-out keeps the God on the page.
    assert "if (error && error.status === 401) return;" in GOD_HTML
    # The main page honours the marker even over a workspace session, once.
    assert 'new URLSearchParams(window.location.search).get("signin") === "god"' in INDEX_HTML
    assert "(data.authenticated && !state.godSignIn)" in INDEX_HTML
    assert 'id="authGodBack"' in INDEX_HTML and '"auth.hint_god"' in INDEX_HTML
    assert 'if (data.portal === "god") {' in INDEX_HTML
    assert 'window.location.assign("/god");' in INDEX_HTML
    # The God account changes its own password here; nothing cuts a long one short.
    assert 'id="changePasswordButton"' in GOD_HTML and 'id="passwordDialog"' in GOD_HTML
    assert '"/api/god/change-password"' in GOD_HTML
    dialog = GOD_HTML.split('<dialog id="passwordDialog">', 1)[1].split("</dialog>", 1)[0]
    assert "maxlength" not in dialog
    assert 'id="userTable"' in GOD_HTML
    assert 'class="brand-mark"' not in GOD_HTML
    assert "สถานะและการจัดการ" in GOD_HTML
    assert 'control.className = "user-control"' in GOD_HTML
    assert '"action-button details-action"' in GOD_HTML
    assert '"action-button delete-action"' in GOD_HTML
    assert 'id="editDialog"' in GOD_HTML
    assert 'id="deleteDialog"' in GOD_HTML
    assert '"/api/god/users/update"' in GOD_HTML
    assert '"/api/god/users/delete"' in GOD_HTML
    assert "/api/login/start" not in GOD_HTML
    assert "/api/send" not in GOD_HTML


def test_web_auth_roles_and_account_scope(tmp_path):
    auth = WebAuth(str(tmp_path / "auth.json"))
    auth.setup("admin@example.com", "secret-pass")
    created = auth.create_user("viewer@example.com", "viewer-pass", "viewer", [])
    auth.grant_account_access(created["id"], "acct-1")
    user = auth._find_user(created["id"])

    assert auth.has_permission(user, "read") is True
    assert auth.has_permission(user, "send") is False
    assert auth.has_permission(user, "manage_api") is False
    assert auth.can_access_account(user, "acct-1") is True
    assert auth.can_access_account(user, "acct-2") is False


class _IncomingApiFake:
    contact_mid = "U-incoming-contact"
    group_mid = "C-incoming-group"

    def __init__(self):
        self.calls = []

    def get_all_contact_ids(self):
        return [self.contact_mid]

    def get_contacts(self, mids):
        return {
            "contacts": {
                mid: {
                    "contact": {
                        "mid": mid,
                        "displayName": "API Contact",
                        "statusMessage": "",
                    }
                }
                for mid in mids
            }
        }

    def get_all_chat_mids(self):
        return {"memberChatMids": [self.group_mid], "invitedChatMids": []}

    def get_chats(self, mids):
        return {
            "chats": [
                {
                    "chatMid": mid,
                    "chatName": "API Group",
                    "extra": {"groupExtra": {"memberMids": ["U-one", "U-two"]}},
                }
                for mid in mids
            ]
        }

    def send_text(self, mid, text):
        self.calls.append(("plain", mid, text))
        return {"id": f"message-{len(self.calls)}"}

    def send_encrypted_text(self, mid, text):
        self.calls.append(("encrypted", mid, text))
        return {"id": f"message-{len(self.calls)}"}

    def send_image(self, mid, data, name=None):
        self.calls.append(("image", mid, data, name))
        return {"id": f"message-{len(self.calls)}"}


def _incoming_api_owner(state):
    token = state.web_auth.setup("owner@example.com", "owner-pass")
    owner = state.web_auth.current_user(WebAuth.cookie_header(token))
    assert owner
    _seed_account(state, "incoming-account")
    state.web_auth.grant_account_access(owner["id"], "incoming-account")
    owner = state.web_auth._find_user(owner["id"])
    assert owner
    return owner


def test_incoming_api_link_is_scoped_encrypted_and_sends_to_selected_targets(web_state):
    owner = _incoming_api_owner(web_state)
    fake = _IncomingApiFake()
    created = web_state.create_incoming_api_link(
        {
            "accountId": "incoming-account",
            "name": "Website notifications",
            "targets": [
                {"type": "contact", "mid": fake.contact_mid},
                {"type": "group", "mid": fake.group_mid},
            ],
            "encrypt": True,
        },
        owner,
        fake,
    )["link"]

    link_id, token = created["endpointPath"].rsplit("/", 2)[-2:]
    stored = json.dumps(web_state.store.get("incoming_api", {"links": []}), ensure_ascii=False)
    assert token not in stored
    assert "tokenSecret" in stored
    assert web_state.list_incoming_api_links("incoming-account", owner)["count"] == 1

    web_state.get_api = lambda account_id: fake
    result = web_state.execute_incoming_api(link_id, token, {"text": "hello API"})

    assert result["ok"] is True
    assert result["sent"] == 2
    assert result["failed"] == 0
    assert fake.calls == [
        ("encrypted", fake.contact_mid, "hello API"),
        ("encrypted", fake.group_mid, "hello API"),
    ]
    actions = [
        entry["action"] for entry in web_state.list_bot_logs("incoming-account", owner)["logs"]
    ]
    assert "incoming_api.create" in actions
    assert "incoming_api.request" in actions
    assert actions.count("incoming_api.send.success") == 2

    with pytest.raises(WebError) as invalid:
        web_state.execute_incoming_api(link_id, "x" * len(token), {"text": "denied"})
    assert invalid.value.status == HTTPStatus.NOT_FOUND


def test_incoming_api_post_sends_image_and_optional_text(web_state):
    owner = _incoming_api_owner(web_state)
    fake = _IncomingApiFake()
    created = web_state.create_incoming_api_link(
        {
            "accountId": "incoming-account",
            "name": "Image notifications",
            "targets": [{"type": "contact", "mid": fake.contact_mid}],
            "encrypt": True,
        },
        owner,
        fake,
    )["link"]
    link_id, token = created["endpointPath"].rsplit("/", 2)[-2:]
    web_state.get_api = lambda account_id: fake
    image = b"fake-png-content"

    result = web_state.execute_incoming_api(
        link_id,
        token,
        {
            "text": "caption",
            "imageBase64": base64.b64encode(image).decode("ascii"),
            "filename": "notice.png",
        },
        method="POST",
    )

    assert result["ok"] is True
    assert result["method"] == "POST"
    assert result["contents"] == ["text", "image"]
    assert result["results"][0]["messageIds"] == ["message-1", "message-2"]
    assert fake.calls == [
        ("encrypted", fake.contact_mid, "caption"),
        ("image", fake.contact_mid, image, "notice.png"),
    ]


def test_incoming_api_get_rejects_image_payload(web_state):
    owner = _incoming_api_owner(web_state)
    fake = _IncomingApiFake()
    created = web_state.create_incoming_api_link(
        {
            "accountId": "incoming-account",
            "name": "GET notifications",
            "targets": [{"type": "contact", "mid": fake.contact_mid}],
        },
        owner,
        fake,
    )["link"]
    link_id, token = created["endpointPath"].rsplit("/", 2)[-2:]
    web_state.get_api = lambda account_id: fake

    with pytest.raises(WebError) as invalid:
        web_state.execute_incoming_api(
            link_id,
            token,
            {"imageUrl": "https://example.com/photo.jpg"},
            method="GET",
        )
    assert invalid.value.status == HTTPStatus.BAD_REQUEST


def test_incoming_api_links_are_tenant_isolated(web_state):
    owner = _incoming_api_owner(web_state)
    other_token = web_state.web_auth.register("other@example.com", "other-pass", "Other")
    other = web_state.web_auth.current_user(WebAuth.cookie_header(other_token))
    assert other
    fake = _IncomingApiFake()
    created = web_state.create_incoming_api_link(
        {
            "accountId": "incoming-account",
            "name": "Private link",
            "targets": [{"type": "contact", "mid": fake.contact_mid}],
            "active": False,
        },
        owner,
        fake,
    )["link"]

    assert created["active"] is False
    assert web_state.list_incoming_api_links("incoming-account", owner)["count"] == 1
    with pytest.raises(WebError) as forbidden:
        web_state.list_incoming_api_links("incoming-account", other)
    assert forbidden.value.status == HTTPStatus.FORBIDDEN
    with pytest.raises(WebError) as hidden:
        web_state.update_incoming_api_link({"id": created["id"], "active": False}, other)
    assert hidden.value.status == HTTPStatus.NOT_FOUND


def test_external_incoming_api_route_needs_only_the_generated_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    server = OkLineWebServer(("127.0.0.1", 0), OkLineWebHandler)
    server.state = WebState(_make_config(tmp_path))
    owner = _incoming_api_owner(server.state)
    fake = _IncomingApiFake()
    created = server.state.create_incoming_api_link(
        {
            "accountId": "incoming-account",
            "name": "External system",
            "targets": [{"type": "contact", "mid": fake.contact_mid}],
        },
        owner,
        fake,
    )["link"]
    server.state.get_api = lambda account_id: fake
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        response = requests.get(
            f"{base}{created['endpointPath']}",
            params={"text": "sent with GET"},
            headers={"Origin": "https://external.example"},
            timeout=5,
        )
        assert response.status_code == HTTPStatus.OK
        assert response.json()["sent"] == 1
        assert response.json()["method"] == "GET"

        image = b"external-image"
        response = requests.post(
            f"{base}{created['endpointPath']}",
            json={
                "imageBase64": base64.b64encode(image).decode("ascii"),
                "filename": "external.png",
            },
            headers={"Origin": "https://external.example"},
            timeout=5,
        )
        assert response.status_code == HTTPStatus.OK
        assert response.json()["contents"] == ["image"]
        assert fake.calls == [
            ("plain", fake.contact_mid, "sent with GET"),
            ("image", fake.contact_mid, image, "external.png"),
        ]

        protected = requests.get(
            f"{base}/api/incoming-links?accountId=incoming-account", timeout=5
        )
        assert protected.status_code == HTTPStatus.UNAUTHORIZED
    finally:
        server.shutdown()
        server.state.close()
        server.server_close()


def test_member_tenant_data_is_isolated_and_god_can_inspect(web_state):
    auth = web_state.web_auth
    auth.setup("owner@example.com", "owner-pass")
    god = auth.ensure_god("god", "god-password")
    token_a = auth.register("a@example.com", "member-pass", "Member A")
    token_b = auth.register("b@example.com", "member-pass", "Member B")
    member_a = auth.current_user(WebAuth.cookie_header(token_a))
    member_b = auth.current_user(WebAuth.cookie_header(token_b))
    assert member_a and member_b

    web_state.create_pattern({"name": "A only", "text": "alpha"}, member_a)
    web_state.create_pattern({"name": "B only", "text": "beta"}, member_b)
    assert [p["name"] for p in web_state.list_patterns(member_a)["patterns"]] == ["A only"]
    assert [p["name"] for p in web_state.list_patterns(member_b)["patterns"]] == ["B only"]

    web_state.save_ai_settings(
        {"provider": "fal", "apiKey": "tenant-a-key", "model": "fal-ai/flux/dev"},
        member_a,
    )
    assert web_state.ai_settings(member_a)["configured"] is True
    assert web_state.ai_settings(member_b)["configured"] is False

    account_id = "member-a-line"
    web_state.account_store.data["accounts"].append(
        {
            "id": account_id,
            "label": "Member A LINE",
            "ownerId": member_a["id"],
            "createdAt": "2026-07-12T00:00:00",
            "updatedAt": "2026-07-12T00:00:00",
        }
    )
    web_state.account_store.save()
    auth.grant_account_access(member_a["id"], account_id)
    member_a = auth._find_user(member_a["id"])
    assert member_a
    assert auth.can_access_account(member_a, account_id) is True
    assert auth.can_access_account(member_b, account_id) is False

    fake = _IncomingApiFake()
    web_state.create_incoming_api_link(
        {
            "accountId": account_id,
            "name": "Member A API",
            "targets": [{"type": "contact", "mid": fake.contact_mid}],
        },
        member_a,
        fake,
    )

    detail = web_state.user_detail(member_a["id"], god)
    assert detail["accounts"][0]["label"] == "Member A LINE"
    assert detail["patterns"][0]["name"] == "A only"
    assert detail["ai"]["provider"] == "fal"
    assert detail["ai"]["model"] == "fal-ai/flux/dev"
    assert "apiKey" not in detail["ai"]

    web_state.delete_user({"id": member_a["id"]}, god)
    assert web_state.account_store.get(account_id) is None
    assert web_state.list_patterns(member_a)["patterns"] == []
    assert member_a["id"] not in web_state.store.get("ai_settings", {})["tenants"]
    assert web_state.store.get("incoming_api", {"links": []})["links"] == []


def test_delete_user_keeps_all_state_when_transaction_fails(web_state, monkeypatch):
    auth = web_state.web_auth
    admin_token = auth.setup("owner@example.com", "owner-pass")
    admin = auth.current_user(WebAuth.cookie_header(admin_token))
    member_token = auth.register("member@example.com", "member-pass", "Member")
    member = auth.current_user(WebAuth.cookie_header(member_token))
    assert admin and member
    account_id = "member-line"
    web_state.account_store.data["accounts"].append(
        {
            "id": account_id,
            "label": "Member LINE",
            "ownerId": member["id"],
            "tokenFile": "",
        }
    )
    web_state.account_store.save()
    auth.grant_account_access(member["id"], account_id)

    def fail_transaction(values):
        raise OSError("database unavailable")

    monkeypatch.setattr(web_state.store, "set_many", fail_transaction)
    with pytest.raises(OSError, match="database unavailable"):
        web_state.delete_user({"id": member["id"]}, admin)

    assert auth._find_user(member["id"]) is not None
    assert web_state.account_store.get(account_id) is not None


def test_delete_account_keeps_access_and_account_when_transaction_fails(
    web_state, monkeypatch
):
    auth = web_state.web_auth
    token = auth.setup("owner@example.com", "owner-pass")
    owner = auth.current_user(WebAuth.cookie_header(token))
    assert owner
    _seed_account(web_state, "acct-rollback")
    auth.grant_account_access(owner["id"], "acct-rollback")

    def fail_transaction(values):
        raise OSError("database unavailable")

    monkeypatch.setattr(web_state.store, "set_many", fail_transaction)
    with pytest.raises(OSError, match="database unavailable"):
        web_state.delete_account("acct-rollback", owner)

    assert web_state.account_store.get("acct-rollback") is not None
    assert auth.can_access_account(auth._find_user(owner["id"]), "acct-rollback") is True


def test_web_managed_line_session_is_encrypted_and_loadable(web_state, tmp_path):
    api = OkLine(
        access_token="secret-access-token",
        refresh_token="secret-refresh-token",
        certificate="secret-certificate",
        record=False,
    )
    loaded = None
    token_file = tmp_path / "account.tokens"
    try:
        web_state._save_account_tokens(api, str(token_file))
        raw = token_file.read_bytes()
        assert raw.startswith(b"fernet:")
        assert b"secret-access-token" not in raw

        loaded = web_state._api_from_account_tokens(str(token_file), redact=True)
        assert loaded.tokens.access_token == "secret-access-token"
        assert loaded.tokens.refresh_token == "secret-refresh-token"
        assert loaded.tokens.certificate == "secret-certificate"
    finally:
        if loaded is not None:
            loaded.close()
        api.close()


def test_reconnect_replaces_session_without_changing_account_references(
    web_state, tmp_path
):
    account_id = "stable-account"
    token_file = tmp_path / "stable-account.tokens"
    old_api = OkLine(
        access_token="old-access",
        refresh_token="old-refresh",
        mid="U-same",
        record=False,
    )
    new_api = OkLine(
        access_token="new-access",
        refresh_token="new-refresh",
        mid="U-same",
        record=False,
    )
    loaded = None
    try:
        web_state._save_account_tokens(old_api, str(token_file))
        web_state.account_store.data["accounts"] = [
            {
                "id": account_id,
                "label": "Custom label",
                "tokenFile": str(token_file),
                "mid": "U-same",
                "userid": "old-user-id",
                "sessionState": "reauth_required",
                "sessionError": "expired",
            }
        ]
        web_state.account_store.data["activeAccountId"] = account_id
        web_state.account_store.save()
        web_state.schedules = [{"id": "job-1", "accountId": account_id}]

        account = web_state._commit_reconnected_account(
            new_api,
            {
                "mid": "U-same",
                "userid": "new-user-id",
                "displayName": "LINE display name",
            },
            account_id,
            None,
        )
        web_state._bind_account_session(new_api, account_id, str(token_file))
        new_api.tokens.access_token = "refreshed-access"
        assert new_api._session_save_hook is not None
        new_api._session_save_hook()
        loaded = web_state._api_from_account_tokens(
            str(token_file), account_id=account_id, redact=True
        )

        assert account["id"] == account_id
        assert account["label"] == "Custom label"
        assert account["userid"] == "new-user-id"
        assert account["sessionState"] == "connected"
        assert "sessionError" not in account
        assert web_state.schedules == [{"id": "job-1", "accountId": account_id}]
        assert loaded.tokens.access_token == "refreshed-access"
        assert loaded.tokens.refresh_token == "new-refresh"
    finally:
        if loaded is not None:
            loaded.close()
        new_api.close()
        old_api.close()


def test_reconnect_rejects_a_different_line_account_before_overwriting_session(
    web_state, tmp_path
):
    token_file = tmp_path / "protected.tokens"
    old_api = OkLine(access_token="old-access", mid="U-original", record=False)
    wrong_api = OkLine(access_token="wrong-access", mid="U-other", record=False)
    try:
        web_state._save_account_tokens(old_api, str(token_file))
        before = token_file.read_bytes()
        web_state.account_store.data["accounts"] = [
            {
                "id": "protected-account",
                "label": "Protected",
                "tokenFile": str(token_file),
                "mid": "U-original",
                "sessionState": "reauth_required",
            }
        ]
        web_state.account_store.save()

        with pytest.raises(WebError) as exc:
            web_state._commit_reconnected_account(
                wrong_api,
                {"mid": "U-other", "displayName": "Wrong account"},
                "protected-account",
                None,
            )

        assert exc.value.status == HTTPStatus.CONFLICT
        assert exc.value.code == "line_account_mismatch"
        assert token_file.read_bytes() == before
        assert web_state.account_store.requires_reauth("protected-account") is True
    finally:
        wrong_api.close()
        old_api.close()


def test_web_session_keeps_stored_e2ee_when_restored_key_cannot_export(web_state, tmp_path):
    class RestoredE2EE:
        @staticmethod
        def is_ready():
            return True

        @staticmethod
        def export_keys():
            return {"mid": "u123", "latestKeyId": 7, "keys": {}}

    api = OkLine(access_token="updated-token", record=False)
    token_file = tmp_path / "account.tokens"
    original_e2ee = {
        "mid": "u123",
        "latestKeyId": 7,
        "keys": {"7": "private-key-blob"},
    }
    try:
        web_state._save_account_tokens(
            api,
            str(token_file),
            fallback_e2ee=original_e2ee,
        )
        api.e2ee = RestoredE2EE()

        web_state._save_account_tokens(api, str(token_file))

        raw = token_file.read_bytes()
        payload = web_state.secret_cipher.decrypt(raw[len(b"fernet:") :])
        stored = json.loads(payload.decode("utf-8"))
        assert stored["accessToken"] == "updated-token"
        assert stored["e2ee"] == original_e2ee
    finally:
        api.close()


def test_legacy_web_session_migration_preserves_e2ee(web_state, tmp_path, monkeypatch):
    from okline.session import Session

    token_file = tmp_path / "legacy-session.json"
    original_e2ee = {
        "mid": "u123",
        "latestKeyId": 7,
        "keys": {"7": "private-key-blob"},
    }
    Session(access_token="legacy-token", e2ee=original_e2ee).save(str(token_file))
    api = OkLine(access_token="legacy-token", record=False)
    monkeypatch.setattr(
        OkLine,
        "from_tokens_file",
        classmethod(lambda cls, path, **kwargs: api),
    )

    loaded = web_state._api_from_account_tokens(str(token_file), redact=True)
    try:
        raw = token_file.read_bytes()
        assert raw.startswith(b"fernet:")
        payload = web_state.secret_cipher.decrypt(raw[len(b"fernet:") :])
        stored = json.loads(payload.decode("utf-8"))
        assert stored["accessToken"] == "legacy-token"
        assert stored["e2ee"] == original_e2ee
    finally:
        loaded.close()


class _RecentChatFakeApi:
    def __init__(self):
        self.checked = None
        self.message_box_calls = 0

    def get_message_boxes(self, *, limit=100, last_messages_per_box=5):
        self.message_box_calls += 1
        return {
            "messageBoxes": [
                {"id": "u_old", "unreadCount": 0, "lastMessages": [{"createdTime": "1000"}]},
                {"id": "c_old", "unreadCount": 0, "lastMessages": [{"createdTime": "500"}]},
                {
                    "id": "c_new",
                    "unreadCount": 2,
                    "lastMessages": [
                        {
                            "id": "gm-1",
                            "from": "u1",
                            "text": "group update",
                            "contentType": 0,
                            "createdTime": "3000",
                        }
                    ],
                },
                {
                    "id": "u_new",
                    "unreadCount": 3,
                    "lastMessages": [
                        {
                            "id": "dm-1",
                            "from": "u_new",
                            "text": "hello unread",
                            "contentType": 0,
                            "createdTime": "2500",
                        }
                    ],
                },
            ][:limit]
        }

    def get_all_contact_ids(self):
        return ["u_old", "u_never", "u_new"]

    def get_contacts(self, ids):
        names = {
            "u_old": "Beta Old",
            "u_never": "Alpha Never",
            "u_new": "Zeta New",
        }
        return {
            "contacts": {
                mid: {
                    "contact": {
                        "mid": mid,
                        "displayName": names[mid],
                        "statusMessage": "",
                    }
                }
                for mid in ids
            }
        }

    def get_all_chat_mids(self):
        return {"memberChatMids": ["c_old", "c_never", "c_new"], "invitedChatMids": []}

    def get_chats(self, mids):
        names = {
            "c_old": "Beta Old Group",
            "c_never": "Alpha Never Group",
            "c_new": "Zeta New Group",
        }
        return {
            "chats": [
                {
                    "chatMid": mid,
                    "chatName": names[mid],
                    "extra": {"groupExtra": {"memberMids": ["u1", "u2"]}},
                }
                for mid in mids
            ]
        }

    def send_chat_checked(self, chat_mid, last_message_id):
        self.checked = (chat_mid, last_message_id)


def test_contacts_and_groups_sort_by_recent_chat():
    handler = OkLineWebHandler.__new__(OkLineWebHandler)
    api = _RecentChatFakeApi()

    contacts = handler._contacts(api, {"limit": ["10"]})["contacts"]
    groups = handler._groups(api, {"limit": ["10"]})["groups"]
    conversations = handler._conversations(api, {"limit": ["10"]})["conversations"]

    assert [contact["mid"] for contact in contacts] == ["u_new", "u_old", "u_never"]
    assert [group["mid"] for group in groups] == ["c_new", "c_old", "c_never"]
    assert contacts[0]["unreadCount"] == 3
    assert contacts[0]["lastMessage"]["text"] == "hello unread"
    assert contacts[0]["lastMessageAt"] == 2500
    assert groups[0]["unreadCount"] == 2
    assert groups[0]["lastMessage"]["text"] == "group update"
    assert contacts[-1]["unreadCount"] == 0
    assert [item["mid"] for item in conversations] == [
        "c_new",
        "u_new",
        "u_old",
        "c_old",
    ]


def test_contacts_groups_and_notifications_share_one_inbox_snapshot(web_state):
    _seed_account(web_state, "account-1")
    handler = OkLineWebHandler.__new__(OkLineWebHandler)
    handler.server = type("Server", (), {"state": web_state})()
    api = _RecentChatFakeApi()
    query = {"accountId": ["account-1"], "limit": ["10"]}

    handler._contacts(api, query)
    handler._groups(api, query)
    handler._conversations(api, query)

    assert api.message_box_calls == 1


def test_mark_messages_read_sends_line_read_receipt():
    handler = OkLineWebHandler.__new__(OkLineWebHandler)
    api = _RecentChatFakeApi()

    result = handler._mark_messages_read(api, {"chatMid": "u_new", "lastMessageId": "dm-1"})

    assert result == {"ok": True, "chatMid": "u_new", "lastMessageId": "dm-1"}
    assert api.checked == ("u_new", "dm-1")


def test_web_ui_shows_unread_conversation_previews():
    assert 'className = "line-row" + (unreadCount ? " has-unread" : "")' in INDEX_HTML
    assert 'badge.className = "line-unread-badge";' in INDEX_HTML
    assert "options.lastMessage ? contentLabel(options.lastMessage)" in INDEX_HTML
    assert 'await post("/api/messages/read"' in INDEX_HTML
    assert "function clearUnreadForTarget(mid)" in INDEX_HTML


def test_chat_background_refresh_does_not_clear_or_jump_the_message_list():
    assert 'id="messagesShell" aria-busy="false"' in INDEX_HTML
    assert 'id="messagesLoading" role="status"' in INDEX_HTML
    assert "function setMessagesLoading(loading)" in INDEX_HTML
    assert "function messageRenderSignature(messages)" in INDEX_HTML
    assert "function renderMessageRows(messages, accountId" in INDEX_HTML
    assert "messageRenderSignatures: new Map()" in INDEX_HTML
    assert "messageRequestSeq: 0" in INDEX_HTML
    assert "state.messageLoading" in INDEX_HTML
    assert "requestSeq !== state.messageRequestSeq" in INDEX_HTML
    assert "signature !== previousSignature" in INDEX_HTML
    assert "previousBottomDistance < 80" in INDEX_HTML

    start = INDEX_HTML.index("async function loadMessages")
    end = INDEX_HTML.index("async function sendText", start)
    block = INDEX_HTML[start:end]
    assert 'listLoading("messagesList")' not in block
    assert "if (!silent)" in block
    assert "toastError(err);" in block


def test_web_ui_renders_line_media_and_structured_message_types():
    assert "const isMediaType = [1, 2, 3, 5, 14, 21].includes(ct);" in INDEX_HTML
    assert 'document.createElement("video")' in INDEX_HTML
    assert 'document.createElement("audio")' in INDEX_HTML
    assert "function locationBody(m)" in INDEX_HTML
    assert "function contactBody(m)" in INDEX_HTML
    assert "https://www.google.com/maps/search/?api=1&query=" in INDEX_HTML
    assert "if (ct === 15 && m.location) return locationBody(m);" in INDEX_HTML
    assert "if (ct === 13) return contactBody(m);" in INDEX_HTML
    assert 'm.contentType === "18") return chatEventLabel(m);' in INDEX_HTML
    assert '13: "content.contact"' in INDEX_HTML
    assert '22: "content.flex"' in INDEX_HTML


def test_web_ui_combines_contacts_and_groups_in_leftmost_all_tab():
    all_tab = INDEX_HTML.index('id="loadAllButton"')
    contacts_tab = INDEX_HTML.index('id="loadContactsButton"')
    groups_tab = INDEX_HTML.index('id="loadGroupsButton"')

    assert all_tab < contacts_tab < groups_tab
    assert 'id="allList"' in INDEX_HTML
    assert 'data-i18n="contacts.tab_all"' in INDEX_HTML
    assert 'contactsSubtab: "all"' in INDEX_HTML
    assert "function renderAllList()" in INDEX_HTML
    assert 'setContactsSubtab("all");' in INDEX_HTML
    assert ".sort((a, b) => compareConversationItems(a.item, b.item))" in INDEX_HTML


def test_web_ui_notifies_only_after_conversation_baseline_is_ready():
    assert 'id="notificationButton"' in INDEX_HTML
    assert INDEX_HTML.index('id="notificationButton"') < INDEX_HTML.index(
        'id="refreshAllButton"'
    )
    assert 'data-sound-status="idle"' in INDEX_HTML
    assert "Notification.requestPermission()" in INDEX_HTML
    assert 'navigator.serviceWorker.register("/service-worker.js")' in INDEX_HTML
    assert "state.notificationWorker.showNotification(title, options)" in INDEX_HTML
    assert "new Notification(" in INDEX_HTML
    assert "function unlockNotificationSound()" in INDEX_HTML
    assert "async function playNotificationSound({urgent = false, preview = false} = {})" in INDEX_HTML
    assert 'button.dataset.soundStatus = "played";' in INDEX_HTML
    assert 'button.dataset.soundStatus = "blocked";' in INDEX_HTML
    assert "function playNotificationSoundForItems(accountId, items)" in INDEX_HTML
    assert 'const permissionPromise = permission === "default"' in INDEX_HTML
    assert "? Notification.requestPermission()" in INDEX_HTML
    assert 'if (permission !== "granted")' in INDEX_HTML
    assert "state.notificationsEnabled = true;" in INDEX_HTML
    assert "const soundReady = soundUnlocked && await playNotificationSound();" in INDEX_HTML
    # Granted browser permission turns notifications on without the bell opt-in.
    assert 'state.notificationsEnabled = saved !== "off-by-user"' in INDEX_HTML
    assert '&& Notification.permission === "granted";' in INDEX_HTML
    assert (
        "silent && previousMessageKey && lastMessageKey !== previousMessageKey" in INDEX_HTML
    )
    assert "silent: false" in INDEX_HTML
    assert "vibrate: [100, 60, 100]" in INDEX_HTML
    assert "function processNewMessageNotifications(accountId, conversations)" in INDEX_HTML
    assert "state.notificationAccountId !== accountId" in INDEX_HTML
    assert "!state.conversationBaselineReady" in INDEX_HTML
    assert "processNewMessageNotifications(accountId, conversations);" in INDEX_HTML
    # Hidden tabs keep refreshing (UX plan B1).
    assert "lineViewIsVisible" not in INDEX_HTML
    assert (
        'document.title = unread ? `(${count}) LinePassport` : "LinePassport";' in INDEX_HTML
    )


def test_chat_images_open_in_accessible_modal_viewer():
    assert 'id="imageViewer" role="dialog" aria-modal="true"' in INDEX_HTML
    assert 'id="imageViewerImage"' in INDEX_HTML
    assert 'id="imageViewerOriginal"' in INDEX_HTML
    assert 'button.className = "msg-media-button";' in INDEX_HTML
    assert (
        'button.addEventListener("click", () => openImageViewer(mediaSrc, img.alt));'
        in INDEX_HTML
    )
    assert "function openImageViewer(src, alt)" in INDEX_HTML
    assert "function closeImageViewer()" in INDEX_HTML
    assert 'event.key === "Escape"' in INDEX_HTML
    assert 'event.target === $("imageViewerStage")' in INDEX_HTML


def test_notification_service_worker_is_public(live_server):
    response = requests.get(f"{live_server}/service-worker.js", timeout=2)

    assert response.status_code == HTTPStatus.OK
    assert response.headers["Content-Type"].startswith("application/javascript")
    assert 'self.addEventListener("notificationclick"' in response.text


def test_contacts_background_refresh_uses_lightweight_conversation_endpoint():
    # Live updates come from /api/events; polling is only the 30 s fallback.
    assert "const CONVERSATION_BACKGROUND_REFRESH_MS = 30000;" in INDEX_HTML
    assert 'new EventSource(`/api/events?${accountQuery(accountId)}`)' in INDEX_HTML
    assert "function refreshConversationsInBackground()" in INDEX_HTML
    assert "api(`/api/conversations?${accountQuery(accountId)}&limit=1000`)" in INDEX_HTML
    assert "if (signature === state.conversationSignature) return;" in INDEX_HTML
    assert "if (!state.sseConnected) refreshConversationsInBackground();" in INDEX_HTML
    start = INDEX_HTML.index("async function refreshConversationsInBackground")
    end = INDEX_HTML.index("function listLoading", start)
    block = INDEX_HTML[start:end]
    assert 'state.tab !== "line"' not in block
    assert 'document.visibilityState !== "visible"' not in block
    assert "loadContacts()" not in block
    assert "loadGroups()" not in block


def test_schedule_once_requires_and_sets_run_at():
    job = _schedule_from_body(
        {
            "name": "once",
            "to": "u" + "1" * 32,
            "text": "hello",
            "mode": "once",
            "runAt": "2026-07-07T12:30",
        },
        account_id="acct",
    )

    assert job["mode"] == "once"
    assert job["maxRuns"] == 1
    assert job["enabled"] is True
    assert job["nextRunAt"] is not None


def test_schedule_image_source_does_not_require_text():
    job = _schedule_from_body(
        {
            "name": "image",
            "to": "u" + "6" * 32,
            "contentSource": "image",
            "imageSource": "https://example.test/photo.jpg",
            "mode": "once",
            "runAt": "2026-07-07T12:30",
        },
        account_id="acct",
    )

    assert job["contentSource"] == "image"
    assert job["imageSource"] == "https://example.test/photo.jpg"
    assert job["text"] == ""


def test_schedule_api_source_does_not_require_text():
    job = _schedule_from_body(
        {
            "name": "api",
            "to": "u" + "7" * 32,
            "contentSource": "api",
            "apiUrl": "https://example.test/message",
            "apiMethod": "POST",
            "apiBody": '{"kind":"daily"}',
            "mode": "once",
            "runAt": "2026-07-07T12:30",
        },
        account_id="acct",
    )

    assert job["contentSource"] == "api"
    assert job["apiMethod"] == "POST"
    assert job["apiBody"] == '{"kind":"daily"}'


def test_api_json_text_payload():
    items = _contents_from_api_json({"message": "hello from api"})

    assert items == [{"kind": "text", "text": "hello from api"}]


def test_api_json_image_base64_payload():
    encoded = base64.b64encode(b"fake-image").decode("ascii")

    items = _contents_from_api_json({"image_base64": encoded})

    assert items == [{"kind": "image", "data": b"fake-image", "name": "api-image.jpg"}]


def test_api_json_relative_image_payload_uses_api_url(monkeypatch):
    urls = []

    def fake_download(url: str):
        urls.append(url)
        return b"fake-image", "photo.jpg"

    monkeypatch.setattr(webapp_module, "_download_image", fake_download)

    items = _contents_from_api_json(
        {"image_url": "images/photo"},
        base_url="https://example.test/api/message",
    )

    assert urls == ["https://example.test/api/images/photo"]
    assert items == [{"kind": "image", "data": b"fake-image", "name": "photo.jpg"}]


def test_repeat_next_run_inside_daily_window():
    job = _schedule_from_body(
        {
            "to": "u" + "2" * 32,
            "text": "ping",
            "mode": "repeat",
            "windowStart": "09:00",
            "windowEnd": "18:00",
            "intervalMinutes": 15,
        },
        account_id="acct",
    )

    next_epoch = _next_repeat_epoch(job, dt.datetime(2026, 7, 7, 10, 5))

    assert dt.datetime.fromtimestamp(next_epoch) == dt.datetime(2026, 7, 7, 10, 5)


def test_repeat_after_window_moves_to_next_day_start():
    job = _schedule_from_body(
        {
            "to": "u" + "3" * 32,
            "text": "ping",
            "mode": "repeat",
            "windowStart": "09:00",
            "windowEnd": "18:00",
            "intervalMinutes": 15,
        },
        account_id="acct",
    )

    next_epoch = _next_repeat_epoch(job, dt.datetime(2026, 7, 7, 18, 30))

    assert dt.datetime.fromtimestamp(next_epoch) == dt.datetime(2026, 7, 8, 9, 0)


def test_repeat_cross_midnight_window_accepts_after_midnight():
    job = _schedule_from_body(
        {
            "to": "u" + "5" * 32,
            "text": "ping",
            "mode": "repeat",
            "windowStart": "22:00",
            "windowEnd": "06:00",
            "intervalMinutes": 15,
        },
        account_id="acct",
    )

    next_epoch = _next_repeat_epoch(job, dt.datetime(2026, 7, 8, 1, 30))

    assert dt.datetime.fromtimestamp(next_epoch) == dt.datetime(2026, 7, 8, 1, 30)


def test_repeat_stops_after_max_runs():
    job = _schedule_from_body(
        {
            "to": "u" + "4" * 32,
            "text": "ping",
            "mode": "repeat",
            "windowStart": "09:00",
            "windowEnd": "18:00",
            "intervalMinutes": 15,
            "maxRuns": 2,
        },
        account_id="acct",
    )
    job["sentCount"] = 2

    _advance_after_success(job)

    assert job["enabled"] is False
    assert job["status"] == "completed"
    assert job["nextRunAt"] is None


class _MediaFakeApi:
    """Records send_image / send_file calls instead of hitting the network."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def send_image(self, to, data, name=None):
        self.calls.append(("image", to, len(data), name))
        return {"id": "IMG"}

    def send_file(self, to, data, name=None):
        self.calls.append(("file", to, len(data), name))
        return {"id": "FILE"}


_MEDIA_MID = "CzJEu4tJfylYAqsWewJegDeo7iEvyDr70eGX_ZBo8DDI"


def test_send_media_routes_image_and_file():
    api = _MediaFakeApi()
    png = base64.b64encode(b"\x89PNG....").decode()
    img = OkLineWebHandler._send_media(
        None, api, {"to": _MEDIA_MID, "filename": "pic.png", "data": png, "kind": "image"}
    )
    doc = OkLineWebHandler._send_media(
        None,
        api,
        {"to": _MEDIA_MID, "filename": "doc.pdf", "data": base64.b64encode(b"%PDF").decode()},
    )
    assert img["messageId"] == "IMG"
    assert doc["messageId"] == "FILE"
    assert [c[0] for c in api.calls] == ["image", "file"]
    assert api.calls[0][3] == "pic.png"


def test_send_media_strips_data_url_and_detects_image_by_extension():
    api = _MediaFakeApi()
    png = base64.b64encode(b"\x89PNG").decode()
    res = OkLineWebHandler._send_media(
        None,
        api,
        {"to": _MEDIA_MID, "filename": "p.jpg", "data": "data:image/jpeg;base64," + png},
    )
    assert res["messageId"] == "IMG"
    assert api.calls[0][0] == "image"


def test_send_media_rejects_empty_data():
    api = _MediaFakeApi()
    with pytest.raises(WebError):
        OkLineWebHandler._send_media(
            None, api, {"to": _MEDIA_MID, "filename": "x", "data": ""}
        )
    assert api.calls == []


def test_sniff_content_type_detects_media_and_documents():
    assert webapp_module._sniff_content_type(bytes.fromhex("ffd8ffe0")) == "image/jpeg"
    assert webapp_module._sniff_content_type(b"\x89PNG\r\n\x1a\n") == "image/png"
    assert webapp_module._sniff_content_type(b"GIF89a1234") == "image/gif"
    assert webapp_module._sniff_content_type(b"RIFF\x00\x00\x00\x00WEBPxxxx") == "image/webp"
    assert webapp_module._sniff_content_type(b"%PDF-1.7") == "application/pdf"
    assert webapp_module._sniff_content_type(b"PK\x03\x04rest") == "application/zip"
    assert webapp_module._sniff_content_type(b"RIFF\x00\x00\x00\x00WAVEdata") == "audio/wav"
    assert webapp_module._sniff_content_type(b"OggSrest") == "application/ogg"
    assert webapp_module._sniff_content_type(b"fLaCrest") == "audio/flac"
    assert webapp_module._sniff_content_type(b"ID3rest") == "audio/mpeg"
    assert webapp_module._sniff_content_type(b"\xff\xf1rest") == "audio/aac"
    assert webapp_module._sniff_content_type(b"\x1aE\xdf\xa3rest") == "application/webm"
    assert webapp_module._sniff_content_type(b"\x00\x00\x00\x18ftypisom") == "application/mp4"
    assert webapp_module._sniff_content_type(b"nope") == "application/octet-stream"
    assert webapp_module._message_content_type(b"nope", "notes.txt") == "text/plain"
    assert (
        webapp_module._message_content_type(b"\x00\x00\x00\x18ftypisom", "", 2) == "video/mp4"
    )
    assert (
        webapp_module._message_content_type(b"\x00\x00\x00\x18ftypM4A ", "", 3) == "audio/mp4"
    )


@pytest.mark.parametrize(
    ("value", "size", "expected"),
    [
        ("bytes=0-99", 1000, (0, 99)),
        ("bytes=500-", 1000, (500, 999)),
        ("bytes=-100", 1000, (900, 999)),
        ("bytes=0-9999", 1000, (0, 999)),
        ("bytes=1000-", 1000, None),
        ("bytes=2-1", 1000, None),
        ("bytes=0-1,4-5", 1000, None),
        ("invalid", 1000, None),
    ],
)
def test_parse_byte_range(value, size, expected):
    assert webapp_module._parse_byte_range(value, size) == expected


def test_message_content_serves_single_range_for_media_players():
    handler = OkLineWebHandler.__new__(OkLineWebHandler)
    handler.headers = {"range": "bytes=2-5"}
    captured = {}
    handler._require_permission = lambda permission: None
    handler._with_api = lambda query, callback: (b"0123456789", "clip.mp4", 2)

    def capture(payload, content_type, status, *, headers=None):
        captured.update(
            payload=payload,
            content_type=content_type,
            status=status,
            headers=dict(headers or []),
        )

    handler._bytes = capture
    handler._message_content(
        {
            "account_id": ["account-1"],
            "message_id": ["message-1"],
            "chat_mid": ["Uchat"],
        }
    )

    assert captured == {
        "payload": b"2345",
        "content_type": "video/mp4",
        "status": HTTPStatus.PARTIAL_CONTENT,
        "headers": {
            "accept-ranges": "bytes",
            "content-range": "bytes 2-5/10",
        },
    }


def test_message_summary_extracts_sticker_id():
    msg = {
        "id": "9",
        "from": "u" + "0" * 32,
        "contentType": 7,
        "contentMetadata": {"STKID": "52002734", "STKPKGID": "11537"},
    }
    summary = webapp_module._message_summary(None, msg, {})
    assert summary["stickerId"] == "52002734"
    assert summary["contentType"] == 7
    # a plain image message carries no sticker id
    img = webapp_module._message_summary(None, {"id": "1", "contentType": 1}, {})
    assert img["stickerId"] is None


def test_message_summary_exposes_safe_structured_content_metadata():
    contact_mid = "u" + "7" * 32
    location = webapp_module._message_summary(
        None,
        {
            "id": "loc-1",
            "contentType": 15,
            "location": {
                "title": "Tokyo Tower",
                "address": "Minato, Tokyo",
                "latitude": "35.6586",
                "longitude": "139.7454",
                "phone": "03-1234-5678",
            },
        },
        {},
    )
    assert location["location"] == {
        "title": "Tokyo Tower",
        "address": "Minato, Tokyo",
        "phone": "03-1234-5678",
        "latitude": 35.6586,
        "longitude": 139.7454,
    }

    contact = webapp_module._message_summary(
        None,
        {
            "id": "contact-1",
            "contentType": 13,
            "contentMetadata": {"mid": contact_mid, "displayName": "Alice"},
        },
        {contact_mid: "Alice LINE"},
    )
    assert contact["contactMid"] == contact_mid
    assert contact["contactName"] == "Alice LINE"

    video = webapp_module._message_summary(
        None,
        {
            "id": "video-1",
            "contentType": 2,
            "contentMetadata": {
                "DURATION": "4200",
                "PREVIEW_URL": "https://manager.line-scdn.net/preview",
            },
        },
        {},
    )
    assert video["durationMs"] == 4200
    assert video["mediaReady"] is True
    assert video["hasPreview"] is True
    assert "PREVIEW_URL" not in video

    flex = webapp_module._message_summary(
        None,
        {
            "id": "flex-1",
            "contentType": 22,
            "contentMetadata": {"ALT_TEXT": "Order summary"},
        },
        {},
    )
    assert flex["altText"] == "Order summary"


def test_message_summary_rejects_unsafe_location_and_link_values():
    summary = webapp_module._message_summary(
        None,
        {
            "contentType": 12,
            "text": "javascript:alert(1)",
            "location": {"latitude": 999, "longitude": -999},
            "contentMetadata": {"URL": "https://user:pass@example.com/private"},
        },
        {},
    )
    assert summary["linkUrl"] is None
    assert summary["location"] is None


def test_message_summary_exposes_chat_event_names_instead_of_a_fake_file():
    actor = "u" + "1" * 32
    member = "u" + "2" * 32
    message = {
        "id": "event-1",
        "contentType": 18,
        "contentMetadata": {
            "LOC_KEY": "C_GI",
            "LOC_ARGS": actor + "\x1e" + member,
        },
    }

    assert webapp_module._chat_event_mids(message) == [actor, member]
    summary = webapp_module._message_summary(None, message, {actor: "Alice", member: "Bob"})

    assert summary["eventKey"] == "C_GI"
    assert summary["eventNames"] == ["Alice", "Bob"]
    assert summary["fileName"] is None


def test_message_summary_marks_decrypted_e2ee_image_ready_without_exposing_key():
    class ReadyE2EE:
        @staticmethod
        def is_ready():
            return True

    class Api:
        e2ee = ReadyE2EE()

        @staticmethod
        def decrypt_message(message):
            return {
                **message,
                "contentMetadata": {
                    **message["contentMetadata"],
                    "ENC_KM": "private-key-material",
                },
            }

    message = {
        "id": "123",
        "from": "u" + "0" * 32,
        "contentType": 1,
        "contentMetadata": {"e2eeVersion": "2", "SID": "emi"},
        "chunks": ["a", "b", "c"],
    }
    summary = webapp_module._message_summary(Api(), message, {})

    assert summary["encrypted"] is True
    assert summary["mediaReady"] is True
    assert "private-key-material" not in json.dumps(summary)


def test_encode_message_talk_meta_matches_line_binary_shape():
    message_id = "622631501804339664"
    outer = json.loads(base64.b64decode(encode_message_talk_meta(message_id)))
    payload = base64.b64decode(outer["message"])

    assert payload[:3] == b"\x0b" + struct.pack(">h", 4)
    size = struct.unpack(">I", payload[3:7])[0]
    assert payload[7 : 7 + size] == message_id.encode()
    assert payload[7 + size :] == b"\x0f" + struct.pack(">h", 27) + b"\x0c\0\0\0\0\0"


def test_download_message_content_uses_e2ee_sid_oid_and_talk_meta(monkeypatch):
    calls = {}

    class ReadyE2EE:
        @staticmethod
        def is_ready():
            return True

    class Obs:
        @staticmethod
        def download_object(service, sid, oid, *, talk_meta=None):
            calls["download"] = (service, sid, oid, talk_meta)
            return b"encrypted"

    class Api:
        e2ee = ReadyE2EE()
        obs = Obs()

        @staticmethod
        def get_recent_messages(chat_mid, count):
            calls["recent"] = (chat_mid, count)
            return [
                {
                    "id": "123",
                    "contentType": 14,
                    "chunks": ["a", "b", "c"],
                }
            ]

        @staticmethod
        def decrypt_message(message):
            return {
                **message,
                "contentMetadata": {
                    "SID": "emi",
                    "OID": "encrypted-object",
                    "ENC_KM": "media-key",
                    "FILE_NAME": "report.pdf",
                },
            }

    monkeypatch.setattr(
        webapp_module.e2ee_frame,
        "decrypt_media_blob",
        lambda data, key: b"plain" if (data, key) == (b"encrypted", "media-key") else b"",
    )

    assert webapp_module._download_message_payload(Api(), "123", "Uchat") == (
        b"plain",
        "report.pdf",
        14,
    )
    assert webapp_module._safe_message_filename("../report.pdf\r\n") == "report.pdf"
    assert calls["recent"] == ("Uchat", 200)
    assert calls["download"] == (
        "talk",
        "emi",
        "encrypted-object",
        encode_message_talk_meta("123"),
    )


def test_download_message_content_prefers_line_cdn_urls_for_plain_images(monkeypatch):
    calls = []

    class Obs:
        @staticmethod
        def download_object(*args, **kwargs):
            raise AssertionError("OBS fallback should not be used")

    class Api:
        obs = Obs()

        @staticmethod
        def get_recent_messages(chat_mid, count):
            assert (chat_mid, count) == ("Uchat", 200)
            return [
                {
                    "id": "123",
                    "contentType": 1,
                    "contentMetadata": {
                        "PREVIEW_URL": "https://manager.line-scdn.net/image/preview",
                        "DOWNLOAD_URL": "https://manager.line-scdn.net/image/full",
                    },
                }
            ]

    def fake_download(url, *, session=None):
        assert session is None
        calls.append(url)
        return b"preview" if url.endswith("preview") else b"full"

    monkeypatch.setattr(webapp_module, "_download_line_media_url", fake_download)

    assert (
        webapp_module._download_message_content(Api(), "123", "Uchat", preview=True)
        == b"preview"
    )
    assert webapp_module._download_message_content(Api(), "123", "Uchat") == b"full"
    assert calls == [
        "https://manager.line-scdn.net/image/preview",
        "https://manager.line-scdn.net/image/full",
    ]


def test_download_message_content_uses_account_session_for_plain_video(monkeypatch):
    calls = []
    session = object()

    class Transport:
        pass

    class Obs:
        @staticmethod
        def download_object(*args, **kwargs):
            raise AssertionError("OBS fallback should not be used")

    class Api:
        transport = Transport()
        transport.session = session
        obs = Obs()

        @staticmethod
        def get_recent_messages(chat_mid, count):
            assert (chat_mid, count) == ("Uchat", 200)
            return [
                {
                    "id": "video-123",
                    "contentType": 2,
                    "contentMetadata": {
                        "PREVIEW_URL": "https://manager.line-scdn.net/video/preview",
                        "DOWNLOAD_URL": "https://manager.line-scdn.net/video/full",
                    },
                }
            ]

    def fake_download(url, *, session=None):
        calls.append((url, session))
        return b"video"

    monkeypatch.setattr(webapp_module, "_download_line_media_url", fake_download)

    assert webapp_module._download_message_content(Api(), "video-123", "Uchat") == b"video"
    assert calls == [("https://manager.line-scdn.net/video/full", session)]


@pytest.mark.parametrize(
    "url",
    [
        "http://manager.line-scdn.net/image",
        "https://line-scdn.net.evil.example/image",
        "https://127.0.0.1/image",
        "https://user:pass@manager.line-scdn.net/image",
        "https://manager.line-scdn.net:444/image",
    ],
)
def test_validated_line_media_url_rejects_untrusted_urls(url):
    with pytest.raises(RuntimeError, match="LINE media URL"):
        webapp_module._validated_line_media_url(url)


def test_validated_line_media_url_accepts_line_cdn_https():
    url = "https://manager.line-scdn.net/image/preview"
    assert webapp_module._validated_line_media_url(url) == url


def test_apply_placeholders_formats():
    import re as _re

    f = webapp_module._apply_placeholders
    assert _re.fullmatch(r"\d", f("{1D}"))
    assert _re.fullmatch(r"\d\d", f("{2D}"))
    assert _re.fullmatch(r"\d\d\d", f("{3D}"))
    assert f("{rand:7-7}") == "7"
    assert 1 <= int(f("{rand:9-1}")) <= 9  # bounds accepted either way
    assert _re.fullmatch(r"\d{2}/\d{2}/\d{4}", f("{date}"))
    assert _re.fullmatch(r"\d{2}:\d{2}", f("{time}"))
    assert f("plain text with no tokens") == "plain text with no tokens"


def test_apply_placeholders_randomizes_each_occurrence():
    # every occurrence of a digit placeholder is drawn independently, so a
    # message reusing {1D}/{2D}/{3D} does not repeat the same number.
    out = webapp_module._apply_placeholders("{1D}" * 20)
    assert len(out) == 20 and out.isdigit()
    assert len(set(out)) > 1  # 20 independent digits -> not all identical
    parts = webapp_module._apply_placeholders("{2D}-{2D}-{2D}").split("-")
    assert all(len(p) == 2 and p.isdigit() for p in parts)


def test_message_patterns_crud(web_state):
    initial = web_state.list_patterns()
    assert initial["patterns"] == []
    assert initial["categories"] == [{"id": "general", "name": "General", "system": True}]
    created = web_state.create_pattern({"name": "greeting", "text": "hi {1D}"})
    pid = created["pattern"]["id"]
    items = web_state.list_patterns()["patterns"]
    assert len(items) == 1
    assert items[0]["name"] == "greeting"
    assert items[0]["text"] == "hi {1D}"
    updated = web_state.update_pattern(
        {
            "id": pid,
            "name": "welcome",
            "text": "hello {2D}",
            "categoryId": "general",
        }
    )["pattern"]
    assert updated["id"] == pid
    assert updated["name"] == "welcome"
    assert updated["text"] == "hello {2D}"
    assert updated["categoryId"] == "general"
    assert updated["updatedAt"]
    with pytest.raises(WebError):
        web_state.create_pattern({"name": "", "text": "x"})
    with pytest.raises(WebError):
        web_state.create_pattern({"name": "x", "text": "  "})
    with pytest.raises(WebError):
        web_state.update_pattern({"id": pid, "name": "", "text": "x"})
    with pytest.raises(WebError):
        web_state.update_pattern({"id": "nope", "name": "x", "text": "y"})
    web_state.delete_pattern(pid)
    assert web_state.list_patterns()["patterns"] == []
    with pytest.raises(WebError):
        web_state.delete_pattern("nope")


def test_pattern_categories_group_and_move_patterns_to_general(web_state):
    category = web_state.create_pattern_category({"name": "Promotions"})["category"]
    created = web_state.create_pattern(
        {
            "name": "Weekend sale",
            "text": "Save 20%",
            "categoryId": category["id"],
        }
    )["pattern"]

    listed = web_state.list_patterns()
    assert [item["name"] for item in listed["categories"]] == [
        "General",
        "Promotions",
    ]
    assert listed["patterns"][0]["categoryId"] == category["id"]
    assert listed["patterns"][0]["categoryName"] == "Promotions"

    updated = web_state.update_pattern_category({"id": category["id"], "name": "Campaigns"})[
        "category"
    ]
    assert updated["name"] == "Campaigns"
    renamed = web_state.list_patterns()
    assert renamed["patterns"][0]["categoryName"] == "Campaigns"

    deleted = web_state.delete_pattern_category(category["id"])
    assert deleted["patternsMoved"] == 1
    moved = web_state.list_patterns()["patterns"][0]
    assert moved["id"] == created["id"]
    assert moved["categoryId"] == "general"
    assert moved["categoryName"] == "General"

    with pytest.raises(WebError):
        web_state.delete_pattern_category("general")


def test_pattern_categories_are_tenant_owned(web_state):
    auth = web_state.web_auth
    auth.setup("owner@example.com", "owner-pass")
    token_a = auth.register("category-a@example.com", "member-pass")
    token_b = auth.register("category-b@example.com", "member-pass")
    member_a = auth.current_user(WebAuth.cookie_header(token_a))
    member_b = auth.current_user(WebAuth.cookie_header(token_b))
    assert member_a and member_b

    category_a = web_state.create_pattern_category({"name": "Private A"}, member_a)["category"]
    pattern_a = web_state.create_pattern(
        {"name": "Member A pattern", "text": "private"}, member_a
    )["pattern"]
    assert [item["name"] for item in web_state.list_patterns(member_b)["categories"]] == [
        "General"
    ]
    with pytest.raises(WebError):
        web_state.create_pattern(
            {"name": "Wrong tenant", "text": "x", "categoryId": category_a["id"]},
            member_b,
        )
    with pytest.raises(WebError):
        web_state.update_pattern(
            {"id": pattern_a["id"], "name": "Hijacked", "text": "x"}, member_b
        )


def test_bot_logs_record_schedule_and_pattern_actions(web_state):
    _seed_account(web_state, "acct")

    created = web_state.create_schedule(
        {
            "accountId": "acct",
            "name": "morning",
            "to": "u" + "1" * 32,
            "contentSource": "text",
            "text": "hello",
            "mode": "once",
            "runAt": "2026-07-11T09:00",
        }
    )
    schedule_id = created["schedule"]["id"]
    web_state.toggle_schedule(schedule_id, False, "acct")
    pattern = web_state.create_pattern(
        {"accountId": "acct", "name": "greeting", "text": "hi"}
    )["pattern"]
    web_state.update_pattern(
        {
            "id": pattern["id"],
            "accountId": "acct",
            "name": "updated greeting",
            "text": "hello",
        }
    )
    web_state.delete_pattern(pattern["id"], "acct")

    logs = web_state.list_bot_logs("acct", {"role": "admin", "accountIds": ["acct"]})["logs"]
    assert [log["action"] for log in logs[:5]] == [
        "pattern.delete",
        "pattern.update",
        "pattern.create",
        "schedule.pause",
        "schedule.create",
    ]
    assert {log["accountId"] for log in logs} == {"acct"}

    scoped_admin = {"role": "admin", "accountIds": ["acct"]}
    cleared = web_state.clear_bot_logs("acct", scoped_admin)
    assert cleared["logs"][0]["action"] == "logs.clear"
    assert web_state.list_bot_logs("acct", scoped_admin)["logs"][0]["action"] == ("logs.clear")


def test_clear_stuck_schedules_resets_selected_account_only(web_state):
    _seed_account(web_state, "acct")
    now = "2026-07-11T12:00:00"
    web_state.account_store.data["accounts"].append(
        {
            "id": "other",
            "label": "Other LINE",
            "tokenFile": "",
            "createdAt": now,
            "updatedAt": now,
        }
    )
    web_state.account_store.save()

    first = web_state.create_schedule(
        {
            "accountId": "acct",
            "name": "stuck",
            "to": "u" + "2" * 32,
            "contentSource": "text",
            "text": "hello",
            "mode": "repeat",
            "windowStart": "09:00",
            "windowEnd": "18:00",
            "intervalMinutes": 15,
        }
    )["schedule"]
    second = web_state.create_schedule(
        {
            "accountId": "other",
            "name": "other stuck",
            "to": "u" + "3" * 32,
            "contentSource": "text",
            "text": "hello",
            "mode": "repeat",
            "windowStart": "09:00",
            "windowEnd": "18:00",
            "intervalMinutes": 15,
        }
    )["schedule"]
    with web_state.schedule_lock:
        for job in web_state.schedules:
            if job["id"] in {first["id"], second["id"]}:
                job["running"] = True
                job["status"] = "running"
                job["lastError"] = "stuck"
        web_state.save_schedules()

    cleared = web_state.clear_stuck_schedules(
        "acct", {"role": "admin", "accountIds": ["acct"]}
    )

    assert cleared["cleared"] == 1
    fixed = next(job for job in cleared["schedules"] if job["id"] == first["id"])
    assert fixed["running"] is False
    assert fixed["lastError"] is None
    assert fixed["status"] == "waiting"
    other = web_state._find_schedule(second["id"])
    assert other["running"] is True
    assert other["lastError"] == "stuck"
    logs = web_state.list_bot_logs("acct", {"role": "admin", "accountIds": ["acct"]})["logs"]
    assert logs[0]["action"] == "schedule.clear_stuck"


def test_bot_log_detail_sanitizes_long_line_media_urls(web_state):
    entry = web_state._bot_log_entry(
        "send.item.error",
        account_id="acct",
        detail=(
            "request to https://obs.line-apps.com/r/talk/m/622310832885727233 failed: timeout"
        ),
    )

    assert "obs.line-apps.com" in entry["detail"]
    assert "622310832885727233" not in entry["detail"]
    assert len(entry["detail"]) < 120


def test_bot_log_listing_sanitizes_existing_raw_details(web_state):
    web_state.store.set(
        "bot_logs",
        {
            "logs": [
                {
                    "id": "old",
                    "at": "2026-07-11T16:29:00",
                    "ts": 1783762140,
                    "action": "send.item.error",
                    "accountId": "acct",
                    "ok": False,
                    "detail": (
                        "request to https://obs.line-apps.com/r/talk/m/622310832885727233 "
                        "failed: timeout"
                    ),
                }
            ]
        },
    )

    detail = web_state.list_bot_logs("acct", {"role": "admin", "accountIds": ["acct"]})[
        "logs"
    ][0]["detail"]
    assert "obs.line-apps.com" in detail
    assert "622310832885727233" not in detail


def test_bot_log_detail_summarizes_line_media_ssl_eof(web_state):
    entry = web_state._bot_log_entry(
        "send.item.error",
        account_id="acct",
        detail=(
            "request to https://obs.line-apps.com/r/talk/m/622311294108172391 "
            "failed: HTTPSConnectionPool(host='obs.line-apps.com', port=443): "
            "Max retries exceeded with url: /r/talk/m/622311294108172391 "
            "(Caused by SSLError(SSLEOFError(8, 'EOF occurred in violation of protocol')))"
        ),
    )

    assert entry["detail"] == "LINE media upload failed after retries (SSL EOF)."


def test_bot_log_preserves_full_ai_prompt(web_state):
    prompt = ("Create a LINE campaign image\n" + ("full prompt details " * 35)).strip()
    entry = web_state._bot_log_entry(
        "content.ai.start",
        account_id="acct",
        detail=prompt,
        data={"prompt": prompt},
    )
    web_state.store.set("bot_logs", {"logs": [entry]})

    listed = web_state.list_bot_logs("acct", {"role": "admin", "accountIds": ["acct"]})[
        "logs"
    ][0]

    assert len(prompt) > 500
    assert listed["detail"] == prompt
    assert listed["data"]["prompt"] == prompt


def test_bot_log_preserves_full_ai_error(web_state):
    detail = "fal.ai error 422: " + ("complete upstream details " * 30).strip()
    entry = web_state._bot_log_entry(
        "content.ai.error",
        account_id="acct",
        detail=detail,
        ok=False,
    )

    assert len(detail) > 500
    assert entry["detail"] == detail


def test_resolve_job_contents_placeholders_and_uploaded_image():
    import re as _re

    items = webapp_module._resolve_job_contents({"contentSource": "text", "text": "code {3D}"})
    assert items[0]["kind"] == "text"
    assert _re.fullmatch(r"code \d\d\d", items[0]["text"])

    raw = b"\x89PNG-fake-bytes"
    job = {
        "contentSource": "image",
        "imageData": base64.b64encode(raw).decode(),
        "imageName": "shot.png",
    }
    img = webapp_module._resolve_job_contents(job)
    assert img[0]["kind"] == "image"
    assert img[0]["data"] == raw
    assert img[0]["name"] == "shot.png"


# -- Simple Mode + secure-with-password -----------------------------------
def test_ensure_simple_mode_provisions_hidden_admin(tmp_path):
    auth = WebAuth(str(tmp_path / "auth.json"))
    assert auth.configured() is False
    assert auth.simple_mode() is False

    token = auth.ensure_simple_mode()
    assert token

    assert auth.configured() is True
    assert auth.simple_mode() is True
    user = auth.current_user(WebAuth.cookie_header(token))
    assert user is not None
    assert user["role"] == "admin"
    # A second call must not re-provision once configured.
    assert auth.ensure_simple_mode() is None


def test_secure_with_password_leaves_simple_mode(tmp_path):
    auth = WebAuth(str(tmp_path / "auth.json"))
    token = auth.ensure_simple_mode()
    local = auth.current_user(WebAuth.cookie_header(token))

    auth.secure_with_password(local, "owner@example.com", "owner-pass")

    assert auth.simple_mode() is False
    new_token = auth.login("owner@example.com", "owner-pass")
    assert auth.is_authenticated(WebAuth.cookie_header(new_token)) is True
    # Done once: the secured account cannot run it again.
    secured = auth._find_user(local["id"])
    with pytest.raises(WebError) as again:
        auth.secure_with_password(secured, "other@example.com", "other-pass")
    assert again.value.status == HTTPStatus.FORBIDDEN


def test_only_the_local_user_can_secure_itself(tmp_path):
    # Any other account used to rewrite the first stored account's sign-in.
    auth = WebAuth(str(tmp_path / "auth.json"))
    auth.setup("owner@example.com", "owner-pass")
    auth.ensure_god("god", "god-password")
    auth.register("member@example.com", "member-pass")
    before = [(u["id"], u.get("email"), u.get("role")) for u in auth.data["users"]]
    for user in list(auth.data["users"]):
        with pytest.raises(WebError) as refused:
            auth.secure_with_password(user, "taken@example.com", "taken-pass")
        assert (refused.value.status, refused.value.code) == (HTTPStatus.FORBIDDEN, "forbidden")
    with pytest.raises(WebError):
        auth.secure_with_password(None, "taken@example.com", "taken-pass")
    assert [(u["id"], u.get("email"), u.get("role")) for u in auth.data["users"]] == before
    assert auth.login("owner@example.com", "owner-pass")
    assert auth.sign_in("god", "god-password")[1] is True


def test_create_user_leaves_simple_mode(tmp_path):
    auth = WebAuth(str(tmp_path / "auth.json"))
    auth.ensure_simple_mode()

    auth.create_user("viewer@example.com", "viewer-pass", "viewer", [])

    assert auth.simple_mode() is False


# -- WebError carries a stable machine code -------------------------------
def test_web_error_default_and_explicit_code():
    from http import HTTPStatus

    assert WebError(HTTPStatus.FORBIDDEN, "nope").code == "forbidden"
    assert WebError(HTTPStatus.BAD_REQUEST, "x", "no_account").code == "no_account"


# -- cancel_login frees the QR worker slot --------------------------------
def test_cancel_login_frees_slot(web_state):
    web_state.login = {"state": "qr", "id": "stale-id"}

    result = web_state.cancel_login()

    assert result["state"] == "idle"
    assert web_state.login["state"] == "idle"
    assert web_state.login["id"] != "stale-id"


def test_remote_first_run_requires_setup_token(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = _make_config(tmp_path)
    config.host = "0.0.0.0"
    config.setup_token = "deploy-setup-secret"
    server = OkLineWebServer(("127.0.0.1", 0), OkLineWebHandler)
    server.state = WebState(config)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        status = requests.get(f"{base}/api/auth/status", timeout=5).json()
        assert status["configured"] is False
        assert status["authenticated"] is False
        assert status["setupTokenRequired"] is True

        denied = requests.post(
            f"{base}/api/auth/setup",
            json={
                "email": "owner@example.com",
                "password": "owner-pass",
                "setupToken": "wrong",
            },
            timeout=5,
        )
        assert denied.status_code == HTTPStatus.FORBIDDEN

        created = requests.post(
            f"{base}/api/auth/setup",
            json={
                "email": "owner@example.com",
                "password": "owner-pass",
                "setupToken": "deploy-setup-secret",
            },
            timeout=5,
        )
        assert created.status_code == HTTPStatus.OK
        assert "Secure" in created.headers["set-cookie"]
    finally:
        server.shutdown()
        server.state.close()
        server.server_close()


def test_public_bind_fails_fast_without_first_run_setup_token(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("LINEPASSPORT_SETUP_TOKEN", raising=False)
    monkeypatch.delenv("OKLINE_SETUP_TOKEN", raising=False)
    config = _make_config(tmp_path)
    config.host = "0.0.0.0"

    with pytest.raises(WebError) as caught:
        webapp_module._bind_server(config)

    assert caught.value.code == "setup_token_not_configured"


def test_a_start_that_fails_for_another_reason_than_the_port_says_so(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = _make_config(tmp_path)
    config.port = 0
    starts = []

    def unreadable_state(config):
        starts.append(config.port)
        raise PermissionError(13, "Permission denied", "accounts.json")

    monkeypatch.setattr(webapp_module, "WebState", unreadable_state)
    with pytest.raises(PermissionError):
        webapp_module._bind_server(config)
    assert len(starts) == 1  # not tried again on other ports as if they were in use
    config.port = 18999
    with pytest.raises(PermissionError):
        webapp_module._bind_server(config)
    assert len(starts) == 2


def test_schedule_embedded_image_quota_counts_existing_jobs(monkeypatch):
    monkeypatch.setattr(webapp_module, "MAX_SCHEDULE_IMAGE_STORAGE_PER_ACCOUNT", 10)
    existing = [{"id": "one", "accountId": "acct", "imageData": "QUJDREVG"}]
    candidate = {"id": "two", "accountId": "acct", "imageData": "R0hJSktM"}

    with pytest.raises(WebError) as caught:
        webapp_module._ensure_schedule_image_quota(existing, candidate)

    assert caught.value.code == "schedule_storage_quota"


def test_bot_log_retention_honors_total_byte_cap(monkeypatch):
    monkeypatch.setattr(webapp_module, "MAX_BOT_LOG_STORAGE_BYTES", 250)
    logs = [
        {"id": str(index), "accountId": "acct", "detail": "x" * 100} for index in range(10)
    ]

    kept = webapp_module._trim_bot_logs(logs)

    assert kept[-1]["id"] == "9"
    assert len(kept) < len(logs)


def test_scheduler_image_source_rejects_local_file_path():
    with pytest.raises(WebError) as exc:
        webapp_module._image_content("pyproject.toml")
    assert exc.value.code == "invalid_image_url"


def test_public_url_validator_rejects_private_addresses(monkeypatch):
    monkeypatch.setattr(
        webapp_module.socket,
        "getaddrinfo",
        lambda *args, **kwargs: [(2, 1, 6, "", ("10.0.0.7", 80))],
    )
    with pytest.raises(WebError) as exc:
        webapp_module._validate_public_http_url("http://internal.example/resource")
    assert exc.value.code == "unsafe_url"


# -- HTTP integration: auth, codes, permissions, new routes ---------------
def test_simple_mode_auto_provisions_over_http(live_server):
    session = requests.Session()

    status = session.get(f"{live_server}/api/auth/status", timeout=5).json()
    assert status["simpleMode"] is True
    assert status["authenticated"] is True
    assert status["configured"] is True
    assert status["user"]["role"] == "admin"

    # The Set-Cookie from status auto-authenticates later protected calls.
    accounts = session.get(f"{live_server}/api/accounts", timeout=5)
    assert accounts.status_code == 200


def test_authenticated_http_api_rate_limit_returns_retry_after(
    live_server, monkeypatch
):
    monkeypatch.setitem(
        webapp_module.AUTHENTICATED_API_RATE_LIMITS,
        ("GET", "/api/status"),
        (2, 60.0),
    )
    session = requests.Session()
    session.get(f"{live_server}/api/auth/status", timeout=5)

    assert session.get(f"{live_server}/api/status", timeout=5).status_code == 200
    assert session.get(f"{live_server}/api/status", timeout=5).status_code == 200
    blocked = session.get(f"{live_server}/api/status", timeout=5)

    assert blocked.status_code == HTTPStatus.TOO_MANY_REQUESTS
    assert blocked.json()["code"] == "api_rate_limited"
    assert int(blocked.headers["Retry-After"]) >= 1


def test_protected_route_returns_error_and_code(live_server):
    # No cookie/session -> stable {error, code} instead of a raw message.
    resp = requests.get(f"{live_server}/api/status", timeout=5)
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == "auth_required"
    assert body["error"]


def test_login_cancel_route(live_server):
    session = requests.Session()
    session.get(f"{live_server}/api/auth/status", timeout=5)  # simple admin session

    resp = session.post(f"{live_server}/api/login/cancel", timeout=5)
    assert resp.status_code == 200
    assert resp.json()["state"] == "idle"


def test_register_route_creates_member_and_sets_cookie(live_server):
    admin = requests.Session()
    admin.get(f"{live_server}/api/auth/status", timeout=5)  # simple admin session
    secured = admin.post(
        f"{live_server}/api/auth/secure",
        json={"email": "owner@example.com", "password": "owner-pass"},
        timeout=5,
    )
    assert secured.status_code == 200

    newcomer = requests.Session()
    registered = newcomer.post(
        f"{live_server}/api/auth/register",
        json={
            "email": "newviewer@example.com",
            "displayName": "New Viewer",
            "password": "viewer-pass",
        },
        timeout=5,
    )
    assert registered.status_code == 200
    body = registered.json()
    assert body["authenticated"] is True
    assert body["user"]["role"] == "member"
    assert body["user"]["accountIds"] == []
    assert body["user"]["email"] == "newviewer@example.com"
    assert body["user"]["displayName"] == "New Viewer"

    status = newcomer.get(f"{live_server}/api/auth/status", timeout=5).json()
    assert status["authenticated"] is True
    assert status["user"]["username"] == "newviewer@example.com"

    # A newcomer cannot use the first-run route to take over the owner's account.
    takeover = newcomer.post(
        f"{live_server}/api/auth/secure",
        json={"email": "taken@example.com", "password": "taken-pass"},
        timeout=5,
    )
    assert (takeover.status_code, takeover.json()["code"]) == (403, "forbidden")
    owner = requests.post(
        f"{live_server}/api/auth/login",
        json={"email": "owner@example.com", "password": "owner-pass"},
        timeout=5,
    )
    assert owner.status_code == 200 and owner.json()["user"]["role"] == "admin"
    taken = requests.post(
        f"{live_server}/api/auth/login",
        json={"email": "taken@example.com", "password": "taken-pass"},
        timeout=5,
    )
    assert taken.status_code == HTTPStatus.UNAUTHORIZED
    # And the owner, already secured, cannot run it a second time either.
    again = admin.post(
        f"{live_server}/api/auth/secure",
        json={"email": "taken@example.com", "password": "taken-pass"},
        timeout=5,
    )
    assert again.status_code == HTTPStatus.FORBIDDEN


def test_registered_member_can_add_line_but_cannot_manage_users(live_server):
    admin = requests.Session()
    admin.get(f"{live_server}/api/auth/status", timeout=5)  # provisions simple admin
    secured = admin.post(
        f"{live_server}/api/auth/secure",
        json={"email": "owner@example.com", "password": "owner-pass"},
        timeout=5,
    )
    assert secured.status_code == 200

    member = requests.Session()
    registered = member.post(
        f"{live_server}/api/auth/register",
        json={"email": "member1@example.com", "password": "member-pass"},
        timeout=5,
    )
    assert registered.status_code == 200
    assert registered.json()["user"]["role"] == "member"

    assert member.get(f"{live_server}/api/login/state", timeout=5).status_code == 200
    denied = member.get(f"{live_server}/api/users", timeout=5)
    assert denied.status_code == 403


# -- AI image providers ----------------------------------------------------
def test_web_ui_has_bot_ai_settings_page_and_source():
    # Image generation settings live under Bot, separate from AI Chat Bot.
    assert 'data-tab="assistant"' in INDEX_HTML
    assert 'data-tab-panel="assistant"' in INDEX_HTML
    assert 'data-bot-page-target="ai-settings"' in INDEX_HTML
    assert 'data-bot-page="ai-settings"' in INDEX_HTML
    assert 'id="botAiSettings"' in INDEX_HTML
    assert 'id="aiApiKey"' in INDEX_HTML
    assert 'id="aiTestButton"' in INDEX_HTML
    assert 'id="aiProvider"' in INDEX_HTML  # provider selector (Google / Nano / fal.ai)
    assert 'id="aiAspectRatio"' in INDEX_HTML  # provider-specific image-size selector
    assert 'id="aiModelPrice"' in INDEX_HTML
    assert '"ai.hint_fal"' in INDEX_HTML
    assert 'addEventListener("change", () => activateAiProvider()' in INDEX_HTML
    # Bot scheduler gains the AI image content type + prompt field.
    assert 'value="ai_image"' in INDEX_HTML
    assert 'id="scheduleAiPrompt"' in INDEX_HTML
    assert '"scheduler.source_ai_image"' in INDEX_HTML


def test_assistant_has_an_in_app_usage_guide():
    assert 'data-assistant-view-target="guide"' in INDEX_HTML
    assert 'data-assistant-view="guide"' in INDEX_HTML
    assert 'id="assistantGuideKnowledgeButton"' in INDEX_HTML
    assert 'id="assistantGuideChatButton"' in INDEX_HTML
    assert '"help.assistant.s4_body"' in INDEX_HTML


def test_every_primary_menu_and_settings_has_a_detailed_usage_guide():
    # Every guide uses the same layout: header, numbered steps, tips and FAQ.
    for container, name in (
        ('id="lineGuideView"', "line"),
        ('id="assistantGuideView"', "assistant"),
        ('id="incomingApiGuidePage"', "incoming"),
        ('data-bot-page="guide"', "bot"),
        ('id="logsGuideView"', "logs"),
        ('id="toolsGuideView"', "tools"),
        ('id="settingsGuidePane"', "settings"),
        ('data-crm-pane="guide"', "customers"),
    ):
        assert container in INDEX_HTML
        for part in ("title", "intro", "s1_title", "s1_body", "tip1", "q1", "a1"):
            assert f'data-i18n="help.{name}.{part}"' in INDEX_HTML, (name, part)
            assert f'"help.{name}.{part}": {{th: ' in INDEX_HTML, (name, part)
    assert INDEX_HTML.count('<div class="help-page">') == 8
    assert 'id="lineGuideButton"' in INDEX_HTML
    assert 'id="assistantGuideAutoReplyButton"' in INDEX_HTML
    assert 'id="toolsGuideViewButton"' in INDEX_HTML
    assert 'id="logsGuideViewButton"' in INDEX_HTML
    assert 'id="settingsGuideTabButton"' in INDEX_HTML
    # The Incoming API guide keeps its ready-to-copy request examples.
    assert 'id="incomingApiGuideGetCode"' in INDEX_HTML
    assert 'data-i18n="help.common.dev_examples"' in INDEX_HTML

    assert 'data-god-page-target="guide"' in GOD_HTML
    assert 'data-god-page="guide"' in GOD_HTML
    assert '"unanswered", "system", "guide"' in GOD_HTML


def test_assistant_general_knowledge_mode_is_the_default(web_state):
    # The God page has no AI settings of its own; each shop's form starts unticked.
    assert "StrictKnowledge" not in GOD_HTML
    assert "strict.checked = Boolean(data.strictKnowledge);" in INDEX_HTML
    token = web_state.web_auth.setup("owner@example.com", "owner-pass")
    owner = web_state.web_auth.current_user(WebAuth.cookie_header(token))
    assert web_state.assistant_settings(owner)["strictKnowledge"] is False
    assert not hasattr(web_state, "global_ai_settings")  # no shared AI settings any more


class _ImmediateExecutor:
    def submit(self, fn, *args, **kwargs):
        fn(*args, **kwargs)
        return None

    def shutdown(self, **kwargs):
        return None


class _AutoReplyFakeApi:
    def __init__(self):
        self.boxes = {"U-contact": [], "C-group": []}
        self.sent = []
        self.sent_event = threading.Event()
        self.message_box_calls = 0
        self.contact_lookup_calls = 0
        self.group_lookup_calls = 0

    def get_message_boxes(self, *, limit=100, last_messages_per_box=5):
        self.message_box_calls += 1
        return {
            "messageBoxes": [
                {
                    "id": mid,
                    "lastMessages": list(messages[-last_messages_per_box:]),
                }
                for mid, messages in list(self.boxes.items())[:limit]
            ]
        }

    def get_profile(self):
        return {"mid": "U-me", "displayName": "Owner"}

    def get_contacts(self, mids):
        self.contact_lookup_calls += 1
        names = {
            "U-contact": "Customer One",
            "U-member": "Group Member",
        }
        return {
            "contacts": {
                mid: {"contact": {"mid": mid, "displayName": names.get(mid, mid)}}
                for mid in mids
            }
        }

    def get_chats(self, mids):
        self.group_lookup_calls += 1
        names = {"C-group": "Support Group", "C-other": "Other Group"}
        return {
            "chats": [
                {
                    "chatMid": mid,
                    "chatName": names.get(mid, mid),
                    "extra": {"groupExtra": {"memberMids": ["U-member"]}},
                }
                for mid in mids
            ]
        }

    def send_text(self, mid, text):
        self.sent.append((mid, text))
        self.sent_event.set()
        return {"id": f"reply-{len(self.sent)}"}


def _auto_reply_owner(web_state):
    token = web_state.web_auth.setup("owner@example.com", "owner-pass")
    owner = web_state.web_auth.current_user(WebAuth.cookie_header(token))
    assert owner
    _seed_account(web_state, "auto-account")
    web_state.account_store.data["accounts"][0]["mid"] = "U-me"
    web_state.account_store.save()
    web_state.web_auth.grant_account_access(owner["id"], "auto-account")
    owner = web_state.web_auth._find_user(owner["id"])
    assert owner
    # The workspace's own AI: each workspace uses its own provider and key.
    global_ai = web_state._global_ai_all()
    global_ai["tenants"][owner["id"]] = {
        "settings": {
            "provider": "openai",
            "enabled": True,
            "providers": {
                "openai": {
                    "apiKey": web_state._encrypt_secret("sk-test-key"),
                    "chatModel": "test-model",
                    "embeddingModel": "",
                }
            },
        },
        "apiSources": [],
        "manualAnswers": [],
        "chunks": [],
    }
    web_state.store.set("global_ai", global_ai)
    return owner


def test_revoked_line_session_is_persisted_exposed_and_logged_once(web_state):
    owner = _auto_reply_owner(web_state)
    failure = LineAuthError(
        "V3_TOKEN_CLIENT_LOGGED_OUT",
        code=8,
        path="/api/talk/thrift/Talk/TalkService/getProfile",
        status=200,
    )

    web_state._handle_account_auth_failure("auto-account", failure)
    web_state._handle_account_auth_failure("auto-account", failure)

    account = web_state.account_store.get("auto-account")
    assert account["sessionState"] == "reauth_required"
    assert "code 8" in account["sessionError"]
    assert web_state.account_store.list_accounts({"auto-account"})[0][
        "sessionState"
    ] == "reauth_required"
    with pytest.raises(LineLoginRequired):
        web_state.get_api("auto-account")

    logs = web_state.list_bot_logs("auto-account", owner)["logs"]
    session_logs = [
        item for item in logs if item["action"] == "line.session.reauth_required"
    ]
    assert len(session_logs) == 1
    assert session_logs[0]["ok"] is False


def test_revoked_line_session_pauses_auto_reply_without_line_api_call(web_state):
    owner = _auto_reply_owner(web_state)
    web_state.save_ai_auto_reply_settings(
        "auto-account",
        {"enabled": True, "contactsEnabled": True, "groupsEnabled": True},
        owner,
    )
    web_state.account_store.mark_session_reauth_required(
        "auto-account", "Connect the account again."
    )
    called = []
    web_state._poll_ai_auto_reply_account = lambda *args: called.append(args)

    web_state.poll_ai_auto_replies()

    assert called == []
    settings = web_state.ai_auto_reply_settings("auto-account", owner)
    assert settings["runtime"]["state"] == "login_required"
    assert "Connect the account again" in settings["runtime"]["lastError"]


def test_revoked_line_session_pauses_due_schedule_without_submission(web_state):
    _seed_account(web_state, "scheduled-account")
    web_state.account_store.mark_session_reauth_required(
        "scheduled-account", "Connect the account again."
    )
    web_state.schedules = [
        {
            "id": "paused-job",
            "accountId": "scheduled-account",
            "enabled": True,
            "running": False,
            "nextRunAt": 1,
            "status": "waiting",
        }
    ]

    web_state.run_due_schedules()

    job = web_state.schedules[0]
    assert job["status"] == "login required"
    assert "Connect the account again" in job["lastError"]
    assert web_state.schedule_inflight == set()


def test_web_ui_exposes_reconnect_states_for_accounts_workers_and_schedules():
    assert '"accounts.reconnect_required"' in INDEX_HTML
    assert '"pills.reconnect_required"' in INDEX_HTML
    assert '"assistant.auto_reply_login_required"' in INDEX_HTML
    assert '"scheduler.login_required"' in INDEX_HTML
    assert 'account.sessionState === "reauth_required"' in INDEX_HTML
    assert 'reconnect.addEventListener("click", () => openReconnect(account));' in INDEX_HTML
    assert '"login.reconnect_title"' in INDEX_HTML
    assert '"toast.account_reconnected"' in INDEX_HTML


def test_ai_auto_reply_separates_contacts_groups_and_skips_own_messages(web_state):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    owner = _auto_reply_owner(web_state)
    fake = _AutoReplyFakeApi()
    web_state.get_api = lambda account_id, **kwargs: fake
    asked = []

    def ask(question, user, **metadata):
        asked.append((question, metadata))
        return {
            "ok": True,
            "question": {"answer": f"AI: {question}", "model": "test-model"},
        }

    web_state.ask_global_ai = ask
    settings = web_state.save_ai_auto_reply_settings(
        "auto-account",
        {"enabled": True, "contactsEnabled": True, "groupsEnabled": False},
        owner,
    )
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    assert settings["enabled"] is True
    assert settings["contactsEnabled"] is True
    assert settings["groupsEnabled"] is False

    fake.boxes["U-contact"].append(
        {"id": "old-contact", "from": "U-contact", "text": "old", "contentType": 0, "createdTime": enabled_at - 1000}
    )
    fake.boxes["C-group"].append(
        {"id": "old-group", "from": "U-member", "text": "old group", "contentType": 0, "createdTime": enabled_at - 1000}
    )
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()
    assert fake.sent == []

    fake.boxes["U-contact"].append(
        {"id": "new-contact", "from": "U-contact", "text": "hello", "contentType": 0, "createdTime": enabled_at + 1000}
    )
    fake.boxes["C-group"].append(
        {"id": "new-group-disabled", "from": "U-member", "text": "group ignored", "contentType": 0, "createdTime": enabled_at + 1000}
    )
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()
    assert fake.sent == [("U-contact", "AI: hello")]
    metadata = dict(asked[0][1])
    # The AI record and settings the reply read (so a failed read is told from a
    # failed AI).
    assert isinstance(metadata.pop("data"), dict)
    assert metadata.pop("settings")["configured"] is True
    assert metadata == {
        "source": "line_auto_reply",
        "account_id": "auto-account",
        "chat_mid": "U-contact",
        "chat_type": "contact",
    }

    fake.boxes["U-contact"].append(
        {"id": "own-message", "from": "U-me", "text": "manual reply", "contentType": 0, "createdTime": enabled_at + 2000}
    )
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()
    assert len(fake.sent) == 1

    web_state.save_ai_auto_reply_settings(
        "auto-account",
        {"enabled": True, "contactsEnabled": True, "groupsEnabled": True},
        owner,
    )
    fake.boxes["C-group"].append(
        {"id": "new-group", "from": "U-member", "text": "group hello", "contentType": 0, "createdTime": enabled_at + 3000}
    )
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()
    assert fake.sent[-1] == ("C-group", "AI: group hello")
    assert [entry[0] for entry in fake.sent] == ["U-contact", "C-group"]

    logs = web_state.list_bot_logs("auto-account", owner)["logs"]
    assert any(log["action"] == "ai.auto_reply.received" for log in logs)
    assert any(log["action"] == "ai.auto_reply.sent" for log in logs)
    contact_sent = next(
        log
        for log in logs
        if log["action"] == "ai.auto_reply.sent"
        and log["data"]["chatMid"] == "U-contact"
    )
    assert contact_sent["data"]["chatName"] == "Customer One"
    assert contact_sent["data"]["senderName"] == "Customer One"
    group_sent = next(
        log
        for log in logs
        if log["action"] == "ai.auto_reply.sent"
        and log["data"]["chatMid"] == "C-group"
    )
    assert group_sent["data"]["chatName"] == "Support Group"
    assert group_sent["data"]["senderName"] == "Group Member"
    lookup_counts = (fake.contact_lookup_calls, fake.group_lookup_calls)
    web_state._hydrate_ai_auto_reply_candidate_identities(
        "auto-account",
        fake,
        [{"chatMid": "C-group", "senderMid": "U-member"}],
    )
    assert (fake.contact_lookup_calls, fake.group_lookup_calls) == lookup_counts


def test_ai_auto_reply_only_answers_selected_groups(web_state):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    owner = _auto_reply_owner(web_state)
    fake = _AutoReplyFakeApi()
    fake.boxes["C-other"] = []
    web_state.get_api = lambda account_id, **kwargs: fake
    asked = []

    def ask(question, user, **metadata):
        asked.append((question, metadata))
        return {
            "ok": True,
            "question": {"answer": f"AI: {question}", "model": "test-model"},
        }

    web_state.ask_global_ai = ask
    settings = web_state.save_ai_auto_reply_settings(
        "auto-account",
        {
            "enabled": True,
            "contactsEnabled": False,
            "groupsEnabled": True,
            "groupReplyMode": "selected",
            "selectedGroupMids": ["C-group"],
        },
        owner,
    )
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    assert settings["groupReplyMode"] == "selected"
    assert settings["selectedGroupMids"] == ["C-group"]

    fake.boxes["C-group"].append(
        {
            "id": "selected-group-message",
            "from": "U-member",
            "text": "selected hello",
            "contentType": 0,
            "createdTime": enabled_at + 1000,
        }
    )
    fake.boxes["C-other"].append(
        {
            "id": "other-group-message",
            "from": "U-member",
            "text": "other ignored",
            "contentType": 0,
            "createdTime": enabled_at + 1000,
        }
    )
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()

    assert fake.sent == [("C-group", "AI: selected hello")]
    assert [entry[0] for entry in asked] == ["selected hello"]

    web_state.save_ai_auto_reply_settings(
        "auto-account",
        {
            "enabled": True,
            "contactsEnabled": False,
            "groupsEnabled": True,
            "groupReplyMode": "selected",
            "selectedGroupMids": ["C-other"],
        },
        owner,
    )
    fake.boxes["C-group"].append(
        {
            "id": "selected-group-disabled",
            "from": "U-member",
            "text": "now ignored",
            "contentType": 0,
            "createdTime": enabled_at + 2000,
        }
    )
    fake.boxes["C-other"].append(
        {
            "id": "other-group-enabled",
            "from": "U-member",
            "text": "now selected",
            "contentType": 0,
            "createdTime": enabled_at + 2000,
        }
    )
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()

    assert fake.sent[-1] == ("C-other", "AI: now selected")
    assert [entry[0] for entry in fake.sent] == ["C-group", "C-other"]


def test_ai_auto_reply_selected_group_mode_requires_valid_group(web_state):
    owner = _auto_reply_owner(web_state)

    with pytest.raises(WebError) as missing:
        web_state.save_ai_auto_reply_settings(
            "auto-account",
            {
                "enabled": False,
                "contactsEnabled": True,
                "groupsEnabled": True,
                "groupReplyMode": "selected",
                "selectedGroupMids": [],
            },
            owner,
        )
    assert missing.value.code == "auto_reply_group_required"

    with pytest.raises(WebError) as invalid:
        web_state.save_ai_auto_reply_settings(
            "auto-account",
            {
                "enabled": False,
                "contactsEnabled": True,
                "groupsEnabled": True,
                "groupReplyMode": "selected",
                "selectedGroupMids": ["U-contact"],
            },
            owner,
        )
    assert invalid.value.code == "invalid_auto_reply_setting"


def test_inbox_snapshot_is_shared_by_auto_reply_and_conversation_summaries(web_state):
    _seed_account(web_state, "auto-account")
    fake = _AutoReplyFakeApi()
    fake.boxes["U-contact"].append(
        {
            "id": "message-1",
            "from": "U-contact",
            "text": "hello",
            "contentType": 0,
            "createdTime": 1000,
        }
    )

    first = web_state.inbox_message_boxes("auto-account", fake)
    summaries = web_state.chat_summary_map("auto-account", fake)

    assert first["messageBoxes"]
    assert summaries["U-contact"]["lastMessage"]["text"] == "hello"
    assert fake.message_box_calls == 1

    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.chat_summary_map("auto-account", fake)
    assert fake.message_box_calls == 2


def test_inbox_snapshot_coalesces_concurrent_line_reads(web_state):
    class SlowInboxApi(_AutoReplyFakeApi):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()

        def get_message_boxes(self, *, limit=100, last_messages_per_box=5):
            self.message_box_calls += 1
            self.started.set()
            assert self.release.wait(timeout=2)
            return {"messageBoxes": []}

    _seed_account(web_state, "auto-account")
    fake = SlowInboxApi()
    results = []

    def read_inbox():
        results.append(web_state.inbox_message_boxes("auto-account", fake))

    first = threading.Thread(target=read_inbox)
    second = threading.Thread(target=read_inbox)
    first.start()
    assert fake.started.wait(timeout=2)
    second.start()
    fake.release.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert not first.is_alive()
    assert not second.is_alive()
    assert results == [{"messageBoxes": []}, {"messageBoxes": []}]
    assert fake.message_box_calls == 1


def test_ai_auto_reply_worker_runs_without_browser_requests(web_state, monkeypatch):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    owner = _auto_reply_owner(web_state)
    fake = _AutoReplyFakeApi()
    web_state.get_api = lambda account_id, **kwargs: fake
    web_state.ask_global_ai = lambda question, user, **metadata: {
        "ok": True,
        "question": {"answer": f"AI: {question}", "model": "test-model"},
    }
    web_state.save_ai_auto_reply_settings(
        "auto-account",
        {"enabled": True, "contactsEnabled": True, "groupsEnabled": False},
        owner,
    )
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    fake.boxes["U-contact"].append(
        {
            "id": "background-message",
            "from": "U-contact",
            "text": "still there?",
            "contentType": 0,
            "createdTime": enabled_at + 1,
        }
    )
    monkeypatch.setattr(webapp_module, "AI_AUTO_REPLY_POLL_SECONDS", 0.01)

    web_state.ai_auto_reply_engine = webapp_module.AiAutoReplyEngine(web_state)
    assert fake.sent_event.wait(timeout=2)
    web_state.ai_auto_reply_engine.close()

    assert fake.sent == [("U-contact", "AI: still there?")]
    assert fake.message_box_calls == 1


def test_ai_auto_reply_requires_configured_ai(web_state):
    owner = _auto_reply_owner(web_state)
    global_ai = web_state._global_ai_all()
    # The workspace's provider has no key, and a Shared AI record left in the store
    # does not stand in for it.
    global_ai["tenants"][owner["id"]]["settings"]["providers"]["openai"]["apiKey"] = ""
    global_ai["settings"] = {"enabled": True, "chatModel": "test-model"}
    web_state.store.set("global_ai", global_ai)

    with pytest.raises(WebError) as exc:
        web_state.save_ai_auto_reply_settings(
            "auto-account",
            {"enabled": True, "contactsEnabled": True, "groupsEnabled": True},
            owner,
        )

    assert exc.value.status == HTTPStatus.CONFLICT
    assert exc.value.code == "assistant_not_configured"


def test_the_shared_ai_routes_are_gone_and_god_knowledge_is_keyword_only(
    god_live_server, monkeypatch
):
    def no_ai(*args, **kwargs):
        raise AssertionError("the God's knowledge needs no AI provider")

    monkeypatch.setattr(webapp_module, "make_ai_client", no_ai)
    god = requests.Session()
    god.post(
        f"{god_live_server}/api/auth/login",
        json={"username": "god", "password": "god-password"},
        timeout=5,
    ).raise_for_status()
    # What a God page opened before the update still sends: a clean 404 each time.
    for method, path, body in (
        ("GET", "/api/god/ai/settings", None),
        ("POST", "/api/god/ai/settings",
         {"enabled": True, "baseUrl": "http://127.0.0.1:11434", "chatModel": "llama3"}),
        ("POST", "/api/god/ai/test", {"baseUrl": "http://127.0.0.1:11434"}),
    ):
        gone = god.request(method, f"{god_live_server}{path}", json=body, timeout=5)
        assert (gone.status_code, gone.json()["code"]) == (404, "not_found"), path
        assert "Reload this page" in gone.json()["error"]
    saved = god.post(
        f"{god_live_server}/api/god/ai/knowledge",
        json={"knowledgeMarkdown": "# ส่งของ\n\nส่งฟรีทั่วประเทศเมื่อซื้อครบ 500 บาท"},
        timeout=5,
    )
    assert saved.status_code == 200, saved.text
    knowledge = saved.json()
    assert (knowledge["indexMode"], knowledge["chunkCount"], knowledge["indexError"]) == (
        "keyword", 1, ""
    )
    rebuilt = god.post(f"{god_live_server}/api/god/ai/reindex", json={}, timeout=5)
    assert rebuilt.status_code == 200 and rebuilt.json()["indexMode"] == "keyword"
    assert god.get(f"{god_live_server}/api/god/ai/knowledge", timeout=5).json()["chunkCount"] == 1
    unanswered = god.get(f"{god_live_server}/api/god/ai/unanswered?limit=500", timeout=5)
    assert unanswered.status_code == 200 and unanswered.json() == {"questions": []}


def test_a_god_answer_without_an_owner_joins_the_shared_knowledge_by_keyword(
    web_state, monkeypatch
):
    def no_ai(*args, **kwargs):
        raise AssertionError("no AI provider is needed")

    monkeypatch.setattr(webapp_module, "make_ai_client", no_ai)
    data = web_state._global_ai_all()
    data["chunks"] = [{"id": "old", "text": "old", "embedding": [0.1, 0.2]}]
    data["questions"] = [
        {"id": "q1", "question": "ส่งต่างประเทศไหม", "status": "unanswered",
         "createdAt": "2026-11-21T10:00:00"}
    ]
    web_state.store.set("global_ai", data)
    result = web_state.answer_assistant_question(
        {"id": "q1", "answer": "ยังไม่ส่งต่างประเทศ"}, {"displayName": "God"}
    )
    assert result["question"]["status"] == "resolved"
    assert (result["knowledge"]["indexMode"], result["knowledge"]["chunkCount"]) == ("keyword", 1)
    stored = web_state._global_ai_all()
    assert stored["manualAnswers"][0]["answer"] == "ยังไม่ส่งต่างประเทศ"
    assert all("embedding" not in chunk for chunk in stored["chunks"])
    assert stored["embeddingModel"] == ""


def test_a_god_answer_to_a_staff_question_joins_the_owners_shop_knowledge(
    web_state, monkeypatch
):
    def no_ai(*args, **kwargs):
        raise AssertionError("no AI provider is needed")

    monkeypatch.setattr(webapp_module, "make_ai_client", no_ai)
    token = web_state.web_auth.setup("owner@example.com", "owner-pass")
    owner = web_state.web_auth.current_user(WebAuth.cookie_header(token))
    _seed_account(web_state, "acct")
    web_state.web_auth.grant_account_access(owner["id"], "acct")
    staff = web_state.web_auth.create_staff(owner, "staff@example.com", "staff-pass", "Staff", ["acct"])
    data = web_state._global_ai_all()
    data["questions"] = [
        {"id": "q-staff", "userId": staff["id"], "question": "ส่งต่างประเทศไหม",
         "status": "unanswered", "createdAt": "2026-11-21T10:00:00"},
        # Asked through the owner's account by someone removed since.
        {"id": "q-gone", "userId": "removed-user", "accountId": "acct",
         "question": "มีหน้าร้านไหม", "status": "unanswered", "createdAt": "2026-11-21T10:01:00"},
    ]
    web_state.store.set("global_ai", data)
    assert [item["id"] for item in web_state.tenant_unanswered(owner)["questions"]] == ["q-staff"]

    result = web_state.answer_assistant_question(
        {"id": "q-staff", "answer": "ยังไม่ส่งต่างประเทศ"}, {"displayName": "God"}
    )
    assert result["question"]["status"] == "resolved"
    assert (result["knowledge"]["chunkCount"], result["knowledge"]["indexMode"]) == (1, "keyword")
    stored = web_state._global_ai_all()
    assert staff["id"] not in stored["tenants"]  # staff have no shop data of their own
    assert stored["manualAnswers"] == []  # nor is it the God's global knowledge
    owner_tenant = stored["tenants"][owner["id"]]
    assert [item["answer"] for item in owner_tenant["manualAnswers"]] == ["ยังไม่ส่งต่างประเทศ"]
    assert "ยังไม่ส่งต่างประเทศ" in owner_tenant["chunks"][0]["text"]
    assert web_state.tenant_unanswered(owner)["questions"] == []

    web_state.answer_assistant_question({"id": "q-gone", "answer": "ไม่มีหน้าร้าน"}, {"displayName": "God"})
    stored = web_state._global_ai_all()
    assert "removed-user" not in stored["tenants"]
    assert [item["answer"] for item in stored["tenants"][owner["id"]]["manualAnswers"]] == [
        "ยังไม่ส่งต่างประเทศ", "ไม่มีหน้าร้าน",
    ]


def test_ai_auto_reply_passes_over_messages_quietly_without_a_workspace_ai(web_state):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    owner = _auto_reply_owner(web_state)
    fake = _AutoReplyFakeApi()
    web_state.get_api = lambda account_id, **kwargs: fake
    asked = []

    def ask(question, user, **metadata):
        asked.append(question)
        return {"ok": True, "question": {"answer": f"AI: {question}", "model": "test-model"}}

    web_state.ask_global_ai = ask
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
    )
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    # The workspace's AI goes away: its key is removed.
    web_state.save_assistant_settings({"provider": "openai", "clearApiKey": True, "test": False}, owner)
    fake.boxes["U-contact"].append(
        {"id": "while-missing", "from": "U-contact", "text": "anyone there?", "contentType": 0,
         "createdTime": enabled_at + 1000}
    )
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()
    assert asked == [] and fake.sent == []
    assert web_state.ai_auto_reply_rate_events == {}  # no rate-limit use
    actions = {log["action"] for log in web_state.list_bot_logs("auto-account", owner)["logs"]}
    assert not actions & {"ai.auto_reply.received", "ai.auto_reply.error", "ai.auto_reply.rate_limit"}
    view = web_state.ai_auto_reply_settings("auto-account", owner)
    assert (view["enabled"], view["aiConfigured"]) == (True, False)
    assert (view["runtime"]["state"], view["runtime"]["lastError"]) == ("ai_missing", "")
    # With an AI again, new messages are answered; the one passed over is not.
    web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-test-key-again", "test": False}, owner
    )
    assert web_state.ai_auto_reply_settings("auto-account", owner)["runtime"]["state"] == "starting"
    fake.boxes["U-contact"].append(
        {"id": "after", "from": "U-contact", "text": "hello", "contentType": 0,
         "createdTime": enabled_at + 2000}
    )
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()
    assert asked == ["hello"] and fake.sent == [("U-contact", "AI: hello")]
    assert web_state.ai_auto_reply_settings("auto-account", owner)["runtime"]["state"] == "running"


class _RecordingExecutor:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args, **kwargs):
        self.submitted.append(args[0] if args else None)
        return None

    def shutdown(self, **kwargs):
        return None


def _count_global_ai_reads(web_state, monkeypatch):
    reads = []
    original = web_state.store.get

    def counting_get(key, default):
        if key == "global_ai":
            reads.append(key)
        return original(key, default)

    monkeypatch.setattr(web_state.store, "get", counting_get)
    return reads


def test_line_auto_reply_reads_the_ai_settings_only_for_a_message_to_answer(
    web_state, monkeypatch
):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    recorder = _RecordingExecutor()
    web_state.ai_auto_reply_executor = recorder
    owner = _auto_reply_owner(web_state)
    # A second LINE account of the same owner.
    web_state.account_store.data["accounts"].append(
        {"id": "auto-account-2", "label": "Second LINE", "mid": "U-me-2", "tokenFile": "",
         "createdAt": "2026-07-11T12:00:00", "updatedAt": "2026-07-11T12:00:00"}
    )
    web_state.account_store.save()
    web_state.web_auth.grant_account_access(owner["id"], "auto-account-2")
    owner = web_state.web_auth._find_user(owner["id"])
    fakes = {"auto-account": _AutoReplyFakeApi(), "auto-account-2": _AutoReplyFakeApi()}
    web_state.get_api = lambda account_id, **kwargs: fakes[account_id]
    for account_id in fakes:
        web_state.save_ai_auto_reply_settings(
            account_id, {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
        )
    reads = _count_global_ai_reads(web_state, monkeypatch)

    # Nothing new in either inbox: the AI settings are not read at all.
    web_state.poll_ai_auto_replies()
    web_state.poll_ai_auto_replies()
    assert reads == [] and recorder.submitted == []

    def new_message(message_id, text):
        for account_id, fake in fakes.items():
            enabled_at = web_state._ai_auto_reply_record(account_id)["enabledAtMs"]
            fake.boxes["U-contact"].append(
                {"id": f"{account_id}-{message_id}", "from": "U-contact", "text": text,
                 "contentType": 0, "createdTime": enabled_at + 1000 * message_id}
            )
            web_state.invalidate_inbox_snapshot(account_id)

    # Both accounts of the owner have a message to answer: one read in the cycle.
    new_message(1, "hello")
    web_state.poll_ai_auto_replies()
    assert len(reads) == 1
    assert sorted(candidate["accountId"] for candidate in recorder.submitted) == [
        "auto-account", "auto-account-2"
    ]
    assert web_state.ai_auto_reply_runtime["auto-account"]["state"] == "running"

    # Without an AI, the next messages are passed over quietly and their cursors move.
    web_state.save_assistant_settings({"provider": "openai", "clearApiKey": True, "test": False}, owner)
    with web_state.ai_auto_reply_lock:
        # The recorder never ran the replies: they still hold their chats and places.
        assert web_state.ai_auto_reply_owner_jobs == {owner["id"]: 2}
        web_state.ai_auto_reply_inflight.clear()
        web_state.ai_auto_reply_owner_jobs.clear()
    recorder.submitted.clear()
    reads.clear()
    rate_used = {key: len(stamps) for key, stamps in web_state.ai_auto_reply_rate_events.items()}
    new_message(2, "anyone there?")
    web_state.poll_ai_auto_replies()
    assert len(reads) == 1 and recorder.submitted == []
    assert {
        key: len(stamps) for key, stamps in web_state.ai_auto_reply_rate_events.items()
    } == rate_used  # no rate-limit use
    for account_id in fakes:
        assert web_state.ai_auto_reply_runtime[account_id]["state"] == "ai_missing"
    # With the AI back, the message passed over is not answered later.
    web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-test-key-again", "test": False}, owner
    )
    for account_id in fakes:
        web_state.invalidate_inbox_snapshot(account_id)
    web_state.poll_ai_auto_replies()
    assert recorder.submitted == []


class _FailingChatClient:
    def __init__(self, error):
        self.error = error

    def embed(self, model, texts, timeout=None):
        return [[1.0] for _ in texts]

    def chat(self, **kwargs):
        raise self.error

    def list_models(self, timeout=None):
        return []


def test_auto_reply_failures_are_logged_with_the_ai_key_hidden(web_state, monkeypatch):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    owner = _auto_reply_owner(web_state)  # its OpenAI key is "sk-test-key"
    fake = _AutoReplyFakeApi()
    web_state.get_api = lambda account_id, **kwargs: fake
    errors = iter([
        webapp_module.AssistantError("OpenAI error 429: quota exceeded for sk-test-key", 429),
        # Not a provider error, and still not allowed to show the key.
        RuntimeError("socket closed while sending sk-test-key"),
    ])
    monkeypatch.setattr(
        webapp_module, "make_ai_client", lambda *args, **kwargs: _FailingChatClient(next(errors))
    )
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
    )
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    for index, text in enumerate(("first", "second")):
        fake.boxes["U-contact"].append(
            {"id": f"m{index}", "from": "U-contact", "text": text, "contentType": 0,
             "createdTime": enabled_at + 1000 * (index + 1)}
        )
        web_state.invalidate_inbox_snapshot("auto-account")
        web_state.poll_ai_auto_replies()
        runtime = web_state.ai_auto_reply_settings("auto-account", owner)["runtime"]
        assert runtime["state"] == "error"
        assert "sk-test-key" not in runtime["lastError"] and "[API key]" in runtime["lastError"]
    failures = [
        log for log in web_state.list_bot_logs("auto-account", owner)["logs"]
        if log["action"] == "ai.auto_reply.error"
    ]
    assert len(failures) == 2
    assert all("sk-test-key" not in log["detail"] and "[API key]" in log["detail"] for log in failures)
    assert fake.sent == []  # nothing is sent when the provider fails


def test_auto_replies_of_every_workspace_share_four_workers(web_state):
    assert webapp_module.AI_AUTO_REPLY_WORKERS == 4
    assert web_state.ai_auto_reply_executor._max_workers == 4
    # One workspace gets at most half of them, running or queued.
    assert webapp_module.AI_AUTO_REPLY_JOBS_PER_OWNER == 2


def _wait_for(condition, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def _second_line_owner(web_state, account_id, own_mid, api_key):
    """A registered member with a LINE account and its own AI key."""
    token = web_state.web_auth.register("member@example.com", "member-pass", "Member")
    member = web_state.web_auth.current_user(WebAuth.cookie_header(token))
    web_state.account_store.data["accounts"].append(
        {"id": account_id, "label": "Member LINE", "mid": own_mid, "tokenFile": "",
         "createdAt": "2026-07-11T12:00:00", "updatedAt": "2026-07-11T12:00:00"}
    )
    web_state.account_store.save()
    web_state.web_auth.grant_account_access(member["id"], account_id)
    global_ai = web_state._global_ai_all()
    global_ai["tenants"][member["id"]] = {
        "settings": {"provider": "openai", "enabled": True, "providers": {"openai": {
            "apiKey": web_state._encrypt_secret(api_key), "chatModel": "test-model",
            "embeddingModel": ""}}},
        "apiSources": [], "manualAnswers": [], "chunks": [],
    }
    web_state.store.set("global_ai", global_ai)
    web_state._forget_ai_readiness()
    return web_state.web_auth._find_user(member["id"])


def test_line_auto_replies_wait_for_the_owners_share_of_the_workers(web_state, monkeypatch):
    web_state.ai_auto_reply_engine.close()  # this test polls by itself
    owner_a = _auto_reply_owner(web_state)  # its OpenAI key is "sk-test-key"
    owner_b = _second_line_owner(web_state, "b-account", "U-b-me", "sk-member-key-1")
    fakes = {"auto-account": _AutoReplyFakeApi(), "b-account": _AutoReplyFakeApi()}
    web_state.get_api = lambda account_id, **kwargs: fakes[account_id]
    gate = threading.Event()
    lock = threading.Lock()
    running = {"a": 0, "b": 0}
    most = dict(running)
    started = []

    class _GateClient:
        def __init__(self, api_key):
            self.name = {"sk-test-key": "a", "sk-member-key-1": "b"}[api_key]

        def chat(self, **chat):
            with lock:
                running[self.name] += 1
                most[self.name] = max(most[self.name], running[self.name])
                started.append((self.name, chat["question"]))
            try:
                assert gate.wait(10)
                return {"answer": f"AI: {chat['question']}", "canAnswer": True,
                        "confidence": 0.9, "model": "test-model"}
            finally:
                with lock:
                    running[self.name] -= 1

    monkeypatch.setattr(
        webapp_module, "make_ai_client", lambda provider_id, *, api_key, **kw: _GateClient(api_key)
    )
    for account_id, owner in (("auto-account", owner_a), ("b-account", owner_b)):
        web_state.save_ai_auto_reply_settings(
            account_id, {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
        )
    a_chats = [f"U-a{index}" for index in range(6)]
    for account_id, chats in (("auto-account", a_chats), ("b-account", ["U-b0"])):
        enabled_at = web_state._ai_auto_reply_record(account_id)["enabledAtMs"]
        fakes[account_id].boxes = {
            mid: [{"id": f"{mid}-1", "from": mid, "text": f"hi from {mid}", "contentType": 0,
                   "createdTime": enabled_at + 1000}]
            for mid in chats
        }

    def poll():
        for account_id in fakes:
            web_state.invalidate_inbox_snapshot(account_id)
        web_state.poll_ai_auto_replies()

    try:
        poll()
        # B's reply starts at once on a free worker; A has two running and four waiting.
        assert _wait_for(lambda: ("b", "hi from U-b0") in started, 2.0)
        time.sleep(0.2)
        with lock:
            assert sorted(question for name, question in started if name == "a") == [
                "hi from U-a0", "hi from U-a1"
            ]
        assert web_state.ai_auto_reply_owner_jobs == {owner_a["id"]: 2, owner_b["id"]: 1}
        assert web_state.ai_auto_reply_backlog == {}  # LINE messages wait outside it
        ready = web_state.ai_auto_reply_line_ready[owner_a["id"]]["auto-account"]
        assert [entry["chatMid"] for entry in ready] == ["U-a2", "U-a3", "U-a4", "U-a5"]
        # Waiting LINE messages keep their chats' cursors and use no rate, poll after poll.
        for _ in range(3):
            poll()
        with lock:
            assert len(started) == 3 and most["a"] == 2
        last_seen = web_state._ai_auto_reply_record("auto-account")["lastSeen"]
        assert not set(last_seen) & set(a_chats)
        rated = {key[2] for key in web_state.ai_auto_reply_rate_events if key[0] == "chat"}
        assert rated == {"U-a0", "U-a1", "U-b0"}
    finally:
        gate.set()
    # Places that free up start the ready chats, and later polls the rest: each waiting
    # message is answered exactly once, two of A's at a time.
    assert _wait_for(lambda: poll() or len(fakes["auto-account"].sent) == 6, 15.0)
    for _ in range(3):
        poll()
        time.sleep(0.05)
    assert Counter(mid for mid, _text in fakes["auto-account"].sent) == dict.fromkeys(a_chats, 1)
    assert fakes["b-account"].sent == [("U-b0", "AI: hi from U-b0")]
    assert most == {"a": 2, "b": 1}
    assert _wait_for(lambda: not web_state.ai_auto_reply_owner_jobs)
    assert web_state.ai_auto_reply_inflight == set()
    assert web_state.ai_auto_reply_line_ready == {}


def _queue_telegram_reply(web_state, owner, index):
    """A Telegram auto reply of ``owner`` (no cursor), as _telegram_auto_reply queues it."""
    key = ("tg-account", f"tg-chat-{index}")
    with web_state.ai_auto_reply_lock:
        web_state.ai_auto_reply_inflight.add(key)
    assert web_state._queue_ai_auto_reply(
        {"accountId": key[0], "chatMid": key[1], "chatType": "contact",
         "question": f"tg {index}", "cursor": None, "ownerId": owner["id"]}
    )


def _line_auto_reply_with_recorder(web_state, chats=("U-contact",)):
    """One owner's LINE account with auto reply on and a message in each of ``chats``.

    Jobs are recorded, never run, so places stay taken until a test frees them.
    """
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    recorder = _RecordingExecutor()
    web_state.ai_auto_reply_executor = recorder
    owner = _auto_reply_owner(web_state)
    fake = _AutoReplyFakeApi()
    web_state.get_api = lambda account_id, **kwargs: fake
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
    )
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    fake.boxes = {
        mid: [{"id": f"{mid}-1", "from": mid, "text": f"hi from {mid}", "contentType": 0,
               "createdTime": enabled_at + 1000}]
        for mid in chats
    }

    def poll():
        web_state.invalidate_inbox_snapshot("auto-account")
        web_state.poll_ai_auto_replies()

    return owner, recorder, poll


def _submitted(recorder):
    return [job["question"] for job in recorder.submitted]


def _ready_chats(web_state, owner):
    accounts = web_state.ai_auto_reply_line_ready.get(owner["id"], {})
    return [entry["chatMid"] for entry in accounts.get("auto-account", [])]


def _passed_over(web_state):
    logs = web_state.store.get("bot_logs", {"logs": []})["logs"]
    return [log for log in logs if log["action"] == "ai.auto_reply.rate_limit"]


def test_waiting_line_messages_take_turns_with_waiting_telegram_replies(web_state):
    owner, recorder, poll = _line_auto_reply_with_recorder(
        web_state, chats=("U-c0", "U-c1")
    )
    # The owner's two places are taken and four Telegram replies wait.
    for index in range(6):
        _queue_telegram_reply(web_state, owner, index)
    assert _submitted(recorder) == ["tg 0", "tg 1"]
    # Both LINE messages wait outside the backlog, ready to start: cursors unchanged,
    # no rate used.
    poll()
    assert _ready_chats(web_state, owner) == ["U-c0", "U-c1"]
    assert [job["question"] for job in web_state.ai_auto_reply_backlog[owner["id"]]] == [
        "tg 2", "tg 3", "tg 4", "tg 5"
    ]
    assert not [key for key in web_state.ai_auto_reply_rate_events if key[0] == "chat"
                and key[1] == "auto-account"]
    assert not set(web_state._ai_auto_reply_record("auto-account")["lastSeen"]) & {
        "U-c0", "U-c1"
    }
    # Places that free up start LINE and Telegram replies in turn, at once (no poll in
    # between); a LINE reply uses the rate when it starts.
    for _ in range(4):
        web_state._free_ai_auto_reply_slot(owner["id"])
    assert _submitted(recorder) == [
        "tg 0", "tg 1", "hi from U-c0", "tg 2", "hi from U-c1", "tg 3"
    ]
    for chat in ("U-c0", "U-c1"):
        assert len(web_state.ai_auto_reply_rate_events[("chat", "auto-account", chat)]) == 1
    assert web_state.ai_auto_reply_line_ready == {}
    assert web_state.ai_auto_reply_owner_jobs == {owner["id"]: 2}
    # Nothing is ready on the LINE side any more, so the backlog has every place.
    web_state._free_ai_auto_reply_slot(owner["id"])
    web_state._free_ai_auto_reply_slot(owner["id"])
    assert _submitted(recorder)[-2:] == ["tg 4", "tg 5"]
    # With nothing waiting at all, a place that frees up is free.
    web_state._free_ai_auto_reply_slot(owner["id"])
    assert web_state.ai_auto_reply_owner_jobs == {owner["id"]: 1}
    assert web_state.ai_auto_reply_backlog == {} and web_state.ai_auto_reply_line_turn == {}


def test_replies_that_end_during_a_long_poll_cycle_start_the_next_at_once(
    web_state, monkeypatch
):
    # A poll cycle can take minutes (one LINE account whose requests hang holds up the
    # cycle for every workspace). Places that free up meanwhile go straight to the
    # ready LINE chats and the waiting Telegram replies; none waits for the next poll.
    owner, recorder, poll = _line_auto_reply_with_recorder(
        web_state, chats=("U-c0", "U-c1")
    )
    for index in range(8):
        _queue_telegram_reply(web_state, owner, index)  # two run, six wait
    poll()  # both LINE chats wait, ready
    seen = []

    def slow_refresh():
        for _ in range(4):  # four replies end while the cycle is held up
            web_state._free_ai_auto_reply_slot(owner["id"])
            seen.append((len(recorder.submitted), dict(web_state.ai_auto_reply_owner_jobs)))

    monkeypatch.setattr(web_state, "refresh_inbox_for_all_accounts", slow_refresh)
    poll()
    assert seen == [(count, {owner["id"]: 2}) for count in (3, 4, 5, 6)]
    assert _submitted(recorder)[2:] == ["hi from U-c0", "tg 2", "hi from U-c1", "tg 3"]


def test_a_line_burst_waits_without_using_the_reply_rate(web_state):
    # The default limits: 20 replies a minute per account, 2 places per owner.
    assert webapp_module.MAX_AI_AUTO_REPLIES_PER_ACCOUNT_PER_MINUTE == 20
    chats = tuple(f"U-c{index:02d}" for index in range(25))
    owner, recorder, poll = _line_auto_reply_with_recorder(web_state, chats=chats)

    def rated():
        return len(web_state.ai_auto_reply_rate_events.get(("account", "auto-account"), []))

    poll()
    # Two start; the other 23 wait, with no rate used and no cursor moved, and the
    # first eight of them are ready for the next places.
    assert len(recorder.submitted) == 2 and rated() == 2
    assert webapp_module.MAX_AI_AUTO_REPLY_READY_PER_ACCOUNT == 8
    assert _ready_chats(web_state, owner) == [f"U-c{index:02d}" for index in range(2, 10)]
    assert _passed_over(web_state) == []
    assert not set(web_state._ai_auto_reply_record("auto-account")["lastSeen"]) & set(chats)
    # As replies end, the waiting messages start one by one, each using the rate then.
    for _ in range(10):
        web_state._free_ai_auto_reply_slot(owner["id"])
        poll()
    assert len(recorder.submitted) == 12 and rated() == 12
    assert len({job["chatMid"] for job in recorder.submitted}) == 12
    assert _passed_over(web_state) == []
    # However fast replies end, the account answers 20 in a minute; the rest then wait
    # for the next minute instead of being passed over, and leave no place taken.
    while len(recorder.submitted) < 20:
        web_state._free_ai_auto_reply_slot(owner["id"])
        poll()
    web_state._free_ai_auto_reply_slot(owner["id"])
    poll()
    assert len(recorder.submitted) == 20 and rated() == 20
    assert _passed_over(web_state) == [] and web_state.ai_auto_reply_line_ready == {}
    assert web_state.ai_auto_reply_owner_jobs == {owner["id"]: 1}
    assert not set(web_state._ai_auto_reply_record("auto-account")["lastSeen"]) & set(chats)
    # A minute later they go on.
    with web_state.ai_auto_reply_lock:
        for key, stamps in web_state.ai_auto_reply_rate_events.items():
            web_state.ai_auto_reply_rate_events[key] = [stamp - 61.0 for stamp in stamps]
    poll()
    assert _submitted(recorder)[-1] == "hi from U-c20"
    assert _ready_chats(web_state, owner) == ["U-c21", "U-c22", "U-c23", "U-c24"]
    while len(recorder.submitted) < 25:
        web_state._free_ai_auto_reply_slot(owner["id"])
        poll()
    assert sorted(job["chatMid"] for job in recorder.submitted) == list(chats)
    assert _passed_over(web_state) == []


def test_ready_line_chats_go_stale_or_go_with_their_account(web_state):
    owner, recorder, poll = _line_auto_reply_with_recorder(web_state)
    for index in range(4):
        _queue_telegram_reply(web_state, owner, index)  # two run, "tg 2" and "tg 3" wait
    poll()  # the LINE message waits, ready
    assert _ready_chats(web_state, owner) == ["U-contact"]
    # Its poll's look at it is too old by the time a place frees up: the place goes to
    # the backlog, and the next poll looks at the chat again.
    with web_state.ai_auto_reply_lock:
        for entry in web_state.ai_auto_reply_line_ready[owner["id"]]["auto-account"]:
            entry["readyAt"] -= webapp_module.AI_AUTO_REPLY_READY_SECONDS + 1
    web_state._free_ai_auto_reply_slot(owner["id"])
    assert _submitted(recorder) == ["tg 0", "tg 1", "tg 2"]
    assert web_state.ai_auto_reply_line_ready == {}
    poll()
    assert _ready_chats(web_state, owner) == ["U-contact"]
    # Auto reply is switched off before the next place frees up: the next poll cycle
    # forgets the ready chat, and the place goes to the backlog.
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": False, "contactsEnabled": True, "groupsEnabled": False},
        owner,
    )
    poll()
    assert web_state.ai_auto_reply_line_ready == {}
    web_state._free_ai_auto_reply_slot(owner["id"])
    assert _submitted(recorder) == ["tg 0", "tg 1", "tg 2", "tg 3"]
    assert web_state.ai_auto_reply_owner_jobs == {owner["id"]: 2}


def test_a_ready_line_chat_answered_meanwhile_is_not_started_again(web_state, monkeypatch):
    owner, recorder, poll = _line_auto_reply_with_recorder(web_state, chats=("U-c0",))
    for index in range(2):
        _queue_telegram_reply(web_state, owner, index)
    poll()  # U-c0 waits, ready
    [entry] = web_state.ai_auto_reply_line_ready[owner["id"]]["auto-account"]
    # Its reply ran meanwhile and ended, cursor saved, after this entry's look.
    web_state._save_ai_auto_reply_cursors(
        "auto-account", entry["enabledAtMs"], {"U-c0": entry["cursor"]}
    )
    with web_state.ai_auto_reply_lock:
        web_state.ai_auto_reply_settle_count += 1
        web_state.ai_auto_reply_settled[("auto-account", "U-c0")] = (
            web_state.ai_auto_reply_settle_count
        )
    # A place frees up in the next cycle, after that cycle tidied the marks but before
    # it polled the account again: the mark must still rule the entry out.
    original = web_state._poll_ai_auto_reply_account

    def a_reply_ends_first(*args, **kwargs):
        web_state._free_ai_auto_reply_slot(owner["id"])
        return original(*args, **kwargs)

    monkeypatch.setattr(web_state, "_poll_ai_auto_reply_account", a_reply_ends_first)
    poll()
    assert _submitted(recorder) == ["tg 0", "tg 1"]  # U-c0 is not answered twice
    assert web_state.ai_auto_reply_owner_jobs == {owner["id"]: 1}
    assert web_state.ai_auto_reply_line_ready == {}


def test_a_reply_that_ends_during_a_poll_is_not_sent_again(web_state):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    owner = _auto_reply_owner(web_state)
    fake = _AutoReplyFakeApi()
    web_state.get_api = lambda account_id, **kwargs: fake
    web_state.ask_global_ai = lambda question, user, **metadata: {
        "ok": True, "question": {"answer": f"AI: {question}", "model": "test-model"}
    }
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
    )
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    fake.boxes["U-contact"].append(
        {"id": "m1", "from": "U-contact", "text": "hello", "contentType": 0,
         "createdTime": enabled_at + 1000}
    )
    # A cycle reads the cursors, then the reply for the chat ends and saves its cursor
    # before the cycle gets to that chat (here: a whole other poll in between).
    read_mark = web_state.ai_auto_reply_settle_count
    stale = web_state._ai_auto_reply_record("auto-account")
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()
    assert fake.sent == [("U-contact", "AI: hello")]
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state._poll_ai_auto_reply_account("auto-account", stale, read_mark=read_mark)
    assert fake.sent == [("U-contact", "AI: hello")]  # not a second time
    # The next cycles read the saved cursor; a new message is answered as usual.
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()
    fake.boxes["U-contact"].append(
        {"id": "m2", "from": "U-contact", "text": "still there?", "contentType": 0,
         "createdTime": enabled_at + 2000}
    )
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()
    assert fake.sent == [("U-contact", "AI: hello"), ("U-contact", "AI: still there?")]


def test_a_failed_line_poll_gives_back_what_its_replies_held(web_state, monkeypatch):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    owner = _auto_reply_owner(web_state)
    fake = _AutoReplyFakeApi()
    web_state.get_api = lambda account_id, **kwargs: fake
    web_state.ask_global_ai = lambda question, user, **metadata: {
        "ok": True, "question": {"answer": f"AI: {question}", "model": "test-model"}
    }
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
    )
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    fake.boxes["U-contact"].append(
        {"id": "m1", "from": "U-contact", "text": "hello", "contentType": 0,
         "createdTime": enabled_at + 1000}
    )
    for mid in ("U-x1", "U-x2"):  # three chats: more than the owner's two places
        fake.boxes[mid] = [{"id": f"{mid}-1", "from": mid, "text": f"hi from {mid}",
                            "contentType": 0, "createdTime": enabled_at + 1000}]
    original_save = web_state._save_ai_auto_reply_cursors

    def broken_save(*args, **kwargs):
        raise OSError("disk full")

    # The poll fails after it chose the chats, before the replies reached the workers.
    monkeypatch.setattr(web_state, "_save_ai_auto_reply_cursors", broken_save)
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()
    assert fake.sent == []
    assert web_state.ai_auto_reply_runtime["auto-account"]["state"] == "error"
    assert web_state.ai_auto_reply_owner_jobs == {}
    assert web_state.ai_auto_reply_inflight == set()
    # It leaves no chat ready either (the next poll is the regular one, 5 s later).
    assert web_state.ai_auto_reply_line_ready == {}
    # The rate it used is given back too.
    assert not web_state.ai_auto_reply_rate_events.get(("chat", "auto-account", "U-contact"))
    assert not web_state.ai_auto_reply_rate_events.get(("account", "auto-account"))
    # Nothing was lost: the next polls answer each message, once.
    monkeypatch.setattr(web_state, "_save_ai_auto_reply_cursors", original_save)
    for _ in range(3):
        web_state.invalidate_inbox_snapshot("auto-account")
        web_state.poll_ai_auto_replies()
    assert Counter(mid for mid, _text in fake.sent) == {"U-contact": 1, "U-x1": 1, "U-x2": 1}


def test_a_reply_whose_cursor_cannot_be_saved_is_not_sent_again(web_state, monkeypatch):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    owner = _auto_reply_owner(web_state)
    fake = _AutoReplyFakeApi()
    web_state.get_api = lambda account_id, **kwargs: fake
    web_state.ask_global_ai = lambda question, user, **metadata: {
        "ok": True, "question": {"answer": f"AI: {question}", "model": "test-model"}
    }
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
    )
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    fake.boxes["U-contact"].append(
        {"id": "m1", "from": "U-contact", "text": "hello", "contentType": 0,
         "createdTime": enabled_at + 1000}
    )
    original_set = web_state.store.set

    def no_room(key, value):  # a full disk or a read-only database, for this key
        if key == "ai_auto_reply":
            raise OSError(28, "No space left on device")
        return original_set(key, value)

    def poll():
        web_state.invalidate_inbox_snapshot("auto-account")
        web_state.poll_ai_auto_replies()

    # The reply is sent; saving its chat's cursor then fails.
    monkeypatch.setattr(web_state.store, "set", no_room)
    poll()
    assert fake.sent == [("U-contact", "AI: hello")]
    for _ in range(3):
        poll()
    assert fake.sent == [("U-contact", "AI: hello")]  # never sent again
    # Saving works again: the next poll stores that cursor, and still sends nothing.
    monkeypatch.setattr(web_state.store, "set", original_set)
    poll()
    assert fake.sent == [("U-contact", "AI: hello")]
    assert "U-contact" in web_state._ai_auto_reply_record("auto-account")["lastSeen"]
    assert web_state.ai_auto_reply_unsaved_cursors == {}
    # A new message is answered as usual.
    fake.boxes["U-contact"].append(
        {"id": "m2", "from": "U-contact", "text": "still there?", "contentType": 0,
         "createdTime": enabled_at + 2000}
    )
    poll()
    assert fake.sent == [("U-contact", "AI: hello"), ("U-contact", "AI: still there?")]


def _seal_messages(fake):
    """Make every message of ``fake`` sealed (Letter Sealing); returns the chats read by
    the polls of this thread."""
    from types import SimpleNamespace

    reads = []
    poller = threading.current_thread()  # the CRM reads them too, on its own thread

    def decrypt_message(message):
        if threading.current_thread() is poller:
            reads.append(message["from"])
        return {**message, "text": f"hi from {message['from']}"}

    fake.e2ee = SimpleNamespace(is_ready=lambda: True)
    fake.decrypt_message = decrypt_message
    for messages in fake.boxes.values():
        for message in messages:
            message["chunks"] = ["sealed"]
            message.pop("text", None)
    return reads


def test_waiting_sealed_line_chats_are_read_only_when_they_could_start(web_state):
    # Reading a sealed message can ask LINE for the sender's key, with the account's api
    # lock held: a poll reads only the waiting chats that could start or be left ready,
    # and none while the account has used its replies of the minute.
    chats = tuple(f"U-c{index:02d}" for index in range(30))
    owner, recorder, poll = _line_auto_reply_with_recorder(web_state, chats=chats)
    reads = _seal_messages(web_state.get_api("auto-account"))
    for index in range(2):
        _queue_telegram_reply(web_state, owner, index)  # both places taken
    limit = webapp_module.MAX_AI_AUTO_REPLY_READY_PER_ACCOUNT
    poll()
    assert reads == list(chats[:limit])
    assert _ready_chats(web_state, owner) == list(chats[:limit])
    poll()
    assert reads == list(chats[:limit]) * 2  # those again, never the other 22
    # A place frees up: one ready chat starts, and the next poll reads one more chat.
    web_state._free_ai_auto_reply_slot(owner["id"])
    assert _submitted(recorder) == ["tg 0", "tg 1", "hi from U-c00"]
    reads.clear()
    poll()
    assert reads == list(chats[1:limit + 1])
    # The account's minute is used up: nothing is read, and nothing is left ready.
    with web_state.ai_auto_reply_lock:
        web_state.ai_auto_reply_rate_events[("account", "auto-account")] = [
            time.monotonic()
        ] * webapp_module.MAX_AI_AUTO_REPLIES_PER_ACCOUNT_PER_MINUTE
    reads.clear()
    poll()
    assert reads == [] and web_state.ai_auto_reply_line_ready == {}


def test_a_ready_line_chat_waits_while_its_account_is_polled_again(web_state, monkeypatch):
    owner, recorder, poll = _line_auto_reply_with_recorder(
        web_state, chats=("U-c0", "U-c1")
    )
    for index in range(2):
        _queue_telegram_reply(web_state, owner, index)  # both places taken
    poll()
    assert _ready_chats(web_state, owner) == ["U-c0", "U-c1"]
    # A reply ends while the next poll reads the chats. The place does not start a
    # chat that poll is choosing again (it would start twice): the poll takes it.
    original_boxes = web_state.inbox_message_boxes
    seen = []

    def a_reply_ends_while_reading(*args, **kwargs):
        if not seen:
            web_state._free_ai_auto_reply_slot(owner["id"])
            seen.append(_submitted(recorder))
        return original_boxes(*args, **kwargs)

    monkeypatch.setattr(web_state, "inbox_message_boxes", a_reply_ends_while_reading)
    poll()
    assert seen == [["tg 0", "tg 1"]]
    assert _submitted(recorder) == ["tg 0", "tg 1", "hi from U-c0"]
    assert _ready_chats(web_state, owner) == ["U-c1"]
    assert web_state.ai_auto_reply_owner_jobs == {owner["id"]: 2}
    # A reply ends after the poll chose, before it left U-c1 ready again: that free
    # place starts U-c1 as soon as the poll is done.
    original_names = web_state._hydrate_ai_auto_reply_candidate_identities

    def a_reply_ends_after_choosing(*args, **kwargs):
        web_state._free_ai_auto_reply_slot(owner["id"])
        return original_names(*args, **kwargs)

    monkeypatch.setattr(
        web_state, "_hydrate_ai_auto_reply_candidate_identities", a_reply_ends_after_choosing
    )
    poll()
    assert _submitted(recorder) == ["tg 0", "tg 1", "hi from U-c0", "hi from U-c1"]
    assert Counter(job["chatMid"] for job in recorder.submitted[2:]) == {"U-c0": 1, "U-c1": 1}
    assert web_state.ai_auto_reply_owner_jobs == {owner["id"]: 2}
    assert web_state.ai_auto_reply_line_ready == {}


def test_a_ready_line_chat_keeps_its_turn_through_a_long_poll_cycle(web_state):
    owner, recorder, poll = _line_auto_reply_with_recorder(web_state)
    for index in range(3):
        _queue_telegram_reply(web_state, owner, index)  # two run, "tg 2" waits
    poll()
    assert _ready_chats(web_state, owner) == ["U-contact"]
    # Twenty seconds on (a cycle held up by a LINE account whose requests hang), the
    # next place still goes to the LINE chat on LINE's turn.
    with web_state.ai_auto_reply_lock:
        for entry in web_state.ai_auto_reply_line_ready[owner["id"]]["auto-account"]:
            entry["readyAt"] -= 20.0
    assert webapp_module.AI_AUTO_REPLY_READY_SECONDS == 60.0
    web_state._free_ai_auto_reply_slot(owner["id"])
    assert _submitted(recorder) == ["tg 0", "tg 1", "hi from U-contact"]


def test_ready_line_chats_take_turns_between_an_owner_s_accounts(web_state):
    owner, recorder, poll = _line_auto_reply_with_recorder(
        web_state, chats=("U-a0", "U-a1", "U-a2")
    )
    first = web_state.get_api("auto-account")
    second = _AutoReplyFakeApi()
    web_state.account_store.data["accounts"].append(
        {"id": "second-line", "label": "Second LINE", "mid": "U-me-2", "tokenFile": "",
         "createdAt": "2026-07-11T12:00:00", "updatedAt": "2026-07-11T12:00:00"}
    )
    web_state.account_store.save()
    web_state.web_auth.grant_account_access(owner["id"], "second-line")
    owner = web_state.web_auth._find_user(owner["id"])
    apis = {"auto-account": first, "second-line": second}
    web_state.get_api = lambda account_id, **kwargs: apis[account_id]
    web_state.save_ai_auto_reply_settings(
        "second-line", {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
    )
    enabled_at = web_state._ai_auto_reply_record("second-line")["enabledAtMs"]
    second.boxes = {
        mid: [{"id": f"{mid}-1", "from": mid, "text": f"hi from {mid}", "contentType": 0,
               "createdTime": enabled_at + 1000}]
        for mid in ("U-b0", "U-b1", "U-b2")
    }
    for index in range(2):
        _queue_telegram_reply(web_state, owner, index)  # both places taken
    web_state.invalidate_inbox_snapshot("second-line")
    poll()
    # Places that free up go to the owner's two LINE accounts in turn: the one polled
    # first does not take them all.
    for _ in range(4):
        web_state._free_ai_auto_reply_slot(owner["id"])
    started = [job["accountId"] for job in recorder.submitted[2:]]
    assert started in (
        ["auto-account", "second-line"] * 2,
        ["second-line", "auto-account"] * 2,
    )


def test_a_saved_line_cursor_never_moves_back(web_state):
    _line_auto_reply_with_recorder(web_state)
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]

    def saved():
        return web_state._ai_auto_reply_record("auto-account")["lastSeen"]["U-contact"]

    newer = {"time": enabled_at + 2000, "ids": ["m2"]}
    web_state._save_ai_auto_reply_cursors("auto-account", enabled_at, {"U-contact": newer})
    # A reply chosen from an earlier look at the chat ends after a later look moved on.
    web_state._save_ai_auto_reply_cursors(
        "auto-account", enabled_at, {"U-contact": {"time": enabled_at + 1000, "ids": ["m1"]}}
    )
    assert saved() == newer
    # Another message of the same millisecond: the cursor holds both.
    web_state._save_ai_auto_reply_cursors(
        "auto-account", enabled_at, {"U-contact": {"time": enabled_at + 2000, "ids": ["m3"]}}
    )
    assert saved() == {"time": enabled_at + 2000, "ids": ["m2", "m3"]}


def _line_auto_reply_running(web_state):
    """One owner's LINE account with auto reply on, its replies run at once and the AI
    answering "AI: <question>"; returns the fake LINE and a poll."""
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    owner = _auto_reply_owner(web_state)
    fake = _AutoReplyFakeApi()
    web_state.get_api = lambda account_id, **kwargs: fake
    web_state.ask_global_ai = lambda question, user, **metadata: {
        "ok": True, "question": {"answer": f"AI: {question}", "model": "test-model"}
    }
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
    )
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    fake.boxes["U-contact"].append(
        {"id": "m1", "from": "U-contact", "text": "hello", "contentType": 0,
         "createdTime": enabled_at + 1000}
    )

    def poll():
        web_state.invalidate_inbox_snapshot("auto-account")
        web_state.poll_ai_auto_replies()

    return fake, poll


def test_a_cursor_stored_late_holds_back_a_cycle_that_read_before(web_state, monkeypatch):
    fake, poll = _line_auto_reply_running(web_state)
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    original_set = web_state.store.set

    def no_room(key, value):
        if key == "ai_auto_reply":
            raise OSError(28, "No space left on device")
        return original_set(key, value)

    monkeypatch.setattr(web_state.store, "set", no_room)
    poll()  # sent; its cursor is kept until a save stores it
    assert fake.sent == [("U-contact", "AI: hello")]
    monkeypatch.setattr(web_state.store, "set", original_set)
    # A cycle reads the cursors; then another reply's save of the account stores the
    # kept cursor, before the cycle gets to the chat.
    read_mark = web_state.ai_auto_reply_settle_count
    stale = web_state._ai_auto_reply_record("auto-account")
    assert "U-contact" not in stale["lastSeen"]
    web_state._save_ai_auto_reply_cursors(
        "auto-account", enabled_at, {"U-other": {"time": enabled_at + 1500, "ids": ["o1"]}}
    )
    assert web_state.ai_auto_reply_unsaved_cursors == {}
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state._poll_ai_auto_reply_account("auto-account", stale, read_mark=read_mark)
    assert fake.sent == [("U-contact", "AI: hello")]  # not a second time


def test_a_reply_that_failed_before_sending_is_answered_once_the_store_is_back(
    web_state, monkeypatch
):
    fake, poll = _line_auto_reply_running(web_state)
    original_set = web_state.store.set
    original_enabled = web_state._ai_auto_reply_enabled
    down = {"now": False}

    def flaky_set(key, value):
        if down["now"] and key == "ai_auto_reply":
            raise OSError("could not connect to the database")
        return original_set(key, value)

    def database_goes_down(*args, **kwargs):
        down["now"] = True
        raise OSError("could not connect to the database")

    # The database goes away while the reply runs: nothing is sent, and its cursor
    # cannot be saved either. No cursor is kept for it, so it is not lost.
    monkeypatch.setattr(web_state.store, "set", flaky_set)
    monkeypatch.setattr(web_state, "_ai_auto_reply_enabled", database_goes_down)
    poll()
    assert fake.sent == [] and web_state.ai_auto_reply_unsaved_cursors == {}
    assert web_state.ai_auto_reply_runtime["auto-account"]["state"] == "error"
    down["now"] = False
    monkeypatch.setattr(web_state, "_ai_auto_reply_enabled", original_enabled)
    poll()
    assert fake.sent == [("U-contact", "AI: hello")]
    poll()
    assert fake.sent == [("U-contact", "AI: hello")]


def test_an_auto_reply_is_sent_when_its_question_cannot_be_logged(web_state, monkeypatch):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    owner = _auto_reply_owner(web_state)
    fake = _AutoReplyFakeApi()
    web_state.get_api = lambda account_id, **kwargs: fake

    class _AnsweringClient:
        def embed(self, model, texts, timeout=None):
            return [[1.0] for _ in texts]

        def chat(self, **chat):
            return {"answer": f"AI: {chat['question']}", "canAnswer": True,
                    "confidence": 0.9, "model": "test-model"}

        def list_models(self, timeout=None):
            return []

    monkeypatch.setattr(webapp_module, "make_ai_client", lambda *args, **kwargs: _AnsweringClient())
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
    )
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    fake.boxes["U-contact"].append(
        {"id": "m1", "from": "U-contact", "text": "hello", "contentType": 0,
         "createdTime": enabled_at + 1000}
    )
    original_set = web_state.store.set

    def global_ai_full(key, value):  # the question log cannot be written
        if key == "global_ai":
            raise OSError(28, "No space left on device")
        return original_set(key, value)

    monkeypatch.setattr(web_state.store, "set", global_ai_full)
    web_state.invalidate_inbox_snapshot("auto-account")
    web_state.poll_ai_auto_replies()
    assert fake.sent == [("U-contact", "AI: hello")]
    assert web_state._global_ai_all()["questions"] == []
    # Ask AI gets its answer too, and is told the question history could not keep it.
    result = web_state.ask_global_ai("opening hours?", owner)
    assert result["question"]["answer"] == "AI: opening hours?"
    assert result["logged"] is False
    monkeypatch.setattr(web_state.store, "set", original_set)
    assert web_state.ask_global_ai("opening hours?", owner)["logged"] is True


def test_ask_ai_shows_an_answer_the_question_history_could_not_keep():
    # Try a conversation (the AI chat on the Knowledge page) shows the answer from the
    # reply and notes that the question could not be saved; an unanswered one is not
    # said to be in the list of questions the AI could not answer.
    assert 'if (data.logged === false) toast(t("assistant.not_logged"), true);' in INDEX_HTML
    assert 'textSpan(t("kb.no_info_unlogged"), "kb-source")' in INDEX_HTML
    assert '"assistant.not_logged": {th: "' in INDEX_HTML
    assert '"kb.no_info_unlogged": {th: "' in INDEX_HTML
    # The old Ask AI chat view (no longer shown) adds such an answer to its history.
    assert "await loadAssistant(unlogged);" in INDEX_HTML


def test_ask_global_ai_goes_by_the_settings_it_is_given(web_state, monkeypatch):
    owner = _auto_reply_owner(web_state)

    class _EchoClient:
        def embed(self, model, texts, timeout=None):
            return [[1.0] for _ in texts]

        def chat(self, **chat):
            return {"answer": f"AI: {chat['question']}", "canAnswer": True,
                    "confidence": 0.9, "model": "test-model"}

    monkeypatch.setattr(webapp_module, "make_ai_client", lambda *args, **kwargs: _EchoClient())
    data = web_state._global_ai_all()
    settings = web_state._assistant_settings_for(data, owner)

    def read_again(*args):
        raise AssertionError("the settings were read again")

    monkeypatch.setattr(web_state, "_assistant_settings_for", read_again)
    result = web_state.ask_global_ai("hello", owner, data=data, settings=settings)
    assert result["question"]["answer"] == "AI: hello"


def test_saving_auto_reply_settings_keeps_a_cursor_saved_meanwhile(web_state, monkeypatch):
    owner, _recorder, _poll = _line_auto_reply_with_recorder(web_state)
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    cursor = {"time": enabled_at + 1000, "ids": ["U-contact-1"]}
    original_global_ai = web_state._global_ai_all

    def a_reply_ends_meanwhile():  # while the save checks the AI settings
        web_state._save_ai_auto_reply_cursors("auto-account", enabled_at, {"U-contact": cursor})
        return original_global_ai()

    monkeypatch.setattr(web_state, "_global_ai_all", a_reply_ends_meanwhile)
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": True, "contactsEnabled": True, "groupsEnabled": True}, owner
    )
    record = web_state._ai_auto_reply_record("auto-account")
    assert record["lastSeen"]["U-contact"] == cursor
    assert record["groupsEnabled"] is True and record["enabledAtMs"] == enabled_at


def test_kept_line_cursors_go_with_their_run_and_their_account(web_state):
    owner, _recorder, _poll = _line_auto_reply_with_recorder(web_state)
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    kept = (enabled_at, {"time": enabled_at + 1000, "ids": ["m1"]})
    web_state.ai_auto_reply_unsaved_cursors[("auto-account", "U-x")] = kept
    # A settings change that leaves auto reply on keeps it.
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": True, "contactsEnabled": True, "groupsEnabled": True}, owner
    )
    assert web_state.ai_auto_reply_unsaved_cursors == {("auto-account", "U-x"): kept}
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": False, "contactsEnabled": True, "groupsEnabled": True}, owner
    )
    assert web_state.ai_auto_reply_unsaved_cursors == {}
    web_state.ai_auto_reply_unsaved_cursors[("auto-account", "U-x")] = kept
    web_state._commit_account_removal("auto-account")
    assert web_state.ai_auto_reply_unsaved_cursors == {}


def test_waiting_line_chats_are_answered_oldest_first(web_state):
    # LINE lists the newest chat first. A customer who wrote once is not kept waiting
    # by chats that came in after: the free places, the ready list and the reads all
    # go by when each chat began to wait.
    chats = ("U-c3", "U-c2", "U-c1", "U-c0")  # newest first, as LINE lists them
    owner, recorder, poll = _line_auto_reply_with_recorder(web_state, chats=chats)
    fake = web_state.get_api("auto-account")
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    for index, mid in enumerate(reversed(chats)):
        fake.boxes[mid][0]["createdTime"] = enabled_at + 1000 * (index + 1)
    # U-c3 wrote last, but began to wait first: its earlier message is unanswered too.
    fake.boxes["U-c3"].insert(
        0, {"id": "U-c3-0", "from": "U-c3", "text": "hello?", "contentType": 0,
            "createdTime": enabled_at + 500}
    )
    _queue_telegram_reply(web_state, owner, 0)  # one place left
    poll()
    assert [job["chatMid"] for job in recorder.submitted[1:]] == ["U-c3"]
    assert _ready_chats(web_state, owner) == ["U-c0", "U-c1", "U-c2"]
    web_state._free_ai_auto_reply_slot(owner["id"])
    web_state._free_ai_auto_reply_slot(owner["id"])
    assert [job["chatMid"] for job in recorder.submitted[1:]] == ["U-c3", "U-c0", "U-c1"]


def test_a_line_account_removed_while_its_poll_runs_leaves_nothing_behind(
    web_state, monkeypatch
):
    owner, recorder, poll = _line_auto_reply_with_recorder(
        web_state, chats=("U-c0", "U-c1", "U-c2")
    )
    for index in range(2):
        _queue_telegram_reply(web_state, owner, index)  # both places taken
    # Removed after the poll chose (a removal waits for the account's api lock, which
    # the choosing holds) and before the poll left its chats ready.
    original_save = web_state._save_ai_auto_reply_cursors

    def removed_meanwhile(account_id, enabled_at_ms, cursors):
        web_state._commit_account_removal("auto-account")
        return original_save(account_id, enabled_at_ms, cursors)

    monkeypatch.setattr(web_state, "_save_ai_auto_reply_cursors", removed_meanwhile)
    poll()
    assert web_state.account_store.get("auto-account") is None
    assert web_state.ai_auto_reply_line_ready == {}
    assert web_state.ai_auto_reply_line_polling == set()
    assert "auto-account" not in web_state.ai_auto_reply_runtime
    # A place that frees up starts nothing of it.
    web_state._free_ai_auto_reply_slot(owner["id"])
    assert _submitted(recorder) == ["tg 0", "tg 1"]
    # A ready chat of a removed account opens no LINE session to name it.
    opened = []
    web_state.get_api = lambda account_id, **kwargs: opened.append(account_id)
    web_state._name_ready_ai_auto_reply({"accountId": "auto-account", "chatMid": "U-c0"})
    assert opened == []


def test_deleting_a_workspace_owner_forgets_its_auto_reply_state(web_state):
    web_state.ai_auto_reply_engine.close()  # this test sets the state by hand
    owner = _auto_reply_owner(web_state)
    god = web_state.web_auth.ensure_god("god", "god-password")
    member = _second_line_owner(web_state, "b-account", "U-b-me", "sk-member-key-1")
    keep = ("auto-account", "U-x")  # another workspace's: stays
    gone = ("b-account", "U-y")
    now = time.monotonic()
    with web_state.ai_auto_reply_lock:
        for key, owner_id in ((keep, owner["id"]), (gone, member["id"])):
            web_state.ai_auto_reply_unsaved_cursors[key] = (1, {"time": 1, "ids": ["m1"]})
            web_state.ai_auto_reply_held[key] = 1
            web_state.ai_auto_reply_inflight.add(key)
            web_state.ai_auto_reply_rate_events[("account", key[0])] = [now]
            web_state.ai_auto_reply_rate_events[("chat", *key)] = [now]
            web_state.ai_auto_reply_runtime[key[0]] = {"state": "running"}
            web_state.ai_auto_reply_waiting[key] = (1, 1, 1)
            web_state.ai_auto_reply_idle_seen[key[0]] = {key[1]: (1, 1)}
            web_state.ai_auto_reply_looked[key[0]] = (1, 1, frozenset())
            web_state.ai_auto_reply_identity_cache[key] = (now, "Customer")
            web_state.ai_auto_reply_line_ready[owner_id] = {
                key[0]: [{"accountId": key[0], "chatMid": key[1], "readyAt": now,
                          "readMark": 0, "ownerId": owner_id}]
            }
        web_state.ai_auto_reply_backlog[member["id"]] = deque(
            [{"accountId": "b-account", "chatMid": "U-z", "ownerId": member["id"]}]
        )
        web_state.ai_auto_reply_line_turn[member["id"]] = False
        web_state.ai_auto_reply_line_polling.add("b-account")
    web_state.delete_user({"id": member["id"]}, god)
    assert web_state.account_store.get("b-account") is None
    assert set(web_state.ai_auto_reply_unsaved_cursors) == {keep}
    assert set(web_state.ai_auto_reply_held) == {keep}
    assert web_state.ai_auto_reply_inflight == {keep}
    assert {key[1] for key in web_state.ai_auto_reply_rate_events} == {"auto-account"}
    assert set(web_state.ai_auto_reply_runtime) == {"auto-account"}
    assert set(web_state.ai_auto_reply_waiting) == {keep}
    assert set(web_state.ai_auto_reply_idle_seen) == {"auto-account"}
    assert set(web_state.ai_auto_reply_looked) == {"auto-account"}
    assert set(web_state.ai_auto_reply_identity_cache) == {keep}
    assert list(web_state.ai_auto_reply_line_ready) == [owner["id"]]
    assert web_state.ai_auto_reply_backlog == {} and web_state.ai_auto_reply_line_turn == {}
    assert web_state.ai_auto_reply_line_polling == set()


def test_a_line_reply_that_failed_while_saving_fails_waits_for_the_store(
    web_state, monkeypatch
):
    # Two failures at once: LINE refuses the reply, and its chat's cursor cannot be
    # saved. The message is neither lost nor tried again (the AI asked again) at
    # every poll: it waits until saving works, and is answered then.
    fake, poll = _line_auto_reply_running(web_state)
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    asked = []
    original_ask = web_state.ask_global_ai

    def ask(question, user, **metadata):
        asked.append(question)
        return original_ask(question, user, **metadata)

    web_state.ask_global_ai = ask
    original_set = web_state.store.set
    original_send = fake.send_text

    def no_room(key, value):
        if key == "ai_auto_reply":
            raise OSError(28, "No space left on device")
        return original_set(key, value)

    def refused(mid, text):
        raise RuntimeError("LINE refused the message")

    monkeypatch.setattr(web_state.store, "set", no_room)
    monkeypatch.setattr(fake, "send_text", refused)
    poll()
    assert asked == ["hello"] and fake.sent == []
    assert web_state.ai_auto_reply_held == {("auto-account", "U-contact"): enabled_at}
    assert web_state.ai_auto_reply_unsaved_cursors == {}
    for _ in range(5):
        poll()
    assert asked == ["hello"]  # not asked again while saving fails
    assert web_state.ai_auto_reply_runtime["auto-account"]["state"] == "error"
    monkeypatch.setattr(web_state.store, "set", original_set)
    monkeypatch.setattr(fake, "send_text", original_send)
    poll()
    assert asked == ["hello", "hello"] and fake.sent == [("U-contact", "AI: hello")]
    assert web_state.ai_auto_reply_held == {}
    assert "U-contact" in web_state._ai_auto_reply_record("auto-account")["lastSeen"]
    poll()
    assert fake.sent == [("U-contact", "AI: hello")]


@pytest.mark.parametrize("failing_read", [1, 2], ids=["before-the-ai", "after-the-ai"])
def test_a_line_message_is_not_passed_over_for_a_read_that_failed(
    web_state, monkeypatch, failing_read
):
    # One read of LinePassport's own data fails inside the reply (a refused database
    # connection, say) while saving works: that says nothing about the message, which
    # waits for the next poll instead of being passed over.
    fake, poll = _line_auto_reply_running(web_state)
    original_enabled = web_state._ai_auto_reply_enabled
    calls = []

    def one_read_fails(*args):
        calls.append(args)
        if len(calls) == failing_read:
            raise OSError("could not connect to the database")
        return original_enabled(*args)

    monkeypatch.setattr(web_state, "_ai_auto_reply_enabled", one_read_fails)
    poll()
    assert fake.sent == []
    assert "U-contact" not in web_state._ai_auto_reply_record("auto-account")["lastSeen"]
    poll()
    assert fake.sent == [("U-contact", "AI: hello")]
    assert web_state.ai_auto_reply_held == {}
    poll()
    assert fake.sent == [("U-contact", "AI: hello")]


def test_a_line_chat_that_keeps_writing_keeps_its_place_in_line(web_state):
    # LINE shows a chat's last 10 messages only. A chat that keeps receiving messages
    # (an active group, a customer who keeps writing) still goes by when it began to
    # wait, not by the oldest message LINE still shows.
    owner, _recorder, poll = _line_auto_reply_with_recorder(web_state, chats=("U-busy",))
    assert webapp_module.INBOX_SNAPSHOT_MESSAGES_PER_BOX == 10
    fake = web_state.get_api("auto-account")
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    start = enabled_at + 1000
    for index in range(2):
        _queue_telegram_reply(web_state, owner, index)  # both places taken

    def busy(count):
        return [{"id": f"busy-{n}", "from": "U-busy", "text": f"busy {n}", "contentType": 0,
                 "createdTime": start + 1000 * n} for n in range(count)]

    def once(index, at):
        return [{"id": f"once-{index}", "from": f"U-once-{index}", "text": "hi",
                 "contentType": 0, "createdTime": at}]

    # Seen first with 3 messages: it began to wait at its first one.
    fake.boxes = {"U-busy": busy(3)}
    poll()
    assert web_state.ai_auto_reply_waiting[("auto-account", "U-busy")][2] == start
    # 9 more messages, and 4 customers who each wrote once after it began: LINE lists
    # the busy chat first and shows only its last 10 messages. It keeps its place.
    fake.boxes = {"U-busy": busy(12), **{f"U-once-{i}": once(i, start + 2500) for i in range(4)}}
    poll()
    assert _ready_chats(web_state, owner) == ["U-busy"] + [f"U-once-{i}" for i in range(4)]
    assert web_state.ai_auto_reply_waiting[("auto-account", "U-busy")][2] == start
    # Never seen before with its start out of sight (after a restart, say): it goes by
    # the earliest it can have begun, never later than it did.
    web_state.ai_auto_reply_waiting.clear()
    web_state.ai_auto_reply_idle_seen.clear()
    web_state.ai_auto_reply_looked.clear()
    poll()
    assert _ready_chats(web_state, owner)[0] == "U-busy"
    assert web_state.ai_auto_reply_waiting[("auto-account", "U-busy")][2] == enabled_at
    # Once the busy chat waits no more, what was kept for it goes.
    fake.boxes = {f"U-once-{i}": once(i, start + 2500) for i in range(4)}
    poll()
    assert ("auto-account", "U-busy") not in web_state.ai_auto_reply_waiting


def test_a_poll_that_waited_for_a_removed_account_s_lock_does_nothing(web_state):
    _owner, recorder, _poll = _line_auto_reply_with_recorder(
        web_state, chats=("U-c0", "U-c1")
    )
    fake = web_state.get_api("auto-account")
    record = web_state._ai_auto_reply_record("auto-account")
    web_state.invalidate_inbox_snapshot("auto-account")
    reads_before = fake.message_box_calls
    errors = []

    def run_poll():
        try:
            web_state._poll_ai_auto_reply_account("auto-account", record)
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)

    lock = web_state.api_lock_for("auto-account")
    lock.acquire()  # a removal holds the account's lock while it removes it
    thread = threading.Thread(target=run_poll)
    try:
        thread.start()
        assert _wait_for(lambda: "auto-account" in web_state.ai_auto_reply_line_polling)
        time.sleep(0.05)  # the poll now waits for the account's lock
        web_state._commit_account_removal("auto-account")
    finally:
        lock.release()
    thread.join(timeout=10)
    assert not thread.is_alive() and errors == []
    assert fake.message_box_calls == reads_before  # nothing read for it
    assert recorder.submitted == [] and web_state.ai_auto_reply_line_ready == {}
    assert "auto-account" not in web_state.ai_auto_reply_runtime
    assert "auto-account" not in web_state.inbox_snapshots
    # An error about it afterwards shows nothing either.
    web_state._ai_auto_reply_poll_error("auto-account", RuntimeError("late"))
    assert "auto-account" not in web_state.ai_auto_reply_runtime


def test_no_line_client_is_kept_for_a_removed_account(web_state):
    from types import SimpleNamespace

    _seed_account(web_state, "gone-account")
    made = []

    class _Client:
        def __init__(self):
            self.tokens = SimpleNamespace(access_token="token")
            self.closed = False

        def close(self):
            self.closed = True

    def new_api(account_id=None):
        client = _Client()
        made.append(client)
        return client

    web_state._new_api = new_api
    first = web_state.get_api("gone-account")
    assert web_state.apis["gone-account"] is first
    # A request opens a client for it while it is being removed: after the removal
    # closed the first one, before the account is gone.
    original_close = web_state.close_api
    closes = []

    def a_request_opens_one_meanwhile(account_id):
        original_close(account_id)
        closes.append(account_id)
        if len(closes) == 1:
            web_state.get_api(account_id)

    web_state.close_api = a_request_opens_one_meanwhile
    web_state.delete_account("gone-account")
    assert len(made) == 2 and all(client.closed for client in made)
    assert "gone-account" not in web_state.apis
    # Asked for once it is gone, a client is made but not kept.
    web_state.get_api("gone-account")
    assert len(made) == 3 and "gone-account" not in web_state.apis


def test_a_failed_read_of_the_ai_settings_does_not_pass_a_line_message_over(
    web_state, monkeypatch
):
    # A workspace on Gemini with the picture AI's Google key reads that key while its
    # reply is prepared: a failed read there waits for the next poll too.
    fake, poll = _line_auto_reply_running(web_state)
    original_settings = web_state._assistant_settings_for
    original_process = web_state._process_ai_auto_reply
    state = {"in_job": False, "failed": 0}

    def process(candidate):
        state["in_job"] = True
        try:
            return original_process(candidate)
        finally:
            state["in_job"] = False

    def settings_for(data, user):
        if state["in_job"] and not state["failed"]:
            state["failed"] += 1
            raise OSError("could not connect to the database")
        return original_settings(data, user)

    monkeypatch.setattr(web_state, "_process_ai_auto_reply", process)
    monkeypatch.setattr(web_state, "_assistant_settings_for", settings_for)
    poll()
    assert state["failed"] == 1 and fake.sent == []
    assert "U-contact" not in web_state._ai_auto_reply_record("auto-account")["lastSeen"]
    poll()
    assert fake.sent == [("U-contact", "AI: hello")]


def test_a_sign_in_set_up_that_could_not_be_saved_is_set_up_again(tmp_path):
    for kind in ("simple", "email"):
        path = str(tmp_path / f"auth-{kind}.json")
        auth = WebAuth(path)
        original_save = auth._save
        failures = {"left": 1}

        def save(original=original_save, failures=failures):
            if failures["left"]:
                failures["left"] -= 1
                raise OSError(28, "No space left on device")
            return original()

        auth._save = save

        def set_up(auth=auth, kind=kind):
            if kind == "simple":
                return auth.ensure_simple_mode()
            return auth.setup("owner@example.com", "owner-pass")

        with pytest.raises(OSError):
            set_up()
        # Not kept in memory either: nobody could sign in as it, and the next page
        # load sets it up again.
        assert not auth.configured()
        assert set_up()
        assert auth.configured() and WebAuth(path).configured()


def test_held_line_chats_go_with_their_run_and_their_account(web_state):
    owner, _recorder, _poll = _line_auto_reply_with_recorder(web_state)
    enabled_at = web_state._ai_auto_reply_record("auto-account")["enabledAtMs"]
    web_state.ai_auto_reply_held[("auto-account", "U-x")] = enabled_at
    # A settings change that leaves auto reply on keeps it.
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": True, "contactsEnabled": True, "groupsEnabled": True}, owner
    )
    assert web_state.ai_auto_reply_held == {("auto-account", "U-x"): enabled_at}
    web_state.save_ai_auto_reply_settings(
        "auto-account", {"enabled": False, "contactsEnabled": True, "groupsEnabled": True}, owner
    )
    assert web_state.ai_auto_reply_held == {}
    web_state.ai_auto_reply_held[("auto-account", "U-x")] = enabled_at
    web_state._commit_account_removal("auto-account")
    assert web_state.ai_auto_reply_held == {}


def test_assistant_routes_that_call_the_ai_provider_are_rate_limited():
    limits = webapp_module.AUTHENTICATED_API_RATE_LIMITS
    assert limits[("POST", "/api/assistant/settings")] == (6, 60.0)
    assert limits[("POST", "/api/assistant/ask")] == (20, 60.0)
    assert limits[("POST", "/api/assistant/knowledge/reindex")] == (4, 60.0)
    assert limits[("POST", "/api/ai/models")] == (12, 60.0)


def test_assistant_has_account_scoped_line_auto_reply_controls():
    assert 'data-assistant-view-target="auto-reply"' in INDEX_HTML
    assert 'data-assistant-view="auto-reply"' in INDEX_HTML
    assert 'id="assistantAutoReplyEnabled" type="checkbox"' in INDEX_HTML
    assert 'id="assistantAutoReplyContacts" type="checkbox"' in INDEX_HTML
    assert 'id="assistantAutoReplyGroups" type="checkbox"' in INDEX_HTML
    assert 'id="assistantAutoReplyGroupModeAll"' in INDEX_HTML
    assert 'id="assistantAutoReplyGroupModeSelected"' in INDEX_HTML
    assert 'id="assistantAutoReplyGroupList"' in INDEX_HTML
    assert 'id="assistantAutoReplyGroupSaveButton"' in INDEX_HTML
    assert '"/api/assistant/auto-reply"' in INDEX_HTML
    assert "/api/assistant/auto-reply/groups?" in INDEX_HTML
    assert "contactsEnabled: $(\"assistantAutoReplyContacts\").checked" in INDEX_HTML
    assert "groupsEnabled: $(\"assistantAutoReplyGroups\").checked" in INDEX_HTML
    assert "groupReplyMode: state.assistantAutoReplyGroupMode" in INDEX_HTML
    assert "selectedGroupMids: [...state.assistantAutoReplySelectedGroupMids]" in INDEX_HTML


def test_assistant_auto_reply_is_the_first_and_default_view():
    auto_reply_tab = INDEX_HTML.index('id="assistantAutoReplyViewButton"')
    chat_tab = INDEX_HTML.index('id="assistantChatViewButton"')
    assert auto_reply_tab < chat_tab
    assert 'id="assistantAutoReplyViewButton" class="subtab active"' in INDEX_HTML
    assert 'id="assistantAutoReplyView" data-assistant-view="auto-reply"' in INDEX_HTML
    assert 'id="assistantChatView" class="hidden" data-assistant-view="chat"' in INDEX_HTML
    assert 'assistantView: "auto-reply"' in INDEX_HTML
    assert '? view : "auto-reply"' in INDEX_HTML


def test_assistant_can_generate_safe_starter_knowledge_without_overwriting():
    assert 'id="assistantKnowledgeGenerateButton"' in INDEX_HTML
    assert "function assistantStarterKnowledge()" in INDEX_HTML
    assert "function generateAssistantStarterKnowledge()" in INDEX_HTML
    assert "linepassport:starter-general-v1" in INDEX_HTML
    assert 'current.includes(ASSISTANT_STARTER_KNOWLEDGE_MARKER)' in INDEX_HTML
    assert '${current.replace(/\\s+$/, "")}\\n\\n---\\n\\n${starter}' in INDEX_HTML
    start = INDEX_HTML.index("async function generateAssistantStarterKnowledge")
    end = INDEX_HTML.index("async function loadAssistantKnowledge", start)
    assert 'post("/api/assistant/knowledge"' not in INDEX_HTML[start:end]


def test_bot_has_an_in_app_usage_guide():
    assert 'data-bot-page-target="guide"' in INDEX_HTML
    assert 'data-bot-page="guide"' in INDEX_HTML
    assert 'id="botGuideScheduleButton"' in INDEX_HTML
    assert 'id="botGuidePatternsButton"' in INDEX_HTML
    assert 'id="botGuideLogsButton"' in INDEX_HTML
    assert 'id="botGuideAiButton"' in INDEX_HTML
    assert '"help.bot.s5_body"' in INDEX_HTML
    assert 'id="botGuideBackButton"' in INDEX_HTML
    assert '"ai-settings", "guide"' in INDEX_HTML


def test_web_ui_bot_dates_display_dd_mm_yyyy():
    # Each scheduler date keeps its native yyyy-mm-dd picker plus a dd/mm/yyyy
    # text mirror, so the shown format is independent of the browser locale.
    for base in ("scheduleRunAtDate", "scheduleActiveFrom", "scheduleActiveUntil"):
        assert f'id="{base}Text"' in INDEX_HTML  # the dd/mm/yyyy display
        assert f'id="{base}"' in INDEX_HTML  # the native picker (stores yyyy-mm-dd)
    assert 'class="date-native"' in INDEX_HTML
    assert '"scheduler.date_ph"' in INDEX_HTML
    assert "function isoToDmy" in INDEX_HTML


def test_web_ui_background_refreshes_visible_schedules_or_logs():
    assert "const BOT_BACKGROUND_REFRESH_MS = 3000" in INDEX_HTML
    assert "loadSchedules({background: true})" in INDEX_HTML
    assert "loadBotLogs({background: true})" in INDEX_HTML
    assert 'if (state.tab === "bot") loadSchedules({background: true})' in INDEX_HTML
    assert 'if (state.tab === "logs") loadBotLogs({background: true})' in INDEX_HTML
    assert "setInterval(refreshWorkspaceInBackground, BOT_BACKGROUND_REFRESH_MS)" in INDEX_HTML
    assert 'document.addEventListener("visibilitychange"' in INDEX_HTML
    assert "renderBotLogs({preserveScroll: background})" in INDEX_HTML


def test_apply_permissions_reenables_permission_only_controls():
    # Regression: permission-only controls (e.g. the AI Settings key field) must
    # be re-enabled once the permission is held. The pre-fix one-way gate only
    # ever set disabled=true, leaving account-independent controls stuck disabled.
    assert 'data-requires-account")) el.disabled = false' in INDEX_HTML


def test_schedule_ai_image_requires_prompt():
    base = {
        "to": "u" + "8" * 32,
        "contentSource": "ai_image",
        "mode": "once",
        "runAt": "2026-07-07T12:30",
    }
    with pytest.raises(WebError):
        _schedule_from_body(dict(base), account_id="acct")

    job = _schedule_from_body({**base, "aiPrompt": "a red bicycle"}, account_id="acct")
    assert job["contentSource"] == "ai_image"
    assert job["aiPrompt"] == "a red bicycle"
    assert job["text"] == ""


def test_schedule_ai_image_accepts_message_patterns_instead_of_prompt():
    # An AI-image job needs a prompt OR at least one message pattern.
    job = _schedule_from_body(
        {
            "to": "u" + "8" * 32,
            "contentSource": "ai_image",
            "patternTexts": ["a red bike", "a blue car"],
            "mode": "once",
            "runAt": "2026-07-07T12:30",
        },
        account_id="acct",
    )
    assert job["contentSource"] == "ai_image"
    assert job["aiPrompt"] == ""
    assert job["patternTexts"] == ["a red bike", "a blue car"]


def test_resolve_job_contents_ai_image_uses_pattern_as_prompt(monkeypatch):
    seen = {}

    def fake_gen(prompt, *, provider, api_key, model, base_url, aspect_ratio="", log=None):
        seen["prompt"] = prompt
        return {"data": b"IMG", "mime": "image/png", "name": "x.png"}

    monkeypatch.setattr(webapp_module, "_generate_ai_image", fake_gen)

    # With patterns and no aiPrompt, the (only) pattern text becomes the prompt.
    job = {"contentSource": "ai_image", "aiPrompt": "", "patternTexts": ["a lone prompt"]}
    webapp_module._resolve_job_contents(job, ai_settings={"provider": "google", "apiKey": "k"})
    assert seen["prompt"].startswith(webapp_module.AI_IMAGE_PROMPT_INSTRUCTION)
    assert seen["prompt"].endswith("User prompt:\na lone prompt")


def test_schedule_rejects_encrypted_image_content():
    # E2EE + AI image is rejected at creation so we never pay to generate an
    # image that can't be sent (and likewise for plain image content).
    ai = {
        "to": "u" + "9" * 32,
        "contentSource": "ai_image",
        "aiPrompt": "a fox",
        "encrypt": True,
        "mode": "once",
        "runAt": "2026-07-07T12:30",
    }
    with pytest.raises(WebError):
        _schedule_from_body(ai, account_id="acct")
    with pytest.raises(WebError):
        _schedule_from_body(
            {
                "to": "u" + "9" * 32,
                "contentSource": "image",
                "imageSource": "https://example.test/y.jpg",
                "encrypt": True,
                "mode": "once",
                "runAt": "2026-07-07T12:30",
            },
            account_id="acct",
        )
    # Without encryption the AI-image job builds normally.
    job = _schedule_from_body({**ai, "encrypt": False}, account_id="acct")
    assert job["contentSource"] == "ai_image" and job["encrypt"] is False


def test_resolve_job_contents_ai_image_applies_placeholders_and_settings(monkeypatch):
    import re as _re

    seen = {}

    def fake_gen(prompt, *, provider, api_key, model, base_url, aspect_ratio="", log=None):
        seen.update(
            prompt=prompt,
            provider=provider,
            api_key=api_key,
            model=model,
            aspect_ratio=aspect_ratio,
            base_url=base_url,
        )
        return {"data": b"IMGBYTES", "mime": "image/png", "name": "nano-banana.png"}

    monkeypatch.setattr(webapp_module, "_generate_ai_image", fake_gen)

    job = {"contentSource": "ai_image", "aiPrompt": "a cat {2D}"}
    settings = {
        "provider": "nanobananaapi",
        "apiKey": "key-123",
        "model": "nano-banana-2",
        "aspectRatio": "16:9",
        "baseUrl": "https://x",
    }
    items = webapp_module._resolve_job_contents(job, ai_settings=settings)

    assert items == [{"kind": "image", "data": b"IMGBYTES", "name": "nano-banana.png"}]
    assert seen["prompt"].startswith(webapp_module.AI_IMAGE_PROMPT_INSTRUCTION)
    assert _re.search(r"User prompt:\na cat \d\d$", seen["prompt"])
    assert seen["provider"] == "nanobananaapi"
    assert seen["api_key"] == "key-123"
    assert seen["model"] == "nano-banana-2"
    assert seen["aspect_ratio"] == "16:9"


def test_resolve_job_contents_ai_image_logs_generation_steps(monkeypatch):
    calls = {"post": 0, "get": 0}
    events = []

    def log(action, detail="", ok=True, data=None):
        events.append((action, detail, ok, data or {}))

    def fake_post(
        url, json=None, headers=None, timeout=None, stream=None, allow_redirects=None
    ):
        calls["post"] += 1

        class R:
            status_code = 200

            def json(self):
                return {"code": 200, "msg": "success", "data": {"taskId": "task-123"}}

        return R()

    def fake_get(
        url, params=None, headers=None, timeout=None, stream=None, allow_redirects=None
    ):
        calls["get"] += 1
        flag = 0 if calls["get"] == 1 else 1

        class R:
            status_code = 200

            def json(self):
                if flag == 0:
                    return {"code": 200, "data": {"successFlag": 0}}
                return {
                    "code": 200,
                    "data": {
                        "successFlag": 1,
                        "response": {"resultImageUrl": "https://img.test/out.png"},
                    },
                }

        return R()

    monkeypatch.setattr(webapp_module.requests, "post", fake_post)
    monkeypatch.setattr(webapp_module.requests, "get", fake_get)
    monkeypatch.setattr(webapp_module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        webapp_module, "_download_image", lambda url: (b"\x89PNG\r\n\x1a\nX", "out.png")
    )

    prompt = ("a cat with complete campaign details " * 20).strip()
    items = webapp_module._resolve_job_contents(
        {"contentSource": "ai_image", "aiPrompt": prompt},
        ai_settings={
            "provider": "nanobananaapi",
            "apiKey": "k",
            "model": "nano-banana",
            "aspectRatio": "16:9",
        },
        log=log,
    )

    assert items[0]["name"] == "out.png"
    assert [event[0] for event in events] == [
        "content.ai.start",
        "content.ai.request",
        "content.ai.task",
        "content.ai.poll",
        "content.ai.poll",
        "content.ai.download",
        "content.ai.success",
    ]
    expected_prompt = webapp_module._prepare_ai_image_prompt(prompt)
    assert events[0][1] == expected_prompt
    assert events[0][3]["prompt"] == expected_prompt
    assert events[1][3]["provider"] == "nanobananaapi"
    assert events[1][3]["prompt"] == expected_prompt
    assert events[2][1] == "task-123"
    assert events[4][1] == "attempt 2: ready"


def test_resolve_job_contents_ai_image_logs_generation_error(monkeypatch):
    events = []

    def log(action, detail="", ok=True, data=None):
        events.append((action, detail, ok, data or {}))

    def fake_gen(prompt, *, provider, api_key, model, base_url, aspect_ratio="", log=None):
        raise WebError(HTTPStatus.BAD_GATEWAY, "provider exploded", "ai_error")

    monkeypatch.setattr(webapp_module, "_generate_ai_image", fake_gen)

    with pytest.raises(WebError):
        webapp_module._resolve_job_contents(
            {"contentSource": "ai_image", "aiPrompt": "a cat"},
            ai_settings={"provider": "google", "apiKey": "k"},
            log=log,
        )

    assert [event[0] for event in events] == ["content.ai.start", "content.ai.error"]
    assert events[-1][2] is False
    assert "provider exploded" in events[-1][1]


def test_generate_ai_image_requires_key():
    with pytest.raises(WebError) as exc:
        webapp_module._generate_ai_image("draw a dog", api_key="")
    assert exc.value.code == "ai_not_configured"


def test_generate_ai_image_extracts_inline_image(monkeypatch):
    encoded = base64.b64encode(b"PNG-BYTES").decode("ascii")
    captured = {}
    events = []

    class _Resp:
        status_code = 200

        def json(self):
            # A text part precedes the image part — the parser must not assume
            # index 0 is the image.
            return {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": "Here is your image"},
                                {"inlineData": {"mimeType": "image/png", "data": encoded}},
                            ]
                        }
                    }
                ]
            }

    def fake_post(
        url, json=None, headers=None, timeout=None, stream=None, allow_redirects=None
    ):
        captured.update(url=url, body=json, headers=headers, timeout=timeout)
        return _Resp()

    monkeypatch.setattr(webapp_module.requests, "post", fake_post)

    res = webapp_module._generate_ai_image(
        "a cat",
        api_key="secret",
        model="gemini-2.5-flash-image",
        log=lambda action, detail="", ok=True, data=None: events.append(
            (action, detail, ok, data or {})
        ),
    )

    assert res["data"] == b"PNG-BYTES"
    assert res["mime"] == "image/png"
    assert res["name"] == "gemini-image.png"
    assert captured["url"].endswith("/v1beta/models/gemini-2.5-flash-image:generateContent")
    assert captured["headers"]["x-goog-api-key"] == "secret"
    expected_prompt = webapp_module._prepare_ai_image_prompt("a cat")
    assert captured["body"]["contents"][0]["parts"][0]["text"] == expected_prompt
    assert events == [
        (
            "content.ai.request",
            "Google Gemini · gemini-2.5-flash-image",
            True,
            {
                "provider": "google",
                "model": "gemini-2.5-flash-image",
                "endpoint": captured["url"],
                "prompt": expected_prompt,
            },
        )
    ]


def test_generate_ai_image_surfaces_upstream_error(monkeypatch):
    class _Resp:
        status_code = 429
        text = ""

        def json(self):
            return {"error": {"message": "quota exceeded", "status": "RESOURCE_EXHAUSTED"}}

    monkeypatch.setattr(webapp_module.requests, "post", lambda *a, **k: _Resp())

    with pytest.raises(WebError) as exc:
        webapp_module._generate_ai_image("draw", api_key="k")
    assert "429" in str(exc.value)
    assert "quota exceeded" in str(exc.value)
    assert "Google Gemini error 429" in str(exc.value)
    # A distinct code so the browser shows an AI message, not the generic
    # LINE "upstream_error" string.
    assert exc.value.code == "ai_quota"


def test_generate_nbapi_image_polls_then_downloads(monkeypatch):
    calls = {"post": 0, "get": 0}

    def fake_post(
        url, json=None, headers=None, timeout=None, stream=None, allow_redirects=None
    ):
        calls["post"] += 1
        assert url.endswith("/api/v1/nanobanana/generate")
        assert headers["Authorization"] == "Bearer nb-key"
        assert json["type"] == "TEXTTOIAMGE"  # the vendor's verbatim typo
        assert json["prompt"] == webapp_module._prepare_ai_image_prompt("a dog")
        assert json["callBackUrl"]  # required field is always sent
        assert json["image_size"] == "16:9"  # a concrete size passes through

        class R:
            status_code = 200

            def json(self):
                return {"code": 200, "msg": "success", "data": {"taskId": "t-1"}}

        return R()

    def fake_get(
        url, params=None, headers=None, timeout=None, stream=None, allow_redirects=None
    ):
        calls["get"] += 1
        assert url.endswith("/api/v1/nanobanana/record-info")
        assert params["taskId"] == "t-1"
        flag = 0 if calls["get"] == 1 else 1  # generating, then success

        class R:
            status_code = 200

            def json(self):
                if flag == 0:
                    return {"code": 200, "data": {"successFlag": 0}}
                return {
                    "code": 200,
                    "data": {
                        "successFlag": 1,
                        "response": {"resultImageUrl": "https://img.test/x.png"},
                    },
                }

        return R()

    monkeypatch.setattr(webapp_module.requests, "post", fake_post)
    monkeypatch.setattr(webapp_module.requests, "get", fake_get)
    monkeypatch.setattr(webapp_module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        webapp_module, "_download_image", lambda url: (b"\x89PNG\r\n\x1a\nX", "x.png")
    )

    res = webapp_module._generate_ai_image(
        "a dog",
        provider="nanobananaapi",
        api_key="nb-key",
        model="nano-banana",
        aspect_ratio="16:9",
    )
    assert res["data"] == b"\x89PNG\r\n\x1a\nX"
    assert res["name"] == "x.png"
    assert res["mime"] == "image/png"
    assert calls["post"] == 1 and calls["get"] == 2  # polled until ready


@pytest.mark.parametrize(
    ("model", "endpoint", "resolution", "extra"),
    [
        (
            "nano-banana-2",
            "/api/v1/nanobanana/generate-2",
            "1K",
            {"googleSearch": False, "outputFormat": "jpg"},
        ),
        ("nano-banana-pro", "/api/v1/nanobanana/generate-pro", "2K", {}),
    ],
)
def test_generate_nbapi_image_uses_selected_model_endpoint(
    monkeypatch, model, endpoint, resolution, extra
):
    captured = {}

    def fake_post(
        url, json=None, headers=None, timeout=None, stream=None, allow_redirects=None
    ):
        captured.update(url=url, body=json)

        class R:
            status_code = 200

            def json(self):
                return {"code": 200, "message": "success", "data": {"taskId": "t-2"}}

        return R()

    def fake_get(
        url, params=None, headers=None, timeout=None, stream=None, allow_redirects=None
    ):
        class R:
            status_code = 200

            def json(self):
                return {
                    "code": 200,
                    "data": {
                        "successFlag": 1,
                        "response": {"resultImageUrl": "https://img.test/out.jpg"},
                    },
                }

        return R()

    monkeypatch.setattr(webapp_module.requests, "post", fake_post)
    monkeypatch.setattr(webapp_module.requests, "get", fake_get)
    monkeypatch.setattr(webapp_module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        webapp_module, "_download_image", lambda url: (b"\xff\xd8\xff\xe0", "out.jpg")
    )

    res = webapp_module._generate_ai_image(
        "a dog",
        provider="nanobananaapi",
        api_key="nb-key",
        model=model,
        aspect_ratio="16:9",
    )

    assert captured["url"].endswith(endpoint)
    assert captured["body"]["imageUrls"] == []
    assert captured["body"]["aspectRatio"] == "16:9"
    assert captured["body"]["resolution"] == resolution
    for key, value in extra.items():
        assert captured["body"][key] == value
    assert res["name"] == "out.jpg"


def test_generate_nbapi_image_flags_rejected_key(monkeypatch):
    class R:
        status_code = 401
        text = ""

        def json(self):
            return {"code": 401, "msg": "invalid api key"}

    monkeypatch.setattr(webapp_module.requests, "post", lambda *a, **k: R())
    with pytest.raises(WebError) as exc:
        webapp_module._generate_ai_image("x", provider="nanobananaapi", api_key="bad")
    assert exc.value.code == "ai_key_rejected"


def test_generate_nbapi_image_reports_generation_failure(monkeypatch):
    def fake_post(*a, **k):
        class R:
            status_code = 200

            def json(self):
                return {"code": 200, "data": {"taskId": "t"}}

        return R()

    def fake_get(*a, **k):
        class R:
            status_code = 200

            def json(self):
                return {
                    "code": 200,
                    "data": {"successFlag": 3, "errorMessage": "nsfw blocked"},
                }

        return R()

    monkeypatch.setattr(webapp_module.requests, "post", fake_post)
    monkeypatch.setattr(webapp_module.requests, "get", fake_get)
    monkeypatch.setattr(webapp_module.time, "sleep", lambda *_: None)
    with pytest.raises(WebError) as exc:
        webapp_module._generate_ai_image("x", provider="nanobananaapi", api_key="k")
    assert "nsfw blocked" in str(exc.value)


def test_generate_fal_image_uses_queue_and_downloads_result(monkeypatch):
    calls = {"status": 0}
    captured = {}

    class JsonResponse:
        def __init__(self, status_code, data):
            self.status_code = status_code
            self._data = data
            self.text = ""

        def json(self):
            return self._data

    class ImageResponse:
        status_code = 200
        content = b"\x89PNG\r\n\x1a\nFAL"
        headers = {"content-type": "image/png"}

        def raise_for_status(self):
            return None

    def fake_post(
        url, json=None, headers=None, timeout=None, stream=None, allow_redirects=None
    ):
        captured.update(url=url, body=json, headers=headers, timeout=timeout)
        return JsonResponse(
            202,
            {
                "request_id": "fal-request-1",
                "status_url": "https://queue.fal.run/status/fal-request-1",
                "response_url": "https://queue.fal.run/result/fal-request-1",
            },
        )

    def fake_get(
        url, params=None, headers=None, timeout=None, stream=None, allow_redirects=None
    ):
        if "/status/" in url:
            calls["status"] += 1
            status = "IN_PROGRESS" if calls["status"] == 1 else "COMPLETED"
            return JsonResponse(200, {"status": status})
        if "/result/" in url:
            return JsonResponse(
                200,
                {"images": [{"url": "https://fal.media/files/test/generated.png"}]},
            )
        assert url == "https://fal.media/files/test/generated.png"
        return ImageResponse()

    monkeypatch.setattr(webapp_module.requests, "post", fake_post)
    monkeypatch.setattr(webapp_module.requests, "get", fake_get)
    monkeypatch.setattr(webapp_module.time, "sleep", lambda *_: None)
    monkeypatch.setattr(
        webapp_module,
        "_download_image",
        lambda url: (b"\x89PNG\r\n\x1a\nFAL", "generated.png"),
    )

    result = webapp_module._generate_ai_image(
        "a green city",
        provider="fal",
        api_key="fal-key",
        model="fal-ai/flux/dev",
        aspect_ratio="square_hd",
    )

    assert captured["url"] == "https://queue.fal.run/fal-ai/flux/dev"
    assert captured["headers"]["Authorization"] == "Key fal-key"
    assert captured["body"] == {
        "prompt": webapp_module._prepare_ai_image_prompt("a green city"),
        "image_size": "square_hd",
        "num_images": 1,
        "enable_safety_checker": True,
        "output_format": "png",
    }
    assert calls["status"] == 2
    assert result == {
        "data": b"\x89PNG\r\n\x1a\nFAL",
        "mime": "image/png",
        "name": "generated.png",
    }


def test_generate_fal_image_flags_rejected_key(monkeypatch):
    class Response:
        status_code = 401
        text = ""

        def json(self):
            return {"detail": "Invalid credentials"}

    monkeypatch.setattr(webapp_module.requests, "post", lambda *a, **k: Response())
    with pytest.raises(WebError) as exc:
        webapp_module._generate_ai_image(
            "draw",
            provider="fal",
            api_key="bad",
            model="fal-ai/flux/schnell",
        )
    assert exc.value.code == "ai_key_rejected"
    assert "fal.ai error 401" in str(exc.value)


def test_fal_http_error_preserves_full_structured_message_without_input():
    message = "The model did not generate the expected output. " + ("Review inputs. " * 30)

    class Response:
        status_code = 422
        text = ""

        def json(self):
            return {
                "detail": [
                    {
                        "msg": message,
                        "type": "no_media_generated",
                        "url": "https://docs.fal.ai/errors#no_media_generated",
                        "input": {"prompt": "private prompt"},
                    }
                ]
            }

    detail = webapp_module._fal_http_error(Response())

    assert message in detail
    assert "[no_media_generated]" in detail
    assert "https://docs.fal.ai/errors#no_media_generated" in detail
    assert "private prompt" not in detail


def test_fal_model_catalog_has_labels_prices_and_valid_payloads():
    assert len(webapp_module.FAL_MODELS) == 11
    assert set(webapp_module.FAL_MODELS) == set(webapp_module.FAL_MODEL_LABELS)
    assert set(webapp_module.FAL_MODELS) == set(webapp_module.FAL_MODEL_PRICES)
    for model in webapp_module.FAL_MODELS:
        payload = webapp_module._fal_model_payload("draw", model, "landscape_16_9")
        assert payload["prompt"] == "draw"
        assert payload["num_images"] == 1
        assert (
            payload.get("image_size") == "landscape_16_9"
            or payload.get("aspect_ratio") == "16:9"
        )


def test_fal_model_payload_adapts_model_specific_schemas():
    nano = webapp_module._fal_model_payload("draw", "fal-ai/nano-banana-2", "portrait_16_9")
    assert nano["aspect_ratio"] == "9:16"
    assert nano["resolution"] == "1K"
    assert "image_size" not in nano

    gpt = webapp_module._fal_model_payload("draw", "openai/gpt-image-2", "square")
    assert gpt["image_size"] == "square_hd"
    assert gpt["quality"] == "medium"
    assert "enable_safety_checker" not in gpt

    ideogram = webapp_module._fal_model_payload("draw", "ideogram/v4", "square_hd")
    assert ideogram["rendering_speed"] == "BALANCED"
    assert ideogram["expansion_model"] == "Medium"

    seedream = webapp_module._fal_model_payload(
        "draw", "bytedance/seedream/v5/lite/text-to-image", "square_hd"
    )
    assert seedream["max_images"] == 1

    qwen = webapp_module._fal_model_payload(
        "draw", "fal-ai/qwen-image-2/text-to-image", "square_hd"
    )
    assert qwen["enable_prompt_expansion"] is True


def test_generate_ai_image_flags_rejected_key(monkeypatch):
    class _Resp:
        status_code = 403
        text = ""

        def json(self):
            return {"error": {"message": "API key not valid", "status": "PERMISSION_DENIED"}}

    monkeypatch.setattr(webapp_module.requests, "post", lambda *a, **k: _Resp())

    with pytest.raises(WebError) as exc:
        webapp_module._generate_ai_image("draw", api_key="bad")
    assert exc.value.code == "ai_key_rejected"


def test_generate_ai_image_reports_refusal_when_no_image(monkeypatch):
    class _Resp:
        status_code = 200

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "I can't make that."}]}}]}

    monkeypatch.setattr(webapp_module.requests, "post", lambda *a, **k: _Resp())

    with pytest.raises(WebError) as exc:
        webapp_module._generate_ai_image("draw", api_key="k")
    assert "no image" in str(exc.value).lower()
    assert "I can't make that." in str(exc.value)


def test_ai_settings_save_mask_and_clear(web_state):
    assert web_state.ai_settings()["configured"] is False

    saved = web_state.save_ai_settings(
        {"apiKey": "abcd1234efgh5678wxyz", "model": "gemini-2.5-flash-image"}
    )
    google = saved["providers"]["google"]
    assert saved["configured"] is True and google["hasApiKey"] is True
    assert google["apiKeyPreview"] == "••••wxyz"  # a long key shows its last 4 only
    assert "abcd1234" not in google["apiKeyPreview"]  # raw key is never echoed back
    assert web_state.ai_settings(reveal=True)["apiKey"] == "abcd1234efgh5678wxyz"

    # A blank key on save keeps the stored one but still updates the model.
    web_state.save_ai_settings({"model": "gemini-2.5-flash-image-preview"})
    assert web_state.ai_settings(reveal=True)["apiKey"] == "abcd1234efgh5678wxyz"
    assert (
        web_state.ai_settings()["providers"]["google"]["model"]
        == "gemini-2.5-flash-image-preview"
    )

    # Explicit clear wipes the key.
    web_state.save_ai_settings({"clearApiKey": True})
    assert web_state.ai_settings()["configured"] is False


def test_ai_settings_per_provider_and_migration(web_state):
    web_state.save_ai_settings({"provider": "google", "apiKey": "g-key-aaaa"})
    web_state.save_ai_settings({"provider": "nanobananaapi", "apiKey": "n-key-bbbb"})

    s = web_state.ai_settings()
    assert s["provider"] == "nanobananaapi"  # the latest save activates its provider
    assert s["providers"]["google"]["hasApiKey"] is True
    assert s["providers"]["nanobananaapi"]["hasApiKey"] is True
    assert web_state.ai_settings(reveal=True) == {
        "provider": "nanobananaapi",
        "apiKey": "n-key-bbbb",
        "model": "nano-banana",
        "aspectRatio": "auto",
        "baseUrl": "https://api.nanobananaapi.ai",
    }
    nano = s["providers"]["nanobananaapi"]
    assert "nano-banana-pro" in nano["models"]
    assert nano["modelLabels"]["nano-banana-2"] == "NanoBanana 2"
    assert "16:9" in nano["aspectRatios"]

    web_state.save_ai_settings(
        {"provider": "nanobananaapi", "model": "nano-banana-pro", "aspectRatio": "16:9"}
    )
    assert web_state.ai_settings(reveal=True)["model"] == "nano-banana-pro"
    assert web_state.ai_settings(reveal=True)["aspectRatio"] == "16:9"

    web_state.save_ai_settings(
        {
            "provider": "fal",
            "apiKey": "fal-key-cccc",
            "model": "fal-ai/flux/dev",
            "aspectRatio": "square_hd",
        }
    )
    fal = web_state.ai_settings()["providers"]["fal"]
    assert fal["hasApiKey"] is True
    assert fal["modelLabels"]["fal-ai/flux/schnell"] == "FLUX.1 Schnell (fast)"
    assert fal["modelPrices"]["fal-ai/flux/schnell"] == "$0.003/MP"
    assert fal["modelPrices"]["openai/gpt-image-2"].startswith("$0.04-$0.06")
    assert len(fal["models"]) == 11
    assert "landscape_16_9" in fal["aspectRatios"]
    assert fal["aspectRatioLabels"]["landscape_16_9"] == "Landscape 16:9"
    assert web_state.ai_settings(reveal=True) == {
        "provider": "fal",
        "apiKey": "fal-key-cccc",
        "model": "fal-ai/flux/dev",
        "aspectRatio": "square_hd",
        "baseUrl": "https://queue.fal.run",
    }

    # Switching the active provider (no key) keeps every provider key.
    web_state.save_ai_settings({"provider": "google"})
    assert web_state.ai_settings(reveal=True)["apiKey"] == "g-key-aaaa"
    assert web_state.ai_settings()["providers"]["nanobananaapi"]["hasApiKey"] is True
    assert web_state.ai_settings()["providers"]["fal"]["hasApiKey"] is True

    # Legacy flat {apiKey, model, baseUrl} is migrated to the google provider.
    web_state.store.set(
        "ai_settings", {"apiKey": "legacy-1234", "model": "gemini-2.5-flash-image"}
    )
    migrated = web_state.ai_settings()
    assert migrated["provider"] == "google"
    assert migrated["providers"]["google"]["hasApiKey"] is True
    assert web_state.ai_settings(reveal=True)["apiKey"] == "legacy-1234"

    # Legacy nanobananaapi.ai settings stored the aspect ratio as "model".
    web_state.store.set(
        "ai_settings",
        {
            "provider": "nanobananaapi",
            "nanobananaapi": {"apiKey": "n-key", "model": "16:9"},
        },
    )
    legacy_nano = web_state.ai_settings()
    assert legacy_nano["providers"]["nanobananaapi"]["model"] == "nano-banana"
    assert legacy_nano["providers"]["nanobananaapi"]["aspectRatio"] == "16:9"


def test_ai_settings_http_roundtrip_never_leaks_key(live_server):
    session = requests.Session()
    session.get(f"{live_server}/api/auth/status", timeout=5)  # simple admin session

    got = session.get(f"{live_server}/api/ai/settings", timeout=5)
    assert got.status_code == 200
    assert got.json()["configured"] is False

    saved = session.post(
        f"{live_server}/api/ai/settings", json={"apiKey": "tok-1234567898765432"}, timeout=5
    )
    assert saved.status_code == 200
    assert saved.json()["configured"] is True

    again = session.get(f"{live_server}/api/ai/settings", timeout=5).json()
    google = again["providers"]["google"]
    assert google["hasApiKey"] is True
    assert google["apiKeyPreview"].endswith("5432")
    assert "tok-1234567898765432" not in google["apiKeyPreview"]


@pytest.mark.parametrize("other_traffic", [False, True])
def test_a_chat_that_sent_many_messages_at_once_does_not_go_ahead_of_those_waiting_longer(
    web_state, other_traffic
):
    # A new customer's photo album and question, or a returning customer's, can be
    # more than the 10 messages LINE shows. Such a chat goes after the chats that waited
    # at the last check that saw it with nothing to answer (or that listed every chat
    # but it), not by when it was last answered or when auto reply was switched on;
    # also when nothing newer than the waiting customers' messages was written since.
    owner, _recorder, poll = _line_auto_reply_with_recorder(web_state, chats=())
    fake = web_state.get_api("auto-account")
    now = int(time.time() * 1000)
    enabled_at = now - 3_600_000  # switched on an hour ago
    data = web_state._ai_auto_reply_all()
    data["accounts"]["auto-account"]["enabledAtMs"] = enabled_at
    web_state.store.set("ai_auto_reply", data)
    for index in range(2):
        _queue_telegram_reply(web_state, owner, index)  # both places taken
    waiting = [f"U-once-{index}" for index in range(8)]
    boxes = {
        mid: [{"id": f"{mid}-1", "from": mid, "text": "hi", "contentType": 0,
               "createdTime": now - 120_000 + index}]
        for index, mid in enumerate(waiting)
    }
    # A returning customer, answered just after the switch on: nothing to answer now.
    boxes["U-back"] = [{"id": "back-old", "from": "U-back", "text": "thanks", "contentType": 0,
                        "createdTime": enabled_at + 1000}]
    web_state._save_ai_auto_reply_cursors(
        "auto-account", enabled_at, {"U-back": {"time": enabled_at + 1000, "ids": ["back-old"]}}
    )
    if other_traffic:
        # A chat answered a minute ago, the newest message of this check.
        boxes["U-done"] = [{"id": "done-1", "from": "U-me", "text": "you're welcome",
                            "contentType": 0, "createdTime": now - 60_000}]
    fake.boxes = dict(boxes)
    poll()
    assert _ready_chats(web_state, owner) == waiting

    def album(mid):
        return [{"id": f"{mid}-{n}", "from": mid, "text": f"photo {n}", "contentType": 0,
                 "createdTime": now - 2500 + 200 * n} for n in range(11)]

    fake.boxes = {**boxes, "U-newcomer": album("U-newcomer"),
                  "U-back": boxes["U-back"] + album("U-back")}
    poll()
    assert _ready_chats(web_state, owner) == waiting  # the 8 who waited longer first
    for mid in ("U-newcomer", "U-back"):
        since = web_state.ai_auto_reply_waiting[("auto-account", mid)][2]
        assert enabled_at < since <= now - 2500, mid  # never later than it began
    # Once those were answered (their chats wait no more), the two come next.
    fake.boxes = {"U-newcomer": album("U-newcomer"), "U-back": boxes["U-back"] + album("U-back")}
    poll()
    assert sorted(_ready_chats(web_state, owner)) == ["U-back", "U-newcomer"]


@pytest.mark.parametrize("sent", [True, False])
def test_a_reply_ending_as_its_account_is_removed_keeps_nothing_of_it(web_state, sent):
    # The account goes while the reply's cursor is being saved, and that save fails:
    # nothing is kept for it afterwards (no held chat, cursor, runtime or log line).
    fake, poll = _line_auto_reply_running(web_state)
    if not sent:

        def provider_down(question, user, **metadata):
            raise WebError(HTTPStatus.UNPROCESSABLE_ENTITY, "provider is down", "ai_unavailable")

        web_state.ask_global_ai = provider_down
    original_save = web_state._save_ai_auto_reply_cursors
    lines = {}

    def removed_as_it_saves(account_id, enabled_at_ms, cursors):
        if "U-contact" in cursors and not lines:
            web_state._commit_account_removal(account_id)
            lines["at_removal"] = len(web_state.store.get("bot_logs", {"logs": []})["logs"])
            raise OSError(28, "No space left on device")
        return original_save(account_id, enabled_at_ms, cursors)

    web_state._save_ai_auto_reply_cursors = removed_as_it_saves
    poll()
    assert lines and web_state.account_store.get("auto-account") is None
    assert fake.sent == ([("U-contact", "AI: hello")] if sent else [])
    assert web_state.ai_auto_reply_held == {}
    assert web_state.ai_auto_reply_unsaved_cursors == {}
    assert "auto-account" not in web_state.ai_auto_reply_runtime
    logs = web_state.store.get("bot_logs", {"logs": []})["logs"]
    assert len(logs) == lines["at_removal"]


def test_a_reply_sent_as_its_account_is_removed_leaves_nothing_of_it(web_state):
    # The removal waited for the account's lock, which the send held: it commits as
    # soon as LINE took the message. The reply notes nothing of it afterwards.
    fake, poll = _line_auto_reply_running(web_state)
    original_send = fake.send_text

    def send_then_removed(mid, text):
        result = original_send(mid, text)
        web_state._commit_account_removal("auto-account")
        return result

    fake.send_text = send_then_removed
    poll()
    assert fake.sent == [("U-contact", "AI: hello")]
    assert "auto-account" not in web_state.ai_auto_reply_runtime
    logs = web_state.store.get("bot_logs", {"logs": []})["logs"]
    assert "ai.auto_reply.sent" not in [log["action"] for log in logs]


def test_a_question_answered_as_its_account_or_workspace_goes_is_not_logged(
    web_state, monkeypatch
):
    owner = _auto_reply_owner(web_state)
    gone = {}

    class _Client:
        def chat(self, **chat):
            gone["now"]()  # removed while the AI answered
            return {"answer": "AI: noted", "canAnswer": True, "confidence": 0.9,
                    "model": "test-model"}

    monkeypatch.setattr(
        webapp_module, "make_ai_client", lambda provider_id, *, api_key, **kw: _Client()
    )
    gone["now"] = lambda: web_state._commit_account_removal("auto-account")
    result = web_state.ask_global_ai(
        "my order 42 is late", owner, source="line_auto_reply", account_id="auto-account",
        chat_mid="U-x", chat_type="contact",
    )
    assert result["logged"] is False and result["question"]["answer"] == "AI: noted"
    assert web_state._global_ai_all()["questions"] == []

    def workspace_deleted():
        with web_state.web_auth.lock:
            web_state.web_auth.data = {
                **web_state.web_auth.data,
                "users": [
                    user for user in web_state.web_auth.data["users"]
                    if user.get("id") != owner["id"]
                ],
            }

    gone["now"] = workspace_deleted
    assert web_state.ask_global_ai("opening hours?", owner)["logged"] is False
    assert web_state._global_ai_all()["questions"] == []


def test_a_server_clock_ahead_of_lines_does_not_put_a_chat_behind_later_ones(web_state):
    # Whether a chat that sent many messages at once began before others goes by LINE's
    # own clock, not by this server's: here the server is 30 seconds ahead of LINE.
    owner, _recorder, poll = _line_auto_reply_with_recorder(web_state, chats=())
    fake = web_state.get_api("auto-account")
    line_now = int(time.time() * 1000) - 30_000
    data = web_state._ai_auto_reply_all()
    data["accounts"]["auto-account"]["enabledAtMs"] = line_now - 3_600_000
    web_state.store.set("ai_auto_reply", data)
    for index in range(2):
        _queue_telegram_reply(web_state, owner, index)  # both places taken
    quiet = {"U-done": [{"id": "done-1", "from": "U-me", "text": "bye", "contentType": 0,
                         "createdTime": line_now - 60_000}]}
    fake.boxes = dict(quiet)
    poll()
    # A new customer's album (11 messages), then another customer's one message.
    album = [{"id": f"album-{n}", "from": "U-album", "text": f"photo {n}", "contentType": 0,
              "createdTime": line_now - 20_000 + n} for n in range(11)]
    fake.boxes = {**quiet, "U-album": album,
                  "U-once": [{"id": "once-1", "from": "U-once", "text": "hi", "contentType": 0,
                              "createdTime": line_now - 10_000}]}
    poll()
    assert _ready_chats(web_state, owner) == ["U-album", "U-once"]
    assert web_state.ai_auto_reply_waiting[("auto-account", "U-album")][2] <= line_now - 20_000


def test_a_chat_stored_as_its_account_is_removed_goes_with_the_account(web_state):
    _seed_account(web_state, "acct")
    real_ingest = web_state.crm.ingest

    def removed_first(**values):
        # The removal, and its purge of the account's chats, land while this waits.
        web_state._commit_account_removal("acct")
        return real_ingest(**values)

    web_state.crm.ingest = removed_first
    message = {"id": "m1", "from": "U-customer", "text": "my address is 12 Main St",
               "contentType": 0, "createdTime": int(time.time() * 1000)}
    web_state._crm_ingest_line("acct", "owner", "U-me", [("U-customer", [message], None)])
    assert web_state.crm.conversation_for_chat("acct", "U-customer") is None
    # Once it is gone, nothing more is stored for it.
    web_state.crm.ingest = real_ingest
    web_state._crm_ingest_line("acct", "owner", "U-me", [("U-customer", [message], None)])
    assert web_state.crm.conversation_for_chat("acct", "U-customer") is None


def test_names_looked_up_for_a_removed_account_are_not_kept(web_state):
    _seed_account(web_state, "gone-account")
    fake = _AutoReplyFakeApi()
    web_state.get_api = lambda account_id, **kwargs: fake
    lock = web_state.api_lock_for("gone-account")
    lock.acquire()  # a removal holds the account's lock while it removes it
    candidate = {"accountId": "gone-account", "chatMid": "U-contact", "senderMid": "U-contact"}
    thread = threading.Thread(target=web_state._name_ready_ai_auto_reply, args=(candidate,))
    try:
        thread.start()
        time.sleep(0.05)  # it now waits for the account's lock
        web_state._commit_account_removal("gone-account")
    finally:
        lock.release()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert fake.contact_lookup_calls == 0 and web_state.ai_auto_reply_identity_cache == {}
    # Names a reply looks up for its own log line are not kept for it either.
    web_state._hydrate_ai_auto_reply_candidate_identities("gone-account", fake, [candidate])
    assert candidate["chatName"] == "Customer One"
    assert web_state.ai_auto_reply_identity_cache == {}


def test_a_chat_claimed_as_its_account_is_removed_goes_with_the_account(web_state):
    # Claiming (or closing) a chat makes its customer and chat when there are none yet.
    # The account is removed between its check and those rows: they go too.
    owner = _incoming_api_owner(web_state)
    real_account = web_state.crm._account
    removed = {}

    def checked_then_removed(owner_id, scope, account_id):
        account = real_account(owner_id, scope, account_id)
        if not removed:
            removed["result"] = web_state.delete_account(account_id, owner)
        return account

    web_state.crm._account = checked_then_removed
    with pytest.raises(CrmError):
        web_state.crm.assign(
            owner, {"accountId": "incoming-account", "chatId": "U-customer", "assignee": "me"}
        )
    assert removed
    assert web_state.crm.conversation_for_chat("incoming-account", "U-customer") is None
    assert web_state.crm.db.scalar("SELECT COUNT(*) FROM crm_customers") == 0


def test_auto_reply_settings_saved_as_the_account_is_removed_are_not_kept(web_state):
    owner = _auto_reply_owner(web_state)
    real_global_ai = web_state._global_ai_all

    def removed_while_read():
        web_state._commit_account_removal("auto-account")
        return real_global_ai()

    web_state._global_ai_all = removed_while_read
    with pytest.raises(WebError) as gone:
        web_state.save_ai_auto_reply_settings(
            "auto-account",
            {"enabled": True, "contactsEnabled": True, "groupsEnabled": False},
            owner,
        )
    web_state._global_ai_all = real_global_ai
    assert gone.value.status == HTTPStatus.NOT_FOUND
    assert "auto-account" not in web_state._ai_auto_reply_all()["accounts"]
    assert "auto-account" not in web_state.ai_auto_reply_runtime
    logs = web_state.store.get("bot_logs", {"logs": []})["logs"]
    assert "ai.auto_reply.settings" not in [log["action"] for log in logs]


def test_deleting_an_account_counts_the_customers_a_late_store_deleted_first(web_state):
    # A chat stored by a write that began before the account's removal deletes the
    # account's customers again, before the removal's own purge does: the removal still
    # says how many customers went.
    owner = _incoming_api_owner(web_state)
    now = int(time.time() * 1000)

    def chat(mid, at):
        return {
            "owner_id": owner["id"], "channel": "line", "chat_id": mid,
            "contact": {"externalId": mid, "name": mid},
            "messages": [{"externalId": f"{mid}-1", "direction": "in", "senderKind": "customer",
                          "text": "hi", "contentType": 0, "at": at}],
        }

    web_state.crm.ingest(account_id="incoming-account", **chat("U-first", now))
    real_ingest = web_state.crm.ingest
    real_close = web_state.close_api
    started, committed, late_purged = threading.Event(), threading.Event(), threading.Event()
    closes = []

    def slow_ingest(**values):
        started.set()
        committed.wait(10)  # the removal commits while this one is on its way
        return real_ingest(**values)

    def close_api(account_id):
        real_close(account_id)
        closes.append(account_id)
        if len(closes) == 2:  # just after the removal committed
            committed.set()
            late_purged.wait(10)  # the late store's purge goes first

    web_state.crm.ingest = slow_ingest
    web_state.close_api = close_api
    store = threading.Thread(
        target=web_state._crm_ingest, args=("incoming-account",), kwargs=chat("U-second", now + 1)
    )
    store.start()
    assert started.wait(10)
    result = {}
    removal = threading.Thread(
        target=lambda: result.update(web_state.delete_account("incoming-account", owner))
    )
    removal.start()
    store.join(10)
    late_purged.set()
    removal.join(10)
    assert not store.is_alive() and not removal.is_alive()
    assert result["customersDeleted"] == 2
    assert web_state.crm.db.scalar("SELECT COUNT(*) FROM crm_customers") == 0


def test_an_incoming_api_request_that_went_out_does_not_fail_over_its_counters(
    web_state, monkeypatch
):
    # Saving the link's counters fails just after the message went out (a full disk):
    # the request still answers with what was sent, so its caller does not send it again.
    owner = _incoming_api_owner(web_state)
    fake = _IncomingApiFake()
    created = web_state.create_incoming_api_link(
        {"accountId": "incoming-account", "name": "Orders",
         "targets": [{"type": "contact", "mid": fake.contact_mid}]},
        owner,
        fake,
    )["link"]
    link_id, token = created["endpointPath"].rsplit("/", 2)[-2:]
    web_state.get_api = lambda account_id: fake
    original_set = web_state.store.set

    def store_set(key, value):
        if key == "incoming_api":
            raise OSError(28, "No space left on device")
        return original_set(key, value)

    monkeypatch.setattr(web_state.store, "set", store_set)
    result = web_state.execute_incoming_api(link_id, token, {"text": "Order 42 shipped"})
    assert result["ok"] is True and result["sent"] == 1
    assert fake.calls == [("plain", fake.contact_mid, "Order 42 shipped")]


def test_a_returning_customer_past_the_newest_500_chats_does_not_go_ahead(web_state):
    # An announcement to many customers pushes a chat answered earlier below the 500
    # newest chats, which a poll examines. When that customer later sends more at once
    # than LINE shows (11 messages), they go after the chats waiting since before.
    owner, _recorder, poll = _line_auto_reply_with_recorder(web_state, chats=())
    fake = web_state.get_api("auto-account")
    now = int(time.time() * 1000)
    enabled_at = now - 3_600_000
    data = web_state._ai_auto_reply_all()
    data["accounts"]["auto-account"]["enabledAtMs"] = enabled_at
    web_state.store.set("ai_auto_reply", data)
    for index in range(2):
        _queue_telegram_reply(web_state, owner, index)  # both places taken
    answered = [{"id": "back-1", "from": "U-back", "text": "thanks", "contentType": 0,
                 "createdTime": now - 700_000},
                {"id": "back-2", "from": "U-me", "text": "you're welcome", "contentType": 0,
                 "createdTime": now - 600_000}]
    fake.boxes = {"U-back": answered}
    poll()  # the chat is examined, with nothing to answer
    waiting = [f"U-once-{index}" for index in range(8)]
    boxes = {
        mid: [{"id": f"{mid}-1", "from": mid, "text": "hi", "contentType": 0,
               "createdTime": now - 120_000 + index}]
        for index, mid in enumerate(waiting)
    }
    for index in range(527):
        boxes[f"U-news-{index}"] = [{"id": f"news-{index}", "from": "U-me", "text": "sale!",
                                     "contentType": 0, "createdTime": now - 300_000 - index}]
    boxes["U-back"] = answered  # number 536: listed, not examined
    fake.boxes = dict(boxes)
    poll()
    assert _ready_chats(web_state, owner) == waiting
    album = [{"id": f"album-{n}", "from": "U-back", "text": f"photo {n}", "contentType": 0,
              "createdTime": now - 2500 + 200 * n} for n in range(11)]
    fake.boxes = {"U-back": answered + album, **{k: v for k, v in boxes.items() if k != "U-back"}}
    poll()
    assert _ready_chats(web_state, owner) == waiting  # the 8 who waited longer first


def test_an_incoming_api_request_that_went_out_answers_when_its_counters_cannot_be_read(
    web_state, monkeypatch
):
    # The database goes down as the message goes out, so the link's counters cannot be
    # read back to be counted: the request still answers with what was sent.
    owner = _incoming_api_owner(web_state)
    fake = _IncomingApiFake()
    created = web_state.create_incoming_api_link(
        {"accountId": "incoming-account", "name": "Orders",
         "targets": [{"type": "contact", "mid": fake.contact_mid}]},
        owner,
        fake,
    )["link"]
    link_id, token = created["endpointPath"].rsplit("/", 2)[-2:]
    web_state.get_api = lambda account_id: fake
    original_get = web_state.store.get
    reads = {"incoming_api": 0}

    def store_get(key, default):
        if key == "incoming_api":
            reads[key] += 1
            if reads[key] > 1:
                raise OSError("connection to the database was refused")
        return original_get(key, default)

    monkeypatch.setattr(web_state.store, "get", store_get)
    result = web_state.execute_incoming_api(link_id, token, {"text": "Order 42 shipped"})
    assert result["ok"] is True and result["sent"] == 1
    assert fake.calls == [("plain", fake.contact_mid, "Order 42 shipped")]


@pytest.mark.parametrize("action", ["close", "claim"])
def test_a_chat_closed_as_its_account_is_removed_leaves_nothing_behind(web_state, action):
    # The chat (or, for a claim, who may reply from it) is read, then its account is
    # removed, its chats with it, before the close or claim is written: nothing of the
    # chat is written after that.
    owner = _incoming_api_owner(web_state)
    web_state.crm.set_status(
        owner, {"accountId": "incoming-account", "chatId": "U-customer", "status": "pending"}
    )
    conversation = web_state.crm.conversation_for_chat("incoming-account", "U-customer")
    removed = {}

    def remove_once():
        if not removed:
            removed["result"] = web_state.delete_account("incoming-account", owner)

    if action == "close":
        real_read = web_state.crm._chat_conversation

        def read_then_removed(owner_id, scope, body, *, ensure):
            row = real_read(owner_id, scope, body, ensure=ensure)
            remove_once()
            return row

        web_state.crm._chat_conversation = read_then_removed
    else:
        real_members = web_state.crm._member_map

        def members_then_removed(owner_id):
            members = real_members(owner_id)  # read before: the claimer may reply here
            remove_once()
            return members

        web_state.crm._member_map = members_then_removed
    body = {"accountId": "incoming-account", "chatId": "U-customer"}
    with pytest.raises(CrmError):
        if action == "close":
            web_state.crm.set_status(owner, {**body, "status": "closed"})
        else:
            web_state.crm.assign(owner, {**body, "assignee": "me"})
    assert removed
    assert web_state.crm.db.scalar(
        "SELECT COUNT(*) FROM crm_events WHERE conversation_id = ?", (conversation["id"],)
    ) == 0


def test_a_session_renewal_after_its_account_was_removed_saves_nothing(web_state):
    # The session keeper opened the account's client, then the account was removed
    # before the renewal took the account's lock: nothing is renewed or saved (saving
    # would write the removed account's sign-in back to disk), and nothing is logged.
    _seed_account(web_state, "acct")
    done = []

    class FakeAuth:
        def refresh_access_token(self):
            done.append("renewed")

    class FakeApi:
        auth = FakeAuth()
        _token_refresh_lock = threading.RLock()

        def _session_save_hook(self):
            done.append("saved")

    logs = len(web_state.store.get("bot_logs", {"logs": []})["logs"])
    web_state._commit_account_removal("acct")
    assert web_state._refresh_account_session("acct", FakeApi(), revived=False) is False
    assert done == []
    assert len(web_state.store.get("bot_logs", {"logs": []})["logs"]) == logs


def test_a_chat_seen_waiting_keeps_its_place_when_it_writes_more_than_line_shows(
    web_state,
):
    # A customer began to wait a minute ago, and was seen to; another, answered long
    # ago, then sends an album. The first stays ahead when it writes 9 more messages,
    # so that LINE no longer shows its first one: its start was seen, not bounded.
    owner, _recorder, poll = _line_auto_reply_with_recorder(web_state, chats=())
    fake = web_state.get_api("auto-account")
    now = int(time.time() * 1000)
    enabled_at = now - 3_600_000
    data = web_state._ai_auto_reply_all()
    data["accounts"]["auto-account"]["enabledAtMs"] = enabled_at
    web_state.store.set("ai_auto_reply", data)
    for index in range(2):
        _queue_telegram_reply(web_state, owner, index)  # both places taken
    first = [{"id": "w-1", "from": "U-w", "text": "hi", "contentType": 0,
              "createdTime": now - 60_000}]
    answered = [{"id": "x-old", "from": "U-x", "text": "thanks", "contentType": 0,
                 "createdTime": enabled_at + 1000}]
    web_state._save_ai_auto_reply_cursors(
        "auto-account", enabled_at, {"U-x": {"time": enabled_at + 1000, "ids": ["x-old"]}}
    )
    fake.boxes = {"U-w": first, "U-x": answered}
    poll()
    assert _ready_chats(web_state, owner) == ["U-w"]
    album = [{"id": f"x-{n}", "from": "U-x", "text": f"photo {n}", "contentType": 0,
              "createdTime": now - 2500 + 200 * n} for n in range(11)]
    fake.boxes = {"U-x": answered + album, "U-w": first}
    poll()
    assert _ready_chats(web_state, owner) == ["U-w", "U-x"]
    more = [{"id": f"w-{n}", "from": "U-w", "text": f"again {n}", "contentType": 0,
             "createdTime": now - 2000 + 150 * n} for n in range(2, 11)]
    fake.boxes = {"U-w": first + more, "U-x": answered + album}
    poll()
    assert _ready_chats(web_state, owner) == ["U-w", "U-x"]


def test_a_line_oa_worker_and_the_inbox_go_on_when_the_log_cannot_be_written(
    web_state, monkeypatch
):
    # The server log is a file on the full disk: a LINE OA worker's note about a failed
    # stream, and the inbox's note about a failed refresh, cannot be written either.
    from okline.lineoa.channels import OAChannelWorker

    class FullDisk:
        def write(self, text):
            raise OSError(28, "No space left on device")

        def flush(self):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(sys, "stdout", FullDisk())
    monkeypatch.setattr(sys, "stderr", FullDisk())
    worker = OAChannelWorker.__new__(OAChannelWorker)
    worker.logged = {}
    worker.account_id = "oa1"
    worker._log("the event stream dropped")  # no raise
    web_state._note_inbox_refresh_failure("acct", OSError("LINE did not answer"))


def test_a_session_line_renews_after_its_account_was_removed_is_not_saved(
    web_state, tmp_path
):
    # LINE renewed the session inside a call that was running as the account was
    # removed (a check for new messages, say): the renewal that ends then does not
    # write the removed account's sign-in back to disk.
    token_file = tmp_path / "acct.tokens"
    api = OkLine(access_token="old-access", refresh_token="old-refresh", mid="U-me", record=False)
    try:
        _seed_account(web_state, "acct")
        web_state.account_store.data["accounts"][0]["tokenFile"] = str(token_file)
        web_state.account_store.save()
        web_state._bind_account_session(api, "acct", str(token_file))
        web_state._commit_account_removal("acct")
        api.tokens.access_token = "renewed-access"
        assert api._session_save_hook is not None
        api._session_save_hook()
        assert not token_file.exists()
    finally:
        api.close()


def test_a_session_line_renews_as_its_account_is_removed_is_not_left_on_disk(
    web_state, tmp_path, monkeypatch
):
    # The renewal found the account listed and began to save; the removal committed
    # meanwhile and deleted the file before the write: the file goes again.
    token_file = tmp_path / "acct.tokens"
    api = OkLine(access_token="old-access", refresh_token="old-refresh", mid="U-me", record=False)
    try:
        _seed_account(web_state, "acct")
        web_state.account_store.data["accounts"][0]["tokenFile"] = str(token_file)
        web_state.account_store.save()
        web_state._bind_account_session(api, "acct", str(token_file))
        real_save = web_state._save_account_tokens

        def removal_lands_first(*args, **kwargs):
            web_state._commit_account_removal("acct")
            return real_save(*args, **kwargs)

        monkeypatch.setattr(web_state, "_save_account_tokens", removal_lands_first)
        api.tokens.access_token = "renewed-access"
        assert api._session_save_hook is not None
        api._session_save_hook()
        assert not token_file.exists()
    finally:
        api.close()


def _saved_access_token(state, token_file):
    with open(token_file, "rb") as handle:
        raw = handle.read()
    return json.loads(state.secret_cipher.decrypt(raw[len(b"fernet:"):]))["accessToken"]


def test_a_session_line_renews_as_its_account_is_added_again_keeps_the_new_sign_in(
    web_state, monkeypatch
):
    # The account went while its old LINE client saved a renewal, and the same LINE
    # account is added again by the same name (the same id, so the same file) as that
    # renewal finds it gone: the new sign-in stays on disk.
    profile = {"mid": "U-me-12345678", "displayName": "Shop"}
    old = OkLine(access_token="old-access", refresh_token="old-refresh", mid=profile["mid"],
                 record=False)
    new = OkLine(access_token="new-access", refresh_token="new-refresh", mid=profile["mid"],
                 record=False)
    store = web_state.account_store
    try:
        real_save = web_state._save_account_tokens
        real_listed = web_state._account_listed
        account = store.add_from_api(old, profile, "Shop", "owner", token_writer=real_save)
        token_file = str(account["tokenFile"])
        web_state._bind_account_session(old, account["id"], token_file)
        added = threading.Thread(
            target=store.add_from_api,
            args=(new, profile, "Shop", "owner"),
            kwargs={"token_writer": real_save},
        )
        armed = []

        def removal_lands_during_the_save(*args, **kwargs):
            real_save(*args, **kwargs)
            web_state._commit_account_removal(account["id"])
            armed.append(True)

        def added_again_as_it_looks(account_id):
            listed = real_listed(account_id)
            if armed and not listed and added.ident is None:
                added.start()
                added.join(timeout=0.5)  # done at once, unless it waits for the list
            return listed

        monkeypatch.setattr(web_state, "_save_account_tokens", removal_lands_during_the_save)
        monkeypatch.setattr(web_state, "_account_listed", added_again_as_it_looks)
        old.tokens.access_token = "renewed-access"
        assert old._session_save_hook is not None
        old._session_save_hook()
        added.join(timeout=5)
        assert added.ident is not None and not added.is_alive()
        assert [item["id"] for item in store.data["accounts"]] == [account["id"]]
        assert os.path.exists(token_file)
        assert _saved_access_token(web_state, token_file) == "new-access"
    finally:
        old.close()
        new.close()


def test_an_account_added_again_as_its_removal_ends_keeps_its_sign_in(
    web_state, monkeypatch
):
    # The same LINE account is added again by the same name (the same id, so the same
    # file) after its removal took it off the list and before the removal deleted its
    # sign-in: the new sign-in stays on disk.
    profile = {"mid": "U-me-12345678", "displayName": "Shop"}
    old = OkLine(access_token="old-access", refresh_token="old-refresh", mid=profile["mid"],
                 record=False)
    new = OkLine(access_token="new-access", refresh_token="new-refresh", mid=profile["mid"],
                 record=False)
    store = web_state.account_store
    try:
        save = web_state._save_account_tokens
        account = store.add_from_api(old, profile, "Shop", "owner", token_writer=save)
        real_close = web_state.close_api

        def added_again_first(account_id):
            real_close(account_id)
            store.add_from_api(new, profile, "Shop", "owner", token_writer=save)

        monkeypatch.setattr(web_state, "close_api", added_again_first)
        web_state._commit_account_removal(account["id"])
        assert [item["id"] for item in store.data["accounts"]] == [account["id"]]
        assert os.path.exists(account["tokenFile"])
        assert _saved_access_token(web_state, account["tokenFile"]) == "new-access"
    finally:
        old.close()
        new.close()
