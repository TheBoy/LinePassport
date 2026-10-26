"""Telegram bots as customer chat accounts."""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator

import pytest
import requests

from okline.telegram.bot_api import TelegramApiError
from okline.telegram.channels import normalize_message
from okline.webapp import OkLineWebHandler, OkLineWebServer, WebConfig, WebError, WebState

TOKEN = "555555555:" + "C" * 35
CUSTOMER_ID = 4242
CHAT = f"tgu{CUSTOMER_ID}"


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


class FakeTg:
    sent: list[tuple] = []
    blocked: set[int] = set()

    def __init__(self, token, proxy=None):
        self.token = token

    def get_me(self):
        if self.token != TOKEN:
            raise TelegramApiError("Unauthorized", error_code=401)
        return {"id": 555555555, "is_bot": True, "username": "shop_bot", "first_name": "Shop Bot"}

    def delete_webhook(self):
        return True

    def get_updates(self, offset=0, *, timeout=25, allowed=None):
        time.sleep(0.05)
        return []

    def send_message(self, chat_id, text, **kwargs):
        if chat_id in FakeTg.blocked:
            raise TelegramApiError("Forbidden: bot was blocked by the user", error_code=403)
        FakeTg.sent.append(("text", chat_id, text))
        return {"message_id": 900 + len(FakeTg.sent), "date": int(time.time())}

    def send_photo(self, chat_id, data, *, filename="image.jpg", caption=""):
        FakeTg.sent.append(("photo", chat_id, filename))
        return {"message_id": 900 + len(FakeTg.sent), "date": int(time.time()),
                "photo": [{"file_id": "ph-small", "width": 90}, {"file_id": "ph-big", "width": 800}]}

    def send_document(self, chat_id, data, *, filename="file", caption=""):
        FakeTg.sent.append(("document", chat_id, filename))
        return {"message_id": 900 + len(FakeTg.sent), "date": int(time.time()),
                "document": {"file_id": "doc-1"}}

    def get_file(self, file_id):
        return {"file_path": {"img-1": "photos/file_1.jpg", "doc-2": "documents/evil.html"}[file_id]}

    def download(self, path, max_bytes=0):
        return b"\xff\xd8\xff\xe0jpeg" if path.endswith(".jpg") else b"<script>alert(1)</script>"

    def user_photo_file_id(self, user_id):
        return ""

    def close(self):
        return None


@pytest.fixture
def state(tmp_path, monkeypatch) -> Iterator[WebState]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    FakeTg.sent = []
    FakeTg.blocked = set()
    web = WebState(_make_config(tmp_path))
    web.telegram.client_factory = FakeTg
    try:
        yield web
    finally:
        web.close()


def _owner(state) -> dict:
    state.web_auth.setup("owner@example.com", "owner-pass")
    return dict(state.web_auth.data["users"][0])


def _add_bot(state, owner) -> str:
    result = state.add_telegram_bot({"token": TOKEN, "label": "Shop TG"}, owner)
    return result["account"]["id"]


def _incoming(text="สวัสดี", message_id=1, **extra) -> dict:
    return normalize_message({
        "message_id": message_id,
        "date": int(time.time()),
        "chat": {"id": CUSTOMER_ID, "type": "private", "first_name": "Ploy", "username": "ploy"},
        "from": {"id": CUSTOMER_ID, "first_name": "Ploy", "username": "ploy"},
        "text": text,
        **extra,
    })


def test_normalize_message_kinds():
    base = {"message_id": 7, "date": 1_700_000_000,
            "chat": {"id": 1, "type": "private"}, "from": {"id": 1, "first_name": "A"}}
    assert normalize_message({**base, "chat": {"id": -5, "type": "group"}}) is None
    photo = normalize_message({**base, "photo": [{"file_id": "s", "width": 90, "height": 90},
                                                 {"file_id": "b", "width": 1280, "height": 960}],
                               "caption": "ดูรูปนี้"})
    assert photo["contentType"] == 1 and photo["meta"]["fileId"] == "b" and photo["text"] == "ดูรูปนี้"
    document = normalize_message({**base, "document": {"file_id": "d", "file_name": "q.pdf"}})
    assert document["contentType"] == 14 and document["meta"]["fileName"] == "q.pdf"
    sticker = normalize_message({**base, "sticker": {"file_id": "st", "emoji": "😀", "is_animated": True}})
    assert sticker["contentType"] == 7 and sticker["text"] == "😀"
    place = normalize_message({**base, "location": {"latitude": 13.7, "longitude": 100.5}})
    assert place["contentType"] == 15 and place["meta"]["location"]["latitude"] == 13.7
    assert photo["chatId"] == "tgu1" and photo["at"] == 1_700_000_000_000


def test_adding_a_bot_creates_a_safe_account(state):
    owner = _owner(state)
    with pytest.raises(WebError) as exc:
        state.add_telegram_bot({"token": "555555555:" + "X" * 35}, owner)
    assert exc.value.code == "telegram_token_rejected"
    account_id = _add_bot(state, owner)
    listed = next(a for a in state.account_store.list_accounts() if a["id"] == account_id)
    assert listed["channel"] == "telegram_bot" and listed["capabilities"] == []
    assert listed["tokenFileExists"] and listed["sessionState"] == "connected"
    assert "credentialFile" not in listed and TOKEN not in str(listed)
    stored = state.account_store.get(account_id)
    with open(stored["credentialFile"], encoding="utf-8") as handle:
        assert TOKEN not in handle.read()
    assert state.tg_token(account_id) == TOKEN
    assert account_id in state.web_auth._find_user(owner["id"])["accountIds"]
    with pytest.raises(WebError) as exc:
        state.get_api(account_id)
    assert exc.value.code == "line_only"
    status = state.status(account_id, state.web_auth._find_user(owner["id"]))
    assert status["authenticated"] and status["profile"]["mid"] == "tgu555555555"
    assert status["profile"]["statusMessage"] == "t.me/shop_bot"
    with pytest.raises(WebError) as exc:
        state.add_telegram_bot({"token": TOKEN}, owner)
    assert exc.value.code == "telegram_bot_exists"


def test_messages_flow_through_crm_inbox_and_kpi(state):
    owner = _owner(state)
    account_id = _add_bot(state, owner)
    before = state.inbox_version(account_id)
    state.tg_receive(account_id, _incoming("มีสินค้าไหม"))
    assert state.inbox_version(account_id) > before
    assert state.room_states(account_id)[CHAT]["status"] == "waiting"

    chats = state.tg_conversations(account_id)["conversations"]
    assert chats[0]["mid"] == CHAT and chats[0]["unreadCount"] == 1
    assert chats[0]["lastMessage"]["text"] == "มีสินค้าไหม"
    contacts = state.tg_contacts(account_id, {})["contacts"]
    assert contacts[0]["name"] == "Ploy" and contacts[0]["statusMessage"] == "@ploy"

    user = state.web_auth._find_user(owner["id"])
    sent = state.tg_send(account_id, {"to": CHAT, "text": "มีค่ะ"}, user)
    assert FakeTg.sent[-1] == ("text", CUSTOMER_ID, "มีค่ะ") and sent["messageId"]
    room = state.room_states(account_id)[CHAT]
    assert room["status"] == "answered" and room["answeredBy"] == owner["id"]

    messages = state.tg_messages(account_id, {"chat_mid": [CHAT]})["messages"]
    assert [m["text"] for m in messages] == ["มีสินค้าไหม", "มีค่ะ"]
    assert messages[0]["from"] == CHAT and messages[1]["from"] == "tgu555555555"
    conv = state.crm.conversation_for_chat(account_id, CHAT)
    assert conv["assigned_to"] == owner["id"] and conv["unread"] == 0

    with pytest.raises(WebError) as exc:
        state.tg_send(account_id, {"to": "tgu999", "text": "hi"}, user)
    assert exc.value.code == "telegram_not_started"
    FakeTg.blocked.add(CUSTOMER_ID)
    with pytest.raises(WebError) as exc:
        state.tg_send(account_id, {"to": CHAT, "text": "hi"}, user)
    assert exc.value.code == "telegram_blocked"


def test_ai_replies_to_telegram_customers(state, monkeypatch):
    owner = _owner(state)
    account_id = _add_bot(state, owner)
    state.store.set("ai_auto_reply", {"accounts": {account_id: {
        "accountId": account_id, "enabled": True, "contactsEnabled": True,
        "enabledAtMs": 0, "ownerId": owner["id"]}}})
    monkeypatch.setattr(state, "ask_global_ai",
                        lambda question, owner, **kwargs: {"question": {"answer": f"ตอบ: {question}"}})
    monkeypatch.setattr(state, "_assistant_settings_for", lambda data, user: {"configured": True})
    state.tg_receive(account_id, _incoming("เปิดกี่โมง"))
    state.ai_auto_reply_executor.submit(lambda: None).result(timeout=5)
    deadline = time.time() + 5
    while not FakeTg.sent and time.time() < deadline:
        time.sleep(0.05)
    assert FakeTg.sent[-1] == ("text", CUSTOMER_ID, "ตอบ: เปิดกี่โมง")
    row = state.crm.db.query_one("SELECT sender_kind FROM crm_messages WHERE direction = 'out'")
    assert row == {"sender_kind": "ai"}


def test_removing_the_account_cleans_up(state):
    owner = _owner(state)
    account_id = _add_bot(state, owner)
    state.tg_receive(account_id, _incoming())
    credential_file = state.account_store.get(account_id)["credentialFile"]
    state.delete_account(account_id, state.web_auth._find_user(owner["id"]))
    assert not os.path.exists(credential_file)
    assert not state.telegram.running(account_id)
    assert state.crm.conversation_for_chat(account_id, CHAT) is None


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    FakeTg.sent = []
    FakeTg.blocked = set()
    server = OkLineWebServer(("127.0.0.1", 0), OkLineWebHandler)
    server.state = WebState(_make_config(tmp_path))
    server.state.telegram.client_factory = FakeTg
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.state
    finally:
        server.shutdown()
        server.state.close()
        server.server_close()


def test_chat_routes_branch_by_account_kind(live):
    base, state = live
    session = requests.Session()
    session.post(f"{base}/api/auth/setup",
                 json={"email": "owner@example.com", "password": "owner-pass"}, timeout=5).raise_for_status()
    created = session.post(f"{base}/api/telegram/accounts/create",
                           json={"token": TOKEN, "label": "Shop TG"}, timeout=5)
    assert created.status_code == 200, created.text
    assert TOKEN not in created.text
    account_id = created.json()["account"]["id"]
    # The new account is in the list returned to the page that added it.
    assert account_id in {a["id"] for a in created.json()["accounts"]["accounts"]}
    state.tg_receive(account_id, _incoming("ราคาเท่าไหร่"))
    state.tg_receive(account_id, normalize_message({
        "message_id": 2, "date": int(time.time()),
        "chat": {"id": CUSTOMER_ID, "type": "private"}, "from": {"id": CUSTOMER_ID},
        "photo": [{"file_id": "img-1", "width": 300, "height": 200}],
    }))
    state.tg_receive(account_id, normalize_message({
        "message_id": 3, "date": int(time.time()),
        "chat": {"id": CUSTOMER_ID, "type": "private"}, "from": {"id": CUSTOMER_ID},
        "document": {"file_id": "doc-2", "file_name": "evil.html"},
    }))

    q = f"accountId={account_id}"
    convs = session.get(f"{base}/api/conversations?{q}", timeout=5)
    assert convs.status_code == 200 and convs.json()["conversations"][0]["mid"] == CHAT
    assert session.get(f"{base}/api/groups?{q}", timeout=5).json() == {"groups": [], "count": 0}
    boxes = session.get(f"{base}/api/boxes?{q}", timeout=5)
    assert boxes.status_code == 400 and boxes.json()["code"] == "line_only"
    sent = session.post(f"{base}/api/send", json={"accountId": account_id, "to": CHAT, "text": "ok"}, timeout=5)
    assert sent.status_code == 200, sent.text

    image = session.get(f"{base}/api/message-content?{q}&chat_mid={CHAT}&message_id=2", timeout=5)
    assert image.status_code == 200 and image.headers["content-type"] == "image/jpeg"
    document = session.get(f"{base}/api/message-content?{q}&chat_mid={CHAT}&message_id=3", timeout=5)
    assert document.headers["content-type"] == "application/octet-stream"
    assert document.headers["content-disposition"].startswith("attachment")
    other_chat = session.get(f"{base}/api/message-content?{q}&chat_mid=tgu1&message_id=2", timeout=5)
    assert other_chat.status_code == 404

    status = session.get(f"{base}/api/status?{q}", timeout=5).json()
    assert status["channel"] == "telegram_bot" and status["authenticated"] is True
    schedule = session.post(f"{base}/api/schedules/create", json={
        "accountId": account_id, "to": CHAT, "text": "x", "mode": "once",
        "runAt": "2030-01-01T09:00"}, timeout=5)
    assert schedule.status_code == 400 and schedule.json()["code"] == "line_only"
