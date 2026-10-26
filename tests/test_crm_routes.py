"""CRM through the web app: inbox ingest, HTTP routes, roles, export and push routing."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterator

import pytest
import requests

from okline.webapp import OkLineWebHandler, OkLineWebServer, WebConfig, WebState

CUSTOMER = "U" + "a" * 32
OTHER = "U" + "b" * 32


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
def live(tmp_path, monkeypatch) -> Iterator[tuple[str, WebState]]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    server = OkLineWebServer(("127.0.0.1", 0), OkLineWebHandler)
    server.state = WebState(_make_config(tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.state
    finally:
        server.shutdown()
        server.state.close()
        server.server_close()


def _seed_account(state, owner_id, account_id="acct", mid="U-me"):
    state.account_store.data["accounts"] = [
        {
            "id": account_id,
            "label": "Test LINE",
            "mid": mid,
            "ownerId": owner_id,
            "tokenFile": "",
            "createdAt": "2026-07-11T12:00:00",
            "updatedAt": "2026-07-11T12:00:00",
        }
    ]
    state.account_store.data["activeAccountId"] = account_id
    state.account_store.save()
    state.web_auth.grant_account_access(owner_id, account_id)


def _box(mid, *messages):
    return {
        "id": mid,
        "lastMessages": [
            {"id": f"{mid}-{i}", "from": sender, "to": "", "createdTime": str(at),
             "contentType": 0, "text": text}
            for i, (sender, at, text) in enumerate(messages)
        ],
    }


def _drain(state):
    state.crm_executor.submit(lambda: None).result(timeout=10)


def _sessions(base, state):
    owner = requests.Session()
    owner.post(
        f"{base}/api/auth/setup",
        json={"email": "owner@example.com", "password": "owner-pass"},
        timeout=5,
    ).raise_for_status()
    owner_user = owner.get(f"{base}/api/auth/status", timeout=5).json()["user"]
    _seed_account(state, owner_user["id"])
    owner.post(
        f"{base}/api/team/create",
        json={"email": "staff@example.com", "password": "staff-pass",
              "displayName": "Staff", "accountIds": ["acct"]},
        timeout=5,
    ).raise_for_status()
    staff = requests.Session()
    staff.post(
        f"{base}/api/auth/login",
        json={"email": "staff@example.com", "password": "staff-pass"},
        timeout=5,
    ).raise_for_status()
    staff_user = staff.get(f"{base}/api/auth/status", timeout=5).json()["user"]
    return owner, owner_user, staff, staff_user


def test_inbox_fetch_fills_crm_and_routes_enforce_roles(live):
    base, state = live
    owner, owner_user, staff, staff_user = _sessions(base, state)
    now = int(time.time() * 1000)
    state._after_inbox_fetch(
        "acct",
        {"messageBoxes": [
            _box(CUSTOMER, (CUSTOMER, now - 5000, "ราคาเท่าไหร่คะ")),
            _box("c" + "9" * 32, ("U-other", now - 4000, "group chat")),
        ]},
    )
    _drain(state)

    found = owner.post(f"{base}/api/crm/customers/search", json={}, timeout=5)
    assert found.status_code == 200, found.text
    customers = found.json()["customers"]
    assert len(customers) == 1  # groups are not customers
    assert customers[0]["lastPreview"] == "ราคาเท่าไหร่คะ"
    assert customers[0]["conversations"][0]["status"] == "new"

    # Staff claims the chat; the chat-list map shows it.
    claimed = staff.post(
        f"{base}/api/crm/chat/assign",
        json={"accountId": "acct", "chatId": CUSTOMER, "assignee": "me"},
        timeout=5,
    )
    assert claimed.status_code == 200, claimed.text
    chat_map = owner.get(f"{base}/api/crm/conversations?accountId=acct", timeout=5).json()
    assert chat_map["conversations"][CUSTOMER]["assignedTo"] == staff_user["id"]
    assert {m["role"] for m in chat_map["members"]} == {"owner", "staff"}

    # Staff cannot hand the chat to the owner, export, or save segments.
    refused = staff.post(
        f"{base}/api/crm/chat/assign",
        json={"accountId": "acct", "chatId": CUSTOMER, "assignee": owner_user["id"]},
        timeout=5,
    )
    assert refused.status_code == 403 and refused.json()["code"] == "assign_forbidden"
    assert staff.get(f"{base}/api/crm/customers/export", timeout=5).status_code == 403
    assert staff.post(
        f"{base}/api/crm/segments/save", json={"name": "x", "filter": {}}, timeout=5
    ).status_code == 403

    export = owner.get(f"{base}/api/crm/customers/export", timeout=5)
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert "attachment" in export.headers["content-disposition"]

    summary = staff.get(f"{base}/api/crm/summary", timeout=5).json()
    assert summary["myOpen"] == 1 and summary["unassigned"] == 0
    assert owner.get(f"{base}/api/crm/nope", timeout=5).status_code == 404


def test_staff_reply_from_line_is_attributed_and_claims(live):
    base, state = live
    _owner, _owner_user, _staff, staff_user = _sessions(base, state)
    now = int(time.time() * 1000)
    state._after_inbox_fetch(
        "acct", {"messageBoxes": [_box(CUSTOMER, (CUSTOMER, now - 9000, "hello"))]}
    )
    _drain(state)
    state.note_outgoing("acct", CUSTOMER, staff_user["id"])
    state._after_inbox_fetch(
        "acct",
        {"messageBoxes": [_box(CUSTOMER, (CUSTOMER, now - 9000, "hello"),
                               ("U-me", now, "สวัสดีค่ะ"))]},
    )
    _drain(state)
    conv = state.crm.conversation_for_chat("acct", CUSTOMER)
    assert conv["assigned_to"] == staff_user["id"] and conv["status"] == "open"
    row = state.crm.db.query_one(
        "SELECT sender_kind, sender_id FROM crm_messages WHERE direction = 'out'"
    )
    assert row == {"sender_kind": "staff", "sender_id": staff_user["id"]}


def test_push_for_an_assigned_chat_only_reaches_the_assignee(live, monkeypatch):
    _base, state = live
    state.web_auth.setup("owner@example.com", "owner-pass")
    owner_id = state.web_auth.data["users"][0]["id"]
    _seed_account(state, owner_id)
    now = int(time.time() * 1000)
    for chat in (CUSTOMER, OTHER):
        state.crm.ingest(
            owner_id=owner_id, account_id="acct", channel="line", chat_id=chat,
            contact={"externalId": chat},
            messages=[{"externalId": chat, "direction": "in", "at": now, "text": "hi"}],
        )
    conv = state.crm.conversation_for_chat("acct", CUSTOMER)
    state.crm.system_assign(owner_id, conv["id"], "staff-1")

    monkeypatch.setattr(
        state, "_push_recipients",
        lambda account_id: [("staff-1", [{"endpoint": "s"}]), ("staff-2", [{"endpoint": "t"}])],
    )
    delivered: list[tuple[str, str | None]] = []

    class _Immediate:
        def submit(self, fn, subscription, payload):
            delivered.append((subscription["endpoint"], payload.get("mid")))

        def shutdown(self, **_kwargs):
            return None

    monkeypatch.setattr(state, "push_executor", _Immediate())
    state._dispatch_push("acct", [CUSTOMER, OTHER])
    assert sorted(delivered) == [("s", CUSTOMER), ("s", OTHER), ("t", OTHER)]


def test_removing_an_account_purges_its_chats(live):
    _base, state = live
    state.crm.ingest(
        owner_id="owner", account_id="acct", channel="line", chat_id=CUSTOMER,
        contact={"externalId": CUSTOMER},
        messages=[{"externalId": "m", "direction": "in", "at": int(time.time() * 1000)}],
    )
    state.account_store.data["accounts"] = [
        {"id": "acct", "label": "x", "mid": "U-me", "ownerId": "owner", "tokenFile": ""}
    ]
    state._commit_account_removal("acct")
    assert state.crm.conversation_for_chat("acct", CUSTOMER) is None


def test_crm_changes_wake_the_live_stream_without_touching_inbox_version(live):
    _base, state = live
    inbox, crm = state.inbox_version("acct"), state.crm_version("acct")
    state.crm_changed(["acct"])
    assert state.wait_live_event("acct", inbox, crm, 0.5) == (inbox, crm + 1)
    assert state.inbox_version("acct") == inbox
