"""Tests for the UX-plan backend: KPI tracking, presence, staff sharing, events."""

from __future__ import annotations

import datetime as dt
import ipaddress
import json
import re
import socket
import threading
import time
from collections.abc import Iterator

import pytest
import requests
import urllib3

from okline import assistant as assistant_module
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


class _FakeAiClient:
    """Stands in for every provider client; make_ai_client builds it (see below)."""

    instances: list[_FakeAiClient] = []

    def __init__(self, provider_id, api_key, base_url="", **kwargs):
        self.provider_id = provider_id
        self.api_key = api_key
        self.base_url = base_url
        self.kwargs = kwargs
        _FakeAiClient.instances.append(self)

    def embed(self, model, texts, timeout=None):
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

    def list_models(self, timeout=None):
        return []


def _fake_clients(monkeypatch, client_class=_FakeAiClient):
    """Route the web app's make_ai_client to ``client_class``; returns its calls."""
    calls = []

    def make(provider_id, *, api_key, base_url="", transport=None, timeout=60.0):
        calls.append(
            {
                "provider": provider_id,
                "apiKey": api_key,
                "baseUrl": base_url,
                "transport": transport,
                "timeout": timeout,
            }
        )
        return client_class(provider_id, api_key, base_url, transport=transport, timeout=timeout)

    monkeypatch.setattr(webapp_module, "make_ai_client", make)
    return calls


def test_owner_configures_gemini_without_god(web_state, monkeypatch):
    calls = _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    assert web_state.assistant_settings(owner)["configured"] is False
    saved = web_state.save_assistant_settings(
        {"provider": "gemini", "apiKey": "AIza-test-key"}, owner
    )
    assert saved["configured"] is True and saved["provider"] == "gemini"
    assert saved["test"]["ok"] is True
    assert saved["source"] == "tenant"
    assert saved["chatModel"] == "gemini-3.8-flash"  # a blank model is the provider's default
    assert calls[0]["provider"] == "gemini" and calls[0]["apiKey"] == "AIza-test-key"
    assert calls[0]["timeout"] == 60.0  # one chat call takes at most a minute
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


GEMINI_MODELS = [
    {"name": "gemini-2.5-flash", "chat": True, "embedding": False},
    {"name": "gemini-2.5-pro", "chat": True, "embedding": False},
    {"name": "gemini-2.5-flash-preview-tts", "chat": True, "embedding": False},
    {"name": "gemini-2.5-flash-image", "chat": True, "embedding": False},
    {"name": "gemini-3-pro-image-preview", "chat": True, "embedding": False},
    {"name": "gemini-embedding-001", "chat": False, "embedding": True},
    {"name": "imagen-4.0-generate-001", "chat": False, "embedding": False},
]
OPENAI_MODELS = [
    {"name": "gpt-6-luna", "displayName": "gpt-6-luna", "kind": "chat"},
    {"name": "gpt-6-mini", "displayName": "gpt-6-mini", "kind": "chat"},
    {"name": "text-embedding-3-small", "displayName": "text-embedding-3-small", "kind": "embedding"},
    {"name": "gpt-image-2", "displayName": "gpt-image-2", "kind": "other"},
]


def test_refresh_models_lists_what_the_key_offers(web_state, monkeypatch):
    seen = []

    class _ListingClient(_FakeAiClient):
        def list_models(self, timeout=None):
            seen.append((self.provider_id, self.api_key))
            return GEMINI_MODELS if self.provider_id == "gemini" else OPENAI_MODELS

    _fake_clients(monkeypatch, _ListingClient)
    owner = _seed_owned_account(web_state, _owner(web_state))
    with pytest.raises(WebError) as no_key:
        web_state.ai_models({"provider": "gemini"}, owner)
    assert no_key.value.code == "ai_key_required" and not seen

    # The key typed in the form, before anything is saved.
    listed = web_state.ai_models({"provider": "gemini", "apiKey": " AIza-typed "}, owner)
    assert seen[-1] == ("gemini", "AIza-typed")
    assert listed["chatModels"] == ["gemini-2.5-flash", "gemini-2.5-pro"]  # no TTS, no image
    assert listed["embeddingModels"] == ["gemini-embedding-001"]
    assert listed["imageModels"] == ["gemini-2.5-flash-image", "gemini-3-pro-image-preview"]
    # Nothing typed: a saved key, and each section starts with its own.
    web_state.save_ai_settings({"provider": "google", "apiKey": "AIza-image"}, owner)
    web_state.ai_models({"provider": "gemini"}, owner)
    assert seen[-1] == ("gemini", "AIza-image")  # no auto-reply key yet
    web_state.save_assistant_settings({"provider": "gemini", "apiKey": "AIza-brain", "test": False}, owner)
    web_state.ai_models({"provider": "gemini"}, owner)
    assert seen[-1] == ("gemini", "AIza-brain")
    web_state.ai_models({"provider": "gemini", "purpose": "image"}, owner)
    assert seen[-1] == ("gemini", "AIza-image")  # the key the image section shows
    web_state.ai_models({"provider": "gemini", "purpose": "image", "apiKey": "AIza-typed"}, owner)
    assert seen[-1] == ("gemini", "AIza-typed")
    # Invisible characters copied along with a key are dropped.
    for invisible in ("​", "‌", "‍", "⁠", "﻿"):
        web_state.ai_models({"provider": "gemini", "apiKey": f"{invisible}AIza-typed{invisible}"}, owner)
        assert seen[-1] == ("gemini", "AIza-typed"), repr(invisible)
    # With no image key, the image section falls back to the auto-reply key.
    web_state.save_ai_settings({"provider": "google", "clearApiKey": True}, owner)
    web_state.ai_models({"provider": "gemini", "purpose": "image"}, owner)
    assert seen[-1] == ("gemini", "AIza-brain")
    web_state.save_ai_settings({"provider": "google", "apiKey": "AIza-image"}, owner)

    # Every provider lists with its own key: the lists use each model's kind.
    with pytest.raises(WebError) as openai_no_key:
        web_state.ai_models({"provider": "openai"}, owner)
    assert openai_no_key.value.code == "ai_key_required"
    assert seen[-1][0] == "gemini"  # the Gemini key never goes to OpenAI
    openai = web_state.ai_models({"provider": "openai", "apiKey": "sk-typed-openai"}, owner)
    assert seen[-1] == ("openai", "sk-typed-openai")
    assert openai == {
        "ok": True,
        "provider": "openai",
        "chatModels": ["gpt-6-luna", "gpt-6-mini"],
        "embeddingModels": ["text-embedding-3-small"],
        "imageModels": [],
    }
    # With the auto reply on OpenAI, Gemini still lists with its own saved key.
    web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-saved-openai", "test": False}, owner
    )
    web_state.ai_models({"provider": "openai"}, owner)
    assert seen[-1] == ("openai", "sk-saved-openai")
    web_state.ai_models({"provider": "gemini"}, owner)
    assert seen[-1] == ("gemini", "AIza-brain")
    # Old cached pages may still ask for the removed choices.
    for body in (
        {"provider": "ollama", "baseUrl": "http://ollama.test:11434"},
        {"provider": "global"},
        {"provider": ""},
        {},
    ):
        with pytest.raises(WebError) as refused:
            web_state.ai_models(body, owner)
        assert (refused.value.code, refused.value.status) == ("ai_settings_invalid", 400), body


def test_refresh_models_waits_thirty_seconds_at_most_for_every_provider(web_state, monkeypatch):
    # A stalled provider must end in the readable 422 well before Cloudflare's 100 s.
    assert webapp_module.MODEL_LIST_TIMEOUT_SECONDS == 30
    asked = []

    class _TimedClient(_FakeAiClient):
        def list_models(self, timeout=None):
            asked.append((self.provider_id, timeout))
            return OPENAI_MODELS

    _fake_clients(monkeypatch, _TimedClient)
    _fake_dns(monkeypatch, {"llm.example.com": ["93.184.216.34"]})
    owner = _seed_owned_account(web_state, _owner(web_state))
    listed = [
        provider.id
        for provider in webapp_module.provider_registry()
        if provider.models_source != "static" and webapp_module.provider_available(provider.id)[0]
    ]
    assert {"gemini", "openai", "custom"} <= set(listed)
    for provider_id in listed:
        body = {"provider": provider_id, "apiKey": "sk-typed-key-0001"}
        if provider_id == "custom":
            body["baseUrl"] = "https://llm.example.com/v1"
        web_state.ai_models(body, owner)
    web_state.ai_models({"provider": "gemini", "purpose": "image", "apiKey": "AIza-typed"}, owner)
    assert [provider_id for provider_id, _ in asked] == [*listed, "gemini"]
    assert {timeout for _, timeout in asked} == {30.0}


def test_refresh_models_gives_the_real_clients_the_short_deadline(web_state, monkeypatch):
    # Through the real clients: the deadline reaches the request itself.
    seen = []

    def recording_transport(method, url, *, headers, json_body, timeout, max_bytes):
        seen.append((url.split("/")[2], timeout))
        return 200, {"Content-Type": "application/json"}, json.dumps({
            "data": [{"id": "gpt-6-mini", "object": "model"}],
            "models": [{"name": "models/gemini-9-flash", "displayName": "Gemini 9 Flash",
                        "supportedGenerationMethods": ["generateContent"]}],
        }).encode()

    real_make = webapp_module.make_ai_client

    def make(provider_id, **kwargs):
        return real_make(provider_id, **{**kwargs, "transport": recording_transport})

    monkeypatch.setattr(webapp_module, "make_ai_client", make)
    _fake_dns(monkeypatch, {"llm.example.com": ["93.184.216.34"]})
    owner = _seed_owned_account(web_state, _owner(web_state))
    for body in (
        {"provider": "gemini", "apiKey": "AIza-typed-key"},
        {"provider": "openai", "apiKey": "sk-typed-key-0001"},
        {"provider": "custom", "apiKey": "sk-typed-key-0001", "baseUrl": "https://llm.example.com/v1"},
    ):
        web_state.ai_models(body, owner)
    assert [host for host, _ in seen] == [
        "generativelanguage.googleapis.com", "api.openai.com", "llm.example.com"
    ]
    assert all(0 < timeout <= 30 for _, timeout in seen), seen


def test_built_in_model_lists_need_no_key_and_no_request(web_state, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("no request expected")

    monkeypatch.setattr(requests, "request", no_network)
    # The AI transports send through a requests session.
    monkeypatch.setattr(requests.sessions.Session, "request", no_network)
    owner = _seed_owned_account(web_state, _owner(web_state))
    zai = web_state.ai_models({"provider": "zai"}, owner)
    assert "glm-4.7" in zai["chatModels"] and zai["embeddingModels"] == []
    qwen = web_state.ai_models({"provider": "qwen"}, owner)
    assert "qwen3.8-flash" in qwen["chatModels"]
    assert qwen["embeddingModels"] == ["text-embedding-v4"]


def test_refresh_models_says_why_a_list_could_not_be_loaded(web_state, monkeypatch):
    calls = []

    class _RefusingClient(_FakeAiClient):
        def list_models(self, timeout=None):
            calls.append(self.api_key)
            # A client error can repeat its input: requests echoes a bad header value.
            raise webapp_module.AssistantError(
                f"Gemini error 400: API key not valid: {self.api_key}", 400
            )

    _fake_clients(monkeypatch, _RefusingClient)
    owner = _seed_owned_account(web_state, _owner(web_state))
    with pytest.raises(WebError) as failed:
        web_state.ai_models({"provider": "gemini", "apiKey": "AIza-wrong-key"}, owner)
    assert failed.value.code == "ai_models_unavailable"
    assert failed.value.status == 422  # a 502 would lose its message behind Cloudflare
    assert "API key not valid" in failed.value.message
    assert "AIza-wrong-key" not in failed.value.message and "[API key]" in failed.value.message
    # A key with characters no key has is refused before any request.
    for bad in ("AIza-test’", "AIzaกtest", "AIza test"):
        with pytest.raises(WebError) as refused:
            web_state.ai_models({"provider": "gemini", "apiKey": bad}, owner)
        assert (refused.value.code, refused.value.status) == ("ai_key_invalid", 400)
    assert calls == ["AIza-wrong-key"]

    # Any reason the provider gives stays a 422, even with a URL the parser cannot
    # read; a saved key is hidden as well as a typed one.
    web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-saved-secret-key", "test": False}, owner
    )
    reasons = iter([
        webapp_module.AssistantError("OpenAI error 502: upstream [http://10.0.0.5:8080] timed out", 502),
        webapp_module.AssistantError("see http://[fd00::1 for details"),
        webapp_module.AssistantError("OpenAI error 401: Incorrect API key sk-saved-secret-key", 401),
        ValueError("invalid literal for int() with base 10: 'big'"),
        TypeError("'NoneType' object is not subscriptable"),
    ])

    class _GatewayClient(_FakeAiClient):
        def list_models(self, timeout=None):
            raise next(reasons)

    _fake_clients(monkeypatch, _GatewayClient)
    for expected in ("upstream", "fd00", "[API key]", "big", "NoneType"):
        with pytest.raises(WebError) as failed_gateway:
            web_state.ai_models({"provider": "openai"}, owner)
        assert (failed_gateway.value.code, failed_gateway.value.status) == ("ai_models_unavailable", 422)
        assert expected in failed_gateway.value.message
        assert "sk-saved-secret-key" not in failed_gateway.value.message

    # A stray short key is not a secret: the reason is shown as Google wrote it.
    class _ShortKeyClient(_FakeAiClient):
        def list_models(self, timeout=None):
            raise webapp_module.AssistantError(
                "Gemini error 400: API key not valid. Please pass a valid API key.", 400
            )

    _fake_clients(monkeypatch, _ShortKeyClient)
    with pytest.raises(WebError) as short:
        web_state.ai_models({"provider": "gemini", "apiKey": "a"}, owner)
    assert "API key not valid. Please pass a valid API key." in short.value.message


def test_saved_api_keys_drop_invisible_characters_and_refuse_odd_ones(web_state, monkeypatch):
    _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "gemini", "apiKey": "​AIza-clean﻿", "test": False}, owner
    )
    assert web_state._assistant_settings_for(web_state._global_ai_all(), owner)["apiKey"] == "AIza-clean"
    with pytest.raises(WebError) as refused:
        web_state.save_assistant_settings({"provider": "gemini", "apiKey": "AIza’x", "test": False}, owner)
    assert refused.value.code == "ai_key_invalid"
    web_state.save_ai_settings({"provider": "google", "apiKey": "AIza-image​"}, owner)
    assert web_state._ai_provider_view(web_state._ai_all(owner), "google")["apiKey"] == "AIza-image"
    with pytest.raises(WebError) as spaced:
        web_state.save_ai_settings({"provider": "google", "apiKey": "AIza image"}, owner)
    assert spaced.value.code == "ai_key_invalid"
    # A blank key still keeps the saved one.
    web_state.save_ai_settings({"provider": "google", "apiKey": "  "}, owner)
    assert web_state._ai_provider_view(web_state._ai_all(owner), "google")["apiKey"] == "AIza-image"


def test_refresh_models_needs_the_manage_accounts_permission(live, monkeypatch):
    base, state = live

    class _ListingClient(_FakeAiClient):
        def list_models(self, timeout=None):
            return GEMINI_MODELS

    _fake_clients(monkeypatch, _ListingClient)
    state.web_auth.setup("owner@example.com", "owner-pass")
    state.web_auth.create_user("viewer@example.com", "viewer-pass", "viewer", [])
    body = {"provider": "gemini", "apiKey": "AIza-typed"}
    assert requests.post(f"{base}/api/ai/models", json=body, timeout=5).status_code == 401
    viewer = requests.Session()
    viewer.post(f"{base}/api/auth/login",
                json={"email": "viewer@example.com", "password": "viewer-pass"},
                timeout=5).raise_for_status()
    assert viewer.post(f"{base}/api/ai/models", json=body, timeout=5).status_code == 403
    owner = requests.Session()
    owner.post(f"{base}/api/auth/login",
               json={"email": "owner@example.com", "password": "owner-pass"},
               timeout=5).raise_for_status()
    listed = owner.post(f"{base}/api/ai/models", json=body, timeout=5)
    assert listed.status_code == 200 and "gemini-2.5-flash" in listed.json()["chatModels"]


def test_the_ai_settings_routes_answer_as_the_page_expects(live, monkeypatch):
    base, state = live
    _fake_clients(monkeypatch)
    state.web_auth.setup("owner@example.com", "owner-pass")
    owner_user = state.web_auth._find_user(state.web_auth.data["users"][0]["id"])
    _seed_owned_account(state, owner_user)
    state.web_auth.create_staff(owner_user, "staff@example.com", "staff-pass", "Staff", ["acct"])
    owner = requests.Session()
    owner.post(f"{base}/api/auth/login",
               json={"email": "owner@example.com", "password": "owner-pass"},
               timeout=5).raise_for_status()
    got = owner.get(f"{base}/api/assistant/settings", timeout=5)
    assert got.status_code == 200
    assert (got.json()["provider"], got.json()["canEdit"], len(got.json()["providers"])) == ("", True, 11)
    # Old cached pages post the removed choices: a translated 400, not a 500.
    for old in ({"provider": "ollama", "baseUrl": "http://127.0.0.1:11434"}, {"provider": "global"}):
        refused = owner.post(f"{base}/api/assistant/settings", json=old, timeout=5)
        assert (refused.status_code, refused.json()["code"]) == (400, "ai_settings_invalid")
    saved = owner.post(
        f"{base}/api/assistant/settings",
        json={"provider": "openai", "apiKey": "sk-route-test-key", "chatModel": "gpt-6-luna",
              "embeddingModel": "", "strictKnowledge": True, "test": True},
        timeout=5,
    )
    assert saved.status_code == 200, saved.text
    payload = saved.json()
    assert payload["test"] == {"ok": True, "error": "", "model": "gpt-6-luna"}
    assert (payload["provider"], payload["configured"], payload["strictKnowledge"]) == ("openai", True, True)
    assert "sk-route-test-key" not in saved.text
    # Staff see what the workspace uses but cannot change it.
    staff = requests.Session()
    staff.post(f"{base}/api/auth/login",
               json={"email": "staff@example.com", "password": "staff-pass"},
               timeout=5).raise_for_status()
    seen = staff.get(f"{base}/api/assistant/settings", timeout=5).json()
    assert (seen["provider"], seen["configured"], seen["canEdit"]) == ("openai", True, False)
    # No body: the 403 comes before the body is read, and on Windows an unread body
    # can turn the answer into a reset connection.
    blocked = staff.post(f"{base}/api/assistant/settings", timeout=5)
    assert blocked.status_code == 403


def test_deleting_an_account_says_first_how_many_customers_go_with_it(live):
    base, state = live
    state.web_auth.setup("owner@example.com", "owner-pass")
    owner = state.web_auth._find_user(state.web_auth.data["users"][0]["id"])
    _seed_owned_account(state, owner)
    state.crm.ingest(
        owner_id=owner["id"], account_id="acct", channel="line", chat_id=_CUSTOMER_MID,
        contact={"externalId": _CUSTOMER_MID, "name": "Somchai"},
        messages=[{"externalId": "m1", "direction": "in", "senderKind": "customer",
                   "text": "hi", "contentType": 0, "at": int(time.time() * 1000)}],
    )
    session = requests.Session()
    session.post(f"{base}/api/auth/login",
                 json={"email": "owner@example.com", "password": "owner-pass"},
                 timeout=5).raise_for_status()
    impact = session.get(f"{base}/api/accounts/delete-impact?accountId=acct", timeout=5)
    assert impact.status_code == 200 and impact.json()["customers"] == 1

    # Only someone who manages that account may ask.
    for email, role in (("viewer@example.com", "viewer"), ("member@example.com", "member")):
        state.web_auth.create_user(email, "other-pass", role, [])
        other = requests.Session()
        other.post(f"{base}/api/auth/login", json={"email": email, "password": "other-pass"},
                   timeout=5).raise_for_status()
        assert other.get(f"{base}/api/accounts/delete-impact?accountId=acct",
                         timeout=5).status_code == 403

    deleted = session.post(f"{base}/api/accounts/delete", json={"accountId": "acct"}, timeout=5)
    assert deleted.status_code == 200 and deleted.json()["customersDeleted"] == 1
    assert state.crm.db.scalar("SELECT COUNT(*) FROM crm_customers") == 0


def test_shop_topics_cite_sources_and_collect_faq(web_state, monkeypatch):
    _fake_clients(monkeypatch)
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


def test_gemini_client_parses_structured_answer():
    from okline import assistant

    calls = []

    def fake_transport(method, url, *, headers, json_body, timeout, max_bytes):
        calls.append(
            {"method": method, "url": url, "headers": dict(headers), "body": json_body,
             "timeout": timeout, "maxBytes": max_bytes}
        )
        if url.endswith(":batchEmbedContents"):
            payload = {"embeddings": [{"values": [0.1, 0.2]}]}
        else:
            payload = {
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
        return 200, {"Content-Type": "application/json"}, json.dumps(payload).encode()

    client = assistant.GeminiClient("secret", transport=fake_transport)
    result = client.chat(
        model="gemini-x",
        question="q",
        context="",
        system_prompt="s",
        strict_knowledge=True,
        temperature=0.2,
    )
    assert result == {
        "answer": "ok", "canAnswer": True, "confidence": 0.7, "model": "gemini-x",
        "promptEvalCount": 5, "evalCount": 2,
    }
    assert client.embed("", ["hello"]) == [[0.1, 0.2]]
    chat, embed = calls
    assert chat["method"] == "POST" and chat["url"].endswith("/v1beta/models/gemini-x:generateContent")
    assert chat["headers"]["x-goog-api-key"] == "secret"
    assert "key=" not in chat["url"]  # the key never goes into the URL
    assert chat["body"]["generationConfig"]["responseMimeType"] == "application/json"
    assert chat["body"]["generationConfig"]["temperature"] == 0.2
    assert chat["maxBytes"] == assistant.MAX_AI_RESPONSE_BYTES
    # A blank model is Gemini's default search model; embeddings get the larger cap.
    assert embed["url"].endswith("/models/gemini-embedding-001:batchEmbedContents")
    assert embed["maxBytes"] == assistant.MAX_AI_EMBEDDING_BYTES


# --- AI for auto reply: the settings shape, every provider, the Custom API ----

_REAL_GETADDRINFO = socket.getaddrinfo


def _fake_dns(monkeypatch, table):
    """DNS answers for the hosts in ``table`` (host -> addresses); others resolve as usual."""

    def getaddrinfo(host, port, *args, **kwargs):
        if host in table:
            return [
                (
                    socket.AF_INET6 if ":" in address else socket.AF_INET,
                    socket.SOCK_STREAM,
                    6,
                    "",
                    (address, port),
                )
                for address in table[host]
            ]
        return _REAL_GETADDRINFO(host, port, *args, **kwargs)

    monkeypatch.setattr(webapp_module.socket, "getaddrinfo", getaddrinfo)


def _seed_tenant_settings(web_state, owner, settings, **tenant):
    data = web_state._global_ai_all()
    data["tenants"][owner["id"]] = {
        "settings": settings,
        "apiSources": [],
        "manualAnswers": [],
        "chunks": [],
        **tenant,
    }
    web_state.store.set("global_ai", data)


def test_assistant_settings_list_every_provider_in_the_registry_order(web_state, monkeypatch):
    monkeypatch.setattr(webapp_module, "provider_available", lambda provider_id: (True, ""))
    owner = _seed_owned_account(web_state, _owner(web_state))
    data = web_state.assistant_settings(owner)
    assert {key: data[key] for key in (
        "provider", "source", "configured", "enabled", "canEdit", "notice", "imageKeyAvailable",
        "strictKnowledge", "chatModel", "embeddingModel", "baseUrl", "hasApiKey", "apiKeyPreview",
    )} == {
        "provider": "", "source": "none", "configured": False, "enabled": False, "canEdit": True,
        "notice": "", "imageKeyAvailable": False, "strictKnowledge": False, "chatModel": "",
        "embeddingModel": "", "baseUrl": "", "hasApiKey": False, "apiKeyPreview": "",
    }
    for gone in ("globalAvailable", "defaults"):
        assert gone not in data  # nothing from a shared AI or the God
    assert [item["id"] for item in data["providers"]] == [
        "gemini", "anthropic", "openai", "zai", "deepseek", "qwen", "xai", "mistral",
        "moonshot", "openrouter", "custom",
    ]
    assert data["providers"][0] == {
        "id": "gemini", "label": "Gemini (Google)", "group": "main", "available": True,
        "unavailableReason": "", "keyRequired": True, "hasApiKey": False, "apiKeyPreview": "",
        "keyHint": "aistudio.google.com -> Get API key", "keyPlaceholder": "AIza...",
        "chatModel": "", "embeddingModel": "", "defaultChatModel": "gemini-3.8-flash",
        "defaultEmbeddingModel": "gemini-embedding-001", "embeddings": True,
        "needsBaseUrl": False, "baseUrl": "",
    }
    groups = {item["id"]: item["group"] for item in data["providers"]}
    assert [name for name, group in groups.items() if group == "main"] == ["gemini", "anthropic", "openai"]
    custom = data["providers"][-1]
    assert (custom["group"], custom["keyRequired"], custom["needsBaseUrl"], custom["keyHint"]) == (
        "custom", False, True, custom["keyHint"]
    )
    # The image tool's Google key is offered to Gemini, and only says so.
    web_state.save_ai_settings({"provider": "google", "apiKey": "AIza-image-key"}, owner)
    shared = web_state.assistant_settings(owner)
    assert shared["imageKeyAvailable"] is True and shared["providers"][0]["hasApiKey"] is False


def test_a_provider_the_server_cannot_run_is_listed_but_refused(web_state, monkeypatch):
    def without_claude(provider_id):
        return (False, "ai_provider_unavailable") if provider_id == "anthropic" else (True, "")

    _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    monkeypatch.setattr(webapp_module, "provider_available", lambda provider_id: (True, ""))
    web_state.save_assistant_settings(
        {"provider": "anthropic", "apiKey": "sk-ant-test-key", "test": False}, owner
    )
    assert web_state.assistant_settings(owner)["configured"] is True
    # The same workspace on a server without the anthropic package (Python 3.9).
    monkeypatch.setattr(webapp_module, "provider_available", without_claude)
    data = web_state.assistant_settings(owner)
    assert (data["provider"], data["configured"]) == ("anthropic", False)
    claude = data["providers"][1]
    assert (claude["available"], claude["unavailableReason"]) == (False, "ai_provider_unavailable")
    for call in (web_state.save_assistant_settings, web_state.ai_models):
        with pytest.raises(WebError) as refused:
            call({"provider": "anthropic", "apiKey": "sk-ant-test-key"}, owner)
        assert (refused.value.code, refused.value.status) == ("ai_provider_unavailable", 400)


def test_each_provider_keeps_its_own_key(web_state, monkeypatch):
    # Claude also on Python 3.9, where the anthropic package is not installed.
    monkeypatch.setattr(webapp_module, "provider_available", lambda provider_id: (True, ""))
    calls = _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "gemini", "apiKey": "AIza-gemini-key", "chatModel": "gemini-pro-x", "test": False},
        owner,
    )
    # Switching provider never takes another provider's key along.
    with pytest.raises(WebError) as no_key:
        web_state.save_assistant_settings({"provider": "openai", "test": False}, owner)
    assert (no_key.value.code, no_key.value.status) == ("ai_key_required", 400)
    saved = web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-openai-key", "chatModel": "", "test": True}, owner
    )
    assert (calls[-1]["provider"], calls[-1]["apiKey"]) == ("openai", "sk-openai-key")
    assert (saved["provider"], saved["chatModel"], saved["configured"]) == ("openai", "gpt-6-luna", True)
    by_id = {item["id"]: item for item in saved["providers"]}
    assert by_id["openai"]["apiKeyPreview"] == "••••"  # a short key shows no characters
    assert by_id["openai"]["embeddingModel"] == "text-embedding-3-small"  # a new provider's default
    assert (by_id["gemini"]["hasApiKey"], by_id["gemini"]["chatModel"]) == (True, "gemini-pro-x")
    # Back to Gemini without typing a key: its own saved key and model.
    back = web_state.save_assistant_settings({"provider": "gemini", "test": True}, owner)
    assert (calls[-1]["provider"], calls[-1]["apiKey"]) == ("gemini", "AIza-gemini-key")
    assert back["chatModel"] == "gemini-pro-x"
    # clearApiKey clears the chosen provider's key and no other.
    cleared = web_state.save_assistant_settings(
        {"provider": "openai", "clearApiKey": True, "test": False}, owner
    )
    by_id = {item["id"]: item for item in cleared["providers"]}
    assert (by_id["openai"]["hasApiKey"], by_id["gemini"]["hasApiKey"]) == (False, True)
    assert (cleared["provider"], cleared["configured"]) == ("openai", False)
    stored = web_state._global_ai_all()["tenants"][owner["id"]]["settings"]
    assert sorted(stored["providers"]) == ["gemini", "openai"]
    assert not {"apiKey", "chatModel", "embeddingModel", "baseUrl"} & set(stored)  # no flat fields
    assert stored["providers"]["gemini"]["apiKey"].startswith("fernet:")
    assert "AIza-gemini-key" not in json.dumps(web_state.store.get("global_ai", {}))
    # A provider without embeddings never keeps a search model.
    claude = web_state.save_assistant_settings(
        {"provider": "anthropic", "apiKey": "sk-ant-key-0001", "embeddingModel": "x", "test": False},
        owner,
    )
    assert claude["embeddingModel"] == "" and claude["chatModel"] == "claude-opus-5-5"


def test_one_ask_holds_a_worker_for_about_75_seconds_at_most(web_state, monkeypatch):
    """An auto reply waits 15 s at most for its question's search vector, then 60 s
    for the answer, so a provider that stops answering cannot hold a shared worker
    for minutes."""
    monkeypatch.setattr(
        webapp_module, "_public_http_target", lambda url, **kw: (url, ("93.184.216.34",))
    )
    calls: list[tuple[str, float]] = []

    def recording_transport(base_url):
        def send(method, url, *, headers, json_body=None, timeout=60.0, max_bytes=0):
            calls.append((url.rsplit("/", 1)[-1], float(timeout)))
            if url.endswith("/embeddings"):
                data = [
                    {"index": index, "embedding": [0.1] * 8}
                    for index in range(len(json_body["input"]))
                ]
                return 200, {}, json.dumps({"data": data}).encode()
            answer = {"answer": "ok", "canAnswer": True, "confidence": 0.9}
            body = {"choices": [{"message": {"content": json.dumps(answer)},
                                 "finish_reason": "stop"}]}
            return 200, {}, json.dumps(body).encode()

        return send

    monkeypatch.setattr(webapp_module, "_custom_ai_transport", recording_transport)
    owner = _owner(web_state)
    web_state.save_assistant_settings(
        {"provider": "custom", "apiKey": "sk-budget-key-123456",
         "baseUrl": "https://llm.example.com/v1", "chatModel": "m",
         "embeddingModel": "e", "test": False},
        owner,
    )
    web_state.save_assistant_knowledge({"sections": [
        {"id": "hours", "title": "Hours", "body": "Open 9 to 5, every day."},
    ]}, owner)
    assert web_state.assistant_knowledge(owner)["indexMode"] == "embedding"
    calls.clear()
    web_state.ask_global_ai(
        "When are you open?", owner, source="telegram_auto_reply", account_id="a", chat_mid="c"
    )
    assert [name for name, _timeout in calls] == ["embeddings", "completions"]
    vector_wait, answer_wait = (timeout for _name, timeout in calls)
    # Each limit is what is left of its time when the request goes out: all of it on a
    # clock that ticks every 16 ms (Windows), a moment less on a finer one (Linux).
    assert 14.0 < vector_wait <= 15.0
    assert 59.0 < answer_wait <= 60.0
    assert sum(timeout for _name, timeout in calls) <= 75


def test_the_custom_api_key_stays_with_the_address_it_was_saved_for(web_state, monkeypatch):
    calls = _fake_clients(monkeypatch)
    _fake_dns(monkeypatch, {
        "llm.example.com": ["93.184.216.34"],
        "other.example.com": ["93.184.216.35"],
        "intranet.example.com": ["10.1.2.3"],
    })
    owner = _seed_owned_account(web_state, _owner(web_state))
    saved = web_state.save_assistant_settings(
        {"provider": "custom", "apiKey": "sk-custom-secret", "chatModel": "my-model",
         "baseUrl": " https://llm.example.com/v1/chat/completions ", "embeddingModel": "",
         "test": True},
        owner,
    )
    assert saved["test"] == {"ok": True, "error": "", "model": "my-model"}
    assert (saved["provider"], saved["configured"], saved["baseUrl"]) == (
        "custom", True, "https://llm.example.com/v1"
    )
    assert calls[-1]["baseUrl"] == "https://llm.example.com/v1"
    assert callable(calls[-1]["transport"])  # the public-address transport
    custom = saved["providers"][-1]
    assert (custom["baseUrl"], custom["hasApiKey"], custom["chatModel"], custom["embeddingModel"]) == (
        "https://llm.example.com/v1", True, "my-model", ""
    )
    # Same origin, another path: the key stays.
    kept = web_state.save_assistant_settings(
        {"provider": "custom", "baseUrl": "https://llm.example.com/v2", "chatModel": "my-model"},
        owner,
    )
    assert calls[-1]["apiKey"] == "sk-custom-secret" and kept["providers"][-1]["hasApiKey"] is True
    # Another origin and no new key: the key is dropped, never sent there.
    moved = web_state.save_assistant_settings(
        {"provider": "custom", "baseUrl": "https://other.example.com/v1", "chatModel": "my-model"},
        owner,
    )
    assert (calls[-1]["apiKey"], calls[-1]["baseUrl"]) == ("", "https://other.example.com/v1")
    assert moved["providers"][-1]["hasApiKey"] is False
    assert moved["configured"] is True  # the Custom API key is optional
    for body, code in (
        ({"baseUrl": "http://llm.example.com/v1"}, "ai_base_url_invalid"),
        ({"baseUrl": "https://user:pass@llm.example.com/v1"}, "ai_base_url_invalid"),
        ({"baseUrl": "https://llm.example.com/v1?token=x"}, "ai_base_url_invalid"),
        ({"baseUrl": "https://llm.example.com/v1#part"}, "ai_base_url_invalid"),
        ({"baseUrl": "https://llm.example.com/" + "v" * 2050}, "ai_base_url_invalid"),
        ({"baseUrl": "https://llm.example.com\\@intranet.example.com/v1"}, "ai_base_url_invalid"),
        ({"baseUrl": "https://ร้าน.example.com/v1"}, "ai_base_url_invalid"),
        ({"baseUrl": ""}, "ai_base_url_required"),
        ({"baseUrl": "https://intranet.example.com/v1"}, "ai_base_url_blocked"),
        ({"baseUrl": "https://10.0.0.8/v1"}, "ai_base_url_blocked"),
        ({"baseUrl": "https://[::1]/v1"}, "ai_base_url_blocked"),
        ({"baseUrl": "https://llm.example.com/v1", "chatModel": ""}, "ai_model_required"),
    ):
        with pytest.raises(WebError) as refused:
            web_state.save_assistant_settings(
                {"provider": "custom", "chatModel": "my-model", **body, "test": False}, owner
            )
        assert (refused.value.code, refused.value.status) == (code, 400), body
    assert web_state.assistant_settings(owner)["baseUrl"] == "https://other.example.com/v1"


def test_custom_api_models_use_the_saved_key_only_at_its_address(web_state, monkeypatch):
    seen = []

    class _ListingClient(_FakeAiClient):
        def list_models(self, timeout=None):
            seen.append((self.api_key, self.base_url))
            return [
                {"name": "my-model", "displayName": "my-model", "kind": "chat"},
                {"name": "my-embed", "displayName": "my-embed", "kind": "embedding"},
            ]

    calls = _fake_clients(monkeypatch, _ListingClient)
    _fake_dns(monkeypatch, {
        "llm.example.com": ["93.184.216.34"],
        "other.example.com": ["93.184.216.35"],
        "metadata.example.com": ["169.254.169.254"],
    })
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "custom", "apiKey": "sk-custom-secret", "chatModel": "my-model",
         "baseUrl": "https://llm.example.com/v1", "test": False},
        owner,
    )
    listed = web_state.ai_models(
        {"provider": "custom", "apiKey": "", "baseUrl": "https://llm.example.com/v1/models"}, owner
    )
    assert seen[-1] == ("sk-custom-secret", "https://llm.example.com/v1")
    assert (listed["chatModels"], listed["embeddingModels"]) == (["my-model"], ["my-embed"])
    assert callable(calls[-1]["transport"])
    web_state.ai_models({"provider": "custom", "baseUrl": "https://other.example.com/v1"}, owner)
    assert seen[-1] == ("", "https://other.example.com/v1")  # the saved key stays home
    web_state.ai_models(
        {"provider": "custom", "apiKey": "sk-typed", "baseUrl": "https://other.example.com/v1"}, owner
    )
    assert seen[-1] == ("sk-typed", "https://other.example.com/v1")
    web_state.ai_models({"provider": "custom"}, owner)  # no address typed: the saved one
    assert seen[-1] == ("sk-custom-secret", "https://llm.example.com/v1")
    # The address is checked before any request.
    count = len(seen)
    for url, code in (
        ("https://metadata.example.com/v1", "ai_base_url_blocked"),
        ("http://llm.example.com/v1", "ai_base_url_invalid"),
    ):
        with pytest.raises(WebError) as refused:
            web_state.ai_models({"provider": "custom", "baseUrl": url}, owner)
        assert (refused.value.code, refused.value.status) == (code, 400)
    # Only the workspace owner, even for staff given more rights later.
    staff = web_state.web_auth.create_staff(owner, "s@example.com", "staff-pass", "S", ["acct"])
    promoted = dict(web_state.web_auth._find_user(staff["id"]), role="member")
    with pytest.raises(WebError) as staff_refused:
        web_state.ai_models({"provider": "custom", "baseUrl": "https://llm.example.com/v1"}, promoted)
    assert staff_refused.value.status == 403
    assert len(seen) == count


def test_a_custom_api_address_that_is_missing_or_not_found_says_so(web_state, monkeypatch):
    # The page shows the text of the code, so each reason has a code of its own.
    # Nothing is looked up for real: every host here is "not found".
    looked_up = []

    def getaddrinfo(host, port, *args, **kwargs):
        looked_up.append(host)
        raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")

    monkeypatch.setattr(webapp_module.socket, "getaddrinfo", getaddrinfo)
    calls = _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    for base_url, code, said in (
        ("https://api.my-llm-provder.example/v1", "ai_base_url_not_found", "could not be found"),
        # A Thai domain typed in its xn-- form passes the shape check and is looked up.
        ("https://xn--42c6bd5a.example/v1", "ai_base_url_not_found", "could not be found"),
        ("", "ai_base_url_required", "Enter the Custom API address"),
        ("   ", "ai_base_url_required", "Enter the Custom API address"),
        ("https://ร้าน.example.com/v1", "ai_base_url_invalid", "xn--"),
        ("http://llm.example.com/v1", "ai_base_url_invalid", "https://"),
    ):
        for route in ("save", "models"):
            with pytest.raises(WebError) as refused:
                if route == "save":
                    web_state.save_assistant_settings(
                        {"provider": "custom", "chatModel": "my-model", "baseUrl": base_url,
                         "test": False},
                        owner,
                    )
                else:
                    web_state.ai_models({"provider": "custom", "baseUrl": base_url}, owner)
            assert (refused.value.code, refused.value.status) == (code, 400), (base_url, route)
            assert said in str(refused.value), (base_url, route)
    assert calls == []  # no client was made for any of them
    assert sorted(set(looked_up)) == ["api.my-llm-provder.example", "xn--42c6bd5a.example"]
    # Both new codes have a translated line on the page.
    for code in ("ai_base_url_not_found", "ai_base_url_required"):
        entry = rf'"errors\.{code}": {{th: "[^"]+", en: "[^"]+"}}'
        assert re.search(entry, webapp_module.INDEX_HTML), code


def test_a_dns_lookup_that_hangs_is_given_up_on_in_time(monkeypatch):
    release = threading.Event()

    def hanging_getaddrinfo(host, port, *args, **kwargs):
        release.wait(30)
        raise socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")

    monkeypatch.setattr(webapp_module.socket, "getaddrinfo", hanging_getaddrinfo)
    try:
        assert webapp_module.DNS_LOOKUP_TIMEOUT_SECONDS == 10.0
        started = time.monotonic()
        with pytest.raises(WebError) as refused:
            webapp_module._public_http_target("https://slow-dns.example.com/v1", dns_timeout=0.3)
        assert refused.value.status == 502
        assert time.monotonic() - started < 3
        # The Custom API counts the lookup against its own deadline, and says why.
        transport = webapp_module._custom_ai_transport("https://slow-dns.example.com/v1")
        started = time.monotonic()
        with pytest.raises(webapp_module.AssistantError) as failed:
            transport("POST", "https://slow-dns.example.com/v1/chat/completions",
                      headers={}, json_body={}, timeout=1, max_bytes=1000)
        assert failed.value.code == "ai_base_url_not_found"
        assert time.monotonic() - started < 4
    finally:
        release.set()  # the lookup threads end too


def test_a_dns_lookup_thread_that_cannot_start_is_a_failed_lookup(monkeypatch):
    def no_thread(self):
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(webapp_module.threading.Thread, "start", no_thread)
    with pytest.raises(WebError) as refused:
        webapp_module._public_http_target("https://llm.example.com/v1")
    assert refused.value.status == 502


def test_a_legacy_gemini_record_still_works_before_the_migration(web_state, monkeypatch):
    calls = _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    _seed_tenant_settings(web_state, owner, {
        "provider": "gemini", "enabled": True, "baseUrl": "http://127.0.0.1:11434",
        "apiKey": web_state._encrypt_secret("AIza-legacy-key"), "chatModel": "",
        "embeddingModel": "", "systemPrompt": "Be brief.", "strictKnowledge": True, "topK": 3,
        "temperature": 0, "timeout": 120, "updatedAt": "2026-10-01T10:00:00",
    })
    settings = web_state._assistant_settings_for(web_state._global_ai_all(), owner)
    assert {key: settings[key] for key in (
        "provider", "source", "configured", "apiKey", "baseUrl", "chatModel", "embeddingModel",
        "temperature", "topK", "strictKnowledge", "systemPrompt",
    )} == {
        "provider": "gemini", "source": "tenant", "configured": True, "apiKey": "AIza-legacy-key",
        "baseUrl": "",  # the Ollama address is never used
        "chatModel": "gemini-3.8-flash", "embeddingModel": "gemini-embedding-001",
        "temperature": 0.0, "topK": 3, "strictKnowledge": True, "systemPrompt": "Be brief.",
    }
    gemini = web_state.assistant_settings(owner)["providers"][0]
    assert (gemini["hasApiKey"], gemini["apiKeyPreview"], gemini["baseUrl"]) == (True, "••••", "")
    assert gemini["embeddingModel"] == "gemini-embedding-001"
    # Saving another provider moves the Gemini key into the new shape, with Gemini.
    web_state.save_assistant_settings({"provider": "openai", "apiKey": "sk-openai-key", "test": False}, owner)
    stored = web_state._global_ai_all()["tenants"][owner["id"]]["settings"]
    assert sorted(stored["providers"]) == ["gemini", "openai"]
    assert not {"apiKey", "baseUrl"} & set(stored)
    assert web_state._decrypt_secret(stored["providers"]["gemini"]["apiKey"]) == "AIza-legacy-key"
    assert "baseUrl" not in stored["providers"]["gemini"]
    assert (stored["temperature"], stored["topK"], stored["systemPrompt"]) == (0.0, 3, "Be brief.")
    back = web_state.save_assistant_settings({"provider": "gemini", "test": True}, owner)
    assert back["configured"] is True and calls[-1]["apiKey"] == "AIza-legacy-key"


@pytest.mark.parametrize(
    "record",
    [
        None,
        {"provider": "ollama", "enabled": True, "baseUrl": "http://127.0.0.1:11434",
         "chatModel": "qwen2.5:7b", "embeddingModel": "bge-m3", "temperature": 0.5},
        {"provider": "global", "enabled": True},
        {"provider": "", "aiNotice": "shared_ai_removed"},
        "broken",
    ],
)
def test_without_a_known_provider_the_workspace_is_not_configured(web_state, monkeypatch, record):
    calls = _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    if record is not None:
        _seed_tenant_settings(web_state, owner, record)
    # A Shared AI record left in the store is never used.
    data = web_state._global_ai_all()
    data["settings"] = {"enabled": True, "chatModel": "llama3", "baseUrl": "http://127.0.0.1:11434"}
    web_state.store.set("global_ai", data)
    settings = web_state._assistant_settings_for(web_state._global_ai_all(), owner)
    assert {key: settings[key] for key in (
        "provider", "source", "configured", "enabled", "apiKey", "baseUrl", "chatModel",
        "embeddingModel",
    )} == {
        "provider": "", "source": "none", "configured": False, "enabled": False, "apiKey": "",
        "baseUrl": "", "chatModel": "", "embeddingModel": "",
    }
    assert settings["systemPrompt"] and settings["topK"] == 5 and settings["timeout"] >= 5
    assert 0.0 <= settings["temperature"] <= 2.0
    payload = web_state.assistant_settings(owner)
    assert (payload["provider"], payload["source"], payload["configured"]) == ("", "none", False)
    assert payload["notice"] == (record.get("aiNotice", "") if isinstance(record, dict) else "")
    assert not any(item["hasApiKey"] for item in payload["providers"])
    with pytest.raises(WebError) as asked:
        web_state.ask_global_ai("ร้านเปิดกี่โมง", owner)
    assert (asked.value.code, asked.value.status) == ("assistant_not_configured", 409)
    with pytest.raises(WebError) as auto_reply:
        web_state.save_ai_auto_reply_settings("acct", {"enabled": True}, owner)
    assert (auto_reply.value.code, auto_reply.value.status) == ("assistant_not_configured", 409)
    assert web_state.test_assistant_settings(owner) == {"ok": False, "error": "not_configured"}
    # Old cached pages may still send the removed choices.
    for provider in ("ollama", "global", "", "nope"):
        with pytest.raises(WebError) as refused:
            web_state.save_assistant_settings({"provider": provider, "apiKey": "x" * 12}, owner)
        assert (refused.value.code, refused.value.status) == ("ai_settings_invalid", 400)
    assert calls == []


def test_the_removed_ai_notice_stays_until_a_working_ai_is_saved(web_state, monkeypatch):
    _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    _seed_tenant_settings(web_state, owner, {"provider": "", "aiNotice": "ollama_removed",
                                             "systemPrompt": "Be brief."})
    assert web_state.assistant_settings(owner)["notice"] == "ollama_removed"
    # A save that leaves no working AI (the key cleared) keeps the notice.
    kept = web_state.save_assistant_settings(
        {"provider": "openai", "clearApiKey": True, "test": False}, owner
    )
    assert (kept["notice"], kept["configured"]) == ("ollama_removed", False)
    saved = web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-openai-key", "test": False}, owner
    )
    assert (saved["notice"], saved["configured"], saved["systemPrompt"]) == ("", True, "Be brief.")
    # A notice kept on the workspace record itself is read and cleared the same way.
    _seed_tenant_settings(web_state, owner, {}, aiNotice="shared_ai_removed")
    assert web_state.assistant_settings(owner)["notice"] == "shared_ai_removed"
    web_state.save_assistant_settings({"provider": "openai", "apiKey": "sk-openai-key", "test": False}, owner)
    assert web_state.assistant_settings(owner)["notice"] == ""
    assert "aiNotice" not in web_state._global_ai_all()["tenants"][owner["id"]]


@pytest.mark.parametrize("provider_id", [provider.id for provider in webapp_module.provider_registry()])
def test_saving_tests_every_provider_with_a_short_chat(web_state, monkeypatch, provider_id):
    pings = []

    class _PingClient(_FakeAiClient):
        def chat(self, **kwargs):
            pings.append(kwargs)
            return {"answer": "pong", "canAnswer": True, "confidence": 1.0, "model": kwargs["model"]}

    monkeypatch.setattr(webapp_module, "provider_available", lambda provider_id: (True, ""))
    calls = _fake_clients(monkeypatch, _PingClient)
    _fake_dns(monkeypatch, {"llm.example.com": ["93.184.216.34"]})
    owner = _seed_owned_account(web_state, _owner(web_state))
    body = {"provider": provider_id, "apiKey": "key-for-the-test-1234", "test": True}
    if provider_id == "custom":
        body.update(baseUrl="https://llm.example.com/v1", chatModel="my-model")
    saved = web_state.save_assistant_settings(body, owner)
    assert saved["test"]["ok"] is True and saved["configured"] is True
    assert [ping["question"] for ping in pings] == ["ping"]
    assert calls[0]["provider"] == provider_id and calls[0]["apiKey"] == "key-for-the-test-1234"
    assert callable(calls[0]["transport"]) is (provider_id == "custom")


def test_a_failed_test_is_a_result_with_the_key_hidden(web_state, monkeypatch):
    outcomes = iter([
        webapp_module.AssistantError(
            "OpenAI error 401: Incorrect API key provided: sk-secret-test-key-0001. "
            "See https://platform.openai.com/account/api-keys?ref=x1",
            401,
            "ai_key_invalid",
        ),
        RuntimeError("boom sk-secret-test-key-0001"),
    ])

    class _FailingClient(_FakeAiClient):
        def chat(self, **kwargs):
            raise next(outcomes)

    _fake_clients(monkeypatch, _FailingClient)
    owner = _seed_owned_account(web_state, _owner(web_state))
    saved = web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-secret-test-key-0001"}, owner
    )
    assert saved["configured"] is True  # saved first, then tested
    assert saved["test"]["ok"] is False
    assert "Incorrect API key" in saved["test"]["error"] and "[API key]" in saved["test"]["error"]
    assert "sk-secret-test-key-0001" not in saved["test"]["error"]
    assert "ref=x1" not in saved["test"]["error"]
    unexpected = web_state.save_assistant_settings({"provider": "openai"}, owner)
    assert unexpected["test"] == {"ok": False, "error": "The AI test failed (RuntimeError)."}


def test_a_temperature_of_zero_stays_zero(web_state, monkeypatch):
    asked = []

    class _RecordingClient(_FakeAiClient):
        def chat(self, **kwargs):
            asked.append(kwargs)
            return super().chat(**kwargs)

    _fake_clients(monkeypatch, _RecordingClient)
    owner = _seed_owned_account(web_state, _owner(web_state))
    saved = web_state.save_assistant_settings(
        {"provider": "deepseek", "apiKey": "sk-deepseek-key", "temperature": 0, "test": False}, owner
    )
    assert saved["temperature"] == 0.0
    web_state.ask_global_ai("hello", owner)
    assert asked[-1]["temperature"] == 0.0
    normalize = web_state._tenant_ai_settings
    assert normalize({"temperature": 0})["temperature"] == 0.0
    assert normalize({})["temperature"] == 0.2
    assert normalize({"temperature": "warm"})["temperature"] == 0.2
    assert normalize({"temperature": 9})["temperature"] == 2.0
    assert normalize({"temperature": float("nan")})["temperature"] == 0.2


def test_a_provider_failure_while_asking_is_a_422_with_the_key_hidden(web_state, monkeypatch):
    class _BrokenClient(_FakeAiClient):
        def chat(self, **kwargs):
            raise webapp_module.AssistantError(
                "Grok error 429: rate limit reached for key xai-secret-key-0002", 429
            )

    _fake_clients(monkeypatch, _BrokenClient)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "xai", "apiKey": "xai-secret-key-0002", "test": False}, owner
    )
    with pytest.raises(WebError) as failed:
        web_state.ask_global_ai("hello", owner)
    assert (failed.value.code, failed.value.status) == ("ai_unavailable", 422)
    assert "rate limit" in failed.value.message
    assert "xai-secret-key-0002" not in failed.value.message


def test_assistant_history_says_only_whether_the_workspace_has_an_ai(web_state, monkeypatch):
    _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    data = web_state._global_ai_all()
    data["settings"] = {"enabled": True, "baseUrl": "http://10.0.0.5:11434", "chatModel": "llama3",
                        "apiKey": "god-secret-key", "systemPrompt": "God prompt"}
    web_state.store.set("global_ai", data)
    assert web_state.assistant_history(owner)["settings"] == {
        "configured": False, "provider": "", "source": "none",
    }
    web_state.save_assistant_settings(
        {"provider": "mistral", "apiKey": "mistral-key-0003", "test": False}, owner
    )
    assert web_state.assistant_history(owner)["settings"] == {
        "configured": True, "provider": "mistral", "source": "tenant",
    }


def test_model_lists_follow_each_model_kind():
    lists = webapp_module._ai_model_lists("openai", [
        {"name": "gpt-6-luna", "kind": "chat"},
        {"name": "text-embedding-3-small", "kind": "embedding"},
        {"name": "gpt-image-2", "kind": "other"},
        {"name": "", "kind": "chat"},
        "not a model",
    ])
    assert lists == {
        "chatModels": ["gpt-6-luna"],
        "embeddingModels": ["text-embedding-3-small"],
        "imageModels": [],
    }
    # Gemini's own list has no kind: it keeps the split by supported methods.
    gemini = webapp_module._ai_model_lists("gemini", GEMINI_MODELS)
    assert gemini["chatModels"] == ["gemini-2.5-flash", "gemini-2.5-pro"]
    assert gemini["imageModels"] == ["gemini-2.5-flash-image", "gemini-3-pro-image-preview"]


@pytest.mark.parametrize(
    "address",
    [
        "10.0.0.1", "172.16.5.4", "192.168.1.1", "127.0.0.1", "0.0.0.0", "169.254.169.254",
        "169.254.1.1", "100.64.0.1", "224.0.0.1", "239.255.255.250", "240.0.0.1",
        "255.255.255.255", "192.0.2.10", "198.18.0.1",
        "::1", "::", "fe80::1", "fc00::1", "fd00:ec2::254", "fec0::1", "ff02::1",
        # IPv6 forms with an IPv4 address inside, even a public one.
        "::ffff:127.0.0.1", "::ffff:8.8.8.8", "::127.0.0.1", "64:ff9b::808:808",
        "2002:808:808::1", "2001:0:4136:e378:8000:63bf:3fff:fdd2",
    ],
)
def test_internal_addresses_are_never_public(address):
    assert webapp_module._ip_address_is_public(ipaddress.ip_address(address)) is False


@pytest.mark.parametrize("address", ["8.8.8.8", "93.184.216.34", "1.1.1.1", "2606:4700:4700::1111"])
def test_public_addresses_are_public(address):
    assert webapp_module._ip_address_is_public(ipaddress.ip_address(address)) is True


def test_a_host_with_any_internal_address_is_refused(monkeypatch):
    _fake_dns(monkeypatch, {
        "mixed.example.com": ["93.184.216.34", "10.0.0.1"],
        "public.example.com": ["93.184.216.34"],
    })
    with pytest.raises(WebError) as refused:
        webapp_module._public_http_target("https://mixed.example.com/v1")
    assert refused.value.code == "unsafe_url"
    assert webapp_module._public_http_target("https://public.example.com/v1") == (
        "https://public.example.com/v1", ("93.184.216.34",)
    )


class _FakeRaw:
    def __init__(self, chunks, *, delay=0.0, forever=False):
        self.chunks = list(chunks)
        self.delay = delay
        self.forever = forever
        self.decoded: list[bool] = []  # decode_content of each read

    def read1(self, amount, decode_content=True):
        self.decoded.append(decode_content)
        if self.delay:
            time.sleep(self.delay)
        if self.chunks:
            return self.chunks.pop(0)
        return b"x" if self.forever else b""


class _FakeHttpResponse:
    def __init__(self, status=200, body=b"{}", *, headers=None, raw=None):
        self.status_code = status
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.raw = raw or _FakeRaw([body])
        self.closed = False

    def close(self):
        self.closed = True


class _FakeSession:
    """requests.Session for the Custom API transport: records calls, answers from a script."""

    script: list = []
    calls: list = []
    sessions: list = []

    def __init__(self):
        self.trust_env = True
        self.adapters = {}
        self.closed = False
        _FakeSession.sessions.append(self)

    def mount(self, prefix, adapter):
        self.adapters[prefix] = adapter

    def request(self, method, url, **kwargs):
        adapter = self.adapters["https://"]
        _FakeSession.calls.append({
            "method": method, "url": url, "address": adapter.address,
            "host": adapter.target.hostname, "trustEnv": self.trust_env, **kwargs,
        })
        outcome = _FakeSession.script.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def close(self):
        self.closed = True


@pytest.fixture
def custom_api(monkeypatch):
    """The Custom API transport over fake DNS and a fake requests.Session: no network."""
    _FakeSession.script = []
    _FakeSession.calls = []
    _FakeSession.sessions = []
    _fake_dns(monkeypatch, {
        "llm.example.com": ["93.184.216.34", "93.184.216.35"],
        "inside.example.com": ["10.0.0.7"],
        "meta.example.com": ["169.254.169.254"],
        "mapped.example.com": ["::ffff:127.0.0.1"],
    })
    monkeypatch.setattr(webapp_module.requests, "Session", _FakeSession)
    return _FakeSession


def _completion(answer):
    return json.dumps({
        "model": "my-model",
        "choices": [{"message": {"role": "assistant", "content": json.dumps(answer, ensure_ascii=False)},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 7, "completion_tokens": 3},
    }).encode()


def test_the_custom_api_transport_pins_the_checked_address_and_sends_json(custom_api):
    custom_api.script = [
        _FakeHttpResponse(200, b'{"ok": true}', headers={"Content-Type": "application/json"})
    ]
    transport = webapp_module._custom_ai_transport("https://llm.example.com/v1")
    status, headers, body = transport(
        "POST", "https://llm.example.com/v1/chat/completions",
        headers={"Authorization": "Bearer sk-test"}, json_body={"model": "m", "q": "สวัสดี"},
        timeout=10, max_bytes=1000,
    )
    assert (status, body, headers["Content-Type"]) == (200, b'{"ok": true}', "application/json")
    [call] = custom_api.calls
    assert (call["address"], call["host"]) == ("93.184.216.34", "llm.example.com")
    assert call["trustEnv"] is False  # no proxy or .netrc credentials from the environment
    assert call["allow_redirects"] is False and call["stream"] is True
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["headers"]["Content-Type"] == "application/json"
    assert call["headers"]["Accept-Encoding"] == "identity"
    assert json.loads(call["data"].decode("utf-8")) == {"model": "m", "q": "สวัสดี"}
    connect, read = call["timeout"]
    assert connect <= 5.0 and read <= 10.0
    assert all(session.closed for session in custom_api.sessions)


def test_the_custom_api_transport_refuses_other_origins_http_and_internal_addresses(custom_api):
    for base, url, code in (
        ("https://llm.example.com/v1", "https://evil.example.com/v1/models", "ai_base_url_invalid"),
        ("https://llm.example.com/v1", "https://llm.example.com:8443/v1/models", "ai_base_url_invalid"),
        ("http://llm.example.com/v1", "http://llm.example.com/v1/models", "ai_base_url_invalid"),
        ("https://inside.example.com/v1", "https://inside.example.com/v1/models", "ai_base_url_blocked"),
        ("https://meta.example.com/v1", "https://meta.example.com/v1/models", "ai_base_url_blocked"),
        ("https://mapped.example.com/v1", "https://mapped.example.com/v1/models", "ai_base_url_blocked"),
        ("https://127.0.0.1/v1", "https://127.0.0.1/v1/models", "ai_base_url_blocked"),
        ("https://[::1]/v1", "https://[::1]/v1/models", "ai_base_url_blocked"),
        ("https://169.254.169.254/latest", "https://169.254.169.254/latest/meta-data",
         "ai_base_url_blocked"),
    ):
        transport = webapp_module._custom_ai_transport(base)
        with pytest.raises(webapp_module.AssistantError) as refused:
            transport("GET", url, headers={}, json_body=None, timeout=5, max_bytes=1000)
        assert refused.value.code == code, (base, url)
    assert custom_api.calls == []  # refused before any connection


def test_the_custom_api_transport_never_follows_a_redirect(custom_api):
    custom_api.script = [_FakeHttpResponse(302, b"", headers={"Location": "http://169.254.169.254/"})]
    transport = webapp_module._custom_ai_transport("https://llm.example.com/v1")
    with pytest.raises(webapp_module.AssistantError) as refused:
        transport("POST", "https://llm.example.com/v1/chat/completions", headers={}, json_body={},
                  timeout=5, max_bytes=1000)
    assert refused.value.status_code == 302 and "redirect" in str(refused.value)
    assert len(custom_api.calls) == 1


def test_the_custom_api_transport_caps_the_answer(custom_api):
    transport = webapp_module._custom_ai_transport("https://llm.example.com/v1")
    custom_api.script = [
        _FakeHttpResponse(200, raw=_FakeRaw([b"x" * 600, b"x" * 600])),
        _FakeHttpResponse(200, headers={"Content-Length": "5000"}),
    ]
    for _ in range(2):
        with pytest.raises(webapp_module.AssistantError) as too_big:
            transport("GET", "https://llm.example.com/v1/models", headers={}, json_body=None,
                      timeout=5, max_bytes=1000)
        assert "larger than" in str(too_big.value)


def test_the_custom_api_transport_has_a_total_deadline(custom_api):
    # A server that trickles one byte at a time cannot stretch a call past its deadline.
    custom_api.script = [_FakeHttpResponse(200, raw=_FakeRaw([], delay=0.05, forever=True))]
    transport = webapp_module._custom_ai_transport("https://llm.example.com/v1")
    started = time.monotonic()
    with pytest.raises(webapp_module.AssistantError) as late:
        transport("POST", "https://llm.example.com/v1/chat/completions", headers={}, json_body={},
                  timeout=1, max_bytes=10_000_000)
    assert "did not answer in time" in str(late.value)
    assert time.monotonic() - started < 3


def test_the_custom_api_transport_never_sends_a_request_twice(custom_api):
    transport = webapp_module._custom_ai_transport("https://llm.example.com/v1")

    def post():
        return transport("POST", "https://llm.example.com/v1/chat/completions", headers={},
                         json_body={"q": 1}, timeout=5, max_bytes=1000)

    # Once the request may have reached the server, the other address is not tried.
    for failure, reason in (
        (requests.exceptions.ReadTimeout("read timed out"), "did not answer in time"),
        (requests.exceptions.ConnectionError(urllib3.exceptions.ProtocolError("Connection aborted.")),
         "connection broke"),
    ):
        custom_api.calls.clear()
        custom_api.script = [failure, _FakeHttpResponse(200)]
        with pytest.raises(webapp_module.AssistantError) as failed:
            post()
        assert reason in str(failed.value)
        assert [call["address"] for call in custom_api.calls] == ["93.184.216.34"]
    # A connection that never opened sent nothing, so the next address may answer.
    refused = urllib3.exceptions.NewConnectionError(None, "Connection refused")
    for failure in (
        requests.exceptions.ConnectionError(urllib3.exceptions.MaxRetryError(None, "/v1", refused)),
        requests.exceptions.ConnectTimeout("connect timed out"),
    ):
        custom_api.calls.clear()
        custom_api.script = [failure, _FakeHttpResponse(200, b'{"ok": true}')]
        status, _headers, body = post()
        assert (status, body) == (200, b'{"ok": true}')
        assert [call["address"] for call in custom_api.calls] == ["93.184.216.34", "93.184.216.35"]
    # Both addresses refused: one short error.
    custom_api.calls.clear()
    custom_api.script = [requests.exceptions.ConnectTimeout(), requests.exceptions.ConnectTimeout()]
    with pytest.raises(webapp_module.AssistantError) as unreachable:
        post()
    assert "cannot connect" in str(unreachable.value) and len(custom_api.calls) == 2


def test_the_custom_api_client_talks_through_the_transport(custom_api):
    custom_api.script = [
        _FakeHttpResponse(200, _completion({"answer": "ร้านเปิด 9 โมง", "canAnswer": True, "confidence": 0.8}))
    ]
    client = webapp_module.make_ai_client(
        "custom", api_key="sk-custom-secret", base_url="https://llm.example.com/v1",
        transport=webapp_module._custom_ai_transport("https://llm.example.com/v1"),
    )
    result = client.chat(model="my-model", question="เปิดกี่โมง", context="ร้านเปิด 9 โมง",
                         system_prompt="Shop.", strict_knowledge=True, temperature=0.2)
    assert (result["answer"], result["canAnswer"]) == ("ร้านเปิด 9 โมง", True)
    [call] = custom_api.calls
    assert call["url"] == "https://llm.example.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-custom-secret"
    sent = json.loads(call["data"])
    assert sent["model"] == "my-model" and sent["response_format"] == {"type": "json_object"}


def test_a_saved_custom_api_is_called_only_through_the_public_address_transport(
    web_state, custom_api, monkeypatch
):
    answer = {"answer": "pong", "canAnswer": True, "confidence": 1.0}
    custom_api.script = [_FakeHttpResponse(200, _completion(answer))]
    owner = _seed_owned_account(web_state, _owner(web_state))
    saved = web_state.save_assistant_settings(
        {"provider": "custom", "apiKey": "sk-custom-secret", "baseUrl": "https://llm.example.com/v1",
         "chatModel": "my-model", "test": True},
        owner,
    )
    assert saved["test"]["ok"] is True, saved["test"]
    [call] = custom_api.calls
    assert (call["url"], call["address"], call["trustEnv"]) == (
        "https://llm.example.com/v1/chat/completions", "93.184.216.34", False
    )
    # A redirect answer is a failed test, not a followed hop.
    custom_api.script = [_FakeHttpResponse(307, b"", headers={"Location": "http://10.0.0.1/"})]
    retest = web_state.test_assistant_settings(owner)
    assert retest["ok"] is False and "redirect" in retest["error"]
    # The host now resolves to an internal address (DNS rebinding): nothing is sent.
    _fake_dns(monkeypatch, {"llm.example.com": ["10.0.0.9"]})
    with pytest.raises(WebError) as failed:
        web_state.ask_global_ai("hello", owner)
    assert (failed.value.code, failed.value.status) == ("ai_unavailable", 422)
    assert len(custom_api.calls) == 2


def test_the_custom_api_transport_refuses_a_compressed_answer_unread(custom_api):
    transport = webapp_module._custom_ai_transport("https://llm.example.com/v1")

    def get(encoding, body=b'{"ok": true}'):
        raw = _FakeRaw([body])
        response = _FakeHttpResponse(200, headers={"Content-Encoding": encoding}, raw=raw)
        custom_api.script = [response]
        try:
            return transport("GET", "https://llm.example.com/v1/models", headers={},
                             json_body=None, timeout=5, max_bytes=1000), raw, response
        finally:
            custom_api.script = []

    # It asked for identity: anything else could inflate past the size cap in one read.
    for encoding in ("gzip", "br", "deflate", "gzip, gzip", "zstd", "x-gzip", "identity, gzip"):
        raw = _FakeRaw([b"\x1f\x8b" * 10])
        response = _FakeHttpResponse(500, headers={"Content-Encoding": encoding}, raw=raw)
        custom_api.script = [response]
        with pytest.raises(webapp_module.AssistantError) as refused:
            transport("GET", "https://llm.example.com/v1/models", headers={}, json_body=None,
                      timeout=5, max_bytes=1000)
        assert "compressed" in str(refused.value) and refused.value.status_code == 500, encoding
        assert raw.decoded == [] and response.closed  # not a byte read
    # No encoding, or identity: the body comes back exactly as sent, never decoded.
    for encoding in ("", "identity", " IDENTITY "):
        (status, _headers, body), raw, _response = get(encoding)
        assert (status, body) == (200, b'{"ok": true}')
        assert raw.decoded and not any(raw.decoded), encoding


class _RecordingWatchdog(webapp_module.DeadlineWatchdog):
    made: list[_RecordingWatchdog] = []

    def __init__(self, deadline):
        super().__init__(deadline)
        self.starts = 0
        self.cancels = 0
        _RecordingWatchdog.made.append(self)

    def start(self):
        self.starts += 1
        super().start()

    def cancel(self):
        self.cancels += 1
        super().cancel()


def test_the_custom_api_transport_watches_each_attempt_and_always_disarms(
    custom_api, monkeypatch
):
    monkeypatch.setattr(webapp_module, "DeadlineWatchdog", _RecordingWatchdog)
    transport = webapp_module._custom_ai_transport("https://llm.example.com/v1")
    refused = urllib3.exceptions.NewConnectionError(None, "Connection refused")
    outcomes = [
        ([_FakeHttpResponse(200, b"{}")], None),
        ([_FakeHttpResponse(500, b'{"error": "down"}')], None),
        ([_FakeHttpResponse(302, b"", headers={"Location": "/v2"})], "redirect"),
        ([_FakeHttpResponse(200, headers={"Content-Encoding": "gzip"})], "compressed"),
        ([_FakeHttpResponse(200, raw=_FakeRaw([b"x" * 2000]))], "larger than"),
        ([requests.exceptions.ReadTimeout("read timed out")], "did not answer in time"),
        ([requests.exceptions.SSLError("bad certificate")], "secure connection failed"),
        ([requests.exceptions.ConnectionError(
            urllib3.exceptions.MaxRetryError(None, "/v1", refused)), _FakeHttpResponse(200)], None),
        ([requests.exceptions.ConnectTimeout(), requests.exceptions.ConnectTimeout()],
         "cannot connect"),
    ]
    for script, failure in outcomes:
        _RecordingWatchdog.made = []
        custom_api.calls.clear()
        custom_api.sessions.clear()
        custom_api.script = list(script)
        started = time.monotonic()
        if failure is None:
            transport("POST", "https://llm.example.com/v1/chat/completions", headers={},
                      json_body={"q": 1}, timeout=10, max_bytes=1000)
        else:
            with pytest.raises(webapp_module.AssistantError, match=failure):
                transport("POST", "https://llm.example.com/v1/chat/completions", headers={},
                          json_body={"q": 1}, timeout=10, max_bytes=1000)
        attempts = len(custom_api.calls)
        # One watchdog and one session per address tried, each armed and then disarmed,
        # whatever the outcome; every one ends at the call's own total deadline.
        assert len(_RecordingWatchdog.made) == len(custom_api.sessions) == attempts, script
        assert [session.adapters["https://"].watchdog for session in custom_api.sessions] == (
            _RecordingWatchdog.made
        )
        for watchdog in _RecordingWatchdog.made:
            assert (watchdog.starts, watchdog.cancels, watchdog.fired) == (1, 1, False), script
            assert started + 9 < watchdog.deadline <= time.monotonic() + 10
        assert all(session.closed for session in custom_api.sessions)


# --- The Custom API transport over real sockets (127.0.0.1, a throwaway test CA) ---

_CUSTOM_API_HOST = "llm.test"  # never looked up: the transport is pinned to 127.0.0.1


class _TlsServer:
    """A TLS server on 127.0.0.1 that runs ``handler(conn)`` for each connection."""

    def __init__(self, cert_file, key_file, handler):
        import ssl

        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.load_cert_chain(cert_file, key_file)
        self.handler = handler
        self.connections = 0
        self.listener = socket.socket()
        self.listener.bind(("127.0.0.1", 0))
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    @property
    def port(self):
        return self.listener.getsockname()[1]

    def _serve(self):
        while not self.stop.is_set():
            try:
                conn, _address = self.listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            self.connections += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn):
        try:
            conn.settimeout(15)
            with self.context.wrap_socket(conn, server_side=True) as tls:
                self.handler(tls)
        except OSError:
            pass  # the client gave up first, or refused the certificate
        finally:
            conn.close()

    def close(self):
        self.stop.set()
        self.thread.join(2)
        self.listener.close()


def _test_certificates(tmp_path, host):
    """A throwaway CA and a server certificate for ``host`` only (no IP address)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    now = dt.datetime.now(dt.timezone.utc)
    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "LinePassport test CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=True,
                crl_sign=True, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()),
                       critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    key = ec.generate_private_key(ec.SECP256R1())
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(host)]), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    files = {"ca": tmp_path / "ca.pem", "cert": tmp_path / "cert.pem", "key": tmp_path / "key.pem"}
    files["ca"].write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    files["cert"].write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    files["key"].write_bytes(
        key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                          serialization.NoEncryption())
    )
    return {name: str(path) for name, path in files.items()}


@pytest.fixture
def custom_tls(tmp_path, monkeypatch):
    """Start TLS servers for the Custom API host, pinned to 127.0.0.1. No DNS at all."""
    files = _test_certificates(tmp_path, _CUSTOM_API_HOST)
    # Two addresses, as a real host may have: a second connection would show up.
    monkeypatch.setattr(
        webapp_module, "_public_http_target", lambda url, **kw: (url, ("127.0.0.1", "127.0.0.1"))
    )
    # The transport ignores the environment (trust_env off): trust the test CA the
    # way requests finds its default bundle.
    monkeypatch.setattr(requests.adapters, "DEFAULT_CA_BUNDLE_PATH", files["ca"])
    monkeypatch.setattr(requests.adapters, "_preloaded_ssl_context", None, raising=False)

    def numeric_only(host, *args, **kwargs):
        try:
            ipaddress.ip_address(str(host).strip("[]"))
        except ValueError:
            raise AssertionError(f"unexpected DNS lookup of {host!r}") from None
        return _REAL_GETADDRINFO(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", numeric_only)
    servers = []

    def start(handler):
        server = _TlsServer(files["cert"], files["key"], handler)
        servers.append(server)
        return server

    yield start
    for server in servers:
        server.close()


def _read_request_head(conn):
    """Read a whole request (its body too, so closing sends no reset); return its head."""
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = conn.recv(65536)
        if not chunk:
            return data
        data += chunk
    head, _, body = data.partition(b"\r\n\r\n")
    length = re.search(rb"(?im)^content-length:\s*(\d+)\s*$", head)
    missing = int(length.group(1)) - len(body) if length else 0
    while missing > 0:
        chunk = conn.recv(65536)
        if not chunk:
            break
        missing -= len(chunk)
    return head + b"\r\n\r\n"


def _answer(head_and_body):
    """A handler that reads the request, keeps its head, and sends ``head_and_body``."""
    heads = []

    def handler(conn):
        heads.append(_read_request_head(conn))
        conn.sendall(head_and_body)

    handler.heads = heads
    return handler


def test_the_custom_api_transport_works_over_a_pinned_tls_connection(custom_tls):
    handler = _answer(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 11\r\n"
        b'Connection: close\r\n\r\n{"ok":true}'
    )
    server = custom_tls(handler)
    base = f"https://{_CUSTOM_API_HOST}:{server.port}/v1"
    status, headers, body = webapp_module._custom_ai_transport(base)(
        "POST", f"{base}/chat/completions", headers={"Authorization": "Bearer sk-test"},
        json_body={"q": "สวัสดี"}, timeout=5, max_bytes=1000,
    )
    assert (status, json.loads(body), headers["Content-Type"]) == (200, {"ok": True},
                                                                 "application/json")
    [head] = handler.heads
    # Sent to 127.0.0.1, for the saved host: its certificate names only the host.
    assert f"Host: {_CUSTOM_API_HOST}:{server.port}\r\n".encode() in head
    assert b"Accept-Encoding: identity\r\n" in head
    assert server.connections == 1


def test_the_custom_api_transport_reads_a_chunked_answer_without_decoding_it(custom_tls):
    # Chunked framing is still undone (it is not a Content-Encoding).
    server = custom_tls(_answer(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nTransfer-Encoding: chunked\r\n"
        b'Connection: close\r\n\r\n5\r\n{"ok"\r\n6\r\n:true}\r\n0\r\n\r\n'
    ))
    base = f"https://{_CUSTOM_API_HOST}:{server.port}/v1"
    status, _headers, body = webapp_module._custom_ai_transport(base)(
        "GET", f"{base}/models", headers={}, json_body=None, timeout=5, max_bytes=1000
    )
    assert (status, body) == (200, b'{"ok":true}')


def test_the_custom_api_transport_still_checks_the_saved_hosts_certificate(custom_tls):
    server = custom_tls(_answer(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}"))
    base = f"https://other.test:{server.port}/v1"  # the certificate is for llm.test
    with pytest.raises(webapp_module.AssistantError, match="secure connection failed"):
        webapp_module._custom_ai_transport(base)(
            "GET", f"{base}/models", headers={}, json_body=None, timeout=5, max_bytes=1000
        )


_TRICKLED_ANSWER = (
    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nX-Pad: " + b"a" * 60
    + b"\r\nContent-Length: 2\r\n\r\n{}"
)
"""About 11 seconds of status line and headers at one TLS record per byte every 0.1 s."""


def _trickle_head(conn):
    _read_request_head(conn)
    for byte in _TRICKLED_ANSWER:
        conn.sendall(bytes([byte]))
        time.sleep(0.1)


def _trickle_body(conn):
    _read_request_head(conn)
    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                 b"Content-Length: 1000\r\n\r\n")
    for _ in range(1000):
        conn.sendall(b" ")
        time.sleep(0.05)


def _stay_silent(conn):
    while conn.recv(65536):
        pass


@pytest.mark.parametrize("serve", [_trickle_head, _trickle_body, _stay_silent],
                         ids=["headers-trickle", "body-trickles", "silent"])
def test_the_custom_api_transport_keeps_its_total_deadline_in_every_phase(custom_tls, serve):
    server = custom_tls(serve)
    base = f"https://{_CUSTOM_API_HOST}:{server.port}/v1"
    started = time.monotonic()
    with pytest.raises(webapp_module.AssistantError, match="did not answer in time"):
        webapp_module._custom_ai_transport(base)(
            "POST", f"{base}/chat/completions", headers={}, json_body={"q": 1},
            timeout=1.5, max_bytes=10_000,
        )
    assert time.monotonic() - started < 3.5
    time.sleep(0.3)
    # The request may have reached the server: the second address is never tried.
    assert server.connections == 1


def test_the_custom_api_transport_never_inflates_a_compressed_bomb(custom_tls):
    import gzip
    import tracemalloc

    bomb = gzip.compress(gzip.compress(b"\0" * 20_000_000))  # a few KB, 20 MB inflated
    for encoding in ("gzip, gzip", "gzip"):
        server = custom_tls(_answer(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Encoding: "
            + encoding.encode() + b"\r\nContent-Length: " + str(len(bomb)).encode()
            + b"\r\n\r\n" + bomb
        ))
        base = f"https://{_CUSTOM_API_HOST}:{server.port}/v1"
        tracemalloc.start()
        try:
            with pytest.raises(webapp_module.AssistantError, match="compressed answer"):
                webapp_module._custom_ai_transport(base)(
                    "POST", f"{base}/chat/completions", headers={}, json_body={"q": 1},
                    timeout=5, max_bytes=2 * 1024 * 1024,
                )
            peak = tracemalloc.get_traced_memory()[1]
        finally:
            tracemalloc.stop()
        assert peak < 8 * 1024 * 1024, (encoding, peak)


# --- The God's global knowledge: keyword relevance, for every workspace --------

# Shop policies as a God might share them with every workspace.
_GOD_TEXTS = {
    "shipping": (
        "## การจัดส่งสินค้า\n\nจัดส่งทุกวันจันทร์ถึงเสาร์ ผ่าน Kerry Express และไปรษณีย์ไทย "
        "สั่งก่อน 14:00 น. ส่งภายในวันเดียวกัน ลูกค้าในกรุงเทพได้รับของภายใน 1-2 วัน "
        "ต่างจังหวัด 2-4 วัน ส่งฟรีเมื่อซื้อครบ 500 บาท ต่ำกว่านั้นค่าส่ง 50 บาท"
    ),
    "payment": (
        "## การชำระเงิน\n\nรับชำระผ่านการโอนเงินเข้าบัญชีธนาคาร พร้อมเพย์ และบัตรเครดิต/เดบิต "
        "บัตรเครดิตที่ร่วมรายการผ่อนชำระ 0% ได้นานสูงสุด 10 เดือน สำหรับยอดซื้อตั้งแต่ 3,000 บาทขึ้นไป "
        "เก็บเงินปลายทางมีค่าธรรมเนียม 30 บาท"
    ),
    "returns": (
        "## การคืนและเปลี่ยนสินค้า\n\nเปลี่ยนหรือคืนสินค้าได้ภายใน 7 วันหลังได้รับของ "
        "สินค้าต้องยังไม่ผ่านการใช้งานและอยู่ในกล่องเดิม หากสินค้าชำรุดจากการขนส่ง "
        "กรุณาถ่ายวิดีโอตอนแกะกล่องแล้วแจ้งทางแชท ทางร้านจะส่งของใหม่ให้โดยไม่มีค่าใช้จ่าย"
    ),
    "warranty": (
        "## การรับประกัน\n\nเครื่องใช้ไฟฟ้ารับประกัน 1 ปีนับจากวันที่ซื้อ ครอบคลุมความเสียหายจากการผลิต "
        "ไม่ครอบคลุมการตกหล่นหรือโดนน้ำ ส่งเคลมได้ที่ศูนย์บริการทุกสาขา"
    ),
    "hours": (
        "## เวลาตอบแชท\n\nแอดมินตอบแชททุกวัน 09:00-21:00 น. "
        "ข้อความที่ส่งนอกเวลาจะได้รับคำตอบในวันถัดไป"
    ),
    "tax": (
        "## ใบกำกับภาษี\n\nขอใบกำกับภาษีเต็มรูปได้ โดยแจ้งชื่อบริษัท ที่อยู่ "
        "และเลขประจำตัวผู้เสียภาษีภายในวันที่สั่งซื้อ"
    ),
    "express": "## ส่งด่วน\n\nมีบริการส่งด่วนในกรุงเทพ ได้รับของภายใน 3 ชั่วโมง ค่าบริการ 120 บาท",
    "storefront": "Question: ร้านมีหน้าร้านไหม\n\nAnswer: ไม่มีหน้าร้าน ขายออนไลน์อย่างเดียว",
    "shipping_en": (
        "## Shipping\n\nWe ship Monday to Saturday with Kerry Express and Thailand Post. Orders "
        "placed before 2 pm ship the same day. Bangkok deliveries take 1-2 days, other provinces "
        "2-4 days. Shipping is free on orders over 500 baht; otherwise it costs 50 baht."
    ),
    "returns_en": (
        "## Returns\n\nYou can return or exchange an item within 7 days of delivery if it is "
        "unused and in its original box. If it arrives damaged, film yourself opening the parcel "
        "and tell us in the chat; we send a replacement free of charge."
    ),
}


def _god_chunks():
    return [
        {"id": f"god-{name}", "sourceId": "knowledge-md", "sourceName": name, "text": text}
        for name, text in _GOD_TEXTS.items()
    ]


def _set_god_chunks(web_state, chunks):
    data = web_state._global_ai_all()
    data["chunks"] = chunks
    web_state.store.set("global_ai", data)


@pytest.mark.parametrize(
    ("question", "topic"),
    [
        ("ส่งฟรีไหมครับ", "shipping"),
        ("ค่าส่งเท่าไหร่คะ", "shipping"),
        ("ผ่อนได้ไหม", "payment"),
        ("รับบัตรเครดิตไหมคะ", "payment"),
        ("มีเก็บเงินปลายทางไหม", "payment"),
        # "บริการ" (service) is in the express delivery text too: it says nothing.
        ("มีบริการผ่อนชำระไหม", "payment"),
        ("เปลี่ยนสินค้าได้ไหม", "returns"),
        ("คืนของได้กี่วัน", "returns"),
        ("ประกันกี่ปี", "warranty"),
        ("เคลมสินค้ายังไง", "warranty"),
        ("ขอใบกำกับภาษีได้ไหม", "tax"),
        ("มีส่งด่วนไหม", "express"),
        ("มีหน้าร้านไหม", "storefront"),
        ("Do you offer free shipping?", "shipping_en"),
        ("Can I return an item?", "returns_en"),
        ("What if the parcel arrives damaged?", "returns_en"),
    ],
)
def test_a_clearly_related_global_chunk_is_used(question, topic):
    ranked = webapp_module._rank_global_knowledge(question, _god_chunks(), limit=3)
    assert [chunk["sourceName"] for chunk in ranked] == [topic], [
        (chunk["sourceName"], chunk["score"]) for chunk in ranked
    ]
    assert ranked[0]["score"] >= webapp_module.GLOBAL_KNOWLEDGE_MIN_SCORE


@pytest.mark.parametrize(
    "question",
    [
        "มีสีแดงไหม",
        "ไซส์ L รอบอกเท่าไหร่",
        "สินค้าตัวนี้ยังมีของไหม",  # "product", "this one" and "of" say nothing
        "ราคาเท่าไหร่",  # one stray group: "าคา" is in "ธนาคาร" (bank)
        "มีโปรโมชั่นอะไรบ้าง",
        "ใช้ได้กับ iPhone ไหม",
        "สวัสดีครับ",
        "ขอบคุณค่ะ",
        "ลดราคาได้ไหม",
        "ของแท้ไหม",
        "Do you have this in red?",
        "What sizes do you have?",
        "Is this compatible with iPhone?",
        "Hello",
    ],
)
def test_a_clearly_unrelated_global_chunk_is_not_used(question):
    assert webapp_module._rank_global_knowledge(question, _god_chunks(), limit=3) == []


def test_a_long_global_chunk_holding_the_answer_is_still_found():
    from okline.assistant import chunk_markdown, lexical_score

    topics = ("shipping", "payment", "returns", "warranty", "hours", "tax", "express", "storefront")
    [text] = chunk_markdown("\n\n".join(_GOD_TEXTS[topic] for topic in topics))
    assert len(text) > 1000  # short God topics are merged into chunks of up to 1200 letters
    chunk = {"id": "god-all", "sourceName": "knowledge.md", "text": text}
    for question in ("ส่งฟรีไหมครับ", "ผ่อนได้ไหม", "ประกันกี่ปี", "แอดมินตอบกี่โมง"):
        # The score for a shop's own chunks shrinks with the chunk's length, so a
        # fixed cut-off on it (such as 0.08) would drop this chunk.
        assert lexical_score(question, text) < 0.08
        ranked = webapp_module._rank_global_knowledge(question, [chunk], limit=3)
        assert [item["id"] for item in ranked] == ["god-all"], question
    for question in ("มีสีแดงไหม", "ไซส์ L รอบอกเท่าไหร่", "Do you have this in red?"):
        assert webapp_module._rank_global_knowledge(question, [chunk], limit=3) == []


def test_only_global_chunks_nearly_as_good_as_the_best_come_along():
    rank = webapp_module._rank_global_knowledge
    installments = {"id": "a", "sourceName": "installments", "text": "ผ่อนชำระ 0% นาน 10 เดือน"}
    cards = {"id": "b", "sourceName": "cards", "text": "ผ่อนได้ ชำระด้วยบัตรเครดิตทุกธนาคาร"}
    question = "มีบริการผ่อนชำระไหม"
    # On its own the cards text is related enough (4 of the 6 groups of "ผ่อนชำระ") ...
    assert [chunk["id"] for chunk in rank(question, [cards], limit=3)] == ["b"]
    # ... but beside a text holding the whole question it stays out.
    assert [chunk["id"] for chunk in rank(question, [cards, installments], limit=3)] == ["a"]
    # Both shipping texts name Kerry: both come, best first, up to the limit.
    both = rank("Kerry ส่งไหม", _god_chunks(), limit=3)
    assert [chunk["sourceName"] for chunk in both] == ["shipping", "shipping_en"]
    assert [chunk["sourceName"] for chunk in rank("Kerry ส่งไหม", _god_chunks(), limit=1)] == [
        "shipping"
    ]
    assert rank("Kerry ส่งไหม", _god_chunks(), limit=0) == []
    # No vector of the God's old index is passed on.
    with_vector = [{**installments, "embedding": [0.1, 0.2]}]
    assert "embedding" not in rank(question, with_vector, limit=3)[0]


def test_question_words_and_polite_endings_say_nothing_about_the_topic():
    words = webapp_module._knowledge_query_words
    assert words("มีบริการผ่อนชำระไหมคะ") == ["ผ่อนชำระ"]
    assert words("ส่งฟรีไหมครับ") == ["ส่งฟรี"]
    assert words("รบกวนสอบถามค่าส่งเท่าไหร่ครับ") == ["ค่าส่ง"]
    assert words("ประกันกี่ปี") == ["ประกัน"]  # "กี่" splits; "ปี" is too short alone
    assert words("Do you offer free shipping?") == ["offer", "free", "shipping"]
    assert words("Hello, thank you") == []


# English shop policies whose words hold short English words inside them: "red" in
# credit, covered and answered; "lack" in black and packaging; "arge" in charge.
_GOD_TEXTS_EN = {
    "payment": (
        "## Payment\n\nWe accept bank transfer, PromptPay and credit or debit cards. "
        "Cash on delivery costs 30 baht."
    ),
    "warranty": (
        "## Warranty\n\nElectronics are covered by a one-year warranty against manufacturing "
        "defects. Water damage is not covered."
    ),
    "hours": (
        "## Chat hours\n\nOur team answers chats every day from 9:00 to 21:00. Messages sent "
        "later are answered the next day."
    ),
    "returns": (
        "## Returns\n\nUnused items can be returned within 7 days in their original packaging. "
        "Damaged parcels are replaced free of charge."
    ),
}


def _god_chunks_en():
    return [
        {"id": f"god-{name}", "sourceId": "knowledge-md", "sourceName": name, "text": text}
        for name, text in _GOD_TEXTS_EN.items()
    ]


@pytest.mark.parametrize("question", ["Do you have it in red?", "Red?", "Black?", "Large?"])
def test_english_words_do_not_match_letters_inside_other_words(question):
    assert webapp_module._rank_global_knowledge(question, _god_chunks_en(), limit=3) == []


@pytest.mark.parametrize(
    ("question", "topic"),
    [
        ("Can I pay by credit card?", "payment"),  # "card" starts "cards"
        ("Cash on delivery?", "payment"),
        ("Is there a warranty?", "warranty"),
        ("When do you answer chats?", "hours"),  # "answer" starts "answers"
        ("Can it be returned?", "returns"),
    ],
)
def test_english_words_match_whole_words_and_their_starts(question, topic):
    ranked = webapp_module._rank_global_knowledge(question, _god_chunks_en(), limit=3)
    assert [chunk["sourceName"] for chunk in ranked] == [topic], [
        (chunk["sourceName"], chunk["score"]) for chunk in ranked
    ]


@pytest.mark.parametrize("list_from", [1, 1000])  # both ways of finding the words
def test_an_english_word_counts_as_a_word_and_its_longer_or_shorter_forms(monkeypatch, list_from):
    monkeypatch.setattr(webapp_module, "GLOBAL_KNOWLEDGE_WORD_LIST_FROM", list_from)
    terms = webapp_module._knowledge_query_terms
    has = webapp_module._global_knowledge_score
    assert has(terms("blue"), "light_blue x-blue") == 1.0  # "_" and "-" end a word
    assert has(terms("blue"), "lightblue ฟ้าblue") == 0.0  # inside a word, Thai too
    text = "you can return it for free; we ship cards and covered parcels, size xl."
    assert has(terms("cards"), text) == 1.0  # the word itself
    assert has(terms("card"), text) == 1.0  # the start of a chunk word
    assert has(terms("returned"), text) == 1.0  # "return": 4+ letters that start it
    assert has(terms("shipping"), text) == 1.0  # "ship" too
    assert has(terms("red"), text) == 0.0  # letters inside "covered", not a word
    assert has(terms("arc"), text) == 0.0  # inside "parcels", not a word start
    assert has(terms("xl"), text) == 0.0  # a lone 2-letter word is not enough
    assert has(terms("xl free"), text) == 1.0
    assert has(terms("xl blue"), text) == 0.0


def test_a_long_message_is_looked_for_by_a_capped_number_of_terms():
    terms = webapp_module._knowledge_query_terms
    long_message = " ".join(
        f"ส่งฟรีเมื่อซื้อครบ{index}บาท word{index} คำถามที่{index}" for index in range(400)
    )[:4000]
    assert len(long_message) == 4000
    query = terms(long_message)
    distinct = len(query.groups) + len(query.words)
    assert distinct == webapp_module.MAX_GLOBAL_KNOWLEDGE_QUERY_TERMS
    # Each distinct term is kept once, weighed by how often the question has it.
    repeated = terms("ส่งฟรี ส่งฟรี shipping shipping")
    assert repeated.groups == {"ส่ง": 2, "่งฟ": 2, "งฟร": 2, "ฟรี": 2}
    assert repeated.words == {"shipping": 12}
    assert repeated.total == 20


def test_a_long_run_of_letters_is_looked_for_by_its_first_40(monkeypatch):
    terms = webapp_module._knowledge_query_terms
    token = "ab12" * 1000  # one 4000-letter "word", such as a pasted code
    assert webapp_module.MAX_KNOWLEDGE_QUERY_WORD_LENGTH == 40
    assert terms(token).words == {token[:40]: 38}
    crafted = terms(" ".join(f"word{index}" for index in range(23)) + " " + token[:3800])
    assert max(len(word) for word in crafted.words) == 40
    # Looking for it in a chunk takes a few dozen searches, not one per letter.
    searched = []
    original = webapp_module._text_has_word_start

    def counting(text, word, *, whole):
        searched.append(word)
        return original(text, word, whole=whole)

    monkeypatch.setattr(webapp_module, "_text_has_word_start", counting)
    assert webapp_module._global_knowledge_score(terms(token), "we ship every day") == 0.0
    assert len(searched) <= 40
    # Glued to Thai, the way a Thai message ends or starts, it is cut all the same.
    for glued in (token + "ครับ", token + "ของ", "สอบถาม" + token, "ราคา " + token + "ค่ะ"):
        words = webapp_module._knowledge_query_words(glued)
        assert max(len(word) for word in words) <= 40, glued[-12:]
        assert max(len(word) for word in terms(glued).words) <= 40


def test_strict_knowledge_skips_the_ai_when_only_letters_of_god_words_match(web_state, monkeypatch):
    asked = []

    class _RecordingClient(_FakeAiClient):
        def chat(self, **kwargs):
            asked.append(kwargs)
            return {"answer": "ok", "canAnswer": True, "confidence": 0.8, "model": kwargs["model"]}

    _fake_clients(monkeypatch, _RecordingClient)
    _set_god_chunks(web_state, [
        {"id": "god-pay", "sourceName": "payment", "text": "We accept bank transfer and credit cards."},
        {"id": "god-warranty", "sourceName": "warranty", "text": "Defects are covered for one year."},
    ])
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "deepseek", "apiKey": "sk-deepseek-key", "strictKnowledge": True,
         "test": False},
        owner,
    )
    question = web_state.ask_global_ai("Do you have it in red?", owner)["question"]
    assert asked == [] and question["status"] == "unanswered"
    assert all(source["scope"] != "global" for source in question["sources"])
    answered = web_state.ask_global_ai("Can I pay by credit card?", owner)["question"]
    assert len(asked) == 1 and "credit cards" in asked[0]["context"]
    assert [(source["name"], source["scope"]) for source in answered["sources"]] == [
        ("payment", "global")
    ]


def test_every_workspace_answers_with_the_gods_knowledge_after_its_own(web_state, monkeypatch):
    asked = []

    class _RecordingClient(_FakeAiClient):
        def chat(self, **kwargs):
            asked.append({"provider": self.provider_id, **kwargs})
            return {"answer": "ตอบแล้ว", "canAnswer": True, "confidence": 0.8, "model": kwargs["model"]}

    _fake_clients(monkeypatch, _RecordingClient)
    _set_god_chunks(web_state, _god_chunks())
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "deepseek", "apiKey": "sk-deepseek-key", "test": False}, owner
    )
    web_state.save_assistant_knowledge(
        {"sections": [{"id": "pay", "title": "การชำระเงินของร้าน",
                       "body": "ร้านรับโอนผ่านธนาคารกสิกรไทย และผ่อนชำระผ่านบัตรได้"}]},
        owner,
    )
    answered = web_state.ask_global_ai("มีบริการผ่อนชำระไหม", owner)["question"]
    context = asked[-1]["context"]
    assert context.index("ธนาคารกสิกรไทย") < context.index("ผ่อนชำระ 0% ได้นานสูงสุด 10 เดือน")
    assert "SOURCE: payment (shared information)" in context
    assert "ส่งด่วน" not in context  # the express delivery text is not about this
    assert [(source["name"], source["scope"]) for source in answered["sources"]] == [
        ("การชำระเงินของร้าน", "shop"), ("payment", "global"),
    ]
    unrelated = web_state.ask_global_ai("มีสีแดงไหม", owner)["question"]
    assert "(shared information)" not in asked[-1]["context"]
    assert all(source["scope"] == "shop" for source in unrelated["sources"])
    # Another workspace, on another provider and with no shop info, gets it too.
    other = web_state.web_auth._find_user(
        web_state.web_auth.create_user("other@example.com", "other-pass", "member", [])["id"]
    )
    web_state.save_assistant_settings(
        {"provider": "mistral", "apiKey": "mistral-key-0001", "test": False}, other
    )
    answered_other = web_state.ask_global_ai("ผ่อนได้ไหม", other)["question"]
    assert asked[-1]["provider"] == "mistral"
    assert "ผ่อนชำระ 0%" in asked[-1]["context"] and "ธนาคารกสิกรไทย" not in asked[-1]["context"]
    assert [(source["name"], source["scope"]) for source in answered_other["sources"]] == [
        ("payment", "global"),
    ]


def test_global_knowledge_is_capped_at_three_chunks_and_the_top_k(web_state, monkeypatch):
    _fake_clients(monkeypatch)
    _set_god_chunks(web_state, [
        {"id": f"god-kerry-{index}", "sourceName": f"kerry-{index}", "text": f"ส่ง Kerry รอบที่ {index}"}
        for index in range(5)
    ])
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "deepseek", "apiKey": "sk-deepseek-key", "test": False}, owner
    )
    sources = web_state.ask_global_ai("Kerry ส่งไหม", owner)["question"]["sources"]
    assert [source["name"] for source in sources] == ["kerry-0", "kerry-1", "kerry-2"]
    web_state.save_assistant_settings({"provider": "deepseek", "topK": 2, "test": False}, owner)
    sources = web_state.ask_global_ai("Kerry ส่งไหม", owner)["question"]["sources"]
    assert [source["name"] for source in sources] == ["kerry-0", "kerry-1"]


def test_strict_knowledge_still_answers_from_related_global_knowledge(web_state, monkeypatch):
    asked = []

    class _RecordingClient(_FakeAiClient):
        def chat(self, **kwargs):
            asked.append(kwargs)
            return {"answer": "ผ่อนได้", "canAnswer": True, "confidence": 0.8, "model": kwargs["model"]}

    _fake_clients(monkeypatch, _RecordingClient)
    _set_god_chunks(web_state, _god_chunks())
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "deepseek", "apiKey": "sk-deepseek-key", "strictKnowledge": True,
         "test": False},
        owner,
    )
    answered = web_state.ask_global_ai("ผ่อนได้ไหม", owner)["question"]
    assert answered["status"] == "answered" and "ผ่อนชำระ 0%" in asked[-1]["context"]
    # Nothing about the question anywhere: no AI call, the question waits for an answer.
    unanswered = web_state.ask_global_ai("มีสีแดงไหม", owner)["question"]
    assert unanswered["status"] == "unanswered" and len(asked) == 1


def test_own_chunks_use_vectors_only_from_the_model_that_made_them(web_state, monkeypatch):
    embedded = []

    class _VectorClient(_FakeAiClient):
        def embed(self, model, texts, timeout=None):
            embedded.append((self.provider_id, model, list(texts)))
            return [[1.0, 0.0] if "เปิด" in text else [0.0, 1.0] for text in texts]

        def chat(self, **kwargs):
            return {"answer": "ok", "canAnswer": True, "confidence": 0.9, "model": kwargs["model"]}

    _fake_clients(monkeypatch, _VectorClient)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-openai-key", "test": False}, owner
    )
    web_state.save_assistant_knowledge({"sections": [
        {"id": "hours", "title": "เวลาเปิด", "body": "ร้านเปิด 9 โมงถึง 6 โมงเย็น"},
        {"id": "pay", "title": "การจ่ายเงิน", "body": "รับโอนและบัตรเครดิต"},
    ]}, owner)
    tenant = web_state._global_ai_all()["tenants"][owner["id"]]
    assert (tenant["indexMode"], tenant["embeddingProvider"], tenant["embeddingModel"]) == (
        "embedding", "openai", "text-embedding-3-small"
    )

    def ask():
        embedded.clear()
        return web_state.ask_global_ai("ร้านเปิดกี่โมง", owner)["question"]["sources"]

    def change(**fields):
        data = web_state._global_ai_all()
        data["tenants"][owner["id"]].update(fields)
        web_state.store.set("global_ai", data)

    assert ask() == [{"name": "เวลาเปิด", "score": 1.0, "scope": "shop"}]  # by vector
    assert embedded == [("openai", "text-embedding-3-small", ["ร้านเปิดกี่โมง"])]
    # An index made by another provider or model is not compared: keyword search.
    for fields in ({"embeddingProvider": "mistral"},
                   {"embeddingProvider": "openai", "embeddingModel": "text-embedding-3-large"}):
        change(**fields)
        sources = ask()
        assert embedded == [] and sources[0]["name"] == "เวลาเปิด" and sources[0]["score"] < 1.0
    # One chunk without its vector: keyword search too.
    change(embeddingModel="text-embedding-3-small")
    data = web_state._global_ai_all()
    data["tenants"][owner["id"]]["chunks"][1].pop("embedding")
    web_state.store.set("global_ai", data)
    assert ask()[0]["score"] < 1.0 and embedded == []
    web_state.reindex_assistant_knowledge(owner)
    assert ask()[0]["score"] == 1.0
    # A question vector of another size, or none at all, falls back to keywords.

    class _OtherSize(_VectorClient):
        def embed(self, model, texts, timeout=None):
            embedded.append((self.provider_id, model, list(texts)))
            return [[1.0, 0.0, 0.0] for _ in texts]

    class _Down(_VectorClient):
        def embed(self, model, texts, timeout=None):
            raise webapp_module.AssistantError("OpenAI error 503: overloaded", 503)

    for client in (_OtherSize, _Down):
        _fake_clients(monkeypatch, client)
        sources = ask()
        assert sources[0]["name"] == "เวลาเปิด" and sources[0]["score"] < 1.0


@pytest.mark.parametrize("provider_id", ["anthropic", "zai", "deepseek", "xai", "moonshot"])
def test_providers_without_embeddings_index_for_keyword_search_without_the_ai(
    web_state, monkeypatch, provider_id
):
    assert webapp_module.get_provider(provider_id).embeddings is False
    monkeypatch.setattr(webapp_module, "provider_available", lambda provider_id: (True, ""))
    calls = _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": provider_id, "apiKey": "key-for-the-test-1234", "embeddingModel": "some-embed",
         "test": False},
        owner,
    )
    calls.clear()
    knowledge = web_state.save_assistant_knowledge(
        {"sections": [{"id": "hours", "title": "เวลาเปิด", "body": "ร้านเปิด 9 โมง"}]}, owner
    )
    assert calls == []  # no client is even built
    assert (knowledge["indexMode"], knowledge["indexError"], knowledge["chunkCount"]) == (
        "keyword", "", 1
    )
    tenant = web_state._global_ai_all()["tenants"][owner["id"]]
    assert (tenant["embeddingProvider"], tenant["embeddingModel"]) == ("", "")
    assert all("embedding" not in chunk for chunk in tenant["chunks"])


def test_reindexing_embeds_in_the_providers_batches_and_records_the_model(web_state, monkeypatch):
    batches = []

    class _BatchClient(_FakeAiClient):
        def embed(self, model, texts, timeout=None):
            batches.append((self.provider_id, model, len(texts)))
            return [[1.0, float(len(text))] for text in texts]

    _fake_clients(monkeypatch, _BatchClient)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "gemini", "apiKey": "AIza-batch-key", "test": False}, owner
    )
    web_state.save_assistant_knowledge({"sections": [
        {"id": f"t{index}", "title": f"หัวข้อ {index}", "body": f"ข้อมูลร้านข้อที่ {index}"}
        for index in range(40)
    ]}, owner)
    for provider_id, key, model, sizes in (
        ("gemini", "", "gemini-embedding-001", [16, 16, 8]),
        ("qwen", "sk-qwen-batch-key", "text-embedding-v4", [10, 10, 10, 10]),
        ("openai", "sk-openai-batch-key", "text-embedding-3-small", [32, 8]),
    ):
        if key:
            # Switching provider drops the old vectors and builds the index again.
            web_state.save_assistant_settings(
                {"provider": provider_id, "apiKey": key, "test": False}, owner
            )
        assert batches == [(provider_id, model, size) for size in sizes]
        tenant = web_state._global_ai_all()["tenants"][owner["id"]]
        assert (tenant["indexMode"], tenant["embeddingProvider"], tenant["embeddingModel"]) == (
            "embedding", provider_id, model
        )
        assert len(tenant["chunks"]) == 40
        assert all(len(chunk["embedding"]) == 2 for chunk in tenant["chunks"])
        batches.clear()


def test_a_failed_search_index_falls_back_to_keywords_with_the_key_hidden(web_state, monkeypatch):
    outcomes = []

    class _FlakyClient(_FakeAiClient):
        def embed(self, model, texts, timeout=None):
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome(texts)

    _fake_clients(monkeypatch, _FlakyClient)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-embed-secret-0001", "test": False}, owner
    )
    sections = [{"id": f"t{index}", "title": f"หัวข้อ {index}", "body": "ร้านเปิด 9 โมง"}
                for index in range(40)]

    def vectors(texts):
        return [[1.0, 0.0] for _ in texts]

    # The second batch fails: no vector from the first one is kept.
    outcomes[:] = [vectors, webapp_module.AssistantError(
        "OpenAI error 401: Incorrect API key provided: sk-embed-secret-0001. "
        "See https://platform.openai.com/account/api-keys?ref=x1",
        401,
        "ai_key_invalid",
    )]
    knowledge = web_state.save_assistant_knowledge({"sections": sections}, owner)
    assert knowledge["indexMode"] == "keyword"
    assert "Incorrect API key" in knowledge["indexError"] and "[API key]" in knowledge["indexError"]
    assert "sk-embed-secret-0001" not in knowledge["indexError"]
    assert "ref=x1" not in knowledge["indexError"]
    tenant = web_state._global_ai_all()["tenants"][owner["id"]]
    assert all("embedding" not in chunk for chunk in tenant["chunks"])
    assert (tenant["embeddingProvider"], tenant["embeddingModel"]) == ("", "")
    # A wrong number of vectors, or any other failure, is a fallback too.
    outcomes[:] = [lambda texts: [[1.0, 0.0]]]
    knowledge = web_state.reindex_assistant_knowledge(owner)
    assert knowledge["indexMode"] == "keyword"
    assert "returned 1 search vectors for 32 texts" in knowledge["indexError"]
    outcomes[:] = [vectors, lambda texts: [[1.0, 0.0, 0.0] for _ in texts]]
    assert "different sizes" in web_state.reindex_assistant_knowledge(owner)["indexError"]
    outcomes[:] = [ValueError("could not convert 'NaN'")]
    knowledge = web_state.reindex_assistant_knowledge(owner)
    assert knowledge["indexMode"] == "keyword" and "ValueError" in knowledge["indexError"]
    # It works again once the provider does; the questions were answered meanwhile.
    outcomes[:] = [vectors, vectors]
    knowledge = web_state.reindex_assistant_knowledge(owner)
    assert (knowledge["indexMode"], knowledge["indexError"]) == ("embedding", "")


def test_a_question_or_shop_info_saved_while_the_index_is_built_is_kept(web_state, monkeypatch):
    while_embedding = []

    class _SlowClient(_FakeAiClient):
        def embed(self, model, texts, timeout=None):
            while while_embedding:
                while_embedding.pop()()
            return super().embed(model, texts)

    _fake_clients(monkeypatch, _SlowClient)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "gemini", "apiKey": "AIza-slow-key", "test": False}, owner
    )

    def another_request():
        data = web_state._global_ai_all()
        data["questions"].append({"id": "asked-meanwhile", "userId": owner["id"], "question": "q",
                                  "status": "unanswered", "createdAt": "2026-11-21T10:00:00"})
        data["tenants"][owner["id"]]["knowledgeSections"] = [
            {"id": "hours", "title": "เวลาเปิด", "body": "ร้านเปิด 10 โมง"}
        ]
        web_state.store.set("global_ai", data)

    while_embedding.append(another_request)
    web_state.save_assistant_knowledge(
        {"sections": [{"id": "hours", "title": "เวลาเปิด", "body": "ร้านเปิด 9 โมง"}]}, owner
    )
    data = web_state._global_ai_all()
    assert "asked-meanwhile" in [question["id"] for question in data["questions"]]
    tenant = data["tenants"][owner["id"]]
    assert [chunk["text"] for chunk in tenant["chunks"]] == ["## เวลาเปิด\n\nร้านเปิด 10 โมง"]
    # The vectors were for the text before: keyword search until the next build.
    assert tenant["indexMode"] == "keyword"
    assert all("embedding" not in chunk for chunk in tenant["chunks"])
    assert web_state.reindex_assistant_knowledge(owner)["indexMode"] == "embedding"


def test_oversized_search_vectors_fall_back_to_keyword_search(web_state, monkeypatch):
    sizes = {"dimensions": 10_000}

    class _BigVectors(_FakeAiClient):
        def embed(self, model, texts, timeout=None):
            return [[0.5] * sizes["dimensions"] for _ in texts]

    _fake_clients(monkeypatch, _BigVectors)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-openai-key", "test": False}, owner
    )
    sections = {"sections": [
        {"id": f"t{index}", "title": f"หัวข้อ {index}", "body": f"ข้อมูลร้านข้อที่ {index}"}
        for index in range(3)
    ]}
    knowledge = web_state.save_assistant_knowledge(sections, owner)
    assert knowledge["indexMode"] == "keyword" and knowledge["chunkCount"] == 3
    assert "10000 values" in knowledge["indexError"]
    tenant = web_state._global_ai_all()["tenants"][owner["id"]]
    assert all("embedding" not in chunk for chunk in tenant["chunks"])
    assert len(json.dumps(web_state.store.get("global_ai", {}))) < 1_000_000
    # The largest size allowed is kept: the same limit the AI clients apply.
    largest = assistant_module.MAX_EMBEDDING_DIMENSIONS
    assert webapp_module.MAX_EMBEDDING_DIMENSIONS == largest == 8192
    assert webapp_module._vector_size_problem(largest, 1) == ""
    assert "too long" in webapp_module._vector_size_problem(largest + 1, 1)
    sizes["dimensions"] = largest
    assert web_state.reindex_assistant_knowledge(owner)["indexMode"] == "embedding"
    # One workspace's vectors stay within the budget: past it, keyword search.
    sizes["dimensions"] = 4
    monkeypatch.setattr(webapp_module, "MAX_TENANT_VECTOR_VALUES", 11)  # 3 chunks x 4 = 12
    over = web_state.reindex_assistant_knowledge(owner)
    assert over["indexMode"] == "keyword" and "too large" in over["indexError"]
    monkeypatch.setattr(webapp_module, "MAX_TENANT_VECTOR_VALUES", 12)
    assert web_state.reindex_assistant_knowledge(owner)["indexMode"] == "embedding"
    # All workspaces together stay within the server's budget too.
    assert webapp_module.MAX_TENANT_VECTOR_VALUES <= 12 and (
        webapp_module.MAX_TOTAL_VECTOR_VALUES == 4_000_000
    )
    data = web_state._global_ai_all()
    data["tenants"]["someone-else"] = {
        "settings": {}, "apiSources": [], "manualAnswers": [],
        "chunks": [{"id": "x", "text": "x", "embedding": [0.1] * 10}],
    }
    web_state.store.set("global_ai", data)
    monkeypatch.setattr(webapp_module, "MAX_TOTAL_VECTOR_VALUES", 21)  # 10 + 12 = 22
    full = web_state.reindex_assistant_knowledge(owner)
    assert full["indexMode"] == "keyword" and "no room" in full["indexError"]
    monkeypatch.setattr(webapp_module, "MAX_TOTAL_VECTOR_VALUES", 22)
    assert web_state.reindex_assistant_knowledge(owner)["indexMode"] == "embedding"


def test_a_huge_ai_answer_and_model_name_are_cut_before_they_are_kept(web_state, monkeypatch):
    class _Verbose(_FakeAiClient):
        def chat(self, **kwargs):
            return {"answer": "ตอบ" * 40_000, "canAnswer": True, "confidence": 0.9,
                    "model": "m" * 50_000}

    _fake_clients(monkeypatch, _Verbose)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "deepseek", "apiKey": "sk-deepseek-key", "test": False}, owner
    )
    asked = web_state.ask_global_ai("สวัสดีครับ", owner)["question"]
    # LINE's longest text message; answers people type may be longer.
    assert len(asked["answer"]) == webapp_module.MAX_AI_ANSWER_LENGTH == 5000
    assert webapp_module.MAX_ASSISTANT_ANSWER_LENGTH == 20_000
    assert len(asked["model"]) == webapp_module.MAX_AI_MODEL_NAME_LENGTH == 200
    stored = web_state._global_ai_all()["questions"][-1]
    assert (len(stored["answer"]), len(stored["model"])) == (5000, 200)
    assert len(web_state.test_assistant_settings(owner)["model"]) == 200


def test_one_workspace_keeps_at_most_500_questions_in_the_shared_log(web_state, monkeypatch):
    _fake_clients(monkeypatch)
    monkeypatch.setattr(webapp_module, "MAX_ASSISTANT_QUESTIONS_PER_WORKSPACE", 3)
    monkeypatch.setattr(webapp_module, "MAX_ASSISTANT_QUESTIONS", 6)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-openai-key", "test": False}, owner
    )
    # Two questions of another workspace, one logged before the log knew workspaces.
    data = web_state._global_ai_all()
    data["questions"] = [
        {"id": "other-old", "userId": "someone-else", "question": "q", "status": "unanswered"},
        {"id": "other-new", "userId": "someone-else", "workspaceId": "someone-else",
         "question": "q", "status": "unanswered"},
    ]
    web_state.store.set("global_ai", data)
    for index in range(5):
        web_state.ask_global_ai(f"question {index}", owner)
    questions = web_state._global_ai_all()["questions"]
    # The workspace's own oldest went; the other workspace's questions stay.
    assert [item["id"] for item in questions[:2]] == ["other-old", "other-new"]
    assert [item["question"] for item in questions[2:]] == [
        "question 2", "question 3", "question 4"
    ]
    assert all(item["workspaceId"] == owner["id"] for item in questions[2:])
    assert webapp_module.MAX_ASSISTANT_QUESTIONS_PER_WORKSPACE == 3  # 500 unpatched
    assert web_state._question_workspace_id(questions[-1]) == owner["id"]


def test_a_full_question_log_loses_the_oldest_question_of_its_biggest_holder(monkeypatch):
    monkeypatch.setattr(webapp_module, "MAX_ASSISTANT_QUESTIONS_PER_WORKSPACE", 4)
    monkeypatch.setattr(webapp_module, "MAX_ASSISTANT_QUESTIONS", 6)
    trim = webapp_module._trim_question_log
    log = [{"id": f"a{index}", "workspaceId": "a"} for index in range(4)]
    log += [{"id": "b0", "workspaceId": "b"}, {"id": "c0", "userId": "c"}]  # c: legacy
    # A newcomer's question within its own cap pushes out the oldest of the biggest
    # holder, not the oldest of the whole log (b0 and c0 stay).
    log = trim([*log, {"id": "n0", "workspaceId": "n"}], "n")
    assert [item["id"] for item in log] == ["a1", "a2", "a3", "b0", "c0", "n0"]
    log = trim([*log, {"id": "n1", "workspaceId": "n"}], "n")
    assert [item["id"] for item in log] == ["a2", "a3", "b0", "c0", "n0", "n1"]
    # On a tie the holder with the oldest question loses it.
    log = trim([*log, {"id": "b1", "workspaceId": "b"}], "b")
    assert [item["id"] for item in log] == ["a3", "b0", "c0", "n0", "n1", "b1"]


def test_deleting_an_owner_deletes_its_workspaces_logged_questions(web_state, monkeypatch):
    _fake_clients(monkeypatch)
    owner = _owner(web_state)
    token = web_state.web_auth.register("member@example.com", "member-pass", "Member")
    member = web_state.web_auth.current_user(WebAuth.cookie_header(token))
    data = web_state._global_ai_all()
    data["questions"] = [
        {"id": "own", "userId": member["id"], "workspaceId": member["id"]},
        {"id": "staff", "userId": "staff-of-member", "workspaceId": member["id"]},
        {"id": "other", "userId": owner["id"], "workspaceId": owner["id"]},
    ]
    web_state.store.set("global_ai", data)
    web_state.delete_user({"id": member["id"]}, owner)
    assert [item["id"] for item in web_state._global_ai_all()["questions"]] == ["other"]


class _FakeSourceResponse:
    def __init__(self, body: bytes):
        self.status_code = 200
        self.ok = True
        self.headers = {"content-type": "text/plain; charset=utf-8"}
        self.encoding = "utf-8"
        self._body = body

    def iter_content(self, chunk_size=65536):
        yield self._body

    def close(self):
        return None


def _fetch_sources_with(monkeypatch, meanwhile, *, fail=None):
    """Knowledge API fetches run ``meanwhile`` (what other requests save), then answer."""

    def fake_open(method, url, *, timeout, headers=None, **kwargs):
        meanwhile()
        if fail is not None:
            raise fail
        return _FakeSourceResponse("ราคาส่งใหม่ 40 บาท".encode()), url

    monkeypatch.setattr(webapp_module, "_open_public_response", fake_open)


def test_a_saved_source_key_stays_with_the_address_it_was_saved_for(web_state, monkeypatch):
    sent = []

    def fake_open(method, url, *, timeout, headers=None, **kwargs):
        sent.append((url, dict(headers or {}).get("Authorization", "")))
        return _FakeSourceResponse(b"hello"), url

    monkeypatch.setattr(webapp_module, "_open_public_response", fake_open)
    owner = _owner(web_state)
    staff_public = web_state.web_auth.create_staff(
        owner, "staff@example.com", "staff-pass-123", "Staff", []
    )
    staff = web_state.web_auth._find_user(staff_public["id"])
    workspace = web_state.tenant_user(staff)  # what the routes act on for staff
    web_state.save_assistant_api_source(
        {"name": "Catalog", "url": "https://shop-api.example.com/items",
         "apiKey": "owner-secret-token-abc"},
        web_state.tenant_user(owner),
    )
    source_id = web_state.assistant_knowledge(workspace)["apiSources"][0]["id"]
    # Another path at the same address keeps the key.
    kept = web_state.save_assistant_api_source(
        {"id": source_id, "name": "Catalog", "url": "https://shop-api.example.com/v2/items"},
        workspace,
    )
    assert kept["keyCleared"] is False
    web_state.sync_assistant_api_source(source_id, workspace)
    assert sent[-1] == ("https://shop-api.example.com/v2/items", "Bearer owner-secret-token-abc")
    # Another address does not: the key would go to whoever runs it. The page is
    # told, so it can say the key has to be typed again.
    moved = web_state.save_assistant_api_source(
        {"id": source_id, "name": "Catalog", "url": "https://collector.example.net/x"},
        workspace,
    )
    assert moved["keyCleared"] is True
    assert moved["apiSources"][0]["hasApiKey"] is False
    web_state.sync_assistant_api_source(source_id, workspace)
    assert sent[-1] == ("https://collector.example.net/x", "")
    # Not even when it comes back to the first address: the key has to be typed again.
    web_state.save_assistant_api_source(
        {"id": source_id, "name": "Catalog", "url": "https://shop-api.example.com/items"},
        workspace,
    )
    web_state.sync_assistant_api_source(source_id, workspace)
    assert sent[-1] == ("https://shop-api.example.com/items", "")
    # The God's sources follow the same rule.
    web_state.save_global_ai_api_source(
        {"name": "Rules", "url": "https://god-api.example.com/rules", "apiKey": "god-token-xyz"}
    )
    god_source = web_state._global_ai_all()["apiSources"][0]
    god_moved = web_state.save_global_ai_api_source(
        {"id": god_source["id"], "name": "Rules", "url": "https://elsewhere.example.org/rules"}
    )
    assert god_moved["keyCleared"] is True
    stored = web_state._global_ai_all()["apiSources"][0]
    assert web_state._decrypt_secret(str(stored.get("apiKey") or "")) == ""
    page = webapp_module.INDEX_HTML
    assert 'data.keyCleared ? "assistant.api_saved_key_cleared"' in page
    assert "data.keyCleared" in webapp_module.GOD_HTML


def test_a_redirect_to_another_address_drops_the_credentials(monkeypatch):
    calls = []
    replies = iter([
        (302, "https://shop-api.example.com/next"),  # same address: credentials stay
        (302, "https://cdn.example.net/file"),  # another address: they go
        (200, ""),
    ])

    class _Reply:
        def __init__(self, status, location):
            self.status_code = status
            self.headers = {"location": location} if location else {}

        def close(self):
            return None

    def fake_request(method, url, *, timeout, headers, json_data, data, params):
        calls.append((url, dict(headers)))
        return _Reply(*next(replies))

    monkeypatch.setattr(webapp_module, "_validate_public_http_url", lambda url: url)
    monkeypatch.setattr(webapp_module, "_request_public_address", fake_request)
    _response, final = webapp_module._open_public_response(
        "GET", "https://shop-api.example.com/items", timeout=5,
        headers={"Authorization": "Bearer secret", "cookie": "a=b", "Accept": "text/plain"},
    )
    assert final == "https://cdn.example.net/file"
    assert [url for url, _headers in calls] == [
        "https://shop-api.example.com/items",
        "https://shop-api.example.com/next",
        "https://cdn.example.net/file",
    ]
    assert calls[1][1]["Authorization"] == "Bearer secret" and calls[1][1]["cookie"] == "a=b"
    assert calls[2][1] == {"Accept": "text/plain"}
    # An upgrade from http to https on the same host keeps them, as requests does.
    assert webapp_module._https_upgrade("http://shop.example.com/a", "https://shop.example.com/b")
    assert not webapp_module._https_upgrade("http://shop.example.com/a", "https://cdn.example.com/b")
    assert not webapp_module._https_upgrade("http://shop.example.com:8080/", "https://shop.example.com/")
    assert not webapp_module._https_upgrade("https://shop.example.com/", "http://shop.example.com/")


def test_a_shop_source_sync_keeps_what_was_saved_while_it_fetched(web_state, monkeypatch):
    _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-openai-old-key", "test": False}, owner
    )
    [source] = web_state.save_assistant_api_source(
        {"name": "Prices", "url": "https://prices.example.com/list"}, owner
    )["apiSources"]

    def meanwhile():
        data = web_state._global_ai_all()
        data["questions"].append({"id": "asked-meanwhile", "userId": owner["id"], "question": "q",
                                  "status": "unanswered", "createdAt": "2026-11-21T10:00:00"})
        web_state.store.set("global_ai", data)
        web_state.save_assistant_settings(
            {"provider": "openai", "apiKey": "sk-openai-new-key", "test": False}, owner
        )

    _fetch_sources_with(monkeypatch, meanwhile)
    synced = web_state.sync_assistant_api_source(source["id"], owner)
    assert synced["apiSources"][0]["lastError"] == "" and synced["apiSources"][0]["lastSyncAt"]
    data = web_state._global_ai_all()
    assert "asked-meanwhile" in [question["id"] for question in data["questions"]]
    assert web_state._assistant_settings_for(data, owner)["apiKey"] == "sk-openai-new-key"
    tenant = data["tenants"][owner["id"]]
    assert tenant["apiSources"][0]["content"] == "ราคาส่งใหม่ 40 บาท"
    assert any("ราคาส่งใหม่" in chunk["text"] for chunk in tenant["chunks"])

    # A failed fetch keeps them too, and records why it failed.
    def meanwhile_again():
        web_state.save_assistant_settings(
            {"provider": "openai", "apiKey": "sk-openai-third-key", "test": False}, owner
        )

    _fetch_sources_with(
        monkeypatch, meanwhile_again,
        fail=WebError(502, "Knowledge API returned HTTP 503.", "knowledge_api_error"),
    )
    with pytest.raises(WebError):
        web_state.sync_assistant_api_source(source["id"], owner)
    data = web_state._global_ai_all()
    assert web_state._assistant_settings_for(data, owner)["apiKey"] == "sk-openai-third-key"
    failed = data["tenants"][owner["id"]]["apiSources"][0]
    assert "HTTP 503" in failed["lastError"] and failed["content"] == "ราคาส่งใหม่ 40 บาท"

    # A source deleted while it was fetched stays deleted.
    _fetch_sources_with(
        monkeypatch, lambda: web_state.delete_assistant_api_source(source["id"], owner)
    )
    with pytest.raises(WebError) as gone:
        web_state.sync_assistant_api_source(source["id"], owner)
    assert gone.value.status == 404
    assert web_state._global_ai_all()["tenants"][owner["id"]]["apiSources"] == []


def test_a_god_source_sync_keeps_a_workspace_key_saved_while_it_fetched(web_state, monkeypatch):
    _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-openai-old-key", "test": False}, owner
    )
    [source] = web_state.save_global_ai_api_source(
        {"name": "Shipping", "url": "https://god.example.com/shipping"}
    )["apiSources"]
    _fetch_sources_with(
        monkeypatch,
        lambda: web_state.save_assistant_settings(
            {"provider": "openai", "apiKey": "sk-openai-new-key", "test": False}, owner
        ),
    )
    synced = web_state.sync_global_ai_api_source(source["id"])
    assert synced["indexMode"] == "keyword" and synced["chunkCount"] == 1
    data = web_state._global_ai_all()
    assert web_state._assistant_settings_for(data, owner)["apiKey"] == "sk-openai-new-key"
    assert data["apiSources"][0]["content"] == "ราคาส่งใหม่ 40 บาท"
    assert "ราคาส่งใหม่" in data["chunks"][0]["text"]


def test_a_gemini_auto_reply_key_fills_the_image_slot_without_switching_the_image_tool(
    web_state, monkeypatch
):
    _fake_clients(monkeypatch)
    owner = _seed_owned_account(web_state, _owner(web_state))
    web_state.save_ai_settings({"provider": "fal", "apiKey": "fal-key-123456789"}, owner)
    web_state.save_assistant_settings(
        {"provider": "gemini", "apiKey": "AIza-shared-gemini-key", "test": False}, owner
    )
    image = web_state.ai_settings(owner)
    assert image["provider"] == "fal"  # still the provider the owner chose
    assert image["providers"]["google"]["hasApiKey"] is True
    assert image["providers"]["fal"]["hasApiKey"] is True
    google = web_state._ai_provider_view(web_state._ai_all(owner), "google")
    assert google["apiKey"] == "AIza-shared-gemini-key"
    # A Google slot already filled is left alone.
    web_state.save_assistant_settings(
        {"provider": "gemini", "apiKey": "AIza-another-gemini-key", "test": False}, owner
    )
    google = web_state._ai_provider_view(web_state._ai_all(owner), "google")
    assert google["apiKey"] == "AIza-shared-gemini-key"
    assert web_state.ai_settings(owner)["provider"] == "fal"


def test_a_short_saved_key_shows_nothing_and_staff_see_no_key_or_address(web_state):
    mask = webapp_module._mask_secret
    assert [mask(value) for value in ("", "x", "X2bQ9z", "k" * 15)] == ["", "••••", "••••", "••••"]
    assert mask("k" * 12 + "WXYZ") == "••••WXYZ"  # 16 characters: the last 4 are shown
    assert mask("sk-" + "s" * 33 + "TAIL") == "••••TAIL"
    owner = _seed_owned_account(web_state, _owner(web_state))
    _seed_tenant_settings(web_state, owner, {
        "provider": "custom", "enabled": True, "providers": {"custom": {
            "apiKey": web_state._encrypt_secret("X2bQ9z"), "chatModel": "my-model",
            "baseUrl": "https://llm.example.com/v1",
        }},
    })
    mine = web_state.assistant_settings(owner)
    custom = next(item for item in mine["providers"] if item["id"] == "custom")
    assert (mine["canEdit"], mine["apiKeyPreview"], mine["baseUrl"]) == (
        True, "••••", "https://llm.example.com/v1"
    )
    assert (custom["apiKeyPreview"], custom["baseUrl"]) == ("••••", "https://llm.example.com/v1")
    staff = web_state.web_auth._find_user(
        web_state.web_auth.create_staff(owner, "s@example.com", "staff-pass", "S", ["acct"])["id"]
    )
    theirs = web_state.assistant_settings(staff)
    assert (theirs["canEdit"], theirs["hasApiKey"], theirs["apiKeyPreview"], theirs["baseUrl"]) == (
        False, True, "", ""
    )
    assert all(item["apiKeyPreview"] == "" and item["baseUrl"] == "" for item in theirs["providers"])
    custom = next(item for item in theirs["providers"] if item["id"] == "custom")
    assert (custom["hasApiKey"], custom["chatModel"]) == (True, "my-model")
    assert "X2bQ" not in json.dumps(theirs, ensure_ascii=False)
    assert "X2bQ" not in json.dumps(mine, ensure_ascii=False)


def test_a_readiness_answer_read_while_the_settings_change_is_not_kept(web_state, monkeypatch):
    answers = iter([False, True])

    def read(owner):
        value = next(answers)
        if value is False:
            web_state._forget_ai_readiness()  # a save lands while this read runs
        return value

    monkeypatch.setattr(web_state, "_auto_reply_ai_ready", read)
    monkeypatch.setattr(webapp_module, "AI_READY_CACHE_SECONDS", 60.0)
    owner = {"id": "owner-x"}
    assert web_state._auto_reply_ai_ready_cached(owner) is False
    assert web_state._auto_reply_ai_ready_cached(owner) is True  # read again
    assert web_state._auto_reply_ai_ready_cached(owner) is True  # kept: no third read
    assert web_state._auto_reply_ai_ready_cached(None) is False


def test_the_image_tools_google_key_makes_a_keyless_gemini_ready_at_once(web_state, monkeypatch):
    monkeypatch.setattr(webapp_module, "AI_READY_CACHE_SECONDS", 60.0)
    owner = _seed_owned_account(web_state, _owner(web_state))
    _seed_tenant_settings(web_state, owner, {
        "provider": "gemini", "enabled": True,
        "providers": {"gemini": {"apiKey": "", "chatModel": ""}},
    })
    assert web_state._auto_reply_ai_ready_cached(owner) is False
    web_state.save_ai_settings({"provider": "google", "apiKey": "AIza-image-key-0001"}, owner)
    assert web_state._auto_reply_ai_ready_cached(owner) is True


# --- The one-time move to per-workspace AI providers (startup migration) ------

_OLD_TIME = "2026-10-01T10:00:00"


def _seed_legacy_install(config, *, shared_ai_on=True, shared_settings=None):
    """A state directory as an install from before per-workspace providers left it.

    Workspaces: on Ollama, without AI settings (the shared AI), on Gemini (an old
    flat record with the Ollama-era baseUrl), with auto reply only, and idle.
    ``shared_settings`` adds to the shared AI's settings.
    """
    state = WebState(config)
    try:
        state.web_auth.setup("ollama@example.com", "owner-pass")
        owners = {"ollama": state.web_auth.data["users"][0]["id"]}
        for name in ("shared", "gemini", "quiet", "idle"):
            owners[name] = state.web_auth.create_user(
                f"{name}@example.com", "owner-pass", "member", []
            )["id"]
        state.account_store.data["accounts"] = [
            {"id": f"acct-{name}", "label": name, "ownerId": user_id, "tokenFile": "",
             "createdAt": _OLD_TIME, "updatedAt": _OLD_TIME}
            for name, user_id in owners.items()
        ]
        state.account_store.save()
        for name, user_id in owners.items():
            state.web_auth.grant_account_access(user_id, f"acct-{name}")

        def chunks(prefix):
            return [{"id": f"{prefix}-{index}", "sourceId": "knowledge-md",
                     "sourceName": "knowledge.md", "text": f"{prefix} text {index}",
                     "embedding": [0.1, 0.2, 0.3]} for index in range(2)]

        legacy = {
            "settings": {"enabled": shared_ai_on, "baseUrl": "http://127.0.0.1:11434",
                         "apiKey": state._encrypt_secret("ollama-secret-key"), "chatModel": "llama3",
                         "embeddingModel": "bge-m3", "systemPrompt": "God prompt", "topK": 5,
                         **(shared_settings or {})},
            "knowledgeMarkdown": "# ส่งของ\n\nส่งฟรีทั่วประเทศเมื่อซื้อครบ 500 บาท",
            "apiSources": [{"id": "src1", "name": "Promotions", "url": "https://example.com/p.json",
                            "apiKey": state._encrypt_secret("source-key-0001"),
                            "content": "โปรโมชั่นเดือนนี้", "enabled": True}],
            "manualAnswers": [{"id": "ans1", "questionId": "q0", "question": "ส่งต่างประเทศไหม",
                               "answer": "ยังไม่ส่ง"}],
            "chunks": chunks("god"), "embeddingModel": "bge-m3", "indexMode": "embedding",
            "indexError": "",
            "questions": [{"id": "q0", "userId": owners["shared"], "question": "ส่งต่างประเทศไหม",
                           "status": "resolved", "createdAt": _OLD_TIME}],
            "tenants": {
                owners["ollama"]: {
                    "settings": {"provider": "ollama", "enabled": True,
                                 "baseUrl": "http://127.0.0.1:11434", "apiKey": "",
                                 "chatModel": "qwen2.5:7b", "embeddingModel": "bge-m3",
                                 "systemPrompt": "Shop prompt", "strictKnowledge": True, "topK": 3,
                                 "temperature": 0, "timeout": 120, "updatedAt": _OLD_TIME},
                    "knowledgeMarkdown": "ร้านเปิด 9 โมง", "chunks": chunks("ollama"),
                    "embeddingModel": "bge-m3", "indexMode": "embedding",
                    "apiSources": [], "manualAnswers": [],
                },
                owners["shared"]: {
                    "knowledgeMarkdown": "ร้านปิดวันจันทร์", "chunks": chunks("shared"),
                    "embeddingModel": "bge-m3", "indexMode": "embedding",
                    "apiSources": [], "manualAnswers": [],
                },
                owners["gemini"]: {
                    "settings": {"provider": "gemini", "enabled": True,
                                 "baseUrl": "http://127.0.0.1:11434",
                                 "apiKey": state._encrypt_secret("AIza-legacy-gemini"),
                                 "chatModel": "", "embeddingModel": "",
                                 "systemPrompt": "Gemini prompt", "strictKnowledge": False,
                                 "topK": 4, "temperature": 0.3, "timeout": 120,
                                 "updatedAt": _OLD_TIME},
                    "knowledgeMarkdown": "ส่งฟรี", "chunks": chunks("gemini"),
                    "embeddingModel": "gemini-embedding-001", "indexMode": "embedding",
                    "apiSources": [], "manualAnswers": [],
                },
            },
        }
        # Written over what this first start left, the migration flag included.
        state.store.set("global_ai", legacy)
        state.store.set("ai_auto_reply", {"accounts": {
            f"acct-{name}": {"accountId": f"acct-{name}", "ownerId": user_id,
                             "enabled": name != "idle", "contactsEnabled": True,
                             "groupsEnabled": False, "groupReplyMode": "all",
                             "selectedGroupMids": [], "enabledAtMs": 1, "lastSeen": {},
                             "updatedAt": _OLD_TIME}
            for name, user_id in owners.items()
        }})
        return owners, legacy
    finally:
        state.close()


@pytest.fixture
def legacy_config(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # No auto-reply poll while a test looks at what the start wrote.
    monkeypatch.setattr(webapp_module, "AI_AUTO_REPLY_POLL_SECONDS", 3600.0)
    return _make_config(tmp_path)


def _removal_logs(state):
    return [log for log in state.store.get("bot_logs", {"logs": []})["logs"]
            if (log.get("data") or {}).get("disabledReason") == "ai_removed"]


def test_the_startup_migration_gives_every_workspace_its_own_ai_once(legacy_config, monkeypatch):
    owners, legacy = _seed_legacy_install(legacy_config)
    flag = webapp_module.AI_PROVIDERS_MIGRATION_KEY
    state = WebState(legacy_config)  # the first start after the update
    try:
        stored = state.store.get("global_ai", {})
        assert "settings" not in stored  # the shared AI is gone
        assert stored[flag]["sharedAiWasActive"] is True
        # The God's knowledge stays, for keyword search.
        assert stored["knowledgeMarkdown"] == legacy["knowledgeMarkdown"]
        assert stored["manualAnswers"] == legacy["manualAnswers"]
        assert stored["questions"] == legacy["questions"]
        [source] = stored["apiSources"]
        assert (source["name"], state._decrypt_secret(source["apiKey"])) == ("Promotions", "source-key-0001")
        assert [chunk["text"] for chunk in stored["chunks"]] == ["god text 0", "god text 1"]
        assert all("embedding" not in chunk for chunk in stored["chunks"])
        assert (stored["embeddingModel"], stored["indexMode"]) == ("", "keyword")
        tenants = stored["tenants"]
        # Ollama: the prompt settings stay, the provider goes, with a notice.
        ollama = tenants[owners["ollama"]]["settings"]
        assert (ollama["provider"], ollama["aiNotice"]) == ("", "ollama_removed")
        assert {key: ollama[key] for key in ("systemPrompt", "strictKnowledge", "topK", "temperature")} == {
            "systemPrompt": "Shop prompt", "strictKnowledge": True, "topK": 3, "temperature": 0,
        }
        assert not {"baseUrl", "chatModel", "embeddingModel", "apiKey", "enabled"} & set(ollama)
        # No settings while the shared AI was on: a notice; and one for a workspace that
        # only had auto reply on. An idle workspace gets nothing.
        for name in ("shared", "quiet"):
            assert tenants[owners[name]]["settings"]["aiNotice"] == "shared_ai_removed"
        assert owners["idle"] not in tenants
        # Their vectors came from an Ollama model: keyword search now.
        for name in ("ollama", "shared"):
            tenant = tenants[owners[name]]
            assert len(tenant["chunks"]) == 2
            assert all("embedding" not in chunk for chunk in tenant["chunks"])
            assert (tenant["embeddingModel"], tenant["embeddingProvider"], tenant["indexMode"]) == (
                "", "", "keyword"
            )
        # Gemini: the flat record moves into providers.gemini, without the old address.
        gemini = tenants[owners["gemini"]]
        assert gemini["settings"]["provider"] == "gemini" and "aiNotice" not in gemini["settings"]
        assert not {"apiKey", "chatModel", "embeddingModel", "baseUrl"} & set(gemini["settings"])
        record = gemini["settings"]["providers"]["gemini"]
        assert state._decrypt_secret(record["apiKey"]) == "AIza-legacy-gemini"
        assert (record["chatModel"], record["embeddingModel"]) == ("", "gemini-embedding-001")
        assert "baseUrl" not in record and "baseUrl" not in json.dumps(gemini["settings"])
        # Gemini made its vectors: they stay, marked with their provider.
        assert all(chunk["embedding"] == [0.1, 0.2, 0.3] for chunk in gemini["chunks"])
        assert (gemini["embeddingProvider"], gemini["embeddingModel"]) == ("gemini", "gemini-embedding-001")
        gemini_view = state.assistant_settings(state.web_auth._find_user(owners["gemini"]))
        assert (gemini_view["provider"], gemini_view["configured"], gemini_view["notice"]) == (
            "gemini", True, ""
        )
        for name, notice in (("ollama", "ollama_removed"), ("shared", "shared_ai_removed"),
                             ("quiet", "shared_ai_removed")):
            view = state.assistant_settings(state.web_auth._find_user(owners[name]))
            assert (view["notice"], view["configured"], view["provider"]) == (notice, False, "")
        # Their auto replies are switched off with the reason; other switches stay.
        auto = state.store.get("ai_auto_reply", {})["accounts"]
        for name in ("ollama", "shared", "quiet"):
            record = auto[f"acct-{name}"]
            assert (record["enabled"], record["disabledReason"], record["contactsEnabled"],
                    record["groupsEnabled"]) == (False, "ai_removed", True, False)
            assert record["updatedAt"] != _OLD_TIME
        assert auto["acct-gemini"]["enabled"] is True and "disabledReason" not in auto["acct-gemini"]
        assert auto["acct-idle"]["enabled"] is False and "disabledReason" not in auto["acct-idle"]
        logs = _removal_logs(state)
        assert sorted(log["accountId"] for log in logs) == ["acct-ollama", "acct-quiet", "acct-shared"]
        assert all(log["action"] == "ai.auto_reply.settings" and log["ok"] is False for log in logs)
        assert "Ollama" in next(log for log in logs if log["accountId"] == "acct-ollama")["detail"]
        view = state.ai_auto_reply_settings("acct-ollama", state.web_auth._find_user(owners["ollama"]))
        assert (view["enabled"], view["disabledReason"], view["runtime"]["state"]) == (
            False, "ai_removed", "off"
        )
        migrated = {key: state.store.get(key, None) for key in ("global_ai", "ai_auto_reply")}
    finally:
        state.close()

    state = WebState(legacy_config)  # a later start: nothing changes
    try:
        assert {key: state.store.get(key, None) for key in ("global_ai", "ai_auto_reply")} == migrated
        assert len(_removal_logs(state)) == 3
        # The steps themselves change nothing either on data already migrated.
        data = state.store.get("global_ai", {})
        data.pop(flag)
        state.store.set("global_ai", data)
        state._migrate_ai_providers()
        again = state.store.get("global_ai", {})
        assert again[flag]["autoRepliesSwitchedOff"] == 0
        assert {key: value for key, value in again.items() if key != flag} == {
            key: value for key, value in migrated["global_ai"].items() if key != flag
        }
        assert state.store.get("ai_auto_reply", None) == migrated["ai_auto_reply"]
        assert len(_removal_logs(state)) == 3
        # The notice clears when the owner saves a working AI; auto reply goes on again.
        _fake_clients(monkeypatch)
        owner = state.web_auth._find_user(owners["ollama"])
        saved = state.save_assistant_settings(
            {"provider": "openai", "apiKey": "sk-new-openai-key", "test": False}, owner
        )
        assert (saved["notice"], saved["configured"], saved["systemPrompt"], saved["strictKnowledge"]) == (
            "", True, "Shop prompt", True
        )
        result = state.save_ai_auto_reply_settings("acct-ollama", {"enabled": True}, owner)
        assert (result["enabled"], result["disabledReason"]) == (True, "")
        assert "disabledReason" not in state.store.get("ai_auto_reply", {})["accounts"]["acct-ollama"]
    finally:
        state.close()


def test_without_an_active_shared_ai_only_ollama_workspaces_get_a_notice(legacy_config):
    owners, _legacy = _seed_legacy_install(legacy_config, shared_ai_on=False)
    state = WebState(legacy_config)
    try:
        stored = state.store.get("global_ai", {})
        assert "settings" not in stored
        assert stored[webapp_module.AI_PROVIDERS_MIGRATION_KEY]["sharedAiWasActive"] is False
        tenants = stored["tenants"]
        assert tenants[owners["ollama"]]["settings"]["aiNotice"] == "ollama_removed"
        # Nobody lost an AI here: no notice, but the shared AI's vectors are of no use.
        assert "settings" not in tenants[owners["shared"]]
        assert all("embedding" not in chunk for chunk in tenants[owners["shared"]]["chunks"])
        assert owners["quiet"] not in tenants
        auto = state.store.get("ai_auto_reply", {})["accounts"]
        assert auto["acct-ollama"]["enabled"] is False
        assert all(auto[f"acct-{name}"]["enabled"] for name in ("shared", "gemini", "quiet"))
        assert [log["accountId"] for log in _removal_logs(state)] == ["acct-ollama"]
        # A switched-on auto reply with no AI says so instead of failing per message.
        quiet = state.ai_auto_reply_settings("acct-quiet", state.web_auth._find_user(owners["quiet"]))
        assert (quiet["enabled"], quiet["aiConfigured"], quiet["runtime"]["state"]) == (
            True, False, "ai_missing"
        )
    finally:
        state.close()


@pytest.mark.parametrize("god_strict", [True, False])
def test_workspaces_that_relied_on_a_strict_shared_ai_stay_strict(
    legacy_config, monkeypatch, god_strict
):
    owners, _legacy = _seed_legacy_install(
        legacy_config,
        shared_settings={"strictKnowledge": god_strict, "topK": 8, "temperature": 0.7},
    )
    state = WebState(legacy_config)
    try:
        tenants = state.store.get("global_ai", {})["tenants"]
        # The workspace without settings, and the one with only an auto reply.
        for name in ("shared", "quiet"):
            settings = tenants[owners[name]]["settings"]
            assert settings["aiNotice"] == "shared_ai_removed"
            assert settings.get("strictKnowledge", False) is god_strict
            assert not {"systemPrompt", "topK", "temperature"} & set(settings)
        # A workspace's own choice stays its own.
        assert tenants[owners["ollama"]]["settings"]["strictKnowledge"] is True
        assert tenants[owners["gemini"]]["settings"]["strictKnowledge"] is False
        owner = state.web_auth._find_user(owners["shared"])
        view = state.assistant_settings(owner)
        assert (view["strictKnowledge"], view["notice"]) == (god_strict, "shared_ai_removed")
        # The owner adds a key; the page posts the strict setting it showed.
        _fake_clients(monkeypatch)
        state.save_assistant_settings(
            {"provider": "openai", "apiKey": "sk-new-openai-key", "test": False,
             "strictKnowledge": view["strictKnowledge"]},
            owner,
        )
        settings = state._assistant_settings_for(state._global_ai_all(), owner)
        assert (settings["configured"], settings["strictKnowledge"]) == (True, god_strict)
        assert (settings["systemPrompt"], settings["topK"], settings["temperature"]) == (
            webapp_module.DEFAULT_ASSISTANT_SYSTEM_PROMPT, 5, 0.2
        )
    finally:
        state.close()


_GLOBAL_AI_RECORD = {
    "knowledgeMarkdown": "# ส่งของ\n\nส่งฟรีทั่วประเทศ",
    "questions": [{"id": "q1", "userId": "someone", "question": "ส่งฟรีไหม",
                   "status": "unanswered", "createdAt": _OLD_TIME}],
    "tenants": {"owner-1": {"settings": {"provider": "openai", "enabled": True},
                            "knowledgeMarkdown": "ร้านเปิด 9 โมง", "apiSources": [],
                            "manualAnswers": [], "chunks": []}},
}


def test_an_unreadable_global_ai_file_is_kept_and_never_migrated(legacy_config):
    from pathlib import Path

    state_dir = Path(legacy_config.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "global_ai.json"
    damaged = json.dumps(_GLOBAL_AI_RECORD, ensure_ascii=False).encode()[:-40]  # cut short
    path.write_bytes(damaged)
    state = WebState(legacy_config)
    try:
        assert path.read_bytes() == damaged  # the start does not write over it
        # A copy is kept at once, because the first save writes a whole new file.
        kept = list(state_dir.glob("global_ai.json.unreadable-*"))
        assert len(kept) == 1 and kept[0].read_bytes() == damaged
        owner = _owner(state)
        state.save_assistant_settings(
            {"provider": "openai", "apiKey": "sk-after-damage", "test": False}, owner
        )
        assert path.read_bytes() != damaged
        assert kept[0].read_bytes() == damaged  # still there to repair from
        assert len(list(state_dir.glob("global_ai.json.unreadable-*"))) == 1  # once a run
    finally:
        state.close()
    # Repaired: the next start migrates it, and nothing in it is lost.
    path.write_text(json.dumps(_GLOBAL_AI_RECORD, ensure_ascii=False), encoding="utf-8")
    state = WebState(legacy_config)
    try:
        stored = state.store.get("global_ai", {})
        assert isinstance(stored[webapp_module.AI_PROVIDERS_MIGRATION_KEY], dict)
        assert stored["knowledgeMarkdown"] == _GLOBAL_AI_RECORD["knowledgeMarkdown"]
        assert stored["questions"] == _GLOBAL_AI_RECORD["questions"]
        assert stored["tenants"]["owner-1"]["knowledgeMarkdown"] == "ร้านเปิด 9 โมง"
    finally:
        state.close()


def test_the_postgresql_import_stops_at_a_state_file_it_cannot_read(tmp_path):
    from types import SimpleNamespace

    class _DictStore(webapp_module.PostgresStateStore):
        def __init__(self, db):  # no database: only the import of the old files
            self.db = db

        def get(self, key, default):
            return self.db.get(key, default)

        def set(self, key, value):
            self.db[key] = json.loads(json.dumps(value))

    good = json.dumps(_GLOBAL_AI_RECORD, ensure_ascii=False).encode()
    (tmp_path / "global_ai.json").write_bytes(good[:-40])  # cut short
    (tmp_path / "accounts.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"accounts": [{"id": "a1"}]}).encode()
    )
    config = SimpleNamespace(
        accounts_file=str(tmp_path / "accounts.json"),
        schedules_file=str(tmp_path / "schedules.json"),
        auth_file=str(tmp_path / "auth.json"),
    )
    db: dict = {}
    # The start stops there, before anything (accounts, AI settings) is saved under a
    # key it has not imported: a key saved once is never imported.
    with pytest.raises(RuntimeError) as stopped:
        _DictStore(db)._migrate_legacy_files(config)
    [kept] = tmp_path.glob("global_ai.json.unreadable-*")
    assert kept.read_bytes() == good[:-40]
    message = str(stopped.value)
    assert "global_ai.json cannot be read" in message and str(kept) in message
    assert "start LinePassport again" in message
    assert db == {}  # nothing moved yet, and global_ai not as empty
    # Started again before the repair: it stops again, with no second copy.
    with pytest.raises(RuntimeError):
        _DictStore(db)._migrate_legacy_files(config)
    assert len(list(tmp_path.glob("global_ai.json.unreadable-*"))) == 1
    # Repaired: the next start imports it.
    (tmp_path / "global_ai.json").write_bytes(good)
    _DictStore(db)._migrate_legacy_files(config)
    assert db["global_ai"]["tenants"]["owner-1"]["knowledgeMarkdown"] == "ร้านเปิด 9 โมง"
    assert db["accounts"] == {"accounts": [{"id": "a1"}]}  # a BOM is fine


def test_the_postgresql_import_names_every_state_file_it_cannot_read(tmp_path):
    from types import SimpleNamespace

    class _DictStore(webapp_module.PostgresStateStore):
        def __init__(self, db):
            self.db = db

        def get(self, key, default):
            return self.db.get(key, default)

        def set(self, key, value):
            self.db[key] = json.loads(json.dumps(value))

    (tmp_path / "accounts.json").write_bytes(b'{"accounts": [')  # cut short
    # Raw surrogates (CESU-8, as some Java tools write): no save could write them back.
    (tmp_path / "patterns.json").write_bytes(b'{"patterns": ["\xed\xa0\xbd\xed\xb8\x80"]}')
    (tmp_path / "schedules.json").write_bytes(b"[" * 100_000)  # nested too deep
    (tmp_path / "auth.json").write_bytes(b'{"users": []}')
    config = SimpleNamespace(
        accounts_file=str(tmp_path / "accounts.json"),
        schedules_file=str(tmp_path / "schedules.json"),
        auth_file=str(tmp_path / "auth.json"),
    )
    db: dict = {}
    with pytest.raises(RuntimeError) as stopped:
        _DictStore(db)._migrate_legacy_files(config)
    message = str(stopped.value)
    for name in ("accounts.json", "patterns.json", "schedules.json"):
        [kept] = tmp_path.glob(f"{name}.unreadable-*")
        assert f"{name} cannot be read (a copy of it is kept as {kept})" in message
    assert "so they cannot be moved into the database" in message
    assert db == {}  # one start names them all, and moves nothing


def test_a_state_file_no_save_could_write_back_counts_as_unreadable(tmp_path, capsys):
    path = tmp_path / "patterns.json"
    for damaged in (
        b'{"patterns": ["\xed\xa0\xbd\xed\xb8\x80"]}',  # CESU-8 surrogates
        '{"patterns": ["\ud83d"]}'.encode("utf-16", "surrogatepass"),  # a lone surrogate
        b"[" * 100_000,  # nested too deep
    ):
        path.write_bytes(damaged)
        webapp_module._UNREADABLE_KEPT.pop(str(path), None)
        assert webapp_module._read_json_file(str(path), {"patterns": []}) == {"patterns": []}
        assert damaged in {kept.read_bytes() for kept in tmp_path.glob("patterns.json.unreadable-*")}
    assert capsys.readouterr().err.count("cannot be read") == 3


def test_an_unreadable_file_copy_that_fails_is_tried_again(tmp_path, capsys, monkeypatch):
    path = tmp_path / "global_ai.json"
    path.write_bytes(b'{"cut short')
    webapp_module._UNREADABLE_KEPT.pop(str(path), None)
    real_fdopen = webapp_module.os.fdopen

    class _FullDisk:
        def __init__(self, fd, mode):
            self.file = real_fdopen(fd, mode)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self.file.close()
            return False

        def write(self, data):
            self.file.write(data[:4])
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(webapp_module.os, "fdopen", _FullDisk)
    assert webapp_module._read_json_file(str(path), {}) == {}
    assert not list(tmp_path.glob("global_ai.json.unreadable-*"))  # no copy cut short
    assert "no copy of it could be kept yet" in capsys.readouterr().err
    webapp_module._read_json_file(str(path), {})  # tried again, quietly
    assert capsys.readouterr().err == ""
    monkeypatch.undo()
    webapp_module._read_json_file(str(path), {})  # room again: kept, and said
    [kept] = tmp_path.glob("global_ai.json.unreadable-*")
    assert kept.read_bytes() == b'{"cut short'
    assert f"a copy of it is kept as {kept}." in capsys.readouterr().err
    webapp_module._read_json_file(str(path), {})
    assert capsys.readouterr().err == ""


def _no_room_for_copies(monkeypatch):
    """No copy of a damaged state file can be made (a full disk)."""
    real_open = webapp_module.os.open

    def full_disk(name, *args, **kwargs):
        if ".unreadable-" in str(name):
            raise OSError(28, "No space left on device")
        return real_open(name, *args, **kwargs)

    monkeypatch.setattr(webapp_module.os, "open", full_disk)


def _file_store(tmp_path):
    from types import SimpleNamespace

    return webapp_module.FileStateStore(
        SimpleNamespace(
            accounts_file=str(tmp_path / "accounts.json"),
            schedules_file=str(tmp_path / "schedules.json"),
            auth_file=str(tmp_path / "auth.json"),
        )
    )


def test_a_damaged_state_file_is_not_saved_over_before_a_copy_is_kept(
    tmp_path, capsys, monkeypatch
):
    store = _file_store(tmp_path)
    path = tmp_path / "global_ai.json"
    path.write_bytes(b'{"cut short')
    webapp_module._UNREADABLE_KEPT.pop(str(path), None)
    _no_room_for_copies(monkeypatch)
    assert store.get("global_ai", {}) == {}
    assert "so no save writes over it until one is" in capsys.readouterr().err
    # A save would leave nothing to repair from: it fails, and the file stays as it is.
    with pytest.raises(OSError, match="not saved over"):
        store.set("global_ai", {"tenants": {}})
    with pytest.raises(OSError, match="not saved over"):
        store.set_many({"patterns": {"patterns": []}, "global_ai": {"tenants": {}}})
    assert path.read_bytes() == b'{"cut short'
    assert not (tmp_path / "patterns.json").exists()  # the set_many wrote nothing
    assert not list(tmp_path.glob("global_ai.json.unreadable-*"))
    # Room again: the next save keeps a copy first, then writes the file anew.
    monkeypatch.undo()
    store.set("global_ai", {"tenants": {}})
    [kept] = tmp_path.glob("global_ai.json.unreadable-*")
    assert kept.read_bytes() == b'{"cut short'
    said = capsys.readouterr().err
    assert f"a copy of it is kept as {kept}, and the next save of it writes a new file" in said
    assert store.get("global_ai", {}) == {"tenants": {}}
    store.set("global_ai", {"tenants": {"a": {}}})  # later saves go on as usual
    assert len(list(tmp_path.glob("global_ai.json.unreadable-*"))) == 1


def test_a_state_file_repaired_after_a_failed_copy_is_saved_as_usual(tmp_path, monkeypatch):
    store = _file_store(tmp_path)
    path = tmp_path / "patterns.json"
    path.write_bytes(b'{"patterns": [')
    webapp_module._UNREADABLE_KEPT.pop(str(path), None)
    _no_room_for_copies(monkeypatch)
    assert store.get("patterns", {"patterns": []}) == {"patterns": []}
    # Repaired by hand: read again as it is, and saved by what was read.
    path.write_text('{"patterns": [{"id": "p1"}]}', encoding="utf-8")
    assert store.get("patterns", {"patterns": []}) == {"patterns": [{"id": "p1"}]}
    store.set("patterns", {"patterns": [{"id": "p1"}, {"id": "p2"}]})
    assert json.loads(path.read_text(encoding="utf-8"))["patterns"][1] == {"id": "p2"}
    # Damaged again later in the same run: that is copied (or guarded) anew.
    path.write_bytes(b'{"patterns": [{')
    assert store.get("patterns", {"patterns": []}) == {"patterns": []}
    with pytest.raises(OSError, match="not saved over"):
        store.set("patterns", {"patterns": []})
    assert path.read_bytes() == b'{"patterns": [{'


def test_a_state_file_damaged_again_is_copied_again(tmp_path, capsys):
    store = _file_store(tmp_path)
    path = tmp_path / "global_ai.json"
    path.write_bytes(b'{"first damage')
    webapp_module._UNREADABLE_KEPT.pop(str(path), None)
    assert store.get("global_ai", {}) == {}
    store.set("global_ai", {"tenants": {}})  # the first damage is kept, the file saved
    # Damaged again later in the run (a cut-short restore, say): that is kept too.
    path.write_bytes(b'{"second damage')
    assert store.get("global_ai", {}) == {}
    store.set("global_ai", {"tenants": {"x": {}}})
    # A hand repair that still does not parse, before any save: kept as well.
    path.write_bytes(b'{"third damage')
    assert store.get("global_ai", {}) == {}
    path.write_bytes(b'{"still broken')
    assert store.get("global_ai", {}) == {}
    store.set("global_ai", {"tenants": {}})
    copies = {kept.read_bytes() for kept in tmp_path.glob("global_ai.json.unreadable-*")}
    assert copies == {b'{"first damage', b'{"second damage', b'{"third damage', b'{"still broken'}
    assert capsys.readouterr().err.count("cannot be read") == 4


def test_a_state_file_repaired_by_hand_is_not_saved_over_with_what_was_in_memory(
    tmp_path, monkeypatch
):
    # Schedules, sign-ins and accounts are read once and then saved from memory: a
    # repair made before LinePassport saves the file again is not written over (restart
    # to load it). After that save a repair is saved over, so repairs are made with
    # LinePassport stopped.
    store = _file_store(tmp_path)
    path = tmp_path / "schedules.json"
    path.write_bytes(b'{"schedules": [')
    webapp_module._UNREADABLE_KEPT.pop(str(path), None)
    _no_room_for_copies(monkeypatch)
    assert store.get("schedules", {"schedules": []}) == {"schedules": []}
    repaired = b'{"schedules": [{"id": "s1"}]}'
    path.write_bytes(repaired)
    for room in (False, True):
        if room:
            monkeypatch.undo()
        with pytest.raises(OSError, match="Restart LinePassport"):
            store.set("schedules", {"schedules": []})
        assert path.read_bytes() == repaired
    assert not list(tmp_path.glob("schedules.json.unreadable-*"))


def test_a_damaged_ai_settings_file_does_not_stop_the_start(legacy_config, monkeypatch):
    from pathlib import Path

    WebAuth(legacy_config.auth_file).setup("owner@example.com", "owner-pass")
    state_dir = Path(legacy_config.state_dir)
    path = state_dir / "ai_settings.json"
    path.write_bytes(b'{"tenants": {')
    webapp_module._UNREADABLE_KEPT.pop(str(path), None)
    _no_room_for_copies(monkeypatch)
    state = WebState(legacy_config)  # the start's data step leaves it as it is
    try:
        assert path.read_bytes() == b'{"tenants": {'
    finally:
        state.close()


def test_an_unreadable_file_is_copied_once_whatever_the_restarts(tmp_path, capsys, monkeypatch):
    from pathlib import Path

    path = tmp_path / "global_ai.json"
    path.write_bytes(b'{"cut short')
    for _ in range(3):  # three runs of the server
        webapp_module._UNREADABLE_KEPT.pop(str(path), None)
        assert webapp_module._read_json_file(str(path), {}) == {}
        assert webapp_module._read_json_file(str(path), {}) == {}
    [kept] = tmp_path.glob("global_ai.json.unreadable-*")
    # Each run says so once, and where the copy is.
    assert capsys.readouterr().err.count(f"a copy of it is kept as {kept}.") == 3
    # Damaged in another way later, even within the same second: that is kept too.
    path.write_bytes(b'{"cut differently')
    webapp_module._UNREADABLE_KEPT.pop(str(path), None)
    webapp_module._read_json_file(str(path), {})
    copies = sorted(tmp_path.glob("global_ai.json.unreadable-*"))
    assert {item.read_bytes() for item in copies} == {b'{"cut short', b'{"cut differently'}
    # An earlier copy that cannot be read is passed over, not a reason to keep none.
    original = Path.read_bytes

    def read_bytes(self):
        if ".unreadable-" in self.name:
            raise PermissionError(13, "Permission denied", str(self))
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)
    path.write_bytes(b'{"cut short')  # the bytes of the first copy, which is unreadable now
    webapp_module._UNREADABLE_KEPT.pop(str(path), None)
    webapp_module._read_json_file(str(path), {})
    monkeypatch.undo()
    assert len(list(tmp_path.glob("global_ai.json.unreadable-*"))) == 3


# UTF-8 with a byte order mark (Notepad's "UTF-8 with BOM"); UTF-16 with one (Notepad's
# "Unicode", and Windows PowerShell 5.1's Out-File and >), UTF-32, and UTF-16 without.
@pytest.mark.parametrize("encoding", ["utf-8-sig", "utf-16", "utf-32", "utf-16-le"])
def test_a_state_file_saved_with_a_byte_order_mark_is_read_normally(legacy_config, encoding):
    from pathlib import Path

    state_dir = Path(legacy_config.state_dir)
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "global_ai.json"
    path.write_bytes(json.dumps(_GLOBAL_AI_RECORD, ensure_ascii=False).encode(encoding))
    state = WebState(legacy_config)
    try:
        stored = state.store.get("global_ai", {})
        assert isinstance(stored[webapp_module.AI_PROVIDERS_MIGRATION_KEY], dict)
        assert stored["tenants"]["owner-1"]["knowledgeMarkdown"] == "ร้านเปิด 9 โมง"
        assert path.read_bytes().startswith(b"{")  # written back as plain UTF-8
        assert not list(state_dir.glob("global_ai.json.unreadable-*"))
    finally:
        state.close()


def test_a_fresh_install_writes_the_migration_flags_once_it_has_ai_data(legacy_config):
    from pathlib import Path

    path = Path(legacy_config.state_dir) / "global_ai.json"
    state = WebState(legacy_config)  # nothing to migrate, nothing written
    try:
        assert not path.exists()
        owner = _owner(state)
        state.save_assistant_knowledge(
            {"sections": [{"id": "hours", "title": "เวลาเปิด", "body": "ร้านเปิด 9 โมง"}]}, owner
        )
        assert webapp_module.AI_PROVIDERS_MIGRATION_KEY not in state.store.get("global_ai", {})
    finally:
        state.close()
    state = WebState(legacy_config)
    try:
        stored = state.store.get("global_ai", {})
        assert isinstance(stored[webapp_module.AI_PROVIDERS_MIGRATION_KEY], dict)
        assert stored[webapp_module.STAFF_ANSWERS_MOVED_KEY]["moved"] == 0
        assert stored["tenants"][owner["id"]]["chunks"][0]["text"] == "## เวลาเปิด\n\nร้านเปิด 9 โมง"
    finally:
        state.close()


def _seed_staff_answers_install(config, *, ai_migrated):
    """An install whose God answered staff questions before this release.

    The answers sit under the staff member's id. The owner works on Gemini with a
    vector index. Also there: a removed staff member's answers, the answers of a
    staff member whose owner is gone, and a staff record holding shop info of its
    own. ``ai_migrated``: the AI providers migration already ran (and stamped the
    staff record with a notice); else the shared AI was still on.
    """
    state = WebState(config)
    try:
        token = state.web_auth.setup("owner@example.com", "owner-pass")
        owner = state.web_auth.current_user(WebAuth.cookie_header(token))
        _seed_owned_account(state, owner)
        staff = state.web_auth.create_staff(owner, "staff@example.com", "staff-pass", "S", ["acct"])
        other = state.web_auth.create_staff(owner, "other@example.com", "staff-pass", "O", ["acct"])
        lone_owner = state.web_auth.create_user("gone@example.com", "owner-pass", "member", [])
        state.account_store.data["accounts"].append(
            {"id": "acct-gone", "label": "gone", "ownerId": lone_owner["id"], "tokenFile": "",
             "createdAt": _OLD_TIME, "updatedAt": _OLD_TIME}
        )
        state.account_store.save()
        state.web_auth.grant_account_access(lone_owner["id"], "acct-gone")
        lone_staff = state.web_auth.create_staff(
            state.web_auth._find_user(lone_owner["id"]), "lone@example.com", "staff-pass", "L",
            ["acct-gone"],
        )
        # The lone staff member's owner is removed since.
        state.web_auth.data["users"] = [
            user for user in state.web_auth.data["users"] if user["id"] != lone_owner["id"]
        ]
        state.web_auth._save()

        def answer(answer_id, question_id, text, updated_at=_OLD_TIME):
            return {"id": answer_id, "questionId": question_id, "question": f"คำถาม {question_id}",
                    "answer": text, "answeredBy": "God", "createdAt": _OLD_TIME,
                    "updatedAt": updated_at}

        owner_tenant = {
            "settings": {"provider": "gemini", "enabled": True, "providers": {"gemini": {
                "apiKey": state._encrypt_secret("AIza-owner-gemini-key"), "chatModel": "",
                "embeddingModel": "gemini-embedding-001"}}},
            "knowledgeSections": [{"id": "hours", "title": "เวลาเปิด", "body": "ร้านเปิด 9 โมง"}],
            "knowledgeMarkdown": "## เวลาเปิด\n\nร้านเปิด 9 โมง",
            "apiSources": [],
            # The owner's own answer to q2 is newer than the staff copy; q3's is older.
            "manualAnswers": [
                answer("own-q2", "q2", "คำตอบใหม่ของร้าน", "2026-10-05T10:00:00"),
                answer("own-q3", "q3", "คำตอบเก่าของร้าน", "2026-09-01T10:00:00"),
            ],
        }
        owner_tenant["chunks"] = [
            {**chunk, "embedding": [0.1, 0.2]}
            for chunk in WebState._build_knowledge_chunks(owner_tenant, scope=f"tenant:{owner['id']}")
        ]
        owner_tenant.update(
            embeddingProvider="gemini", embeddingModel="gemini-embedding-001", indexMode="embedding"
        )
        staff_answers = [
            answer("staff-q1", "q1", "ยังไม่ส่งต่างประเทศ"),
            answer("staff-q2", "q2", "คำตอบเก่าของพนักงาน"),
            answer("staff-q3", "q3", "คำตอบใหม่ของพนักงาน", "2026-10-03T10:00:00"),
        ]
        staff_tenant = {
            "manualAnswers": staff_answers, "apiSources": [], "embeddingModel": "bge-m3",
            "indexMode": "embedding",
            "chunks": [
                {**chunk, "embedding": [0.3, 0.4]}
                for chunk in WebState._build_knowledge_chunks(
                    {"manualAnswers": staff_answers}, scope=f"tenant:{staff['id']}"
                )
            ],
        }
        if ai_migrated:
            staff_tenant["settings"] = {
                "provider": "", "aiNotice": "shared_ai_removed", "updatedAt": _OLD_TIME
            }
        legacy = {
            "questions": [
                {"id": f"q{index}", "userId": staff["id"], "question": f"คำถาม q{index}",
                 "status": "resolved", "createdAt": _OLD_TIME}
                for index in (1, 2, 3)
            ],
            "tenants": {
                owner["id"]: owner_tenant,
                staff["id"]: staff_tenant,
                # Shop info of its own (never written by this app, but kept if there).
                other["id"]: {"knowledgeMarkdown": "ข้อมูลของพนักงาน", "apiSources": [],
                              "manualAnswers": [answer("other-q4", "q4", "ส่งทุกวัน")],
                              "chunks": []},
                "removed-staff": {"manualAnswers": [answer("gone-q5", "q5", "ไม่มีหน้าร้าน")],
                                  "apiSources": [], "chunks": []},
                lone_staff["id"]: {"manualAnswers": [answer("lone-q6", "q6", "ปิดวันจันทร์")],
                                   "apiSources": [], "chunks": []},
            },
        }
        if ai_migrated:
            legacy[webapp_module.AI_PROVIDERS_MIGRATION_KEY] = {
                "at": _OLD_TIME, "sharedAiWasActive": True, "notices": 1,
                "autoRepliesSwitchedOff": 0,
            }
        else:
            legacy["settings"] = {"enabled": True, "chatModel": "llama3",
                                  "baseUrl": "http://127.0.0.1:11434"}
        state.store.set("global_ai", legacy)
        return {"owner": owner["id"], "staff": staff["id"], "other": other["id"],
                "lone": lone_staff["id"]}
    finally:
        state.close()


@pytest.mark.parametrize("ai_migrated", [False, True])
def test_god_answers_filed_under_staff_ids_move_to_the_team_owner(
    legacy_config, monkeypatch, ai_migrated
):
    ids = _seed_staff_answers_install(legacy_config, ai_migrated=ai_migrated)
    state = WebState(legacy_config)  # the first start after the update
    try:
        stored = state.store.get("global_ai", {})
        tenants = stored["tenants"]
        owner_tenant = tenants[ids["owner"]]
        answers = {item["questionId"]: item["answer"] for item in owner_tenant["manualAnswers"]}
        assert answers == {
            "q1": "ยังไม่ส่งต่างประเทศ",  # moved
            "q2": "คำตอบใหม่ของร้าน",  # the owner's newer answer stays
            "q3": "คำตอบใหม่ของพนักงาน",  # the staff copy was newer
            "q4": "ส่งทุกวัน",  # moved from the record with shop info of its own
        }
        texts = [chunk["text"] for chunk in owner_tenant["chunks"]]
        assert any("ยังไม่ส่งต่างประเทศ" in text for text in texts)
        assert not any("คำตอบเก่าของพนักงาน" in text for text in texts)
        # The answers that moved in have no vector yet, and the page asks for an update.
        # The chunks that did not change (the shop info, the owner's own newer answer)
        # keep theirs, from the model the index still names.
        assert [chunk.get("embedding") for chunk in owner_tenant["chunks"]] == [
            [0.1, 0.2], [0.1, 0.2], None, None, None
        ]
        assert (owner_tenant["embeddingProvider"], owner_tenant["embeddingModel"]) == (
            "gemini", "gemini-embedding-001"
        )
        assert owner_tenant["indexMode"] == "needs-reindex"
        # The staff record is gone; one with shop info of its own keeps only that.
        assert ids["staff"] not in tenants
        other = tenants[ids["other"]]
        assert other["manualAnswers"] == [] and other["knowledgeMarkdown"] == "ข้อมูลของพนักงาน"
        assert [chunk["text"] for chunk in other["chunks"]] == ["ข้อมูลของพนักงาน"]
        assert "settings" not in other  # a staff record is not a workspace: no notice
        # No owner to be found: left alone.
        assert tenants["removed-staff"]["manualAnswers"][0]["answer"] == "ไม่มีหน้าร้าน"
        assert tenants[ids["lone"]]["manualAnswers"][0]["answer"] == "ปิดวันจันทร์"
        flag = stored[webapp_module.STAFF_ANSWERS_MOVED_KEY]
        assert flag["moved"] == 4
        migration = stored[webapp_module.AI_PROVIDERS_MIGRATION_KEY]
        if not ai_migrated:
            # The owner kept Gemini; no staff record was taken for a workspace.
            assert migration["notices"] == sum(
                1 for record in tenants.values()
                if isinstance(record.get("settings"), dict) and record["settings"].get("aiNotice")
            )
            assert ids["other"] not in [
                key for key, record in tenants.items()
                if isinstance(record.get("settings"), dict) and record["settings"].get("aiNotice")
            ]
        # The shop's AI now uses the moved answer, also when staff ask.
        asked = []

        class _RecordingClient(_FakeAiClient):
            def chat(self, **kwargs):
                asked.append(kwargs)
                return {"answer": "ยังไม่ส่ง", "canAnswer": True, "confidence": 0.9,
                        "model": kwargs["model"]}

        _fake_clients(monkeypatch, _RecordingClient)
        staff_user = state.web_auth._find_user(ids["staff"])
        question = state.ask_global_ai("คำถาม q1 ส่งต่างประเทศไหม", staff_user)["question"]
        assert "ยังไม่ส่งต่างประเทศ" in asked[-1]["context"]
        assert ("Approved answers", "shop") in [
            (source["name"], source["scope"]) for source in question["sources"]
        ]
        migrated = state.store.get("global_ai", None)
    finally:
        state.close()
    state = WebState(legacy_config)  # a later start: nothing changes
    try:
        assert state.store.get("global_ai", None) == migrated
    finally:
        state.close()


def test_saved_provider_keys_are_encrypted_when_the_server_starts(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    config = _make_config(tmp_path)
    state = WebState(config)
    try:
        owner = _owner(state)
        data = state._global_ai_all()
        data["tenants"][owner["id"]] = {
            "settings": {"provider": "openai", "enabled": True, "providers": {
                "openai": {"apiKey": "sk-plain-openai-0001", "chatModel": ""},
                "custom": {"apiKey": "sk-plain-custom-0002", "chatModel": "m",
                           "baseUrl": "https://llm.example.com/v1"},
            }},
            "apiSources": [], "manualAnswers": [], "chunks": [],
        }
        state.store.set("global_ai", data)
    finally:
        state.close()
    state = WebState(config)
    try:
        raw = state.store.get("global_ai", {})
        providers = raw["tenants"][owner["id"]]["settings"]["providers"]
        assert providers["openai"]["apiKey"].startswith("fernet:")
        assert providers["custom"]["apiKey"].startswith("fernet:")
        assert "sk-plain" not in json.dumps(raw)
        settings = state._assistant_settings_for(state._global_ai_all(), owner)
        assert settings["apiKey"] == "sk-plain-openai-0001"
    finally:
        state.close()


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
    # The beep comes from the chat ring, which listens to every account.
    assert "playSoftSound" not in INDEX_HTML
    assert "noteInboxForRing(before, state.inbox)" in INDEX_HTML
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


def test_scheduled_sends_page_has_jobs_and_patterns_tabs():
    html = webapp_module.INDEX_HTML
    assert 'id="schedHub"' in html
    assert 'data-hub-tab="schedules"' in html and 'data-hub-tab="patterns"' in html
    assert 'id="hubJobCount"' in html and 'id="hubPatternCount"' in html
    # Each tab has its own main action in the shared header.
    assert 'data-hub-action="schedules"' in html and 'data-hub-action="patterns"' in html
    assert 'id="patternCategoryChips"' in html and 'id="patternSearch"' in html
    assert 'id="openPatternsButton"' not in html and 'id="closePatternsButton"' not in html
    assert "function useStartJobWithPattern" in html
    assert '"patterns.used_in"' in html


def test_wizard_chips_use_digit_tokens_in_text_and_ai_prompt():
    html = webapp_module.INDEX_HTML
    for token in ('["{1D}", "var.1d_hint"]', '["{2D}", "var.2d_hint"]', '["{3D}", "var.3d_hint"]'):
        assert token in html
    assert '["{rand:1-100}", "var.rand_hint"]' not in html
    assert 'id="wizardAiVarChips"' in html
    # The AI prompt converts Thai chip labels back to tokens before saving.
    assert 'aiPrompt: d.type === "ai_image" ? varsToTokens(d.aiPrompt) : ""' in html


def test_ai_prompt_digit_tokens_are_filled_on_every_send():
    prompt = webapp_module._apply_placeholders("cat number {1D}-{2D}-{3D}")
    assert re.fullmatch(r"cat number \d-\d{2}-\d{3}", prompt)


def test_every_settings_tab_has_a_click_handler():
    html = webapp_module.INDEX_HTML
    panes = re.findall(r'class="settings-tabs"[^>]*>(.*?)</nav>', html, re.S)[0]
    names = re.findall(r'data-settings-pane="([a-z]+)"', panes)
    assert {"team", "ai", "kpi", "notify"} <= set(names)
    assert 'document.querySelectorAll(".settings-tabs [data-settings-pane]")' in html
    assert "setSettingsPane(btn.dataset.settingsPane)" in html


def test_settings_is_a_scrolling_workspace_page():
    html = webapp_module.INDEX_HTML
    assert 'data-tab-panel="settings" id="tabPanelSettings"' in html
    assert '"kpi", "settings"];' in html
    assert 'data-tab="settings" data-nav-settings="true"' in html
    assert '.tab-panel[data-tab-panel="settings"].active { overflow: auto;' in html
    # The settings page lives inside the workspace, after the sidebar.
    assert html.index('id="workspace"') < html.index('id="tabPanelSettings"')


def test_disconnected_account_jumps_to_the_qr_with_a_reason():
    html = webapp_module.INDEX_HTML
    # One click from the sidebar or the chat-list banner.
    assert 'id="accountReconnectButton"' in html and 'id="reconnectBannerButton"' in html
    assert '$("accountReconnectButton").addEventListener("click", () => openReconnect());' in html
    # Reconnect skips the Start step and shows the QR right away.
    start = html.index("function openReconnect(account)")
    body = html[start : html.index("function renderReconnectNotice", start)]
    assert "openAddAccount(true, target);" in body and "startLogin()" in body
    # The QR page explains why and what to do.
    assert 'id="reconnectNotice"' in html
    for key in ("reconnect.why_title", "reconnect.reason_1", "reconnect.todo_2", "reconnect.kept"):
        assert f'"{key}": {{th: ' in html
    # First visit to a disconnected account opens it once by itself.
    assert "state.autoReconnected[accountId] = true;" in html
    assert "if (!autoReconnect(accountId, data.status))" in html
    assert "if (autoReconnect(accountId, status)) return;" in html


def test_a_save_of_several_files_that_cannot_be_undone_counts_as_saved(tmp_path, monkeypatch):
    # A damaged schedules.json (its copy kept) is written by a save of several files
    # that then fails, and the undo fails too (a full disk): the file keeps what
    # LinePassport wrote, so its next save is not refused as a hand repair.
    store = _file_store(tmp_path)
    path = tmp_path / "schedules.json"
    path.write_bytes(b'{"schedules": [')
    webapp_module._UNREADABLE_KEPT.pop(str(path), None)
    assert store.get("schedules", {"schedules": []}) == {"schedules": []}
    real_write = webapp_module._write_json_file
    real_replace = webapp_module.os.replace

    def write(target, value):
        if target.endswith("incoming_api.json"):
            raise OSError(28, "No space left on device")
        real_write(target, value)

    def replace(source, target):
        if str(source).endswith(".rollback.tmp"):
            raise OSError(28, "No space left on device")
        return real_replace(source, target)

    monkeypatch.setattr(webapp_module, "_write_json_file", write)
    monkeypatch.setattr(webapp_module.os, "replace", replace)
    with pytest.raises(OSError):
        store.set_many({"schedules": {"schedules": [{"id": "s1"}]}, "incoming_api": {"links": []}})
    monkeypatch.undo()
    assert json.loads(path.read_text(encoding="utf-8")) == {"schedules": [{"id": "s1"}]}
    assert not list(tmp_path.glob("*.rollback.tmp"))
    [kept] = tmp_path.glob("schedules.json.unreadable-*")
    assert kept.read_bytes() == b'{"schedules": ['
    store.set("schedules", {"schedules": [{"id": "s1"}, {"id": "s2"}]})
    assert len(json.loads(path.read_text(encoding="utf-8"))["schedules"]) == 2


def test_a_state_file_save_cut_short_leaves_no_partial_copy(tmp_path, monkeypatch):
    store = _file_store(tmp_path)
    store.set("patterns", {"patterns": []})

    def dump(value, fh, **kwargs):
        fh.write('{"patt')
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(webapp_module.json, "dump", dump)
    with pytest.raises(OSError):
        store.set("patterns", {"patterns": [{"id": "p1"}]})
    monkeypatch.undo()
    assert not (tmp_path / "patterns.json.tmp").exists()
    assert json.loads((tmp_path / "patterns.json").read_text(encoding="utf-8")) == {"patterns": []}


def test_a_refused_save_tells_the_page_what_to_do(live, monkeypatch):
    base, state = live

    def refused():
        raise webapp_module.StateFileError(
            r"C:\secret\auth.json was repaired after LinePassport found it unreadable",
            "state_file_repaired",
            "A LinePassport data file was changed while LinePassport was running, so it "
            "is not saved over. Restart LinePassport.",
        )

    monkeypatch.setattr(state.web_auth, "ensure_simple_mode", refused)
    response = requests.get(f"{base}/api/auth/status", timeout=5)
    assert response.status_code == 500
    body = response.json()
    assert body["code"] == "state_file_repaired"
    assert "Restart LinePassport" in body["error"] and "secret" not in body["error"]
    # The sign-in page says so (in Thai and English) instead of "cannot reach the server".
    html = webapp_module.INDEX_HTML
    for key in ("errors.state_file_repaired", "errors.state_file_unreadable", "auth.server_error"):
        assert f'"{key}": {{th: "' in html, key
    assert 'hint = code !== "server_error" && I18N["errors." + code]' in html
    # The God page, which is in Thai only, says it in Thai too.
    god = webapp_module.GOD_HTML
    assert "GOD_ERROR_TEXTS[data.code] || data.error" in god
    for code in ("state_file_repaired", "state_file_unreadable"):
        assert f'      {code}: "' in god, code
