"""Offline tests for :mod:`okline.transport`.

These exercise the low-level request engine that every Thrift service shares:

* the exact standard header set (``base_headers``), including the X-LAL /
  Accept-Language pairing and the X-Line-Application descriptor,
* URL building in :meth:`Transport.post_json`,
* the LINE ``{"message":"OK","data":...}`` envelope unwrap (and the bare
  ``{"data":...}`` variant),
* error mapping: non-OK envelope -> ``LineApiError``, HTTP 401 ->
  ``LineAuthError``, ``REQUEST_MUST_UPGRADE`` -> ``LineMustUpgradeError``,
* the ``_safe_json`` helper,
* recording integration (``api.history`` / ``api.last`` grow per call),
* ``LineLoginRequired`` when ``require_auth`` is set but no token is held.

Everything runs against the in-memory :class:`FakeSession` from
``tests/conftest.py`` — no real network and no Node.js bridge.
"""

from __future__ import annotations

import json
import threading
import time

import pytest
import requests
from conftest import FakeResp, FakeSession, build_api, enveloped, route

from okline import transport as transport_module
from okline.exceptions import (
    LineApiError,
    LineAuthError,
    LineLoginRequired,
    LineMustUpgradeError,
)
from okline.transport import (
    _LAL_MAP,
    DEFAULT_APPLICATION_HEADER,
    DEFAULT_USER_AGENT,
    LineConfig,
    Tokens,
    Transport,
    _safe_url_for_error,
)

# A real Thrift endpoint key used throughout for ``call``-based tests.
PROFILE = "Talk.TalkService.getProfile"
PROFILE_PATH = "/api/talk/thrift/Talk/TalkService/getProfile"


def test_safe_url_for_error_removes_credentials_query_and_fragment():
    safe = _safe_url_for_error(
        "https://user:password@example.test:8443/path?token=secret#fragment"
    )
    assert safe == "https://example.test:8443/path"


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------
def make_transport(responder=None, *, access_token="TKN", **cfg_kw) -> Transport:
    """A bare :class:`Transport` wired to a fake session (no OkLine wrapper)."""
    responder = responder or (lambda m, u, kw: enveloped({}))
    cfg = LineConfig(**cfg_kw)
    return Transport(cfg, Tokens(access_token=access_token), session=FakeSession(responder))


# ---------------------------------------------------------------------------
# base_headers
# ---------------------------------------------------------------------------
class TestBaseHeaders:
    """The standard header set must match the real extension byte-for-byte."""

    def test_static_headers_have_exact_values(self):
        t = make_transport()
        h = t.base_headers()
        assert h["content-type"] == "application/json"
        assert h["accept"] == "application/json, text/plain, */*"
        assert h["X-Line-Application"] == DEFAULT_APPLICATION_HEADER
        assert h["X-Line-Application"] == "CHROMEOS\t3.7.2\tChrome_OS\t"
        assert h["X-Line-Chrome-Version"] == "3.7.2"
        assert h["User-Agent"] == DEFAULT_USER_AGENT

    def test_locale_drives_accept_language_and_xlal(self):
        """X-LAL is the underscore form of Accept-Language (the bundle's Up map)."""
        t = make_transport(locale="ja-JP")
        h = t.base_headers()
        assert h["Accept-Language"] == "ja-JP"
        assert h["X-LAL"] == "ja_JP"
        # default locale en-US -> en_US
        assert make_transport().base_headers()["X-LAL"] == "en_US"

    def test_unknown_locale_falls_back_to_en_us(self):
        t = make_transport(locale="xx-YY")
        h = t.base_headers()
        assert h["Accept-Language"] == "xx-YY"  # echoed verbatim
        assert h["X-LAL"] == "en_US"  # but X-LAL falls back

    def test_lal_map_is_consistent_for_every_known_locale(self):
        for locale, lal in _LAL_MAP.items():
            assert make_transport(locale=locale).base_headers()["X-LAL"] == lal

    def test_access_token_header_present_when_held(self):
        h = make_transport(access_token="SECRET").base_headers()
        assert h["X-Line-Access"] == "SECRET"

    def test_access_token_omitted_when_with_access_false(self):
        h = make_transport(access_token="SECRET").base_headers(with_access=False)
        assert "X-Line-Access" not in h

    def test_access_token_omitted_when_no_token(self):
        h = make_transport(access_token=None).base_headers()
        assert "X-Line-Access" not in h

    def test_channel_token_header_present_when_held(self):
        t = make_transport()
        t.tokens.channel_access_token = "CHAN"
        assert t.base_headers()["X-Line-ChannelToken"] == "CHAN"
        # absent by default
        assert "X-Line-ChannelToken" not in make_transport().base_headers()


# ---------------------------------------------------------------------------
# post_json / call: URL building and request shape
# ---------------------------------------------------------------------------
class TestUrlBuilding:
    """``post_json`` builds ``<gateway_base><path>`` and POSTs the JSON body."""

    def test_call_targets_gateway_plus_thrift_path(self):
        t = make_transport()
        t.call(PROFILE, [0])
        last = t.session.last
        assert last["method"] == "POST"
        assert last["url"] == "https://line-chrome-gw.line-apps.com" + PROFILE_PATH

    def test_post_json_uses_configured_gateway_base(self):
        t = make_transport(gateway_base="https://example.test")
        t.post_json("/api/foo", [1, 2])
        assert t.session.last["url"] == "https://example.test/api/foo"

    def test_post_json_honours_explicit_base_override(self):
        t = make_transport()
        t.post_json("/api/foo", [], base="https://other.test")
        assert t.session.last["url"] == "https://other.test/api/foo"

    def test_body_is_compact_positional_json_array(self):
        t = make_transport()
        t.call(PROFILE, [0, {"mid": "u123"}])
        sent = t.session.last["data"]
        if isinstance(sent, (bytes, bytearray)):
            sent = sent.decode("utf-8")
        # compact separators, no spaces
        assert sent == '[0,{"mid":"u123"}]'
        assert json.loads(sent) == [0, {"mid": "u123"}]

    def test_body_keeps_non_ascii_unescaped(self):
        """``ensure_ascii=False`` keeps multibyte text readable on the wire."""
        t = make_transport()
        t.post_json("/api/foo", ["こんにちは"])
        sent = t.session.last["data"]
        if isinstance(sent, (bytes, bytearray)):
            sent = sent.decode("utf-8")
        assert "こんにちは" in sent


# ---------------------------------------------------------------------------
# Envelope unwrapping
# ---------------------------------------------------------------------------
class TestEnvelopeUnwrap:
    """The gateway wraps results as ``{"message":"OK","data":<result>}``."""

    def test_ok_envelope_returns_inner_data(self):
        t = make_transport(route({PROFILE_PATH: {"mid": "u1", "displayName": "Z"}}))
        result = t.call(PROFILE, [0])
        assert result == {"mid": "u1", "displayName": "Z"}

    def test_ok_message_is_case_insensitive(self):
        t = make_transport(lambda m, u, kw: enveloped({"x": 1}, message="ok"))
        assert t.call(PROFILE, [0]) == {"x": 1}

    def test_ok_envelope_without_data_returns_whole_payload(self):
        """An OK envelope that lacks a ``data`` key yields the dict itself."""
        t = make_transport(lambda m, u, kw: FakeResp(200, {"message": "OK"}))
        assert t.call(PROFILE, [0]) == {"message": "OK"}

    def test_bare_data_wrapper_is_unwrapped(self):
        """A ``{"data": ...}`` body with no ``message`` is still unwrapped."""
        t = make_transport(lambda m, u, kw: FakeResp(200, {"data": [1, 2, 3]}))
        assert t.call(PROFILE, [0]) == [1, 2, 3]

    def test_plain_json_without_envelope_passes_through(self):
        t = make_transport(lambda m, u, kw: FakeResp(200, [9, 8, 7]))
        assert t.call(PROFILE, [0]) == [9, 8, 7]

    def test_data_can_be_falsy_and_is_preserved(self):
        t = make_transport(lambda m, u, kw: enveloped(0))
        assert t.call(PROFILE, [0]) == 0
        t2 = make_transport(lambda m, u, kw: enveloped([]))
        assert t2.call(PROFILE, [0]) == []


# ---------------------------------------------------------------------------
# Error mapping
# ---------------------------------------------------------------------------
class TestErrorMapping:
    """HTTP status + body + headers map onto the exception hierarchy."""

    def test_non_ok_envelope_raises_api_error_despite_200(self):
        """A non-OK message at HTTP 200 is still an application error."""
        body = {
            "message": "FAILED",
            "data": None,
            "error": {"code": 20, "message": "bad request"},
        }
        t = make_transport(lambda m, u, kw: FakeResp(200, body))
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        err = ei.value
        assert err.code == 20
        assert err.reason == "bad request"
        assert err.status == 200
        assert err.path == PROFILE_PATH

    def test_non_ok_envelope_uses_message_when_no_error_block(self):
        t = make_transport(lambda m, u, kw: FakeResp(200, {"message": "NOPE"}))
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        # message bubbles into the reason when no structured error is present
        assert "NOPE" in str(ei.value) or ei.value.reason == "NOPE"

    def test_http_401_raises_auth_error(self):
        body = {"error": {"code": 8, "message": "token expired"}}
        t = make_transport(lambda m, u, kw: FakeResp(401, body))
        with pytest.raises(LineAuthError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.status == 401
        assert ei.value.code == 8

    def test_http_403_raises_auth_error(self):
        t = make_transport(lambda m, u, kw: FakeResp(403, {"error": {"message": "forbidden"}}))
        with pytest.raises(LineAuthError):
            t.call(PROFILE, [0])

    def test_auth_code_zero_raises_auth_error_even_on_generic_status(self):
        """Talk auth codes {0,1,8} classify as auth errors regardless of status."""
        body = {"error": {"code": 0, "message": "ILLEGAL_ARGUMENT"}}
        t = make_transport(lambda m, u, kw: FakeResp(400, body))
        with pytest.raises(LineAuthError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.code == 0

    def test_generic_http_error_raises_plain_api_error(self):
        """A non-auth, non-upgrade error is a plain LineApiError (not a subclass)."""
        body = {"error": {"code": 42, "message": "boom"}}
        t = make_transport(
            lambda m, u, kw: FakeResp(500, body, headers={"content-type": "application/json"})
        )
        # 500 retries (max_retries default 2) then surfaces the final 500 body.
        with pytest.raises(LineApiError) as ei:
            t.call(PROFILE, [0])
        assert type(ei.value) is LineApiError
        assert ei.value.code == 42
        assert ei.value.status == 500

    def test_request_exception_retries_can_recover(self):
        attempts = 0

        def responder(method, url, kw):
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                raise requests.exceptions.SSLError("EOF occurred in violation of protocol")
            return FakeResp(200, {"ok": True})

        t = make_transport(responder, max_retries=2, retry_backoff=0)
        resp = t.get("/api/ping", require_auth=False)

        assert resp.status_code == 200
        assert attempts == 3

    def test_retry_backoff_is_applied_between_attempts(self, monkeypatch):
        sleeps = []

        monkeypatch.setattr(transport_module, "_sleep", sleeps.append)
        t = make_transport(
            lambda m, u, kw: FakeResp(500, {"error": {"message": "boom"}}),
            max_retries=2,
            retry_backoff=0.5,
            retry_backoff_max=0.75,
        )

        with pytest.raises(LineApiError):
            t.post_json("/api/foo", [], require_auth=False)

        assert sleeps == [0.5, 0.75]

    def test_error_code_from_response_header(self):
        """When the body has no code, ``x-line-resp-code`` supplies it."""
        resp = FakeResp(
            400,
            {"message": "fail"},
            headers={"content-type": "application/json", "x-line-resp-code": "8"},
        )
        t = make_transport(lambda m, u, kw: resp)
        with pytest.raises(LineAuthError) as ei:  # code 8 -> auth
            t.call(PROFILE, [0])
        assert ei.value.code == 8


class TestCredentialRecovery:
    """Expired credentials refresh once and terminal failures request login."""

    def test_http_200_code_119_refreshes_and_retries_with_new_token(self):
        profile_tokens = []
        refresh_calls = 0

        def responder(method, url, kw):
            nonlocal refresh_calls
            if url.endswith("/api/auth/tokenRefresh"):
                refresh_calls += 1
                return enveloped(
                    {
                        "tokenV3IssueResult": {
                            "accessToken": "NEW-TOKEN",
                            "refreshToken": "NEW-REFRESH",
                        }
                    }
                )
            profile_tokens.append(kw["headers"].get("X-Line-Access"))
            if profile_tokens[-1] == "OLD-TOKEN":
                return FakeResp(
                    200,
                    {
                        "message": "FAILED",
                        "error": {"code": "119", "message": "TOKEN_EXPIRED"},
                    },
                )
            return enveloped({"mid": "u1"})

        api = build_api(responder, access_token="OLD-TOKEN")
        try:
            api.tokens.refresh_token = "OLD-REFRESH"
            assert api.call(PROFILE, 0) == {"mid": "u1"}
            assert profile_tokens == ["OLD-TOKEN", "NEW-TOKEN"]
            assert refresh_calls == 1
            assert api.tokens.access_token == "NEW-TOKEN"
            assert api.tokens.refresh_token == "NEW-REFRESH"
        finally:
            api.close()

    def test_terminal_http_200_auth_failure_notifies_once_after_refresh(self):
        refresh_calls = 0
        profile_calls = 0

        def responder(method, url, kw):
            nonlocal refresh_calls, profile_calls
            if url.endswith("/api/auth/tokenRefresh"):
                refresh_calls += 1
                return enveloped(
                    {"tokenV3IssueResult": {"accessToken": "NEW-TOKEN"}}
                )
            profile_calls += 1
            return FakeResp(
                200,
                {
                    "message": "FAILED",
                    "error": {"code": 8, "message": "CLIENT_LOGGED_OUT"},
                },
            )

        api = build_api(responder, access_token="OLD-TOKEN")
        failures = []
        api.tokens.refresh_token = "REFRESH"
        api.transport._auth_failure_hook = failures.append
        try:
            with pytest.raises(LineAuthError) as exc:
                api.call(PROFILE, 0)
            assert exc.value.code == 8
            assert refresh_calls == 1
            assert profile_calls == 2
            assert len(failures) == 1
            assert failures[0].code == 8
        finally:
            api.close()

    def test_get_401_refreshes_closes_old_response_and_retries(self):
        responses = []

        def responder(method, url, kw):
            token = kw["headers"].get("X-Line-Access")
            response = FakeResp(401 if token == "OLD" else 200, {"token": token})
            responses.append(response)
            return response

        t = make_transport(responder, access_token="OLD")

        def refresh():
            t.tokens.access_token = "NEW"
            return True

        t._refresh_hook = refresh
        response = t.get("/api/ping")

        assert response.status_code == 200
        assert responses[0].closed is True
        assert responses[1].closed is False
        assert [call["headers"]["X-Line-Access"] for call in t.session.calls] == [
            "OLD",
            "NEW",
        ]

    def test_concurrent_failures_share_one_refresh(self):
        t = make_transport(access_token="OLD")
        refresh_started = threading.Event()
        release_refresh = threading.Event()
        refresh_calls = 0

        def refresh():
            nonlocal refresh_calls
            refresh_calls += 1
            refresh_started.set()
            assert release_refresh.wait(timeout=2)
            t.tokens.access_token = "NEW"
            return True

        t._refresh_hook = refresh
        results = []
        first = threading.Thread(
            target=lambda: results.append(t._refresh_credentials("OLD"))
        )
        second = threading.Thread(
            target=lambda: results.append(t._refresh_credentials("OLD"))
        )
        first.start()
        assert refresh_started.wait(timeout=2)
        second.start()
        time.sleep(0.01)
        release_refresh.set()
        first.join(timeout=2)
        second.join(timeout=2)

        assert not first.is_alive()
        assert not second.is_alive()
        assert results == [True, True]
        assert refresh_calls == 1


class TestBridgeRecovery:
    def test_hmac_failure_restarts_bridge_once_and_retries_signature(self):
        from okline.hmac_signer import HmacSignerError

        class FlakySigner:
            def __init__(self):
                self.sign_calls = 0
                self.restart_calls = 0

            def sign(self, access_token, path, body):
                self.sign_calls += 1
                if self.sign_calls == 1:
                    raise HmacSignerError("hmac failed: Aborted()")
                return "RECOVERED-SIGNATURE"

            def restart(self):
                self.restart_calls += 1

        signer = FlakySigner()
        t = Transport(
            LineConfig(enable_hmac=True),
            Tokens(access_token="TOKEN"),
            session=FakeSession(lambda m, u, kw: enveloped({"ok": True})),
            signer=signer,
            bridge=object(),
        )

        assert t.post_json("/api/test", []) == {"ok": True}
        assert signer.sign_calls == 2
        assert signer.restart_calls == 1
        assert t.session.last["headers"]["X-Hmac"] == "RECOVERED-SIGNATURE"

    def test_transport_closes_independent_signer_and_e2ee_bridge(self):
        class Closable:
            def __init__(self):
                self.close_calls = 0

            def close(self):
                self.close_calls += 1

        signer = Closable()
        bridge = Closable()
        t = Transport(
            LineConfig(enable_hmac=False),
            Tokens(access_token="TOKEN"),
            session=FakeSession(lambda m, u, kw: enveloped({})),
            signer=signer,
            bridge=bridge,
        )

        t.close()

        assert signer.close_calls == 1
        assert bridge.close_calls == 1


class TestMustUpgrade:
    """``REQUEST_MUST_UPGRADE`` / code 86 must classify as a must-upgrade error."""

    def test_upgrade_reason_string_classifies_as_must_upgrade(self):
        body = {"error": {"code": 99, "message": "REQUEST_MUST_UPGRADE"}}
        t = make_transport(lambda m, u, kw: FakeResp(426, body))
        with pytest.raises(LineMustUpgradeError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.reason == "REQUEST_MUST_UPGRADE"

    def test_upgrade_substring_anywhere_in_reason(self):
        body = {"error": {"message": "client must upgrade now"}}
        t = make_transport(lambda m, u, kw: FakeResp(400, body))
        with pytest.raises(LineMustUpgradeError):
            t.call(PROFILE, [0])

    def test_error_code_86_classifies_as_must_upgrade(self):
        body = {"error": {"code": 86, "message": "outdated"}}
        t = make_transport(lambda m, u, kw: FakeResp(400, body))
        with pytest.raises(LineMustUpgradeError) as ei:
            t.call(PROFILE, [0])
        assert ei.value.code == 86

    def test_must_upgrade_takes_precedence_over_auth_status(self):
        """An UPGRADE reason wins even when the status would imply auth."""
        body = {"error": {"code": 86, "message": "MUST_UPGRADE"}}
        t = make_transport(lambda m, u, kw: FakeResp(401, body))
        with pytest.raises(LineMustUpgradeError):
            t.call(PROFILE, [0])


# ---------------------------------------------------------------------------
# _safe_json
# ---------------------------------------------------------------------------
class TestSafeJson:
    """``_safe_json`` returns parsed JSON or the raw text on failure."""

    def test_parses_valid_json_object(self):
        assert Transport._safe_json('{"a": 1}') == {"a": 1}

    def test_parses_valid_json_array(self):
        assert Transport._safe_json("[1, 2, 3]") == [1, 2, 3]

    def test_parses_json_scalars(self):
        assert Transport._safe_json("true") is True
        assert Transport._safe_json("42") == 42

    def test_returns_raw_text_on_invalid_json(self):
        assert Transport._safe_json("not json at all") == "not json at all"

    def test_returns_empty_string_unchanged(self):
        assert Transport._safe_json("") == ""


# ---------------------------------------------------------------------------
# Recording integration
# ---------------------------------------------------------------------------
class TestRecording:
    """A recording OkLine grows ``history`` / updates ``last`` per call."""

    def test_history_grows_per_successful_call(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}))
        assert api.history == []
        api.call(PROFILE, 0)
        assert len(api.history) == 1
        api.call(PROFILE, 0)
        assert len(api.history) == 2

    def test_last_reflects_most_recent_exchange(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}))
        api.call(PROFILE, 0)
        ex = api.last
        assert ex is not None
        assert ex.endpoint == PROFILE
        assert ex.method == "POST"
        assert ex.path == PROFILE_PATH
        assert ex.status == 200
        assert ex.ok is True
        assert ex.response_body == {"mid": "u1"}

    def test_request_body_is_recorded_as_positional_args(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}))
        api.call(PROFILE, 0, {"k": "v"})
        assert api.last.request_body == [0, {"k": "v"}]

    def test_failed_call_is_recorded_with_error(self, make_api):
        body = {"error": {"code": 8, "message": "expired"}}
        api = make_api(lambda m, u, kw: FakeResp(401, body))
        with pytest.raises(LineAuthError):
            api.call(PROFILE, 0)
        # the exchange is still recorded, flagged not-ok with the error string
        assert len(api.history) == 1
        ex = api.last
        assert ex.ok is False
        assert ex.error is not None
        assert ex.status == 401

    def test_no_recorder_means_empty_history(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}), record=False)
        api.call(PROFILE, 0)
        assert api.history == []
        assert api.last is None

    def test_seq_numbers_increase(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}))
        api.call(PROFILE, 0)
        api.call(PROFILE, 0)
        seqs = [ex.seq for ex in api.history]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)  # all distinct

    def test_secrets_redacted_in_recorded_request_headers(self, make_api):
        """The access token must be masked in the recorded transcript by default."""
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}), access_token="TOPSECRET")
        api.call(PROFILE, 0)
        # raw header on the wire still carries the real token...
        assert api.transport.session.last["headers"]["X-Line-Access"] == "TOPSECRET"
        # ...but the dumped transcript redacts it
        assert "TOPSECRET" not in api.dump()
        assert "<redacted>" in api.dump()


# ---------------------------------------------------------------------------
# require_auth / login required
# ---------------------------------------------------------------------------
class TestLoginRequired:
    """``require_auth`` without a token short-circuits before any HTTP call."""

    def test_login_required_when_no_token(self):
        t = make_transport(access_token=None)
        with pytest.raises(LineLoginRequired) as ei:
            t.post_json("/api/foo", [], require_auth=True)
        assert ei.value.path == "/api/foo"
        # nothing was sent
        assert t.session.last is None

    def test_login_required_is_an_auth_error_subclass(self):
        assert issubclass(LineLoginRequired, LineAuthError)

    def test_no_token_allowed_when_require_auth_false(self):
        """Unauthenticated endpoints (require_auth=False) still go out."""
        t = make_transport(
            access_token=None,
        )
        t.post_json("/api/foo", [1], require_auth=False)
        assert t.session.last is not None
        # no access header attached when unauthenticated
        assert "X-Line-Access" not in t.session.last["headers"]

    def test_call_via_okline_raises_login_required(self, make_api):
        api = make_api(route({PROFILE_PATH: {"mid": "u1"}}), access_token=None)
        with pytest.raises(LineLoginRequired):
            api.call(PROFILE, 0)


# ---------------------------------------------------------------------------
# build_api smoke test (ensures the conftest wiring is what these tests assume)
# ---------------------------------------------------------------------------
def test_build_api_returns_recording_client_by_default():
    api = build_api(route({PROFILE_PATH: {"mid": "u1"}}))
    try:
        assert api.recorder is not None
        assert api.call(PROFILE, 0) == {"mid": "u1"}
        assert len(api.history) == 1
    finally:
        api.close()
