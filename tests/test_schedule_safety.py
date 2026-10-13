"""Scheduled sends: limits, jitter, pause, many chats, retry, calendar, defaults."""

from __future__ import annotations

import base64
import datetime as dt
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
    payload = state.set_schedules_paused({"accountId": "acct", "paused": False}, owner)
    assert payload["settings"]["paused"] is False
    assert state._find_schedule(repeat["id"])["nextRunAt"] >= time.time() - 1


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
