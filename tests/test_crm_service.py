"""CRM service rules: ingest, chat status, assignment, tags, segments, tasks, access."""

from __future__ import annotations

import csv
import io
import os
import sqlite3
import time
from typing import Any

import pytest

from okline.crm import CrmDatabase, CrmError, CrmService
from okline.crm.filters import normalize_filter
from okline.crm.schema import MIGRATIONS

OWNER = {"id": "owner", "role": "member"}
STAFF = {"id": "staff", "role": "operator", "teamOwnerId": "owner", "staffAccountIds": ["acct-a"]}
VIEWER = {"id": "viewer", "role": "viewer", "teamOwnerId": "owner", "staffAccountIds": ["acct-a"]}
PERMISSIONS = {
    "member": {"read", "crm", "manage_crm"},
    "operator": {"read", "crm"},
    "viewer": {"read"},
}
MID_1 = "u" + "1" * 32
MID_2 = "u" + "2" * 32


class FakeContext:
    def __init__(self) -> None:
        self.notified: list[tuple[str, dict[str, Any]]] = []
        self.changed: list[str] = []

    def crm_tenant_id(self, user):
        return str((user or {}).get("teamOwnerId") or (user or {}).get("id") or "")

    def crm_scope(self, user):
        if (user or {}).get("teamOwnerId"):
            return list(user.get("staffAccountIds") or [])
        return None

    def crm_accounts(self, owner_id):
        if owner_id != "owner":
            return []
        return [
            {"id": "acct-a", "label": "Shop A", "channel": "line"},
            {"id": "acct-b", "label": "Shop B", "channel": "line"},
            {"id": "tg-bot", "label": "Bot", "channel": "telegram_bot"},
        ]

    def crm_members(self, owner_id):
        return [
            {"id": "owner", "name": "Owner", "role": "owner", "active": True,
             "accountIds": ["acct-a", "acct-b", "tg-bot"]},
            {"id": "staff", "name": "Staff", "role": "staff", "active": True,
             "accountIds": ["acct-a"]},
        ]

    def crm_has_permission(self, user, permission):
        return permission in PERMISSIONS.get(str((user or {}).get("role") or ""), set())

    def crm_notify(self, user_id, payload):
        self.notified.append((user_id, payload))

    def crm_changed(self, account_ids):
        self.changed.extend(account_ids)


PG_URL = os.getenv("OKLINE_TEST_DATABASE_URL", "")


def reset_postgres(url: str) -> None:
    """Drop every table so each PostgreSQL-backed test starts empty."""
    import psycopg

    with psycopg.connect(url, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
        for (name,) in rows:
            conn.execute(f'DROP TABLE IF EXISTS "{name}" CASCADE')


# Every rule runs on SQLite; set OKLINE_TEST_DATABASE_URL to also run on PostgreSQL.
@pytest.fixture(params=["sqlite", *(["postgres"] if PG_URL else [])])
def crm(request):
    ctx = FakeContext()
    if request.param == "postgres":
        reset_postgres(PG_URL)
        db = CrmDatabase(database_url=PG_URL)
    else:
        db = CrmDatabase()
    service = CrmService(db, ctx)
    try:
        yield service, ctx
    finally:
        service.close()


def _now() -> int:
    return int(time.time() * 1000)


def _msg(external_id, direction, at, text="hi", kind="", sender=""):
    return {
        "externalId": external_id,
        "direction": direction,
        "senderKind": kind or ("customer" if direction == "in" else "app"),
        "senderId": sender,
        "text": text,
        "contentType": 0,
        "at": at,
    }


def _ingest(service, account="acct-a", chat=MID_1, messages=(), name="", channel="line"):
    return service.ingest(
        owner_id="owner",
        account_id=account,
        channel=channel,
        chat_id=chat,
        contact={"externalId": chat if channel == "line" else chat[3:], "name": name},
        messages=list(messages),
    )


def test_ingest_creates_customer_and_ignores_duplicates(crm):
    service, _ctx = crm
    now = _now()
    first = _ingest(service, messages=[_msg("m1", "in", now - 1000, "สวัสดีครับ")], name="Somchai")
    assert first["created"] is True and first["status"] == "new"
    again = _ingest(service, messages=[_msg("m1", "in", now - 1000, "สวัสดีครับ")])
    assert again["created"] is False and again["inserted"] == 0

    listed = service.list_customers(OWNER, {})
    assert listed["total"] == 1
    customer = listed["customers"][0]
    assert customer["displayName"] == "Somchai"
    assert customer["lastPreview"] == "สวัสดีครับ"
    assert customer["identities"][0]["channel"] == "line"
    assert customer["conversations"][0]["status"] == "new"


def test_same_person_on_two_accounts_is_one_customer(crm):
    service, _ctx = crm
    now = _now()
    a = _ingest(service, account="acct-a", messages=[_msg("a1", "in", now - 5000)])
    b = _ingest(service, account="acct-b", messages=[_msg("b1", "in", now - 4000)])
    assert a["customerId"] == b["customerId"]
    assert a["conversationId"] != b["conversationId"]
    detail = service.customer_detail(OWNER, a["customerId"])
    assert {c["accountId"] for c in detail["customer"]["conversations"]} == {"acct-a", "acct-b"}


def test_old_chat_seen_first_time_starts_closed(crm):
    service, _ctx = crm
    old = _now() - 10 * 86_400_000
    result = _ingest(service, messages=[_msg("x", "in", old)])
    assert result["status"] == "closed"


def test_customer_message_reopens_closed_chat_and_staff_reply_claims_it(crm):
    service, _ctx = crm
    now = _now()
    first = _ingest(service, messages=[_msg("m1", "in", now - 60_000)])
    service.set_status(OWNER, {"conversationId": first["conversationId"], "status": "closed"})

    reopened = _ingest(service, messages=[_msg("m2", "in", now - 30_000)])
    assert reopened["reopened"] is True and reopened["status"] == "new"

    replied = _ingest(service, messages=[_msg("m3", "out", now - 10_000, kind="staff",
                                              sender="staff")])
    assert replied["status"] == "open"
    assert replied["assignedTo"] == "staff"

    timeline = service.customer_detail(OWNER, first["customerId"])["timeline"]
    kinds = [item.get("kind") for item in timeline if item["type"] == "event"]
    assert "assigned" in kinds and "status" in kinds


def test_ai_reply_does_not_claim_or_open(crm):
    service, _ctx = crm
    now = _now()
    _ingest(service, messages=[_msg("m1", "in", now - 60_000)])
    result = _ingest(service, messages=[_msg("m2", "out", now - 30_000, kind="ai")])
    assert result["status"] == "new" and result["assignedTo"] == ""


def test_staff_only_sees_customers_on_shared_accounts(crm):
    service, _ctx = crm
    now = _now()
    on_a = _ingest(service, account="acct-a", chat=MID_1, messages=[_msg("a", "in", now)])
    on_b = _ingest(service, account="acct-b", chat=MID_2, messages=[_msg("b", "in", now)])
    manual = service.create_customer(OWNER, {"displayName": "Walk-in"})["customer"]

    staff_ids = {c["id"] for c in service.list_customers(STAFF, {})["customers"]}
    assert staff_ids == {on_a["customerId"], manual["id"]}
    with pytest.raises(CrmError) as exc:
        service.customer_detail(STAFF, on_b["customerId"])
    assert exc.value.status == 404
    with pytest.raises(CrmError):
        service.conversations_map(STAFF, "acct-b")


def test_assignment_rules(crm):
    service, ctx = crm
    now = _now()
    convo = _ingest(service, messages=[_msg("m1", "in", now)])
    body = {"accountId": "acct-a", "chatId": MID_1}

    # Staff may claim, but not hand a chat to somebody else.
    claimed = service.assign(STAFF, {**body, "assignee": "me"})
    assert claimed["conversation"]["assignedTo"] == "staff"
    assert claimed["conversation"]["status"] == "open"
    with pytest.raises(CrmError) as exc:
        service.assign(STAFF, {**body, "assignee": "owner"})
    assert exc.value.code == "assign_forbidden"

    # A manager can reassign; only the person given the chat is told.
    service.assign(OWNER, {**body, "assignee": "owner"})
    assert ctx.notified == []
    service.assign(OWNER, {"conversationId": convo["conversationId"], "assignee": "staff"})
    assert [user for user, _ in ctx.notified] == ["staff"]
    assert ctx.notified[-1][1]["mid"] == MID_1

    # Someone without access to the account cannot be the assignee.
    _ingest(service, account="acct-b", chat=MID_2, messages=[_msg("b1", "in", now)])
    with pytest.raises(CrmError) as exc:
        service.assign(OWNER, {"accountId": "acct-b", "chatId": MID_2, "assignee": "staff"})
    assert exc.value.code == "assignee_invalid"
    with pytest.raises(CrmError) as exc:
        service.assign(VIEWER, {**body, "assignee": "me"})
    assert exc.value.status == 403


def test_assigning_an_unseen_chat_creates_its_record(crm):
    service, _ctx = crm
    result = service.assign(OWNER, {"accountId": "acct-a", "chatId": MID_2, "assignee": "me",
                                    "name": "New lead"})
    assert result["customer"]["displayName"] == "New lead"
    assert result["conversation"]["assignedTo"] == "owner"
    with pytest.raises(CrmError) as exc:
        service.assign(OWNER, {"accountId": "acct-a", "chatId": "c" + "9" * 32, "assignee": "me"})
    assert exc.value.code == "chat_not_supported"


def test_tags_filters_and_segments(crm):
    service, _ctx = crm
    now = _now()
    vip = _ingest(service, chat=MID_1, messages=[_msg("a", "in", now)], name="VIP Lady")
    other = _ingest(service, chat=MID_2, messages=[_msg("b", "in", now - 40 * 86_400_000)],
                    name="Quiet Guy")
    tagged = service.tag_customers(
        OWNER, {"customerIds": [vip["customerId"]], "add": [{"name": "VIP", "color": "#FF0000"}]}
    )
    vip_tag = tagged["customers"][0]["tags"][0]
    assert vip_tag["name"] == "VIP" and vip_tag["color"] == "#ff0000"

    by_tag = service.list_customers(OWNER, {"filter": {"tagsAny": [vip_tag["id"]]}})
    assert [c["id"] for c in by_tag["customers"]] == [vip["customerId"]]
    by_text = service.list_customers(OWNER, {"filter": {"q": "quiet"}})
    assert [c["id"] for c in by_text["customers"]] == [other["customerId"]]
    stale = service.list_customers(OWNER, {"filter": {"lastContact": {"olderThan": 30}}})
    assert [c["id"] for c in stale["customers"]] == [other["customerId"]]

    saved = service.save_segment(OWNER, {"name": "VIPs", "filter": {"tagsAll": [vip_tag["id"]]}})
    assert saved["segments"][0]["count"] == 1
    with pytest.raises(CrmError):
        service.save_segment(STAFF, {"name": "nope", "filter": {}})
    in_segment = service.list_customers(OWNER, {"segmentId": saved["id"]})
    assert in_segment["total"] == 1

    # Deleting the tag removes it from the saved segment too.
    service.delete_tag(OWNER, vip_tag["id"])
    segments = service.list_segments(OWNER)["segments"]
    assert segments[0]["filter"] == {} and segments[0]["count"] == 2


def test_merge_moves_everything_to_one_customer(crm):
    service, _ctx = crm
    now = _now()
    line = _ingest(service, chat=MID_1, messages=[_msg("a", "in", now - 9000)], name="Nok")
    telegram = _ingest(service, account="tg-bot", chat="tgu12345", channel="telegram_bot",
                       messages=[_msg("t1", "in", now - 1000, "from telegram")], name="Nok TG")
    service.add_note(OWNER, {"customerId": telegram["customerId"], "body": "prefers Telegram"})
    service.tag_customers(OWNER, {"customerIds": [telegram["customerId"]], "add": ["loyal"]})

    merged = service.merge_customers(
        OWNER, {"targetId": line["customerId"], "sourceIds": [telegram["customerId"]]}
    )
    customer = merged["customer"]
    assert {i["channel"] for i in customer["identities"]} == {"line", "telegram"}
    assert [t["name"] for t in customer["tags"]] == ["loyal"]
    assert merged["notes"][0]["body"] == "prefers Telegram"
    assert customer["lastPreview"] == "from telegram"
    assert service.list_customers(OWNER, {})["total"] == 1
    # New Telegram messages keep landing on the merged customer.
    later = _ingest(service, account="tg-bot", chat="tgu12345", channel="telegram_bot",
                    messages=[_msg("t2", "in", now)])
    assert later["customerId"] == line["customerId"]


def test_renaming_keeps_their_own_name_and_can_go_back(crm):
    service, ctx = crm
    customer_id = _ingest(service, messages=[_msg("m1", "in", _now())], name="Somchai")["customerId"]

    renamed = service.update_customer(OWNER, {"id": customer_id, "displayName": "คุณสมชาย VIP"})
    customer = renamed["customer"]
    assert (customer["displayName"], customer["nameCustom"]) == ("คุณสมชาย VIP", True)
    assert customer["identities"][0]["name"] == "Somchai"  # their own name stays
    assert "acct-a" in ctx.changed  # open chat lists pick the new name up
    assert service.custom_names("owner") == {MID_1: "คุณสมชาย VIP"}
    chat = service.conversations_map(OWNER, "acct-a")["conversations"][MID_1]
    assert (chat["customerName"], chat["nameCustom"]) == ("คุณสมชาย VIP", True)
    assert service.list_customers(OWNER, {"q": "somchai"})["total"] == 1
    event = next(item for item in renamed["timeline"] if item.get("kind") == "renamed")
    assert event["data"] == {"from": "Somchai", "to": "คุณสมชาย VIP", "own": False}

    # An empty name, or their own name typed back, means no name of the team's.
    back = service.update_customer(OWNER, {"id": customer_id, "displayName": ""})["customer"]
    assert (back["displayName"], back["nameCustom"]) == ("Somchai", False)
    assert service.custom_names("owner") == {}
    service.update_customer(OWNER, {"id": customer_id, "displayName": "Som"})
    own = service.update_customer(OWNER, {"id": customer_id, "displayName": "Somchai"})
    assert own["customer"]["nameCustom"] is False
    with pytest.raises(CrmError):
        service.update_customer(VIEWER, {"id": customer_id, "displayName": "Viewer's pick"})


def test_a_name_follows_the_person_until_the_team_renames_them(crm):
    service, _ctx = crm

    def say(external_id, name):
        return _ingest(service, account="tg-bot", chat="tgu777", channel="telegram_bot",
                       messages=[_msg(external_id, "in", _now())], name=name)

    customer_id = say("t1", "Tom")["customerId"]
    say("t2", "Thomas")
    customer = service.customer_detail(OWNER, customer_id)["customer"]
    assert (customer["displayName"], customer["nameCustom"]) == ("Thomas", False)

    service.update_customer(OWNER, {"id": customer_id, "displayName": "Tom from Chiang Mai"})
    say("t3", "Tommy")
    customer = service.customer_detail(OWNER, customer_id)["customer"]
    assert customer["displayName"] == "Tom from Chiang Mai"
    assert customer["identities"][0]["name"] == "Tommy"
    assert service.custom_names("owner") == {}  # only LINE lists chats under LINE's names


def test_chat_lists_know_the_own_name_of_line_oa_and_telegram_people(crm):
    service, _ctx = crm
    oa_chat = "U" + "3" * 32
    service.ingest(owner_id="owner", account_id="oa-1", channel="line_oa", chat_id=oa_chat,
                   contact={"externalId": oa_chat, "name": "Mint Shop"},
                   messages=[_msg("o1", "in", _now())])
    _ingest(service, account="tg-bot", chat="tgu55", channel="telegram_bot",
            messages=[_msg("t1", "in", _now())], name="Ploy")
    for row in service.inbox_rows(["oa-1", "tg-bot"]):
        service.update_customer(OWNER, {"id": row["customer_id"], "displayName": "Renamed"})
    own = {row["chat_id"]: row["identity_name"] for row in service.inbox_rows(["oa-1", "tg-bot"])}
    assert own == {oa_chat: "Mint Shop", "tgu55": "Ploy"}
    for account, chat_id, name in (("oa-1", oa_chat, "Mint Shop"), ("tg-bot", "tgu55", "Ploy")):
        [row] = service.account_chats(account)
        assert (row["chat_id"], row["display_name"], row["identity_name"]) == (chat_id, "Renamed", name)


def test_customers_the_team_adds_keep_their_name_through_a_merge(crm):
    service, _ctx = crm
    manual = service.create_customer(OWNER, {"displayName": "Walk-in Nok"})["customer"]
    assert manual["nameCustom"] is True
    line = _ingest(service, messages=[_msg("a", "in", _now())])  # LINE has no name for them yet
    merged = service.merge_customers(
        OWNER, {"targetId": line["customerId"], "sourceIds": [manual["id"]]}
    )["customer"]
    assert (merged["displayName"], merged["nameCustom"]) == ("Walk-in Nok", True)
    service.set_identity_profile("owner", "line", MID_1, name="Nok")
    customer = service.customer_detail(OWNER, merged["id"])["customer"]
    assert customer["displayName"] == "Walk-in Nok"
    assert service.custom_names("owner") == {MID_1: "Walk-in Nok"}


def test_names_typed_before_the_upgrade_stay_as_the_team_gave_them(tmp_path):
    path = tmp_path / "crm.sqlite3"
    conn = sqlite3.connect(path)
    for statement in MIGRATIONS[0][1]:
        conn.execute(statement)
    conn.execute("CREATE TABLE crm_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.execute("INSERT INTO crm_meta (key, value) VALUES ('schema', '1')")
    people = [("own", "Somchai", "Somchai"), ("typed", "VIP Som", "Som"),
              ("manual", "Walk-in", None), ("unnamed", "", "")]
    for customer_id, display_name, own in people:
        conn.execute(
            "INSERT INTO crm_customers (id, owner_id, display_name, created_at, updated_at) "
            "VALUES (?, 'owner', ?, 1, 1)",
            (customer_id, display_name),
        )
        if own is not None:
            conn.execute(
                "INSERT INTO crm_identities (id, owner_id, customer_id, channel, external_id, "
                "name, created_at, updated_at) VALUES (?, 'owner', ?, 'line', ?, ?, 1, 1)",
                (f"i-{customer_id}", customer_id, f"u-{customer_id}", own),
            )
    conn.commit()
    conn.close()

    service = CrmService(CrmDatabase(sqlite_path=str(path)), FakeContext())
    try:
        rows = service.db.query("SELECT id, name_custom FROM crm_customers")
    finally:
        service.close()
    assert {row["id"]: row["name_custom"] for row in rows} == {
        "own": 0, "typed": 1, "manual": 1, "unnamed": 0,
    }


def test_tasks_reminders_and_permissions(crm):
    service, ctx = crm
    now = _now()
    convo = _ingest(service, messages=[_msg("m1", "in", now)])
    task = service.create_task(
        OWNER,
        {"customerId": convo["customerId"], "title": "Call back", "dueAt": now - 1,
         "assignedTo": "staff"},
    )["task"]
    assert ctx.notified[-1][0] == "staff"
    assert service.list_tasks(STAFF, {"scope": "mine"})["tasks"][0]["id"] == task["id"]
    assert service.summary(STAFF)["tasksDue"] == 1

    reminders = service.due_task_reminders(now + 1000)
    assert [r["id"] for r in reminders] == [task["id"]]
    assert service.due_task_reminders(now + 2000) == []  # each task reminds once

    done = service.update_task(STAFF, {"id": task["id"], "status": "done"})["task"]
    assert done["status"] == "done" and done["doneAt"]
    with pytest.raises(CrmError):
        service.create_task(VIEWER, {"title": "nope"})


def test_notes_belong_to_their_author(crm):
    service, _ctx = crm
    convo = _ingest(service, messages=[_msg("m1", "in", _now())])
    note = service.add_note(STAFF, {"customerId": convo["customerId"], "body": "likes blue"})
    other_staff = {**STAFF, "id": "staff-2"}
    with pytest.raises(CrmError) as exc:
        service.update_note(other_staff, {"id": note["note"]["id"], "body": "changed"})
    assert exc.value.code == "note_not_yours"
    service.update_note(OWNER, {"id": note["note"]["id"], "pinned": True})
    assert service.customer_detail(OWNER, convo["customerId"])["notes"][0]["pinned"] is True


def test_export_csv_escapes_formulas_and_needs_manager(crm):
    service, _ctx = crm
    service.create_customer(OWNER, {"displayName": "=HYPERLINK(1)", "phone": "081"})
    data = service.export_csv(OWNER, {}).decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(data)))
    assert rows[0][1] == "name"
    assert rows[1][1] == "'=HYPERLINK(1)"
    with pytest.raises(CrmError):
        service.export_csv(STAFF, {})


def test_auto_close_and_settings(crm):
    service, _ctx = crm
    now = _now()
    convo = _ingest(service, messages=[_msg("m1", "in", now - 2 * 86_400_000)])
    assert convo["status"] == "new"
    service.save_settings(OWNER, {"autoCloseDays": 1})
    touched = service.auto_close(now)
    assert touched == ["acct-a"]
    panel = service.chat_panel(OWNER, "acct-a", MID_1)
    assert panel["conversation"]["status"] == "closed"
    with pytest.raises(CrmError):
        service.save_settings(OWNER, {"autoCloseDays": 99})


def test_delete_and_purge_account(crm):
    service, _ctx = crm
    now = _now()
    convo = _ingest(service, messages=[_msg("m1", "in", now)])
    _ingest(service, account="acct-b", chat=MID_2, messages=[_msg("m2", "in", now)])
    service.purge_account("acct-b")
    assert service.db.scalar("SELECT COUNT(*) FROM crm_conversations") == 1
    with pytest.raises(CrmError):
        service.delete_customer(STAFF, convo["customerId"])
    service.delete_customer(OWNER, convo["customerId"])
    assert service.db.scalar("SELECT COUNT(*) FROM crm_messages") == 0


def test_normalize_filter_drops_unknown_values():
    flt = normalize_filter(
        {"q": "  a   b ", "channels": ["line", "fax"], "statuses": ["open", "lost"],
         "lastContact": {"within": 7, "olderThan": 30}, "hasPhone": "yes", "junk": 1}
    )
    assert flt == {"q": "a b", "channels": ["line"], "statuses": ["open"],
                   "lastContact": {"within": 7}}
