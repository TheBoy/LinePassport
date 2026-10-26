"""The whole app on PostgreSQL, the way production runs it.

Skipped unless OKLINE_TEST_DATABASE_URL points at a throwaway database: every
table in it is dropped before each test.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Iterator

import pytest
import requests

from okline.webapp import OkLineWebHandler, OkLineWebServer, WebConfig, WebState

PG_URL = os.getenv("OKLINE_TEST_DATABASE_URL", "")
pytestmark = pytest.mark.skipif(not PG_URL, reason="set OKLINE_TEST_DATABASE_URL to run")

CUSTOMER = "u" + "a" * 32
TOKEN = "777777777:" + "P" * 35


def _reset() -> None:
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
        for (name,) in rows:
            conn.execute(f'DROP TABLE IF EXISTS "{name}" CASCADE')


class FakeBot:
    sent: list[tuple] = []

    def __init__(self, token, proxy=None):
        self.token = token

    def get_me(self):
        return {"id": 777777777, "is_bot": True, "username": "pg_bot", "first_name": "PG"}

    def delete_webhook(self):
        return True

    def get_updates(self, offset=0, *, timeout=25, allowed=None):
        time.sleep(0.05)
        return []

    def send_message(self, chat_id, text, **kwargs):
        FakeBot.sent.append((chat_id, text))
        return {"message_id": 5000 + len(FakeBot.sent), "date": int(time.time())}

    def close(self):
        return None


@pytest.fixture
def live(tmp_path) -> Iterator[tuple[str, WebState]]:
    _reset()
    FakeBot.sent = []
    root = tmp_path / ".okline"
    config = WebConfig(
        host="127.0.0.1",
        port=0,
        tokens_file=str(tmp_path / "tokens.json"),
        state_dir=str(root),
        accounts_file=str(root / "accounts.json"),
        accounts_dir=str(root / "accounts"),
        schedules_file=str(root / "schedules.json"),
        auth_file=str(root / "auth.json"),
        database_url=PG_URL,
    )
    server = OkLineWebServer(("127.0.0.1", 0), OkLineWebHandler)
    server.state = WebState(config)
    server.state.telegram.client_factory = FakeBot
    server.state.broadcast_runner.close()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", server.state
    finally:
        server.shutdown()
        server.state.close()
        server.server_close()


def test_customers_and_telegram_on_postgres(live):
    base, state = live
    assert state.store.label() == "postgresql" and state.crm.db.dialect == "postgres"
    session = requests.Session()
    session.post(f"{base}/api/auth/setup",
                 json={"email": "owner@example.com", "password": "owner-pass"}, timeout=10).raise_for_status()
    owner_id = session.get(f"{base}/api/auth/status", timeout=10).json()["user"]["id"]
    state.account_store.data["accounts"] = [
        {"id": "acct", "label": "Shop", "mid": "U-me", "ownerId": owner_id, "tokenFile": ""}
    ]
    state.account_store.save()
    state.web_auth.grant_account_access(owner_id, "acct")

    # LINE inbox -> customer card.
    now = int(time.time() * 1000)
    state._after_inbox_fetch("acct", {"messageBoxes": [{"id": CUSTOMER, "lastMessages": [
        {"id": "m1", "from": CUSTOMER, "to": "U-me", "createdTime": str(now), "contentType": 0,
         "text": "สวัสดีค่ะ"}]}]})
    state.crm_executor.submit(lambda: None).result(timeout=10)
    found = session.post(f"{base}/api/crm/customers/search", json={"filter": {"q": ""}}, timeout=10)
    assert found.status_code == 200, found.text
    customer = found.json()["customers"][0]
    assert customer["lastPreview"] == "สวัสดีค่ะ"

    # Tags, notes, follow-ups, segments, assignment, export.
    tagged = session.post(f"{base}/api/crm/customers/tags", json={
        "customerIds": [customer["id"]], "add": [{"name": "VIP"}]}, timeout=10)
    assert tagged.status_code == 200, tagged.text
    tag_id = tagged.json()["customers"][0]["tags"][0]["id"]
    assert session.post(f"{base}/api/crm/notes/create", json={
        "customerId": customer["id"], "body": "ลูกค้าประจำ"}, timeout=10).status_code == 200
    assert session.post(f"{base}/api/crm/tasks/create", json={
        "customerId": customer["id"], "title": "โทรกลับ", "dueAt": now + 60_000}, timeout=10).status_code == 200
    segment = session.post(f"{base}/api/crm/segments/save", json={
        "name": "VIP", "filter": {"tagsAny": [tag_id], "lastContact": {"within": 7}}}, timeout=10)
    assert segment.status_code == 200 and segment.json()["segments"][0]["count"] == 1
    claimed = session.post(f"{base}/api/crm/chat/assign", json={
        "accountId": "acct", "chatId": CUSTOMER, "assignee": "me"}, timeout=10)
    assert claimed.status_code == 200 and claimed.json()["conversation"]["status"] == "open"
    detail = session.get(f"{base}/api/crm/customer?id={customer['id']}", timeout=10).json()
    assert {item["kind"] for item in detail["timeline"] if item["type"] == "event"} >= {"assigned", "tag", "note", "task"}
    export = session.get(f"{base}/api/crm/customers/export", timeout=10)
    assert export.status_code == 200 and "VIP" in export.content.decode("utf-8-sig")

    # A Telegram bot account: receive, answer, and message a segment.
    created = session.post(f"{base}/api/telegram/accounts/create", json={"token": TOKEN}, timeout=10)
    assert created.status_code == 200, created.text
    tg = created.json()["account"]["id"]
    state.tg_receive(tg, {"externalId": "1", "chatId": "tgu55", "userId": "55", "name": "Ploy",
                          "username": "ploy", "text": "มีของไหม", "contentType": 0, "meta": {},
                          "at": int(time.time() * 1000), "outgoing": False})
    convs = session.get(f"{base}/api/conversations?accountId={tg}", timeout=10).json()
    assert convs["conversations"][0]["mid"] == "tgu55" and convs["conversations"][0]["unreadCount"] == 1
    sent = session.post(f"{base}/api/send", json={"accountId": tg, "to": "tgu55", "text": "มีค่ะ"}, timeout=10)
    assert sent.status_code == 200, sent.text
    broadcast = session.post(f"{base}/api/crm/broadcasts/create", json={
        "filter": {"channels": ["telegram"]}, "accountId": tg, "text": "โปร {ชื่อลูกค้า}"}, timeout=10)
    assert broadcast.status_code == 200, broadcast.text
    state.run_due_broadcasts()
    assert FakeBot.sent[-1] == (55, "โปร Ploy")
    listed = session.get(f"{base}/api/crm/broadcasts", timeout=10).json()["broadcasts"][0]
    assert listed["status"] == "done" and listed["sent"] == 1

    # Removing the Telegram account clears its chats from the CRM.
    removed = session.post(f"{base}/api/accounts/delete", json={"accountId": tg}, timeout=10)
    assert removed.status_code == 200, removed.text
    assert state.crm.conversation_for_chat(tg, "tgu55") is None


def test_a_restart_keeps_customers_like_a_deploy(tmp_path):
    _reset()
    root = tmp_path / ".okline"
    config = WebConfig(
        state_dir=str(root),
        accounts_file=str(root / "accounts.json"),
        accounts_dir=str(root / "accounts"),
        schedules_file=str(root / "schedules.json"),
        auth_file=str(root / "auth.json"),
        database_url=PG_URL,
    )
    owner = {"id": "owner", "role": "member"}
    first = WebState(config)
    try:
        first.crm.create_customer(owner, {"displayName": "Kept", "phone": "0812345678"})
    finally:
        first.close()

    second = WebState(config)  # same database, new process: migrations must be a no-op
    try:
        assert second.crm.db.migrate() == 1
        listed = second.crm.list_customers(owner, {"filter": {"q": "0812345678"}})
        assert [c["displayName"] for c in listed["customers"]] == ["Kept"]
        tables = {
            row["tablename"]
            for row in second.crm.db.query(
                "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
            )
        }
        assert {"crm_customers", "crm_conversations", "crm_messages", "crm_broadcasts",
                "crm_meta", "okline_web_state"} <= tables
    finally:
        second.close()
