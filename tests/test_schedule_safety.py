"""Scheduled sends: limits, jitter, pause, many chats, retry, calendar, defaults."""

from __future__ import annotations

import base64
import copy
import datetime as dt
import sys
import threading
import time

import pytest

from okline import webapp as webapp_module
from okline.webapp import WebAuth, WebConfig, WebError, WebState, _schedule_from_body

_A = "u" + "1" * 32
_B = "u" + "2" * 32


def _state(tmp_path) -> WebState:
    root = tmp_path / ".okline"
    return WebState(
        WebConfig(
            host="127.0.0.1",
            port=0,
            tokens_file=str(tmp_path / "tokens.json"),
            state_dir=str(root),
            accounts_file=str(root / "accounts.json"),
            accounts_dir=str(root / "accounts"),
            schedules_file=str(root / "schedules.json"),
            auth_file=str(root / "auth.json"),
        )
    )


class _SendApi:
    def __init__(self, fail_times=0, fail_for=()):
        self.sent = []
        self.fail_times = fail_times
        self.fail_for = set(fail_for)

    def send_text(self, to, text):
        if self.fail_times > 0:
            self.fail_times -= 1
            raise RuntimeError("temporary network error")
        if to in self.fail_for:
            raise RuntimeError("blocked")
        self.sent.append(("text", to, text))
        return {"id": f"m{len(self.sent)}"}

    def send_image(self, to, data, name=None):
        self.sent.append(("image", to, name))
        return {"id": f"i{len(self.sent)}"}


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    state = _state(tmp_path)
    token = state.web_auth.setup("owner@example.com", "owner-pass")
    owner = state.web_auth.current_user(WebAuth.cookie_header(token))
    state.account_store.data["accounts"] = [
        {"id": "acct", "label": "Shop", "mid": "U-me", "ownerId": owner["id"], "tokenFile": ""}
    ]
    state.account_store.save()
    state.web_auth.grant_account_access(owner["id"], "acct")
    monkeypatch.setattr(webapp_module, "SCHEDULE_TARGET_GAP_SECONDS", (0.0, 0.0))
    api = _SendApi()
    monkeypatch.setattr(state, "get_api", lambda account_id=None, require_auth=True: api)
    try:
        yield state, state.web_auth._find_user(owner["id"]), api
    finally:
        state.close()


def _job(state, **body):
    payload = {
        "accountId": "acct",
        "name": "promo",
        "to": _A,
        "text": "hi {name}",
        "mode": "once",
        "runAt": "2026-01-01T09:00",
    }
    payload.update(body)
    return state.create_schedule(payload)["schedule"]


def test_default_window_is_whole_day_with_inclusive_end():
    job = _schedule_from_body(
        {"to": _A, "text": "x", "mode": "repeat", "intervalMinutes": 60}, account_id="acct"
    )
    assert (job["windowStart"], job["windowEnd"]) == ("00:00", "23:59")
    late = dt.datetime(2027, 1, 4, 23, 59, 30)
    assert webapp_module._next_repeat_epoch(job, late) == late.timestamp()


def test_monthly_days_include_last_day_of_month():
    job = _schedule_from_body(
        {
            "to": _A,
            "text": "x",
            "mode": "repeat",
            "windowStart": "10:00",
            "windowEnd": "10:00",
            "intervalMinutes": 1440,
            "monthDays": [1, -1],
        },
        account_id="acct",
    )
    after = dt.datetime(2027, 2, 2, 0, 0)
    nxt = dt.datetime.fromtimestamp(webapp_module._next_repeat_epoch(job, after))
    assert nxt == dt.datetime(2027, 2, 28, 10, 0)
    with pytest.raises(WebError):
        webapp_module._schedule_month_days([0])


def test_many_chats_get_their_own_name_and_count_toward_limits(ctx):
    state, _owner, api = ctx
    job = _job(state, targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}])
    assert job["to"] == _A and job["targetName"] == "Ann"
    result = state._execute_schedule(job["id"], manual=False)
    assert result["ok"] is True and len(result["messageIds"]) == 2
    assert [entry[1:] for entry in api.sent] == [(_A, "hi Ann"), (_B, "hi Bob")]
    assert state.schedule_usage("acct")["lastHour"] == 2
    with pytest.raises(WebError):
        _job(state, targets=[{"mid": f"u{n:032d}", "name": str(n)} for n in range(21)])


def test_partial_failure_keeps_successes_and_reports_the_rest(ctx, monkeypatch):
    state, _owner, api = ctx
    api.fail_for = {_B}
    notified = []
    monkeypatch.setattr(state, "_notify_schedule_failure", lambda *args: notified.append(args))
    job = _job(state, targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}])
    state._execute_schedule(job["id"], manual=False)
    stored = state._find_schedule(job["id"])
    assert stored["status"] == "error" and "1/2" in stored["lastError"]
    assert len(api.sent) == 1 and notified


def test_ticked_patterns_and_typed_versions_share_one_random_pool(ctx, monkeypatch):
    state, _owner, api = ctx
    job = _job(
        state,
        text="typed",
        customTexts=["typed"],
        patternIds=["p1", "p2"],
        patternTexts=["typed", "saved one", "saved two"],
    )
    stored = state._find_schedule(job["id"])
    assert stored["customTexts"] == ["typed"] and stored["patternIds"] == ["p1", "p2"]
    monkeypatch.setattr(webapp_module.random, "choice", lambda items: items[-1])
    state._execute_schedule(job["id"], manual=False)
    assert api.sent == [("text", _A, "saved two")]
    html = webapp_module.INDEX_HTML
    assert "syncWizardPatterns" not in html  # ticking never rewrites the library


def test_text_job_can_carry_a_picture(ctx):
    state, _owner, api = ctx
    png = base64.b64encode(b"\x89PNG....").decode()
    job = _job(state, attachImage=True, imageData=png, imageName="promo.png")
    state._execute_schedule(job["id"], manual=False)
    assert [entry[0] for entry in api.sent] == ["text", "image"]
    with pytest.raises(WebError):
        _job(state, attachImage=True, imageData=png, encrypt=True)


def test_over_the_limit_jobs_wait_in_the_queue(ctx):
    state, owner, api = ctx
    state.save_schedule_limits(
        {"accountId": "acct", "maxPerHour": 1, "maxPerDay": 10, "jitterMinutes": 0}, owner
    )
    state.record_schedule_sends("acct", 1)
    job = _job(state)
    state.run_due_schedules()
    stored = state._find_schedule(job["id"])
    assert stored["status"] == "queued"
    assert stored["nextRunAt"] > time.time() + 200
    assert api.sent == []


def test_pause_all_holds_jobs_and_resume_restarts_repeats_from_now(ctx):
    state, owner, api = ctx
    state.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
    job = _job(state)
    state.run_due_schedules()
    assert job["id"] not in state.schedule_inflight and api.sent == []
    repeat = _job(state, mode="repeat", intervalMinutes=60, name="hourly")
    state._find_schedule(repeat["id"])["nextRunAt"] = time.time() - 7200
    resumed = time.time() - 0.001  # (a next run is cut to the microsecond)
    payload = state.set_schedules_paused({"accountId": "acct", "paused": False}, owner)
    assert payload["settings"]["paused"] is False
    assert state._find_schedule(repeat["id"])["nextRunAt"] >= resumed


def test_jitter_delays_up_to_the_setting_unless_exact_time(ctx):
    state, owner, _api = ctx
    state.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
    loose = _job(state, name="loose")
    exact = _job(state, name="exact", exactTime=True)
    state.run_due_schedules()
    assert 0 <= state._find_schedule(loose["id"])["jitter"] <= 180
    assert state._find_schedule(exact["id"])["jitter"] == 0


def test_failed_job_retries_once_then_alerts(ctx, monkeypatch):
    state, _owner, api = ctx
    notified = []
    monkeypatch.setattr(state, "_notify_schedule_failure", lambda *args: notified.append(args))
    api.fail_times = 2
    job = _job(state, mode="repeat", intervalMinutes=60)
    with pytest.raises(WebError):
        state._execute_schedule(job["id"], manual=False)
    stored = state._find_schedule(job["id"])
    assert stored["status"] == "retrying" and not notified
    assert stored["nextRunAt"] > time.time() + 200
    with pytest.raises(WebError):
        state._execute_schedule(job["id"], manual=False)
    assert state._find_schedule(job["id"])["status"] == "error"
    assert len(notified) == 1


def test_upcoming_calendar_lists_next_seven_days(ctx):
    state, owner, _api = ctx
    _job(
        state,
        mode="repeat",
        windowStart="09:00",
        windowEnd="09:00",
        intervalMinutes=1440,
        name="daily",
    )
    events = state.upcoming_schedules("acct", owner, days=7)["events"]
    assert 7 <= len(events) <= 8
    assert all(event["name"] == "daily" for event in events)


def test_limits_are_owner_only(ctx):
    state, owner, _api = ctx
    staff = state.web_auth.create_staff(owner, "s@example.com", "staff-pass", "S", ["acct"])
    staff_user = state.web_auth._find_user(staff["id"])
    with pytest.raises(WebError):
        state.save_schedule_limits(
            {"accountId": "acct", "maxPerHour": 5, "maxPerDay": 10, "jitterMinutes": 1},
            staff_user,
        )
    # Staff may still pause everything in an emergency.
    assert state.set_schedules_paused({"accountId": "acct", "paused": True}, staff_user)[
        "settings"
    ]["paused"]


def test_scheduler_ui_exposes_the_safety_controls():
    html = webapp_module.INDEX_HTML
    for element in (
        'id="jobsUsage"',
        'id="jobsPauseButton"',
        'id="jobsLimitsCard"',
        'id="jobsCalendar"',
        'id="wizardPatternList"',
        'id="wizardPatternCategory"',
        'id="wizardAttachDetails"',
        'id="wizardMonthDay"',
        'id="wizardClash"',
        'id="wizardExactTime"',
        'data-wizard-freq="monthly"',
    ):
        assert element in html, element
    assert 'id="wizardWindowStart" type="time" step="1" value="00:00:00"' in html
    assert 'id="wizardWindowEnd" type="time" step="1" value="23:59:59"' in html
    assert "const MAX_WIZARD_TARGETS = 20;" in html
    assert '"/api/schedules/pause-all"' in html and '"/api/schedules/limits"' in html
    assert "/api/schedules/upcoming?" in html


def _store_fails(state, monkeypatch, failing):
    """Saves of the keys ``failing`` says fail (a full disk)."""
    original = state.store.set

    def store_set(key, value):
        if failing(key):
            raise OSError(28, "No space left on device")
        return original(key, value)

    monkeypatch.setattr(state.store, "set", store_set)


def _wait_until(condition, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


@pytest.mark.parametrize("mode", ["once", "repeat"])
def test_a_run_whose_save_fails_after_sending_is_not_sent_again(ctx, monkeypatch, mode):
    state, owner, api = ctx
    job = _job(state, mode=mode, intervalMinutes=60,
               targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}])
    full = {"on": False}
    _store_fails(state, monkeypatch, lambda key: full["on"] and key == "schedules")
    original_send = api.send_text

    def send(to, text):
        result = original_send(to, text)
        if to == _B:
            full["on"] = True  # the disk fills as the last message goes out
        return result

    api.send_text = send
    result = state._execute_schedule(job["id"], manual=False)
    assert result["ok"] is True and len(api.sent) == 2
    stored = state._find_schedule(job["id"])
    assert stored["status"] != "retrying" and not stored.get("retrying")
    assert stored["sentCount"] == 1 and stored["lastError"] is None
    assert not stored["running"] and state.schedules_unsaved
    # The schedules page says so, and that a restart now would send it again.
    listing = state.list_schedules("acct", owner)
    assert listing["schedulesUnsaved"] is True and listing["unsavedSent"] == [job["id"]]
    # Room again: the scheduler's next round saves the run and sends nothing again.
    full["on"] = False
    state.run_due_schedules()
    assert not state.schedules_unsaved and state.schedule_inflight == set()
    listing = state.list_schedules("acct", owner)
    assert listing["schedulesUnsaved"] is False and listing["unsavedSent"] == []
    [saved] = [
        item for item in state.store.get("schedules", {"schedules": []})["schedules"]
        if item["id"] == job["id"]
    ]
    assert saved["sentCount"] == 1 and not saved["running"]
    assert saved["enabled"] is (mode == "repeat")
    time.sleep(0.1)
    assert len(api.sent) == 2


def test_a_send_that_cannot_be_counted_still_counts_as_sent(ctx, monkeypatch):
    state, _owner, api = ctx
    job = _job(state, mode="repeat", intervalMinutes=60)
    _store_fails(state, monkeypatch, lambda key: key.startswith("schedule_sends:"))
    result = state._execute_schedule(job["id"], manual=False)
    assert result["ok"] is True and len(api.sent) == 1
    stored = state._find_schedule(job["id"])
    assert stored["status"] == "waiting" and stored["lastError"] is None
    assert state.schedule_usage("acct")["lastHour"] == 1  # still counted, in memory


def test_a_run_that_cannot_be_saved_first_sends_nothing_and_waits(ctx, monkeypatch):
    state, owner, api = ctx
    job = _job(state)
    status = state._find_schedule(job["id"])["status"]
    full = {"on": True}
    _store_fails(state, monkeypatch, lambda key: full["on"] and key == "schedules")
    with pytest.raises(WebError) as refused:
        state._execute_schedule(job["id"], manual=False)
    assert refused.value.code == "schedules_unsaved"
    stored = state._find_schedule(job["id"])
    assert api.sent == [] and not stored.get("running") and stored["status"] == status
    # The schedules page says the schedules cannot be saved; the job's own error would
    # show the server's error text (with its file names).
    assert not stored.get("lastError")
    assert state.list_schedules("acct", owner)["schedulesUnsaved"] is True
    # The scheduler's rounds start nothing while saving fails, and keep nothing marked.
    for _ in range(2):
        state.run_due_schedules()
        assert state.schedule_inflight == set() and api.sent == []
    full["on"] = False
    state.run_due_schedules()
    assert _wait_until(lambda: len(api.sent) == 1)
    assert _wait_until(lambda: state._find_schedule(job["id"])["status"] == "completed")
    assert state.list_schedules("acct", owner)["schedulesUnsaved"] is False


def test_schedule_changes_that_cannot_be_saved_are_not_kept(ctx, monkeypatch):
    state, _owner, _api = ctx
    job = _job(state)
    before = [dict(item) for item in state.schedules]
    _store_fails(state, monkeypatch, lambda key: key == "schedules")
    with pytest.raises(OSError):
        _job(state, name="second")
    with pytest.raises(OSError):
        state.update_schedule(
            job["id"],
            {"accountId": "acct", "name": "renamed", "to": _A, "text": "x", "mode": "once",
             "runAt": "2026-01-01T09:00"},
        )
    with pytest.raises(OSError):
        state.toggle_schedule(job["id"], False)
    with pytest.raises(OSError):
        state.delete_schedule(job["id"])
    assert state.schedules == before


def test_a_round_that_fails_partway_keeps_none_of_its_schedules_marked(ctx, monkeypatch):
    # A read that fails partway through a round (a database that dropped the
    # connection) fails the round, and leaves none of its schedules marked as running:
    # the next round sends them all.
    state, _owner, api = ctx
    _job(state, name="first")
    _job(state, name="second", to=_B)
    quota_left = state._schedule_quota_left
    calls = {"n": 0}

    def flaky(account_id, *settings):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("connection to the database was lost")
        return quota_left(account_id, *settings)

    monkeypatch.setattr(state, "_schedule_quota_left", flaky)
    with pytest.raises(OSError):
        state.run_due_schedules()
    assert state.schedule_inflight == set() and api.sent == []
    state.run_due_schedules()
    assert _wait_until(lambda: len(api.sent) == 2)


def test_clear_stuck_and_resume_all_that_cannot_be_saved_change_nothing(ctx, monkeypatch):
    state, owner, _api = ctx
    job = _job(state, mode="repeat", intervalMinutes=60)
    stored = state._find_schedule(job["id"])
    stored["status"] = "error"
    stored["lastError"] = "an earlier failure"
    state.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
    stored["nextRunAt"] = time.time() - 3600  # due while the account was paused
    state.save_schedules()
    before = copy.deepcopy(state.schedules)
    logs = len(state.store.get("bot_logs", {"logs": []})["logs"])
    _store_fails(state, monkeypatch, lambda key: key == "schedules")
    with pytest.raises(OSError):
        state.clear_stuck_schedules("acct", owner)
    with pytest.raises(OSError):
        state.set_schedules_paused({"accountId": "acct", "paused": False}, owner)
    assert state.schedules == before
    assert state.schedule_settings_for("acct")["paused"] is True
    assert len(state.store.get("bot_logs", {"logs": []})["logs"]) == logs


def test_a_text_whose_picture_fails_counts_as_sent_and_is_not_sent_again(ctx):
    state, _owner, api = ctx
    job = _job(
        state,
        attachImage=True,
        imageData=base64.b64encode(b"picture").decode(),
        imageName="sale.png",
        targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}],
    )

    def upload_fails(to, data, name=None):
        raise RuntimeError("the picture upload failed")

    api.send_image = upload_fails
    assert state._execute_schedule(job["id"], manual=False)["ok"] is True
    assert [kind for kind, _to, _text in api.sent] == ["text", "text"]
    stored = state._find_schedule(job["id"])
    assert not stored.get("retrying") and stored["status"] == "error"
    assert stored["lastError"] == (
        "Sent to 2/2 chats. Ann: only part of it went out (the picture upload failed); "
        "Bob: only part of it went out (the picture upload failed)"
    )
    state.run_due_schedules()
    time.sleep(0.2)
    assert len(api.sent) == 2  # no retry sends the texts again


def test_a_send_cut_short_by_a_new_sign_in_is_not_sent_again_to_those_who_got_it(ctx):
    state, _owner, api = ctx
    job = _job(state, targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}])
    original = api.send_text

    def send(to, text):
        if to == _B:
            # LINE revokes the session after the first chat (a sign-in elsewhere).
            state.account_store.mark_session_reauth_required("acct", "revoked")
            raise webapp_module.LineAuthError("session revoked")
        return original(to, text)

    api.send_text = send
    assert state._execute_schedule(job["id"], manual=False)["ok"] is True
    assert api.sent == [("text", _A, "hi Ann")]
    stored = state._find_schedule(job["id"])
    assert stored["lastError"] == "Sent to 1/2 chats. Bob: not sent, LINE asked to sign in again"
    assert stored["enabled"] is False  # this once job is done
    # Connected again: the chat that got it does not get it again.
    state.account_store.mark_session_connected("acct")
    api.send_text = original
    state.run_due_schedules()
    time.sleep(0.2)
    assert api.sent == [("text", _A, "hi Ann")]


def test_a_send_stopped_partway_names_the_chats_it_did_not_reach(ctx):
    state, _owner, api = ctx
    job = _job(state, targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}])
    original = api.send_text

    def send(to, text):
        result = original(to, text)
        state.scheduler.stop_event.set()  # Ctrl-C as the first message goes out
        return result

    api.send_text = send
    assert state._execute_schedule(job["id"], manual=False)["ok"] is True
    stored = state._find_schedule(job["id"])
    assert len(api.sent) == 1 and not stored["running"]
    assert stored["lastError"] == "Sent to 1/2 chats. Bob: not sent, LinePassport stopped"
    # Stopped before anything went out: not run, so due again once LinePassport starts.
    later = _job(state, name="later")
    status = state._find_schedule(later["id"])["status"]
    result = state._execute_schedule(later["id"], manual=False)
    stored = state._find_schedule(later["id"])
    assert result["ok"] is False and len(api.sent) == 1
    assert stored["enabled"] and stored["status"] == status and not stored.get("running")
    assert not stored.get("lastError") and not stored.get("history")
    [saved] = [
        item for item in state.store.get("schedules", {"schedules": []})["schedules"]
        if item["id"] == later["id"]
    ]
    assert not saved.get("running") and saved["enabled"]


def test_a_run_ends_when_the_server_log_cannot_be_written_either(ctx, monkeypatch):
    # A full disk can fail the server log (stderr sent to a file) as well as the
    # activity log: the run still ends, and counts as sent.
    state, _owner, api = ctx
    job = _job(state)

    class _FullDisk:
        def write(self, text):
            raise OSError(28, "No space left on device")

        def flush(self):
            raise OSError(28, "No space left on device")

    _store_fails(
        state, monkeypatch,
        lambda key: key == "bot_logs" or key.startswith("schedule_sends:"),
    )
    monkeypatch.setattr(sys, "stderr", _FullDisk())
    result = state._execute_schedule(job["id"], manual=False)
    stored = state._find_schedule(job["id"])
    assert result["ok"] is True and len(api.sent) == 1
    assert not stored["running"] and stored["status"] == "completed"


def test_the_schedules_page_says_when_the_schedules_cannot_be_saved():
    html = webapp_module.INDEX_HTML
    assert "function scheduleSaveState(data)" in html
    assert "state.schedulesUnsaved = unsaved;" in html
    assert "state.schedulesUnsaved = scheduleSaveState(data);" in html
    for key in ("jobs.unsaved_banner", "jobs.unsaved_sent", "jobs.quoted",
                "errors.schedules_unsaved"):
        assert f'"{key}": {{th: "' in html, key


def test_resume_all_whose_setting_cannot_be_saved_keeps_its_jobs_as_they_were(ctx, monkeypatch):
    # The jobs' new times are saved, then saving that the account runs again fails: the
    # account stays paused, and its jobs keep the times they had.
    state, owner, _api = ctx
    job = _job(state, mode="repeat", intervalMinutes=60)
    state.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
    state._find_schedule(job["id"])["nextRunAt"] = time.time() - 3600  # due while paused
    state.save_schedules()
    before = copy.deepcopy(state.schedules)
    _store_fails(state, monkeypatch, lambda key: key == "schedule_settings")
    with pytest.raises(OSError):
        state.set_schedules_paused({"accountId": "acct", "paused": False}, owner)
    assert state.schedules == before
    assert state.schedule_settings_for("acct")["paused"] is True
    saved = state.store.get("schedules", {"schedules": []})["schedules"]
    assert [item["nextRunAt"] for item in saved] == [item["nextRunAt"] for item in before]


def test_a_send_cut_short_by_a_sign_in_still_names_the_chats_it_missed(ctx):
    # A repeating schedule that LINE's sign-in cut short: the next round marks it as
    # waiting for the account, and it still names the chats it did not reach.
    state, _owner, api = ctx
    job = _job(state, mode="repeat", intervalMinutes=60,
               targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}])
    original = api.send_text

    def send(to, text):
        if to == _B:
            state.account_store.mark_session_reauth_required("acct", "revoked")
            raise webapp_module.LineAuthError("session revoked")
        return original(to, text)

    api.send_text = send
    assert state._execute_schedule(job["id"], manual=False)["ok"] is True
    report = "Sent to 1/2 chats. Bob: not sent, LINE asked to sign in again"
    assert state._find_schedule(job["id"])["lastError"] == report
    state.run_due_schedules()
    stored = state._find_schedule(job["id"])
    assert stored["status"] == "login required" and stored["lastError"] == report


def test_a_run_now_that_could_not_begin_is_refused_rather_than_queued(ctx, monkeypatch):
    # While the schedules cannot be saved, a Run now of several chats would be queued and
    # then dropped without a word: it is refused at once, and queued once a save works.
    state, _owner, api = ctx
    job = _job(state, targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}])
    full = {"on": True}
    _store_fails(state, monkeypatch, lambda key: full["on"] and key == "schedules")
    with pytest.raises(OSError):
        state.save_schedules()
    assert state.schedules_unsaved
    with pytest.raises(WebError) as refused:
        state.run_schedule_now(job["id"], "acct")
    assert refused.value.code == "schedules_unsaved"
    assert state.schedule_inflight == set() and api.sent == []
    full["on"] = False
    assert state.run_schedule_now(job["id"], "acct")["queued"] is True
    assert _wait_until(lambda: len(api.sent) == 2)


def test_a_one_chat_run_now_that_did_not_go_out_says_so(ctx):
    # LinePassport began to stop before the chat had its turn: nothing went out, and the
    # answer says so instead of looking like a send.
    state, _owner, api = ctx
    job = _job(state)
    state.scheduler.stop_event.set()
    with pytest.raises(WebError) as refused:
        state.run_schedule_now(job["id"], "acct")
    assert refused.value.code == "server_stopping" and api.sent == []
    stored = state._find_schedule(job["id"])
    assert not stored.get("running") and not stored.get("history")


def test_the_schedules_page_tells_a_queued_run_from_a_sent_one():
    html = webapp_module.INDEX_HTML
    assert 'toast(t(data && data.queued ? "toast.schedule_queued" : "toast.schedule_sent"));' in html
    assert r'const partial = /^Sent to \d+\/\d+ chats\. /.test(job.lastError || "");' in html
    for key in ("toast.schedule_queued", "errors.server_stopping"):
        assert f'"{key}": {{th: "' in html, key
    assert webapp_module._SCHEDULE_PARTIAL_RE.match(
        "Sent to 1/2 chats. Bob: not sent, LINE asked to sign in again"
    )


class _FullDiskLog:
    """A server log on the full disk: writing to it fails."""

    def write(self, text):
        raise OSError(28, "No space left on device")

    def flush(self):
        raise OSError(28, "No space left on device")


@pytest.mark.parametrize(
    ("engine", "method"),
    [
        ("ScheduleEngine", "run_due_schedules"),
        ("BroadcastRunner", "run_due_broadcasts"),
        ("AiAutoReplyEngine", "poll_ai_auto_replies"),
        ("SessionKeeper", "keep_sessions_fresh"),
    ],
)
def test_an_engine_goes_on_when_a_round_fails_and_the_log_cannot_be_written(
    monkeypatch, engine, method
):
    # A round fails (a read the database refused) and so does logging it (the server log
    # is a file on the full disk): the engine's next rounds still run.
    rounds = []

    class Host:
        pass

    def failing_round():
        rounds.append(1)
        raise OSError("connection refused")

    host = Host()
    setattr(host, method, failing_round)

    class Stop:
        waits = 0

        def wait(self, seconds):
            Stop.waits += 1
            return Stop.waits > 3

    runner = getattr(webapp_module, engine).__new__(getattr(webapp_module, engine))
    runner.state = host
    runner.stop_event = Stop()
    monkeypatch.setattr(sys, "stderr", _FullDiskLog())
    runner._run()
    assert len(rounds) == 3


def test_a_damaged_state_file_reads_as_empty_when_the_log_cannot_be_written(
    tmp_path, monkeypatch
):
    path = tmp_path / "kv" / "settings.json"
    path.parent.mkdir()
    path.write_bytes(b'{"half a file')
    monkeypatch.setattr(sys, "stderr", _FullDiskLog())
    assert webapp_module._read_json_file(str(path), {"empty": True}) == {"empty": True}


def test_a_scheduler_round_and_an_account_removal_do_not_wait_for_each_other(
    ctx, monkeypatch
):
    # A round holds the schedules' lock while it reads (a slow database here), and the
    # removal of another account takes the accounts' lock and then the schedules':
    # the round must not need the accounts' lock meanwhile, or both wait for good.
    state, owner, _api = ctx
    state.account_store.data["accounts"].append(
        {"id": "acct-2", "label": "Shop 2", "mid": "U-two", "ownerId": owner["id"],
         "tokenFile": ""}
    )
    state.account_store.save()
    state.web_auth.grant_account_access(owner["id"], "acct-2")
    _job(state, name="first", mode="repeat", intervalMinutes=60)
    _job(state, name="second", mode="repeat", intervalMinutes=60, accountId="acct-2")
    in_read = threading.Event()
    real_settings = state.schedule_settings_for

    def slow_settings(account_id):
        if account_id == "acct" and not in_read.is_set():
            in_read.set()
            time.sleep(0.5)
        return real_settings(account_id)

    monkeypatch.setattr(state, "schedule_settings_for", slow_settings)
    done = {}

    def scheduler_round():
        state.run_due_schedules()
        done["round"] = True

    def removal():
        in_read.wait(5)
        state._commit_account_removal("acct-2")
        done["removal"] = True

    threads = [threading.Thread(target=scheduler_round, daemon=True),
               threading.Thread(target=removal, daemon=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert done == {"round": True, "removal": True}
    assert state.account_store.get("acct-2") is None


@pytest.mark.parametrize("action", ["list", "update"])
def test_the_schedules_page_and_an_account_removal_do_not_wait_for_each_other(
    ctx, monkeypatch, action
):
    # Listing or editing schedules holds the schedules' lock for a moment while another
    # account is removed (a removal takes the accounts' lock, then the schedules'):
    # neither may then wait for the other for good.
    state, owner, _api = ctx
    state.account_store.data["accounts"].append(
        {"id": "acct-2", "label": "Shop 2", "mid": "U-two", "ownerId": owner["id"],
         "tokenFile": ""}
    )
    state.account_store.save()
    state.web_auth.grant_account_access(owner["id"], "acct-2")
    job = _job(state, name="first", mode="repeat", intervalMinutes=60)
    inside = threading.Event()

    def slowly(real):
        def call(*args):
            if not inside.is_set():
                inside.set()
                time.sleep(0.5)  # while the schedules' lock is held
            return real(*args)

        return call

    if action == "list":
        monkeypatch.setattr(
            state.web_auth, "can_access_account", slowly(state.web_auth.can_access_account)
        )

        def first():
            state.list_schedules("acct", owner)
    else:
        monkeypatch.setattr(state, "_find_schedule", slowly(state._find_schedule))

        def first():
            state.update_schedule(
                job["id"],
                {"accountId": "acct", "name": "renamed", "to": _A, "text": "x",
                 "mode": "once", "runAt": "2026-01-01T09:00"},
            )

    done = {}

    def page():
        first()
        done["page"] = True

    def removal():
        inside.wait(5)
        state._commit_account_removal("acct-2")
        done["removal"] = True

    threads = [threading.Thread(target=page, daemon=True),
               threading.Thread(target=removal, daemon=True)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
    assert done == {"page": True, "removal": True}


def test_an_edit_saved_as_its_account_is_removed_does_not_turn_the_schedule_on(ctx):
    # The removal commits just before the edit takes the schedules' lock: the edit is
    # refused, and the removed account's schedule stays off.
    state, _owner, _api = ctx
    job = _job(state, mode="repeat", intervalMinutes=60)
    real_lock = state.schedule_lock
    removed = {}

    class RemovalFirst:
        """The schedules' lock, with the account removed before its first use."""

        def __enter__(self):
            if not removed:
                removed["done"] = True
                state._commit_account_removal("acct")
            return real_lock.__enter__()

        def __exit__(self, *exc):
            return real_lock.__exit__(*exc)

    state.schedule_lock = RemovalFirst()
    try:
        with pytest.raises(WebError) as refused:
            state.update_schedule(
                job["id"],
                {"accountId": "acct", "name": "renamed", "to": _A, "text": "x",
                 "mode": "repeat", "intervalMinutes": 60},
            )
    finally:
        state.schedule_lock = real_lock
    assert removed and refused.value.status == 400
    stored = state._find_schedule(job["id"])
    assert stored["enabled"] is False and stored["status"] == "account deleted"


def test_a_run_now_whose_start_cannot_be_saved_is_refused_rather_than_queued(
    ctx, monkeypatch
):
    # The first save to fail is the Run now's own start (nothing had tried to save
    # before): it is refused in the answer, not queued and lost.
    state, _owner, api = ctx
    job = _job(state, targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}])
    assert not state.schedules_unsaved
    _store_fails(state, monkeypatch, lambda key: key == "schedules")
    with pytest.raises(WebError) as refused:
        state.run_schedule_now(job["id"], "acct")
    assert refused.value.code == "schedules_unsaved"
    assert state.schedule_inflight == set() and api.sent == []
    assert not state._find_schedule(job["id"]).get("running")


def test_a_queued_run_now_that_sent_nothing_before_a_stop_says_so_on_the_job(ctx):
    state, _owner, api = ctx
    job = _job(state, targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}])
    state.scheduler.stop_event.set()  # LinePassport begins to stop
    assert state.run_schedule_now(job["id"], "acct")["queued"] is True
    assert _wait_until(lambda: state._find_schedule(job["id"]).get("status") == "error")
    stored = state._find_schedule(job["id"])
    assert api.sent == [] and not stored.get("running")
    assert stored["lastError"].startswith("Not sent: LinePassport stopped")
    assert stored["history"][-1]["ok"] is False


def test_resume_all_restores_its_jobs_when_a_removal_lands_between_its_saves(
    ctx, monkeypatch
):
    # Another account's removal replaces the schedules list after the jobs' new times
    # were saved; the setting save then fails: the times still go back.
    state, owner, _api = ctx
    state.account_store.data["accounts"].append(
        {"id": "acct-2", "label": "Shop 2", "mid": "U-two", "ownerId": owner["id"],
         "tokenFile": ""}
    )
    state.account_store.save()
    state.web_auth.grant_account_access(owner["id"], "acct-2")
    job = _job(state, mode="repeat", intervalMinutes=60)
    state.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
    due = time.time() - 3600
    state._find_schedule(job["id"])["nextRunAt"] = due
    state.save_schedules()
    original = state.store.set

    def store_set(key, value):
        if key == "schedule_settings":
            state._commit_account_removal("acct-2")
            raise OSError(28, "No space left on device")
        return original(key, value)

    monkeypatch.setattr(state.store, "set", store_set)
    with pytest.raises(OSError):
        state.set_schedules_paused({"accountId": "acct", "paused": False}, owner)
    assert state.account_store.get("acct-2") is None
    assert state._find_schedule(job["id"])["nextRunAt"] == due


def _hold_senders(state):
    """Every schedule sender busy until the returned event is set (other workspaces'
    sends going out): a run handed over now waits for a free sender."""
    release = threading.Event()
    workers = state.schedule_executor._max_workers
    busy = threading.Barrier(workers + 1)

    def hold():
        busy.wait(5)
        release.wait(10)

    for _ in range(workers):
        state.schedule_executor.submit(hold)
    busy.wait(5)
    return release


def _only_the_tests_rounds(state, monkeypatch):
    """From here on the state's own scheduler thread (a round every ten seconds) starts no
    round: only the rounds the test calls run, so none comes between its steps."""
    real_round = state.run_due_schedules
    caller = threading.current_thread()

    def run_due_schedules():
        if threading.current_thread() is caller:
            real_round()

    monkeypatch.setattr(state, "run_due_schedules", run_due_schedules)


def _two_chats(state, **body):
    return _job(state, targets=[{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}], **body)


def test_a_queued_run_now_of_a_schedule_deleted_while_it_waits_sends_nothing(ctx):
    state, _owner, api = ctx
    job = _two_chats(state)
    release = _hold_senders(state)
    try:
        assert state.run_schedule_now(job["id"], "acct")["queued"] is True
        state.delete_schedule(job["id"], "acct")
    finally:
        release.set()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [] and state.schedule_inflight == set()


def test_a_queued_run_now_sends_the_schedule_as_edited_while_it_waited(ctx):
    # The edit is what goes out, to the chats it names, and the schedule stays marked
    # as running until then (no other run starts beside it).
    state, _owner, api = ctx
    job = _two_chats(state)
    release = _hold_senders(state)
    try:
        assert state.run_schedule_now(job["id"], "acct")["queued"] is True
        edited = state.update_schedule(
            job["id"],
            {"accountId": "acct", "name": "promo", "text": "new {name}", "mode": "once",
             "runAt": "2026-01-01T09:00", "targets": [{"mid": _A, "name": "Ann"}]},
        )["schedule"]
        assert edited["running"] is True and edited["status"] == "running"
        with pytest.raises(WebError) as busy:
            state.run_schedule_now(job["id"], "acct")  # one chat now: runs at once
        assert busy.value.status == 409
    finally:
        release.set()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [("text", _A, "new Ann")]
    stored = state._find_schedule(job["id"])
    assert not stored.get("running") and stored["history"][-1]["ok"] is True


def test_a_queued_run_now_cleared_as_stuck_while_it_waits_sends_nothing(ctx):
    state, owner, api = ctx
    job = _two_chats(state)
    release = _hold_senders(state)
    try:
        assert state.run_schedule_now(job["id"], "acct")["queued"] is True
        assert state.clear_stuck_schedules("acct", owner)["cleared"] == 1
    finally:
        release.set()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [] and not state._find_schedule(job["id"]).get("running")


@pytest.mark.parametrize("change", ["nothing", "switched off", "paused", "moved later"])
def test_a_scheduled_send_waiting_for_a_sender_goes_out_only_if_still_due(ctx, change):
    state, owner, api = ctx
    job = _job(state)  # due: its time has passed
    release = _hold_senders(state)
    try:
        state.run_due_schedules()
        assert job["id"] in state.schedule_inflight
        if change == "switched off":
            state.toggle_schedule(job["id"], False, "acct")
        elif change == "paused":
            state.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
        elif change == "moved later":
            state.update_schedule(
                job["id"],
                {"accountId": "acct", "name": "promo", "to": _A, "text": "hi {name}",
                 "mode": "once", "runAt": "2099-01-01T09:00"},
            )
    finally:
        release.set()
    state.schedule_executor.shutdown(wait=True)
    stored = state._find_schedule(job["id"])
    assert not stored.get("running") and state.schedule_inflight == set()
    if change == "nothing":
        assert api.sent == [("text", _A, "hi ")] and stored["sentCount"] == 1
    else:
        assert api.sent == [] and stored["sentCount"] == 0


def test_a_schedule_created_as_its_account_is_removed_is_not_added(ctx):
    # The removal commits just before the new schedule takes the schedules' lock: it
    # is refused, and nothing is added for the removed account.
    state, _owner, _api = ctx
    real_lock = state.schedule_lock
    removed = {}

    class RemovalFirst:
        """The schedules' lock, with the account removed before its first use."""

        def __enter__(self):
            if not removed:
                removed["done"] = True
                state._commit_account_removal("acct")
            return real_lock.__enter__()

        def __exit__(self, *exc):
            return real_lock.__exit__(*exc)

    state.schedule_lock = RemovalFirst()
    try:
        with pytest.raises(WebError) as refused:
            _job(state)
    finally:
        state.schedule_lock = real_lock
    assert removed and refused.value.status == 400
    assert state.schedules == []


def test_a_run_now_while_linepassport_stops_says_it_is_stopping(ctx):
    state, _owner, api = ctx
    job = _two_chats(state)
    state.schedule_executor.shutdown(wait=True)
    with pytest.raises(WebError) as refused:
        state.run_schedule_now(job["id"], "acct")
    assert refused.value.status == 503 and refused.value.code == "server_stopping"
    assert not state._find_schedule(job["id"]).get("running")
    assert state.schedule_inflight == set() and api.sent == []


def test_a_due_daily_send_edited_while_it_waits_goes_out_edited(ctx):
    # An every-day send whose minute has passed waits for a free sender, and its text is
    # fixed meanwhile: the edit keeps its next run, so today's send goes out, edited.
    state, _owner, api = ctx
    minute = (dt.datetime.now() - dt.timedelta(minutes=2)).strftime("%H:%M")
    body = {"accountId": "acct", "name": "greeting", "to": _A, "text": "old",
            "mode": "repeat", "windowStart": minute, "windowEnd": minute,
            "intervalMinutes": 1440, "exactTime": True}
    job = state.create_schedule(body)["schedule"]
    due = time.time() - 120
    state._find_schedule(job["id"])["nextRunAt"] = due  # its minute came: it is due
    state.save_schedules()
    release = _hold_senders(state)
    try:
        state.run_due_schedules()
        assert job["id"] in state.schedule_inflight
        edited = state.update_schedule(job["id"], {**body, "text": "new"})["schedule"]
        assert edited["nextRunAt"] == due
    finally:
        release.set()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [("text", _A, "new")]


def test_an_edit_keeps_the_next_run_unless_it_changes_when_the_schedule_runs(ctx):
    state, _owner, _api = ctx
    body = {"accountId": "acct", "name": "x", "to": _A, "text": "a", "mode": "repeat",
            "windowStart": "09:00", "windowEnd": "18:00", "intervalMinutes": 60}
    job = state.create_schedule(body)["schedule"]
    state._find_schedule(job["id"]).update(
        {"nextRunAt": 1000.0, "jitter": 90, "jitterFor": 1000.0}
    )
    kept = state.update_schedule(
        job["id"], {**body, "text": "b", "targets": [{"mid": _B, "name": "Bob"}]}
    )["schedule"]
    assert (kept["nextRunAt"], kept["jitter"], kept["jitterFor"]) == (1000.0, 90, 1000.0)
    exact = state.update_schedule(job["id"], {**body, "exactTime": True})["schedule"]
    assert exact["nextRunAt"] == 1000.0 and "jitter" not in exact and "jitterFor" not in exact
    moved = state.update_schedule(job["id"], {**body, "windowStart": "10:00"})["schedule"]
    assert moved["nextRunAt"] != 1000.0


@pytest.mark.parametrize("saved_by", ["old form", "first wizard", "api"])
def test_an_edit_keeps_the_next_run_of_a_schedule_an_older_version_saved(ctx, saved_by):
    # An every-day send saved before weekdays, month days and exact times were kept (by
    # the old form, with a one-minute window, or by the page's first wizard, which saved
    # the window as start to start + 1) is due and waits for a free sender; its text is
    # fixed meanwhile, on the page or through the API: today's send goes out, edited.
    state, _owner, api = ctx
    opened = dt.datetime.now() - dt.timedelta(minutes=2)  # its minute came: it is due
    minute = opened.strftime("%H:%M")
    body = {"accountId": "acct", "name": "greeting", "to": _A, "text": "old",
            "mode": "repeat", "windowStart": minute, "windowEnd": minute,
            "intervalMinutes": 1440}
    job = state.create_schedule(body)["schedule"]
    due = opened.timestamp()
    stored = state._find_schedule(job["id"])
    for key in ("weekdays", "monthDays", "exactTime"):
        stored.pop(key, None)  # not kept then
    stored.update({"nextRunAt": due, "jitter": 0, "jitterFor": due})
    if saved_by == "first wizard":
        stored["windowEnd"] = (opened + dt.timedelta(minutes=1)).strftime("%H:%M")
    state.save_schedules()
    if saved_by == "api":
        edit = {"accountId": "acct", "text": "new"}
    else:
        # What the page's editor sends for it: an every-day send at its start time.
        edit = {**body, "text": "new", "targets": [{"mid": _A, "name": ""}],
                "weekdays": [], "monthDays": [], "exactTime": False, "enabled": True,
                "runAt": "", "activeFrom": "", "activeUntil": "", "maxRuns": 0}
    release = _hold_senders(state)
    try:
        state.run_due_schedules()
        assert job["id"] in state.schedule_inflight
        edited = state.update_schedule(job["id"], edit)["schedule"]
        assert (edited["nextRunAt"], edited["jitter"]) == (due, 0)
    finally:
        release.set()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [("text", _A, "new")]


def _page_edit(body, **changes):
    """What the page's editor saves for an every-day or monthly send (see wizardPayload):
    its window as start to start, and no weekdays unless it is weekly."""
    return {**body, "targets": [{"mid": _A, "name": ""}], "windowEnd": body["windowStart"],
            "intervalMinutes": 1440, "weekdays": [], "monthDays": [], "exactTime": False,
            "enabled": True, "runAt": "", "activeFrom": "", "activeUntil": "",
            "maxRuns": 0, **changes}


@pytest.mark.parametrize("window", [("22:00", "06:00"), ("23:59", "00:00")])
def test_a_page_edit_of_a_nightly_send_whose_window_crosses_midnight_keeps_its_next_run(
    ctx, window
):
    # A once-a-day send whose window crosses midnight (made in Custom, by the old form, or
    # by the page's first wizard at 23:59, which saved 23:59 to 00:00), with its next run
    # at 00:00, inside the window of the day before (where the scheduler before this one
    # left it after a run). An API edit of its text keeps that, and so does the page's
    # save, which shows it as an every-day send at its start and saves its window as start
    # to start. After that run it goes on at its start the same evening.
    state, _owner, _api = ctx
    start, end = window
    body = {"accountId": "acct", "name": "night", "to": _A, "text": "old",
            "mode": "repeat", "windowStart": start, "windowEnd": end,
            "intervalMinutes": 1440}
    job = state.create_schedule(body)["schedule"]
    midnight = dt.datetime.combine(dt.date.today() + dt.timedelta(days=1), dt.time(0, 0))
    state._find_schedule(job["id"])["nextRunAt"] = midnight.timestamp()
    state.save_schedules()
    kept = state.update_schedule(job["id"], {"accountId": "acct", "text": "new"})["schedule"]
    assert kept["nextRunAt"] == midnight.timestamp()
    saved = state.update_schedule(job["id"], _page_edit(body, text="newer"))["schedule"]
    assert saved["windowEnd"] == start and saved["nextRunAt"] == midnight.timestamp()
    hour, minute = (int(part) for part in start.split(":"))
    evening = dt.datetime.combine(midnight.date(), dt.time(hour, minute))
    ran = midnight + dt.timedelta(seconds=40)
    assert webapp_module._next_run_after(saved, ran) == evening.timestamp()


def test_a_page_edit_keeps_the_next_run_of_a_monthly_send_made_with_weekdays(ctx):
    # A monthly send made through the API with weekdays too (which a monthly send does
    # not go by) is due and waits for a free sender; its text is fixed on the page, which
    # saves it without weekdays: this month's send goes out, edited.
    state, _owner, api = ctx
    opened = dt.datetime.now() - dt.timedelta(minutes=2)  # its minute came: it is due
    today = opened.date()
    minute = opened.strftime("%H:%M")
    body = {"accountId": "acct", "name": "bill", "to": _A, "text": "old",
            "mode": "repeat", "windowStart": minute, "windowEnd": minute,
            "intervalMinutes": 1440, "monthDays": [today.day],
            "weekdays": [(today.weekday() + 3) % 7]}
    job = state.create_schedule(body)["schedule"]
    due = opened.timestamp()
    stored = state._find_schedule(job["id"])
    assert stored["weekdays"] and stored["monthDays"] == [today.day]
    stored.update({"nextRunAt": due, "jitter": 0, "jitterFor": due})
    state.save_schedules()
    release = _hold_senders(state)
    try:
        state.run_due_schedules()
        assert job["id"] in state.schedule_inflight
        edit = _page_edit(body, text="new", monthDays=[today.day])
        edited = state.update_schedule(job["id"], edit)["schedule"]
        assert (edited["nextRunAt"], edited["jitter"]) == (due, 0)
    finally:
        release.set()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [("text", _A, "new")]


def _ended_at(monkeypatch, when):
    """The scheduler's clock reads ``when``: a run of a job ends then."""

    class _Clock(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return cls.combine(when.date(), when.time())

    monkeypatch.setattr(webapp_module._dt, "datetime", _Clock)


@pytest.mark.parametrize(
    ("window", "days", "ended", "expected"),
    [
        # Every day, 22:00 to 06:00: the next night's start, not 00:00 (inside its window).
        (("22:00", "06:00"), {}, (2027, 1, 4, 22, 0, 40), (2027, 1, 5, 22, 0)),
        # A run of it that ended after midnight: the same evening, not the next one.
        (("22:00", "06:00"), {}, (2027, 1, 5, 0, 2, 10), (2027, 1, 5, 22, 0)),
        # Saturdays, 23:00 to 01:00: the next Saturday, after a run on time or late.
        (("23:00", "01:00"), {"weekdays": [5]}, (2027, 1, 9, 23, 0, 40), (2027, 1, 16, 23, 0)),
        (("23:00", "01:00"), {"weekdays": [5]}, (2027, 1, 10, 0, 1), (2027, 1, 16, 23, 0)),
        # On the 28th, 22:00 to 06:00: the next month's 28th.
        (("22:00", "06:00"), {"monthDays": [28]}, (2027, 1, 28, 22, 1), (2027, 2, 28, 22, 0)),
        # Every day at 23:59, late past midnight by its random delay: the same day at 23:59.
        (("23:59", "23:59"), {}, (2027, 1, 5, 0, 1, 30), (2027, 1, 5, 23, 59)),
        # The page's first wizard's every day at 23:59, saved as 23:59 to 00:00.
        (("23:59", "00:00"), {}, (2027, 1, 4, 23, 59, 20), (2027, 1, 5, 23, 59)),
        # Every day at 09:00, a run of the day before that waited in the queue all night.
        (("09:00", "09:00"), {}, (2027, 1, 5, 3, 0), (2027, 1, 5, 9, 0)),
        # Every day at 00:00: the next day's 00:00.
        (("00:00", "00:00"), {}, (2027, 1, 4, 0, 0, 40), (2027, 1, 5, 0, 0)),
    ],
)
def test_a_once_a_day_send_goes_on_at_the_start_of_its_next_window(
    monkeypatch, window, days, ended, expected
):
    # Once in each window (a window that crosses midnight takes 00:00 in), and none
    # skipped after a run that ended after midnight (late by its random delay, a wait
    # for a free sender, the hourly limit or a retry).
    start, end = window
    job = _schedule_from_body(
        {"to": _A, "text": "x", "mode": "repeat", "windowStart": start, "windowEnd": end,
         "intervalMinutes": 1440, **days},
        account_id="acct",
    )
    _ended_at(monkeypatch, dt.datetime(*ended))
    job["sentCount"] = 1
    webapp_module._advance_after_success(job)
    assert job["status"] == "waiting"
    assert dt.datetime.fromtimestamp(job["nextRunAt"]) == dt.datetime(*expected)


def test_the_calendar_lists_a_weekly_send_whose_window_crosses_midnight_once_a_week():
    job = _schedule_from_body(
        {"to": _A, "text": "x", "mode": "repeat", "windowStart": "23:00",
         "windowEnd": "01:00", "intervalMinutes": 1440, "weekdays": [5]},
        account_id="acct",
    )
    today = dt.date.today()  # its first run is still to come, whatever the date
    first = dt.datetime.combine(
        today + dt.timedelta(days=(5 - today.weekday()) % 7 or 7), dt.time(23, 0)
    )
    job["nextRunAt"] = first.timestamp()
    horizon = (first + dt.timedelta(days=22)).timestamp()
    runs = webapp_module._simulate_schedule_runs(job, horizon, limit=10)
    assert [dt.datetime.fromtimestamp(at) for at in runs] == [
        first + dt.timedelta(days=7 * week) for week in range(4)
    ]


@pytest.mark.parametrize("weekly", [False, True])
@pytest.mark.parametrize("due", ["waiting", "queued", "retrying"])
def test_a_page_edit_keeps_a_due_run_of_a_send_whose_window_crosses_midnight(
    ctx, due, weekly
):
    # A once-a-day send whose window crosses midnight, every day or weekly, is due: its
    # run waits for a free sender, waits in the queue for the hourly limit, or is to be
    # tried again. Its text is fixed on the page, which saves its window as start to
    # start: the run is kept, and the one that waits for a sender goes out, edited.
    state, _owner, api = ctx
    # A window that opened two minutes ago and closes the next day, two minutes before.
    opened = dt.datetime.now() - dt.timedelta(minutes=2)
    start = opened.strftime("%H:%M")
    end = (opened - dt.timedelta(minutes=2)).strftime("%H:%M")
    if end >= start:  # it opened at 00:00 or 00:01: one that opened at 23:58 instead
        opened = opened.replace(hour=0, minute=0) - dt.timedelta(minutes=2)
        start, end = "23:58", "23:56"
    body = {"accountId": "acct", "name": "night", "to": _A, "text": "old",
            "mode": "repeat", "windowStart": start, "windowEnd": end,
            "intervalMinutes": 1440,
            "weekdays": [opened.weekday()] if weekly else []}
    job = state.create_schedule(body)["schedule"]
    stored = state._find_schedule(job["id"])
    if due == "waiting":
        at = time.time() - 120  # its start came
    elif due == "queued":
        at = time.time() + webapp_module.SCHEDULE_QUEUE_RETRY_SECONDS
        stored["status"] = "queued"
    else:
        at = time.time() + webapp_module.SCHEDULE_RETRY_DELAY_SECONDS
        stored.update({"status": "retrying", "retrying": True})
    stored.update({"nextRunAt": at, "jitter": 0, "jitterFor": at})
    state.save_schedules()
    release = _hold_senders(state)
    try:
        state.run_due_schedules()
        assert (job["id"] in state.schedule_inflight) == (due == "waiting")
        edit = _page_edit(body, text="new", weekdays=body["weekdays"])
        edited = state.update_schedule(job["id"], edit)["schedule"]
        assert edited["windowEnd"] == start
        assert (edited["nextRunAt"], edited["jitter"]) == (at, 0)
    finally:
        release.set()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == ([("text", _A, "new")] if due == "waiting" else [])


def test_an_edit_that_moves_a_window_end_or_a_weekly_day_works_the_next_run_out_again(ctx):
    # The end of the window of a send that goes out more than once a day counts, and so
    # do the days of a weekly send.
    state, _owner, _api = ctx
    hourly = {"accountId": "acct", "name": "hourly", "to": _A, "text": "a",
              "mode": "repeat", "windowStart": "09:00", "windowEnd": "18:00",
              "intervalMinutes": 60}
    weekly = {"accountId": "acct", "name": "weekly", "to": _A, "text": "a",
              "mode": "repeat", "windowStart": "09:00", "windowEnd": "09:00",
              "intervalMinutes": 1440, "weekdays": [0]}
    for body, edit in (
        (hourly, {"accountId": "acct", "windowEnd": "17:00"}),
        (weekly, _page_edit(weekly, weekdays=[1])),
    ):
        job = state.create_schedule(body)["schedule"]
        state._find_schedule(job["id"]).update(
            {"nextRunAt": 1000.0, "jitter": 0, "jitterFor": 1000.0}
        )
        edited = state.update_schedule(job["id"], edit)["schedule"]
        assert edited["nextRunAt"] != 1000.0, body["name"]


def test_a_repeating_send_that_fails_twice_on_its_last_day_stops(ctx, monkeypatch):
    # Its window is open all day and today is its last day: once the retry fails too it
    # is switched off (as a one-off send is), not tried again from now every few minutes
    # until its window closes.
    state, _owner, api = ctx
    notified = []
    monkeypatch.setattr(state, "_notify_schedule_failure", lambda *args: notified.append(args))
    api.fail_times = 99
    job = _job(state, mode="repeat", windowStart="00:00", windowEnd="23:59",
               intervalMinutes=1440, activeUntil=dt.date.today().isoformat())
    for _ in range(2):
        with pytest.raises(WebError):
            state._execute_schedule(job["id"], manual=False)
    stored = state._find_schedule(job["id"])
    assert stored["status"] == "error" and len(notified) == 1
    assert stored["enabled"] is False and stored["nextRunAt"] is None
    state.run_due_schedules()
    assert state.schedule_inflight == set() and api.sent == []


@pytest.mark.parametrize(
    ("window", "ended", "expected"),
    [
        # Every day, 22:00 to 06:00: its run and its retry failed; the next night's start,
        # not 00:00 inside the same window.
        (("22:00", "06:00"), (2027, 1, 4, 22, 5, 30), (2027, 1, 5, 22, 0)),
        # Every day at 23:59, its retry failed after midnight: the same day at 23:59.
        (("23:59", "23:59"), (2027, 1, 5, 0, 4, 10), (2027, 1, 5, 23, 59)),
    ],
)
def test_a_once_a_day_send_whose_retry_failed_goes_on_at_its_next_window(
    monkeypatch, window, ended, expected
):
    start, end = window
    job = _schedule_from_body(
        {"to": _A, "text": "x", "mode": "repeat", "windowStart": start, "windowEnd": end,
         "intervalMinutes": 1440},
        account_id="acct",
    )
    _ended_at(monkeypatch, dt.datetime(*ended))
    webapp_module._advance_after_error(job)
    assert job["enabled"] is True
    assert dt.datetime.fromtimestamp(job["nextRunAt"]) == dt.datetime(*expected)


def _minute(at):
    return at.replace(second=0, microsecond=0)


def _daily(state, start, end=None, **body):
    payload = {"accountId": "acct", "name": "note", "to": _A, "text": "hi", "mode": "repeat",
               "windowStart": start, "windowEnd": end or start, "intervalMinutes": 1440,
               "exactTime": True}
    payload.update(body)
    return state.create_schedule(payload)["schedule"]


@pytest.mark.parametrize(
    ("late", "weekly", "sent"),
    [
        # Its next start is three minutes away: it waits for that start.
        (dt.timedelta(days=1) - dt.timedelta(minutes=3), False, False),
        # Nearer the start it missed than its next one: it goes out late.
        (dt.timedelta(hours=11), False, True),
        (dt.timedelta(hours=13), False, False),
        # A weekly send late by 13 hours: its next start is days away, so it goes out.
        (dt.timedelta(hours=13), True, True),
    ],
)
def test_a_once_a_day_send_that_missed_its_time_goes_out_late_unless_its_next_is_nearer(
    ctx, late, weekly, sent
):
    # Its run could not go out at its time (the account had to be connected again, or
    # LinePassport was not running) and comes due now. It goes out late while that is
    # nearer the start it missed than its next start; otherwise it waits for its next
    # start, so it does not go out twice within a short time.
    state, owner, api = ctx
    missed = _minute(dt.datetime.now() - late)
    following = missed + dt.timedelta(days=7 if weekly else 1)
    job = _daily(state, missed.strftime("%H:%M"),
                 weekdays=[missed.weekday()] if weekly else [])
    stored = state._find_schedule(job["id"])
    stored.update({
        "nextRunAt": missed.timestamp(),
        "status": "login required",
        "lastError": "LINE session expired or was revoked. Connect the account again.",
    })
    state.save_schedules()
    events = state.upcoming_schedules("acct", owner, days=8)["events"]
    assert events[0]["at"] == (missed if sent else following).timestamp()
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    stored = state._find_schedule(job["id"])
    assert stored["nextRunAt"] == following.timestamp()
    assert stored["status"] == "waiting" and state.schedule_inflight == set()
    assert stored["lastError"] is None  # the account is connected again
    events = state.upcoming_schedules("acct", owner, days=8)["events"]
    assert events[0]["at"] == following.timestamp()
    logs = state.store.get("bot_logs", {"logs": []})["logs"]
    if sent:
        assert api.sent == [("text", _A, "hi")] and stored["history"][-1]["ok"] is True
        assert logs[-1]["action"] == "schedule.run.success"
    else:
        assert api.sent == [] and stored["sentCount"] == 0
        assert stored["history"][-1]["ok"] is False
        assert stored["history"][-1]["error"].startswith("Not sent: it could not go out")
        assert logs[-1]["action"] == "schedule.run.missed" and logs[-1]["ok"] is False


def _open_window(state, **body):
    """A once-a-day send whose window opened a minute ago and is open five more."""
    opened = _minute(dt.datetime.now() - dt.timedelta(minutes=1))
    job = _daily(state, opened.strftime("%H:%M"),
                 (opened + dt.timedelta(minutes=6)).strftime("%H:%M"), **body)
    return job, opened


def test_a_once_a_day_send_that_missed_its_time_goes_out_in_a_window_open_now(ctx):
    # Yesterday's run comes due late, while today's window is open: it goes out now,
    # once, and goes on at tomorrow's start.
    state, _owner, api = ctx
    job, opened = _open_window(state)
    state._find_schedule(job["id"])["nextRunAt"] = (opened - dt.timedelta(days=1)).timestamp()
    state.save_schedules()
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [("text", _A, "hi")]
    stored = state._find_schedule(job["id"])
    assert stored["nextRunAt"] == (opened + dt.timedelta(days=1)).timestamp()


@pytest.mark.parametrize("held", ["queued", "retrying"])
def test_a_once_a_day_run_held_past_its_time_waits_for_its_next_start(ctx, held):
    # It waited in the queue for the account's daily limit, or its retry could not run
    # (LinePassport was not running), until its next start is nearer than the start it
    # missed: it goes out at that start instead, with a retry of its own if it fails.
    state, _owner, api = ctx
    following = _minute(dt.datetime.now() + dt.timedelta(minutes=3))
    missed = following - dt.timedelta(days=1)
    job = _daily(state, following.strftime("%H:%M"))
    if held == "queued":
        # The queue moves its next run on every few minutes and keeps the time it was for.
        pushed = time.time() - 10
        update = {"nextRunAt": pushed, "queuedFrom": [missed.timestamp(), pushed]}
    else:
        # Its run failed at yesterday's start; the retry, 5 minutes later, never ran.
        update = {"nextRunAt": missed.timestamp() + webapp_module.SCHEDULE_RETRY_DELAY_SECONDS,
                  "retrying": True}
    state._find_schedule(job["id"]).update({**update, "status": held})
    assert state._scheduled_run_due(job["id"]) is False
    state.run_due_schedules()
    stored = state._find_schedule(job["id"])
    assert stored["nextRunAt"] == following.timestamp() and stored["status"] == "waiting"
    assert "retrying" not in stored
    assert api.sent == [] and state.schedule_inflight == set()


@pytest.mark.parametrize("sent_by", ["a run", "run now"])
def test_a_once_a_day_send_due_again_in_a_window_it_went_out_in_is_not_sent_again(
    ctx, sent_by
):
    # It went out in its window open now, and a run of it comes due in that window again
    # (a next run an older version stored inside it, the way the scheduler before this
    # one stored 00:00): not sent again; it goes on at its next window.
    state, owner, api = ctx
    job, opened = _open_window(state)
    if sent_by == "run now":
        state.run_schedule_now(job["id"], "acct")
    else:
        state._execute_schedule(job["id"], manual=False)
    assert len(api.sent) == 1
    stored = state._find_schedule(job["id"])
    history = len(stored["history"])
    stored.update({"nextRunAt": time.time() - 10, "jitter": 0, "jitterFor": None})
    state.save_schedules()
    events = state.upcoming_schedules("acct", owner, days=2)["events"]
    assert events[0]["at"] == (opened + dt.timedelta(days=1)).timestamp()
    state.run_due_schedules()
    stored = state._find_schedule(job["id"])
    assert len(api.sent) == 1 and state.schedule_inflight == set()
    assert stored["nextRunAt"] == (opened + dt.timedelta(days=1)).timestamp()
    assert len(stored["history"]) == history and stored["enabled"] is True
    logs = state.store.get("bot_logs", {"logs": []})["logs"]
    assert logs[-1]["action"] == "schedule.run.already_sent"


@pytest.mark.parametrize(
    "action",
    ["switched off and on", "switched on", "start moved", "weekdays edited", "resumed"],
)
def test_switching_on_or_editing_a_once_a_day_send_never_sends_it_twice_in_a_window(
    ctx, action
):
    # It went out in its window open now. Switching it off and on (or on again), an edit
    # that changes when it runs (its window still open), or Resume all with a run of it
    # stored inside that window: it goes on at its next window, not again now.
    state, owner, api = ctx
    job, opened = _open_window(state)
    body = {"accountId": "acct", "name": "note", "to": _A, "text": "hi", "mode": "repeat",
            "windowStart": opened.strftime("%H:%M"),
            "windowEnd": (opened + dt.timedelta(minutes=6)).strftime("%H:%M"),
            "intervalMinutes": 1440, "exactTime": True}
    state._execute_schedule(job["id"], manual=False)
    assert len(api.sent) == 1
    following = opened + dt.timedelta(days=1)
    if action == "switched off and on":
        state.toggle_schedule(job["id"], False, "acct")
        state.toggle_schedule(job["id"], True, "acct")
    elif action == "switched on":
        state.toggle_schedule(job["id"], True, "acct")
    elif action == "start moved":
        earlier = opened - dt.timedelta(minutes=1)
        state.update_schedule(job["id"], {**body, "windowStart": earlier.strftime("%H:%M")})
        following = earlier + dt.timedelta(days=1)
    elif action == "weekdays edited":
        days = sorted({opened.weekday(), (opened.weekday() + 1) % 7})
        state.update_schedule(job["id"], {**body, "weekdays": days})
    else:
        state.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
        state._find_schedule(job["id"])["nextRunAt"] = time.time() - 10
        state.set_schedules_paused({"accountId": "acct", "paused": False}, owner)
    stored = state._find_schedule(job["id"])
    assert stored["nextRunAt"] == following.timestamp() and stored["enabled"] is True
    state.run_due_schedules()
    assert len(api.sent) == 1 and state.schedule_inflight == set()


def test_switching_on_a_once_a_day_send_that_has_not_gone_out_in_its_open_window(ctx):
    # The control: it did not go out in its window open now, so switched on it is due now.
    state, _owner, _api = ctx
    job, _opened = _open_window(state)
    state.toggle_schedule(job["id"], False, "acct")
    switched = state.toggle_schedule(job["id"], True, "acct")["schedule"]
    assert switched["nextRunAt"] <= time.time()


def test_switching_on_a_once_a_day_send_after_its_last_run_does_not_send_it_again(ctx):
    # Today is its last day and it went out in its window open now, so it is done:
    # switched on again, it has no next run and does not go out again.
    state, _owner, api = ctx
    job, opened = _open_window(state)
    state._find_schedule(job["id"])["activeUntil"] = opened.date().isoformat()
    state._execute_schedule(job["id"], manual=False)
    assert state._find_schedule(job["id"])["status"] == "completed"
    switched = state.toggle_schedule(job["id"], True, "acct")["schedule"]
    assert switched["nextRunAt"] is None
    state.run_due_schedules()
    stored = state._find_schedule(job["id"])
    assert len(api.sent) == 1 and state.schedule_inflight == set()
    assert stored["status"] == "completed" and stored["enabled"] is False


@pytest.mark.parametrize("damaged", [{"windowStart": "25:00"}, {"activeFrom": "someday"}])
def test_a_due_once_a_day_run_whose_times_cannot_be_read_is_not_held(damaged):
    # Its stored times are damaged: the check of a missed run lets it go out as before,
    # instead of failing the scheduler's round for every schedule.
    job = {"mode": "repeat", "intervalMinutes": 1440, "windowStart": "09:00",
           "windowEnd": "09:00", "enabled": True, "lastRunAt": "2027-01-04T09:00:30",
           **damaged}
    at = dt.datetime(2027, 1, 5, 21, 0).timestamp()
    assert webapp_module._due_run_deferred(job, at) == ("", None)


def test_a_held_missed_run_keeps_the_report_of_chats_a_sign_in_cut_off(ctx):
    # Its last run was cut short when LINE asked to sign in again, and its next run is
    # missed and held: the report of the chats that run did not reach stays.
    state, _owner, api = ctx
    following = _minute(dt.datetime.now() + dt.timedelta(minutes=3))
    job = _daily(state, following.strftime("%H:%M"))
    report = "Sent to 1/2 chats. Bob: not sent, LINE asked to sign in again"
    state._find_schedule(job["id"]).update({
        "nextRunAt": (following - dt.timedelta(days=1)).timestamp(),
        "status": "login required",
        "lastError": report,
    })
    state.run_due_schedules()
    stored = state._find_schedule(job["id"])
    assert stored["nextRunAt"] == following.timestamp() and stored["status"] == "waiting"
    assert stored["lastError"] == report and api.sent == []



def _job_at(start, end=None, **body):
    """A once-a-day job (not stored), its window from ``start`` to ``end``."""
    return _schedule_from_body(
        {"to": _A, "text": "x", "mode": "repeat", "windowStart": start,
         "windowEnd": end or start, "intervalMinutes": 1440, **body},
        account_id="acct",
    )


def _deferred(job, due, at):
    job["nextRunAt"] = due.timestamp()
    why, until = webapp_module._due_run_deferred(job, at.timestamp())
    return why, None if until is None else dt.datetime.fromtimestamp(until)


def test_a_missed_run_due_in_the_last_second_before_its_next_start_waits_for_it():
    # Monday's 22:00 run comes due on Tuesday half a second before 22:00: it waits for
    # 22:00 (one send), as one due a minute earlier does. At 22:00 itself it goes out.
    job = _job_at("22:00")
    monday, tuesday = dt.datetime(2027, 1, 4, 22, 0), dt.datetime(2027, 1, 5, 22, 0)
    for before in (60, 1.5, 1.0, 0.5, 0.001):
        at = tuesday - dt.timedelta(seconds=before)
        assert _deferred(job, monday, at) == ("missed", tuesday), before
    assert _deferred(job, monday, tuesday) == ("", None)


def test_a_late_run_goes_out_when_it_is_exactly_halfway_to_its_next_start():
    # Monday's 22:00 run, due at Tuesday 10:00 (12 hours either side), goes out; a second
    # later it waits for Tuesday's 22:00.
    job = _job_at("22:00")
    monday, tuesday = dt.datetime(2027, 1, 4, 22, 0), dt.datetime(2027, 1, 5, 22, 0)
    assert _deferred(job, monday, dt.datetime(2027, 1, 5, 10, 0)) == ("", None)
    later = dt.datetime(2027, 1, 5, 10, 0, 1)
    assert _deferred(job, monday, later) == ("missed", tuesday)
    # A run worked out from now at 15:00 inside a 09:00-23:00 window: halfway to Tuesday's
    # 09:00 is midnight.
    custom = _job_at("09:00", "23:00")
    worked_out, next_start = dt.datetime(2027, 1, 4, 15, 0), dt.datetime(2027, 1, 5, 9, 0)
    assert _deferred(custom, worked_out, dt.datetime(2027, 1, 5, 0, 0)) == ("", None)
    later = dt.datetime(2027, 1, 5, 0, 0, 1)
    assert _deferred(custom, worked_out, later) == ("missed", next_start)


def test_a_last_run_time_this_system_cannot_convert_is_left_out(monkeypatch):
    # On Windows a time before 1970 cannot be converted (on Linux it can, and is used).
    if sys.platform == "win32":
        assert webapp_module._last_run_time({"lastRunAt": "1969-12-31T23:00:00"}) is None
    # A last run the next run cannot be worked out from counts as none.
    job = _job_at("00:00", "23:59")
    job["lastRunAt"] = (dt.datetime.now() - dt.timedelta(hours=1)).replace(microsecond=0).isoformat()

    def cannot(*_args, **_kwargs):
        raise OSError(22, "Invalid argument")

    monkeypatch.setattr(webapp_module, "_next_run_after", cannot)
    assert abs(webapp_module._next_run_from_now(job) - time.time()) < 5  # due now


@pytest.mark.parametrize("window", [("09:00", "23:00"), ("13:00", "06:00")])
def test_a_run_worked_out_late_in_its_window_goes_out_after_its_random_delay(window):
    # Switched on (or created, resumed, edited) a minute before its long window closes,
    # its run comes due by its random delay after the window closed, or its retry 5
    # minutes after a failure: it is not late, so it goes out.
    job = _job_at(*window)
    for due, at, retrying in (
        (dt.datetime(2027, 1, 4, 22, 59), dt.datetime(2027, 1, 4, 23, 1, 30), False),
        (dt.datetime(2027, 1, 4, 22, 59), dt.datetime(2027, 1, 4, 23, 29), False),
        (dt.datetime(2027, 1, 4, 23, 1), dt.datetime(2027, 1, 4, 23, 1, 10), True),
    ):
        if window[0] == "13:00":  # the same, before 06:00 the next morning
            due, at = due + dt.timedelta(hours=7), at + dt.timedelta(hours=7)
        job["retrying"] = retrying
        assert _deferred(job, due, at) == ("", None), (due, at)


def test_a_next_run_an_older_version_stored_outside_its_windows_counts_from_its_start():
    # An older version left the next run of an every-day 00:30 send at 00:00, and of an
    # every-day 23:30 send at a time that drifted from its creation (23:01): neither lies
    # in a window of it, so the run counts from the start before it, and waits for the
    # start a few minutes on. A retry due then is its own time and goes out.
    for start, due, at, following in (
        ("00:30", (2027, 1, 7, 0, 0), (2027, 1, 7, 0, 0, 5), (2027, 1, 7, 0, 30)),
        ("09:00", (2027, 1, 7, 0, 0), (2027, 1, 7, 0, 0, 5), (2027, 1, 7, 9, 0)),
        ("23:30", (2027, 1, 7, 23, 1, 15), (2027, 1, 7, 23, 1, 20), (2027, 1, 7, 23, 30)),
    ):
        job = _job_at(start)
        held = _deferred(job, dt.datetime(*due), dt.datetime(*at))
        assert held == ("missed", dt.datetime(*following)), start
        job["retrying"] = True
        assert _deferred(job, dt.datetime(*due), dt.datetime(*at)) == ("", None), start
    # The queue keeps a run with no time of its own as one with none, even once its next
    # run lies inside a window.
    job = _job_at("00:30")
    inside = dt.datetime(2027, 1, 7, 0, 30).timestamp()
    job.update({"nextRunAt": inside, "queuedFrom": [None, inside]})
    assert webapp_module._run_own_time(job, inside + 60) is None


def test_once_a_day_sends_whose_timing_is_equal_hold_the_same_late_runs():
    # A Custom send once a day between 09:00 and 23:00, and the page's daily 09:00: equal
    # timing (the end of a once-a-day window does not count). Their run due on Monday at
    # 09:00 is late (LinePassport was not running): both go out, or both wait for Tuesday,
    # whatever the time they come due.
    custom, daily = _job_at("09:00", "23:00"), _job_at("09:00")
    assert webapp_module._schedule_timing(custom) == webapp_module._schedule_timing(daily)
    monday = dt.datetime(2027, 1, 4, 9, 0)
    decisions = set()
    for minutes in range(10, 24 * 60, 10):
        at = monday + dt.timedelta(minutes=minutes)
        assert _deferred(custom, monday, at) == _deferred(daily, monday, at), at
        decisions.add(_deferred(custom, monday, at)[0])
    assert decisions == {"", "missed"}
    # Late into its window, nearer Tuesday's start: it waits for it.
    assert _deferred(custom, monday, dt.datetime(2027, 1, 4, 22, 0)) == (
        "missed", dt.datetime(2027, 1, 5, 9, 0))


def test_a_queued_run_keeps_the_time_it_was_for(ctx):
    # Over the account's limits, a due run waits in the queue, which moves its next run on
    # every few minutes: the time it was for stays with it, so a run that waits in the
    # queue until its next start is nearer is held then.
    state, owner, api = ctx
    state.save_schedule_limits(
        {"accountId": "acct", "maxPerHour": 1, "maxPerDay": 1, "jitterMinutes": 0}, owner
    )
    state.record_schedule_sends("acct", 1)
    opened = _minute(dt.datetime.now() - dt.timedelta(minutes=2))
    job = _daily(state, opened.strftime("%H:%M"))
    state._find_schedule(job["id"])["nextRunAt"] = opened.timestamp()
    state.run_due_schedules()
    stored = state._find_schedule(job["id"])
    assert stored["status"] == "queued" and stored["nextRunAt"] > time.time() + 200
    assert stored["queuedFrom"] == [opened.timestamp(), stored["nextRunAt"]]
    assert webapp_module._run_own_time(stored, time.time()) == opened.timestamp()
    pushed = time.time() - 1  # its next queue round comes
    stored.update({"nextRunAt": pushed, "queuedFrom": [opened.timestamp(), pushed]})
    state.run_due_schedules()
    stored = state._find_schedule(job["id"])
    assert stored["status"] == "queued" and stored["queuedFrom"][0] == opened.timestamp()
    # Anything else that sets its next run (its next start, say) leaves the queue's note
    # behind.
    following = (opened + dt.timedelta(days=1)).timestamp()
    stored["nextRunAt"] = following
    assert webapp_module._run_own_time(stored, time.time()) == following
    assert api.sent == []


@pytest.mark.parametrize(
    "last_run", ["0001-01-01T00:00:00+14:00", "9999-12-31T23:59:59-14:00", "not a time"]
)
def test_a_damaged_last_run_time_is_left_out(ctx, last_run):
    # Its last run's time cannot be read as a time: the next run is worked out from now
    # as if it had none, and the scheduler's round goes on for the other schedules.
    state, owner, api = ctx
    job, _opened = _open_window(state)
    stored = state._find_schedule(job["id"])
    stored.update({"lastRunAt": last_run, "nextRunAt": None})
    assert webapp_module._last_run_time(stored) is None
    assert abs(webapp_module._next_run_from_now(stored) - time.time()) < 5  # its window is open
    other = _job(state, name="other", to=_B)  # a one-off send, due
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    assert ("text", _B, "hi ") in api.sent
    assert state.toggle_schedule(job["id"], True, "acct")["schedule"]["nextRunAt"]
    state.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
    assert state.set_schedules_paused({"accountId": "acct", "paused": False}, owner)[
        "settings"]["paused"] is False
    assert state._find_schedule(other["id"])["sentCount"] == 1


def test_a_held_run_is_logged_when_the_round_cannot_save(ctx, monkeypatch):
    # The round holds a missed run, then its save fails (a full disk): the activity log
    # says so all the same (the held run is not due again, to be logged later).
    state, _owner, _api = ctx
    following = _minute(dt.datetime.now() + dt.timedelta(minutes=3))
    job = _daily(state, following.strftime("%H:%M"))
    state._find_schedule(job["id"])["nextRunAt"] = (following - dt.timedelta(days=1)).timestamp()
    _store_fails(state, monkeypatch, lambda key: key == "schedules")
    state.run_due_schedules()
    assert state.schedules_unsaved is True
    assert state._find_schedule(job["id"])["nextRunAt"] == following.timestamp()
    actions = [entry["action"] for entry in state.store.get("bot_logs", {"logs": []})["logs"]]
    assert "schedule.run.missed" in actions


def _run_log(state):
    return [entry["action"] for entry in state.store.get("bot_logs", {"logs": []})["logs"]
            if entry["action"].startswith("schedule.run.")]


def test_switching_on_a_once_a_day_send_days_after_its_last_run_waits_for_its_next_start(ctx):
    # Its last run was 3 days ago and today's start was 2 hours ago (its one-minute
    # window closed): switched off and on, it goes on at tomorrow's start, not at once.
    state, _owner, api = ctx
    start = _minute(dt.datetime.now() - dt.timedelta(hours=2))
    job = _daily(state, start.strftime("%H:%M"))
    state._find_schedule(job["id"]).update(
        {"lastRunAt": (start - dt.timedelta(days=3)).isoformat(), "sentCount": 3})
    state.toggle_schedule(job["id"], False, "acct")
    switched = state.toggle_schedule(job["id"], True, "acct")["schedule"]
    assert switched["nextRunAt"] == (start + dt.timedelta(days=1)).timestamp()
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == []


def test_a_missed_run_of_a_paused_account_is_left_as_it_is(ctx):
    # Pause all keeps it as it is (Resume all works it out from now), with no note in its
    # history or the activity log.
    state, owner, api = ctx
    following = _minute(dt.datetime.now() + dt.timedelta(minutes=3))
    job = _daily(state, following.strftime("%H:%M"))
    missed = (following - dt.timedelta(days=1)).timestamp()
    state._find_schedule(job["id"])["nextRunAt"] = missed
    state.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
    state.run_due_schedules()
    stored = state._find_schedule(job["id"])
    assert stored["nextRunAt"] == missed and stored.get("history", []) == []
    assert _run_log(state) == [] and api.sent == []


def test_a_missed_run_over_the_daily_limit_waits_for_its_next_start(ctx):
    # The daily limit is used up, and the run it missed is held all the same (its next
    # start, waiting, the note), not queued for the limit.
    state, _owner, _api = ctx
    state._save_schedule_settings("acct", {"maxPerHour": 1, "maxPerDay": 1, "jitterMinutes": 0})
    state.record_schedule_sends("acct", 1)
    following = _minute(dt.datetime.now() + dt.timedelta(minutes=3))
    job = _daily(state, following.strftime("%H:%M"))
    state._find_schedule(job["id"])["nextRunAt"] = (following - dt.timedelta(days=1)).timestamp()
    state.run_due_schedules()
    stored = state._find_schedule(job["id"])
    assert stored["nextRunAt"] == following.timestamp() and stored["status"] == "waiting"
    assert stored["history"][-1]["ok"] is False and _run_log(state) == ["schedule.run.missed"]


def test_a_held_run_of_an_account_removed_meanwhile_writes_no_log_line(ctx, monkeypatch):
    # Its account was removed between the round and the activity-log line.
    state, _owner, _api = ctx
    following = _minute(dt.datetime.now() + dt.timedelta(minutes=3))
    job = _daily(state, following.strftime("%H:%M"))
    state._find_schedule(job["id"])["nextRunAt"] = (following - dt.timedelta(days=1)).timestamp()
    monkeypatch.setattr(state, "_account_listed", lambda account_id: False)
    state.run_due_schedules()
    assert _run_log(state) == []


def test_a_once_a_day_send_whose_last_run_is_ahead_of_the_clock_goes_out_in_its_window(ctx):
    # The clock was set back, so its lastRunAt is 2 days ahead: a run due in its window
    # open now goes out (a last run not yet come does not count).
    state, _owner, api = ctx
    job, opened = _open_window(state)
    state._find_schedule(job["id"]).update({
        "nextRunAt": time.time() - 10,
        "lastRunAt": (opened + dt.timedelta(days=2)).isoformat()})
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    assert len(api.sent) == 1


def test_switching_on_a_once_a_day_send_whose_last_run_is_ahead_of_the_clock(ctx):
    # Switched off and on in its window open now, with its lastRunAt 2 days ahead (the
    # clock was set back): due now.
    state, _owner, _api = ctx
    job, opened = _open_window(state)
    state._find_schedule(job["id"])["lastRunAt"] = (opened + dt.timedelta(days=2)).isoformat()
    state.toggle_schedule(job["id"], False, "acct")
    switched = state.toggle_schedule(job["id"], True, "acct")["schedule"]
    assert switched["nextRunAt"] <= time.time()


def test_a_run_worked_out_late_in_a_long_window_goes_out_after_its_random_delay(ctx):
    # Its window opened 13 hours ago and closed a minute ago; its run was worked out
    # from now in the window's last minutes and comes due by its random delay after the
    # window closed: it goes out (nearer its own time than its next start, 11 hours on).
    state, _owner, api = ctx
    now = dt.datetime.now()
    opened = _minute(now - dt.timedelta(hours=13))
    closed = _minute(now - dt.timedelta(minutes=1))
    job = _daily(state, opened.strftime("%H:%M"), closed.strftime("%H:%M"), exactTime=False)
    due = time.time() - 150
    state._find_schedule(job["id"]).update({"nextRunAt": due, "jitter": 140, "jitterFor": due})
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [("text", _A, "hi")]



@pytest.mark.parametrize(
    ("window", "next_run", "delay", "retrying"),
    [
        # The all-day window (the API's default, and Custom's), worked out at 23:57:30.
        (("00:00", "23:59"), (2027, 1, 4, 23, 57, 30), 100, False),
        # Its retry at 23:58:30, with a random delay of its own.
        (("00:00", "23:59"), (2027, 1, 4, 23, 58, 30), 60, True),
        # A window that ends just before its next start.
        (("09:00", "08:57"), (2027, 1, 5, 8, 57, 30), 100, False),
        # The 30-minute setting.
        (("12:00", "11:40"), (2027, 1, 5, 11, 40, 29), 619, False),
    ],
)
def test_a_run_that_comes_due_by_its_random_delay_is_not_held_near_its_next_start(
    monkeypatch, window, next_run, delay, retrying
):
    # It comes due by its random delay (or a retry's own delay), a round later, though the
    # job's next start comes first: it is on time, so it goes out (the next start's run
    # then goes out in its own window). The calendar lists it, as the scheduler sends it.
    job = _job_at(*window)
    due = dt.datetime(*next_run).timestamp()
    job.update({"nextRunAt": due, "jitter": delay, "jitterFor": due, "retrying": retrying})
    assert webapp_module._due_run_deferred(job, due + delay + 3) == ("", None)
    horizon = due + 3 * 86_400
    # The calendar as seen when the run comes due (it reads the clock).
    monkeypatch.setattr(webapp_module.time, "time", lambda: due + delay + 3)
    assert webapp_module._simulate_schedule_runs(dict(job), horizon, limit=5)[0] == due
    monkeypatch.undo()
    # The same run waiting in the queue for the hourly or daily limit is late, and waits for
    # the next start.
    pushed = due + delay + 3
    job.update({"nextRunAt": pushed, "queuedFrom": [due, pushed], "jitter": 0,
                "jitterFor": pushed})
    assert webapp_module._due_run_deferred(job, pushed + 1)[0] == "missed"


def test_a_run_switched_on_just_before_its_next_start_goes_out_after_its_random_delay(ctx):
    # Its window ends just before its next start, which is a minute or less away; its run
    # was worked out from now and comes due by its random delay: it goes out.
    state, _owner, api = ctx
    following = _minute(dt.datetime.now() + dt.timedelta(minutes=1))
    end = (following - dt.timedelta(minutes=1)).strftime("%H:%M")
    job = _daily(state, following.strftime("%H:%M"), end, exactTime=False)
    due = time.time() - 100
    state._find_schedule(job["id"]).update({"nextRunAt": due, "jitter": 95, "jitterFor": due})
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [("text", _A, "hi")]


def test_a_retry_is_for_the_time_of_the_run_it_retries(ctx):
    # A run due at its start fails: its retry, 5 minutes on, keeps the time that run was
    # for, moved on by the pause.
    state, _owner, api = ctx
    opened = _minute(dt.datetime.now() - dt.timedelta(minutes=2))
    job = _daily(state, opened.strftime("%H:%M"))
    state._find_schedule(job["id"])["nextRunAt"] = opened.timestamp()
    api.fail_times = 1
    with pytest.raises(WebError):
        state._execute_schedule(job["id"], manual=False)
    stored = state._find_schedule(job["id"])
    assert stored["retrying"] is True and stored["nextRunAt"] > time.time() + 200
    delay = webapp_module.SCHEDULE_RETRY_DELAY_SECONDS
    assert stored["retryFrom"] == [opened.timestamp() + delay, stored["nextRunAt"]]
    assert webapp_module._run_own_time(stored, time.time()) == opened.timestamp() + delay


def test_a_late_run_whose_retry_is_delayed_past_halfway_waits_for_its_next_start():
    # Monday's 09:00 run went out late at 20:55 (still nearer 09:00 than Tuesday's) and
    # failed. Its retry, due at 21:00:10, could not run until Tuesday 02:30 (LinePassport
    # was not running): it counts from the run it retries, and waits for Tuesday's 09:00.
    # On time, it goes out.
    job = _job_at("09:00")
    retry = dt.datetime(2027, 1, 4, 21, 0, 10).timestamp()
    origin = dt.datetime(2027, 1, 4, 9, 5).timestamp()
    job.update({"nextRunAt": retry, "retrying": True, "retryFrom": [origin, retry]})
    assert webapp_module._due_run_deferred(job, retry + 20) == ("", None)
    later = dt.datetime(2027, 1, 5, 2, 30).timestamp()
    why, until = webapp_module._due_run_deferred(job, later)
    assert (why, dt.datetime.fromtimestamp(until)) == ("missed", dt.datetime(2027, 1, 5, 9, 0))


def test_once_a_day_sends_whose_timing_is_equal_hold_the_same_retry_a_sign_in_cut_off():
    # Both runs failed at 09:00:03 and their retries at 09:05:03 met LINE's sign-in error:
    # the retry flag is gone, and the next run stays at the retry's time. The account is
    # connected again in the evening: both count from the run they retried, whatever
    # their windows.
    retry = dt.datetime(2027, 1, 4, 9, 5, 3).timestamp()
    origin = dt.datetime(2027, 1, 4, 9, 5, 3).timestamp()
    jobs = [_job_at("09:00", "23:00"), _job_at("09:00")]
    for job in jobs:
        job.update({"nextRunAt": retry, "retryFrom": [origin, retry]})
    for hour, minute in ((20, 30), (21, 1), (21, 2), (21, 4), (23, 30)):
        at = dt.datetime(2027, 1, 4, hour, minute).timestamp()
        decisions = [webapp_module._due_run_deferred(dict(job), at) for job in jobs]
        assert decisions[0] == decisions[1], (hour, minute)


def test_each_queue_round_moves_the_note_with_the_next_run(ctx):
    # The queue's note goes with every next run it sets, so a run that waits in the queue
    # for more than two rounds keeps the time it was for.
    state, owner, _api = ctx
    state.save_schedule_limits(
        {"accountId": "acct", "maxPerHour": 1, "maxPerDay": 1, "jitterMinutes": 0}, owner
    )
    state.record_schedule_sends("acct", 1)
    opened = _minute(dt.datetime.now() - dt.timedelta(minutes=2))
    job = _daily(state, opened.strftime("%H:%M"))
    state._find_schedule(job["id"])["nextRunAt"] = opened.timestamp()
    state.run_due_schedules()
    for _ in range(2):  # its next queue rounds come
        stored = state._find_schedule(job["id"])
        pushed = time.time() - 1
        stored.update({"nextRunAt": pushed, "queuedFrom": [stored["queuedFrom"][0], pushed]})
        state.run_due_schedules()
        stored = state._find_schedule(job["id"])
        assert stored["status"] == "queued"
        assert stored["queuedFrom"] == [opened.timestamp(), stored["nextRunAt"]]


def test_a_queued_retry_keeps_the_time_it_was_for():
    # A retry that waits in the queue counts from the time the queue kept, not from the
    # queue's latest round.
    job = _job_at("09:00")
    retry_at = dt.datetime(2027, 1, 4, 9, 5).timestamp()
    pushed = dt.datetime(2027, 1, 4, 21, 5).timestamp()
    job.update({"retrying": True, "nextRunAt": pushed, "queuedFrom": [retry_at, pushed]})
    assert webapp_module._run_own_time(job, pushed + 30) == retry_at
    why, until = webapp_module._due_run_deferred(job, pushed + 30)
    assert (why, dt.datetime.fromtimestamp(until)) == ("missed", dt.datetime(2027, 1, 5, 9, 0))


@pytest.mark.parametrize("before", [30, 1, 0.01])
def test_a_next_run_stored_just_before_its_start_waits_for_it(before):
    # A next run an older version stored a few seconds before a start is outside every
    # window, so it waits for that start (sent once, at 09:00) instead of going out at once
    # and again at 09:00.
    job = _job_at("09:00")
    start = dt.datetime(2027, 1, 4, 9, 0)
    due = start - dt.timedelta(seconds=before)
    assert _deferred(job, due, due + dt.timedelta(seconds=before / 2)) == ("missed", start)


def test_a_held_run_is_logged_when_a_read_fails_the_round(ctx, monkeypatch):
    # The round holds a missed run, then reading another schedule's settings fails (the
    # round raises): the activity log says so all the same.
    state, _owner, _api = ctx
    following = _minute(dt.datetime.now() + dt.timedelta(minutes=3))
    held = _daily(state, following.strftime("%H:%M"))
    state._find_schedule(held["id"])["nextRunAt"] = (following - dt.timedelta(days=1)).timestamp()
    state.account_store.data["accounts"].append(
        dict(state.account_store.data["accounts"][0], id="acct2")
    )
    other = _daily(state, following.strftime("%H:%M"), accountId="acct2")
    state._find_schedule(other["id"]).update({"nextRunAt": time.time() - 5, "jitterFor": None})
    real = state.schedule_settings_for

    def settings_for(account_id):
        if account_id == "acct2":
            raise OSError(111, "database down")
        return real(account_id)

    monkeypatch.setattr(state, "schedule_settings_for", settings_for)
    with pytest.raises(OSError):
        state.run_due_schedules()
    assert state._find_schedule(held["id"])["nextRunAt"] == following.timestamp()
    assert _run_log(state) == ["schedule.run.missed"]



def test_an_on_time_run_of_a_send_that_once_waited_in_the_queue_goes_out():
    # The queue's note from Monday morning stays on the job (it is ignored once the next run
    # changes): a run worked out at 23:58 that comes due by its random delay is on time.
    job = _job_at("00:00", "23:59")
    switched = dt.datetime(2027, 1, 4, 23, 58).timestamp()
    stale = [dt.datetime(2027, 1, 4, 9, 0).timestamp(), dt.datetime(2027, 1, 4, 9, 5).timestamp()]
    job.update({"nextRunAt": switched, "jitter": 90, "jitterFor": switched, "queuedFrom": stale})
    assert webapp_module._due_run_deferred(job, switched + 93) == ("", None)


def test_a_retry_that_comes_due_on_time_near_its_next_start_goes_out():
    # The run worked out at 23:50 went out at 23:52 and failed; its retry (23:57, its note
    # 23:55) comes due by its own random delay at 23:59:30: on time.
    job = _job_at("00:00", "23:59")
    retry = dt.datetime(2027, 1, 4, 23, 57).timestamp()
    note = [dt.datetime(2027, 1, 4, 23, 55).timestamp(), retry]
    job.update({"nextRunAt": retry, "retrying": True, "retryFrom": note, "jitter": 150,
                "jitterFor": retry})
    assert webapp_module._due_run_deferred(job, retry + 153) == ("", None)


@pytest.mark.parametrize(("after", "held"), [(60, False), (61, True)])
def test_a_run_is_on_time_for_a_minute_after_its_random_delay(after, held):
    # Near its next start (00:00), a run worked out at 23:58 with a random delay of 30 s is
    # on time until a minute after it came due, and late (held) after that.
    job = _job_at("00:00", "23:59")
    worked = dt.datetime(2027, 1, 4, 23, 58).timestamp()
    job.update({"nextRunAt": worked, "jitter": 30, "jitterFor": worked})
    why, _until = webapp_module._due_run_deferred(job, worked + 30 + after)
    assert why == ("missed" if held else "")


@pytest.mark.parametrize(
    "delay",
    [float("inf"), 1e12, 216_000, -5, float("-inf"), "soon", [1],
     pytest.param("90", id="text that reads as a number"),
     pytest.param(10 ** 400, id="too large for a float")],
)
def test_a_run_whose_random_delay_is_not_one_is_on_time_for_a_minute_after_its_time(delay):
    # Near its next start (00:00), a run worked out at 23:58 whose stored random delay is
    # damaged (a hand-edited store) is decided as one with no delay: on time until a minute
    # after its time, and late (held) after that.
    job = _job_at("00:00", "23:59")
    worked = dt.datetime(2027, 1, 4, 23, 58).timestamp()
    job.update({"nextRunAt": worked, "jitter": delay, "jitterFor": worked})
    assert webapp_module._run_on_time(job, worked + 60, 60) is True
    assert webapp_module._run_on_time(job, worked + 61, 60) is False
    assert webapp_module._run_on_time_safe(job, worked + 61) is False
    assert webapp_module._due_run_deferred(job, worked + 60) == ("", None)
    why, until = webapp_module._due_run_deferred(job, worked + 61)
    assert (why, until) == ("missed", dt.datetime(2027, 1, 5, 0, 0).timestamp())


def test_a_retry_of_a_run_with_no_time_of_its_own_is_on_time_by_its_pause():
    # A next run an older version stored outside the window went out late and failed: its
    # retry has no time of its own, yet comes due on time by its pause, so it goes out. A
    # retry delayed by hours (LinePassport was not running) counts from the start before.
    job = _job_at("12:03", "18:00")
    retry = dt.datetime(2027, 1, 5, 0, 6).timestamp()
    job.update({"nextRunAt": retry, "retrying": True, "retryFrom": [None, retry],
                "jitter": 30, "jitterFor": retry})
    assert webapp_module._due_run_deferred(job, retry + 33) == ("", None)
    why, until = webapp_module._due_run_deferred(job, retry + 3 * 3600)
    assert (why, dt.datetime.fromtimestamp(until)) == ("missed", dt.datetime(2027, 1, 5, 12, 3))


def test_a_retry_whose_flag_a_run_now_cleared_is_still_on_time_by_its_pause():
    # The retry of a run with no time of its own. A Run now that failed meanwhile ended the
    # retry (its flag is gone) and left its next run and its note: at that next run it
    # still comes due on time by its pause, and goes out.
    job = _job_at("12:03", "18:00")
    retry = dt.datetime(2027, 1, 5, 0, 6).timestamp()
    job.update({"nextRunAt": retry, "retryFrom": [None, retry], "jitter": 30,
                "jitterFor": retry})
    assert "retrying" not in job
    assert webapp_module._due_run_deferred(job, retry + 33) == ("", None)


def test_a_next_run_a_hair_after_its_start_is_its_own():
    # A next run at a start that a float carries a fraction of a microsecond past it (the
    # datetime rounds it up) still lies in its window.
    job = _job_at("09:00")
    start = dt.datetime(2027, 1, 4, 9, 0).timestamp()
    job["nextRunAt"] = start + 0.0000006
    assert webapp_module._run_own_time(job, start + 10) == start + 0.0000006


def _near_start(state, due_ago, delay):
    """A once-a-day send whose window ends just before its next start, 2-3 minutes away; its
    run was worked out ``due_ago`` seconds ago (inside its window), its random delay
    ``delay``."""
    following = _minute(dt.datetime.now() + dt.timedelta(minutes=3))
    end = (following - dt.timedelta(minutes=1)).strftime("%H:%M")
    job = _daily(state, following.strftime("%H:%M"), end, exactTime=False)
    due = time.time() - due_ago
    state._find_schedule(job["id"]).update({"nextRunAt": due, "jitter": delay, "jitterFor": due})
    return job, following, due


@pytest.mark.parametrize("on_time", [True, False])
def test_only_a_run_handed_to_a_sender_on_time_may_wait_for_one(ctx, on_time):
    # Its next start is 2-3 minutes away. Handed to a sender on time, it may wait for one
    # for up to 15 minutes; one the round let out late (LinePassport was not running) may
    # not, so a sender starting it now holds it.
    state, owner, _api = ctx
    job, following, due = _near_start(state, 400, 60)
    stored = state._find_schedule(job["id"])
    stored["onTimeFor"] = due if on_time else None
    with state.schedule_lock:
        assert state._scheduled_run_due(job["id"]) is on_time
    state.schedule_inflight.add(job["id"])  # the calendar decides it as the sender will
    events = state.upcoming_schedules("acct", owner, days=2)["events"]
    assert events[0]["at"] == (due if on_time else following.timestamp())
    state.schedule_inflight.discard(job["id"])


def test_a_note_for_an_earlier_next_run_gives_no_wait_for_a_sender(ctx):
    # The round handed an earlier next run over on time. The one waiting now (an edit worked
    # it out again) was never handed over on time: a sender that starts it late, near its
    # next start, holds it, and the calendar lists that start.
    state, owner, _api = ctx
    job, following, due = _near_start(state, 400, 60)
    state._find_schedule(job["id"])["onTimeFor"] = due - 300
    with state.schedule_lock:
        assert state._scheduled_run_due(job["id"]) is False
    state.schedule_inflight.add(job["id"])
    events = state.upcoming_schedules("acct", owner, days=2)["events"]
    assert events[0]["at"] == following.timestamp()
    state.schedule_inflight.discard(job["id"])


def test_the_calendar_gives_no_wait_for_a_sender_to_a_run_that_no_sender_holds(ctx):
    # Handed over on time, but LinePassport stopped before a sender began it: no sender
    # holds it now, so the next round decides it with its own margin and holds it. The
    # calendar lists what that round does.
    state, owner, api = ctx
    job, following, due = _near_start(state, 400, 60)
    state._find_schedule(job["id"])["onTimeFor"] = due
    events = state.upcoming_schedules("acct", owner, days=2)["events"]
    assert events[0]["at"] == following.timestamp()
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == []
    assert state._find_schedule(job["id"])["nextRunAt"] == following.timestamp()


@pytest.mark.parametrize(("waited", "starts"), [(840, True), (940, False)])
def test_a_run_handed_over_on_time_waits_for_a_sender_for_15_minutes_at_most(
    ctx, waited, starts
):
    # Handed to a sender on time, 2-3 minutes before its next start: a sender that starts it
    # 14 minutes after it came due sends it; one that starts it 15 minutes 40 s after holds
    # it (it is late by then, and nearer its next start).
    state, _owner, _api = ctx
    job, _following, due = _near_start(state, waited + 60, 60)
    state._find_schedule(job["id"])["onTimeFor"] = due
    with state.schedule_lock:
        assert state._scheduled_run_due(job["id"]) is starts


def test_the_round_notes_whether_a_run_goes_to_a_sender_on_time(ctx):
    # Both sends' window opens six hours from now and is open until a minute before that, so
    # their next runs (10 seconds and 10 minutes ago) lie inside it at any time of day. The
    # late one carries the note of an on-time hand-over that no sender began (LinePassport
    # stopped first): the round lets it out late, and clears the note.
    state, _owner, _api = ctx
    start = _minute(dt.datetime.now() + dt.timedelta(hours=6))
    end = (start - dt.timedelta(minutes=1)).strftime("%H:%M")
    on_time = _daily(state, start.strftime("%H:%M"), end, name="on time")
    late = _daily(state, start.strftime("%H:%M"), end, name="late")
    now = time.time()
    state._find_schedule(on_time["id"]).update({"nextRunAt": now - 10})
    state._find_schedule(late["id"]).update({"nextRunAt": now - 600, "onTimeFor": now - 600})
    release = _hold_senders(state)
    try:
        state.run_due_schedules()
        assert {on_time["id"], late["id"]} <= state.schedule_inflight  # both handed over
        assert state._find_schedule(on_time["id"])["onTimeFor"] == now - 10
        assert state._find_schedule(late["id"])["onTimeFor"] is None
    finally:
        release.set()
    state.schedule_executor.shutdown(wait=True)


def test_a_run_late_by_minutes_near_its_next_start_waits_for_it(ctx):
    # Late by 7 minutes (LinePassport was not running) and nearer its next start: the round
    # holds it, and the calendar lists its next start.
    state, owner, api = ctx
    job, following, _due = _near_start(state, 420, 0)
    events = state.upcoming_schedules("acct", owner, days=2)["events"]
    assert events[0]["at"] == following.timestamp()
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == []
    assert state._find_schedule(job["id"])["nextRunAt"] == following.timestamp()


def test_the_calendar_goes_on_from_a_random_delay_past_the_next_start(monkeypatch):
    # Switched on at 23:58:30 in the all-day window, its run draws a 150 s delay, so it goes
    # out at 00:01 on Tuesday and takes Tuesday's place: the calendar lists Wednesday next.
    job = _job_at("00:00", "23:59")
    worked = dt.datetime(2027, 1, 4, 23, 58, 30).timestamp()
    job.update({"nextRunAt": worked, "jitter": 150, "jitterFor": worked})
    monkeypatch.setattr(webapp_module.time, "time", lambda: worked + 3)
    runs = webapp_module._simulate_schedule_runs(dict(job), worked + 3 * 86_400, limit=3)
    assert [dt.datetime.fromtimestamp(at) for at in runs] == [
        dt.datetime(2027, 1, 4, 23, 58, 30), dt.datetime(2027, 1, 6, 0, 0),
        dt.datetime(2027, 1, 7, 0, 0),
    ]
    # A delay that ends before 00:00 leaves Tuesday's run in its place.
    job["jitter"] = 60
    runs = webapp_module._simulate_schedule_runs(dict(job), worked + 3 * 86_400, limit=2)
    assert dt.datetime.fromtimestamp(runs[1]) == dt.datetime(2027, 1, 5, 0, 0)


def test_the_calendar_of_an_hourly_send_is_left_as_it_was(monkeypatch):
    # Only a once-a-day send's calendar goes on from its drawn random delay. An hourly
    # send's rows are left as they were, an hour after each listed run (the scheduler itself
    # goes on from when a run ended, so its real runs come a little later).
    job = _schedule_from_body(
        {"to": _A, "text": "x", "mode": "repeat", "windowStart": "09:00", "windowEnd": "18:00",
         "intervalMinutes": 60}, account_id="acct")
    due = dt.datetime(2027, 1, 4, 10, 0).timestamp()
    job.update({"nextRunAt": due, "jitter": 120, "jitterFor": due})
    monkeypatch.setattr(webapp_module.time, "time", lambda: due - 60)
    runs = webapp_module._simulate_schedule_runs(dict(job), due + 86_400, limit=3)
    assert [dt.datetime.fromtimestamp(at) for at in runs] == [
        dt.datetime(2027, 1, 4, 10, 0), dt.datetime(2027, 1, 4, 11, 0),
        dt.datetime(2027, 1, 4, 12, 0),
    ]


@pytest.mark.parametrize(
    "delay",
    [float("inf"), float("nan"), 1e20, 1e12, -5, "soon", [1],
     pytest.param("90", id="text that reads as a number"),
     # Just outside what counts as a delay (none to a day), and a whole number too large
     # for a float.
     86_401, 86_400.5, -0.5, pytest.param(10 ** 400, id="too large for a float")],
)
def test_the_calendar_leaves_out_a_random_delay_that_is_not_one(monkeypatch, delay):
    # Its stored random delay is damaged (a hand-edited store): the calendar lists its runs
    # as if none were drawn.
    job = _job_at("09:00", "23:00")
    due = dt.datetime(2027, 1, 4, 9, 0).timestamp()
    job.update({"nextRunAt": due, "jitter": delay, "jitterFor": due})
    assert webapp_module._drawn_delay(job) == 0
    monkeypatch.setattr(webapp_module.time, "time", lambda: due - 3600)
    runs = webapp_module._simulate_schedule_runs(dict(job), due + 3 * 86_400, limit=3)
    assert [dt.datetime.fromtimestamp(at) for at in runs] == [
        dt.datetime(2027, 1, 4, 9, 0), dt.datetime(2027, 1, 5, 9, 0),
        dt.datetime(2027, 1, 6, 9, 0),
    ]


def test_a_random_delay_counts_only_for_the_next_run_it_was_drawn_for():
    job = {"nextRunAt": 5000.0, "jitter": 90, "jitterFor": 5000.0}
    assert webapp_module._drawn_delay(job) == 90
    assert webapp_module._drawn_delay({**job, "jitter": 1800}) == 1800  # the longest one
    assert webapp_module._drawn_delay({**job, "jitterFor": 4000.0}) == 0
    assert webapp_module._drawn_delay({"nextRunAt": 5000.0}) == 0


def test_a_random_delay_read_again_from_the_store_counts_for_its_next_run():
    # A store read again (after a restart) holds the next run, and the next run the delay
    # was drawn for, as two equal numbers, no longer as one object: the delay still counts.
    job = {"nextRunAt": float("5000.5"), "jitter": 90, "jitterFor": float("5000.5")}
    assert job["jitterFor"] is not job["nextRunAt"]
    assert webapp_module._drawn_delay(job) == 90


@pytest.mark.parametrize(
    "stored", [{"jitter": 0}, {"jitter": None}, {}, {"jitter": False}],
    ids=["0", "null", "missing", "false"],
)
def test_a_stored_random_delay_of_none_counts_as_none(stored):
    # A delay of none is stored for the next run of a send at its exact time (and of any
    # send whose draw gave none). It adds nothing to the time the scheduler's round counts
    # the run as on time: a minute after its time and not a second more. Here, half a
    # second later, the run is late and held for its next start, by then nearer than its
    # own time. (A run queued for the limit has a delay of none stored as well, and is late
    # whatever its delay: see _run_on_time.)
    job = _job_at("00:00", "23:59")
    worked = dt.datetime(2027, 1, 4, 23, 58).timestamp()
    job.update({"nextRunAt": worked, "jitterFor": worked, **stored})
    assert webapp_module._drawn_delay(job) == 0
    assert webapp_module._run_on_time(job, worked + 60, 60) is True
    assert webapp_module._run_on_time(job, worked + 60.5, 60) is False
    why, until = webapp_module._due_run_deferred(job, worked + 60.5)
    assert (why, until) == ("missed", dt.datetime(2027, 1, 5, 0, 0).timestamp())


def test_a_stored_random_delay_of_true_counts_as_a_second():
    # The app stores a whole number of seconds. A true in a hand-edited store is a number
    # too, the way Python reads it: a delay of one second.
    job = {"nextRunAt": 5000.0, "jitter": True, "jitterFor": 5000.0}
    assert webapp_module._drawn_delay(job) == 1


def test_a_stored_random_delay_of_up_to_a_day_is_one():
    # The scheduler draws up to 30 minutes. A stored number up to a day still counts as a
    # delay; a longer one does not.
    job = {"nextRunAt": 5000.0, "jitterFor": 5000.0}
    assert webapp_module._drawn_delay({**job, "jitter": 3600}) == 3600
    assert webapp_module._drawn_delay({**job, "jitter": 86_400}) == 86_400
    assert webapp_module._drawn_delay({**job, "jitter": 86_401}) == 0
    # The app stores whole seconds. A fraction of a second (a hand-edited store) is a number
    # from none to a day as well, and counts as it is.
    assert webapp_module._drawn_delay({**job, "jitter": 0.5}) == 0.5
    assert webapp_module._drawn_delay({**job, "jitter": 0.999}) == 0.999


def test_the_calendar_decides_a_run_whose_random_delay_is_not_one_as_one_with_none(
    monkeypatch,
):
    # 23 hours after its next run, a run with no delay is late and nearer its next start:
    # the calendar lists that start first, whatever the damaged delay holds.
    job = _job_at("09:00", "23:00")
    due = dt.datetime(2027, 1, 4, 9, 0).timestamp()
    job.update({"nextRunAt": due, "jitter": float("inf"), "jitterFor": due})
    monkeypatch.setattr(webapp_module.time, "time", lambda: due + 23 * 3600)
    runs = webapp_module._simulate_schedule_runs(dict(job), due + 3 * 86_400, limit=2)
    assert [dt.datetime.fromtimestamp(at) for at in runs] == [
        dt.datetime(2027, 1, 5, 9, 0), dt.datetime(2027, 1, 6, 9, 0),
    ]


def test_a_damaged_schedule_does_not_fail_its_accounts_calendar(ctx):
    # One schedule's stored random delay is damaged, and another's id (a hand-edited
    # store): the calendar still lists every send of the account.
    state, owner, _api = ctx
    _daily(state, "09:00", name="whole")
    delay = state._find_schedule(_daily(state, "10:00", name="delay")["id"])
    delay.update({"jitter": float("inf"), "jitterFor": delay["nextRunAt"]})
    state._find_schedule(_daily(state, "11:00", name="id")["id"])["id"] = ["damaged"]
    events = state.upcoming_schedules("acct", owner, days=2)["events"]
    assert {event["name"] for event in events} == {"whole", "delay", "id"}


@pytest.mark.parametrize("damaged", [["damaged"], {"damaged": 1}])
def test_a_damaged_id_does_not_fail_its_accounts_schedules_list(ctx, damaged):
    # One schedule's id is damaged (a hand-edited store): the account's schedules are
    # still listed, and Clear stuck jobs still answers.
    state, owner, _api = ctx
    _daily(state, "09:00", name="whole")
    state._find_schedule(_daily(state, "10:00", name="id")["id"])["id"] = damaged
    listing = state.list_schedules("acct", owner)
    assert [job["name"] for job in listing["schedules"]] == ["whole", "id"]
    assert listing["unsavedSent"] == []
    assert state.clear_stuck_schedules("acct", owner)["unsavedSent"] == []


_NOT_A_DELAY = [
    float("inf"), float("nan"), 1e12, 150_000, 86_401, -100, "soon", [1],
    pytest.param("90", id="text that reads as a number"),
    pytest.param({"seconds": 90}, id="a dict"),
    pytest.param(10 ** 400, id="too large for a float"),
]


@pytest.mark.parametrize("delay", _NOT_A_DELAY)
def test_the_round_stores_none_in_place_of_a_random_delay_that_is_not_one(ctx, delay):
    # Its stored random delay is damaged (a hand-edited store), its next run hours away.
    # The scheduler's round stores none in its place and saves that, so the round, a
    # sender's start, the on-time rule and the calendar all read the same delay.
    state, _owner, api = ctx
    start = _minute(dt.datetime.now() + dt.timedelta(hours=6)).strftime("%H:%M")
    stored = state._find_schedule(_daily(state, start, exactTime=False)["id"])
    stored.update({"jitter": delay, "jitterFor": stored["nextRunAt"]})
    state.save_schedules()
    state.run_due_schedules()
    saved = state.store.get("schedules", {"schedules": []})["schedules"]
    assert stored["jitter"] == 0 and [item["jitter"] for item in saved] == [0]
    assert api.sent == [] and not state.schedule_inflight


@pytest.mark.parametrize("delay", _NOT_A_DELAY)
def test_a_run_whose_stored_random_delay_is_not_one_goes_out_at_its_time(ctx, delay):
    # The run of such a send goes out at its time, as one with no delay. (Before, a delay
    # that was not a number failed the scheduler's round, and an endless or very long one
    # kept the run from ever coming due.)
    state, _owner, api = ctx
    job, _opened = _open_window(state, exactTime=False)
    due = time.time() - 5
    state._find_schedule(job["id"]).update({"nextRunAt": due, "jitter": delay,
                                            "jitterFor": due})
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [("text", _A, "hi")]


def test_a_stored_random_delay_below_none_does_not_send_a_run_before_its_time(ctx):
    # Its next run is a minute away and its stored random delay is damaged to less than
    # none: nothing goes out before its time.
    state, _owner, api = ctx
    job, _opened = _open_window(state, exactTime=False)
    due = time.time() + 60
    state._find_schedule(job["id"]).update({"nextRunAt": due, "jitter": -100,
                                            "jitterFor": due})
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == []


def test_the_round_leaves_a_stored_random_delay_of_up_to_a_day_as_it_is(ctx):
    # A delay the scheduler drew (up to 30 minutes), or any number up to a day, stays as it
    # is, and the run waits for it.
    state, _owner, api = ctx
    job, _opened = _open_window(state, exactTime=False)
    due = time.time() - 5
    stored = state._find_schedule(job["id"])
    for delay in (90, 1800, 86_400, 90.5):
        stored.update({"nextRunAt": due, "jitter": delay, "jitterFor": due})
        state.run_due_schedules()
        assert stored["jitter"] == delay and type(stored["jitter"]) is type(delay)
    assert api.sent == [] and not state.schedule_inflight


def test_a_random_delay_drawn_above_a_day_is_stored_as_none_in_the_same_round(
    ctx, monkeypatch
):
    # The page saves a delay setting of 30 minutes at most. A hand-edited one (a damaged
    # store) lets the round draw a delay above a day: the same round stores none in its
    # place.
    state, _owner, api = ctx
    state._save_schedule_settings("acct", {"jitterMinutes": 3000})
    monkeypatch.setattr(webapp_module.random, "randint", lambda _low, high: high)
    start = _minute(dt.datetime.now() + dt.timedelta(hours=6)).strftime("%H:%M")
    stored = state._find_schedule(_daily(state, start, exactTime=False)["id"])
    assert "jitter" not in stored
    state.run_due_schedules()
    assert stored["jitter"] == 0 and stored["jitterFor"] == stored["nextRunAt"]
    assert api.sent == [] and not state.schedule_inflight


@pytest.mark.parametrize(("minutes", "longest"), [(7, 420), (0, 0)])
def test_the_round_draws_a_random_delay_from_none_up_to_the_setting(
    ctx, monkeypatch, minutes, longest
):
    # The delay is drawn from none up to the account's setting, in seconds: up to 420 for a
    # setting of 7 minutes, and none for a setting of none.
    state, _owner, api = ctx
    _only_the_tests_rounds(state, monkeypatch)
    state._save_schedule_settings("acct", {"jitterMinutes": minutes})
    asked = []
    caller = threading.current_thread()

    def randint(low, high):
        if threading.current_thread() is caller:
            asked.append((low, high))
        return high

    monkeypatch.setattr(webapp_module.random, "randint", randint)
    start = _minute(dt.datetime.now() + dt.timedelta(hours=6)).strftime("%H:%M")
    stored = state._find_schedule(_daily(state, start, exactTime=False)["id"])
    state.run_due_schedules()
    assert asked == [(0, longest)] and stored["jitter"] == longest
    assert api.sent == [] and not state.schedule_inflight


def test_the_round_keeps_a_random_delay_it_has_just_drawn(ctx, monkeypatch):
    # A delay the round draws within the setting stays as drawn, in the job and in the
    # saved file.
    state, _owner, api = ctx
    monkeypatch.setattr(webapp_module.random, "randint", lambda _low, _high: 97)
    start = _minute(dt.datetime.now() + dt.timedelta(hours=6)).strftime("%H:%M")
    stored = state._find_schedule(_daily(state, start, exactTime=False)["id"])
    assert "jitter" not in stored
    state.run_due_schedules()
    saved = state.store.get("schedules", {"schedules": []})["schedules"]
    assert stored["jitter"] == 97 and stored["jitterFor"] == stored["nextRunAt"]
    assert [item["jitter"] for item in saved] == [97]
    assert api.sent == [] and not state.schedule_inflight


def test_a_round_with_nothing_to_store_does_not_save_the_schedules(ctx, monkeypatch):
    # A send at its exact time, hours ahead: the first round stores its delay (none) for
    # its next run. The rounds after it (one comes every ten seconds) have nothing to
    # store, and do not save the schedules again.
    state, _owner, api = ctx
    start = _minute(dt.datetime.now() + dt.timedelta(hours=6)).strftime("%H:%M")
    stored = state._find_schedule(_daily(state, start)["id"])
    state.run_due_schedules()
    assert stored["jitter"] == 0 and stored["jitterFor"] == stored["nextRunAt"]
    saved = []
    real_set, real_set_many = state.store.set, state.store.set_many

    def store_set(key, value):
        saved.append(key)
        return real_set(key, value)

    def store_set_many(values):
        saved.extend(values)
        return real_set_many(values)

    monkeypatch.setattr(state.store, "set", store_set)
    monkeypatch.setattr(state.store, "set_many", store_set_many)
    state.run_due_schedules()
    state.run_due_schedules()
    assert "schedules" not in saved
    assert api.sent == [] and not state.schedule_inflight


def test_the_round_leaves_a_stored_random_delay_of_true_as_it_is(ctx):
    # A true in a hand-edited store counts as a delay of a second (see above), so the
    # round does not store none in its place.
    state, _owner, api = ctx
    start = _minute(dt.datetime.now() + dt.timedelta(hours=6)).strftime("%H:%M")
    stored = state._find_schedule(_daily(state, start, exactTime=False)["id"])
    stored.update({"jitter": True, "jitterFor": stored["nextRunAt"]})
    state.run_due_schedules()
    assert stored["jitter"] is True
    assert api.sent == [] and not state.schedule_inflight


@pytest.mark.parametrize("kind", ["exact time", "every hour", "one time"])
def test_the_round_stores_none_in_place_of_a_damaged_delay_for_every_kind_of_send(ctx, kind):
    # Not only for a once-a-day send with a random delay: a send at its exact time, one
    # every hour and a one-time one get none in place of a damaged delay as well, so none
    # of them fails the scheduler's round or never comes due.
    state, _owner, api = ctx
    ahead = _minute(dt.datetime.now() + dt.timedelta(hours=6))
    if kind == "exact time":
        job = _daily(state, ahead.strftime("%H:%M"))
    elif kind == "every hour":
        job = _daily(state, ahead.strftime("%H:%M"),
                     (ahead + dt.timedelta(hours=1)).strftime("%H:%M"),
                     exactTime=False, intervalMinutes=60)
    else:
        job = _job(state, runAt=ahead.strftime("%Y-%m-%dT%H:%M"))
    stored = state._find_schedule(job["id"])
    assert stored["nextRunAt"] == ahead.timestamp()
    stored.update({"jitter": "soon", "jitterFor": stored["nextRunAt"]})
    state.save_schedules()
    state.run_due_schedules()
    saved = state.store.get("schedules", {"schedules": []})["schedules"]
    assert stored["jitter"] == 0 and [item["jitter"] for item in saved] == [0]
    assert api.sent == [] and not state.schedule_inflight


def test_a_senders_start_reads_the_stored_delay_when_the_owner_moved_the_next_run(
    ctx, monkeypatch
):
    # The state is set by hand here and a sender's check is called directly (the next test
    # goes through a real hand-over). A delay of 10 minutes is stored for a send's next
    # run. The owner switches the send off and on, which works its next run out from now.
    # Until the next round draws a delay for that next run, the stored one is the earlier
    # run's: a sender's start counts it, so a run that waited for a sender is not started
    # (the next round draws its delay and runs it after that), while the on-time rule and
    # the calendar count none. Had the earlier delay been none, the sender would start the
    # run.
    state, _owner, api = ctx
    _only_the_tests_rounds(state, monkeypatch)
    job, _opened = _open_window(state, exactTime=False)
    stored = state._find_schedule(job["id"])
    was = time.time() - 100
    stored.update({"nextRunAt": was, "jitter": 600, "jitterFor": was, "onTimeFor": was})
    state.toggle_schedule(job["id"], False, "acct")
    state.toggle_schedule(job["id"], True, "acct")
    assert stored["jitterFor"] != stored["nextRunAt"] and stored["jitter"] == 600
    assert webapp_module._drawn_delay(stored) == 0
    assert webapp_module._run_on_time_safe(stored, time.time()) is True
    with state.schedule_lock:
        assert state._scheduled_run_due(job["id"]) is False
        stored["jitter"] = 0
        assert state._scheduled_run_due(job["id"]) is True
    assert api.sent == []


@pytest.mark.parametrize("move", ["switched on again", "changed when it runs", "Resume all"])
def test_a_run_moved_while_it_waits_for_a_sender_goes_out_once_at_the_next_round(
    ctx, monkeypatch, move
):
    # The same through a real hand-over. A run whose delay of 10 minutes has passed comes
    # due at a round and waits for a free sender. The owner moves its next run to now. The
    # freed sender does not start the run: the stored delay is still the earlier run's.
    # The next round draws a delay for the new next run (none here), and the run goes out,
    # once.
    state, owner, api = ctx
    _only_the_tests_rounds(state, monkeypatch)
    opened = _minute(dt.datetime.now() - dt.timedelta(minutes=30))
    job = _daily(state, opened.strftime("%H:%M"),
                 (opened + dt.timedelta(minutes=60)).strftime("%H:%M"), exactTime=False)
    stored = state._find_schedule(job["id"])
    was = time.time() - 630
    stored.update({"nextRunAt": was, "jitter": 600, "jitterFor": was})
    release = _hold_senders(state)
    try:
        state.run_due_schedules()
        assert job["id"] in state.schedule_inflight
        if move == "switched on again":
            state.toggle_schedule(job["id"], False, "acct")
            state.toggle_schedule(job["id"], True, "acct")
        elif move == "changed when it runs":
            earlier = (opened - dt.timedelta(minutes=5)).strftime("%H:%M")
            state.update_schedule(job["id"], {"accountId": "acct", "windowStart": earlier})
        else:
            state.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
            state.set_schedules_paused({"accountId": "acct", "paused": False}, owner)
        stored = state._find_schedule(job["id"])
        moved = stored["nextRunAt"]
        assert moved > was + 600 and stored["jitter"] == 600 and stored["jitterFor"] == was
    finally:
        release.set()
    assert _wait_until(lambda: not state.schedule_inflight)
    assert api.sent == [] and not stored.get("running")  # the freed sender left it
    monkeypatch.setattr(webapp_module.random, "randint", lambda _low, _high: 0)
    state.run_due_schedules()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [("text", _A, "hi")]
    stored = state._find_schedule(job["id"])
    assert stored["jitter"] == 0 and stored["jitterFor"] == moved
    assert stored["sentCount"] == 1 and state.schedule_inflight == set()


def test_a_freed_sender_starts_a_run_whose_stored_delay_an_edit_removed(ctx, monkeypatch):
    # A send at its exact time waits for a free sender. The owner unticks "exact time": the
    # edit keeps its next run and removes the stored delay (the next round sets it again).
    # The freed sender reads a delay of none and starts the run.
    state, _owner, api = ctx
    _only_the_tests_rounds(state, monkeypatch)
    job, _opened = _open_window(state)
    state._find_schedule(job["id"])["nextRunAt"] = time.time() - 5
    release = _hold_senders(state)
    try:
        state.run_due_schedules()
        assert job["id"] in state.schedule_inflight
        state.update_schedule(job["id"], {"accountId": "acct", "exactTime": False})
        assert "jitter" not in state._find_schedule(job["id"])
    finally:
        release.set()
    state.schedule_executor.shutdown(wait=True)
    assert api.sent == [("text", _A, "hi")]


def test_a_drawn_random_delay_is_kept_through_a_restart(ctx, tmp_path, monkeypatch):
    # The round drew a delay of 97 s for a run and saved it. A second state reads the same
    # store, as LinePassport does when it starts again: its first round reads the same
    # delay, draws none in its place and sends nothing yet.
    state, _owner, api = ctx
    _only_the_tests_rounds(state, monkeypatch)
    monkeypatch.setattr(webapp_module.random, "randint", lambda _low, _high: 97)
    job, _opened = _open_window(state, exactTime=False)
    state.run_due_schedules()
    assert state._find_schedule(job["id"])["jitter"] == 97 and api.sent == []
    again = _state(tmp_path)
    try:
        _only_the_tests_rounds(again, monkeypatch)
        # A delay drawn again after the restart would show: it would be 5.
        monkeypatch.setattr(webapp_module.random, "randint", lambda _low, _high: 5)
        monkeypatch.setattr(again, "get_api", lambda account_id=None, require_auth=True: api)
        stored = again._find_schedule(job["id"])
        # Read again, the two next runs are equal numbers and no longer one object.
        assert stored["jitterFor"] == stored["nextRunAt"]
        assert stored["jitterFor"] is not stored["nextRunAt"]
        assert webapp_module._drawn_delay(stored) == 97
        again.run_due_schedules()
        again.schedule_executor.shutdown(wait=True)
        assert stored["jitter"] == 97 and api.sent == [] and not stored.get("running")
    finally:
        again.close()


def test_a_retry_note_left_from_an_earlier_run_is_ignored():
    # Monday's retry left its note; on Tuesday a run worked out from now at 15:00 comes due
    # late at 23:30, nearer 15:00 than Wednesday's 09:00: it goes out.
    job = _job_at("09:00", "23:00")
    worked = dt.datetime(2027, 1, 5, 15, 0).timestamp()
    note = [dt.datetime(2027, 1, 4, 9, 5).timestamp(),
            dt.datetime(2027, 1, 4, 9, 5, 3).timestamp()]
    job.update({"nextRunAt": worked, "retryFrom": note})
    assert webapp_module._run_own_time(job, worked + 60) == worked
    late = dt.datetime(2027, 1, 5, 23, 30).timestamp()
    assert webapp_module._due_run_deferred(job, late) == ("", None)


def test_a_retry_note_left_from_an_earlier_run_does_not_put_a_run_on_time():
    # Monday's retry left its note. Tuesday's next run, 22:00, lies outside the send's
    # window (09:00-21:00, its end moved since), so it has no time of its own; it comes due
    # by its random delay at 22:02. The note does not make it a retry: it is not on time,
    # and, counted from the start before it, it waits for Wednesday's 09:00.
    job = _job_at("09:00", "21:00")
    worked = dt.datetime(2027, 1, 5, 22, 0).timestamp()
    note = [dt.datetime(2027, 1, 4, 9, 5).timestamp(),
            dt.datetime(2027, 1, 4, 9, 5, 3).timestamp()]
    job.update({"nextRunAt": worked, "jitter": 120, "jitterFor": worked, "retryFrom": note})
    why, until = webapp_module._due_run_deferred(job, worked + 120)
    assert (why, dt.datetime.fromtimestamp(until)) == ("missed", dt.datetime(2027, 1, 6, 9, 0))


def test_a_retry_of_a_run_with_no_time_of_its_own_has_none(ctx):
    # A next run an older version stored 30 s before the window's start has no time of its
    # own; it fails, and its retry keeps none.
    state, _owner, api = ctx
    opened = _minute(dt.datetime.now() - dt.timedelta(minutes=2))
    job = _daily(state, opened.strftime("%H:%M"))
    state._find_schedule(job["id"])["nextRunAt"] = opened.timestamp() - 30
    api.fail_times = 1
    with pytest.raises(WebError):
        state._execute_schedule(job["id"], manual=False)
    stored = state._find_schedule(job["id"])
    assert stored["retryFrom"] == [None, stored["nextRunAt"]]
    assert webapp_module._run_own_time(stored, time.time()) is None
