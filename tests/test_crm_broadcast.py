"""Messages to a segment: targeting, pacing, quotas and controls."""

from __future__ import annotations

import time
from collections.abc import Iterator

import pytest

from okline.crm import CrmError
from okline.telegram.bot_api import TelegramApiError
from okline.webapp import WebConfig, WebError, WebState

ALICE = "u" + "a" * 32
BOB = "u" + "b" * 32
CAROL = "u" + "c" * 32


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


class FakeLine:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def send_text(self, to, text):
        self.sent.append((to, text))
        return {"id": f"b{len(self.sent)}"}


@pytest.fixture
def state(tmp_path, monkeypatch) -> Iterator[WebState]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    web = WebState(_make_config(tmp_path))
    web.broadcast_runner.close()  # the tests drive sending themselves
    try:
        yield web
    finally:
        web.close()


def _setup(state, monkeypatch) -> tuple[dict, FakeLine]:
    state.web_auth.setup("owner@example.com", "owner-pass")
    owner_id = state.web_auth.data["users"][0]["id"]
    state.account_store.data["accounts"] = [
        {"id": "shop", "label": "Shop", "mid": "U-me", "ownerId": owner_id, "tokenFile": ""},
        {"id": "other", "label": "Other", "mid": "U-me2", "ownerId": owner_id, "tokenFile": ""},
    ]
    state.account_store.save()
    state.web_auth.grant_account_access(owner_id, "shop")
    state.web_auth.grant_account_access(owner_id, "other")
    owner = dict(state.web_auth._find_user(owner_id))
    now = int(time.time() * 1000)
    for account, chat, name in (("shop", ALICE, "Alice"), ("shop", BOB, "Bob"), ("other", CAROL, "Carol")):
        state.crm.ingest(
            owner_id=owner_id, account_id=account, channel="line", chat_id=chat,
            contact={"externalId": chat, "name": name},
            messages=[{"externalId": f"{chat}-1", "direction": "in", "text": "hi", "at": now}],
        )
    line = FakeLine()
    monkeypatch.setattr(state, "get_api", lambda account_id=None, require_auth=True: line)
    return owner, line


def _segment(state, owner) -> str:
    tag = state.crm.create_tag(owner, {"name": "VIP"})["id"]
    ids = [c["id"] for c in state.crm.list_customers(owner, {})["customers"]]
    state.crm.tag_customers(owner, {"customerIds": ids, "add": [tag]})
    return state.crm.save_segment(owner, {"name": "VIP", "filter": {"tagsAny": [tag]}})["id"]


def _due(state, broadcast_id):
    state.crm.delay_broadcast(broadcast_id, 0)
    state.run_due_broadcasts()


def test_targets_people_who_chat_with_the_account(state, monkeypatch):
    owner, _line = _setup(state, monkeypatch)
    segment = _segment(state, owner)
    preview = state.crm.preview_broadcast(owner, {"segmentId": segment, "accountId": "shop"})
    assert preview["inSegment"] == 3 and preview["reachable"] == 2
    with pytest.raises(CrmError):
        state.crm.create_broadcast(owner, {"segmentId": segment, "accountId": "shop", "text": ""})
    staff = {**owner, "role": "operator"}
    with pytest.raises(CrmError):
        state.crm.create_broadcast(staff, {"segmentId": segment, "accountId": "shop", "text": "x"})
    created = state.crm.create_broadcast(
        owner, {"segmentId": segment, "accountId": "shop", "text": "สวัสดี {ชื่อลูกค้า}"}
    )["broadcast"]
    assert created["total"] == 2 and created["status"] == "running" and created["name"] == "VIP"

    paused = state.crm.control_broadcast(owner, {"id": created["id"], "action": "pause"})
    assert paused["broadcast"]["status"] == "paused"
    with pytest.raises(CrmError):
        state.crm.control_broadcast(owner, {"id": created["id"], "action": "pause"})
    state.crm.control_broadcast(owner, {"id": created["id"], "action": "resume"})
    cancelled = state.crm.control_broadcast(owner, {"id": created["id"], "action": "cancel"})
    assert cancelled["broadcast"]["status"] == "cancelled"


def test_sends_one_by_one_within_the_quota(state, monkeypatch):
    owner, line = _setup(state, monkeypatch)
    segment = _segment(state, owner)
    state._save_schedule_settings("shop", {"maxPerHour": 1})
    broadcast = state.crm.create_broadcast(
        owner, {"segmentId": segment, "accountId": "shop", "text": "สวัสดี {ชื่อลูกค้า}"}
    )["broadcast"]

    state.run_due_broadcasts()
    assert len(line.sent) == 1
    to, text = line.sent[0]
    assert text == {ALICE: "สวัสดี Alice", BOB: "สวัสดี Bob"}[to]
    # The next one waits: a 30-90 s gap, then the hourly limit.
    state.run_due_broadcasts()
    assert len(line.sent) == 1
    _due(state, broadcast["id"])
    assert len(line.sent) == 1
    current = state.crm.list_broadcasts(owner)["broadcasts"][0]
    assert current["note"] == "waiting_for_quota" and current["sent"] == 1

    state._save_schedule_settings("shop", {"maxPerHour": 30})
    _due(state, broadcast["id"])
    assert len(line.sent) == 2
    done = state.crm.list_broadcasts(owner)["broadcasts"][0]
    assert done["status"] == "done" and done["sent"] == 2
    # A segment message is not a reply: the customer still waits in KPI terms.
    assert state.schedule_usage("shop")["lastHour"] == 2


def test_disconnected_account_pauses_and_line_test_send(state, monkeypatch):
    owner, line = _setup(state, monkeypatch)
    segment = _segment(state, owner)
    result = state.test_broadcast(
        {"accountId": "shop", "text": "ลองส่ง {ชื่อลูกค้า}", "sampleName": "Alice"}, owner
    )
    assert line.sent == [("U-me", "ลองส่ง Alice")] and result["text"] == "ลองส่ง Alice"
    broadcast = state.crm.create_broadcast(
        owner, {"segmentId": segment, "accountId": "shop", "text": "hi"}
    )["broadcast"]
    state.account_store.mark_session_reauth_required("shop", "signed out")
    state.run_due_broadcasts()
    stopped = state.crm.list_broadcasts(owner)["broadcasts"][0]
    assert stopped["id"] == broadcast["id"]
    assert stopped["status"] == "paused" and stopped["note"] == "account_disconnected"


class FakeBot:
    blocked: set[int] = set()
    sent: list[tuple] = []

    def __init__(self, token, proxy=None):
        self.token = token

    def get_me(self):
        return {"id": 42424242, "is_bot": True, "username": "shop_bot", "first_name": "Shop"}

    def delete_webhook(self):
        return True

    def get_updates(self, offset=0, *, timeout=25, allowed=None):
        time.sleep(0.05)
        return []

    def send_message(self, chat_id, text, **kwargs):
        if chat_id in FakeBot.blocked:
            raise TelegramApiError("Forbidden: bot was blocked by the user", error_code=403)
        FakeBot.sent.append((chat_id, text))
        # Telegram numbers both directions of a chat from one sequence.
        return {"message_id": 1000 + len(FakeBot.sent), "date": int(time.time())}

    def close(self):
        return None


def test_telegram_bot_broadcast_skips_blocked_people(state):
    FakeBot.blocked = {2}
    FakeBot.sent = []
    state.telegram.client_factory = FakeBot
    state.web_auth.setup("owner@example.com", "owner-pass")
    owner = dict(state.web_auth.data["users"][0])
    account_id = state.add_telegram_bot({"token": "42424242:" + "Z" * 35}, owner)["account"]["id"]
    owner = dict(state.web_auth._find_user(owner["id"]))
    for user_id in (1, 2):
        state.tg_receive(account_id, {
            "externalId": "1", "chatId": f"tgu{user_id}", "userId": str(user_id),
            "name": f"P{user_id}", "username": "", "text": "hi", "contentType": 0,
            "meta": {}, "at": int(time.time() * 1000), "outgoing": False,
        })
    broadcast = state.crm.create_broadcast(
        owner, {"filter": {}, "accountId": account_id, "text": "โปรใหม่ {name}"}
    )["broadcast"]
    for _ in range(2):
        _due(state, broadcast["id"])
    final = state.crm.list_broadcasts(owner)["broadcasts"][0]
    assert final["status"] == "done" and final["sent"] == 1 and final["failed"] == 1
    assert FakeBot.sent == [(1, "โปรใหม่ P1")]
    with pytest.raises(WebError) as exc:
        state.test_broadcast({"accountId": account_id, "text": "x"}, owner)
    assert exc.value.code == "broadcast_test_unsupported"
    out = state.crm.db.query_one(
        "SELECT sender_kind FROM crm_messages WHERE direction = 'out' AND text = 'โปรใหม่ P1'"
    )
    assert out == {"sender_kind": "broadcast"}
