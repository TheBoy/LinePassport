"""Findings from rehearsing the upgrade to per-workspace AI providers on PostgreSQL.

The default tests run on the file store, which keeps a NUL character, NaN and
Infinity (of these values it refuses only a lone surrogate). PostgreSQL's jsonb
refuses a NUL character inside a string, NaN and Infinity, and psycopg cannot send
a lone surrogate. ``_store_like_postgres`` makes the file store refuse the same
values, and the tests at the end run on a real PostgreSQL when
OKLINE_TEST_DATABASE_URL points at a throwaway database (every table in it is
dropped first).
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_webapp import _AutoReplyFakeApi, _ImmediateExecutor
from test_webapp_ux import (
    _fake_clients,
    _FakeAiClient,
    _make_config,
    _owner,
    _removal_logs,
    _seed_legacy_install,
    _seed_owned_account,
    _seed_staff_answers_install,
)

from okline import webapp as webapp_module
from okline.webapp import WebError, WebState

PG_URL = os.getenv("OKLINE_TEST_DATABASE_URL", "")
on_postgres = pytest.mark.skipif(not PG_URL, reason="set OKLINE_TEST_DATABASE_URL to run")

_A = "u" + "1" * 32
_B = "u" + "2" * 32
# A character of two surrogates written apart, as some programs send it. Built here
# because an editor may join the two escapes into the character itself.
_SPLIT_PAIR = chr(0xD83D) + chr(0xDE00)
_GRIN = chr(0x1F600)
_LONE_HIGH = chr(0xD83D)
_LONE_LOW = chr(0xDC00)
_NONCHARACTER = chr(0xFFFE)  # not a character, but one the database takes


def _postgres_would_refuse(value) -> str:
    """Why PostgreSQL (jsonb, through psycopg) refuses ``value``; "" when it takes it."""
    if isinstance(value, str):
        if "\x00" in value:
            return "a NUL character"
        try:
            value.encode("utf-8")
        except UnicodeEncodeError:
            return "a lone surrogate"
        return ""
    if isinstance(value, float) and not math.isfinite(value):
        return "NaN or Infinity"
    if isinstance(value, dict):
        for key, item in value.items():
            reason = _postgres_would_refuse(key) or _postgres_would_refuse(item)
            if reason:
                return reason
    if isinstance(value, (list, tuple)):
        for item in value:
            reason = _postgres_would_refuse(item)
            if reason:
                return reason
    return ""


def _store_like_postgres(state, monkeypatch) -> list[str]:
    """Make the file store refuse what PostgreSQL refuses; returns the keys refused."""
    refused: list[str] = []
    original_set, original_many = state.store.set, state.store.set_many

    def check(key, value):
        reason = _postgres_would_refuse(value)
        if reason:
            refused.append(key)
            raise ValueError(f"unsupported value for {key}: {reason}")

    def store_set(key, value):
        check(key, value)
        return original_set(key, value)

    def store_many(values):
        for key, value in values.items():
            check(key, value)
        return original_many(values)

    monkeypatch.setattr(state.store, "set", store_set)
    monkeypatch.setattr(state.store, "set_many", store_many)
    return refused


@pytest.fixture
def web_state(tmp_path, monkeypatch) -> Iterator[WebState]:
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # No auto-reply poll while a test looks at what was stored.
    monkeypatch.setattr(webapp_module, "AI_AUTO_REPLY_POLL_SECONDS", 3600.0)
    state = WebState(_make_config(tmp_path))
    try:
        yield state
    finally:
        state.close()


@pytest.fixture
def legacy_config(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(webapp_module, "AI_AUTO_REPLY_POLL_SECONDS", 3600.0)
    return _make_config(tmp_path)


# --- One helper for text from outside that enters the stored state -------------


@pytest.mark.parametrize(
    ("text", "kept"),
    [
        ("ร้านเปิด 9 โมง", "ร้านเปิด 9 โมง"),
        ("", ""),
        ("a\x00b\x00", "ab"),
        ("a" + _LONE_HIGH + "b", "ab"),
        ("a" + _LONE_LOW + "b", "ab"),
        (_LONE_LOW + _LONE_HIGH, ""),  # the wrong way round: two lone ones
        ("ab" + _LONE_HIGH, "ab"),
        # Two surrogates that stand for one character become that character.
        ("a" + _SPLIT_PAIR + "b", "a" + _GRIN + "b"),
        ("a" + _LONE_HIGH + _SPLIT_PAIR + "\x00b", "a" + _GRIN + "b"),
        ("a" + _GRIN + "b", "a" + _GRIN + "b"),
        # Everything else stays, other control characters included.
        ("tab\there\nnew line \x01\x1f\x7f" + _NONCHARACTER,
         "tab\there\nnew line \x01\x1f\x7f" + _NONCHARACTER),
    ],
)
def test_storable_text_drops_only_what_the_database_refuses(text, kept):
    cleaned = webapp_module._storable_text(text)
    assert cleaned == kept
    assert _postgres_would_refuse(cleaned) == ""
    assert webapp_module._storable_text(cleaned) == cleaned  # nothing left to drop
    if text == kept:
        assert cleaned is text  # clean text is not copied


def test_an_error_text_made_for_the_log_can_be_stored():
    error = RuntimeError("LINE answered\x00 with a broken" + _LONE_LOW + " reply")
    assert webapp_module._safe_log_detail(error) == "LINE answered with a broken reply"
    assert webapp_module._ai_error_text("OpenAI error 500: \x00oops" + _LONE_HIGH) == (
        "OpenAI error 500: oops"
    )
    # Cleaned before the text is put on one line: no gap is left where one stood alone.
    assert webapp_module._safe_log_detail("LINE said \x00 no\n" + _LONE_HIGH + " twice") == (
        "LINE said no twice"
    )
    # A text that holds nothing else counts as no text.
    assert webapp_module._safe_log_detail("\x00" + _LONE_HIGH) == ""
    assert webapp_module._short_text("a\x00b" + _LONE_HIGH, 10) == "ab"
    # The cut counts the characters that are kept.
    assert webapp_module._short_text("\x00" * 50 + "abcdef", 6) == "abcdef"
    # A key repeated with such a character inside it is hidden all the same: the text
    # is cleaned before the key is looked for, not after.
    key = "sk-1234567890abcdef"
    for echoed in ("sk-1234\x00567890abcdef", "sk-1234567890" + _LONE_LOW + "abcdef"):
        assert webapp_module._ai_error_text(f"bad key {echoed} given", key) == (
            "bad key [API key] given"
        )


def test_an_activity_log_line_holds_only_text_the_database_takes(web_state, monkeypatch):
    refused = _store_like_postgres(web_state, monkeypatch)
    web_state.append_bot_log(
        "ai.auto_reply.received",
        account_id="acct",
        detail="มีของไหม\x00คะ" + _LONE_HIGH,
        data={
            "question": "มีของไหม\x00คะ" + _LONE_HIGH,
            "chatName": "ลูกค้า" + _LONE_LOW,
            "nested": {"names": ["a\x00", ("b" + _SPLIT_PAIR,)], "count": 3, "ok": True},
            "messageId": None,
            # Numbers that are none, and a name that is a key.
            "scores": [0.5, float("nan"), float("inf"), float("-inf")],
            "by\x00Name": {"Ann" + _LONE_HIGH: 1},
        },
    )
    web_state.append_bot_log("content.api.error", account_id="acct", ok=False,
                             detail="HTTP 500\x00 from the API")
    assert refused == []
    first, second = web_state.store.get("bot_logs", {"logs": []})["logs"][-2:]
    assert first["detail"] == "มีของไหมคะ"
    assert first["data"] == {
        "question": "มีของไหมคะ",
        "chatName": "ลูกค้า",
        "nested": {"names": ["a", ["b" + _GRIN]], "count": 3, "ok": True},
        "messageId": None,
        "scores": [0.5, None, None, None],
        "byName": {"Ann": 1},
    }
    assert second["detail"] == "HTTP 500 from the API"


def test_a_sign_in_error_the_database_refuses_does_not_stop_the_accounts_being_saved(
    web_state, monkeypatch
):
    # Accounts are kept in memory and saved whole, like the schedules: one text the
    # database refuses in a record would fail every later save until a restart.
    _seed_owned_account(web_state, _owner(web_state))
    refused = _store_like_postgres(web_state, monkeypatch)
    changed = web_state.account_store.mark_session_reauth_required(
        "acct", "LINE said\x00 sign in again" + _LONE_HIGH
    )
    assert changed is True and refused == []
    [account] = web_state.store.get("accounts", {})["accounts"]
    assert account["sessionError"] == "LINE said sign in again"
    web_state.account_store.mark_session_connected("acct")
    assert refused == []


def test_a_profile_name_the_database_refuses_does_not_stop_the_accounts_being_saved(
    web_state, monkeypatch
):
    _seed_owned_account(web_state, _owner(web_state))
    refused = _store_like_postgres(web_state, monkeypatch)
    web_state.account_store.update_profile("acct", {
        "mid": "U-me", "displayName": "ร้านกาแฟ\x00" + _LONE_HIGH, "userid": "shop" + _LONE_LOW,
        "pictureStatus": "pic\x00-1", "picturePath": "/p/1" + _LONE_HIGH,
    })
    assert refused == []
    [account] = web_state.store.get("accounts", {})["accounts"]
    assert (account["label"], account["userid"], account["pictureStatus"], account["picturePath"]) == (
        "ร้านกาแฟ", "shop", "pic-1", "/p/1"
    )
    # A name of nothing else leaves the label as it was.
    web_state.account_store.update_profile("acct", {"mid": "U-me", "displayName": _LONE_HIGH})
    assert web_state.account_store.get("acct")["label"] == "ร้านกาแฟ" and refused == []


# --- 3. An AI answer holding a NUL character or a lone surrogate ----------------

_DIRTY_ANSWER = "\x00 ร้านเปิด 9 โมง\x00ค่ะ " + _LONE_HIGH + _SPLIT_PAIR + " \x00"
_CLEAN_ANSWER = "ร้านเปิด 9 โมงค่ะ " + _GRIN


class _DirtyAiClient(_FakeAiClient):
    answer = _DIRTY_ANSWER

    def chat(self, **kwargs):
        return {"answer": self.answer, "canAnswer": True, "confidence": 0.9,
                "model": "model\x00-x" + _LONE_LOW}


def _workspace_with_ai(state, monkeypatch, client=_DirtyAiClient):
    _fake_clients(monkeypatch, client)
    owner = _seed_owned_account(state, _owner(state))
    state.save_assistant_settings(
        {"provider": "deepseek", "apiKey": "sk-deepseek-key", "test": False}, owner
    )
    return owner


def test_an_ai_answer_is_cleaned_where_it_is_cut_and_its_question_is_logged(
    web_state, monkeypatch
):
    owner = _workspace_with_ai(web_state, monkeypatch)
    refused = _store_like_postgres(web_state, monkeypatch)
    result = web_state.ask_global_ai("ร้านเปิดกี่โมง", owner)
    assert result["logged"] is True and refused == []
    assert result["question"]["answer"] == _CLEAN_ANSWER
    assert result["question"]["model"] == "model-x"
    stored = web_state._global_ai_all()["questions"][-1]
    assert (stored["answer"], stored["model"], stored["status"]) == (
        _CLEAN_ANSWER, "model-x", "answered"
    )


def test_an_ai_answer_is_cut_to_5000_characters_that_are_kept(web_state, monkeypatch):
    class _Long(_DirtyAiClient):
        answer = "\x00" * 40 + "ก" * 4990 + _LONE_HIGH * 40 + "ข" * 40

    owner = _workspace_with_ai(web_state, monkeypatch, _Long)
    answer = web_state.ask_global_ai("สวัสดีครับ", owner)["question"]["answer"]
    assert answer == "ก" * 4990 + "ข" * 10
    assert len(answer) == webapp_module.MAX_AI_ANSWER_LENGTH


def test_an_ai_answer_of_nothing_but_refused_characters_is_no_answer(web_state, monkeypatch):
    class _Empty(_DirtyAiClient):
        answer = " \x00" + _LONE_HIGH + "\x00 "

    owner = _workspace_with_ai(web_state, monkeypatch, _Empty)
    question = web_state.ask_global_ai("สวัสดีครับ", owner)["question"]
    assert (question["answer"], question["status"]) == ("", "unanswered")


class _EchoClient(_FakeAiClient):
    """Answers with the question it was asked, and records it."""

    asked: list[dict] = []

    def chat(self, **kwargs):
        _EchoClient.asked.append(kwargs)
        return {"answer": "ตอบ: " + kwargs["question"], "canAnswer": True, "confidence": 0.9,
                "model": kwargs["model"]}


def test_a_question_is_asked_and_logged_without_what_the_database_refuses(
    web_state, monkeypatch
):
    _EchoClient.asked = []
    owner = _workspace_with_ai(web_state, monkeypatch, _EchoClient)
    refused = _store_like_postgres(web_state, monkeypatch)
    result = web_state.ask_global_ai(" ร้านเปิด\x00กี่โมง" + _LONE_HIGH + " ", owner)
    assert _EchoClient.asked[-1]["question"] == "ร้านเปิดกี่โมง"
    assert result["logged"] is True and refused == []
    assert result["question"]["question"] == "ร้านเปิดกี่โมง"
    assert web_state._global_ai_all()["questions"][-1]["question"] == "ร้านเปิดกี่โมง"
    # Nothing readable in it: no question, as for an empty one.
    with pytest.raises(WebError, match="Question is required"):
        web_state.ask_global_ai("\x00 " + _LONE_LOW, owner)
    assert len(_EchoClient.asked) == 1
    # A failed auto reply's text hides the AI key even with such a character inside it.
    failure = RuntimeError("the key sk-deep\x00seek-key was refused" + _LONE_HIGH)
    assert web_state._auto_reply_error_detail(failure, owner) == "the key [API key] was refused"


class _ReplyApi:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []

    def send_text(self, to, text):
        self.sent.append((to, text))
        return {"id": f"m{len(self.sent)}"}


def _answer_one_message(state, owner, question="ร้านเปิดกี่โมง"):
    """Run one auto reply for a LINE message, as the poller queues it."""
    state.save_ai_auto_reply_settings("acct", {"enabled": True}, owner)
    state._process_ai_auto_reply({
        "accountId": "acct",
        "ownerId": owner["id"],
        "chatMid": _A,
        "chatType": "contact",
        "chatName": "ลูกค้า",
        "senderMid": _A,
        "senderName": "ลูกค้า",
        "enabledAtMs": state._ai_auto_reply_record("acct")["enabledAtMs"],
        "question": question,
        "messageId": "m-in-1",
    })


def test_an_auto_reply_sends_the_answer_it_stores(web_state, monkeypatch):
    owner = _workspace_with_ai(web_state, monkeypatch)
    api = _ReplyApi()
    monkeypatch.setattr(web_state, "get_api", lambda account_id=None, **kwargs: api)
    refused = _store_like_postgres(web_state, monkeypatch)
    _answer_one_message(web_state, owner)
    # What the customer gets is what the question log and the activity log keep.
    assert api.sent == [(_A, _CLEAN_ANSWER)]
    assert refused == []
    assert web_state._global_ai_all()["questions"][-1]["answer"] == _CLEAN_ANSWER
    logs = {log["action"]: log for log in web_state.store.get("bot_logs", {"logs": []})["logs"]}
    assert logs["ai.auto_reply.answered"]["detail"] == _CLEAN_ANSWER
    assert logs["ai.auto_reply.answered"]["data"]["answer"] == _CLEAN_ANSWER
    assert logs["ai.auto_reply.answered"]["data"]["model"] == "model-x"
    assert logs["ai.auto_reply.sent"]["data"]["answer"] == _CLEAN_ANSWER
    assert web_state.ai_auto_reply_runtime["acct"]["replyCount"] == 1


def _auto_reply_actions(state) -> list[str]:
    return [log["action"] for log in state.store.get("bot_logs", {"logs": []})["logs"]
            if log["action"].startswith("ai.auto_reply.") and log["action"] != "ai.auto_reply.settings"]


def test_a_line_message_reaches_the_ai_without_what_the_database_refuses(
    web_state, monkeypatch
):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    _EchoClient.asked = []
    owner = _workspace_with_ai(web_state, monkeypatch, _EchoClient)
    api = _AutoReplyFakeApi()
    monkeypatch.setattr(web_state, "get_api", lambda account_id=None, **kwargs: api)
    refused = _store_like_postgres(web_state, monkeypatch)
    web_state.save_ai_auto_reply_settings(
        "acct", {"enabled": True, "contactsEnabled": True, "groupsEnabled": False}, owner
    )
    enabled_at = web_state._ai_auto_reply_record("acct")["enabledAtMs"]

    def arrives(message_id, text, after_ms):
        api.boxes["U-contact"].append({"id": message_id, "from": "U-contact", "text": text,
                                       "contentType": 0, "createdTime": enabled_at + after_ms})
        web_state.invalidate_inbox_snapshot("acct")
        web_state.poll_ai_auto_replies()

    # Nothing readable in it: passed over like an empty message, with no AI call, no
    # log line and no error on the account.
    arrives("junk", "\x00" + _LONE_HIGH, 1000)
    assert _EchoClient.asked == [] and api.sent == [] and _auto_reply_actions(web_state) == []
    assert web_state.ai_auto_reply_settings("acct", owner)["runtime"]["state"] != "error"
    # With such characters inside: asked, answered and logged without them.
    arrives("dirty", "ร้านเปิด\x00กี่โมง" + _LONE_HIGH, 2000)
    assert [call["question"] for call in _EchoClient.asked] == ["ร้านเปิดกี่โมง"]
    assert api.sent == [("U-contact", "ตอบ: ร้านเปิดกี่โมง")]
    assert refused == []
    assert _auto_reply_actions(web_state) == [
        "ai.auto_reply.received", "ai.auto_reply.answered", "ai.auto_reply.sent"
    ]
    assert web_state._global_ai_all()["questions"][-1]["question"] == "ร้านเปิดกี่โมง"


def test_a_telegram_or_line_oa_message_reaches_the_ai_without_what_the_database_refuses(
    web_state, monkeypatch
):
    web_state.ai_auto_reply_engine.close()
    web_state.ai_auto_reply_executor.shutdown(wait=True, cancel_futures=True)
    web_state.ai_auto_reply_executor = _ImmediateExecutor()
    _EchoClient.asked = []
    owner = _workspace_with_ai(web_state, monkeypatch, _EchoClient)
    api = _ReplyApi()
    monkeypatch.setattr(web_state, "get_api", lambda account_id=None, **kwargs: api)
    refused = _store_like_postgres(web_state, monkeypatch)
    web_state.save_ai_auto_reply_settings("acct", {"enabled": True}, owner)
    at = web_state._ai_auto_reply_record("acct")["enabledAtMs"] + 1000

    def arrives(text):
        web_state._telegram_auto_reply(
            "acct", owner["id"], "tgu55",
            {"text": text, "contentType": 0, "at": at, "name": "Ploy\x00", "externalId": "7"},
        )

    arrives(" \x00" + _LONE_LOW)
    assert _EchoClient.asked == [] and api.sent == [] and _auto_reply_actions(web_state) == []
    assert web_state.ai_auto_reply_inflight == set()
    arrives(" มีของ\x00ไหม" + _LONE_HIGH)
    assert [call["question"] for call in _EchoClient.asked] == ["มีของไหม"]
    assert api.sent == [("tgu55", "ตอบ: มีของไหม")] and refused == []
    received = next(log for log in web_state.store.get("bot_logs", {"logs": []})["logs"]
                    if log["action"] == "ai.auto_reply.received")
    assert (received["detail"], received["data"]["chatName"]) == ("มีของไหม", "Ploy")


# --- 4. A failure text holding a NUL in a scheduled send ------------------------


class _FailingApi:
    """Sends that fail for the chats in ``errors`` with the error given for each."""

    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.errors: dict[str, Exception] = {}

    def send_text(self, to, text):
        if to in self.errors:
            raise self.errors[to]
        self.sent.append((to, text))
        return {"id": f"m{len(self.sent)}"}


@pytest.fixture
def sched(web_state, monkeypatch):
    owner = _seed_owned_account(web_state, _owner(web_state))
    monkeypatch.setattr(webapp_module, "SCHEDULE_TARGET_GAP_SECONDS", (0.0, 0.0))
    monkeypatch.setattr(web_state, "_notify_schedule_failure", lambda *args: None)
    api = _FailingApi()
    monkeypatch.setattr(web_state, "get_api", lambda account_id=None, require_auth=True: api)
    return web_state, owner, api


def _job(state, **body):
    payload = {"accountId": "acct", "name": "promo", "to": _A, "text": "hi {name}",
               "mode": "repeat", "intervalMinutes": 60}
    payload.update(body)
    return state.create_schedule(payload)["schedule"]


def _saved_schedules(state):
    return state.store.get("schedules", {"schedules": []})["schedules"]


def test_a_failure_text_the_database_refuses_does_not_stop_the_schedules_being_saved(
    sched, monkeypatch
):
    state, owner, api = sched
    refused = _store_like_postgres(state, monkeypatch)
    api.errors[_A] = RuntimeError("LINE answered\x00 with a broken" + _LONE_LOW + " reply")
    job = _job(state)
    with pytest.raises(WebError) as failed:
        state._execute_schedule(job["id"], manual=False)
    reason = "LINE answered with a broken reply"
    assert failed.value.message == reason
    stored = state._find_schedule(job["id"])
    assert stored["lastError"] == reason
    assert stored["history"][-1]["error"] == reason and stored["status"] == "retrying"
    # The failed run is saved, and so is every change after it.
    assert refused == [] and not state.schedules_unsaved
    assert _saved_schedules(state)[0]["lastError"] == reason
    assert state.list_schedules("acct", owner)["schedulesUnsaved"] is False
    second = _job(state, name="second")
    assert [item["id"] for item in _saved_schedules(state)] == [job["id"], second["id"]]
    [line] = [log for log in state.store.get("bot_logs", {"logs": []})["logs"]
              if log["action"] == "schedule.run.error"]
    assert line["detail"] == reason


def test_a_partly_failed_send_reports_text_the_database_takes(sched, monkeypatch):
    state, _owner_user, api = sched
    refused = _store_like_postgres(state, monkeypatch)
    api.errors[_B] = RuntimeError("blocked\x00 by" + _LONE_HIGH + " LINE")
    job = _job(state, targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}])
    result = state._execute_schedule(job["id"], manual=False)
    assert result["ok"] is True and api.sent == [(_A, "hi Ann")]
    stored = state._find_schedule(job["id"])
    assert stored["lastError"] == "Sent to 1/2 chats. Bob: blocked by LINE"
    assert stored["history"][-1]["error"] == stored["lastError"]
    assert refused == [] and not state.schedules_unsaved
    assert _saved_schedules(state)[0]["lastError"] == stored["lastError"]


# --- 2. A damaged auto-reply record at the first start --------------------------


def _auto_reply_path(config) -> Path:
    return Path(config.state_dir) / "ai_auto_reply.json"


@pytest.mark.parametrize(
    "damaged",
    [
        b"[]",  # not a JSON object
        b'"auto replies"',
        b"null",
        b'{"accounts": ["acct-ollama"]}',  # its accounts are not a JSON object
        b'{"accounts": {"acct-ollama": {"enabled": tr',  # cut short: cannot be read
    ],
)
def test_a_damaged_auto_reply_record_makes_the_ai_move_wait_for_a_later_start(
    legacy_config, capsys, damaged
):
    owners, legacy = _seed_legacy_install(legacy_config)
    path = _auto_reply_path(legacy_config)
    good = path.read_bytes()
    path.write_bytes(damaged)
    webapp_module._UNREADABLE_KEPT.pop(str(path), None)
    flag = webapp_module.AI_PROVIDERS_MIGRATION_KEY
    capsys.readouterr()
    state = WebState(legacy_config)  # the first start after the update
    try:
        stored = state.store.get("global_ai", {})
        # Nothing is moved, and nothing says it was: the next start tries again.
        assert flag not in stored
        assert stored["settings"] == legacy["settings"]
        assert stored["tenants"][owners["ollama"]]["settings"]["provider"] == "ollama"
        assert path.read_bytes() == damaged  # left as it is
        assert _removal_logs(state) == []
        said = capsys.readouterr().err
        assert "auto-reply settings cannot be read" in said and "a later start" in said
    finally:
        state.close()
    # Repaired: the next start moves every workspace and switches the auto replies off.
    path.write_bytes(good)
    state = WebState(legacy_config)
    try:
        stored = state.store.get("global_ai", {})
        assert stored[flag]["autoRepliesSwitchedOff"] == 3 and "settings" not in stored
        auto = state.store.get("ai_auto_reply", {})["accounts"]
        for name in ("ollama", "shared", "quiet"):
            record = auto[f"acct-{name}"]
            assert (record["enabled"], record["disabledReason"]) == (False, "ai_removed")
        assert auto["acct-gemini"]["enabled"] is True
        assert sorted(log["accountId"] for log in _removal_logs(state)) == [
            "acct-ollama", "acct-quiet", "acct-shared"
        ]
    finally:
        state.close()


def test_an_install_that_never_saved_auto_reply_settings_still_moves(legacy_config):
    owners, _legacy = _seed_legacy_install(legacy_config)
    _auto_reply_path(legacy_config).unlink()
    state = WebState(legacy_config)
    try:
        stored = state.store.get("global_ai", {})
        migration = stored[webapp_module.AI_PROVIDERS_MIGRATION_KEY]
        assert migration["autoRepliesSwitchedOff"] == 0 and "settings" not in stored
        assert stored["tenants"][owners["ollama"]]["settings"]["aiNotice"] == "ollama_removed"
        assert not _auto_reply_path(legacy_config).exists()  # nothing to write
    finally:
        state.close()


@pytest.mark.parametrize("record", [{}, {"accounts": {}}, {"accounts": {"acct-idle": "broken"}}])
def test_an_auto_reply_record_with_nothing_to_switch_off_does_not_hold_the_move(
    legacy_config, record
):
    _seed_legacy_install(legacy_config)
    _auto_reply_path(legacy_config).write_text(json.dumps(record), encoding="utf-8")
    state = WebState(legacy_config)
    try:
        migration = state.store.get("global_ai", {})[webapp_module.AI_PROVIDERS_MIGRATION_KEY]
        assert migration["autoRepliesSwitchedOff"] == 0
        assert state.store.get("ai_auto_reply", None) == record  # as it was
    finally:
        state.close()


def test_the_stores_say_whether_a_key_holds_anything(tmp_path):
    from types import SimpleNamespace

    store = webapp_module.FileStateStore(
        SimpleNamespace(
            accounts_file=str(tmp_path / "accounts.json"),
            schedules_file=str(tmp_path / "schedules.json"),
            auth_file=str(tmp_path / "auth.json"),
        )
    )
    assert store.holds("ai_auto_reply") is False
    path = tmp_path / "ai_auto_reply.json"
    path.write_bytes(b'{"cut short')
    webapp_module._UNREADABLE_KEPT.pop(str(path), None)
    assert store.get("ai_auto_reply", None) is None and store.holds("ai_auto_reply") is True
    store.set("patterns", {"patterns": []})
    assert store.holds("patterns") is True

    class _MemoryStore(webapp_module.StateStore):  # a store that only reads and writes
        def __init__(self):
            self.values = {"null": None, "list": []}

        def get(self, key, default):
            return self.values.get(key, default)

    memory = _MemoryStore()
    assert (memory.holds("list"), memory.holds("missing")) == (True, False)


# --- 1. Search vectors dropped when staff answers move to the owner -------------


def _global_ai_path(config) -> Path:
    return Path(config.state_dir) / "global_ai.json"


def _read_global_ai(config) -> dict:
    return json.loads(_global_ai_path(config).read_text(encoding="utf-8"))


def _as_the_released_version_stored_it(config, owner_id) -> None:
    """The owner's AI record in the shape from before per-workspace providers: one flat
    Gemini record with a blank search model (the default), and an index that names
    its model but no provider."""
    data = _read_global_ai(config)
    tenant = data["tenants"][owner_id]
    entry = tenant["settings"]["providers"]["gemini"]
    tenant["settings"] = {"provider": "gemini", "enabled": True, "apiKey": entry["apiKey"],
                          "chatModel": "", "embeddingModel": "",
                          "baseUrl": "http://127.0.0.1:11434"}
    del tenant["embeddingProvider"]
    _global_ai_path(config).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


class _SameVectorClient(_FakeAiClient):
    """Every text gets the vector the seeded chunks have; records what it was asked."""

    embedded: list[tuple[str, list[str]]] = []
    asked: list[dict] = []

    def embed(self, model, texts, timeout=None):
        _SameVectorClient.embedded.append((model, list(texts)))
        return [[0.1, 0.2] for _ in texts]

    def chat(self, **kwargs):
        _SameVectorClient.asked.append(kwargs)
        return {"answer": "ยังไม่ส่ง", "canAnswer": True, "confidence": 0.9,
                "model": kwargs["model"]}


@pytest.mark.parametrize("install", ["ai moved before", "shared ai still on", "released shape"])
def test_answers_moved_to_the_owner_leave_its_search_vectors_in_place(
    legacy_config, monkeypatch, install
):
    ids = _seed_staff_answers_install(legacy_config, ai_migrated=install == "ai moved before")
    if install == "released shape":
        _as_the_released_version_stored_it(legacy_config, ids["owner"])
    seeded = {chunk["id"]: chunk["text"]
              for chunk in _read_global_ai(legacy_config)["tenants"][ids["owner"]]["chunks"]}
    state = WebState(legacy_config)  # the first start after the update
    try:
        tenant = state.store.get("global_ai", {})["tenants"][ids["owner"]]
        # The index still says which model made its vectors; some chunks wait for theirs.
        assert (tenant["embeddingProvider"], tenant["embeddingModel"], tenant["indexMode"]) == (
            "gemini", "gemini-embedding-001", "needs-reindex"
        )
        kept = [chunk for chunk in tenant["chunks"] if "embedding" in chunk]
        waiting = [chunk for chunk in tenant["chunks"] if "embedding" not in chunk]
        # The shop info and the owner's own newer answer did not change: same id, same
        # text, same vector.
        assert all(seeded[chunk["id"]] == chunk["text"] for chunk in kept)
        assert all(chunk["embedding"] == [0.1, 0.2] for chunk in kept)
        assert sorted(chunk["text"] for chunk in kept) == sorted(
            text for text in seeded.values() if "คำตอบเก่าของร้าน" not in text
        )
        assert len(kept) == 2 and any("ร้านเปิด 9 โมง" in chunk["text"] for chunk in kept)
        # Only the answers that moved in wait for vectors.
        assert len(waiting) == 3 and not {chunk["id"] for chunk in waiting} & set(seeded)
        for answer in ("ยังไม่ส่งต่างประเทศ", "คำตอบใหม่ของพนักงาน", "ส่งทุกวัน"):
            assert sum(answer in chunk["text"] for chunk in waiting) == 1
        view = state.assistant_settings(state.web_auth._find_user(ids["owner"]))
        assert (view["provider"], view["configured"], view["notice"]) == ("gemini", True, "")
        # A question is still ranked by vector, and finds the moved answer by keyword.
        _SameVectorClient.embedded, _SameVectorClient.asked = [], []
        _fake_clients(monkeypatch, _SameVectorClient)
        owner = state.web_auth._find_user(ids["owner"])
        question = state.ask_global_ai("คำถาม q1 ส่งต่างประเทศไหม", owner)["question"]
        assert _SameVectorClient.embedded == [
            ("gemini-embedding-001", ["คำถาม q1 ส่งต่างประเทศไหม"])
        ]
        context = _SameVectorClient.asked[-1]["context"]
        assert "ร้านเปิด 9 โมง" in context and "ยังไม่ส่งต่างประเทศ" in context
        assert question["sources"][0] == {"name": "เวลาเปิด", "score": 1.0, "scope": "shop"}
        # Update index gives the waiting chunks their vectors.
        knowledge = state.reindex_assistant_knowledge(owner)
        assert (knowledge["indexMode"], knowledge["indexError"]) == ("embedding", "")
        tenant = state.store.get("global_ai", {})["tenants"][ids["owner"]]
        assert all(chunk["embedding"] == [0.1, 0.2] for chunk in tenant["chunks"])
        assert len(tenant["chunks"]) == 5
    finally:
        state.close()


def test_a_chunk_keeps_its_vector_only_with_the_same_id_and_the_same_text(web_state):
    tenant = {
        "knowledgeSections": [
            {"id": "hours", "title": "เวลาเปิด", "body": "ร้านเปิด 9 โมง"},
            {"id": "pay", "title": "การจ่ายเงิน", "body": "รับโอนและบัตรเครดิต"},
            {"id": "park", "title": "ที่จอดรถ", "body": "จอดได้หน้าร้าน"},
        ],
        "apiSources": [], "manualAnswers": [],
        "embeddingProvider": "gemini", "embeddingModel": "gemini-embedding-001",
        "indexMode": "embedding",
    }
    built = WebState._build_knowledge_chunks(tenant, scope="tenant:owner-1")
    tenant["chunks"] = [
        {**built[0], "embedding": [0.1, 0.2]},
        # The id of the new chunk, with another text (a damaged or hand-made record).
        {**built[1], "text": "รับเงินสดเท่านั้น", "embedding": [0.3, 0.4]},
        {"id": "made-by-hand", "sourceId": "x", "sourceName": "x", "text": built[2]["text"],
         "embedding": [0.5, 0.6]},
    ]
    web_state._rebuild_chunks_without_ai(tenant, "owner-1")
    assert [chunk.get("embedding") for chunk in tenant["chunks"]] == [[0.1, 0.2], None, None]
    assert [chunk["text"] for chunk in tenant["chunks"]] == [chunk["text"] for chunk in built]
    assert (tenant["embeddingProvider"], tenant["embeddingModel"], tenant["indexMode"]) == (
        "gemini", "gemini-embedding-001", "needs-reindex"
    )
    # Every chunk unchanged: the index stays as it is.
    tenant["indexMode"] = "embedding"
    tenant["chunks"] = [{**chunk, "embedding": [0.1, 0.2]} for chunk in built]
    web_state._rebuild_chunks_without_ai(tenant, "owner-1")
    assert all(chunk["embedding"] == [0.1, 0.2] for chunk in tenant["chunks"])
    assert tenant["indexMode"] == "embedding"
    # No chunk unchanged: no vector is left, and the page asks for an update.
    tenant["knowledgeSections"] = [{"id": "new", "title": "ใหม่", "body": "ข้อมูลใหม่"}]
    web_state._rebuild_chunks_without_ai(tenant, "owner-1")
    assert all("embedding" not in chunk for chunk in tenant["chunks"])
    assert (tenant["embeddingProvider"], tenant["embeddingModel"], tenant["indexMode"]) == (
        "", "", "needs-reindex"
    )
    # Vectors of no named model are not kept for some chunks only.
    tenant.update(knowledgeSections=[{"id": "a", "title": "ก", "body": "ข้อมูล ก"}], indexMode="")
    tenant["chunks"] = [{**chunk, "embedding": [0.1, 0.2]}
                        for chunk in WebState._build_knowledge_chunks(tenant, scope="tenant:owner-1")]
    tenant["knowledgeSections"].append({"id": "b", "title": "ข", "body": "ข้อมูล ข"})
    web_state._rebuild_chunks_without_ai(tenant, "owner-1")
    assert all("embedding" not in chunk for chunk in tenant["chunks"])
    assert (tenant["embeddingModel"], tenant["indexMode"]) == ("", "needs-reindex")


def test_the_ai_move_keeps_waiting_chunks_only_for_an_index_that_says_so(web_state):
    def tenant(index_mode):
        return {
            "settings": {"provider": "gemini", "enabled": True, "apiKey": "", "chatModel": "",
                         "embeddingModel": ""},
            "embeddingModel": "gemini-embedding-001", "indexMode": index_mode,
            "chunks": [{"id": "c1", "text": "one", "embedding": [0.1, 0.2]},
                       {"id": "c2", "text": "two"}],
        }

    # Answers moved in just before (see _move_staff_answers): the vectors stay.
    waiting = tenant("needs-reindex")
    web_state._migrate_tenant_ai(waiting, shared_active=False, now="2026-11-24T10:00:00")
    assert [chunk.get("embedding") for chunk in waiting["chunks"]] == [[0.1, 0.2], None]
    assert (waiting["embeddingProvider"], waiting["embeddingModel"], waiting["indexMode"]) == (
        "gemini", "gemini-embedding-001", "needs-reindex"
    )
    # An index that says it is whole and is not: its vectors are not trusted.
    damaged = tenant("embedding")
    web_state._migrate_tenant_ai(damaged, shared_active=False, now="2026-11-24T10:00:00")
    assert all("embedding" not in chunk for chunk in damaged["chunks"])
    assert (damaged["embeddingProvider"], damaged["embeddingModel"], damaged["indexMode"]) == (
        "", "", "keyword"
    )
    # Nor are vectors of another model, waiting chunks or not.
    other = tenant("needs-reindex")
    other["embeddingModel"] = "bge-m3"
    web_state._migrate_tenant_ai(other, shared_active=False, now="2026-11-24T10:00:00")
    assert all("embedding" not in chunk for chunk in other["chunks"])
    assert (other["embeddingProvider"], other["embeddingModel"], other["indexMode"]) == (
        "", "", "keyword"
    )
    # Nor an index that holds something else in place of a vector, or no dict for a chunk.
    for broken in ({"id": "c2", "text": "two", "embedding": None}, "c2"):
        odd = tenant("needs-reindex")
        odd["chunks"][1] = broken
        web_state._migrate_tenant_ai(odd, shared_active=False, now="2026-11-24T10:00:00")
        assert "embedding" not in odd["chunks"][0]
        assert (odd["embeddingProvider"], odd["embeddingModel"], odd["indexMode"]) == (
            "", "", "keyword"
        )


class _TopicVectorClient(_FakeAiClient):
    """Texts about opening get one vector, all others another."""

    embedded: list[list[str]] = []
    asked: list[dict] = []
    down = False

    def embed(self, model, texts, timeout=None):
        if _TopicVectorClient.down:
            raise webapp_module.AssistantError("OpenAI error 429: rate limit reached", 429)
        _TopicVectorClient.embedded.append(list(texts))
        return [[1.0, 0.0] if "เปิด" in text else [0.0, 1.0] for text in texts]

    def chat(self, **kwargs):
        _TopicVectorClient.asked.append(kwargs)
        return {"answer": "", "canAnswer": False, "confidence": 0.0, "model": kwargs["model"]}


_SECTIONS = [
    {"id": "hours", "title": "เวลาเปิด", "body": "ร้านเปิด 9 โมงถึง 6 โมงเย็น"},
    {"id": "pay", "title": "การจ่ายเงิน", "body": "รับโอนและบัตรเครดิต"},
    {"id": "park", "title": "ที่จอดรถ", "body": "จอดได้หน้าร้าน"},
]


def _indexed_workspace(state, monkeypatch, **settings):
    _TopicVectorClient.embedded, _TopicVectorClient.asked = [], []
    _TopicVectorClient.down = False
    _fake_clients(monkeypatch, _TopicVectorClient)
    owner = _seed_owned_account(state, _owner(state))
    state.save_assistant_settings(
        {"provider": "openai", "apiKey": "sk-openai-key", "test": False, **settings}, owner
    )
    knowledge = state.save_assistant_knowledge({"sections": _SECTIONS}, owner)
    assert knowledge["indexMode"] == "embedding"
    return owner


def _own_tenant(state, owner) -> dict:
    return state._global_ai_all()["tenants"][owner["id"]]


def test_one_approved_answer_in_normal_use_keeps_the_index_whole(web_state, monkeypatch):
    owner = _indexed_workspace(web_state, monkeypatch)
    asked = web_state.ask_global_ai("มีที่จอดรถไหม", owner)["question"]
    assert asked["status"] == "unanswered"
    result = web_state.answer_assistant_question(
        {"id": asked["id"], "answer": "มีที่จอดหน้าร้าน 5 คัน"}, {"displayName": "God"}
    )
    assert (result["knowledge"]["indexMode"], result["knowledge"]["indexError"]) == (
        "embedding", ""
    )
    tenant = _own_tenant(web_state, owner)
    assert len(tenant["chunks"]) == 4
    assert all(len(chunk["embedding"]) == 2 for chunk in tenant["chunks"])


def test_an_answer_approved_while_the_search_model_fails_keeps_the_other_vectors(
    web_state, monkeypatch
):
    owner = _indexed_workspace(web_state, monkeypatch)
    before = {chunk["id"]: chunk["embedding"] for chunk in _own_tenant(web_state, owner)["chunks"]}
    asked = web_state.ask_global_ai("มีที่จอดรถไหม", owner)["question"]
    _TopicVectorClient.down = True  # the provider refuses the index update
    result = web_state.answer_assistant_question(
        {"id": asked["id"], "answer": "มีที่จอดหน้าร้าน 5 คัน"}, {"displayName": "God"}
    )
    knowledge = result["knowledge"]
    assert knowledge["indexMode"] == "needs-reindex" and knowledge["chunkCount"] == 4
    assert "rate limit reached" in knowledge["indexError"]
    tenant = _own_tenant(web_state, owner)
    assert (tenant["embeddingProvider"], tenant["embeddingModel"]) == (
        "openai", "text-embedding-3-small"
    )
    # The chunks that did not change keep their vectors; only the new answer waits.
    assert {chunk["id"]: chunk["embedding"] for chunk in tenant["chunks"][:3]} == before
    assert "embedding" not in tenant["chunks"][3] and "5 คัน" in tenant["chunks"][3]["text"]
    # A question meanwhile: the shop info by vector, the new answer by keyword.
    _TopicVectorClient.down = False
    _TopicVectorClient.embedded, _TopicVectorClient.asked = [], []
    sources = web_state.ask_global_ai("ร้านเปิดกี่โมง มีที่จอดรถไหม", owner)["question"]["sources"]
    assert _TopicVectorClient.embedded == [["ร้านเปิดกี่โมง มีที่จอดรถไหม"]]
    assert sources[0] == {"name": "เวลาเปิด", "score": 1.0, "scope": "shop"}
    assert sources[-1]["name"] == "Approved answers" and 0 < sources[-1]["score"] < 1.0
    assert "5 คัน" in _TopicVectorClient.asked[-1]["context"]
    # The next index update that works gives the answer its vector.
    knowledge = web_state.reindex_assistant_knowledge(owner)
    assert (knowledge["indexMode"], knowledge["indexError"]) == ("embedding", "")
    assert all("embedding" in chunk for chunk in _own_tenant(web_state, owner)["chunks"])


def test_shop_info_saved_while_the_search_model_fails_keeps_the_other_vectors(
    web_state, monkeypatch
):
    owner = _indexed_workspace(web_state, monkeypatch)
    before = _own_tenant(web_state, owner)["chunks"]
    _TopicVectorClient.down = True
    sections = [*_SECTIONS[:2], {"id": "park", "title": "ที่จอดรถ", "body": "จอดได้หลังร้าน"}]
    knowledge = web_state.save_assistant_knowledge({"sections": sections}, owner)
    assert knowledge["indexMode"] == "needs-reindex" and "rate limit" in knowledge["indexError"]
    chunks = _own_tenant(web_state, owner)["chunks"]
    assert chunks[:2] == before[:2]  # as they were, vectors included
    assert "embedding" not in chunks[2] and "หลังร้าน" in chunks[2]["text"]
    # Nothing changed and the provider still fails: the index is left as it is.
    again = web_state.reindex_assistant_knowledge(owner)
    assert again["indexMode"] == "needs-reindex" and "rate limit" in again["indexError"]
    assert _own_tenant(web_state, owner)["chunks"] == chunks


def test_a_stored_vector_is_kept_only_for_the_text_it_was_made_for(web_state, monkeypatch):
    owner = _indexed_workspace(web_state, monkeypatch)
    data = web_state._global_ai_all()
    stored = data["tenants"][owner["id"]]["chunks"]
    # A record whose second chunk has the right id and another text (changed by hand).
    stored[1]["text"] = "รับเงินสดเท่านั้น"
    web_state.store.set("global_ai", data)
    _TopicVectorClient.down = True
    knowledge = web_state.reindex_assistant_knowledge(owner)
    assert knowledge["indexMode"] == "needs-reindex"
    chunks = _own_tenant(web_state, owner)["chunks"]
    assert ["embedding" in chunk for chunk in chunks] == [True, False, True]
    assert "รับโอนและบัตรเครดิต" in chunks[1]["text"]  # rebuilt from the shop info


def test_with_the_ai_not_set_up_an_index_missing_vectors_is_keyword_search(
    web_state, monkeypatch
):
    owner = _indexed_workspace(web_state, monkeypatch)
    # The key is removed: nothing changes in the shop info, so the vectors stay.
    web_state.save_assistant_settings(
        {"provider": "openai", "clearApiKey": True, "test": False}, owner
    )
    assert web_state.assistant_settings(owner)["configured"] is False
    assert _own_tenant(web_state, owner)["indexMode"] == "embedding"
    # Shop info saved now: no update could make the missing vector, so nothing waits
    # for one, and no button on the page is left unable to clear a pending update.
    sections = [*_SECTIONS[:2], {"id": "park", "title": "ที่จอดรถ", "body": "จอดได้หลังร้าน"}]
    knowledge = web_state.save_assistant_knowledge({"sections": sections}, owner)
    assert (knowledge["indexMode"], knowledge["indexError"]) == ("keyword", "")
    tenant = _own_tenant(web_state, owner)
    assert all("embedding" not in chunk for chunk in tenant["chunks"])
    assert (tenant["embeddingProvider"], tenant["embeddingModel"]) == ("", "")


def test_waiting_chunks_keep_the_places_keyword_search_gives_them(web_state, monkeypatch):
    owner = _indexed_workspace(web_state, monkeypatch, topK=2)
    data = web_state._global_ai_all()
    tenant = data["tenants"][owner["id"]]
    # Two chunks came in while no vector could be made (as after the move of answers).
    tenant["chunks"] += [
        {"id": "w1", "sourceId": "answer:w1", "sourceName": "วันหยุด",
         "text": "ร้านเปิดทุกวัน ไม่มีวันหยุด"},
        {"id": "w2", "sourceId": "answer:w2", "sourceName": "ส่งของ", "text": "ส่งฟรีทั่วประเทศ"},
    ]
    tenant["indexMode"] = "needs-reindex"
    web_state.store.set("global_ai", data)

    def ask(question):
        _TopicVectorClient.embedded = []
        sources = web_state.ask_global_ai(question, owner)["question"]["sources"]
        return [(source["name"], source["score"]) for source in sources]

    # By vector first; the waiting chunk keyword search would pick keeps its place, and
    # the one it would not pick gets none.
    ranked = ask("ร้านเปิดกี่โมง")
    assert _TopicVectorClient.embedded == [["ร้านเปิดกี่โมง"]]
    assert [name for name, _score in ranked] == ["เวลาเปิด", "วันหยุด"]
    assert ranked[0][1] == 1.0 and 0 < ranked[1][1] < 1.0
    # Nothing about the waiting chunks: vectors only.
    assert ask("จ่ายเงินยังไง โอนได้ไหม") == [("การจ่ายเงิน", 1.0), ("ที่จอดรถ", 1.0)]
    # Every place keyword search gives goes to a waiting chunk: both are found.
    ranked = ask("ส่งฟรีไหม ไม่มีวันหยุดใช่ไหม")
    assert sorted(name for name, _score in ranked) == ["วันหยุด", "ส่งของ"]
    # The question's vector cannot be had: keyword search for every chunk, as before.
    _TopicVectorClient.down = True
    ranked = ask("ร้านเปิดกี่โมง")
    assert [name for name, _score in ranked] == ["เวลาเปิด", "วันหยุด"]
    assert all(0 < score < 1.0 for _name, score in ranked)


def test_an_index_that_says_it_is_whole_is_not_used_with_a_vector_missing(
    web_state, monkeypatch
):
    owner = _indexed_workspace(web_state, monkeypatch)

    def ask(**fields):
        data = web_state._global_ai_all()
        tenant = data["tenants"][owner["id"]]
        tenant.update(fields)
        web_state.store.set("global_ai", data)
        _TopicVectorClient.embedded = []
        return web_state.ask_global_ai("ร้านเปิดกี่โมง", owner)["question"]["sources"]

    data = web_state._global_ai_all()
    chunks = data["tenants"][owner["id"]]["chunks"]
    whole = [dict(chunk) for chunk in chunks]
    missing = [dict(chunk) for chunk in chunks]
    del missing[1]["embedding"]
    # A vector missing from an index marked as whole: keyword search, no vector asked for.
    sources = ask(chunks=missing, indexMode="embedding")
    assert _TopicVectorClient.embedded == [] and sources[0]["score"] < 1.0
    # Marked as waiting for an update: the vectors that are there are used.
    sources = ask(chunks=missing, indexMode="needs-reindex")
    assert _TopicVectorClient.embedded == [["ร้านเปิดกี่โมง"]] and sources[0]["score"] == 1.0
    # A vector of another size, or something that is no vector: keyword search.
    for broken in ([0.5, 0.5, 0.5], [], None, "vector"):
        odd = [dict(chunk) for chunk in whole]
        odd[2]["embedding"] = broken
        for mode in ("embedding", "needs-reindex"):
            sources = ask(chunks=odd, indexMode=mode)
            assert _TopicVectorClient.embedded == [] and sources[0]["score"] < 1.0
    # Vectors of another model are not compared, waiting chunks or not.
    sources = ask(chunks=missing, indexMode="needs-reindex", embeddingModel="another-model")
    assert _TopicVectorClient.embedded == [] and sources[0]["score"] < 1.0


def test_vectors_kept_for_some_chunks_stay_within_the_size_limits(web_state, monkeypatch):
    owner = _indexed_workspace(web_state, monkeypatch)
    _TopicVectorClient.down = True
    monkeypatch.setattr(webapp_module, "MAX_TENANT_VECTOR_VALUES", 7)  # 4 chunks x 2 = 8
    sections = [*_SECTIONS, {"id": "ship", "title": "ส่งของ", "body": "ส่งฟรีทั่วประเทศ"}]
    knowledge = web_state.save_assistant_knowledge({"sections": sections}, owner)
    assert knowledge["indexMode"] == "keyword" and knowledge["chunkCount"] == 4
    tenant = _own_tenant(web_state, owner)
    assert all("embedding" not in chunk for chunk in tenant["chunks"])
    assert (tenant["embeddingProvider"], tenant["embeddingModel"]) == ("", "")


# --- On a real PostgreSQL -------------------------------------------------------


def _reset_database() -> None:
    import psycopg

    with psycopg.connect(PG_URL, autocommit=True) as conn:
        rows = conn.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ).fetchall()
        for (name,) in rows:
            conn.execute(f'DROP TABLE IF EXISTS "{name}" CASCADE')


@pytest.fixture
def pg_state(tmp_path, monkeypatch) -> Iterator[WebState]:
    _reset_database()
    monkeypatch.setattr(webapp_module, "AI_AUTO_REPLY_POLL_SECONDS", 3600.0)
    config = _make_config(tmp_path)
    config.database_url = PG_URL
    state = WebState(config)
    try:
        yield state
    finally:
        state.close()


@on_postgres
def test_postgresql_refuses_what_the_tests_take_it_to_refuse(pg_state):
    assert pg_state.store.label() == "postgresql"
    for value in ({"text": "a\x00b"}, {"text": "a" + _LONE_HIGH}, {"n": float("nan")},
                  {"n": float("inf")}, {"a\x00": 1}):
        assert _postgres_would_refuse(value)
        with pytest.raises(Exception):  # noqa: B017 - psycopg's error, or the codec's
            pg_state.store.set("probe", value)
    for value in ({"text": webapp_module._storable_text("a\x00b" + _LONE_HIGH + _SPLIT_PAIR)},
                  {"text": "tab\there \x01\x1f\x7f" + _NONCHARACTER + " " + _GRIN}):
        assert _postgres_would_refuse(value) == ""
        pg_state.store.set("probe", value)
        assert pg_state.store.get("probe", None) == value
    assert (pg_state.store.holds("probe"), pg_state.store.holds("no_such_key")) == (True, False)


@on_postgres
def test_on_postgresql_an_auto_reply_sends_the_answer_it_stores(pg_state, monkeypatch):
    owner = _workspace_with_ai(pg_state, monkeypatch)
    api = _ReplyApi()
    monkeypatch.setattr(pg_state, "get_api", lambda account_id=None, **kwargs: api)
    _answer_one_message(pg_state, owner)
    assert api.sent == [(_A, _CLEAN_ANSWER)]
    question = pg_state.store.get("global_ai", {})["questions"][-1]
    assert (question["answer"], question["model"]) == (_CLEAN_ANSWER, "model-x")
    actions = [log["action"] for log in pg_state.store.get("bot_logs", {"logs": []})["logs"]]
    assert {"ai.auto_reply.received", "ai.auto_reply.answered", "ai.auto_reply.sent"} <= set(
        actions
    )


@on_postgres
def test_on_postgresql_a_failure_text_with_a_nul_does_not_stop_the_schedules(
    pg_state, monkeypatch
):
    owner = _seed_owned_account(pg_state, _owner(pg_state))
    monkeypatch.setattr(pg_state, "_notify_schedule_failure", lambda *args: None)
    api = _FailingApi()
    api.errors[_A] = RuntimeError("LINE answered\x00 with a broken" + _LONE_LOW + " reply")
    monkeypatch.setattr(pg_state, "get_api", lambda account_id=None, require_auth=True: api)
    job = _job(pg_state)
    with pytest.raises(WebError):
        pg_state._execute_schedule(job["id"], manual=False)
    assert not pg_state.schedules_unsaved
    second = _job(pg_state, name="second")
    saved = _saved_schedules(pg_state)
    assert [item["id"] for item in saved] == [job["id"], second["id"]]
    assert saved[0]["lastError"] == "LINE answered with a broken reply"
    assert pg_state.list_schedules("acct", owner)["schedulesUnsaved"] is False


@on_postgres
def test_on_postgresql_answers_moved_to_the_owner_leave_its_search_vectors(
    tmp_path, monkeypatch
):
    _reset_database()
    monkeypatch.setattr(webapp_module, "AI_AUTO_REPLY_POLL_SECONDS", 3600.0)
    config = _make_config(tmp_path)
    config.database_url = PG_URL
    ids = _seed_staff_answers_install(config, ai_migrated=False)
    state = WebState(config)  # the first start after the update: both steps run
    try:
        stored = state.store.get("global_ai", {})
        assert isinstance(stored[webapp_module.AI_PROVIDERS_MIGRATION_KEY], dict)
        assert stored[webapp_module.STAFF_ANSWERS_MOVED_KEY]["moved"] == 4
        tenant = stored["tenants"][ids["owner"]]
        assert (tenant["embeddingProvider"], tenant["embeddingModel"], tenant["indexMode"]) == (
            "gemini", "gemini-embedding-001", "needs-reindex"
        )
        assert [chunk.get("embedding") for chunk in tenant["chunks"]] == [
            [0.1, 0.2], [0.1, 0.2], None, None, None
        ]
    finally:
        state.close()


@on_postgres
@pytest.mark.parametrize("damaged", ["[]", '"auto replies"', "null", '{"accounts": []}'])
def test_on_postgresql_a_damaged_auto_reply_record_makes_the_ai_move_wait(
    tmp_path, monkeypatch, damaged
):
    import psycopg

    _reset_database()
    monkeypatch.setattr(webapp_module, "AI_AUTO_REPLY_POLL_SECONDS", 3600.0)
    config = _make_config(tmp_path)
    config.database_url = PG_URL
    owners, _legacy = _seed_legacy_install(config)
    with psycopg.connect(PG_URL, autocommit=True) as conn:
        good = conn.execute(
            "SELECT value::text FROM okline_web_state WHERE key = 'ai_auto_reply'"
        ).fetchone()[0]
        conn.execute(
            "UPDATE okline_web_state SET value = %s::jsonb WHERE key = 'ai_auto_reply'",
            (damaged,),
        )
    flag = webapp_module.AI_PROVIDERS_MIGRATION_KEY
    state = WebState(config)
    try:
        stored = state.store.get("global_ai", {})
        assert flag not in stored and "settings" in stored
        assert state.store.holds("ai_auto_reply") is True
    finally:
        state.close()
    with psycopg.connect(PG_URL, autocommit=True) as conn:
        left = conn.execute(
            "SELECT value::text FROM okline_web_state WHERE key = 'ai_auto_reply'"
        ).fetchone()[0]
        assert json.loads(left) == json.loads(damaged)  # left as it is
        conn.execute(
            "UPDATE okline_web_state SET value = %s::jsonb WHERE key = 'ai_auto_reply'",
            (good,),
        )
    state = WebState(config)
    try:
        assert state.store.get("global_ai", {})[flag]["autoRepliesSwitchedOff"] == 3
        auto = state.store.get("ai_auto_reply", {})["accounts"]
        assert auto["acct-ollama"]["disabledReason"] == "ai_removed"
        assert auto["acct-gemini"]["enabled"] is True
        assert owners["ollama"] in state.store.get("global_ai", {})["tenants"]
    finally:
        state.close()
