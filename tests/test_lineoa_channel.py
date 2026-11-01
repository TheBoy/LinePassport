"""LINE Official Accounts (through OA Manager) as customer chat accounts."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator
from types import SimpleNamespace

import pytest
import requests

from okline import webapp
from okline.crm import CrmError
from okline.lineoa import OAManagerError, OAManagerLoginError, normalize_event
from okline.webapp import OkLineWebHandler, OkLineWebServer, WebConfig, WebError, WebState

BOT = "U" + "a" * 32
BOT2 = "U" + "c" * 32
CHAT = "U" + "b" * 32
COOKIE_VALUE = "very-secret-session"


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


class FakeOA:
    """Stands in for OAManagerClient; the class keeps what every instance did."""

    sent: list[tuple] = []
    reads: list[tuple] = []
    fail_send: OAManagerError | None = None
    refresh_ok = False

    def __init__(self, cookies=None, proxy=None):
        self.cookies_in = cookies
        self.base_url = "https://chat.line.biz"

    # what the stream worker uses: nothing to sync, and no stream to open
    def chats(self, bot_id, *, next_cursor=None, limit=25):
        return [], ""

    def messages(self, bot_id, chat_id, *, backward=None):
        return [], ""

    def chat(self, bot_id, chat_id):
        return {}

    def streaming_token(self, bot_id):
        raise OAManagerError("offline", network=True)

    def send_text(self, bot_id, chat_id, text, *, send_id=None):
        if FakeOA.fail_send is not None:
            error, FakeOA.fail_send = FakeOA.fail_send, None
            raise error
        FakeOA.sent.append(("text", bot_id, chat_id, text))
        return f"{chat_id}_1700000000000_{len(FakeOA.sent):08d}"

    def send_file(self, bot_id, chat_id, data, filename, *, send_id=None):
        FakeOA.sent.append(("file", bot_id, chat_id, filename))
        return f"{chat_id}_1700000000000_{len(FakeOA.sent):08d}"

    def mark_read(self, bot_id, chat_id, message_id, timestamp):
        FakeOA.reads.append((bot_id, chat_id, message_id))

    def content(self, bot_id, content_hash, *, preview=False):
        if content_hash == "img-hash":
            return b"\xff\xd8\xff\xe0" + b"0" * 64, "image/jpeg"
        if content_hash == "html-hash":
            return b"<html><script>alert(1)</script></html>", "text/html"
        raise OAManagerError("gone", status=404)

    def refresh_session(self):
        return FakeOA.refresh_ok

    def cookies(self):
        return [{"name": "ses", "value": "renewed", "domain": "chat.line.biz", "path": "/"}]

    def close(self):
        return None


def _result(*bots):
    return SimpleNamespace(
        cookies=[{"name": "ses", "value": COOKIE_VALUE, "domain": "chat.line.biz", "path": "/"}],
        me={"name": "Toon"},
        bots=[
            {"botId": bot[0], "name": bot[1], "basicSearchId": bot[2],
             "responseMode": bot[3] if len(bot) > 3 else "CHAT",
             "iconUrl": "https://profile.line-scdn.net/x", "userPermissionType": "OPERATOR"}
            for bot in bots
        ],
    )


HOTMAN = (BOT, "Hotman", "@065mkrte")


@pytest.fixture
def state(tmp_path, monkeypatch) -> Iterator[WebState]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    FakeOA.sent, FakeOA.reads, FakeOA.fail_send, FakeOA.refresh_ok = [], [], None, False
    web = WebState(_make_config(tmp_path))
    web.line_oa.client_factory = FakeOA
    try:
        yield web
    finally:
        web.close()


def _owner(state) -> dict:
    state.web_auth.setup("owner@example.com", "owner-pass")
    return state.web_auth._find_user(state.web_auth.data["users"][0]["id"])


def _add_oa(state, owner) -> str:
    return state._oa_add_bots(owner["id"], _result(HOTMAN), [BOT])[0]


def _event(kind="message", text="ทดสอบ", message_id="m1", **extra) -> dict:
    event = {
        "type": kind,
        "source": {"chatId": CHAT, "userId": CHAT},
        "timestamp": int(time.time() * 1000),
        "message": {"id": message_id, "type": "text", "text": text},
        **extra,
    }
    return normalize_event(event, names={CHAT: "Toon"})


def test_adding_an_oa_creates_a_safe_account(state):
    owner = _owner(state)
    account_id = _add_oa(state, owner)
    assert account_id == "oa-065mkrte"
    account = state.account_store.get(account_id)
    assert account["channel"] == "line_oa" and account["botId"] == BOT
    with open(account["credentialFile"], encoding="utf-8") as handle:
        assert COOKIE_VALUE not in handle.read()
    assert state.oa_cookies(account_id)[0]["value"] == COOKIE_VALUE
    listed = next(a for a in state.account_store.list_accounts() if a["id"] == account_id)
    assert "credentialFile" not in listed and listed["capabilities"] == []
    assert listed["userid"] == "@065mkrte" and listed["sessionState"] == "connected"
    assert state.line_oa.running(account_id)
    with pytest.raises(WebError) as exc:
        _add_oa(state, owner)
    assert exc.value.code == "line_oa_exists"


def test_messages_flow_through_crm_inbox_and_kpi(state):
    owner = _owner(state)
    account_id = _add_oa(state, owner)
    before = state.inbox_version(account_id)
    state.oa_receive(account_id, _event(text="มีสินค้าไหม"))
    assert state.inbox_version(account_id) > before
    assert state.room_states(account_id)[CHAT]["status"] == "waiting"
    chats = state.tg_conversations(account_id)["conversations"]
    assert chats[0]["mid"] == CHAT and chats[0]["unreadCount"] == 1
    assert state.tg_contacts(account_id, {})["contacts"][0]["name"] == "Toon"

    sent = state.tg_send(account_id, {"to": CHAT, "text": "มีค่ะ"}, owner)
    assert FakeOA.sent[-1] == ("text", BOT, CHAT, "มีค่ะ")
    room = state.room_states(account_id)[CHAT]
    assert room["status"] == "answered" and room["answeredBy"] == owner["id"]
    # OA Manager echoes our send back under the same id: still one copy.
    state.oa_receive(account_id, _event("messageSent", text="มีค่ะ", message_id="m2",
                                        sendId=sent["messageId"], bizId="biz-1"))
    messages = state.tg_messages(account_id, {"chat_mid": [CHAT]})["messages"]
    assert [m["text"] for m in messages] == ["มีสินค้าไหม", "มีค่ะ"]
    assert messages[0]["from"] == CHAT and messages[1]["from"] == BOT
    conv = state.crm.conversation_for_chat(account_id, CHAT)
    assert conv["assigned_to"] == owner["id"] and conv["unread"] == 0

    with pytest.raises(WebError) as exc:
        state.tg_send(account_id, {"to": "tgu1", "text": "hi"}, owner)
    assert exc.value.code == "line_oa_chat_invalid"


def test_replies_from_oa_manager_and_auto_replies_are_told_apart(state):
    owner = _owner(state)
    account_id = _add_oa(state, owner)
    state.oa_receive(account_id, _event(message_id="in-1"))
    state.oa_receive(account_id, _event("messageSent", text="สวัสดีค่ะ", message_id="auto-1",
                                        bizId="__AUTO_RESPONSE"))
    assert state.room_states(account_id)[CHAT]["status"] == "waiting"  # not a person
    state.oa_receive(account_id, _event("messageSent", text="ได้ค่ะ", message_id="out-1",
                                        sendId=f"{CHAT}_1_00000001", bizId="biz-staff"))
    room = state.room_states(account_id)[CHAT]
    assert room["status"] == "answered" and room["answeredBy"] == "line_app"
    kinds = state.crm.db.query(
        "SELECT external_id, sender_kind FROM crm_messages WHERE direction = 'out' ORDER BY at"
    )
    assert {row["external_id"]: row["sender_kind"] for row in kinds} == {
        "auto-1": "system", f"{CHAT}_1_00000001": "app",
    }


def test_backfill_keeps_history_without_alerts(state):
    owner = _owner(state)
    account_id = _add_oa(state, owner)
    state.oa_backfill(account_id, [_event(message_id="old-1"), _event("messageSent", message_id="old-2")])
    assert CHAT not in state.room_states(account_id)
    assert state.crm.conversation_for_chat(account_id, CHAT)["unread"] == 0
    assert len(state.tg_messages(account_id, {"chat_mid": [CHAT]})["messages"]) == 2


def test_media_reads_and_avatar(state, monkeypatch):
    owner = _owner(state)
    account_id = _add_oa(state, owner)
    image = normalize_event({"type": "message", "source": {"chatId": CHAT}, "timestamp": 1,
                             "message": {"id": "p1", "type": "image",
                                         "contentProvider": {"type": "line", "contentHash": "img-hash"}}})
    image["at"] = int(time.time() * 1000)
    state.oa_receive(account_id, image)
    data, filename, content_type = state.tg_media(account_id, CHAT, "p1")
    assert data.startswith(b"\xff\xd8") and content_type == 1 and filename == "p1.jpg"
    state.tg_mark_read(account_id, {"chatMid": CHAT})
    deadline = time.time() + 5
    while not FakeOA.reads and time.time() < deadline:
        time.sleep(0.02)
    assert FakeOA.reads == [(BOT, CHAT, "p1")]
    for url in ("http://evil.test/x.png", "https://evilline-scdn.net/x.png",
                "http://profile.line-scdn.net/x.png"):
        with pytest.raises(WebError) as exc:
            state._oa_avatar({"iconUrl": url})  # only LINE's own image host, over https
        assert exc.value.code == "profile_picture_not_found"


def test_an_ended_session_asks_for_a_new_scan_unless_line_signs_in_again(state):
    owner = _owner(state)
    account_id = _add_oa(state, owner)
    FakeOA.fail_send, FakeOA.refresh_ok = OAManagerError("ended", status=401), True
    state.tg_send(account_id, {"to": CHAT, "text": "hi"}, owner)
    assert state.oa_cookies(account_id)[0]["value"] == "renewed"
    assert state.account_store.session_state(account_id) == "connected"

    FakeOA.fail_send, FakeOA.refresh_ok = OAManagerError("ended", status=401), False
    with pytest.raises(WebError) as exc:
        state.tg_send(account_id, {"to": CHAT, "text": "hi"}, owner)
    assert exc.value.code == "line_oa_reauth"
    assert state.account_store.session_state(account_id) == "reauth_required"
    assert not state.line_oa.running(account_id)


class FakeLogin:
    result = _result(HOTMAN)
    error: OAManagerLoginError | None = None
    seen_cookies: list = []

    def __init__(self, *, cookies=None, **kwargs):
        FakeLogin.seen_cookies.append(cookies)
        self.trace = []
        self.client = FakeOA()

    def run(self, on_qr, on_pin, *, wait_seconds=0, should_stop=lambda: False):
        on_qr(b"PNG", "image/png")
        on_pin("4821")
        if FakeLogin.error is not None:
            raise FakeLogin.error
        return FakeLogin.result


def _wait_for(state, owner, wanted):
    deadline = time.time() + 5
    while time.time() < deadline:
        login = state.oa_login_state(owner)
        if login["state"] in wanted:
            return login
        time.sleep(0.02)
    raise AssertionError(state.oa_login_state(owner))


def test_signing_in_adds_the_only_oa_or_offers_a_choice(state, monkeypatch):
    monkeypatch.setattr(webapp, "OAManagerQrLogin", FakeLogin)
    owner = _owner(state)
    FakeLogin.result, FakeLogin.error = _result(HOTMAN), None
    state.oa_login_start({}, owner)
    login = _wait_for(state, owner, {"done", "error"})
    assert login["state"] == "done" and login["accountId"] == "oa-065mkrte"

    FakeLogin.result = _result(HOTMAN, (BOT2, "Second", "@second", "BOT"))
    state.oa_login_start({}, owner)
    login = _wait_for(state, owner, {"pick", "error"})
    assert [(b["botId"], b["connected"], b["chatOff"]) for b in login["bots"]] == [
        (BOT, True, False), (BOT2, False, True)]
    assert COOKIE_VALUE not in str(login)
    picked = state.oa_login_pick({"botIds": [BOT2]}, owner)
    assert picked["state"] == "done" and picked["account"]["channel"] == "line_oa"
    # Chat mode is off for that OA in OA Manager: the account says so until it works.
    assert picked["account"]["channelProblem"] == "chat_off"
    state.oa_problem(picked["account"]["id"], "")
    assert "channelProblem" not in state.account_store.get(picked["account"]["id"])
    with pytest.raises(WebError) as exc:
        state.oa_login_pick({"botIds": [BOT2]}, owner)
    assert exc.value.code == "line_oa_login_expired"

    FakeLogin.error = OAManagerLoginError("No Business ID", "no_business_id")
    state.oa_login_start({}, owner)
    login = _wait_for(state, owner, {"error"})
    assert login["errorCode"] == "line_oa_no_business_id" and login["qrImage"] is None


def test_reconnecting_replaces_the_session_of_that_oa_only(state, monkeypatch):
    monkeypatch.setattr(webapp, "OAManagerQrLogin", FakeLogin)
    owner = _owner(state)
    account_id = _add_oa(state, owner)
    state.oa_auth_failed(account_id)
    FakeLogin.result, FakeLogin.error, FakeLogin.seen_cookies = _result(HOTMAN), None, []
    FakeLogin.result.cookies = [{"name": "ses", "value": "fresh", "domain": "chat.line.biz", "path": "/"}]
    state.oa_login_start({"reconnectAccountId": account_id}, owner)
    assert _wait_for(state, owner, {"done", "error"})["state"] == "done"
    assert FakeLogin.seen_cookies[-1][0]["value"] == COOKIE_VALUE  # LINE may skip the QR
    assert state.oa_cookies(account_id)[0]["value"] == "fresh"
    assert state.account_store.session_state(account_id) == "connected"
    assert state.line_oa.running(account_id)

    FakeLogin.result = _result((BOT2, "Second", "@second"))
    state.oa_login_start({"reconnectAccountId": account_id}, owner)
    login = _wait_for(state, owner, {"error", "done"})
    assert login["errorCode"] == "line_oa_mismatch"


def test_segment_sends_are_not_offered_for_line_oa(state):
    owner = _owner(state)
    account_id = _add_oa(state, owner)
    with pytest.raises(CrmError) as exc:
        state.crm.create_broadcast(owner, {"accountId": account_id, "text": "โปรใหม่"})
    assert exc.value.code == "broadcast_line_oa"


def test_removing_the_account_cleans_up(state):
    owner = _owner(state)
    account_id = _add_oa(state, owner)
    state.oa_receive(account_id, _event())
    credential_file = state.account_store.get(account_id)["credentialFile"]
    state.delete_account(account_id, owner)
    assert not os.path.exists(credential_file)
    assert not state.line_oa.running(account_id)
    assert state.crm.conversation_for_chat(account_id, CHAT) is None


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    FakeOA.sent, FakeOA.reads, FakeOA.fail_send, FakeOA.refresh_ok = [], [], None, False
    server = OkLineWebServer(("127.0.0.1", 0), OkLineWebHandler)
    server.state = WebState(_make_config(tmp_path))
    server.state.line_oa.client_factory = FakeOA
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.state
    finally:
        server.shutdown()
        server.state.close()
        server.server_close()


def test_chat_routes_serve_line_oa_accounts(live):
    base, state = live
    session = requests.Session()
    session.post(f"{base}/api/auth/setup",
                 json={"email": "owner@example.com", "password": "owner-pass"}, timeout=5).raise_for_status()
    owner = state.web_auth._find_user(state.web_auth.data["users"][0]["id"])
    assert session.get(f"{base}/api/lineoa/login/state", timeout=5).json() == {"state": "idle"}
    account_id = _add_oa(state, owner)
    state.oa_receive(account_id, _event(text="ราคาเท่าไหร่"))
    for message_id, content_hash, kind in (("p1", "img-hash", "image"), ("f1", "html-hash", "file")):
        event = normalize_event({"type": "message", "source": {"chatId": CHAT},
                                 "timestamp": int(time.time() * 1000),
                                 "message": {"id": message_id, "type": kind, "fileName": "evil.html",
                                             "contentHash": content_hash}})
        state.oa_receive(account_id, event)

    q = f"accountId={account_id}"
    convs = session.get(f"{base}/api/conversations?{q}", timeout=5)
    assert convs.status_code == 200 and convs.json()["conversations"][0]["mid"] == CHAT
    boxes = session.get(f"{base}/api/boxes?{q}", timeout=5)
    assert boxes.status_code == 400 and boxes.json()["code"] == "line_only"
    sent = session.post(f"{base}/api/send", json={"accountId": account_id, "to": CHAT, "text": "ok"}, timeout=5)
    assert sent.status_code == 200, sent.text
    image = session.get(f"{base}/api/message-content?{q}&chat_mid={CHAT}&message_id=p1", timeout=5)
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    document = session.get(f"{base}/api/message-content?{q}&chat_mid={CHAT}&message_id=f1", timeout=5)
    assert document.headers["content-type"] == "application/octet-stream"
    assert document.headers["content-disposition"].startswith("attachment")
    status = session.get(f"{base}/api/status?{q}", timeout=5).json()
    assert status["channel"] == "line_oa" and status["authenticated"] is True
    assert status["profile"]["statusMessage"] == "@065mkrte"
    schedule = session.post(f"{base}/api/schedules/create", json={
        "accountId": account_id, "to": CHAT, "text": "x", "mode": "once",
        "runAt": "2030-01-01T09:00"}, timeout=5)
    assert schedule.status_code == 400 and schedule.json()["code"] == "line_only"
    inbox = session.get(f"{base}/api/inbox", timeout=5).json()
    row = next(chat for chat in inbox["chats"] if chat["accountId"] == account_id)
    assert row["channel"] == "line" and row["name"] == "Toon"
