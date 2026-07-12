from __future__ import annotations

import base64
import datetime as dt
import threading
from collections.abc import Iterator
from html.parser import HTMLParser
from http import HTTPStatus

import pytest
import requests

from okline import webapp as webapp_module
from okline.webapp import (
    GOD_HTML,
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


def test_add_account_wizard_uses_line_name_and_large_pin_display():
    assert "<title>LinePassport</title>" in INDEX_HTML
    assert "versionLabel" not in INDEX_HTML
    assert "OkLine Web" not in INDEX_HTML
    assert "OkLine God" not in GOD_HTML
    assert "LinePassport God" in GOD_HTML
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
    assert INDEX_HTML.index('id="loadGroupsButton"') < INDEX_HTML.index('id="contactsRefreshButton"') < INDEX_HTML.index('id="contactSearchRow"')


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


def test_web_ui_has_bot_log_panel():
    assert 'id="botLogList"' in INDEX_HTML
    assert 'id="botLogRefreshButton"' in INDEX_HTML
    assert 'id="botLogClearButton"' in INDEX_HTML
    assert "compactLogValue" in INDEX_HTML
    assert "log-detail" in INDEX_HTML
    assert ".bot-log-item {\n      display: flex;" in INDEX_HTML
    assert "-webkit-line-clamp" not in INDEX_HTML
    assert "/api/bot/logs" in INDEX_HTML

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
            if node_id in {"tabPanelLine", "tabPanelTools", "tabPanelBot", "tabPanelAi"}:
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
    assert parser.parents["tabPanelBot"][-2] == "tab-panels"
    assert parser.parents["tabPanelAi"][-2] == "tab-panels"
    assert parser.parents["tabPanelTools"][-2] == "tab-panels"
    assert 'id="authModeToggle"' in INDEX_HTML
    assert '"/api/auth/register"' in INDEX_HTML
    assert 'id="webUsernameInput" type="email"' in INDEX_HTML
    assert 'id="registrationNameInput"' in INDEX_HTML
    assert 'id="newUserEmail" type="email"' in INDEX_HTML
    assert 'displayName: mode === "register"' in INDEX_HTML
    assert 'select.value = "member"' in INDEX_HTML
    assert 'accountIds: checkedAccountIds' not in INDEX_HTML
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
    assert 'document.body.classList.toggle("login-page-scroll", view === "login")' in INDEX_HTML

    require_start = INDEX_HTML.index("function requireAccount")
    require_end = INDEX_HTML.index("function accountQuery", require_start)
    require_block = INDEX_HTML[require_start:require_end]
    assert 'if (!state.accounts.length) return ""' in require_block
    assert require_block.index(
        'if (!state.accounts.length) return ""'
    ) < require_block.index("showGate(false)")
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

    with pytest.raises(WebError) as regular_login:
        auth.login("god", "god-password")
    assert regular_login.value.status == HTTPStatus.UNAUTHORIZED

    token = auth.login_god("god", "god-password")
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


def test_god_portal_is_separate_and_management_only(god_live_server):
    page = requests.get(f"{god_live_server}/god", timeout=5)
    assert page.status_code == 200
    assert "LinePassport God" in page.text
    assert "/api/god/users/detail" in page.text
    assert "/api/accounts/switch" not in page.text

    regular = requests.Session()
    denied = regular.post(
        f"{god_live_server}/api/auth/login",
        json={"username": "god", "password": "god-password"},
        timeout=5,
    )
    assert denied.status_code == HTTPStatus.UNAUTHORIZED

    god = requests.Session()
    logged_in = god.post(
        f"{god_live_server}/api/god/login",
        json={"username": "god", "password": "god-password"},
        timeout=5,
    )
    assert logged_in.status_code == 200
    assert WebAuth.god_cookie_name in god.cookies
    assert WebAuth.cookie_name not in god.cookies

    users = god.get(f"{god_live_server}/api/god/users", timeout=5)
    assert users.status_code == 200
    payload = users.json()
    assert [user["email"] for user in payload["users"]] == ["owner@example.com"]
    assert "god" not in payload["roles"]

    main_api = god.get(f"{god_live_server}/api/accounts", timeout=5)
    assert main_api.status_code == HTTPStatus.UNAUTHORIZED


def test_god_portal_html_has_only_user_management_actions():
    assert 'id="loginView"' in GOD_HTML
    assert 'id="userTable"' in GOD_HTML
    assert 'id="editDialog"' in GOD_HTML
    assert 'id="deleteDialog"' in GOD_HTML
    assert '"/api/god/login"' in GOD_HTML
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
    assert auth.can_access_account(user, "acct-1") is True
    assert auth.can_access_account(user, "acct-2") is False


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
    assert [p["name"] for p in web_state.list_patterns(member_a)["patterns"]] == [
        "A only"
    ]
    assert [p["name"] for p in web_state.list_patterns(member_b)["patterns"]] == [
        "B only"
    ]

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


class _RecentChatFakeApi:
    def get_message_boxes(self, *, limit=100, last_messages_per_box=5):
        return {
            "messageBoxes": [
                {"id": "c_new", "lastMessages": [{"createdTime": "3000"}]},
                {"id": "u_new", "lastMessages": [{"createdTime": "2500"}]},
                {"id": "u_old", "lastMessages": [{"createdTime": "1000"}]},
                {"id": "c_old", "lastMessages": [{"createdTime": "500"}]},
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


def test_contacts_and_groups_sort_by_recent_chat():
    handler = OkLineWebHandler.__new__(OkLineWebHandler)
    api = _RecentChatFakeApi()

    contacts = handler._contacts(api, {"limit": ["10"]})["contacts"]
    groups = handler._groups(api, {"limit": ["10"]})["groups"]

    assert [contact["mid"] for contact in contacts] == ["u_new", "u_old", "u_never"]
    assert [group["mid"] for group in groups] == ["c_new", "c_old", "c_never"]


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


def test_sniff_content_type_detects_images():
    assert webapp_module._sniff_content_type(bytes.fromhex("ffd8ffe0")) == "image/jpeg"
    assert webapp_module._sniff_content_type(b"\x89PNG\r\n\x1a\n") == "image/png"
    assert webapp_module._sniff_content_type(b"GIF89a1234") == "image/gif"
    assert webapp_module._sniff_content_type(b"RIFF\x00\x00\x00\x00WEBPxxxx") == "image/webp"
    assert webapp_module._sniff_content_type(b"nope") == "application/octet-stream"


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
    assert initial["categories"] == [
        {"id": "general", "name": "General", "system": True}
    ]
    created = web_state.create_pattern({"name": "greeting", "text": "hi {1D}"})
    pid = created["pattern"]["id"]
    items = web_state.list_patterns()["patterns"]
    assert len(items) == 1
    assert items[0]["name"] == "greeting"
    assert items[0]["text"] == "hi {1D}"
    with pytest.raises(WebError):
        web_state.create_pattern({"name": "", "text": "x"})
    with pytest.raises(WebError):
        web_state.create_pattern({"name": "x", "text": "  "})
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

    updated = web_state.update_pattern_category(
        {"id": category["id"], "name": "Campaigns"}
    )["category"]
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

    category_a = web_state.create_pattern_category(
        {"name": "Private A"}, member_a
    )["category"]
    assert [item["name"] for item in web_state.list_patterns(member_b)["categories"]] == [
        "General"
    ]
    with pytest.raises(WebError):
        web_state.create_pattern(
            {"name": "Wrong tenant", "text": "x", "categoryId": category_a["id"]},
            member_b,
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
    web_state.delete_pattern(pattern["id"], "acct")

    logs = web_state.list_bot_logs(
        "acct", {"role": "admin", "accountIds": ["acct"]}
    )["logs"]
    assert [log["action"] for log in logs[:4]] == [
        "pattern.delete",
        "pattern.create",
        "schedule.pause",
        "schedule.create",
    ]
    assert {log["accountId"] for log in logs} == {"acct"}

    scoped_admin = {"role": "admin", "accountIds": ["acct"]}
    cleared = web_state.clear_bot_logs("acct", scoped_admin)
    assert cleared["logs"][0]["action"] == "logs.clear"
    assert web_state.list_bot_logs("acct", scoped_admin)["logs"][0]["action"] == (
        "logs.clear"
    )


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
    logs = web_state.list_bot_logs(
        "acct", {"role": "admin", "accountIds": ["acct"]}
    )["logs"]
    assert logs[0]["action"] == "schedule.clear_stuck"


def test_bot_log_detail_sanitizes_long_line_media_urls(web_state):
    entry = web_state._bot_log_entry(
        "send.item.error",
        account_id="acct",
        detail=(
            "request to https://obs.line-apps.com/r/talk/m/622310832885727233 "
            "failed: timeout"
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

    detail = web_state.list_bot_logs(
        "acct", {"role": "admin", "accountIds": ["acct"]}
    )["logs"][0]["detail"]
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

    listed = web_state.list_bot_logs(
        "acct", {"role": "admin", "accountIds": ["acct"]}
    )["logs"][0]

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
    auth.ensure_simple_mode()

    auth.secure_with_password("owner@example.com", "owner-pass")

    assert auth.simple_mode() is False
    new_token = auth.login("owner@example.com", "owner-pass")
    assert auth.is_authenticated(WebAuth.cookie_header(new_token)) is True


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
def test_web_ui_has_ai_settings_tab_and_source():
    # Top tab bar gains an AI Settings tab + panel.
    assert 'data-tab="ai"' in INDEX_HTML
    assert 'data-tab-panel="ai"' in INDEX_HTML
    assert '"tabs.ai"' in INDEX_HTML
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


def test_web_ui_bot_dates_display_dd_mm_yyyy():
    # Each scheduler date keeps its native yyyy-mm-dd picker plus a dd/mm/yyyy
    # text mirror, so the shown format is independent of the browser locale.
    for base in ("scheduleRunAtDate", "scheduleActiveFrom", "scheduleActiveUntil"):
        assert f'id="{base}Text"' in INDEX_HTML  # the dd/mm/yyyy display
        assert f'id="{base}"' in INDEX_HTML  # the native picker (stores yyyy-mm-dd)
    assert 'class="date-native"' in INDEX_HTML
    assert '"scheduler.date_ph"' in INDEX_HTML
    assert "function isoToDmy" in INDEX_HTML


def test_web_ui_background_refreshes_bot_schedules_and_logs():
    assert "const BOT_BACKGROUND_REFRESH_MS = 3000" in INDEX_HTML
    assert "loadSchedules({background: true})" in INDEX_HTML
    assert "loadBotLogs({background: true})" in INDEX_HTML
    assert "setInterval(refreshBotInBackground, BOT_BACKGROUND_REFRESH_MS)" in INDEX_HTML
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

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["post"] += 1

        class R:
            status_code = 200

            def json(self):
                return {"code": 200, "msg": "success", "data": {"taskId": "task-123"}}

        return R()

    def fake_get(url, params=None, headers=None, timeout=None):
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
    monkeypatch.setattr(webapp_module, "_download_image", lambda url: (b"\x89PNG\r\n\x1a\nX", "out.png"))

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

    def fake_post(url, json=None, headers=None, timeout=None):
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
    assert captured["url"].endswith(
        "/v1beta/models/gemini-2.5-flash-image:generateContent"
    )
    assert captured["headers"]["x-goog-api-key"] == "secret"
    assert captured["body"]["contents"][0]["parts"][0]["text"] == "a cat"
    assert events == [
        (
            "content.ai.request",
            "Google Gemini · gemini-2.5-flash-image",
            True,
            {
                "provider": "google",
                "model": "gemini-2.5-flash-image",
                "endpoint": captured["url"],
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

    def fake_post(url, json=None, headers=None, timeout=None):
        calls["post"] += 1
        assert url.endswith("/api/v1/nanobanana/generate")
        assert headers["Authorization"] == "Bearer nb-key"
        assert json["type"] == "TEXTTOIAMGE"  # the vendor's verbatim typo
        assert json["callBackUrl"]  # required field is always sent
        assert json["image_size"] == "16:9"  # a concrete size passes through

        class R:
            status_code = 200

            def json(self):
                return {"code": 200, "msg": "success", "data": {"taskId": "t-1"}}

        return R()

    def fake_get(url, params=None, headers=None, timeout=None):
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

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, body=json)

        class R:
            status_code = 200

            def json(self):
                return {"code": 200, "message": "success", "data": {"taskId": "t-2"}}

        return R()

    def fake_get(url, params=None, headers=None, timeout=None):
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
    monkeypatch.setattr(webapp_module, "_download_image", lambda url: (b"\xff\xd8\xff\xe0", "out.jpg"))

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
                return {"code": 200, "data": {"successFlag": 3, "errorMessage": "nsfw blocked"}}

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

    def fake_post(url, json=None, headers=None, timeout=None):
        captured.update(url=url, body=json, headers=headers, timeout=timeout)
        return JsonResponse(
            202,
            {
                "request_id": "fal-request-1",
                "status_url": "https://queue.fal.run/status/fal-request-1",
                "response_url": "https://queue.fal.run/result/fal-request-1",
            },
        )

    def fake_get(url, params=None, headers=None, timeout=None):
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
        "prompt": "a green city",
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
        assert payload.get("image_size") == "landscape_16_9" or payload.get(
            "aspect_ratio"
        ) == "16:9"


def test_fal_model_payload_adapts_model_specific_schemas():
    nano = webapp_module._fal_model_payload(
        "draw", "fal-ai/nano-banana-2", "portrait_16_9"
    )
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
        {"apiKey": "abcd1234wxyz", "model": "gemini-2.5-flash-image"}
    )
    google = saved["providers"]["google"]
    assert saved["configured"] is True and google["hasApiKey"] is True
    assert google["apiKeyPreview"].endswith("wxyz")
    assert "abcd1234" not in google["apiKeyPreview"]  # raw key is never echoed back
    assert web_state.ai_settings(reveal=True)["apiKey"] == "abcd1234wxyz"

    # A blank key on save keeps the stored one but still updates the model.
    web_state.save_ai_settings({"model": "gemini-2.5-flash-image-preview"})
    assert web_state.ai_settings(reveal=True)["apiKey"] == "abcd1234wxyz"
    assert web_state.ai_settings()["providers"]["google"]["model"] == "gemini-2.5-flash-image-preview"

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
        f"{live_server}/api/ai/settings", json={"apiKey": "tok-98765432"}, timeout=5
    )
    assert saved.status_code == 200
    assert saved.json()["configured"] is True

    again = session.get(f"{live_server}/api/ai/settings", timeout=5).json()
    google = again["providers"]["google"]
    assert google["hasApiKey"] is True
    assert google["apiKeyPreview"].endswith("5432")
    assert "tok-98765432" not in google["apiKeyPreview"]
