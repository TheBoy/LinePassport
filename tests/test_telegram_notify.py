"""Telegram staff alerts: linking, alert routing, replying and buttons."""

from __future__ import annotations

import re
import threading
import time
from collections.abc import Iterator

import pytest
import requests

from okline.telegram.bot_api import TelegramApiError, TelegramBotClient
from okline.telegram.errors import TelegramError
from okline.webapp import OkLineWebHandler, OkLineWebServer, WebConfig, WebState

TOKEN = "123456789:" + "A" * 35
CUSTOMER = "u" + "a" * 32


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


class FakeBot:
    """Stands in for TelegramBotClient; every instance shares one outbox."""

    outbox: list[dict] = []
    answers: list[tuple[str, str]] = []

    def __init__(self, token, proxy=None):
        self.token = token

    def get_me(self):
        if self.token != TOKEN:
            raise TelegramApiError("Unauthorized", error_code=401)
        return {"id": 123456789, "is_bot": True, "username": "shop_alert_bot", "first_name": "Shop"}

    def delete_webhook(self):
        return True

    def get_updates(self, offset=0, *, timeout=25, allowed=None):
        time.sleep(0.05)
        return []

    def send_message(self, chat_id, text, *, buttons=None, reply_to=None, disable_preview=True):
        FakeBot.outbox.append({"chat": chat_id, "text": text, "buttons": buttons, "reply_to": reply_to})
        return {"message_id": 1000 + len(FakeBot.outbox)}

    def answer_callback(self, callback_id, text="", *, alert=False):
        FakeBot.answers.append((callback_id, text))

    def close(self):
        return None


class FakeLine:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send_text(self, to, text):
        self.sent.append((to, text))
        return {"id": f"line-{len(self.sent)}"}


@pytest.fixture
def state(tmp_path, monkeypatch) -> Iterator[WebState]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    FakeBot.outbox = []
    FakeBot.answers = []
    web = WebState(_make_config(tmp_path))
    web.staff_notifier.client_factory = FakeBot
    try:
        yield web
    finally:
        web.close()


def _owner_with_account(state) -> dict:
    state.web_auth.setup("owner@example.com", "owner-pass")
    owner = state.web_auth.data["users"][0]
    state.account_store.data["accounts"] = [
        {"id": "acct", "label": "Shop LINE", "mid": "U-me", "ownerId": owner["id"], "tokenFile": ""}
    ]
    state.account_store.save()
    state.web_auth.grant_account_access(owner["id"], "acct")
    return dict(state.web_auth._find_user(owner["id"]))


def _flush(state):
    state.staff_notifier.executor.submit(lambda: None).result(timeout=5)


def _link(state, owner) -> int:
    notifier = state.staff_notifier
    url = notifier.create_link(owner["id"], owner["id"])["url"]
    code = url.rsplit("start=", 1)[1]
    assert url.startswith("https://t.me/shop_alert_bot?start=")
    notifier.handle_update(owner["id"], FakeBot(TOKEN), {
        "update_id": 1,
        "message": {"message_id": 5, "date": int(time.time()), "text": f"/start {code}",
                    "chat": {"id": 777, "type": "private"},
                    "from": {"id": 777, "first_name": "Owner", "username": "owner_tg"}},
    })
    return 777


def test_configure_validates_and_encrypts_the_token(state):
    owner = _owner_with_account(state)
    with pytest.raises(TelegramError) as exc:
        state.staff_notifier.configure(owner["id"], "not-a-token")
    assert exc.value.code == "telegram_token_invalid"
    with pytest.raises(TelegramError) as exc:
        state.staff_notifier.configure(owner["id"], "123456789:" + "B" * 35)
    assert exc.value.code == "telegram_token_rejected"

    state.staff_notifier.configure(owner["id"], TOKEN)
    stored = state.store.get("telegram_notify", {})["tenants"][owner["id"]]
    assert stored["username"] == "shop_alert_bot"
    assert TOKEN not in str(stored) and stored["token"].startswith("fernet:")


def test_link_code_works_once(state):
    owner = _owner_with_account(state)
    state.staff_notifier.configure(owner["id"], TOKEN)
    chat = _link(state, owner)
    assert state.staff_notifier.link_for(owner["id"])["chatId"] == chat
    assert "เชื่อมกับ LinePassport แล้ว" in FakeBot.outbox[-1]["text"]
    settings = state.staff_notifier.settings(owner["id"], owner["id"], can_manage=True)
    assert settings["link"]["linked"] and settings["link"]["username"] == "owner_tg"


def test_waiting_customer_alert_reply_and_claim(state, monkeypatch):
    owner = _owner_with_account(state)
    state.staff_notifier.configure(owner["id"], TOKEN)
    chat = _link(state, owner)
    now = int(time.time() * 1000)
    state.crm.ingest(
        owner_id=owner["id"], account_id="acct", channel="line", chat_id=CUSTOMER,
        contact={"externalId": CUSTOMER, "name": "Nok"},
        messages=[{"externalId": "m1", "direction": "in", "text": "มีของไหมคะ", "at": now}],
    )
    FakeBot.outbox.clear()

    state._dispatch_push("acct", [CUSTOMER])
    _flush(state)
    alert = FakeBot.outbox[-1]
    assert alert["chat"] == chat
    assert "Nok" in alert["text"] and "มีของไหมคะ" in alert["text"]
    claim = alert["buttons"][0][0]["callback_data"]
    assert claim.startswith("c:")

    # Replying to the alert sends the text to the customer as this person.
    line = FakeLine()
    monkeypatch.setattr(state, "get_api", lambda account_id=None, require_auth=True: line)
    notifier = state.staff_notifier
    notifier.handle_update(owner["id"], FakeBot(TOKEN), {
        "update_id": 2,
        "message": {"message_id": 9, "date": int(time.time()), "text": "มีค่ะ",
                    "chat": {"id": chat, "type": "private"}, "from": {"id": chat},
                    "reply_to_message": {"message_id": 1000 + len(FakeBot.outbox)}},
    })
    assert line.sent == [(CUSTOMER, "มีค่ะ")]
    assert FakeBot.outbox[-1]["text"].startswith("✓ ส่งถึง Nok")
    logs = state.list_bot_logs("acct", owner)["logs"]
    assert any(log["action"] == "operator_send" for log in logs)

    # The claim button makes the chat theirs.
    notifier.handle_update(owner["id"], FakeBot(TOKEN), {
        "update_id": 3,
        "callback_query": {"id": "cb1", "data": claim, "from": {"id": chat}},
    })
    assert FakeBot.answers[-1][1].startswith("รับเรื่องแล้ว")
    assert state.crm.conversation_for_chat("acct", CUSTOMER)["assigned_to"] == owner["id"]


def test_stale_or_unlinked_replies_are_not_sent(state, monkeypatch):
    owner = _owner_with_account(state)
    state.staff_notifier.configure(owner["id"], TOKEN)
    chat = _link(state, owner)
    state.crm.ingest(
        owner_id=owner["id"], account_id="acct", channel="line", chat_id=CUSTOMER,
        contact={"externalId": CUSTOMER},
        messages=[{"externalId": "m1", "direction": "in", "at": int(time.time() * 1000)}],
    )
    state._dispatch_push("acct", [CUSTOMER])
    _flush(state)
    alert_id = 1000 + len(FakeBot.outbox)
    line = FakeLine()
    monkeypatch.setattr(state, "get_api", lambda account_id=None, require_auth=True: line)
    notifier = state.staff_notifier
    notifier.handle_update(owner["id"], FakeBot(TOKEN), {
        "update_id": 4,
        "message": {"message_id": 10, "date": int(time.time()) - 3600, "text": "late",
                    "chat": {"id": chat, "type": "private"},
                    "reply_to_message": {"message_id": alert_id}},
    })
    notifier.handle_update(owner["id"], FakeBot(TOKEN), {
        "update_id": 5,
        "message": {"message_id": 11, "date": int(time.time()), "text": "who am i",
                    "chat": {"id": 999, "type": "private"},
                    "reply_to_message": {"message_id": alert_id}},
    })
    assert line.sent == []
    assert "ยังไม่ได้เชื่อม" in FakeBot.outbox[-1]["text"]


def test_alert_mode_respects_an_open_page(state):
    owner = _owner_with_account(state)
    state.staff_notifier.configure(owner["id"], TOKEN)
    _link(state, owner)
    state.presence_current[owner["id"]] = {"state": "active", "at": int(time.time() * 1000)}
    assert state._telegram_recipients("acct") == []
    state.staff_notifier.set_mode(owner["id"], "always")
    assert state._telegram_recipients("acct") == [owner["id"]]


def test_assignment_notifies_on_telegram(state):
    owner = _owner_with_account(state)
    state.staff_notifier.configure(owner["id"], TOKEN)
    _link(state, owner)
    FakeBot.outbox.clear()
    state.notify_user(owner["id"], {"kind": "task_due", "title": "ถึงเวลางานติดตาม", "body": "โทรหาลูกค้า"})
    _flush(state)
    assert FakeBot.outbox[-1]["text"].startswith("📌 ถึงเวลางานติดตาม")
    assert "โทรหาลูกค้า" in FakeBot.outbox[-1]["text"]


@pytest.fixture
def live(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    server = OkLineWebServer(("127.0.0.1", 0), OkLineWebHandler)
    server.state = WebState(_make_config(tmp_path))
    server.state.staff_notifier.client_factory = FakeBot
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.state
    finally:
        server.shutdown()
        server.state.close()
        server.server_close()


def test_routes_let_only_the_owner_set_the_bot(live):
    base, state = live
    owner = requests.Session()
    owner.post(f"{base}/api/auth/setup",
               json={"email": "owner@example.com", "password": "owner-pass"}, timeout=5).raise_for_status()
    owner_id = owner.get(f"{base}/api/auth/status", timeout=5).json()["user"]["id"]
    state.account_store.data["accounts"] = [
        {"id": "acct", "label": "Shop", "mid": "U-me", "ownerId": owner_id, "tokenFile": ""}
    ]
    state.account_store.save()
    state.web_auth.grant_account_access(owner_id, "acct")
    owner.post(f"{base}/api/team/create", json={
        "email": "staff@example.com", "password": "staff-pass", "displayName": "Staff",
        "accountIds": ["acct"]}, timeout=5).raise_for_status()
    staff = requests.Session()
    staff.post(f"{base}/api/auth/login",
               json={"email": "staff@example.com", "password": "staff-pass"}, timeout=5).raise_for_status()

    refused = staff.post(f"{base}/api/telegram/notify/settings", json={"token": TOKEN}, timeout=5)
    assert refused.status_code == 403
    saved = owner.post(f"{base}/api/telegram/notify/settings", json={"token": TOKEN}, timeout=5)
    assert saved.status_code == 200, saved.text
    assert saved.json()["botUsername"] == "shop_alert_bot"
    assert TOKEN not in saved.text
    link = staff.post(f"{base}/api/telegram/notify/link", json={}, timeout=5)
    assert link.status_code == 200
    assert re.fullmatch(r"https://t\.me/shop_alert_bot\?start=[\w-]+", link.json()["url"])
    assert staff.get(f"{base}/api/telegram/notify", timeout=5).json()["canManage"] is False


def test_client_errors_never_include_the_token():
    class Boom(requests.Session):
        def post(self, url, **kwargs):
            raise requests.ConnectionError(f"failed to reach {url}")

    client = TelegramBotClient(TOKEN, session=Boom())
    with pytest.raises(TelegramApiError) as exc:
        client.get_me()
    assert TOKEN not in str(exc.value) and exc.value.network
    with pytest.raises(TelegramApiError):
        client.download("../../etc/passwd")
    assert TOKEN not in repr(client)
