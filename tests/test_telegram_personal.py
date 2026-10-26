"""Personal Telegram accounts (MTProto) behind a fake adapter."""

from __future__ import annotations

import asyncio
import datetime as dt
import time
from collections.abc import Iterator
from types import SimpleNamespace

import pytest

from okline.telegram.errors import TelegramError
from okline.telegram.mtproto import normalize_telethon
from okline.webapp import WebConfig, WebError, WebState

CUSTOMER = 8080
CHAT = f"tgu{CUSTOMER}"
START = {"apiId": "123456", "apiHash": "a" * 32, "phone": "+66 81 111 2222", "label": "My TG"}


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


class FakeUser:
    instances: list[FakeUser] = []
    need_password = True
    telegram_id = 31337

    def __init__(self, api_id, api_hash, session="", proxy=None):
        self.session = session or ""
        self.closed = False
        self.sent: list[tuple] = []
        self.on_message = None
        self.on_signed_out = None
        FakeUser.instances.append(self)

    def send_code(self, phone):
        self.phone = phone

    def sign_in_code(self, code):
        if code != "12345":
            raise TelegramError(400, "wrong code", "telegram_code_invalid")
        return "password" if FakeUser.need_password else "ok"

    def sign_in_password(self, password):
        if password != "secret":
            raise TelegramError(400, "wrong password", "telegram_password_invalid")

    def authorized(self):
        return self.session == "SESSION-1"

    def me(self):
        return {"id": FakeUser.telegram_id, "first_name": "Shop", "last_name": "Owner",
                "username": "shop_owner", "phone": "66811112222"}

    def session_string(self):
        return "SESSION-1"

    def recent(self, limit):
        return [{
            "externalId": "1", "chatId": CHAT, "userId": str(CUSTOMER), "name": "Old Friend",
            "username": "old", "text": "เมื่อวานคุยกัน", "contentType": 0, "meta": {"mtproto": True},
            "at": int(time.time() * 1000) - 3_600_000, "outgoing": False,
        }]

    def listen(self, on_message, on_signed_out):
        self.on_message = on_message
        self.on_signed_out = on_signed_out

    def send_text(self, user_id, text):
        self.sent.append(("text", user_id, text))
        return {"message_id": 70 + len(self.sent), "date": int(time.time())}

    def send_file(self, user_id, data, filename, is_image):
        self.sent.append(("file", user_id, filename))
        return {"message_id": 70 + len(self.sent), "date": int(time.time())}

    def download(self, user_id, message_id):
        return b"\xff\xd8\xffjpg", "photo.jpg"

    def close(self):
        self.closed = True


@pytest.fixture
def state(tmp_path, monkeypatch) -> Iterator[WebState]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    FakeUser.instances = []
    FakeUser.need_password = True
    FakeUser.telegram_id = 31337
    web = WebState(_make_config(tmp_path))
    web.telegram_users.adapter_factory = FakeUser
    try:
        yield web
    finally:
        web.close()


def _owner(state) -> dict:
    state.web_auth.setup("owner@example.com", "owner-pass")
    return dict(state.web_auth.data["users"][0])


def _wait_running(state, account_id) -> FakeUser:
    deadline = time.time() + 5
    while not state.telegram_users.running(account_id) and time.time() < deadline:
        time.sleep(0.02)
    assert state.telegram_users.running(account_id)
    adapter = state.telegram_users.adapter(account_id)
    deadline = time.time() + 5
    while adapter.on_message is None and time.time() < deadline:
        time.sleep(0.02)
    return adapter


def _sign_in(state, owner) -> str:
    assert state.tg_user_login_start(START, owner)["state"] == "code"
    with pytest.raises(TelegramError) as exc:
        state.tg_user_login_code({"code": "99999"}, owner)
    assert exc.value.code == "telegram_code_invalid"
    assert state.tg_user_login_code({"code": "1-2-3-4-5"}, owner)["state"] == "password"
    done = state.tg_user_login_password({"password": "secret"}, owner)
    assert done["state"] == "done"
    return done["account"]["id"]


def test_sign_in_with_two_step_password_creates_the_account(state):
    owner = _owner(state)
    account_id = _sign_in(state, owner)
    account = state.account_store.get(account_id)
    assert account["channel"] == "telegram_user" and account["mid"] == "tgu31337"
    assert account["userid"] == "@shop_owner" and account["label"] == "My TG"
    with open(account["credentialFile"], encoding="utf-8") as handle:
        stored = handle.read()
    assert "SESSION-1" not in stored and "a" * 32 not in stored
    assert state.tgu_credentials(account_id)["session"] == "SESSION-1"
    assert FakeUser.instances[0].closed  # the sign-in client is not kept

    _wait_running(state, account_id)
    # Recent chats arrive as history: a customer card, but no waiting chat or alert.
    conv = state.crm.conversation_for_chat(account_id, CHAT)
    assert conv and conv["display_name"] == "Old Friend"
    assert CHAT not in state.room_states(account_id)

    with pytest.raises(WebError) as exc:
        state.tg_user_login_start(START, owner) and _sign_in(state, owner)
    assert exc.value.code == "telegram_account_exists"


def test_live_messages_and_replies(state):
    owner = _owner(state)
    FakeUser.need_password = False
    state.tg_user_login_start(START, owner)
    account_id = state.tg_user_login_code({"code": "12345"}, owner)["account"]["id"]
    adapter = _wait_running(state, account_id)

    adapter.on_message({
        "externalId": "2", "chatId": CHAT, "userId": str(CUSTOMER), "name": "Old Friend",
        "username": "old", "text": "ยังมีของไหม", "contentType": 0, "meta": {"mtproto": True},
        "at": int(time.time() * 1000), "outgoing": False,
    })
    assert state.room_states(account_id)[CHAT]["status"] == "waiting"
    assert state.tg_conversations(account_id)["conversations"][0]["unreadCount"] == 1

    user = state.web_auth._find_user(owner["id"])
    state.tg_send(account_id, {"to": CHAT, "text": "มีค่ะ"}, user)
    assert adapter.sent[-1] == ("text", CUSTOMER, "มีค่ะ")
    assert state.room_states(account_id)[CHAT]["status"] == "answered"
    data, _name, kind = state.tg_media(account_id, CHAT, "2")
    assert data.startswith(b"\xff\xd8") and kind == 0

    status = state.status(account_id, user)
    assert status["channel"] == "telegram_user" and status["profile"]["statusMessage"] == "@shop_owner"


def test_signed_out_account_asks_to_sign_in_again(state):
    owner = _owner(state)
    FakeUser.need_password = False
    state.tg_user_login_start(START, owner)
    account_id = state.tg_user_login_code({"code": "12345"}, owner)["account"]["id"]
    adapter = _wait_running(state, account_id)
    adapter.on_signed_out()
    assert state.account_store.requires_reauth(account_id)
    assert state.status(account_id, state.web_auth._find_user(owner["id"]))["loginRequired"]

    # Signing in as someone else is refused; the same account reconnects.
    FakeUser.telegram_id = 999
    state.tg_user_login_start({**START, "reconnectAccountId": account_id}, owner)
    with pytest.raises(WebError) as exc:
        state.tg_user_login_code({"code": "12345"}, owner)
    assert exc.value.code == "telegram_account_mismatch"
    FakeUser.telegram_id = 31337
    state.tg_user_login_start({**START, "reconnectAccountId": account_id}, owner)
    state.tg_user_login_code({"code": "12345"}, owner)
    assert not state.account_store.requires_reauth(account_id)


def test_missing_telethon_and_bad_input(state):
    owner = _owner(state)

    def missing(*args, **kwargs):
        raise TelegramError(501, "Telethon missing", "telegram_mtproto_missing")

    state.telegram_users.adapter_factory = missing
    with pytest.raises(TelegramError) as exc:
        state.tg_user_login_start(START, owner)
    assert exc.value.code == "telegram_mtproto_missing"
    state.telegram_users.adapter_factory = FakeUser
    for bad, code in (({"apiId": "x"}, "telegram_api_id_invalid"),
                      ({"apiHash": "nothex"}, "telegram_api_id_invalid"),
                      ({"phone": "12"}, "telegram_phone_invalid")):
        with pytest.raises(TelegramError) as exc:
            state.tg_user_login_start({**START, **bad}, owner)
        assert exc.value.code == code
    staff = {**owner, "teamOwnerId": "someone"}
    with pytest.raises(WebError):
        state.tg_user_login_start(START, staff)
    assert state.tg_user_available()["install"].startswith("pip install")


def test_normalize_telethon_private_messages():
    async def chat():
        return SimpleNamespace(first_name="Nok", last_name="", username="nok", bot=False)

    base = {
        "id": 5, "is_private": True, "chat_id": 777, "out": False,
        "date": dt.datetime(2026, 10, 26, tzinfo=dt.timezone.utc),
        "message": "", "media": None, "photo": None, "sticker": None, "voice": None,
        "audio": None, "video": None, "gif": None, "video_note": None, "document": None,
        "contact": None, "geo": None, "venue": None, "file": None, "get_chat": chat,
    }
    text = asyncio.run(normalize_telethon(SimpleNamespace(**{**base, "message": "hello"})))
    assert text["chatId"] == "tgu777" and text["name"] == "Nok" and text["contentType"] == 0
    photo = asyncio.run(normalize_telethon(SimpleNamespace(**{**base, "photo": object(), "media": object()})))
    assert photo["contentType"] == 1 and photo["meta"]["mtproto"] is True
    document = asyncio.run(normalize_telethon(SimpleNamespace(
        **{**base, "document": object(), "media": object(),
           "file": SimpleNamespace(name="quote.pdf", size=10, ext=".pdf", mime_type="application/pdf")})))
    assert document["contentType"] == 14 and document["meta"]["fileName"] == "quote.pdf"
    group = asyncio.run(normalize_telethon(SimpleNamespace(**{**base, "is_private": False})))
    assert group is None
