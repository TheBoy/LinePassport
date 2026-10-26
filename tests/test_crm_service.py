"""CRM service rules: ingest, chat status, assignment, tags, segments, tasks, access."""

from __future__ import annotations

import csv
import io
import time
from typing import Any

import pytest

from okline.crm import CrmDatabase, CrmError, CrmService
from okline.crm.filters import normalize_filter

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


@pytest.fixture
def crm():
    ctx = FakeContext()
    service = CrmService(CrmDatabase(), ctx)
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
