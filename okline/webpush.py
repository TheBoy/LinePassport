"""Minimal Web Push sender: VAPID (RFC 8292) + aes128gcm payloads (RFC 8291).

Only what LinePassport needs to alert staff who have no tab open: generate and
persist a VAPID key pair, encrypt a small JSON payload for one browser
subscription, and POST it to that browser's push service.
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

#: Push services browsers actually use. Subscriptions pointing anywhere else are
#: refused so the server can't be used to make requests to arbitrary hosts.
PUSH_SERVICE_HOST_SUFFIXES = (
    "fcm.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
    "push.apple.com",
)
RECORD_SIZE = 4096
MAX_PAYLOAD_BYTES = 3000


class WebPushError(RuntimeError):
    """Raised when a push could not be delivered."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def subscription_gone(self) -> bool:
        """The browser unsubscribed or the subscription expired."""
        return self.status_code in {404, 410}


def b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def b64url_decode(value: str) -> bytes:
    text = str(value or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _public_bytes(key: ec.EllipticCurvePublicKey) -> bytes:
    return key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def _hkdf(salt: bytes, ikm: bytes, info: bytes, length: int) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=length, salt=salt, info=info).derive(ikm)


def push_endpoint_allowed(endpoint: str) -> bool:
    parsed = urlparse(str(endpoint or ""))
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host:
        return False
    return any(
        host == suffix or host.endswith("." + suffix) for suffix in PUSH_SERVICE_HOST_SUFFIXES
    )


class VapidKeys:
    """The server's VAPID key pair, created once and kept in the state dir."""

    def __init__(self, private_key: ec.EllipticCurvePrivateKey) -> None:
        self.private_key = private_key

    @classmethod
    def load_or_create(cls, path: str | Path) -> VapidKeys:
        key_path = Path(path)
        if key_path.exists():
            loaded = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
            if not isinstance(loaded, ec.EllipticCurvePrivateKey):
                raise ValueError("VAPID key file does not hold an EC private key")
            return cls(loaded)
        private_key = ec.generate_private_key(ec.SECP256R1())
        pem = private_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        key_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = key_path.with_name(key_path.name + ".tmp")
        tmp.write_bytes(pem)
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, key_path)
        return cls(private_key)

    @property
    def public_key_b64(self) -> str:
        """The applicationServerKey the browser subscribes with."""
        return b64url_encode(_public_bytes(self.private_key.public_key()))

    def authorization(self, endpoint: str, subject: str, *, ttl: int = 12 * 3600) -> str:
        parsed = urlparse(endpoint)
        header = b64url_encode(json.dumps({"typ": "JWT", "alg": "ES256"}).encode())
        claims = b64url_encode(
            json.dumps(
                {
                    "aud": f"{parsed.scheme}://{parsed.netloc}",
                    "exp": int(time.time()) + ttl,
                    "sub": subject,
                },
                separators=(",", ":"),
            ).encode()
        )
        signing_input = f"{header}.{claims}".encode("ascii")
        der = self.private_key.sign(signing_input, ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        signature = b64url_encode(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
        return f"vapid t={header}.{claims}.{signature}, k={self.public_key_b64}"


def encrypt_payload(
    payload: bytes,
    user_public_key: bytes,
    auth_secret: bytes,
    *,
    salt: bytes | None = None,
    server_private_key: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """Encrypt one Web Push message body with the aes128gcm content coding."""
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise WebPushError("Push payload is too large.")
    salt = salt or os.urandom(16)
    server_key = server_private_key or ec.generate_private_key(ec.SECP256R1())
    server_public = _public_bytes(server_key.public_key())
    user_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), user_public_key)
    shared = server_key.exchange(ec.ECDH(), user_key)
    ikm = _hkdf(
        auth_secret,
        shared,
        b"WebPush: info\x00" + user_public_key + server_public,
        32,
    )
    cek = _hkdf(salt, ikm, b"Content-Encoding: aes128gcm\x00", 16)
    nonce = _hkdf(salt, ikm, b"Content-Encoding: nonce\x00", 12)
    # One record; 0x02 marks the last (and only) record.
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    header = salt + struct.pack("!IB", RECORD_SIZE, len(server_public)) + server_public
    return header + ciphertext


def send_web_push(
    subscription: dict[str, Any],
    data: dict[str, Any],
    vapid: VapidKeys,
    *,
    subject: str,
    ttl: int = 600,
    urgency: str = "high",
    timeout: float = 10.0,
) -> int:
    """Deliver ``data`` to one subscription; returns the push service status."""
    endpoint = str(subscription.get("endpoint") or "")
    if not push_endpoint_allowed(endpoint):
        raise WebPushError("Push endpoint is not a known browser push service.")
    body = encrypt_payload(
        json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        b64url_decode(str(subscription.get("p256dh") or "")),
        b64url_decode(str(subscription.get("auth") or "")),
    )
    try:
        response = requests.post(
            endpoint,
            data=body,
            headers={
                "Authorization": vapid.authorization(endpoint, subject),
                "Content-Encoding": "aes128gcm",
                "Content-Type": "application/octet-stream",
                "TTL": str(int(ttl)),
                "Urgency": urgency,
            },
            timeout=timeout,
            allow_redirects=False,
        )
    except requests.RequestException as exc:
        raise WebPushError(f"Push service unreachable: {exc}") from exc
    try:
        if response.status_code >= 400:
            raise WebPushError(
                f"Push service returned HTTP {response.status_code}",
                response.status_code,
            )
        return response.status_code
    finally:
        response.close()
