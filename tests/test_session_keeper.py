"""SessionKeeper: renew LINE tokens before the 7-day expiry, revive code-119 drops."""

from __future__ import annotations

import base64
import json
import threading
import time

import pytest

from okline import webapp as webapp_module
from okline.webapp import WebConfig, WebState, _jwt_expiry


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


def _jwt(exp: float) -> str:
    def part(obj):
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part({'exp': int(exp), 'iat': int(exp) - 604800})}.sig"


class _Tokens:
    def __init__(self, access):
        self.access_token = access
        self.refresh_token = "RT"


class _Auth:
    def __init__(self, api, fail=False):
        self.api = api
        self.fail = fail
        self.calls = 0

    def refresh_access_token(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("refresh rejected")
        self.api.transport.tokens.access_token = _jwt(time.time() + 7 * 86400)
        return self.api.transport.tokens.access_token


class _Api:
    def __init__(self, access, fail=False):
        self.transport = type("T", (), {})()
        self.transport.tokens = _Tokens(access)
        self.auth = _Auth(self, fail)
        self._token_refresh_lock = threading.RLock()
        self.saved = 0
        self._session_save_hook = self._save

    def _save(self):
        self.saved += 1


@pytest.fixture
def ctx(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    state = _state(tmp_path)
    token = tmp_path / "shop.tokens"
    token.write_text("{}")
    state.account_store.data["accounts"] = [
        {"id": "shop", "label": "Shop", "mid": "U-me", "ownerId": "o", "tokenFile": str(token)}
    ]
    state.account_store.save()
    holder: dict = {}
    monkeypatch.setattr(state, "get_api", lambda account_id=None, require_auth=True: holder["api"])
    try:
        yield state, holder
    finally:
        state.close()


def _actions(state):
    return [log.get("action") for log in state.store.get("bot_logs", {"logs": []})["logs"]]


def test_jwt_expiry_reads_the_exp_claim():
    assert _jwt_expiry(_jwt(1_800_000_000)) == 1_800_000_000
    assert _jwt_expiry("not-a-jwt") is None
    assert _jwt_expiry(None) is None


def test_token_close_to_expiry_is_renewed(ctx):
    state, holder = ctx
    holder["api"] = _Api(_jwt(time.time() + 86400))
    assert state.keep_sessions_fresh() == ["shop"]
    assert holder["api"].saved == 1
    assert "line.session.refreshed" in _actions(state)


def test_token_with_days_left_is_left_alone(ctx):
    state, holder = ctx
    holder["api"] = _Api(_jwt(time.time() + 5 * 86400))
    assert state.keep_sessions_fresh() == []
    assert holder["api"].auth.calls == 0


def test_expired_session_with_code_119_is_revived_without_a_qr(ctx):
    state, holder = ctx
    state.account_store.mark_session_reauth_required(
        "shop", "LINE session expired or was revoked (code 119). Connect the account again."
    )
    holder["api"] = _Api(_jwt(time.time() - 86400))
    assert state.keep_sessions_fresh() == ["shop"]
    assert not state.account_store.requires_reauth("shop")


def test_revoked_session_is_not_retried(ctx):
    state, holder = ctx
    state.account_store.mark_session_reauth_required(
        "shop", "LINE session expired or was revoked (code 8). Connect the account again."
    )
    holder["api"] = _Api(_jwt(time.time() - 86400))
    assert state.keep_sessions_fresh() == []
    assert holder["api"].auth.calls == 0


def test_failed_refresh_is_logged_and_retried_at_most_hourly(ctx):
    state, holder = ctx
    holder["api"] = _Api(_jwt(time.time() + 3600), fail=True)
    now = time.time()
    assert state.keep_sessions_fresh(now) == []
    assert state.keep_sessions_fresh(now + 60) == []
    assert holder["api"].auth.calls == 1
    assert "line.session.refresh_failed" in _actions(state)
    state.keep_sessions_fresh(now + webapp_module.SESSION_REFRESH_RETRY_SECONDS + 1)
    assert holder["api"].auth.calls == 2
