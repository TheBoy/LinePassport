# Authentication & login

[← docs home](./index.md)

There are three ways to get an authenticated `OkLine`. All login traffic is
signed with `X-Hmac` (so **Node.js must be on your PATH** — see
[architecture](./architecture.md)).

> 🔒 **Tokens are credentials.** An access token grants full access to your LINE
> account. Never commit, log, paste or share them. OkLine redacts them by
> default in recorded output.

---

## 1. Reuse an existing token

The simplest option — if you already have an access token:

```python
from okline import OkLine

api = OkLine(access_token="...", refresh_token="...")
print(api.get_profile())
```

If you pass a `refresh_token`, OkLine automatically refreshes the access token
when the server returns `401` and retries the request (see
[Token refresh](#token-refresh)).

---

## 2. E-mail + password (RSA)

This mirrors the extension exactly: it fetches an RSA key, encrypts your
credentials and calls `loginV2`.

```python
api = OkLine()
result = api.auth.email_login("me@example.com", "secret", with_e2ee=False)

if result.success:
    print("access token:", result.access_token[:12], "…")
    print(api.get_profile())
elif result.type == 3:   # REQUIRE_DEVICE_CONFIRM
    print("Confirm this PIN on your phone:", result.pin_code)
else:
    print("needs verification:", result.type, result.display_message)
```

`LoginResult` fields: `.success`, `.type` (`LoginResultType`), `.access_token`,
`.refresh_token`, `.certificate`, `.pin_code`, `.display_message`, `.raw`.

`with_e2ee=True` negotiates Letter Sealing (E2EE). Under the hood the password
blob is `chr(len(sessionKey)) + sessionKey + chr(len(email)) + email +
chr(len(password)) + password`, RSA/PKCS1v1.5-encrypted to hex — handled for you
in [`okline/crypto.py`](../okline/crypto.py).

---

## 3. QR-code login (recommended)

Scan a QR with the LINE app on your phone — no password needed. OkLine renders
the QR as ASCII right in your terminal and drives the whole flow:

```python
from okline import OkLine
from okline.qrterm import print_qr

api = OkLine()
result = api.auth.qr_login(
    on_qr=lambda url: print_qr(url),          # draw the QR (scan it)
    on_pin=lambda pin: print("PIN:", pin),    # show the PIN to confirm
    wait_seconds=180,                         # how long to wait for you
)
print("logged in:", bool(result.access_token))
```

What happens:

```
createSession → createQrCode → (generate Curve25519 key in the WASM)
  → show QR  =  callbackUrl + ?secret=<pubkey>&e2eeVersion=1
  → checkQrCodeVerified (you scan) → verifyCertificate / PIN flow
  → checkPinCodeVerified (you confirm the PIN) → qrCodeLoginV2 → tokens
```

The **`?secret=…&e2eeVersion=1`** is mandatory — without it the LINE app shows
"an error occurred" after scanning. OkLine generates the Curve25519 keypair
inside the real `ltsm.wasm` and appends it automatically, so you only render the
URL `on_qr` hands you.

**Light terminal?** Pass `print_qr(url, invert=True)`. **QR garbled on Windows?**
run `chcp 65001` first, or use `print_qr(url, style="full")`.

### Skip the PIN on later logins

`qr_login` returns a `certificate`. Save it and pass it next time to skip the PIN
step (`verifyCertificate` succeeds for a known device):

```python
result = api.auth.qr_login(on_qr=print_qr, certificate=saved_certificate)
```

---

## Token refresh

Automatic: with a `refresh_token` set, a `401` triggers
`POST /api/auth/tokenRefresh` and the original request is retried. You can also
refresh manually:

```python
new_access = api.auth.refresh_access_token()
```

---

## Saving & reusing tokens

```python
import json
res = api.auth.qr_login(on_qr=print_qr)
json.dump({
    "accessToken": res.access_token,
    "refreshToken": res.refresh_token,
    "certificate": res.certificate,
}, open("tokens.json", "w"))            # keep this file secret!

# later
t = json.load(open("tokens.json"))
api = OkLine(access_token=t["accessToken"], refresh_token=t["refreshToken"])
```

The CLI can do this for you: `python -m okline qr-login --save tokens.json`
(see [CLI](./cli.md)).

---

## Logout

Invalidate the current session on the server:

```python
api.logout_v2()
```

You can also remove the device from **LINE app → Settings → Account → logged-in
devices**.
