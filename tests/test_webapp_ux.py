"""Tests for the UX-plan backend: KPI tracking, presence, staff sharing, events."""

from __future__ import annotations

import datetime as dt
import threading
import time
from collections.abc import Iterator

import pytest
import requests

from okline import webapp as webapp_module
from okline.webapp import (
    OkLineWebHandler,
    OkLineWebServer,
    WebAuth,
    WebConfig,
    WebError,
    WebState,
)

_CUSTOMER_MID = "U" + "a" * 32


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
def web_state(tmp_path, monkeypatch) -> Iterator[WebState]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    state = WebState(_make_config(tmp_path))
    try:
        yield state
    finally:
        state.close()


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


class _SendFakeApi:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text(self, to, text):
        self.sent.append((to, text))
        return {"id": f"m{len(self.sent)}"}


class _HandlerStub:
    def __init__(self, state, user):
        self.server = type("Server", (), {"state": state})()
        self.current_user = user


def _owner(web_state):
    token = web_state.web_auth.setup("owner@example.com", "owner-pass")
    owner = web_state.web_auth.current_user(WebAuth.cookie_header(token))
    assert owner
    return owner


def _seed_owned_account(web_state, owner, account_id="acct", mid="U-me"):
    web_state.account_store.data["accounts"] = [
        {
            "id": account_id,
            "label": "Test LINE",
            "mid": mid,
            "ownerId": owner["id"],
            "tokenFile": "",
            "createdAt": "2026-07-11T12:00:00",
            "updatedAt": "2026-07-11T12:00:00",
        }
    ]
    web_state.account_store.data["activeAccountId"] = account_id
    web_state.account_store.save()
    web_state.web_auth.grant_account_access(owner["id"], account_id)
    return web_state.web_auth._find_user(owner["id"])


def _box(mid, *messages):
    return {
        "id": mid,
        "lastMessages": [
            {"id": f"{mid}-{index}", "from": sender, "createdTime": str(at), "contentType": 0}
            for index, (sender, at) in enumerate(messages)
        ],
    }


def test_operator_send_logs_web_user(web_state):
    owner = _seed_owned_account(web_state, _owner(web_state))
    result = OkLineWebHandler._send(
        _HandlerStub(web_state, owner),
        _SendFakeApi(),
        {"accountId": "acct", "to": _CUSTOMER_MID, "text": "hello"},
    )
    assert result["ok"] is True
    logs = web_state.list_bot_logs("acct", owner)["logs"]
    sent = [log for log in logs if log["action"] == "operator_send"]
    assert sent and sent[0]["data"]["webUserId"] == owner["id"]
    assert sent[0]["data"]["to"] == _CUSTOMER_MID
    assert sent[0]["data"]["kind"] == "text"


def test_room_state_tracks_waiting_and_attributes_replies(web_state, monkeypatch):
    owner = _seed_owned_account(web_state, _owner(web_state))
    base = 1_800_000_000_000
    clock = {"now": base / 1000}
    monkeypatch.setattr(webapp_module.time, "time", lambda: clock["now"])

    # First sight of a room is history: no KPI event, but it is waiting.
    web_state.update_room_state(
        "acct", "U-me", [_box(_CUSTOMER_MID, (_CUSTOMER_MID, base - 5000))]
    )
    assert web_state.room_states("acct")[_CUSTOMER_MID]["status"] == "waiting"
    assert web_state._kpi_responses("acct") == []

    # A web operator reply answers the room immediately and is attributed.
    clock["now"] = (base + 60_000) / 1000
    web_state.note_outgoing("acct", _CUSTOMER_MID, owner["id"])
    room = web_state.room_states("acct")[_CUSTOMER_MID]
    assert room["status"] == "answered"
    assert room["answeredBy"] == owner["id"]
    responses = web_state._kpi_responses("acct")
    assert responses[-1]["answeredBy"] == owner["id"]
    assert responses[-1]["responseSeconds"] == 65

    # A scheduled send does not count as answering a new question.
    web_state.update_room_state(
        "acct", "U-me", [_box(_CUSTOMER_MID, (_CUSTOMER_MID, base + 100_000))]
    )
    web_state.note_outgoing("acct", _CUSTOMER_MID, "schedule")
    web_state.update_room_state(
        "acct",
        "U-me",
        [_box(_CUSTOMER_MID, (_CUSTOMER_MID, base + 100_000), ("U-me", base + 110_000))],
    )
    assert web_state.room_states("acct")[_CUSTOMER_MID]["status"] == "waiting"

    # A reply typed on the phone (no web attribution) counts as line_app.
    web_state.outgoing_attribution.clear()
    web_state.update_room_state(
        "acct",
        "U-me",
        [
            _box(
                _CUSTOMER_MID,
                (_CUSTOMER_MID, base + 100_000),
                ("U-me", base + 110_000),
                ("U-me", base + 160_000),
            )
        ],
    )
    assert web_state.room_states("acct")[_CUSTOMER_MID]["status"] == "answered"
    assert web_state._kpi_responses("acct")[-1]["answeredBy"] == "line_app"

    # Silence for 24h closes the room; an unanswered one is recorded as missed.
    web_state.update_room_state(
        "acct", "U-me", [_box(_CUSTOMER_MID, (_CUSTOMER_MID, base + 200_000))]
    )
    clock["now"] = (base + 200_000 + 25 * 3600 * 1000) / 1000
    web_state.update_room_state("acct", "U-me", [])
    assert web_state.room_states("acct")[_CUSTOMER_MID]["status"] == "closed"
    assert web_state._kpi_responses("acct")[-1]["missed"] is True


def test_group_chats_are_not_tracked_for_kpi(web_state):
    _seed_owned_account(web_state, _owner(web_state))
    web_state.update_room_state("acct", "U-me", [_box("C-group", ("U-x", 1000))])
    assert web_state.room_states("acct") == {}


def test_inbox_fetch_bumps_event_version_only_on_change(web_state):
    _seed_owned_account(web_state, _owner(web_state))
    payload = {"messageBoxes": [_box(_CUSTOMER_MID, (_CUSTOMER_MID, 1000))]}
    web_state._after_inbox_fetch("acct", payload)
    first = web_state.inbox_version("acct")
    web_state._after_inbox_fetch("acct", payload)
    assert web_state.inbox_version("acct") == first
    assert web_state.wait_inbox_event("acct", first, 0.01) is None
    changed = {
        "messageBoxes": [_box(_CUSTOMER_MID, (_CUSTOMER_MID, 1000), (_CUSTOMER_MID, 2000))]
    }
    web_state._after_inbox_fetch("acct", changed)
    assert web_state.wait_inbox_event("acct", first, 0.01) == first + 1


def test_presence_heartbeats_merge_into_segments(web_state, monkeypatch):
    owner = _owner(web_state)
    clock = {"now": 1_800_000_000.0}
    monkeypatch.setattr(webapp_module.time, "time", lambda: clock["now"])

    def beat(state, advance=30):
        clock["now"] += advance
        return web_state.record_presence(owner, {"state": state, "notification": "granted"})

    beat("active", 0)
    beat("active")
    beat("active")
    beat("away")
    beat("away")
    beat("standby", 600)  # a long gap means the user was offline in between
    web_state.flush_presence()
    segments = [
        item
        for item in web_state.store.get("presence", {})["segments"]
        if item["userId"] == owner["id"]
    ]
    assert [item["state"] for item in segments] == ["active", "away", "standby"]
    start = 1_800_000_000_000
    assert segments[0]["from"] == start and segments[0]["to"] == start + 90_000
    assert segments[1]["from"] == start + 90_000 and segments[1]["to"] == start + 120_000
    assert segments[2]["from"] == start + 720_000
    with pytest.raises(WebError):
        web_state.record_presence(owner, {"state": "sleeping"})


def test_kpi_report_aggregates_responses_and_presence(web_state):
    owner = _seed_owned_account(web_state, _owner(web_state))
    staff = web_state.web_auth.create_staff(
        owner, "staff@example.com", "staff-pass", "Staff", ["acct"]
    )
    start = 1_780_000_000_000
    web_state.store.set(
        "kpi_responses:acct",
        {
            "responses": [
                {
                    "chatMid": "U1",
                    "askedAt": start,
                    "answeredAt": start + 60_000,
                    "answeredBy": staff["id"],
                    "responseSeconds": 60,
                },
                {
                    "chatMid": "U2",
                    "askedAt": start + 1000,
                    "answeredAt": start + 601_000,
                    "answeredBy": staff["id"],
                    "responseSeconds": 600,
                },
                {
                    "chatMid": "U3",
                    "askedAt": start + 2000,
                    "answeredAt": start + 7000,
                    "answeredBy": "ai",
                    "responseSeconds": 5,
                },
                {"chatMid": "U4", "askedAt": start + 3000, "answeredBy": None, "missed": True},
            ]
        },
    )
    web_state.presence_cache = [
        {"userId": staff["id"], "state": "active", "from": start, "to": start + 1_800_000},
        {
            "userId": staff["id"],
            "state": "away",
            "from": start + 1_800_000,
            "to": start + 3_600_000,
        },
    ]
    report = web_state.kpi_report("acct", owner, from_ms=start, to_ms=start + 3_600_000)
    assert report["tiles"]["chats"] == 4
    assert report["tiles"]["slaPercent"] == 50.0  # 60s and 5s within 300s, of 4
    assert report["split"] == {"ai": 1, "human": 2, "lineApp": 0, "missed": 1}
    row = next(item for item in report["perUser"] if item["id"] == staff["id"])
    assert row["chats"] == 2 and row["avgResponseSeconds"] == 330
    assert row["presence"]["active"] == 1800 and row["presence"]["away"] == 1800
    # No shift and no business hours: "offline" is not meaningful.
    assert row["presence"]["offline"] is None
    staff_user = web_state.web_auth._find_user(staff["id"])
    with pytest.raises(WebError):
        web_state.kpi_report("acct", staff_user, from_ms=start, to_ms=start + 1)


def test_kpi_shift_windows_expand_weekly_rules():
    start = int(dt.datetime(2027, 1, 4, 0, 0).timestamp() * 1000)  # a Monday
    windows = webapp_module._time_windows(
        [{"days": [0], "start": "09:00", "end": "17:00"}], start, start + 86_400_000
    )
    assert windows == [(start + 9 * 3_600_000, start + 17 * 3_600_000)]
    overnight = webapp_module._time_windows(
        [{"days": [0], "start": "22:00", "end": "06:00"}], start, start + 2 * 86_400_000
    )
    assert overnight == [(start + 22 * 3_600_000, start + 30 * 3_600_000)]


def test_kpi_settings_owner_only_and_validated(web_state):
    owner = _seed_owned_account(web_state, _owner(web_state))
    staff = web_state.web_auth.create_staff(
        owner, "s@example.com", "staff-pass", "S", ["acct"]
    )
    saved = web_state.save_kpi_settings(
        {
            "accountId": "acct",
            "settings": {
                "slaSeconds": 120,
                "shifts": {
                    staff["id"]: [{"days": [0, 1], "start": "09:00", "end": "18:00"}],
                    "stranger": [],
                },
            },
        },
        owner,
    )
    assert saved["settings"]["slaSeconds"] == 120
    assert list(saved["settings"]["shifts"]) == [staff["id"]]
    staff_user = web_state.web_auth._find_user(staff["id"])
    mine = web_state.kpi_settings("acct", staff_user)
    assert mine["myShifts"][0]["start"] == "09:00" and mine["canEdit"] is False
    with pytest.raises(WebError):
        web_state.save_kpi_settings({"accountId": "acct", "settings": {}}, staff_user)


def test_owner_can_share_account_with_staff(web_state):
    auth = web_state.web_auth
    owner = _seed_owned_account(web_state, _owner(web_state))
    member_token = auth.register("other@example.com", "member-pass", "Other")
    other = auth.current_user(WebAuth.cookie_header(member_token))

    staff = web_state.create_staff(
        {
            "email": "staff@example.com",
            "password": "staff-pass",
            "displayName": "Staff",
            "accountIds": ["acct"],
        },
        owner,
    )["user"]
    assert staff["role"] == "operator" and staff["staffAccountIds"] == ["acct"]
    staff_user = auth._find_user(staff["id"])
    assert auth.can_access_account(staff_user, "acct") is True
    assert auth.owns_account(staff_user, "acct") is False
    assert auth.allowed_account_ids(staff_user) == {"acct"}

    # Owners cannot hand out accounts they do not own; staff cannot build teams.
    with pytest.raises(WebError):
        web_state.create_staff(
            {"email": "x@example.com", "password": "x-pass-123", "accountIds": ["acct"]},
            other,
        )
    with pytest.raises(WebError):
        web_state.team_payload(staff_user)
    with pytest.raises(WebError):
        web_state.update_staff({"id": staff["id"], "displayName": "Nope"}, other)

    # Staff work on the shop owner's AI data.
    assert web_state.tenant_user(staff_user)["id"] == owner["id"]

    web_state.update_staff({"id": staff["id"], "accountIds": []}, owner)
    assert auth.can_access_account(auth._find_user(staff["id"]), "acct") is False
    web_state.delete_staff({"id": staff["id"]}, owner)
    assert auth._find_user(staff["id"]) is None


def test_deleting_shared_account_removes_staff_access(web_state):
    auth = web_state.web_auth
    owner = _seed_owned_account(web_state, _owner(web_state))
    staff = auth.create_staff(owner, "staff@example.com", "staff-pass", "Staff", ["acct"])
    web_state.delete_account("acct", owner)
    assert auth._find_user(staff["id"])["staffAccountIds"] == []


def test_events_stream_pushes_conversation_updates(live):
    base, state = live
    session = requests.Session()
    session.post(
        f"{base}/api/auth/setup",
        json={"email": "owner@example.com", "password": "owner-pass"},
        timeout=5,
    ).raise_for_status()
    user = session.get(f"{base}/api/auth/status", timeout=5).json()["user"]
    _seed_owned_account(state, state.web_auth._find_user(user["id"]))
    with session.get(f"{base}/api/events?accountId=acct", stream=True, timeout=10) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        lines = response.iter_lines(chunk_size=1, decode_unicode=True)
        assert next(lines) == "retry: 5000"
        assert next(lines) == "event: hello"
        state._after_inbox_fetch(
            "acct", {"messageBoxes": [_box(_CUSTOMER_MID, (_CUSTOMER_MID, 1000))]}
        )
        for line in lines:
            if line.startswith("event: conversation_updated"):
                break
        else:
            pytest.fail("no conversation_updated event")


class _FakeGemini:
    instances: list[_FakeGemini] = []

    def __init__(self, api_key, **kwargs):
        self.api_key = api_key
        _FakeGemini.instances.append(self)

    def embed(self, model, texts):
        return [[1.0, float(len(text) % 7 + 1)] for text in texts]

    def chat(self, **kwargs):
        if "เปิด" in kwargs["question"] and "เปิด" in kwargs["context"]:
            return {
                "answer": "เปิด 9 โมง",
                "canAnswer": True,
                "confidence": 0.9,
                "model": kwargs["model"],
            }
        return {"answer": "", "canAnswer": False, "confidence": 0.0, "model": kwargs["model"]}

    def list_models(self):
        return []


def test_owner_configures_gemini_without_god(web_state, monkeypatch):
    monkeypatch.setattr(webapp_module, "GeminiClient", _FakeGemini)
    owner = _seed_owned_account(web_state, _owner(web_state))
    assert web_state.assistant_settings(owner)["configured"] is False
    saved = web_state.save_assistant_settings(
        {"provider": "gemini", "apiKey": "AIza-test-key"}, owner
    )
    assert saved["configured"] is True and saved["provider"] == "gemini"
    assert saved["test"]["ok"] is True
    assert saved["source"] == "tenant"
    assert "AIza-test-key" not in str(web_state.store.get("global_ai", {}))
    # The same key now also powers the AI image tool when that slot was empty.
    assert web_state.ai_settings(owner)["providers"]["google"]["hasApiKey"] is True
    # Auto-reply for the owner's account can now be switched on.
    result = web_state.save_ai_auto_reply_settings("acct", {"enabled": True}, owner)
    assert result["enabled"] is True and result["aiConfigured"] is True

    staff = web_state.web_auth.create_staff(
        owner, "s@example.com", "staff-pass", "S", ["acct"]
    )
    staff_user = web_state.web_auth._find_user(staff["id"])
    with pytest.raises(WebError):
        web_state.save_assistant_settings({"provider": "gemini", "apiKey": "x"}, staff_user)
    assert web_state.assistant_settings(staff_user)["configured"] is True


def test_shop_topics_cite_sources_and_collect_faq(web_state, monkeypatch):
    monkeypatch.setattr(webapp_module, "GeminiClient", _FakeGemini)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "gemini", "apiKey": "k", "test": False}, owner
    )
    knowledge = web_state.save_assistant_knowledge(
        {
            "sections": [
                {"id": "hours", "title": "เวลาเปิด–ปิด", "body": "ร้านเปิด 9:00 ถึง 18:00"},
                {"id": "", "title": "เมนูราคา", "body": ""},
            ]
        },
        owner,
    )
    assert [item["title"] for item in knowledge["sections"]] == ["เวลาเปิด–ปิด", "เมนูราคา"]
    assert knowledge["knowledgeMarkdown"].startswith("## เวลาเปิด–ปิด")
    assert knowledge["indexMode"] == "embedding"

    staff = web_state.web_auth.create_staff(
        owner, "s@example.com", "staff-pass", "S", ["acct"]
    )
    staff_user = web_state.web_auth._find_user(staff["id"])
    answered = web_state.ask_global_ai("ร้านเปิดกี่โมง", staff_user)["question"]
    assert answered["status"] == "answered"
    assert answered["sources"][0]["name"] == "เวลาเปิด–ปิด"

    unknown = web_state.ask_global_ai("ส่งต่างประเทศไหม", staff_user)["question"]
    assert unknown["status"] == "unanswered"
    pending = web_state.tenant_unanswered(owner)["questions"]
    assert [item["id"] for item in pending] == [unknown["id"]]

    result = web_state.add_knowledge_faq(
        {"question": "ส่งต่างประเทศไหม", "answer": "ยังไม่ส่ง", "questionId": unknown["id"]},
        staff_user,
    )
    faq = next(item for item in result["sections"] if item["id"] == "faq")
    assert "ถาม: ส่งต่างประเทศไหม" in faq["body"] and "ตอบ: ยังไม่ส่ง" in faq["body"]
    assert web_state.tenant_unanswered(owner)["questions"] == []
    # Staff and owner see one shared shop data set.
    assert web_state.assistant_knowledge(owner)["sections"] == result["sections"]


def test_gemini_client_parses_structured_answer(monkeypatch):
    from okline import assistant

    calls = []

    class _Response:
        ok = True
        status_code = 200
        reason = "OK"

        def __init__(self, payload):
            self._payload = payload
            self.content = b"{}"

        def json(self):
            return self._payload

        def close(self):
            pass

    def fake_request(method, url, **kwargs):
        calls.append((method, url, kwargs))
        if url.endswith(":batchEmbedContents"):
            return _Response({"embeddings": [{"values": [0.1, 0.2]}]})
        return _Response(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [
                                {"text": '{"answer":"ok","canAnswer":true,"confidence":0.7}'}
                            ]
                        }
                    }
                ],
                "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 2},
            }
        )

    monkeypatch.setattr(assistant.requests, "request", fake_request)
    client = assistant.GeminiClient("secret")
    result = client.chat(
        model="gemini-x",
        question="q",
        context="",
        system_prompt="s",
        strict_knowledge=True,
        temperature=0.2,
    )
    assert result["answer"] == "ok" and result["canAnswer"] is True
    assert client.embed("", ["hello"]) == [[0.1, 0.2]]
    assert calls[0][2]["headers"]["x-goog-api-key"] == "secret"
    assert "key=" not in calls[0][1]  # the key never goes into the URL


def test_legacy_knowledge_markdown_splits_into_topics():
    sections = webapp_module._knowledge_sections_from_markdown(
        "intro line\n\n# เวลาเปิด\nทุกวัน\n\n## คำถามที่พบบ่อย\nถาม: a\nตอบ: b"
    )
    assert [item["title"] for item in sections] == ["", "เวลาเปิด", "คำถามที่พบบ่อย"]
    assert sections[2]["id"] == "faq"
    assert webapp_module._knowledge_sections_markdown(sections).startswith("intro line")


def test_presence_and_kpi_routes_enforce_roles(live):
    base, state = live
    owner_session = requests.Session()
    owner_session.post(
        f"{base}/api/auth/setup",
        json={"email": "owner@example.com", "password": "owner-pass"},
        timeout=5,
    ).raise_for_status()
    owner = owner_session.get(f"{base}/api/auth/status", timeout=5).json()["user"]
    _seed_owned_account(state, state.web_auth._find_user(owner["id"]))
    created = owner_session.post(
        f"{base}/api/team/create",
        json={
            "email": "staff@example.com",
            "password": "staff-pass",
            "displayName": "Staff",
            "accountIds": ["acct"],
        },
        timeout=5,
    )
    assert created.status_code == 200, created.text
    staff_session = requests.Session()
    staff_session.post(
        f"{base}/api/auth/login",
        json={"email": "staff@example.com", "password": "staff-pass"},
        timeout=5,
    ).raise_for_status()
    beat = staff_session.post(
        f"{base}/api/presence", json={"state": "active", "notification": "default"}, timeout=5
    )
    assert beat.status_code == 200 and beat.json()["state"] == "active"
    assert staff_session.get(f"{base}/api/kpi?accountId=acct", timeout=5).status_code == 403
    assert staff_session.get(f"{base}/api/team", timeout=5).status_code == 403
    report = owner_session.get(f"{base}/api/kpi?accountId=acct", timeout=5)
    assert report.status_code == 200
    assert {row["role"] for row in report.json()["perUser"]} == {"owner", "staff"}


def test_weekly_schedule_skips_days_and_daily_jobs_do_not_drift(monkeypatch):
    job = webapp_module._schedule_from_body(
        {
            "name": "weekly",
            "to": "u" + "1" * 32,
            "text": "hi",
            "mode": "repeat",
            "windowStart": "09:00",
            "windowEnd": "09:01",
            "intervalMinutes": 1440,
            "weekdays": [0, 2],  # Mon, Wed
        },
        account_id="acct",
    )
    assert job["weekdays"] == [0, 2]
    tuesday = dt.datetime(2027, 1, 5, 10, 0)
    nxt = dt.datetime.fromtimestamp(webapp_module._next_repeat_epoch(job, tuesday))
    assert nxt == dt.datetime(2027, 1, 6, 9, 0)

    class _Now(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2027, 1, 6, 9, 0, 40)

    monkeypatch.setattr(webapp_module._dt, "datetime", _Now)
    job["sentCount"] = 1
    webapp_module._advance_after_success(job)
    assert dt.datetime.fromtimestamp(job["nextRunAt"]) == dt.datetime(2027, 1, 11, 9, 0)
    with pytest.raises(WebError):
        webapp_module._schedule_weekdays([7])
    assert webapp_module._schedule_weekdays(list(range(7))) == []


# -- Frontend structure for the redesigned UI -------------------------------------
from okline.webapp import INDEX_HTML


def _js_block(start: str, end: str) -> str:
    begin = INDEX_HTML.index(start)
    return INDEX_HTML[begin : INDEX_HTML.index(end, begin)]


def test_sidebar_replaces_top_tabs_with_account_card_and_dev_group():
    assert 'class="tabbar sidenav"' in INDEX_HTML
    assert 'id="accountCard"' in INDEX_HTML and 'id="accountPopover"' in INDEX_HTML
    for tab in ("line", "assistant", "bot", "kpi", "incoming-api", "tools", "logs"):
        assert f'data-tab="{tab}"' in INDEX_HTML
    assert 'data-tab-panel="kpi"' in INDEX_HTML
    # Developer pages sit in their own small group at the bottom.
    assert INDEX_HTML.index('data-i18n="tabs.dev_group"') < INDEX_HTML.index(
        'id="tabIncomingApi"'
    )
    # One status dot replaces the contradictory pills.
    assert ".line-me-pills { display: none; }" in INDEX_HTML
    # Phones get a bottom bar.
    assert "@media (max-width: 640px)" in INDEX_HTML


def test_thai_ui_uses_plan_vocabulary():
    import re

    th_values = re.findall(r'\{th:\s*"((?:[^"\\]|\\.)*)"', INDEX_HTML)
    joined = "\n".join(th_values)
    for banned in (
        "Knowledge",
        "RAG",
        "AI Chat Bot",
        "แพทเทิร์น",
        "Contact",
        "chunks",
        "ส่งเดี๋ยวนี้",
    ):
        assert banned not in joined, banned
    assert '"tabs.line": {th: "แชท"' in INDEX_HTML
    assert '"tabs.assistant": {th: "ตอบอัตโนมัติ"' in INDEX_HTML
    assert '"tabs.bot": {th: "ส่งตามเวลา"' in INDEX_HTML


def test_chat_opens_on_click_and_accepts_pasted_chat_id():
    assert ".convo-advanced { display: none !important; }" in INDEX_HTML
    assert "function looksLikeMid(value)" in INDEX_HTML
    assert (
        'list.prepend(itemButton(t("chat.open_mid"), rawSearch, rawSearch, {}));' in INDEX_HTML
    )
    assert '<span data-i18n="chat.send">Send</span>' in INDEX_HTML
    assert "encrypt: Boolean(encrypt || state.forceEncrypt)" in INDEX_HTML


def test_notification_policy_matches_plan():
    block = _js_block(
        "    function processNewMessageNotifications(accountId, conversations) {",
        "    async function refreshConversationsInBackground() {",
    )
    assert "pageIsInFront()" in block
    assert "playSoftSound()" in block
    assert "dispatchBackgroundAlerts(accountId, direct);" in block
    assert "autoReplyCovers(item.mid)" in block
    assert "insideWorkingTime()" in block
    assert "renotify: true" not in INDEX_HTML
    assert "const NOTIFY_ROOM_COOLDOWN_MS = 120000;" in INDEX_HTML
    assert 'tag: "linepassport:digest"' in INDEX_HTML
    assert "requireInteraction: escalate" in INDEX_HTML
    assert 'const NOTIFY_SNOOZE_KEY = "okline.notifyBannerSnoozedUntil";' in INDEX_HTML
    assert 'id="notifyBanner"' in INDEX_HTML


def test_live_updates_use_sse_with_backoff_and_freshness_chip():
    assert "const SSE_BACKOFF_MS = [5000, 10000, 30000, 60000];" in INDEX_HTML
    assert 'source.addEventListener("conversation_updated"' in INDEX_HTML
    assert 'id="freshnessChip"' in INDEX_HTML
    assert 'chip.classList.toggle("stale", seconds > 60);' in INDEX_HTML


def test_presence_heartbeat_and_busy_toggle():
    assert (
        'await post("/api/presence", {state: presence, notification: notificationPermission(), ts: Date.now()});'
        in INDEX_HTML
    )
    assert "const PRESENCE_IDLE_MS = 15 * 60000;" in INDEX_HTML
    assert 'id="presenceChip"' in INDEX_HTML
    assert "function togglePresenceBusy()" in INDEX_HTML


def test_scheduler_wizard_has_three_steps_and_safe_defaults():
    for step in ("1", "2", "3"):
        assert f'data-wizard-pane="{step}"' in INDEX_HTML
    for kind in ("text", "image", "ai_image", "api"):
        assert f'data-wizard-type="{kind}"' in INDEX_HTML
    for freq in ("once", "daily", "weekly", "custom"):
        assert f'data-wizard-freq="{freq}"' in INDEX_HTML
    assert 'freq: "daily",' in INDEX_HTML  # once a day, never every 15 minutes
    assert 'id="wizardTime" type="time"' in INDEX_HTML
    assert '"/api/schedules/test"' in INDEX_HTML
    assert '["{name}", "var.name"]' in INDEX_HTML
    assert 'id="wizardAdvanced"' in INDEX_HTML and '"wizard.ban_warning"' in INDEX_HTML


def test_auto_reply_setup_checklist_and_shop_topics():
    assert 'id="assistantSetup"' in INDEX_HTML
    assert '"/api/assistant/settings"' in INDEX_HTML
    assert 'id="kbTopics"' in INDEX_HTML and 'id="kbChatLog"' in INDEX_HTML
    assert 'post("/api/assistant/knowledge", {sections})' in INDEX_HTML
    assert '"/api/assistant/knowledge/faq"' in INDEX_HTML
    assert "setTimeout(() => saveKbTopics().catch(toastError), 2000)" in INDEX_HTML


def test_add_account_waits_for_the_start_button_before_showing_qr():
    # No LINE account yet: the 4-step wizard opens on "Start"; the QR is only
    # requested after the person presses it (proxy can be set first).
    block = _js_block("    function openAddAccount(", "    async function cancelLogin()")
    assert "startLogin()" not in block
    assert 'id="beginAddAccountButton" class="primary wizard-start"' in INDEX_HTML
    assert '$("beginAddAccountButton").addEventListener("click", () => startLogin()' in INDEX_HTML
    assert "qr-first" not in INDEX_HTML
    assert INDEX_HTML.index('id="loginProxyEnabled"') < INDEX_HTML.index('id="beginAddAccountButton"')


# -- B6: LINE operation stream --------------------------------------------------
class _OpsEvent:
    def __init__(self, event):
        self.event = event


class _OpsFakeApi:
    def __init__(self, events=None, fail=False):
        self.events = events or []
        self.fail = fail
        self.box_calls = 0
        self.stream_calls = 0
        self.release = threading.Event()
        self.ops = self

    def stream(self, *, reconnect=True):
        self.stream_calls += 1
        if self.fail:
            raise RuntimeError("SSE open failed: HTTP 500")
        for name in self.events:
            yield _OpsEvent(name)
        # Keep the stream open until the test is done, like the real gateway.
        self.release.wait(5)

    def get_message_boxes(self, *, limit=100, last_messages_per_box=5):
        self.box_calls += 1
        return {"messageBoxes": [_box(_CUSTOMER_MID, (_CUSTOMER_MID, 1000 + self.box_calls))]}


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_ops_stream_refreshes_inbox_and_relaxes_polling(web_state, monkeypatch):
    _seed_owned_account(web_state, _owner(web_state))
    monkeypatch.setattr(webapp_module, "OPS_REFRESH_DEBOUNCE_SECONDS", 0.0)
    fake = _OpsFakeApi(events=["ping", "message", "message"])
    monkeypatch.setattr(web_state, "get_api", lambda account_id=None, require_auth=True: fake)
    try:
        web_state._ensure_ops_watcher("acct")
        assert _wait_for(lambda: fake.box_calls >= 1)
        assert web_state.ops_stream_healthy("acct") is True
        assert web_state.inbox_max_age("acct") == webapp_module.OPS_FALLBACK_REFRESH_SECONDS
        # A burst of operations is coalesced; pings alone never fetch.
        assert fake.box_calls <= 2
        assert web_state.inbox_version("acct") >= 1
    finally:
        fake.release.set()
        web_state.stop_ops_watcher("acct")


def test_ops_stream_falls_back_to_polling_after_three_failures(web_state, monkeypatch):
    _seed_owned_account(web_state, _owner(web_state))
    monkeypatch.setattr(webapp_module, "OPS_FAILURE_LIMIT", 3)
    fake = _OpsFakeApi(fail=True)
    monkeypatch.setattr(web_state, "get_api", lambda account_id=None, require_auth=True: fake)
    waits = []
    watcher = webapp_module.OpsWatcher.__new__(webapp_module.OpsWatcher)
    watcher.state = web_state
    watcher.account_id = "acct"
    watcher.connected = False
    watcher.last_event_at = 0.0
    watcher.failures = 0
    watcher.disabled_until = 0.0

    class _Stop:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            return fake.stream_calls >= 3 and watcher.disabled_until > 0

        def wait(self, seconds):
            waits.append(seconds)

    watcher.stop_event = _Stop()
    watcher._run()
    assert fake.stream_calls == 3
    assert watcher.disabled_until > time.monotonic()
    assert watcher.healthy() is False
    assert web_state.inbox_max_age("acct") == webapp_module.INBOX_SNAPSHOT_TTL_SECONDS


def test_ops_watchers_stop_when_account_is_removed(web_state, monkeypatch):
    owner = _seed_owned_account(web_state, _owner(web_state))
    fake = _OpsFakeApi(events=["message"])
    monkeypatch.setattr(web_state, "get_api", lambda account_id=None, require_auth=True: fake)
    monkeypatch.setattr(web_state, "close_api", lambda account_id: None)
    try:
        web_state._ensure_ops_watcher("acct")
        watcher = web_state.ops_watchers["acct"]
        web_state.delete_account("acct", owner)
        assert "acct" not in web_state.ops_watchers
        assert watcher.stop_event.is_set()
    finally:
        fake.release.set()


def test_narrow_screens_show_one_chat_pane_at_a_time():
    assert 'id="convoBackButton"' in INDEX_HTML
    assert '.tab-panel[data-tab-panel="line"].active.convo-open > .line-list { display: none; }' in INDEX_HTML
    assert '$("tabPanelLine").classList.toggle("convo-open", Boolean(mid));' in INDEX_HTML
    # The hidden legacy row must not steal the stretch row from the messages.
    assert ".line-convo { grid-template-rows: auto minmax(0, 1fr) auto; }" in INDEX_HTML


def test_chat_background_is_white_in_every_color_scheme():
    assert "--line-msg-bg: #ffffff;" in INDEX_HTML
    assert "#202b36" not in INDEX_HTML
