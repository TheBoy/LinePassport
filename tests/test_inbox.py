"""The all-channels inbox: chats from every LINE and Telegram account in one list."""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Iterator

import pytest
import requests

from okline.telegram.channels import normalize_message
from okline.webapp import OkLineWebHandler, OkLineWebServer, WebConfig, WebState

TOKEN = "555555555:" + "C" * 35
CUSTOMER = "U" + "a" * 32
GROUP = "c" + "9" * 32
TG_CUSTOMER = 4242
TG_CHAT = f"tgu{TG_CUSTOMER}"


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
    def __init__(self, token, proxy=None):
        self.token = token

    def get_me(self):
        return {"id": 555555555, "is_bot": True, "username": "shop_bot", "first_name": "Shop Bot"}

    def delete_webhook(self):
        return True

    def get_updates(self, offset=0, *, timeout=25, allowed=None):
        time.sleep(0.05)
        return []

    def send_message(self, chat_id, text, **kwargs):
        return {"message_id": 901, "date": int(time.time())}

    def user_photo_file_id(self, user_id):
        return ""

    def close(self):
        return None


@pytest.fixture
def state(tmp_path, monkeypatch) -> Iterator[WebState]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    web = WebState(_make_config(tmp_path))
    web.telegram.client_factory = FakeTg
    try:
        yield web
    finally:
        web.close()


@pytest.fixture
def live(tmp_path, monkeypatch) -> Iterator[tuple[str, WebState]]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
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


def _owner(state) -> dict:
    state.web_auth.setup("owner@example.com", "owner-pass")
    return state.web_auth.data["users"][0]


def _seed_line(state, owner) -> None:
    state.account_store.data["accounts"] = [
        {
            "id": "acct",
            "label": "Shop LINE",
            "mid": "U-me",
            "ownerId": owner["id"],
            "tokenFile": "",
            "createdAt": "2026-07-11T12:00:00",
            "updatedAt": "2026-07-11T12:00:00",
        }
    ]
    state.account_store.save()
    state.web_auth.grant_account_access(owner["id"], "acct")


def _box(mid, unread, *messages):
    return {
        "id": mid,
        "unreadCount": unread,
        "lastMessages": [
            {"id": f"{mid}-{i}", "from": sender, "to": "", "createdTime": str(at),
             "contentType": 0, "text": text}
            for i, (sender, at, text) in enumerate(messages)
        ],
    }


def _line_inbox(state, payload) -> None:
    """What a real LINE fetch does: cache the snapshot, then fill the CRM."""
    state.inbox_snapshots["acct"] = (time.monotonic(), payload)
    state._after_inbox_fetch("acct", payload)
    state.crm_executor.submit(lambda: None).result(timeout=10)


def _tg_incoming(text) -> dict:
    return normalize_message({
        "message_id": 1,
        "date": int(time.time()),
        "chat": {"id": TG_CUSTOMER, "type": "private", "first_name": "Ploy", "username": "ploy"},
        "from": {"id": TG_CUSTOMER, "first_name": "Ploy", "username": "ploy"},
        "text": text,
    })


def test_inbox_lists_every_channel_and_where_each_chat_comes_from(state):
    owner = _owner(state)
    _seed_line(state, owner)
    tg_id = state.add_telegram_bot({"token": TOKEN, "label": "Shop TG"}, owner)["account"]["id"]
    now = int(time.time() * 1000)
    state.group_name_cache["acct"] = (time.monotonic(), {GROUP: "ทีมขาย"})
    _line_inbox(state, {"messageBoxes": [
        _box(CUSTOMER, 2, (CUSTOMER, now - 9000, "ราคาเท่าไหร่คะ")),
        _box(GROUP, 1, ("U-other", now - 5000, "ประชุมกี่โมง")),
    ]})
    state.tg_receive(tg_id, _tg_incoming("มีสินค้าไหม"))

    owner = state.web_auth._find_user(owner["id"])  # adding the bot saved a new record
    data = state.inbox(owner)
    assert "acct" not in state.inbox_watchers  # listing chats never speeds up LINE polling
    assert {a["id"]: (a["channel"], a["label"], a["connected"]) for a in data["accounts"]} == {
        "acct": ("line", "Shop LINE", False),  # no LINE session file in this test
        tg_id: ("telegram_bot", "Shop TG", True),
    }
    assert [chat["key"] for chat in data["chats"]] == [
        f"{tg_id}|{TG_CHAT}", f"acct|{GROUP}", f"acct|{CUSTOMER}",
    ]
    tg, group, line = data["chats"]
    assert (tg["channel"], tg["kind"], tg["name"], tg["username"]) == ("telegram", "contact", "Ploy", "ploy")
    assert (tg["unread"], tg["lastPreview"], tg["status"], tg["lastFromMe"]) == (1, "มีสินค้าไหม", "new", False)
    assert (group["channel"], group["kind"], group["name"]) == ("line", "group", "ทีมขาย")
    assert (group["unread"], group["lastPreview"], group["customerId"]) == (1, "ประชุมกี่โมง", "")
    # LINE unread counts come from LINE itself, not from the CRM's own count.
    assert (line["channel"], line["kind"], line["unread"]) == ("line", "contact", 2)
    assert (line["lastPreview"], line["status"]) == ("ราคาเท่าไหร่คะ", "new")
    assert line["customerId"] and line["conversationId"]
    assert data["unread"] == 4 and data["me"] == owner["id"]

    # Replying from the inbox claims the chat and clears its unread count.
    state.tg_send(tg_id, {"to": TG_CHAT, "text": "มีค่ะ"}, owner)
    tg = state.inbox(owner)["chats"][0]
    assert (tg["lastFromMe"], tg["unread"], tg["assignedTo"]) == (True, 0, owner["id"])
    assert tg["lastPreview"] == "มีค่ะ"


def test_staff_see_only_the_accounts_they_can_open(state):
    owner = _owner(state)
    _seed_line(state, owner)
    tg_id = state.add_telegram_bot({"token": TOKEN, "label": "Shop TG"}, owner)["account"]["id"]
    owner = state.web_auth._find_user(owner["id"])
    staff = state.web_auth.create_staff(owner, "staff@example.com", "staff-pass", "Staff", ["acct"])
    _line_inbox(state, {"messageBoxes": [_box(CUSTOMER, 1, (CUSTOMER, 1000, "hi"))]})
    state.tg_receive(tg_id, _tg_incoming("hello"))

    assert {chat["accountId"] for chat in state.inbox(owner)["chats"]} == {"acct", tg_id}
    seen = state.inbox(state.web_auth._find_user(staff["id"]))
    assert [account["id"] for account in seen["accounts"]] == ["acct"]
    assert [chat["chatId"] for chat in seen["chats"]] == [CUSTOMER]


def test_group_names_load_in_the_background(state, monkeypatch):
    owner = _owner(state)
    _seed_line(state, owner)

    class FakeLine:
        def get_all_chat_mids(self):
            return {"memberChatMids": [GROUP]}

        def get_chats(self, mids):
            return {"chats": [{"chatMid": mid, "chatName": "ทีมขาย"} for mid in mids]}

    jobs = []
    monkeypatch.setattr(state, "get_api", lambda account_id: FakeLine())
    monkeypatch.setattr(state.ops_refresh_executor, "submit", lambda fn, *args: jobs.append((fn, args)))

    # Building the list never waits for LINE, and only one refresh runs at a time.
    assert state._group_names("acct") == {}
    assert state._group_names("acct") == {}
    assert len(jobs) == 1
    before = state.inbox_version("acct")
    fn, args = jobs[0]
    fn(*args)
    assert state._group_names("acct") == {GROUP: "ทีมขาย"}
    assert len(jobs) == 1  # fresh names are reused
    assert state.inbox_version("acct") == before + 1  # open pages reload the list
    state._refresh_group_names("acct")
    assert state.inbox_version("acct") == before + 1  # nothing new, nobody woken


def test_wait_any_live_event_wakes_for_any_account(state):
    versions = {"a": (0, 0), "b": (0, 0)}
    assert state.wait_any_live_event(versions, 0.05) is None
    timer = threading.Timer(0.05, state._bump_crm_version, args=("b",))
    timer.start()
    try:
        assert state.wait_any_live_event(versions, 5) == {"a": (0, 0), "b": (0, 1)}
    finally:
        timer.cancel()
    state._bump_inbox_version("a")
    assert state.wait_any_live_event(versions, 5) == {"a": (1, 0), "b": (0, 1)}


def test_inbox_routes_need_sign_in_and_stream_changes(live):
    base, state = live
    assert requests.get(f"{base}/api/inbox", timeout=5).status_code == 401
    assert requests.get(f"{base}/api/events/inbox", timeout=5).status_code == 401
    session = requests.Session()
    session.post(
        f"{base}/api/auth/setup",
        json={"email": "owner@example.com", "password": "owner-pass"},
        timeout=5,
    ).raise_for_status()
    owner = state.web_auth.data["users"][0]
    _seed_line(state, owner)
    tg_id = state.add_telegram_bot({"token": TOKEN, "label": "Shop TG"}, owner)["account"]["id"]
    owner = state.web_auth._find_user(owner["id"])
    state.web_auth.create_staff(owner, "staff@example.com", "staff-pass", "Staff", ["acct"])
    staff = requests.Session()
    staff.post(
        f"{base}/api/auth/login",
        json={"email": "staff@example.com", "password": "staff-pass"},
        timeout=5,
    ).raise_for_status()
    _line_inbox(state, {"messageBoxes": [_box(CUSTOMER, 1, (CUSTOMER, 1000, "hi"))]})

    listed = session.get(f"{base}/api/inbox", timeout=5)
    assert listed.status_code == 200, listed.text
    assert [chat["key"] for chat in listed.json()["chats"]] == [f"acct|{CUSTOMER}"]

    with staff.get(f"{base}/api/events/inbox", stream=True, timeout=10) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines(chunk_size=1, decode_unicode=True)
        assert next(lines) == "retry: 5000"
        assert next(lines) == "event: hello"
        state._bump_inbox_version(tg_id)  # staff cannot open this account: no event
        state._bump_crm_version("acct")
        for line in lines:
            if line.startswith("data: ") and "accountIds" in line:
                assert json.loads(line[len("data: "):])["accountIds"] == ["acct"]
                break
        else:
            pytest.fail("no inbox_changed event")
