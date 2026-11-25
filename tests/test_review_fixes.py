"""Faults found when the three changes after the AI providers were reviewed together.

Each test fails on the code as it was merged: a stop handler set after the line a
script waits for, a Run now that did not say its account has to be connected again,
names and ids that kept what a store refuses, and a search that missed shop info it had
vectors for.
"""

from __future__ import annotations

import builtins
import json
import signal
import threading
from http import HTTPStatus
from pathlib import Path

import pytest
from test_schedule_restart import (
    _THREE,
    _killed_after_the_first_chat,
    _round,
    _saved,
    _sent_to,
    _shop,
)
from test_schedule_safety import _A, _B, _job, _only_the_tests_rounds, _SendApi, _state

from okline import webapp as webapp_module
from okline.webapp import AccountStore, WebAuth, WebError, _rank_own_chunks, _send_job_contents

_C = _THREE[2]["mid"]
# Built with chr(): what PostgreSQL's jsonb (a NUL) and every store (half a pair) refuse.
_NUL = chr(0)
_HALF = chr(0xD83D)


@pytest.fixture
def start(tmp_path, monkeypatch):
    """LinePassport on one stored state: each call is a start of it (the one before
    stands for a process that ended). Only the test starts the scheduler's rounds."""
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(webapp_module, "SCHEDULE_TARGET_GAP_SECONDS", (0.0, 0.0))
    started = []

    def begin(api):
        state = _state(tmp_path)
        started.append(state)
        _only_the_tests_rounds(state, monkeypatch)
        monkeypatch.setattr(state, "get_api", lambda account_id=None, require_auth=True: api)
        return state

    yield begin
    for state in started:
        state.close()


# -- stopping -----------------------------------------------------------------------------


def test_the_stop_handlers_are_set_before_the_first_line_is_printed(tmp_path, monkeypatch):
    # A script (and the test that stops a real process) waits for "LinePassport running
    # at" and may send its signal at once: the handler has to be there by then, or the
    # signal kills the process as it did before there was a handler.
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    seen = {}
    real_print = builtins.print

    def spy(*args, **kwargs):
        if args and str(args[0]).startswith("LinePassport running at"):
            seen["handler"] = signal.getsignal(signal.SIGTERM)
        real_print(*args, **kwargs)

    def stopped_at_once(self, *args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(builtins, "print", spy)
    monkeypatch.setattr(webapp_module.OkLineWebServer, "serve_forever", stopped_at_once)
    code = webapp_module.serve(
        host="127.0.0.1",
        port=0,
        tokens_file=str(tmp_path / "tokens.json"),
        state_dir=str(tmp_path / "state"),
        open_browser=False,
    )
    assert code == 0
    handler = seen["handler"]
    assert getattr(handler, "__func__", None) is webapp_module._StopSignals._on_signal
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL  # put back afterwards


def test_a_stop_waits_for_a_run_now_to_one_chat_until_its_result_is_saved(start, monkeypatch):
    # A Run now to one chat is sent in the request's thread, which ends with the
    # process. The stop used to wait only until the message had gone, so the run's end
    # could stay unsaved and the chat read as one that may not have got it.
    api = _SendApi()
    state = start(api)
    _shop(state)
    job = _job(state)
    real_save = state.save_schedules
    blocked, release = threading.Event(), threading.Event()

    def save():
        if api.sent and not release.is_set():
            # The first save after the message went out: the run's end.
            blocked.set()
            assert release.wait(10)
        real_save()

    monkeypatch.setattr(state, "save_schedules", save)
    sender = threading.Thread(target=lambda: state.run_schedule_now(job["id"], "acct"))
    sender.start()
    assert blocked.wait(10)
    closer = threading.Thread(target=state.close)
    closer.start()
    closer.join(0.5)
    try:
        assert closer.is_alive()  # the stop waits for the run's end
    finally:
        release.set()
        sender.join(10)
        closer.join(10)
    assert not sender.is_alive() and not closer.is_alive()
    done = _saved(state, job["id"])
    assert done["sentCount"] == 1 and done["history"][-1]["ok"] is True
    assert "runProgress" not in done


# -- a Run now that was cut short ---------------------------------------------------------


@pytest.mark.parametrize("enabled", [True, False])
def test_a_cut_run_now_says_that_its_account_has_to_be_connected_again(
    start, monkeypatch, enabled
):
    api = _SendApi()
    state = start(api)
    _shop(state)
    job = _job(state, targets=_THREE, enabled=enabled)
    _killed_after_the_first_chat(state, monkeypatch, job, manual=True)
    again = start(api)
    again.account_store.mark_session_reauth_required("acct", "revoked")
    _round(again)
    held = again._find_schedule(job["id"])
    # It waits for the sign-in, and says so, as a scheduled send of the account does
    # (the page shows "login required" before "Part sent, goes on").
    assert held["status"] == "login required"
    assert [chat["state"] for chat in held["runProgress"]["chats"]] == ["sent"]
    assert _sent_to(api) == [_A]
    assert _saved(again, job["id"])["status"] == "login required"
    # Connected again: it goes on with the two chats left, and the notice is gone.
    monkeypatch.setattr(AccountStore, "_session_state", staticmethod(lambda account: "active"))
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]
    done = again._find_schedule(job["id"])
    assert done["status"] != "login required" and "runProgress" not in done
    assert done["history"][-1]["ok"] is True and done["history"][-1]["resumed"] is True


def test_a_run_now_whose_schedule_changed_meanwhile_is_refused_with_its_own_text(
    start, monkeypatch
):
    state = start(_SendApi())
    _shop(state)
    job = _job(state)
    monkeypatch.setattr(
        state,
        "_execute_schedule",
        lambda *args, **kwargs: {"ok": False, "skipped": True, "messageIds": []},
    )
    with pytest.raises(WebError) as refused:
        state.run_schedule_now(job["id"], "acct")
    # Without a code of its own the page showed "That is already in progress."
    assert refused.value.status == HTTPStatus.CONFLICT
    assert refused.value.code == "schedule_changed"
    html = webapp_module.INDEX_HTML
    [line] = [text for text in html.splitlines() if '"errors.schedule_changed":' in text]
    assert "{th: " in line and 'en: "Not sent: the schedule changed meanwhile.' in line


def test_the_thai_texts_of_a_send_cut_short_do_not_call_it_stuck():
    # The page's word for a stuck job is the one in "Clear stuck jobs": a send that goes
    # on by itself must not read as one to clear.
    stuck = "ค้าง"
    html = webapp_module.INDEX_HTML
    assert stuck in html  # the word is still the page's word for stuck jobs
    for key in ("jobs.status_part_sent", "jobs.part_sent_edit", "jobs.part_sent_title"):
        [line] = [text for text in html.splitlines() if f'"{key}":' in text]
        thai = line.split(", en: ")[0]
        assert "{th: " in thai and stuck not in thai, key


# -- what a store refuses -----------------------------------------------------------------


def test_a_message_id_is_kept_without_what_a_store_refuses():
    class Api:
        def send_text(self, to, text):
            return {"id": "m" + _NUL + "1" + _HALF}

    ids = _send_job_contents(Api(), _A, {}, items=[{"kind": "text", "text": "hi"}])
    assert ids == ["m1"]


def test_a_chat_is_handed_to_the_crm_without_what_the_database_refuses(start, monkeypatch):
    state = start(_SendApi())
    owner = _shop(state)
    seen = {}

    def ingest(**values):
        seen.update(values)
        return {"customerId": "c"}

    monkeypatch.setattr(state.crm, "ingest", ingest)
    state._crm_ingest(
        "acct",
        owner_id=owner["id"],
        channel="telegram",
        chat_id="chat-1",
        contact={"externalId": "chat-1", "name": "A" + _NUL + "nn", "username": _HALF + "ann"},
        messages=[
            {"externalId": "m1", "direction": "in", "text": "hi" + _NUL + _HALF, "contentType": 0}
        ],
    )
    assert seen["contact"] == {"externalId": "chat-1", "name": "Ann", "username": "ann"}
    assert seen["messages"][0]["text"] == "hi" and seen["account_id"] == "acct"


def test_an_account_is_added_without_what_a_store_refuses_in_its_profile(start):
    state = start(_SendApi())
    owner = _shop(state)

    class Api:
        def save_tokens(self, path):
            Path(path).write_text("{}", encoding="utf-8")

    account = state.account_store.add_from_api(
        Api(),
        {"mid": "u" + "9" * 32, "displayName": "Sh" + _NUL + "op" + _HALF},
        None,
        owner["id"],
    )
    assert account["label"] == "Shop"
    stored = json.dumps(state.account_store.data)
    assert "\\u0000" not in stored and "\\ud83d" not in stored
    # A name that is nothing else gets the usual one.
    other = state.account_store.add_from_api(
        Api(), {"mid": "u" + "8" * 32, "displayName": _NUL}, _NUL + " ", owner["id"]
    )
    assert other["label"] == "Line Account"


def test_a_name_typed_at_sign_up_is_kept_without_what_a_store_refuses(start):
    state = start(_SendApi())
    _shop(state)
    state.web_auth.register("new@example.com", "new-user-pass-1", "N" + _NUL + "ew" + _HALF)
    user = state.web_auth._find_user_by_username("new@example.com")
    assert user["displayName"] == "New"
    renamed = state.account_store.update_label("acct", "Sh" + _NUL + "op")
    assert renamed["label"] == "Shop"


def test_a_user_that_cannot_be_saved_is_not_kept_in_memory(start, monkeypatch):
    # Kept, it failed every later save of the sign-ins: nobody could sign in until a
    # restart, and where sign-up is open a visitor could cause that.
    state = start(_SendApi())
    _shop(state)
    users_before = len(state.web_auth.data["users"])
    sessions_before = json.dumps(state.web_auth.data.get("sessions"), sort_keys=True)
    real_save = state.web_auth._save

    def refused():
        raise RuntimeError("the store refuses a value")

    monkeypatch.setattr(state.web_auth, "_save", refused)
    with pytest.raises(RuntimeError):
        state.web_auth.register("x@example.com", "x-user-pass-1", "X")
    monkeypatch.setattr(state.web_auth, "_save", real_save)
    assert len(state.web_auth.data["users"]) == users_before
    assert state.web_auth._find_user_by_username("x@example.com") is None
    assert json.dumps(state.web_auth.data.get("sessions"), sort_keys=True) == sessions_before
    # The same sign-up works once the store takes it.
    token = state.web_auth.register("x@example.com", "x-user-pass-1", "X")
    user = state.web_auth.current_user(WebAuth.cookie_header(token))
    assert user["username"] == "x@example.com"


# -- search -------------------------------------------------------------------------------


def test_a_question_no_stored_vector_matches_still_finds_shop_info_by_its_words():
    # An index that waits for some vectors: when no stored vector says anything for the
    # question, the parts that have vectors were passed over, also those whose words
    # match.
    chunks = [
        {"id": "a", "text": "opening hours are nine to six every day", "embedding": [1.0, 0.0]},
        {"id": "b", "text": "delivery takes two days", "embedding": [1.0, 0.0]},
        {"id": "c", "text": "parking is behind the building"},
    ]
    found = _rank_own_chunks("opening hours", chunks, query_embedding=[0.0, 1.0], top_k=3)
    assert [item["text"] for item in found][:1] == ["opening hours are nine to six every day"]
    # A vector that does match still ranks first, with the waiting part where keyword
    # search puts it.
    found = _rank_own_chunks("parking", chunks, query_embedding=[1.0, 0.0], top_k=3)
    assert {item["text"] for item in found[:2]} == {
        "opening hours are nine to six every day",
        "delivery takes two days",
    }
    assert found[-1]["text"] == "parking is behind the building"
