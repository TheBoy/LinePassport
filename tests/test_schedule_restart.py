"""Scheduled sends across a stop of LinePassport: no chat gets one run twice."""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_schedule_safety import (
    _A,
    _B,
    _daily,
    _job,
    _minute,
    _only_the_tests_rounds,
    _open_window,
    _SendApi,
    _state,
    _store_fails,
    _wait_until,
)

from okline import webapp as webapp_module
from okline.webapp import WebAuth, WebError

_C = "u" + "3" * 32
_D = "u" + "4" * 32
_THREE = [{"mid": _A, "name": "Ann"}, {"mid": _B, "name": "Bob"}, {"mid": _C, "name": "Cat"}]
_UNKNOWN = "may not have got it, LinePassport stopped while it was being sent"


class _Killed(BaseException):
    """LinePassport's process is killed: nothing of it runs afterwards, not even what
    handles an Exception."""


def _kill():
    raise _Killed


def _shop(state):
    """A signed-in owner with one LINE account, "acct"; returns the owner."""
    token = state.web_auth.setup("owner@example.com", "owner-pass")
    owner = state.web_auth.current_user(WebAuth.cookie_header(token))
    state.account_store.data["accounts"] = [
        {"id": "acct", "label": "Shop", "mid": "U-me", "ownerId": owner["id"], "tokenFile": ""}
    ]
    state.account_store.save()
    state.web_auth.grant_account_access(owner["id"], "acct")
    return state.web_auth._find_user(owner["id"])


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    """LinePassport before the stop: only the test starts its scheduler's rounds."""
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    state = _state(tmp_path)
    owner = _shop(state)
    monkeypatch.setattr(webapp_module, "SCHEDULE_TARGET_GAP_SECONDS", (0.0, 0.0))
    api = _SendApi()
    monkeypatch.setattr(state, "get_api", lambda account_id=None, require_auth=True: api)
    _only_the_tests_rounds(state, monkeypatch)
    try:
        yield state, owner, api
    finally:
        state.close()


@pytest.fixture
def restart(tmp_path, monkeypatch):
    """LinePassport as it starts again: another WebState on the same stored state (the
    first one stands for the process that ended, and runs nothing more). It sends
    through ``api``, and only the test starts its scheduler's rounds."""
    started = []

    def start(api):
        again = _state(tmp_path)
        started.append(again)
        _only_the_tests_rounds(again, monkeypatch)
        monkeypatch.setattr(again, "get_api", lambda account_id=None, require_auth=True: api)
        return again

    yield start
    for state in started:
        state.close()


class _Pause:
    """The scheduler's stop event, with the first pause between two chats of the send
    the test runs replaced by ``then``: what happens while the send waits there."""

    def __init__(self, event, then):
        self.event = event
        self.then = then
        self.thread = threading.current_thread()
        self.used = False

    def is_set(self):
        return self.event.is_set()

    def set(self):
        self.event.set()

    def wait(self, timeout=None):
        if self.used or threading.current_thread() is not self.thread:
            return self.event.wait(timeout)
        self.used = True
        self.then()
        return self.event.is_set()


def _in_the_pause(state, monkeypatch, then):
    """``then`` happens while the send the test runs next pauses after its first chat."""
    monkeypatch.setattr(state.scheduler, "stop_event", _Pause(state.scheduler.stop_event, then))


def _killed_after_the_first_chat(state, monkeypatch, job, *, manual=False):
    """Run the job's send; LinePassport is killed in the pause after its first chat."""
    _in_the_pause(state, monkeypatch, _kill)
    with pytest.raises(_Killed):
        state._execute_schedule(job["id"], manual=manual)


def _saved(state, schedule_id):
    """The job as the store holds it: what a process that ends now leaves behind."""
    [job] = [
        item for item in state.store.get("schedules", {"schedules": []})["schedules"]
        if item["id"] == schedule_id
    ]
    return job


def _turns(job):
    return [(chat["name"], chat["state"]) for chat in job["runProgress"]["chats"]]


def _sent_to(api):
    return [to for _kind, to, _text in api.sent]


def _round(state):
    """One round of the scheduler, and every send it starts to its end."""
    state.run_due_schedules()
    assert _wait_until(lambda: not state.schedule_inflight)


def _logged(state):
    return [log["action"] for log in state.store.get("bot_logs", {"logs": []})["logs"]]


# -- killed partway ---------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["once", "repeat"])
def test_a_send_killed_between_two_chats_goes_on_with_the_others_after_the_restart(
    ctx, restart, monkeypatch, mode
):
    # The case of 2026-11-24: a send to three chats, and LinePassport's process is killed
    # in the pause after the first (a Docker container stopped or redeployed, a crash).
    # Before, the next start sent it to all three again. Now the store holds the first
    # chat's turn, and the next start goes on with the two others.
    state, _owner, api = ctx
    job = _job(state, mode=mode, intervalMinutes=60, exactTime=True, targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, job)
    assert api.sent == [("text", _A, "hi Ann")]
    left = _saved(state, job["id"])
    assert left["running"] is True and left["sentCount"] == 0 and left["history"] == []
    assert left["runProgress"]["manual"] is False
    assert left["runProgress"]["chats"] == [
        {"mid": _A, "name": "Ann", "state": "sent", "messageIds": ["m1"]}
    ]
    again = restart(api)
    cut = again._find_schedule(job["id"])
    assert not cut["running"] and "runToken" not in cut and "runningSince" not in cut
    assert cut["status"] == "waiting" and not cut.get("lastError")
    assert cut["runProgress"]["cut"] == "stopped" and _turns(cut) == [("Ann", "sent")]
    assert _saved(again, job["id"]) == cut
    _round(again)
    assert api.sent == [("text", _A, "hi Ann"), ("text", _B, "hi Bob"), ("text", _C, "hi Cat")]
    done = again._find_schedule(job["id"])
    # One run: one history line, written as it ended, with the ids from before and
    # after the stop.
    assert done["history"] == [
        {"at": done["lastRunAt"], "ok": True, "messageIds": ["m1", "m2", "m3"], "resumed": True}
    ]
    assert done["sentCount"] == 1 and done["lastError"] is None
    assert done["lastMessageId"] == "m3" and "runProgress" not in done
    assert not done["running"] and "runToken" not in done
    if mode == "once":
        assert done["status"] == "completed" and done["enabled"] is False
    else:
        assert done["status"] == "waiting" and done["nextRunAt"] > time.time() + 3000
    assert _saved(again, job["id"]) == done
    assert _logged(again).count("schedule.run.resume") == 1
    # Nothing is left of it: a later round, and a later start, send nothing.
    _round(again)
    _round(restart(api))
    assert len(api.sent) == 3


@pytest.mark.parametrize("arrived", [True, False])
def test_a_chat_that_was_being_sent_to_when_linepassport_was_killed_gets_nothing_again(
    ctx, restart, arrived
):
    # Killed while the message to the second chat was on its way: nobody knows whether
    # it arrived. Its turn was saved before the send, so it gets nothing again, and the
    # run's report names it; the third chat gets it.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    original = api.send_text

    def send(to, text):
        if to != _B:
            return original(to, text)
        if arrived:
            original(to, text)
        raise _Killed

    api.send_text = send
    with pytest.raises(_Killed):
        state._execute_schedule(job["id"], manual=False)
    left = _saved(state, job["id"])
    assert left["running"] is True and _turns(left) == [("Ann", "sent"), ("Bob", "sending")]
    del api.send_text
    again = restart(api)
    _round(again)
    assert _sent_to(api) == ([_A, _B, _C] if arrived else [_A, _C])
    done = again._find_schedule(job["id"])
    assert done["lastError"] == f"Sent to 2/3 chats. Bob: {_UNKNOWN}"
    assert done["history"][-1]["ok"] is False and done["history"][-1]["resumed"] is True
    assert done["history"][-1]["error"] == done["lastError"]
    assert done["sentCount"] == 1 and done["enabled"] is False and done["status"] == "error"
    assert "runProgress" not in done
    _round(restart(api))
    assert _sent_to(api) == ([_A, _B, _C] if arrived else [_A, _C])


def test_a_send_to_one_chat_that_was_killed_as_it_went_out_is_not_sent_again(ctx, restart):
    state, _owner, api = ctx
    job = _job(state, targetName="Ann")
    api.send_text = lambda to, text: _kill()
    with pytest.raises(_Killed):
        state._execute_schedule(job["id"], manual=False)
    assert _turns(_saved(state, job["id"])) == [("Ann", "sending")]
    del api.send_text
    again = restart(api)
    _round(again)
    done = again._find_schedule(job["id"])
    assert api.sent == [] and done["enabled"] is False and done["sentCount"] == 1
    assert done["lastError"] == f"Sent to 0/1 chats. Ann: {_UNKNOWN}"


def test_a_send_killed_before_it_reached_any_chat_is_due_again_in_full(
    ctx, restart, monkeypatch
):
    # Killed while it made what it sends: no chat had its turn, so the job holds no
    # record, which is also all the store holds of a run that a version before these
    # records left behind. As before, it is marked as recovered and goes out in full.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    making = webapp_module._resolve_job_contents
    monkeypatch.setattr(webapp_module, "_resolve_job_contents", lambda *a, **k: _kill())
    with pytest.raises(_Killed):
        state._execute_schedule(job["id"], manual=False)
    left = _saved(state, job["id"])
    assert left["running"] is True and left["runToken"] and "runProgress" not in left
    monkeypatch.setattr(webapp_module, "_resolve_job_contents", making)
    again = restart(api)
    recovered = again._find_schedule(job["id"])
    assert recovered["status"] == "error" and not recovered["running"]
    assert recovered["lastError"] == "Recovered after the previous process stopped unexpectedly."
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]
    done = again._find_schedule(job["id"])
    assert done["history"] == [
        {"at": done["lastRunAt"], "ok": True, "messageIds": ["m1", "m2", "m3"]}
    ]


def test_a_send_killed_after_only_failed_chats_is_due_again_in_full(ctx, restart, monkeypatch):
    # The first chat's send failed and LinePassport was killed in the pause after it: the
    # run had reached nobody, so its record counts for nothing and it goes out in full.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    api.fail_for = {_A}
    _killed_after_the_first_chat(state, monkeypatch, job)
    left = _saved(state, job["id"])
    assert left["running"] is True and _turns(left) == [("Ann", "failed")]
    assert left["runProgress"]["chats"][0]["error"] == "blocked"
    api.fail_for = set()
    again = restart(api)
    recovered = again._find_schedule(job["id"])
    assert "runProgress" not in recovered and recovered["status"] == "error"
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]
    assert "resumed" not in again._find_schedule(job["id"])["history"][-1]


# -- Clear stuck jobs -------------------------------------------------------------------


@pytest.mark.parametrize("stuck", [True, False])
def test_clear_stuck_jobs_after_the_restart_does_not_send_to_the_first_chat_again(
    ctx, restart, monkeypatch, stuck
):
    # After the restart the owner presses Clear stuck jobs. It resets the job's running
    # and error state as before, and leaves its record of the chats that have had their
    # turn: the send still goes on with the two others, and the first gets nothing
    # again. (A send cut short is not stuck by itself: here the job also shows an
    # error from an earlier run, or Clear stuck jobs has nothing to clear.)
    state, owner, api = ctx
    job = _job(state, targets=_THREE)
    if stuck:
        state._find_schedule(job["id"])["lastError"] = "an earlier failure"
        state.save_schedules()
    _killed_after_the_first_chat(state, monkeypatch, job)
    again = restart(api)
    cleared = again.clear_stuck_schedules("acct", again.web_auth._find_user(owner["id"]))
    assert cleared["cleared"] == (1 if stuck else 0)
    job_now = again._find_schedule(job["id"])
    assert job_now["lastError"] is None and job_now["status"] == "waiting"
    assert not job_now["running"] and _turns(job_now) == [("Ann", "sent")]
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]
    done = again._find_schedule(job["id"])
    assert done["history"][-1] == {
        "at": done["lastRunAt"], "ok": True, "messageIds": ["m1", "m2", "m3"], "resumed": True,
    }
    assert [entry.get("action") for entry in done["history"][:-1]] == (
        ["clear_stuck"] if stuck else []
    )
    assert done["sentCount"] == 1 and done["status"] == "completed"
    _round(again)
    assert len(api.sent) == 3


def test_clear_stuck_jobs_while_a_send_goes_out_does_not_send_to_a_chat_again(
    ctx, restart, monkeypatch
):
    # Clear stuck jobs is pressed while the send pauses after its first chat, and then
    # LinePassport is killed. The job is saved as not running, with its record: the next
    # start goes on with the two others.
    state, owner, api = ctx
    job = _job(state, targets=_THREE)

    def clear_then_kill():
        assert state.clear_stuck_schedules("acct", owner)["cleared"] == 1
        _kill()

    _in_the_pause(state, monkeypatch, clear_then_kill)
    with pytest.raises(_Killed):
        state._execute_schedule(job["id"], manual=False)
    left = _saved(state, job["id"])
    assert left["running"] is False and left["status"] == "waiting"
    assert _turns(left) == [("Ann", "sent")]
    again = restart(api)
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]
    done = again._find_schedule(job["id"])
    assert done["sentCount"] == 1 and done["history"][-1]["resumed"] is True


def test_a_send_goes_on_to_its_other_chats_after_clear_stuck_jobs(ctx, monkeypatch):
    # As before: Clear stuck jobs does not end a send that is going out.
    state, owner, api = ctx
    job = _job(state, targets=_THREE)
    _in_the_pause(state, monkeypatch, lambda: state.clear_stuck_schedules("acct", owner))
    assert state._execute_schedule(job["id"], manual=False)["ok"] is True
    assert _sent_to(api) == [_A, _B, _C]
    done = state._find_schedule(job["id"])
    assert done["sentCount"] == 1 and "runProgress" not in done
    assert "resumed" not in done["history"][-1]
    _round(state)
    assert len(api.sent) == 3


# -- a stop the system asks for ---------------------------------------------------------


def test_a_stop_the_system_asked_for_pauses_a_send_between_two_chats(ctx, restart):
    # docker stop, a deploy: serve() notes that the stop is the system's (SIGTERM), and
    # the send, partway through its chats, is left as it is instead of being ended with
    # a report as Ctrl-C ends it. It goes on after the next start.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    original = api.send_text

    def send(to, text):
        result = original(to, text)
        state.stop_pauses_sends = True
        state.scheduler.stop_event.set()  # SIGTERM as the first message goes out
        return result

    api.send_text = send
    result = state._execute_schedule(job["id"], manual=False)
    assert result["ok"] is False and result["cut"] is True and result["messageIds"] == ["m1"]
    left = _saved(state, job["id"])
    assert left["running"] is True and left["history"] == [] and not left.get("lastError")
    assert _turns(left) == [("Ann", "sent")] and left == state._find_schedule(job["id"])
    del api.send_text
    again = restart(api)
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]
    done = again._find_schedule(job["id"])
    assert done["history"] == [
        {"at": done["lastRunAt"], "ok": True, "messageIds": ["m1", "m2", "m3"], "resumed": True}
    ]


def test_a_stop_the_system_asked_for_before_any_chat_leaves_the_send_due(ctx, restart):
    # As a stop with Ctrl-C does: a send that reached nobody is not run.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    status = state._find_schedule(job["id"])["status"]
    state.stop_pauses_sends = True
    state.scheduler.stop_event.set()
    assert state._execute_schedule(job["id"], manual=False)["ok"] is False
    left = _saved(state, job["id"])
    assert not left.get("running") and left["status"] == status and "runProgress" not in left
    again = restart(api)
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]


def _stop_signals(state=None):
    class Host:
        stop_pauses_sends = False

    return webapp_module._StopSignals(state or Host())


def test_the_first_stop_signal_counts_and_a_second_one_changes_nothing():
    stop = _stop_signals()
    assert stop.named() == ""
    stop.serving = True
    with pytest.raises(KeyboardInterrupt):  # ends serve_forever, as Ctrl-C does
        stop._on_signal(signal.SIGTERM, None)
    assert stop.received == signal.SIGTERM and stop.state.stop_pauses_sends is True
    assert stop.named() == " (SIGTERM)"
    # One that comes during the shutdown after Ctrl-C is noted and changes nothing:
    # the stop stays Ctrl-C's, which ends the sends.
    late = _stop_signals()
    try:
        stop._on_signal(signal.SIGTERM, None)  # a second one: nothing more
        late._on_signal(signal.SIGTERM, None)
    except KeyboardInterrupt:  # (not left to end the whole test run)
        pytest.fail("a signal during the shutdown broke it off")
    assert late.received == signal.SIGTERM and late.state.stop_pauses_sends is False
    assert late.named() == ""
    # One that comes just before serve_forever ends it as it begins.
    early = _stop_signals()
    early._on_signal(signal.SIGTERM, None)
    with pytest.raises(KeyboardInterrupt):
        early.serve_begins()
    assert early.state.stop_pauses_sends is True and early.named() == " (SIGTERM)"


def test_stop_signals_are_handled_only_in_the_main_thread_and_put_back():
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("the tests do not run in the main thread")
    if signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:
        pytest.skip("SIGTERM is handled or ignored already")
    elsewhere = _stop_signals()
    thread = threading.Thread(target=elsewhere.install)
    thread.start()
    thread.join()
    assert elsewhere._handled == [] and signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    ctrl_c = signal.getsignal(signal.SIGINT)
    stop = _stop_signals()
    stop.install()
    try:
        assert signal.SIGTERM in stop._handled
        assert signal.getsignal(signal.SIGTERM) == stop._on_signal
        assert signal.getsignal(signal.SIGINT) is ctrl_c  # Ctrl-C is left as it is
        if hasattr(signal, "SIGBREAK"):  # Windows: Ctrl-Break
            assert signal.getsignal(signal.SIGBREAK) == stop._on_signal
    finally:
        stop.restore()
    assert stop._handled == [] and signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    # A signal that is ignored on purpose stays ignored.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    try:
        ignoring = _stop_signals()
        ignoring.install()
        assert signal.SIGTERM not in ignoring._handled
        assert signal.getsignal(signal.SIGTERM) is signal.SIG_IGN
        ignoring.restore()
    finally:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)


def _send_sigterm_to_this_process():
    if sys.platform == "win32":
        # Windows cannot send SIGTERM to a process from outside: os.kill would end this
        # one at once. Raised inside it, the signal runs its handler all the same.
        signal.raise_signal(signal.SIGTERM)
    else:
        os.kill(os.getpid(), signal.SIGTERM)


def test_sigterm_stops_okline_web_in_order_and_its_send_goes_on_after_the_restart(
    tmp_path, monkeypatch, capsys
):
    # okline web as docker stop ends it: SIGTERM while a send to three chats pauses
    # after the first. serve() shuts down in order and returns 0; the send is left
    # with its first chat's turn, and the next start sends to the two others.
    if threading.current_thread() is not threading.main_thread():
        pytest.skip("the tests do not run in the main thread")
    if signal.getsignal(signal.SIGTERM) is not signal.SIG_DFL:
        pytest.skip("SIGTERM is handled or ignored already")
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    api = _SendApi()
    monkeypatch.setattr(
        webapp_module.WebState, "get_api", lambda self, account_id=None, require_auth=True: api
    )
    seed = _state(tmp_path)
    try:
        _shop(seed)
        job = _job(seed, targets=_THREE)
    finally:
        seed.close()
    monkeypatch.setattr(webapp_module, "SCHEDULE_TARGET_GAP_SECONDS", (60.0, 60.0))
    servers = []
    bind = webapp_module._bind_server

    def bind_and_keep(config):
        servers.append(bind(config))
        return servers[-1]

    monkeypatch.setattr(webapp_module, "_bind_server", bind_and_keep)
    problems = []

    def first_turn_saved(state):
        saved = _saved(state, job["id"])
        return "runProgress" in saved and _turns(saved) == [("Ann", "sent")]

    def stop_it():
        try:
            assert _wait_until(lambda: bool(servers))
            state = servers[0].state
            state.run_due_schedules()
            # The first chat's turn has ended and is saved: the send pauses now.
            assert _wait_until(lambda: first_turn_saved(state))
            assert callable(signal.getsignal(signal.SIGTERM))  # or it would end the tests
            _send_sigterm_to_this_process()
        except BaseException as exc:
            problems.append(exc)
            if servers:
                servers[0].shutdown()  # never leave serve() running

    stopper = threading.Thread(target=stop_it, daemon=True)
    stopper.start()
    code = webapp_module.serve(
        host="127.0.0.1",
        port=0,
        tokens_file=str(tmp_path / "tokens.json"),
        state_dir=str(tmp_path / ".okline"),
        open_browser=False,
    )
    stopper.join(10)
    assert problems == [] and code == 0
    assert "stopping (SIGTERM)..." in capsys.readouterr().out
    assert signal.getsignal(signal.SIGTERM) is signal.SIG_DFL
    assert servers[0].state.stop_pauses_sends is True
    assert _sent_to(api) == [_A]
    monkeypatch.setattr(webapp_module, "SCHEDULE_TARGET_GAP_SECONDS", (0.0, 0.0))
    again = _state(tmp_path)
    try:
        _only_the_tests_rounds(again, monkeypatch)
        left = again._find_schedule(job["id"])
        assert not left["running"] and _turns(left) == [("Ann", "sent")]
        _round(again)
        assert _sent_to(api) == [_A, _B, _C]
        done = again._find_schedule(job["id"])
        assert done["history"] == [
            {"at": done["lastRunAt"], "ok": True, "messageIds": ["m1", "m2", "m3"],
             "resumed": True}
        ]
        assert done["sentCount"] == 1 and done["status"] == "completed"
    finally:
        again.close()


def test_a_stop_signal_ends_the_okline_web_process_with_exit_code_0(tmp_path):
    # The real thing, from outside: the process `okline web` gets SIGTERM, says that it
    # stops and ends by itself with code 0 (a killed process ends with -15, or with 137
    # as a container's process 1 once Docker's grace period is over). Windows has no
    # SIGTERM to send to another process: there it gets Ctrl-Break, which is handled the
    # same way.
    windows = sys.platform == "win32"
    env = {
        key: value for key, value in os.environ.items()
        if key not in {"OKLINE_DATABASE_URL", "DATABASE_URL"}
    }
    env["PYTHONUNBUFFERED"] = "1"
    process = subprocess.Popen(
        [sys.executable, "-m", "okline", "web", "--port", "0", "--no-open",
         "--state-dir", str(tmp_path / "state")],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        # (Ctrl-Break goes to a process group: the process gets one of its own.)
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if windows else 0,
    )
    lines = []

    def read():
        for line in process.stdout:
            lines.append(line)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        assert _wait_until(
            lambda: any("LinePassport running at" in line for line in lines)
            or process.poll() is not None,
            seconds=120.0,
        ), lines
        assert process.poll() is None, lines
        if windows:
            try:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                code = process.wait(timeout=30)
            except (OSError, subprocess.TimeoutExpired):
                pytest.skip("Ctrl-Break does not reach the process (no console shared with it)")
        else:
            process.send_signal(signal.SIGTERM)
            code = process.wait(timeout=60)
        reader.join(10)
        assert code == 0, lines
        named = "SIGBREAK" if windows else "SIGTERM"
        assert any(f"stopping ({named})..." in line for line in lines), lines
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)


def test_a_stop_waits_a_moment_for_a_message_on_its_way(tmp_path, monkeypatch):
    # A stop comes while a message is on its way to the first chat: LinePassport's LINE
    # connections are closed only once it has arrived (within a few seconds), and the
    # send then stops before its next chat.
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(webapp_module, "SCHEDULE_TARGET_GAP_SECONDS", (0.0, 0.0))
    state = _state(tmp_path)
    on_its_way, arrive = threading.Event(), threading.Event()
    arrived_before_the_close = []
    stopping_first = []
    results = []
    try:
        _only_the_tests_rounds(state, monkeypatch)
        _shop(state)
        # The scheduler's stop comes before the slower parts of the shutdown, so no
        # send starts on another chat meanwhile.
        close_broadcasts = state.broadcast_runner.close

        def broadcasts_close():
            stopping_first.append(state.scheduler.stop_event.is_set())
            close_broadcasts()

        monkeypatch.setattr(state.broadcast_runner, "close", broadcasts_close)
        api = _SendApi()
        monkeypatch.setattr(state, "get_api", lambda account_id=None, require_auth=True: api)
        job = _job(state, targets=_THREE)
        original = api.send_text

        def send(to, text):
            on_its_way.set()
            arrive.wait(10)
            return original(to, text)

        api.send_text = send
        close_crm = state.crm.close

        def crm_close():  # called once the LINE connections are closed
            arrived_before_the_close.append(len(api.sent))
            close_crm()

        monkeypatch.setattr(state.crm, "close", crm_close)
        run = threading.Thread(
            target=lambda: results.append(state._execute_schedule(job["id"], manual=False)),
            daemon=True,
        )
        run.start()
        assert on_its_way.wait(10)
        threading.Timer(0.3, arrive.set).start()
    finally:
        state.close()
    run.join(10)
    assert arrived_before_the_close == [1] and _sent_to(api) == [_A]
    assert stopping_first == [True]
    # Stopped as Ctrl-C stops it (the system did not ask): ended, with its report.
    assert results[0]["ok"] is True
    assert _saved(state, job["id"])["lastError"] == (
        "Sent to 1/3 chats. Bob: not sent, LinePassport stopped; "
        "Cat: not sent, LinePassport stopped"
    )


def test_a_stop_does_not_wait_long_for_a_message_on_its_way(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(webapp_module, "SCHEDULE_STOP_WAIT_SECONDS", 0.05)
    state = _state(tmp_path)
    on_its_way, arrive = threading.Event(), threading.Event()
    waits = []
    try:
        _only_the_tests_rounds(state, monkeypatch)
        _shop(state)
        wait_for = state.schedule_sends_cond.wait_for

        def timed_wait(condition, timeout=None):
            waits.append(timeout)
            return wait_for(condition, timeout)

        monkeypatch.setattr(state.schedule_sends_cond, "wait_for", timed_wait)
        api = _SendApi()
        monkeypatch.setattr(state, "get_api", lambda account_id=None, require_auth=True: api)
        job = _job(state)
        original = api.send_text

        def send(to, text):
            on_its_way.set()
            arrive.wait(10)
            return original(to, text)

        api.send_text = send
        run = threading.Thread(
            target=lambda: state._execute_schedule(job["id"], manual=False), daemon=True
        )
        run.start()
        assert on_its_way.wait(10)
    finally:
        state.close()
    assert waits == [0.05]  # the time it waited at most, and not the default
    assert api.sent == [] and state.schedule_sends_now == 1
    arrive.set()
    run.join(10)
    assert _sent_to(api) == [_A] and state.schedule_sends_now == 0


# -- a chat's turn that cannot be saved ---------------------------------------------------


def _disk_fills_as_the_first_message_goes_out(state, monkeypatch, api):
    full = {"on": False}
    _store_fails(state, monkeypatch, lambda key: full["on"] and key == "schedules")
    original = api.send_text

    def send(to, text):
        result = original(to, text)
        full["on"] = True
        return result

    api.send_text = send
    return full


def test_a_chat_whose_turn_cannot_be_saved_gets_nothing_until_it_can_be(ctx, monkeypatch):
    # The disk fills as the first message goes out. The second chat's turn cannot be
    # saved, so nothing is sent to it (a restart would not know of it): the send is cut
    # short, and goes on once the schedules can be saved again.
    state, owner, api = ctx
    job = _job(state, targets=_THREE)
    full = _disk_fills_as_the_first_message_goes_out(state, monkeypatch, api)
    result = state._execute_schedule(job["id"], manual=False)
    assert result["ok"] is False and result["cut"] is True
    assert _sent_to(api) == [_A] and state.schedules_unsaved
    held = state._find_schedule(job["id"])
    assert not held["running"] and held["status"] == "waiting" and "runToken" not in held
    assert held["runProgress"]["cut"] == "unsaved" and _turns(held) == [("Ann", "sent")]
    assert held["sentCount"] == 0 and held["history"] == [] and not held.get("lastError")
    assert state.list_schedules("acct", owner)["schedulesUnsaved"] is True
    for _ in range(2):  # no round starts anything while saving fails
        state.run_due_schedules()
        assert state.schedule_inflight == set() and _sent_to(api) == [_A]
    full["on"] = False
    del api.send_text
    _round(state)
    assert _sent_to(api) == [_A, _B, _C] and not state.schedules_unsaved
    done = state._find_schedule(job["id"])
    assert done["history"] == [
        {"at": done["lastRunAt"], "ok": True, "messageIds": ["m1", "m2", "m3"], "resumed": True}
    ]
    assert done["sentCount"] == 1 and "runProgress" not in done
    assert _saved(state, job["id"]) == done


def test_a_restart_while_a_turn_could_not_be_saved_sends_to_no_chat_twice(
    ctx, restart, monkeypatch
):
    # The same, and LinePassport is restarted before the schedules can be saved. What the
    # store holds is the run with its first chat's turn begun (its end could not be
    # saved): the next start goes on with the two others, and names the first as one
    # that may not have got it.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    _disk_fills_as_the_first_message_goes_out(state, monkeypatch, api)
    assert state._execute_schedule(job["id"], manual=False)["cut"] is True
    left = _saved(state, job["id"])
    assert left["running"] is True and _turns(left) == [("Ann", "sending")]
    del api.send_text
    again = restart(api)
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]
    done = again._find_schedule(job["id"])
    assert done["lastError"] == f"Sent to 2/3 chats. Ann: {_UNKNOWN}"
    assert done["sentCount"] == 1 and done["history"][-1]["resumed"] is True


def test_a_send_whose_first_turn_cannot_be_saved_is_not_run(ctx, monkeypatch):
    # Saved as running, and then the disk fills before its first chat's turn: nothing
    # goes out, and the job is left as it was, due again at a round that can save.
    state, owner, api = ctx
    job = _job(state, targets=_THREE)
    status = state._find_schedule(job["id"])["status"]
    full = {"on": False}
    _store_fails(state, monkeypatch, lambda key: full["on"] and key == "schedules")
    making = webapp_module._resolve_job_contents

    def make(*args, **kwargs):
        full["on"] = True
        return making(*args, **kwargs)

    monkeypatch.setattr(webapp_module, "_resolve_job_contents", make)
    with pytest.raises(WebError) as refused:
        state._execute_schedule(job["id"], manual=False)
    assert refused.value.code == "schedules_unsaved" and api.sent == []
    stored = state._find_schedule(job["id"])
    assert not stored.get("running") and stored["status"] == status
    assert "runProgress" not in stored and not stored.get("lastError")
    assert stored["history"] == [] and state.list_schedules("acct", owner)["schedulesUnsaved"]
    monkeypatch.setattr(webapp_module, "_resolve_job_contents", making)
    full["on"] = False
    _round(state)
    assert _sent_to(api) == [_A, _B, _C]
    assert "resumed" not in state._find_schedule(job["id"])["history"][-1]


def test_a_send_whose_end_could_not_be_saved_goes_to_no_chat_again_after_a_restart(
    ctx, restart, monkeypatch
):
    # The disk fills as the last message goes out: the send's end cannot be saved, and
    # the page asks not to restart yet. Restarted all the same, LinePassport sent it to
    # both chats again before. Now both turns are in the store: nothing goes out, and
    # the chat whose turn's end was not saved shows as one that may not have got it.
    state, owner, api = ctx
    job = _job(state, targets=_THREE[:2])
    full = {"on": False}
    _store_fails(state, monkeypatch, lambda key: full["on"] and key == "schedules")
    original = api.send_text

    def send(to, text):
        result = original(to, text)
        full["on"] = to == _B
        return result

    api.send_text = send
    assert state._execute_schedule(job["id"], manual=False)["ok"] is True
    assert state.list_schedules("acct", owner)["unsavedSent"] == [job["id"]]
    left = _saved(state, job["id"])
    assert left["running"] is True and _turns(left) == [("Ann", "sent"), ("Bob", "sending")]
    del api.send_text
    again = restart(api)
    _round(again)
    assert _sent_to(api) == [_A, _B]
    done = again._find_schedule(job["id"])
    assert done["lastError"] == f"Sent to 1/2 chats. Bob: {_UNKNOWN}"
    assert done["sentCount"] == 1 and done["enabled"] is False
    assert done["history"][-1]["messageIds"] == ["m1"]


# -- what goes on, and what does not ------------------------------------------------------


def _rewind(state, schedule_id, seconds):
    """The stored record of the job's run as if it had been written ``seconds`` ago."""
    data = state.store.get("schedules", {"schedules": []})
    written = time.time() - seconds
    for item in data["schedules"]:
        if item["id"] == schedule_id:
            item["runProgress"]["at"] = written
    state.store.set("schedules", data)
    return dt.datetime.fromtimestamp(written).replace(microsecond=0).isoformat()


def test_a_cut_send_whose_next_run_has_come_due_stays_as_far_as_it_got(
    ctx, restart, monkeypatch
):
    # An hourly send is killed after the first of its three chats, and LinePassport is
    # down for two hours. Its next run has come due by then: that run goes out, to all
    # three, and the cut one counts as a run that ended where it was cut.
    state, _owner, api = ctx
    job = _job(state, mode="repeat", intervalMinutes=60, exactTime=True, targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, job)
    cut_at = _rewind(state, job["id"], 7200)
    again = restart(api)
    _round(again)
    assert _sent_to(api) == [_A, _A, _B, _C]
    done = again._find_schedule(job["id"])
    cut, following = done["history"]
    assert cut == {
        "at": cut_at,
        "ok": False,
        "error": "Sent to 1/3 chats. Bob: not sent, LinePassport stopped; "
                 "Cat: not sent, LinePassport stopped",
        "messageIds": ["m1"],
    }
    assert following == {"at": done["lastRunAt"], "ok": True, "messageIds": ["m2", "m3", "m4"]}
    assert done["sentCount"] == 2 and done["lastError"] is None and done["status"] == "waiting"
    logged = _logged(again)
    assert "schedule.run.resume" not in logged and "schedule.run.error" in logged


def test_a_cut_send_on_its_last_run_goes_on_however_late(ctx, restart, monkeypatch):
    # An hourly send on the last of its runs (maxRuns) is cut short, and LinePassport is
    # down for two hours. No run of it comes after this one, so none has overtaken it:
    # like a send that runs once, it goes on with the two other chats, and is done.
    state, _owner, api = ctx
    job = _job(state, mode="repeat", intervalMinutes=60, exactTime=True, maxRuns=1,
               targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, job)
    _rewind(state, job["id"], 7200)
    again = restart(api)
    _round(again)
    done = again._find_schedule(job["id"])
    assert _sent_to(api) == [_A, _B, _C] and done["sentCount"] == 1
    assert done["enabled"] is False and done["status"] == "completed"
    assert done["history"] == [
        {"at": done["lastRunAt"], "ok": True, "messageIds": ["m1", "m2", "m3"], "resumed": True}
    ]


def test_a_once_a_day_send_cut_short_on_its_last_run_goes_on_however_late(
    ctx, restart, monkeypatch
):
    # The same 13 hours late: a whole run that late would wait for its next start, but
    # this send has no run after this one, so the rest of it goes out now.
    state, _owner, api = ctx
    missed = _minute(dt.datetime.now() - dt.timedelta(hours=13))
    job = _daily(state, missed.strftime("%H:%M"), maxRuns=1, targets=_THREE)
    state._find_schedule(job["id"])["nextRunAt"] = missed.timestamp()
    state.save_schedules()
    _killed_after_the_first_chat(state, monkeypatch, job)
    _rewind(state, job["id"], 13 * 3600 - 120)
    again = restart(api)
    _round(again)
    done = again._find_schedule(job["id"])
    assert _sent_to(api) == [_A, _B, _C] and done["sentCount"] == 1
    assert done["enabled"] is False and done["status"] == "completed"
    assert done["history"][-1]["ok"] is True and done["history"][-1]["resumed"] is True


def test_a_once_a_day_send_cut_short_goes_on_while_its_window_is_open(
    ctx, restart, monkeypatch
):
    state, _owner, api = ctx
    job, opened = _open_window(state, targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, job)
    again = restart(api)
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]
    done = again._find_schedule(job["id"])
    assert done["nextRunAt"] == (opened + dt.timedelta(days=1)).timestamp()
    assert done["sentCount"] == 1 and done["history"][-1]["resumed"] is True


def test_a_once_a_day_send_cut_short_is_not_gone_on_with_near_its_next_start(
    ctx, restart, monkeypatch
):
    # Cut short at its start 13 hours ago, and LinePassport starts again now, nearer its
    # next start than the one it was for: like a whole run that late, the rest of it
    # does not go out. The cut run counts as it stands, and the send goes on tomorrow.
    state, _owner, api = ctx
    missed = _minute(dt.datetime.now() - dt.timedelta(hours=13))
    job = _daily(state, missed.strftime("%H:%M"), targets=_THREE)
    state._find_schedule(job["id"])["nextRunAt"] = missed.timestamp()
    state.save_schedules()
    _killed_after_the_first_chat(state, monkeypatch, job)
    cut_at = _rewind(state, job["id"], 13 * 3600 - 120)
    again = restart(api)
    _round(again)
    assert _sent_to(api) == [_A]
    done = again._find_schedule(job["id"])
    assert done["nextRunAt"] == (missed + dt.timedelta(days=1)).timestamp()
    assert done["sentCount"] == 1 and done["lastRunAt"] == cut_at and "runProgress" not in done
    assert done["history"] == [
        {
            "at": cut_at,
            "ok": False,
            "error": "Sent to 1/3 chats. Bob: not sent, LinePassport stopped; "
                     "Cat: not sent, LinePassport stopped",
            "messageIds": ["m1"],
        }
    ]
    assert done["lastError"] == done["history"][0]["error"] and done["enabled"] is True
    assert _logged(again)[-2:] == ["schedule.run.error", "schedule.run.missed"]


def test_a_run_now_that_was_cut_short_goes_on_by_itself(ctx, restart, monkeypatch):
    # A Run now of a schedule that is not due for years is killed after its first chat.
    # The next start goes on with it, whatever the schedule's own time, once its
    # account's sends are not paused; it ends as a Run now: the schedule stays as it is.
    state, owner, api = ctx
    job = _job(state, runAt="2099-01-01T09:00", targets=_THREE)
    next_run = state._find_schedule(job["id"])["nextRunAt"]
    _killed_after_the_first_chat(state, monkeypatch, job, manual=True)
    assert _saved(state, job["id"])["runProgress"]["manual"] is True
    again = restart(api)
    owner = again.web_auth._find_user(owner["id"])
    again.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
    _round(again)
    assert _sent_to(api) == [_A] and _turns(again._find_schedule(job["id"])) == [("Ann", "sent")]
    again.set_schedules_paused({"accountId": "acct", "paused": False}, owner)
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]
    done = again._find_schedule(job["id"])
    assert done["enabled"] is True and done["status"] == "waiting"
    assert done["nextRunAt"] == next_run and done["sentCount"] == 1
    assert done["history"] == [
        {"at": done["lastRunAt"], "ok": True, "messageIds": ["m1", "m2", "m3"], "resumed": True}
    ]
    _round(again)
    assert len(api.sent) == 3


def test_run_now_on_a_send_that_was_cut_short_goes_on_with_it(ctx, restart, monkeypatch):
    # The account's sends are paused, so the scheduler leaves the cut send. The owner
    # presses Run now: it goes on with that run (the two chats that have had no turn),
    # and ends as the run at its time that it began as: the schedule is done.
    state, owner, api = ctx
    job = _job(state, targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, job)
    again = restart(api)
    owner = again.web_auth._find_user(owner["id"])
    again.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
    _round(again)
    assert _sent_to(api) == [_A]
    assert again.run_schedule_now(job["id"], "acct")["queued"] is True
    assert _wait_until(lambda: not again.schedule_inflight)
    assert _sent_to(api) == [_A, _B, _C]
    done = again._find_schedule(job["id"])
    assert done["status"] == "completed" and done["enabled"] is False
    assert done["sentCount"] == 1 and done["history"][-1]["resumed"] is True
    again.set_schedules_paused({"accountId": "acct", "paused": False}, owner)
    _round(again)
    assert len(api.sent) == 3


def test_run_now_on_a_cut_send_with_no_chat_left_says_that_nothing_was_sent(
    ctx, restart, monkeypatch
):
    # A send to one chat was killed as its message went out. Its account's sends are
    # paused, so the scheduler leaves it, and the owner presses Run now. No chat is left
    # to send to: the answer says so instead of reading as a send, nothing is made for
    # it (no AI image), and the run's end is written. Run now again then sends.
    state, owner, api = ctx
    job = _job(state, targetName="Ann")
    api.send_text = lambda to, text: _kill()
    with pytest.raises(_Killed):
        state._execute_schedule(job["id"], manual=False)
    del api.send_text
    again = restart(api)
    owner = again.web_auth._find_user(owner["id"])
    again.set_schedules_paused({"accountId": "acct", "paused": True}, owner)
    making = webapp_module._resolve_job_contents
    made = []

    def make(*args, **kwargs):
        made.append(1)
        return making(*args, **kwargs)

    monkeypatch.setattr(webapp_module, "_resolve_job_contents", make)
    with pytest.raises(WebError) as refused:
        again.run_schedule_now(job["id"], "acct")
    assert refused.value.code == "schedule_cut_ended" and refused.value.status == 409
    assert api.sent == [] and made == []
    ended = again._find_schedule(job["id"])
    assert ended["lastError"] == f"Sent to 0/1 chats. Ann: {_UNKNOWN}"
    assert ended["sentCount"] == 1 and "runProgress" not in ended
    again.run_schedule_now(job["id"], "acct")
    assert api.sent == [("text", _A, "hi Ann")] and made == [1]


def test_run_now_on_a_cut_send_with_no_chat_left_lets_no_round_come_between(
    ctx, restart, monkeypatch
):
    # The same Run now, and a scheduler round comes just as it starts. The run is begun
    # already (under the lock that decided nothing is left), so the round leaves it,
    # and the Run now does not find the send ended by the round and send it afresh.
    state, _owner, api = ctx
    job = _job(state, targetName="Ann")
    api.send_text = lambda to, text: _kill()
    with pytest.raises(_Killed):
        state._execute_schedule(job["id"], manual=False)
    del api.send_text
    again = restart(api)
    execute = again._execute_schedule

    def round_first(*args, **kwargs):
        again.run_due_schedules()
        assert not again.schedule_inflight
        return execute(*args, **kwargs)

    monkeypatch.setattr(again, "_execute_schedule", round_first)
    with pytest.raises(WebError) as refused:
        again.run_schedule_now(job["id"], "acct")
    assert refused.value.code == "schedule_cut_ended" and api.sent == []
    ended = again._find_schedule(job["id"])
    assert ended["sentCount"] == 1 and len(ended["history"]) == 1
    assert ended["lastError"] == f"Sent to 0/1 chats. Ann: {_UNKNOWN}"


def test_run_now_that_is_cut_before_its_chats_turn_does_not_answer_as_a_send(
    ctx, restart, monkeypatch
):
    # A cut send has one chat left (the schedule was edited down to it), so Run now
    # sends while the request waits. The system stops LinePassport before that chat's
    # turn: the send pauses again, and the answer says that nothing was sent.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE[:2])
    _killed_after_the_first_chat(state, monkeypatch, job)
    again = restart(api)
    again.update_schedule(
        job["id"],
        {"accountId": "acct", "name": "promo", "text": "hi {name}", "mode": "once",
         "runAt": "2026-01-01T09:00", "targets": [_THREE[1]]},
    )
    again.stop_pauses_sends = True
    again.scheduler.stop_event.set()
    with pytest.raises(WebError) as refused:
        again.run_schedule_now(job["id"], "acct")
    assert refused.value.code == "server_stopping" and _sent_to(api) == [_A]
    left = _saved(again, job["id"])
    assert left["running"] is True and _turns(left) == [("Ann", "sent")]
    _round(restart(api))
    assert _sent_to(api) == [_A, _B]


def test_a_cut_send_over_the_hourly_limit_counts_only_the_chats_left(
    ctx, restart, monkeypatch
):
    # The account may send three an hour, and the first chat's message used one: the
    # two chats left fit, so the send goes on instead of waiting in the queue.
    state, owner, api = ctx
    state.save_schedule_limits(
        {"accountId": "acct", "maxPerHour": 3, "maxPerDay": 10, "jitterMinutes": 0}, owner
    )
    job = _job(state, targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, job)
    again = restart(api)
    assert again.schedule_usage("acct")["lastHour"] == 1
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]
    assert again._find_schedule(job["id"])["status"] == "completed"


def test_a_cut_run_now_waits_while_its_account_has_to_be_connected_again(
    ctx, restart, monkeypatch
):
    state, _owner, api = ctx
    job = _job(state, runAt="2099-01-01T09:00", targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, job, manual=True)
    again = restart(api)
    again.account_store.mark_session_reauth_required("acct", "revoked")
    _round(again)
    assert _sent_to(api) == [_A] and again.schedule_inflight == set()
    assert _turns(again._find_schedule(job["id"])) == [("Ann", "sent")]
    again.account_store.mark_session_connected("acct")
    _round(again)
    assert _sent_to(api) == [_A, _B, _C]


def test_a_send_that_another_run_took_over_writes_nothing_more(ctx):
    # Clear stuck jobs is pressed while the send's last message is on its way, and
    # another run of the schedule begins before it arrives. The job is that run's now:
    # the first one writes neither the end of its turn nor the end of its run to it.
    state, owner, api = ctx
    job = _job(state, targets=_THREE[:2])
    original = api.send_text
    taken = {}

    def send(to, text):
        result = original(to, text)
        if to == _B:
            assert state.clear_stuck_schedules("acct", owner)["cleared"] == 1
            taken["token"] = state._begin_schedule_run(job["id"], None)[1]["runToken"]
        return result

    api.send_text = send
    state._execute_schedule(job["id"], manual=False)
    stored = state._find_schedule(job["id"])
    assert stored["running"] is True and stored["runToken"] == taken["token"]
    assert _turns(stored) == [("Ann", "sent"), ("Bob", "sending")]
    assert stored["sentCount"] == 0
    assert [entry.get("action") for entry in stored["history"]] == ["clear_stuck"]


def test_a_cut_send_goes_on_as_the_schedule_is_edited_meanwhile(ctx, restart, monkeypatch):
    # Edited after the restart, before it goes on: the new text goes to the chats it
    # names now that have had no turn. Ann, who got the first text, gets nothing again.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, job)
    again = restart(api)
    edited = again.update_schedule(
        job["id"],
        {"accountId": "acct", "name": "promo", "text": "new {name}", "mode": "once",
         "runAt": "2026-01-01T09:00",
         "targets": [_THREE[0], _THREE[2], {"mid": _D, "name": "Dan"}]},
    )["schedule"]
    assert _turns(edited) == [("Ann", "sent")] and not edited["running"]
    _round(again)
    assert api.sent == [("text", _A, "hi Ann"), ("text", _C, "new Cat"), ("text", _D, "new Dan")]
    done = again._find_schedule(job["id"])
    assert done["history"][-1]["ok"] is True and done["lastError"] is None


def test_a_cut_send_that_line_asks_to_sign_in_for_names_the_chats_it_missed(
    ctx, restart, monkeypatch
):
    # LINE asks to sign in again as the send goes on: as for any send cut short by the
    # sign-in, the chats that got it get nothing again, and the rest are reported.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, job)
    again = restart(api)
    original = api.send_text

    def send(to, text):
        again.account_store.mark_session_reauth_required("acct", "revoked")
        raise webapp_module.LineAuthError("session revoked")

    api.send_text = send
    _round(again)
    done = again._find_schedule(job["id"])
    assert done["lastError"] == (
        "Sent to 1/3 chats. Bob: not sent, LINE asked to sign in again; "
        "Cat: not sent, LINE asked to sign in again"
    )
    assert done["history"][-1]["resumed"] is True and "runProgress" not in done
    assert done["sentCount"] == 1 and done["enabled"] is False
    again.account_store.mark_session_connected("acct")
    api.send_text = original
    _round(again)
    assert _sent_to(api) == [_A]


def _five_minutes_later(state, schedule_id):
    """The job's retry has come due (also past any random delay drawn for it)."""
    waiting = state._find_schedule(schedule_id)
    assert waiting["status"] == "retrying" and waiting["retrying"] is True
    assert waiting["nextRunAt"] > time.time() + 200
    waiting["nextRunAt"] = time.time() - 600
    waiting["retryFrom"][1] = waiting["nextRunAt"]


@pytest.mark.parametrize("fails", ["the sends", "what it sends"])
@pytest.mark.parametrize("second_try", ["works", "fails too"])
def test_the_rest_of_a_cut_send_gets_a_second_try_when_its_first_reaches_nobody(
    ctx, restart, monkeypatch, fails, second_try
):
    # The send goes on ten seconds after the start, and nothing gets through yet (the
    # network is not up after a reboot, or what it sends cannot be made). Like a whole
    # run that reaches nobody, it is tried once more five minutes later: by then with
    # the same record, so Ann, who got it before the stop, still gets nothing again.
    # When the second try fails too, the run ends with its report.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, job)
    again = restart(api)
    making = webapp_module._resolve_job_contents
    tries = 1 if second_try == "works" else 2

    def cannot(*args, **kwargs):
        raise RuntimeError("temporary network error")

    if fails == "the sends":
        api.fail_times = 2 * tries  # Bob's and Cat's, at each try
    else:
        monkeypatch.setattr(webapp_module, "_resolve_job_contents", cannot)
    _round(again)
    waiting = again._find_schedule(job["id"])
    assert _sent_to(api) == [_A] and not waiting["running"]
    assert _turns(waiting) == [("Ann", "sent")] and waiting["runProgress"]["cut"] == "failed"
    assert waiting["sentCount"] == 0 and waiting["lastError"] == "temporary network error"
    assert waiting["history"] == [
        {"at": waiting["history"][0]["at"], "ok": False, "error": "temporary network error",
         "retry": True}
    ]
    assert _saved(again, job["id"]) == waiting
    _round(again)  # not due before its five minutes are over
    assert _sent_to(api) == [_A]
    _five_minutes_later(again, job["id"])
    if second_try == "works":
        monkeypatch.setattr(webapp_module, "_resolve_job_contents", making)
    _round(again)
    done = again._find_schedule(job["id"])
    assert "runProgress" not in done and "retrying" not in done and done["sentCount"] == 1
    if second_try == "works":
        assert _sent_to(api) == [_A, _B, _C]
        assert done["history"][-1] == {
            "at": done["lastRunAt"], "ok": True, "messageIds": ["m1", "m2", "m3"],
            "resumed": True,
        }
        assert done["status"] == "completed" and done["lastError"] is None
    else:
        reason = "" if fails == "the sends" else "not sent, "
        assert _sent_to(api) == [_A]
        assert done["lastError"] == (
            f"Sent to 1/3 chats. Bob: {reason}temporary network error; "
            f"Cat: {reason}temporary network error"
        )
        assert done["status"] == "error" and done["enabled"] is False
    _round(again)
    assert len(api.sent) == (3 if second_try == "works" else 1)


def test_the_rest_of_a_cut_run_now_is_not_tried_again(ctx, restart, monkeypatch):
    # A Run now gets no second try, cut short or not: the rest that cannot go out is
    # reported at once.
    state, _owner, api = ctx
    job = _job(state, runAt="2099-01-01T09:00", targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, job, manual=True)
    again = restart(api)
    api.fail_times = 2
    _round(again)
    done = again._find_schedule(job["id"])
    assert _sent_to(api) == [_A] and "retrying" not in done and "runProgress" not in done
    assert done["lastError"] == (
        "Sent to 1/3 chats. Bob: temporary network error; Cat: temporary network error"
    )
    assert done["sentCount"] == 1 and done["enabled"] is True


def test_a_send_that_reaches_nobody_forgets_its_turns(ctx):
    # A whole run that fails is tried again in five minutes, to every chat: its failed
    # turns are not kept as a record of a run cut short.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    api.fail_times = 3
    with pytest.raises(WebError):
        state._execute_schedule(job["id"], manual=False)
    waiting = state._find_schedule(job["id"])
    assert "runProgress" not in waiting and waiting["status"] == "retrying"
    _five_minutes_later(state, job["id"])
    _round(state)
    assert _sent_to(api) == [_A, _B, _C]
    assert "resumed" not in state._find_schedule(job["id"])["history"][-1]


def test_a_send_that_line_asks_to_sign_in_for_at_its_first_chat_goes_to_every_chat_later(ctx):
    # LINE asks to sign in again at the first chat: no chat got it, so nothing is kept
    # of the run. Once the account is connected again, every chat gets it, the first
    # one too.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    original = api.send_text

    def send(to, text):
        state.account_store.mark_session_reauth_required("acct", "revoked")
        raise webapp_module.LineAuthError("session revoked")

    api.send_text = send
    with pytest.raises(WebError):
        state._execute_schedule(job["id"], manual=False)
    held = state._find_schedule(job["id"])
    assert held["status"] == "login required" and "runProgress" not in held
    assert "runProgress" not in _saved(state, job["id"])
    state.account_store.mark_session_connected("acct")
    api.send_text = original
    _round(state)
    assert _sent_to(api) == [_A, _B, _C]


@pytest.mark.parametrize("gone", ["deleted", "account removed"])
def test_a_send_stops_before_its_next_chat_once_its_schedule_is_gone(ctx, monkeypatch, gone):
    # Deleted, or its account removed, while the send pauses after its first chat: the
    # job is not marked as this run any more, so nothing goes to the other chats.
    state, _owner, api = ctx
    job = _job(state, targets=_THREE)
    if gone == "deleted":
        _in_the_pause(state, monkeypatch, lambda: state.delete_schedule(job["id"], "acct"))
    else:
        _in_the_pause(state, monkeypatch, lambda: state._commit_account_removal("acct"))
    result = state._execute_schedule(job["id"], manual=False)
    assert result["ok"] is False and result["skipped"] is True
    assert _sent_to(api) == [_A]
    if gone == "deleted":
        assert state.schedules == []
    else:
        left = state._find_schedule(job["id"])
        assert left["status"] == "account deleted" and "runProgress" not in left
        assert left["sentCount"] == 0 and not left["running"]


def test_a_record_of_a_run_that_an_earlier_version_ran_over_is_dropped_at_the_start(
    ctx, restart, monkeypatch
):
    # A Run now was cut short, and LinePassport was then put back to an earlier version
    # for a while. That version knows no records: it sent the schedule in full, counted
    # the run and left the record where it was. Started again, this version does not go
    # on with the run of that record: the job has counted a run since it began. A
    # record of failed chats alone is dropped as well.
    state, _owner, api = ctx
    earlier = _job(state, name="put back", runAt="2099-01-01T09:00", targets=_THREE)
    failed = _job(state, name="failed only", runAt="2099-01-01T09:00", targets=_THREE)
    _killed_after_the_first_chat(state, monkeypatch, earlier, manual=True)
    data = state.store.get("schedules", {"schedules": []})
    for item in data["schedules"]:
        if item["id"] == earlier["id"]:
            assert item["runProgress"]["sentCount"] == 0 and item["running"] is True
            # What the earlier version left after its own run of it.
            item.update({"running": False, "status": "waiting", "sentCount": 1,
                         "lastRunAt": "2026-11-24T21:40:00"})
        else:
            item["runProgress"] = {
                "manual": False, "startedAt": "2026-11-24T09:00:00", "sentCount": 0,
                "at": time.time() - 60,
                "chats": [{"mid": _A, "name": "Ann", "state": "failed", "error": "blocked"}],
            }
    state.store.set("schedules", data)
    again = restart(api)
    assert "runProgress" not in again._find_schedule(earlier["id"])
    assert "runProgress" not in again._find_schedule(failed["id"])
    assert "runProgress" not in _saved(again, earlier["id"])
    _round(again)
    assert _sent_to(api) == [_A]


# -- the schedules page -------------------------------------------------------------------


def test_the_schedules_page_shows_a_send_cut_short_as_one_that_goes_on():
    html = webapp_module.INDEX_HTML
    assert (
        'if (sendCutShort(job) && (job.enabled || job.runProgress.manual)) '
        'return ["part_sent", "warn"];'
    ) in html
    assert 'if (statusKey === "part_sent") statusBadge.title = t("jobs.part_sent_title");' in html
    # Its history line says that it went on, and opening it for an edit says what an
    # edit changes.
    assert '${entry.resumed ? " · " + t("jobs.history_resumed") : ""}' in html
    assert 'if (job && sendCutShort(job)) toast(t("jobs.part_sent_edit"));' in html
    for key in ("jobs.status_part_sent", "jobs.part_sent_title", "jobs.history_resumed",
                "jobs.part_sent_edit", "botlog.action.schedule.run.resume",
                "errors.schedule_cut_ended"):
        assert f'"{key}": {{th: "' in html, key
    assert "sends it again" not in html  # (what a restart did to an unsaved send before)


@pytest.mark.skipif(shutil.which("node") is None, reason="Node.js is not installed")
def test_the_schedules_page_tells_a_send_cut_short_from_a_failed_one(tmp_path):
    html = webapp_module.INDEX_HTML
    source = html[html.index("    function sendCutShort(job) {"):
                  html.index("    function jobPreview(job) {")]
    sent = {"chats": [{"mid": _A, "name": "Ann", "state": "sent"}]}
    run_now = {"manual": True, "chats": [{"mid": _A, "name": "Ann", "state": "sending"}]}
    failed = {"chats": [{"mid": _A, "name": "Ann", "state": "failed", "error": "blocked"}]}
    jobs = [
        {"enabled": True, "status": "waiting", "runProgress": sent},
        {"enabled": True, "status": "waiting", "runProgress": sent, "lastError": "earlier"},
        {"enabled": True, "status": "running", "running": True, "runProgress": sent},
        {"enabled": True, "status": "login required", "runProgress": sent},
        {"enabled": True, "status": "queued", "runProgress": sent},
        {"enabled": True, "status": "retrying", "runProgress": sent},
        # Switched off: a send at its time waits to be switched on; a Run now goes on.
        {"enabled": False, "status": "paused", "runProgress": sent},
        {"enabled": False, "status": "paused", "runProgress": run_now},
        {"enabled": True, "status": "error", "lastError": "x", "runProgress": failed},
        {"enabled": True, "status": "waiting"},
    ]
    path = tmp_path / "status.js"
    path.write_text(
        source
        + f"\nconst jobs = {json.dumps(jobs)};\n"
        + "console.log(JSON.stringify(jobs.map((job) => jobStatus(job)[0])));\n",
        encoding="utf-8",
    )
    result = subprocess.run(["node", str(path)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == [
        "part_sent", "part_sent", "running", "login_required", "queued", "retrying",
        "paused", "part_sent", "error", "waiting",
    ]
