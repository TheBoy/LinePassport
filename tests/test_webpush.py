"""Web Push (B7): payload encryption, VAPID and LinePassport's delivery policy."""

from __future__ import annotations

import json
import struct
import time

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from okline import webapp as webapp_module
from okline import webpush
from okline.webapp import WebAuth, WebConfig, WebError, WebState


def _hkdf(salt, ikm, info, length):
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def _decrypt(body: bytes, user_key: ec.EllipticCurvePrivateKey, auth: bytes) -> bytes:
    """What the browser does with an aes128gcm push body (RFC 8291)."""
    salt = body[:16]
    record_size, id_len = struct.unpack("!IB", body[16:21])
    assert record_size == webpush.RECORD_SIZE
    server_public = body[21 : 21 + id_len]
    ciphertext = body[21 + id_len :]
    user_public = webpush._public_bytes(user_key.public_key())
    shared = user_key.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), server_public)
    )
    ikm = _hkdf(auth, shared, b"WebPush: info\x00" + user_public + server_public, 32)
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    padded = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert padded.endswith(b"\x02")
    return padded[:-1]


def test_payload_round_trips_through_browser_decryption():
    user_key = ec.generate_private_key(ec.SECP256R1())
    auth = b"0123456789abcdef"
    body = webpush.encrypt_payload(
        b'{"title":"hi"}', webpush._public_bytes(user_key.public_key()), auth
    )
    assert _decrypt(body, user_key, auth) == b'{"title":"hi"}'
    with pytest.raises(webpush.WebPushError):
        webpush.encrypt_payload(
            b"x" * 5000, webpush._public_bytes(user_key.public_key()), auth
        )


def test_vapid_keys_persist_and_sign_valid_jwt(tmp_path):
    keys = webpush.VapidKeys.load_or_create(tmp_path / "vapid.pem")
    again = webpush.VapidKeys.load_or_create(tmp_path / "vapid.pem")
    assert keys.public_key_b64 == again.public_key_b64
    header = keys.authorization("https://fcm.googleapis.com/fcm/send/abc", "mailto:a@b.c")
    token = header.split("t=", 1)[1].split(",", 1)[0]
    head, claims, signature = token.split(".")
    payload = json.loads(webpush.b64url_decode(claims))
    assert payload["aud"] == "https://fcm.googleapis.com"
    assert payload["sub"] == "mailto:a@b.c" and payload["exp"] > time.time()
    raw = webpush.b64url_decode(signature)
    der = encode_dss_signature(
        int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
    )
    keys.private_key.public_key().verify(
        der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256())
    )
    assert header.endswith("k=" + keys.public_key_b64)


@pytest.mark.parametrize(
    ("endpoint", "allowed"),
    [
        ("https://fcm.googleapis.com/fcm/send/x", True),
        ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
        ("https://web.push.apple.com/x", True),
        ("https://db5p.notify.windows.com/w/?token=x", True),
        ("http://fcm.googleapis.com/fcm/send/x", False),
        ("https://evil.example/fcm.googleapis.com", False),
        ("https://fcm.googleapis.com.evil.example/x", False),
        ("https://127.0.0.1/x", False),
    ],
)
def test_push_endpoints_are_restricted_to_browser_push_services(endpoint, allowed):
    assert webpush.push_endpoint_allowed(endpoint) is allowed


# -- delivery policy ----------------------------------------------------------------
_CUSTOMER = "U" + "a" * 32
_CUSTOMER_2 = "U" + "b" * 32


def _make_state(tmp_path) -> WebState:
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


@pytest.fixture
def push_state(tmp_path, monkeypatch):
    monkeypatch.delenv("OKLINE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    state = _make_state(tmp_path)
    token = state.web_auth.setup("owner@example.com", "owner-pass")
    owner = state.web_auth.current_user(WebAuth.cookie_header(token))
    state.account_store.data["accounts"] = [
        {"id": "acct", "label": "Shop", "mid": "U-me", "ownerId": owner["id"], "tokenFile": ""}
    ]
    state.account_store.save()
    state.web_auth.grant_account_access(owner["id"], "acct")
    sent: list[tuple[str, dict]] = []

    def fake_send(subscription, data, vapid, *, subject, **kwargs):
        sent.append((subscription["endpoint"], data))
        return 201

    monkeypatch.setattr(webapp_module, "send_web_push", fake_send)

    class _Immediate:
        def submit(self, fn, *args):
            fn(*args)

        def shutdown(self, **kwargs):
            pass

    state.push_executor = _Immediate()
    try:
        yield state, state.web_auth._find_user(owner["id"]), sent
    finally:
        state.close()


def _subscribe(state, user, endpoint="https://fcm.googleapis.com/fcm/send/abc"):
    user_key = ec.generate_private_key(ec.SECP256R1())
    state.save_push_subscription(
        {
            "endpoint": endpoint,
            "keys": {
                "p256dh": webpush.b64url_encode(webpush._public_bytes(user_key.public_key())),
                "auth": webpush.b64url_encode(b"0123456789abcdef"),
            },
        },
        user,
    )


def _waiting(state, mid, at_ms):
    with state.room_state_lock:
        rooms = state._room_states("acct")
        rooms[mid] = {"status": "waiting", "firstUnansweredAt": at_ms, "cursorAt": at_ms}
        state._save_room_states("acct", rooms)


def test_subscription_validation(push_state):
    state, owner, _ = push_state
    with pytest.raises(WebError):
        state.save_push_subscription(
            {
                "endpoint": "https://example.com/x",
                "keys": {"p256dh": "a" * 87, "auth": "b" * 22},
            },
            owner,
        )
    with pytest.raises(WebError):
        state.save_push_subscription(
            {
                "endpoint": "https://fcm.googleapis.com/x",
                "keys": {"p256dh": "bad", "auth": "b"},
            },
            owner,
        )
    _subscribe(state, owner)
    _subscribe(state, owner)  # same endpoint replaces, never duplicates
    assert len(state._push_subscriptions()) == 1
    state.remove_push_subscription("https://fcm.googleapis.com/fcm/send/abc", owner)
    assert state._push_subscriptions() == []


def test_push_goes_only_to_offline_staff_with_room_cooldown(push_state):
    state, owner, sent = push_state
    _subscribe(state, owner)
    now_ms = int(time.time() * 1000)
    _waiting(state, _CUSTOMER, now_ms)

    # Someone with a LinePassport page open gets in-page alerts, not a push.
    state.presence_current[owner["id"]] = {"state": "away", "at": now_ms}
    state._dispatch_push("acct", [_CUSTOMER])
    assert sent == []

    state.presence_current.clear()
    state._dispatch_push("acct", [_CUSTOMER])
    assert len(sent) == 1
    assert sent[0][1]["mid"] == _CUSTOMER and sent[0][1]["title"] == "ลูกค้าทักมาใหม่"
    # One push per chat per 2 minutes.
    state._dispatch_push("acct", [_CUSTOMER])
    assert len(sent) == 1


def test_many_chats_at_once_become_one_digest(push_state):
    state, owner, sent = push_state
    _subscribe(state, owner)
    mids = ["U" + str(n) * 32 for n in range(1, 6)]
    for mid in mids:
        _waiting(state, mid, int(time.time() * 1000))
    state._dispatch_push("acct", mids)
    assert len(sent) == 1
    assert sent[0][1]["tag"] == "linepassport:digest"
    assert sent[0][1]["title"] == "ลูกค้า 5 คนรอตอบ"


def test_queue_waits_for_ai_and_escalates_past_target(push_state, monkeypatch):
    state, owner, sent = push_state
    _subscribe(state, owner)
    monkeypatch.setattr(state, "_ai_covers_contacts", lambda account_id: True)
    now_ms = int(time.time() * 1000)
    _waiting(state, _CUSTOMER, now_ms)
    state.queue_push_for_rooms("acct", [_CUSTOMER])
    state.process_push_queue()
    assert sent == []  # still inside the AI's grace period

    # The AI answered in time: nothing is sent once the grace period ends.
    state.note_outgoing("acct", _CUSTOMER, "ai")
    for key in state.push_pending:
        state.push_pending[key] = 0
    state.process_push_queue()
    assert sent == []

    # A chat waiting past the reply target gets one louder reminder.
    _waiting(state, _CUSTOMER_2, now_ms - 10 * 60 * 1000)
    state.process_push_queue()
    assert [item[1]["requireInteraction"] for item in sent] == [True]
    state.process_push_queue()
    assert len(sent) == 1


def test_gone_subscription_is_removed(push_state, monkeypatch):
    state, owner, _ = push_state
    _subscribe(state, owner)

    def gone(*args, **kwargs):
        raise webpush.WebPushError("gone", 410)

    monkeypatch.setattr(webapp_module, "send_web_push", gone)
    state._deliver_push(state._push_subscriptions()[0], {"title": "x"})
    assert state._push_subscriptions() == []


def test_new_waiting_room_is_queued_from_inbox_tracking(push_state):
    state, owner, _ = push_state
    _subscribe(state, owner)
    box = {"id": _CUSTOMER, "lastMessages": [{"from": "U-me", "createdTime": "1000"}]}
    state.update_room_state("acct", "U-me", [box])  # first sight: history only
    assert state.push_pending == {}
    box = {
        "id": _CUSTOMER,
        "lastMessages": [
            {"from": "U-me", "createdTime": "1000"},
            {"from": _CUSTOMER, "createdTime": "2000"},
        ],
    }
    state.update_room_state("acct", "U-me", [box])
    assert ("acct", _CUSTOMER) in state.push_pending


def test_service_worker_and_page_handle_push():
    assert 'self.addEventListener("push"' in webapp_module.SERVICE_WORKER_JS
    assert (
        "requireInteraction: Boolean(data.requireInteraction)"
        in webapp_module.SERVICE_WORKER_JS
    )
    html = webapp_module.INDEX_HTML
    assert (
        "registration.pushManager.subscribe({userVisibleOnly: true, applicationServerKey})"
        in html
    )
    assert (
        'await post("/api/push/subscribe", {endpoint: json.endpoint, keys: json.keys});'
        in html
    )
    assert '"/api/push/unsubscribe"' in html
