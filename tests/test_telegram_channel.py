"""Telegram bots as customer chat accounts."""

from __future__ import annotations

import os
import sys
import threading
import time
from collections.abc import Iterator

import pytest
import requests

from okline import webapp as webapp_module
from okline.telegram.bot_api import TelegramApiError
from okline.telegram.channels import normalize_message
from okline.webapp import (
    OkLineWebHandler,
    OkLineWebServer,
    WebAuth,
    WebConfig,
    WebError,
    WebState,
)

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


def _no_ai_client(*args, **kwargs):
    raise AssertionError("no AI provider call expected")


def _auto_reply_ended(state):
    """Wait for the auto reply handed over to end: it is sent first, and only then noted on
    the chat and recorded, so a test that saw the send has not yet seen those."""
    deadline = time.time() + 30
    while state.ai_auto_reply_inflight and time.time() < deadline:
        time.sleep(0.02)
    assert not state.ai_auto_reply_inflight


def test_ai_replies_to_telegram_customers(state, monkeypatch):
    owner = _owner(state)
    account_id = _add_bot(state, owner)
    state.store.set("ai_auto_reply", {"accounts": {account_id: {
        "accountId": account_id, "enabled": True, "contactsEnabled": True,
        "enabledAtMs": 0, "ownerId": owner["id"]}}})
    monkeypatch.setattr(state, "ask_global_ai",
                        lambda question, owner, **kwargs: {"question": {"answer": f"ตอบ: {question}"}})
    monkeypatch.setattr(webapp_module, "make_ai_client", _no_ai_client)
    # The workspace's own AI, saved the way the settings page saves it.
    state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-test-key-0001", "test": False},
        state.web_auth._find_user(owner["id"]),
    )
    assert state._ai_covers_contacts(account_id) is True
    state.tg_receive(account_id, _incoming("เปิดกี่โมง"))
    _auto_reply_ended(state)
    assert FakeTg.sent[-1] == ("text", CUSTOMER_ID, "ตอบ: เปิดกี่โมง")
    row = state.crm.db.query_one("SELECT sender_kind FROM crm_messages WHERE direction = 'out'")
    assert row == {"sender_kind": "ai"}


def test_telegram_customers_get_no_ai_reply_without_the_workspace_ai(state, monkeypatch):
    owner = _owner(state)
    account_id = _add_bot(state, owner)
    state.store.set("ai_auto_reply", {"accounts": {account_id: {
        "accountId": account_id, "enabled": True, "contactsEnabled": True,
        "enabledAtMs": 0, "ownerId": owner["id"]}}})
    monkeypatch.setattr(webapp_module, "make_ai_client", _no_ai_client)
    # A Shared AI record left in the store no longer answers for a workspace.
    data = state._global_ai_all()
    data["settings"] = {"enabled": True, "chatModel": "llama3",
                        "baseUrl": "http://127.0.0.1:11434"}
    state.store.set("global_ai", data)
    assert state._ai_covers_contacts(account_id) is False
    state.tg_receive(account_id, _incoming("เปิดกี่โมง"))
    deadline = time.time() + 5
    while state.ai_auto_reply_inflight and time.time() < deadline:
        time.sleep(0.05)
    state.ai_auto_reply_executor.submit(lambda: None).result(timeout=5)
    assert not state.ai_auto_reply_inflight
    assert FakeTg.sent == []  # no answer, and not the fallback text either
    # Passed over quietly: no rate-limit use, no log line per message, and the
    # auto-reply page says the AI is missing.
    assert state.ai_auto_reply_rate_events == {}
    user = state.web_auth._find_user(owner["id"])
    actions = {log["action"] for log in state.list_bot_logs(account_id, user)["logs"]}
    assert not actions & {"ai.auto_reply.received", "ai.auto_reply.error", "ai.auto_reply.rate_limit"}
    runtime = state.ai_auto_reply_settings(account_id, user)["runtime"]
    assert (runtime["state"], runtime["lastError"]) == ("ai_missing", "")


class _RecordingExecutor:
    def __init__(self):
        self.submitted = []

    def submit(self, fn, *args, **kwargs):
        self.submitted.append(args[0] if args else None)

    def shutdown(self, **kwargs):
        return None


def test_a_burst_of_telegram_messages_reads_the_ai_settings_once(state, monkeypatch):
    owner = _owner(state)
    account_id = _add_bot(state, owner)
    state.store.set("ai_auto_reply", {"accounts": {account_id: {
        "accountId": account_id, "enabled": True, "contactsEnabled": True,
        "enabledAtMs": 0, "ownerId": owner["id"]}}})
    monkeypatch.setattr(webapp_module, "make_ai_client", _no_ai_client)
    monkeypatch.setattr(webapp_module, "AI_READY_CACHE_SECONDS", 60.0)
    state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    recorder = _RecordingExecutor()
    state.ai_auto_reply_executor = recorder
    reads = []
    original_get = state.store.get

    def counting_get(key, default):
        if key == "global_ai":
            reads.append(key)
        return original_get(key, default)

    monkeypatch.setattr(state.store, "get", counting_get)

    def receive(count, start):
        for index in range(start, start + count):
            state._telegram_auto_reply(
                account_id, owner["id"], f"tgu{CUSTOMER_ID + index}",
                _incoming(f"q{index}", message_id=index),
            )

    # No AI yet: five messages are passed over quietly, on one read.
    receive(5, 1)
    assert len(reads) == 1 and recorder.submitted == []
    assert state.ai_auto_reply_rate_events == {}
    # Saving the AI forgets that answer at once: the next message is answered.
    state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-test-key-0001", "test": False},
        state.web_auth._find_user(owner["id"]),
    )
    reads.clear()
    receive(4, 10)
    assert len(reads) == 1
    # Two start, and two wait for the owner's share of the workers (the recorder
    # never runs a job, so they keep waiting).
    assert [job["question"] for job in recorder.submitted] == ["q10", "q11"]
    assert [job["question"] for job in state.ai_auto_reply_backlog[owner["id"]]] == ["q12", "q13"]
    # Once the answer is older than the cache time, it is read again.
    monkeypatch.setattr(webapp_module, "AI_READY_CACHE_SECONDS", 0.0)
    reads.clear()
    receive(2, 20)
    assert len(reads) == 2


# --- Fair sharing of the auto-reply workers between workspaces -----------------


def _two_workspaces(state):
    """Two owners, each with a Telegram account, auto reply on and their own AI key."""
    owner_a = _owner(state)
    token = state.web_auth.register("member@example.com", "member-pass", "Member")
    owner_b = dict(state.web_auth.current_user(WebAuth.cookie_header(token)))
    accounts = {}
    for name, owner, key in (("a", owner_a, "sk-owner-a-key-01"), ("b", owner_b, "sk-owner-b-key-01")):
        account_id = f"tg-shop-{name}"
        state.account_store.data["accounts"].append({
            "id": account_id, "label": f"Shop {name}", "channel": "telegram_bot",
            "ownerId": owner["id"], "mid": f"tgu{name}", "tokenFile": "",
            "createdAt": "2026-11-22T10:00:00", "updatedAt": "2026-11-22T10:00:00",
        })
        state.account_store.save()
        state.web_auth.grant_account_access(owner["id"], account_id)
        state.save_assistant_settings(
            {"provider": "openai", "apiKey": key, "test": False},
            state.web_auth._find_user(owner["id"]),
        )
        accounts[name] = (account_id, owner["id"], key)
    state.store.set("ai_auto_reply", {"accounts": {
        account_id: {"accountId": account_id, "enabled": True, "contactsEnabled": True,
                     "enabledAtMs": 0, "ownerId": owner_id}
        for account_id, owner_id, _key in accounts.values()
    }})
    return accounts


class _Gate:
    """A fake AI whose chat waits for ``open``; counts each workspace's replies at once."""

    def __init__(self, workspaces):
        self.workspaces = workspaces  # API key -> workspace name
        self.open = threading.Event()
        self.lock = threading.Lock()
        self.running = dict.fromkeys(workspaces.values(), 0)
        self.most = dict(self.running)
        self.started: list[tuple[str, str]] = []

    def client(self, provider_id, *, api_key, **kwargs):
        gate = self

        class _Client:
            def chat(self, **chat):
                name = gate.workspaces[api_key]
                with gate.lock:
                    gate.running[name] += 1
                    gate.most[name] = max(gate.most[name], gate.running[name])
                    gate.started.append((name, chat["question"]))
                try:
                    assert gate.open.wait(10)
                    return {"answer": f"ตอบ {chat['question']}", "canAnswer": True,
                            "confidence": 0.9, "model": "m"}
                finally:
                    with gate.lock:
                        gate.running[name] -= 1

        return _Client()


def _wait_for(condition, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.01)
    return True


def test_one_workspace_cannot_take_every_auto_reply_worker(state, monkeypatch):
    accounts = _two_workspaces(state)
    gate = _Gate({key: name for name, (_a, _o, key) in accounts.items()})
    monkeypatch.setattr(webapp_module, "make_ai_client", gate.client)
    sent = []
    sent_lock = threading.Lock()

    def send(account_id, chat_id, text, *, by):
        with sent_lock:
            sent.append((account_id, chat_id))
        return f"m{len(sent)}"

    monkeypatch.setattr(state, "channel_send_text", send)
    account_a, owner_a, _ = accounts["a"]
    account_b, owner_b, _ = accounts["b"]
    try:
        # Workspace A: six customers write at once; B: one customer, right after.
        for index in range(6):
            state._telegram_auto_reply(
                account_a, owner_a, f"tgu{100 + index}", _incoming(f"a{index}", message_id=index)
            )
        asked_b = time.monotonic()
        state._telegram_auto_reply(account_b, owner_b, "tgu200", _incoming("b0", message_id=9))
        # B's reply starts at once on a free worker, while A holds only its share.
        assert _wait_for(lambda: ("b", "b0") in gate.started, 2.0)
        assert time.monotonic() - asked_b < 1.5
        time.sleep(0.2)  # time for anything else to start, if it wrongly could
        with gate.lock:
            assert sorted(question for name, question in gate.started if name == "a") == ["a0", "a1"]
            assert gate.most["a"] == 2
        assert state.ai_auto_reply_owner_jobs == {owner_a: 2, owner_b: 1}
        waiting = [job["question"] for job in state.ai_auto_reply_backlog[owner_a]]
        assert waiting == ["a2", "a3", "a4", "a5"]
        # A message from a chat whose reply is waiting is not queued a second time.
        state._telegram_auto_reply(account_a, owner_a, "tgu105", _incoming("again", message_id=7))
        assert len(state.ai_auto_reply_backlog[owner_a]) == 4
    finally:
        gate.open.set()
    # Every waiting reply runs as places free up, each exactly once, never more than
    # two of A's at a time.
    assert _wait_for(lambda: len(sent) == 7)
    time.sleep(0.2)
    assert sorted(sent) == sorted(
        [(account_a, f"tgu{100 + index}") for index in range(6)] + [(account_b, "tgu200")]
    )
    assert gate.most == {"a": 2, "b": 1}
    assert _wait_for(lambda: not state.ai_auto_reply_owner_jobs)
    assert state.ai_auto_reply_backlog == {} and state.ai_auto_reply_inflight == set()


class _Boom(BaseException):
    """Escapes the reply's own error handling, as an unexpected failure would."""


def test_a_reply_that_blows_up_still_hands_its_place_to_the_next(state, monkeypatch):
    accounts = _two_workspaces(state)
    account_a, owner_a, _ = accounts["a"]
    gate = threading.Event()
    sent = []

    def ask(question, owner, **kwargs):
        if question in {"a0", "a1"}:
            assert gate.wait(10)
        if question == "a0":
            raise _Boom("unexpected")
        return {"question": {"answer": f"ตอบ {question}"}}

    monkeypatch.setattr(state, "ask_global_ai", ask)
    monkeypatch.setattr(
        state, "channel_send_text",
        lambda account_id, chat_id, text, *, by: sent.append(chat_id) or "m1",
    )
    try:
        for index in range(4):
            state._telegram_auto_reply(
                account_a, owner_a, f"tgu{100 + index}", _incoming(f"a{index}", message_id=index)
            )
        assert [job["question"] for job in state.ai_auto_reply_backlog[owner_a]] == ["a2", "a3"]
    finally:
        gate.set()
    assert _wait_for(lambda: len(sent) == 3)
    assert sorted(sent) == ["tgu101", "tgu102", "tgu103"]  # a0 failed; nothing was lost
    assert _wait_for(lambda: not state.ai_auto_reply_owner_jobs)
    assert state.ai_auto_reply_backlog == {} and state.ai_auto_reply_inflight == set()


def test_a_full_backlog_drops_the_oldest_waiting_reply_with_a_log_line(state, monkeypatch):
    accounts = _two_workspaces(state)
    account_a, owner_a, _ = accounts["a"]
    monkeypatch.setattr(webapp_module, "AI_AUTO_REPLY_BACKLOG_PER_OWNER", 2)
    state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    recorder = _RecordingExecutor()  # never runs a job: places stay taken
    state.ai_auto_reply_executor = recorder

    def receive(index, text):
        state._telegram_auto_reply(
            account_a, owner_a, f"tgu{100 + index}", _incoming(text, message_id=index)
        )

    for index in range(5):
        receive(index, f"a{index}")
    assert [job["question"] for job in recorder.submitted] == ["a0", "a1"]
    assert [job["question"] for job in state.ai_auto_reply_backlog[owner_a]] == ["a3", "a4"]
    user = state.web_auth._find_user(owner_a)
    [dropped] = [
        log for log in state.list_bot_logs(account_a, user)["logs"]
        if log["action"] == "ai.auto_reply.dropped"
    ]
    assert dropped["ok"] is False and dropped["data"]["chatMid"] == "tgu102"
    assert dropped["data"]["question"] == "a2" and "oldest" in dropped["detail"]
    # It was never answered, so the reply limits it used are given back.
    assert not state.ai_auto_reply_rate_events.get(("chat", account_a, "tgu102"))
    assert len(state.ai_auto_reply_rate_events[("account", account_a)]) == 4
    # The dropped chat is free again: its next message waits like any other.
    assert (account_a, "tgu102") not in state.ai_auto_reply_inflight
    receive(2, "a2 again")
    assert [job["question"] for job in state.ai_auto_reply_backlog[owner_a]] == ["a4", "a2 again"]
    assert len(state.ai_auto_reply_owner_jobs) == 1 and state.ai_auto_reply_owner_jobs[owner_a] == 2


def test_removing_the_account_cleans_up(state):
    owner = _owner(state)
    account_id = _add_bot(state, owner)
    state.tg_receive(account_id, _incoming())
    credential_file = state.account_store.get(account_id)["credentialFile"]
    state.delete_account(account_id, state.web_auth._find_user(owner["id"]))
    assert not os.path.exists(credential_file)
    assert not state.telegram.running(account_id)
    assert state.crm.conversation_for_chat(account_id, CHAT) is None


def test_deleting_a_member_removes_their_telegram_bot_too(state):
    auth = state.web_auth
    auth.setup("owner@example.com", "owner-pass")
    god = auth.ensure_god("god", "god-password")
    token = auth.register("member@example.com", "member-pass", "Member")
    member = auth.current_user(WebAuth.cookie_header(token))
    account_id = _add_bot(state, member)
    state.tg_receive(account_id, _incoming())
    credential_file = state.account_store.get(account_id)["credentialFile"]
    assert state.telegram.running(account_id)

    state.delete_user({"id": member["id"]}, god)
    assert state.account_store.get(account_id) is None
    assert not state.telegram.running(account_id)
    assert not os.path.exists(credential_file)
    assert state.crm.conversation_for_chat(account_id, CHAT) is None


def test_account_lists_say_which_accounts_are_telegram(state):
    owner = _owner(state)
    account_id = _add_bot(state, owner)
    owner = state.web_auth._find_user(owner["id"])
    team = state.team_payload(owner)
    assert [(item["id"], item["channel"]) for item in team["accounts"]] == [(account_id, "telegram_bot")]


def test_telegram_network_failures_are_not_called_line_errors(state, monkeypatch):
    owner = _owner(state)
    account_id = _add_bot(state, owner)
    state.tg_receive(account_id, _incoming())

    def offline(self, chat_id, text, **kwargs):
        raise TelegramApiError("Could not reach Telegram.", network=True)

    monkeypatch.setattr(FakeTg, "send_message", offline)
    with pytest.raises(WebError) as exc:
        state.tg_send(account_id, {"to": CHAT, "text": "hi"}, state.web_auth._find_user(owner["id"]))
    assert exc.value.code == "telegram_unavailable"


def test_kpi_counts_replies_sent_from_the_telegram_app(state):
    owner = _owner(state)
    account_id = _add_bot(state, owner)
    owner = state.web_auth._find_user(owner["id"])
    now = int(time.time() * 1000)
    state.store.set(f"kpi_responses:{account_id}", {"responses": [
        {"chatMid": CHAT, "askedAt": now - 120_000, "answeredAt": now - 60_000,
         "responseSeconds": 60, "answeredBy": "telegram_app"},
    ]})
    report = state.kpi_report(account_id, owner, from_ms=now - 3_600_000, to_ms=now)
    assert report["lineApp"]["chats"] == 1


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


def _private_update(update_id):
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "date": int(time.time()), "text": "hi",
                    "chat": {"id": CUSTOMER_ID, "type": "private", "first_name": "Ploy"},
                    "from": {"id": CUSTOMER_ID, "first_name": "Ploy"}},
    }


class _BatchClient:
    """A bot client whose get_updates hands out ``batches`` in turn, then nothing."""

    def __init__(self, batches):
        self.batches = list(batches)
        self.asked = []

    def get_updates(self, offset=0, *, timeout=25, allowed=None):
        self.asked.append(offset)
        if self.batches:
            return self.batches.pop(0)
        time.sleep(0.02)
        return []

    def delete_webhook(self):
        return True

    def close(self):
        return None


def _wait_for(condition, seconds=5.0):
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.01)
    return condition()


def test_a_bot_whose_offset_cannot_be_saved_keeps_receiving():
    # Saving the bot's place in Telegram's update list fails (a database that dropped
    # the connection): the poller goes on from the place it has in memory, which tells
    # Telegram those updates were handled, and saves it with the next ones.
    from okline.telegram.channels import TelegramChannels

    received, saved = [], []
    client = _BatchClient([[_private_update(10)], [_private_update(11)]])

    class Host:
        def tg_token(self, account_id):
            return TOKEN

        def tg_receive(self, account_id, message):
            received.append(message["externalId"])

        def tg_auth_failed(self, account_id):
            return None

        def tg_offset(self, account_id):
            return 0

        def tg_save_offset(self, account_id, offset):
            saved.append(offset)
            if len(saved) == 1:
                raise OSError("connection to the database was lost")

        def notify_proxy(self):
            return None

    channels = TelegramChannels(Host(), client_factory=lambda token, proxy=None: client)
    channels.start("bot")
    try:
        assert _wait_for(lambda: len(saved) == 2)
        assert channels.running("bot")
        assert received == ["10", "11"] and saved == [11, 12]
        assert client.asked[:3] == [0, 11, 12]
    finally:
        channels.close()


def test_the_alert_bot_keeps_receiving_when_its_offset_cannot_be_saved():
    from okline.telegram.notify import NotifyWorker

    handled, saved = [], []
    client = _BatchClient([[{"update_id": 5}], [{"update_id": 6}]])

    class Notifier:
        def load_offset(self, owner_id):
            return 0

        def save_offset(self, owner_id, offset):
            saved.append(offset)
            if len(saved) == 1:
                raise OSError(28, "No space left on device")

        def handle_update(self, owner_id, bot, update, waited=0.0):
            handled.append(update["update_id"])

        def mark_broken(self, owner_id, reason):
            return None

    worker = NotifyWorker(Notifier(), "owner", client)
    try:
        assert _wait_for(lambda: len(saved) == 2)
        assert worker.thread.is_alive()
        assert handled == [5, 6] and saved == [6, 7] and client.asked[:3] == [0, 6, 7]
    finally:
        worker.stop()
        worker.thread.join(timeout=5)


class _TelegramLike(_BatchClient):
    """Hands over every update from the offset asked for, as Telegram does: an update
    is forgotten only once a poll asks for those after it."""

    def __init__(self, updates):
        super().__init__([])
        self.updates = list(updates)

    def get_updates(self, offset=0, *, timeout=25, allowed=None):
        self.asked.append(offset)
        pending = [update for update in self.updates if update["update_id"] >= offset]
        if not pending:
            time.sleep(0.02)
        return pending


class OperationalError(Exception):
    """The name sqlite3 and psycopg give a database that is down."""


def test_a_bot_keeps_a_message_it_could_not_store_with_telegram(monkeypatch):
    # The database is down as a customer writes: the message is not stored, so the
    # poller does not tell Telegram it was handled. Telegram hands it over again after a
    # pause, and it is stored once the database is back. A message refused for itself
    # (data the database will never take) is passed over, so it holds up nothing.
    from okline.telegram import channels as channels_module
    from okline.telegram.channels import TelegramChannels

    monkeypatch.setattr(channels_module, "POLL_BACKOFF_SECONDS", (0.01, 0.01, 0.01, 0.01))
    stored, saved, tries = [], [], {}
    client = _TelegramLike([_private_update(20), _private_update(21), _private_update(22)])

    class Host:
        def tg_token(self, account_id):
            return TOKEN

        def tg_receive(self, account_id, message):
            external = message["externalId"]
            tries[external] = tries.get(external, 0) + 1
            if external == "20" and tries[external] <= 3:
                raise OperationalError("connection to the database was refused")
            if external == "21":
                raise ValueError("A string literal cannot contain NUL (0x00) characters.")
            stored.append(external)

        def tg_auth_failed(self, account_id):
            return None

        def tg_offset(self, account_id):
            return 0

        def tg_save_offset(self, account_id, offset):
            saved.append(offset)

        def notify_proxy(self):
            return None

    channels = TelegramChannels(Host(), client_factory=lambda token, proxy=None: client)
    channels.start("bot")
    try:
        assert _wait_for(lambda: 23 in saved)
        assert stored == ["20", "22"] and tries == {"20": 4, "21": 1, "22": 1}
        # Asked again from message 20 until it was stored, and never saved past it before.
        assert client.asked[:4] == [0, 0, 0, 0] and saved[0] == 23
    finally:
        channels.close()


def test_the_alert_bot_keeps_a_staff_reply_it_could_not_handle_with_telegram(monkeypatch):
    from okline.telegram import notify as notify_module
    from okline.telegram.notify import NotifyWorker

    monkeypatch.setattr(notify_module, "POLL_BACKOFF_SECONDS", (0.01, 0.01, 0.01, 0.01))
    handled, saved, tries = [], [], {}
    client = _TelegramLike([{"update_id": 5}, {"update_id": 6}])

    class Notifier:
        def load_offset(self, owner_id):
            return 0

        def save_offset(self, owner_id, offset):
            saved.append(offset)

        def handle_update(self, owner_id, bot, update, waited=0.0):
            number = update["update_id"]
            tries[number] = tries.get(number, 0) + 1
            if number == 5 and tries[number] <= 2:
                raise OSError(28, "No space left on device")
            handled.append(number)

        def mark_broken(self, owner_id, reason):
            return None

    worker = NotifyWorker(Notifier(), "owner", client)
    try:
        assert _wait_for(lambda: 7 in saved)
        assert handled == [5, 6] and tries == {5: 3, 6: 1}
        assert client.asked[:3] == [0, 0, 0] and saved[0] == 7
    finally:
        worker.stop()
        worker.thread.join(timeout=5)


class _FullDisk:
    """A server log on the full disk: writing to it fails."""

    def write(self, text):
        raise OSError(28, "No space left on device")

    def flush(self):
        raise OSError(28, "No space left on device")


def test_both_bots_keep_receiving_when_the_log_cannot_be_written_either(monkeypatch):
    # Saving the offset fails, and so does writing that to the server log (stderr is a
    # file on the full disk): neither poller ends.
    from okline.telegram.channels import TelegramChannels
    from okline.telegram.notify import NotifyWorker

    received, handled, saved = [], [], []
    bot = _BatchClient([[_private_update(10)], [_private_update(11)]])
    alerts = _BatchClient([[{"update_id": 5}], [{"update_id": 6}]])

    class Host:
        def tg_token(self, account_id):
            return TOKEN

        def tg_receive(self, account_id, message):
            received.append(message["externalId"])

        def tg_auth_failed(self, account_id):
            return None

        def tg_offset(self, account_id):
            return 0

        def tg_save_offset(self, account_id, offset):
            saved.append(("bot", offset))
            raise OSError(28, "No space left on device")

        def notify_proxy(self):
            return None

    class Notifier:
        def load_offset(self, owner_id):
            return 0

        def save_offset(self, owner_id, offset):
            saved.append(("alerts", offset))
            raise OSError(28, "No space left on device")

        def handle_update(self, owner_id, client, update, waited=0.0):
            handled.append(update["update_id"])

        def mark_broken(self, owner_id, reason):
            return None

    monkeypatch.setattr(sys, "stderr", _FullDisk())
    channels = TelegramChannels(Host(), client_factory=lambda token, proxy=None: bot)
    channels.start("bot")
    worker = NotifyWorker(Notifier(), "owner", alerts)
    try:
        assert _wait_for(lambda: len(received) == 2 and len(handled) == 2)
        assert channels.running("bot") and worker.thread.is_alive()
        assert received == ["10", "11"] and handled == [5, 6]
    finally:
        channels.close()
        worker.stop()
        worker.thread.join(timeout=5)


def test_a_telegram_message_handed_over_again_is_not_answered_again(state, monkeypatch):
    # A restart before the bot's offset was saved hands its last messages over again:
    # each is stored once, makes its chat wait once and gets one auto reply.
    owner = _owner(state)
    account_id = _add_bot(state, owner)
    state.store.set("ai_auto_reply", {"accounts": {account_id: {
        "accountId": account_id, "enabled": True, "contactsEnabled": True,
        "enabledAtMs": 0, "ownerId": owner["id"]}}})
    asked = []

    def answer(question, owner, **kwargs):
        asked.append(question)
        return {"question": {"answer": f"ตอบ: {question}"}}

    monkeypatch.setattr(state, "ask_global_ai", answer)
    monkeypatch.setattr(webapp_module, "make_ai_client", _no_ai_client)
    state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-test-key-0001", "test": False},
        state.web_auth._find_user(owner["id"]),
    )
    message = _incoming("เปิดกี่โมง", message_id=7)
    state.tg_receive(account_id, message)
    _auto_reply_ended(state)
    assert FakeTg.sent == [("text", CUSTOMER_ID, "ตอบ: เปิดกี่โมง")]
    room = dict(state.room_states(account_id)[CHAT])
    state.tg_receive(account_id, dict(message))  # handed over again
    state.ai_auto_reply_executor.submit(lambda: None).result(timeout=5)
    time.sleep(0.2)
    assert asked == ["เปิดกี่โมง"] and len(FakeTg.sent) == 1
    assert state.room_states(account_id)[CHAT] == room
    assert state.crm.db.scalar("SELECT COUNT(*) FROM crm_messages WHERE direction = 'in'") == 1


class _InternalError(Exception):
    """psycopg's base for a read-only database's error."""


class _ReadOnlySqlTransaction(_InternalError):
    sqlstate = "25006"  # cannot execute INSERT in a read-only transaction


class _DataError(Exception):
    sqlstate = "22001"  # value too long: refused for itself


class _DiskFull(Exception):
    sqlstate = "53100"


@pytest.mark.parametrize(
    ("error", "unavailable"),
    [
        (OSError(28, "No space left on device"), True),
        (OperationalError("connection refused"), True),
        (_ReadOnlySqlTransaction("cannot execute INSERT in a read-only transaction"), True),
        (_DiskFull("could not extend file"), True),
        (_DataError("value too long for type character varying(120)"), False),
        (ValueError("A string literal cannot contain NUL (0x00) characters."), False),
    ],
)
def test_a_database_that_takes_no_writes_counts_as_unavailable(error, unavailable):
    # A read-only database (a managed one that ran low on space, a standby after a
    # failover) cannot store anything now; a message the bot got meanwhile is kept
    # with Telegram, not passed over as refused for itself.
    from okline._util import storage_unavailable

    assert storage_unavailable(error) is unavailable


def test_a_poll_that_handled_updates_before_the_one_it_holds_tells_telegram_at_once(
    monkeypatch,
):
    # Updates handled before a held one are confirmed by the next poll: it comes at
    # once, not after the pause (a restart during the pause would hand them over again,
    # and the alert bot would relay a staff reply twice).
    from okline.telegram import notify as notify_module
    from okline.telegram.notify import NotifyWorker

    monkeypatch.setattr(notify_module, "POLL_BACKOFF_SECONDS", (5, 5, 5, 5))
    handled = []
    client = _TelegramLike([{"update_id": 5}, {"update_id": 6}])

    class Notifier:
        def load_offset(self, owner_id):
            return 0

        def save_offset(self, owner_id, offset):
            raise OSError("connection to the database was lost")

        def handle_update(self, owner_id, bot, update, waited=0.0):
            if update["update_id"] == 6:
                raise OSError("connection to the database was lost")
            handled.append(update["update_id"])

        def mark_broken(self, owner_id, reason):
            return None

    worker = NotifyWorker(Notifier(), "owner", client)
    try:
        assert _wait_for(lambda: len(client.asked) >= 2, seconds=2.0)
        assert client.asked[:2] == [0, 6] and handled == [5]
    finally:
        worker.stop()
        worker.thread.join(timeout=10)


def test_a_button_pressed_behind_a_held_reply_is_known_to_have_waited(monkeypatch):
    from okline.telegram import notify as notify_module
    from okline.telegram.notify import NotifyWorker

    monkeypatch.setattr(notify_module, "POLL_BACKOFF_SECONDS", (0.3, 0.3, 0.3, 0.3))
    seen, tries = [], {"reply": 0}
    client = _TelegramLike([{"update_id": 5, "message": {}}, {"update_id": 6, "callback_query": {}}])

    class Notifier:
        def load_offset(self, owner_id):
            return 0

        def save_offset(self, owner_id, offset):
            return None

        def handle_update(self, owner_id, bot, update, waited=0.0):
            if update["update_id"] == 5:
                tries["reply"] += 1
                if tries["reply"] <= 2:
                    raise OSError("connection to the database was lost")
            seen.append((update["update_id"], waited))

        def mark_broken(self, owner_id, reason):
            return None

    worker = NotifyWorker(Notifier(), "owner", client)
    try:
        assert _wait_for(lambda: len(seen) == 2)
        (reply, _reply_waited), (press, press_waited) = seen
        assert (reply, press) == (5, 6)
        assert press_waited >= 0.5  # it came twice behind the held reply
    finally:
        worker.stop()
        worker.thread.join(timeout=10)


class _PressDuringPause(_TelegramLike):
    """A staff reply is there at once; a button press comes after the first poll, while
    the poller pauses for the reply it could not handle."""

    def get_updates(self, offset=0, *, timeout=25, allowed=None):
        pending = super().get_updates(offset, timeout=timeout, allowed=allowed)
        if len(self.asked) == 1:
            self.updates.append({"update_id": 6, "callback_query": {}})
        return pending


def test_a_button_pressed_while_the_alert_bot_pauses_is_known_to_have_waited(monkeypatch):
    # The poll after the pause is the first to hand the press over: it has waited since
    # the pause began (pressed a minute before the database is back, it is not done).
    from okline.telegram import notify as notify_module
    from okline.telegram.notify import NotifyWorker

    monkeypatch.setattr(notify_module, "POLL_BACKOFF_SECONDS", (0.6, 0.6, 0.6, 0.6))
    seen, tries = [], {"reply": 0}
    client = _PressDuringPause([{"update_id": 5, "message": {}}])

    class Notifier:
        def load_offset(self, owner_id):
            return 0

        def save_offset(self, owner_id, offset):
            return None

        def handle_update(self, owner_id, bot, update, waited=0.0):
            if update["update_id"] == 5:
                tries["reply"] += 1
                if tries["reply"] == 1:
                    raise OSError("connection to the database was lost")
            seen.append((update["update_id"], waited))

        def mark_broken(self, owner_id, reason):
            return None

    worker = NotifyWorker(Notifier(), "owner", client)
    try:
        assert _wait_for(lambda: len(seen) == 2)
        (reply, _reply_waited), (press, press_waited) = seen
        assert (reply, press) == (5, 6) and client.asked[:2] == [0, 0]
        assert press_waited >= 0.5
    finally:
        worker.stop()
        worker.thread.join(timeout=10)


class _TelegramLimited(_TelegramLike):
    """Hands over at most 100 updates a poll, as Telegram does."""

    def get_updates(self, offset=0, *, timeout=25, allowed=None):
        return super().get_updates(offset, timeout=timeout, allowed=allowed)[:100]


def test_a_button_pressed_behind_more_updates_than_a_poll_is_known_to_have_waited(
    monkeypatch,
):
    # More than a poll's 100 updates wait behind a held reply: the press after them comes
    # in the poll after the one that ends the hold, and has waited behind it as long.
    from okline.telegram import notify as notify_module
    from okline.telegram.notify import NotifyWorker

    monkeypatch.setattr(notify_module, "POLL_BACKOFF_SECONDS", (0.6, 0.6, 0.6, 0.6))
    seen, tries = [], {"reply": 0}
    client = _TelegramLimited(
        [{"update_id": 5, "message": {}}]
        + [{"update_id": 6 + n, "message": {}} for n in range(99)]
        + [{"update_id": 105, "callback_query": {}}]
    )

    class Notifier:
        def load_offset(self, owner_id):
            return 0

        def save_offset(self, owner_id, offset):
            return None

        def handle_update(self, owner_id, bot, update, waited=0.0):
            if update["update_id"] == 5:
                tries["reply"] += 1
                if tries["reply"] == 1:
                    raise OSError("connection to the database was lost")
            seen.append((update["update_id"], waited))

        def mark_broken(self, owner_id, reason):
            return None

    worker = NotifyWorker(Notifier(), "owner", client)
    try:
        assert _wait_for(lambda: any(update_id == 105 for update_id, _ in seen))
        assert client.asked[:3] == [0, 0, 105]
        assert dict(seen)[105] >= 0.5
    finally:
        worker.stop()
        worker.thread.join(timeout=10)


def test_a_button_pressed_while_a_slow_reply_is_relayed_is_done_after_a_blip(monkeypatch):
    # A staff reply takes a while to relay and a press comes meanwhile; the next reply
    # then meets one failed database call and is held, and the next poll (at once)
    # handles it: the press waited behind that hold only a moment, so it is done.
    from okline.telegram.notify import NotifyWorker

    seen, tries = [], {"blip": 0}
    client = _TelegramLike([{"update_id": 5, "message": {}}, {"update_id": 6, "message": {}}])

    class Notifier:
        def load_offset(self, owner_id):
            return 0

        def save_offset(self, owner_id, offset):
            return None

        def handle_update(self, owner_id, bot, update, waited=0.0):
            if update["update_id"] == 5:
                client.updates.append({"update_id": 7, "callback_query": {}})
                time.sleep(0.8)  # the relay to LINE is slow
            if update["update_id"] == 6:
                tries["blip"] += 1
                if tries["blip"] == 1:
                    raise OSError("connection to the database was lost")
            seen.append((update["update_id"], waited))

        def mark_broken(self, owner_id, reason):
            return None

    worker = NotifyWorker(Notifier(), "owner", client)
    try:
        assert _wait_for(lambda: any(update_id == 7 for update_id, _ in seen))
        assert client.asked[:2] == [0, 6]  # polled again at once, no pause
        assert dict(seen)[7] < 0.5
    finally:
        worker.stop()
        worker.thread.join(timeout=10)


def test_a_button_pressed_beyond_a_full_batch_has_waited_when_a_hold_comes_back(
    monkeypatch,
):
    # More than a poll's 100 updates wait behind a held reply; once the store is back a
    # reply in that first full batch meets one more failed call and is held again: the
    # press beyond the batch has waited behind the first hold, not just the second.
    from okline.telegram import notify as notify_module
    from okline.telegram.notify import NotifyWorker

    monkeypatch.setattr(notify_module, "POLL_BACKOFF_SECONDS", (0.6, 0.6, 0.6, 0.6))
    seen, tries = [], {5: 0, 50: 0}
    client = _TelegramLimited(
        [{"update_id": 5, "message": {}}]
        + [{"update_id": 6 + n, "message": {}} for n in range(99)]
        + [{"update_id": 105, "callback_query": {}}]
    )

    class Notifier:
        def load_offset(self, owner_id):
            return 0

        def save_offset(self, owner_id, offset):
            return None

        def handle_update(self, owner_id, bot, update, waited=0.0):
            update_id = update["update_id"]
            if update_id in tries:
                tries[update_id] += 1
                if tries[update_id] == 1:
                    raise OSError("connection to the database was lost")
            seen.append((update_id, waited))

        def mark_broken(self, owner_id, reason):
            return None

    worker = NotifyWorker(Notifier(), "owner", client)
    try:
        assert _wait_for(lambda: any(update_id == 105 for update_id, _ in seen))
        assert client.asked[:3] == [0, 0, 50]  # the second hold polls again at once
        assert dict(seen)[105] >= 0.5
    finally:
        worker.stop()
        worker.thread.join(timeout=10)


class _TelegramLongPolls(_TelegramLimited):
    """Waits at Telegram while no update is pending, as a long poll does (two seconds
    at most here), and keeps how long each poll was let wait."""

    def __init__(self, updates):
        super().__init__(updates)
        self.timeouts = []

    def get_updates(self, offset=0, *, timeout=25, allowed=None):
        self.asked.append(offset)
        self.timeouts.append(timeout)
        deadline = time.monotonic() + min(timeout, 2.0)
        while True:
            pending = [update for update in self.updates if update["update_id"] >= offset]
            if pending or time.monotonic() >= deadline:
                return pending[:100]
            time.sleep(0.01)


def test_a_button_pressed_after_a_full_backlog_is_handled_has_not_waited(monkeypatch):
    # Exactly a poll's 100 updates waited behind a held reply and are handled once the
    # store is back; a button pressed a moment later, with nothing before it, has not
    # waited behind the hold: it is done.
    from okline.telegram import notify as notify_module
    from okline.telegram.notify import NotifyWorker

    monkeypatch.setattr(notify_module, "POLL_BACKOFF_SECONDS", (0.6, 0.6, 0.6, 0.6))
    seen, tries = [], {"reply": 0}
    client = _TelegramLongPolls(
        [{"update_id": 5, "message": {}}]
        + [{"update_id": 6 + n, "message": {}} for n in range(99)]
    )

    class Notifier:
        def load_offset(self, owner_id):
            return 0

        def save_offset(self, owner_id, offset):
            return None

        def handle_update(self, owner_id, bot, update, waited=0.0):
            if update["update_id"] == 5:
                tries["reply"] += 1
                if tries["reply"] == 1:
                    raise OSError("connection to the database was lost")
            seen.append((update["update_id"], waited))

        def mark_broken(self, owner_id, reason):
            return None

    worker = NotifyWorker(Notifier(), "owner", client)
    try:
        assert _wait_for(lambda: any(update_id == 104 for update_id, _ in seen))
        time.sleep(0.3)
        client.updates.append({"update_id": 105, "callback_query": {}})
        assert _wait_for(lambda: any(update_id == 105 for update_id, _ in seen))
        assert dict(seen)[105] < 0.5
        assert client.timeouts[1:3] == [0, 0]  # no waiting at Telegram behind the hold
    finally:
        worker.stop()
        worker.thread.join(timeout=10)
