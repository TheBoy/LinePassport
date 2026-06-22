# OkLine — Python client / SDK for the LINE Chrome messaging API

<!-- After creating the repo, replace `NiceDayZc` with your GitHub handle. -->
[![CI](https://github.com/NiceDayZc/OkLine/actions/workflows/ci.yml/badge.svg)](https://github.com/NiceDayZc/OkLine/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.9%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Tests](https://img.shields.io/badge/tests-392%20passing-brightgreen.svg)](tests/)
[![Endpoints](https://img.shields.io/badge/endpoints-77-blue.svg)](docs/ENDPOINTS.md)
[![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](docs/contributing.md)

**OkLine** is an unofficial, high-level **Python client / SDK for LINE** that
reproduces **every** endpoint of the official **LINE Chrome extension**
(`CHROMEOS` 3.7.2). Use it to **send and receive LINE messages from Python**, log
in by **QR code or e-mail**, and automate your own account — with the real
protocol reproduced byte-for-byte (gateway, headers, payloads, the mandatory
`X-Hmac` signature via LINE's real `ltsm.wasm`, login crypto, the SSE event
stream and OBS media) and **every response recorded** so you can paste it.

> **Keywords:** LINE API · LINE bot · LINE messaging API · unofficial LINE
> client · Python LINE SDK · line-chrome · send LINE message in Python.

`import okline` · `from okline import OkLine`

**Highlights**
- ✅ All 77 Thrift endpoints, typed; or call any of them generically.
- ✅ Mandatory `X-Hmac` signing handled automatically (runs the real WASM).
- ✅ E-mail (RSA) **and** QR login — QR rendered as ASCII in your terminal.
- ✅ **Full response recording** — paste the response of every endpoint.
- ✅ A CLI (`python -m okline …`) to call any endpoint from the shell.

> คลาส Python ที่ทำงานได้จริง ครอบคลุม **ทุก endpoint** ของส่วนขยาย LINE บน Chrome
> (`CHROMEOS 3.7.2`) — gateway, header, payload, การเข้ารหัส login, สตรีมรับข้อความ
> (SSE) และการอัปโหลดสื่อ (OBS) ครบทุกอย่าง ส่ง payload และ header ครบถ้วนตรงกับ
> ตัวจริง

---

## ⚠️ Legal / ความรับผิดชอบ

This is for **interoperability, research and use with your own account**. Using
it must comply with LINE's Terms of Service. Don't use it for spam, scraping
other people's data, or anything abusive. You are responsible for how you use it.

---

## Install

```bash
pip install -r requirements.txt        # requests + cryptography
# optional: pip install qrcode          # to render QR-login codes
```

Python 3.9+. **Node.js 18+ must also be on your PATH** — it is required to
compute the mandatory `X-Hmac` request signature (see below).

## ⭐ X-Hmac request signing (required)

The gateway rejects any request missing a valid **`X-Hmac`** header
(`REQUEST_INVALID_HMAC`, code 10005). That signature is produced by LINE's
secure WASM module (`ltsm.wasm`), so this library ships the real module and
runs it through a tiny persistent **Node.js bridge** to sign every request
exactly like the extension:

```
X-Hmac = base64( Hmac( deriveKey( SHA256("3.7.2"), SHA256(accessToken) ) )
                   .digest(path + body) )
```

It "just works" as long as `node` is on your PATH — the bridge starts lazily on
the first request and is reused. Configuration:

```python
from okline import OkLine, LineConfig
api = OkLine(config=LineConfig(
    node_path="node",          # or an absolute path; env LINE_NODE also works
    enable_hmac=True,          # set False only for offline/mocked tests
))
```

The bundled `ltsm.wasm` + token are specific to this extension build
(`chrome-extension://ophjlpahpchlmihnnnihgmmeilfjmjjc`, v3.7.2). Override the
origin with `LineConfig(ltsm_origin=...)` or env `LTSM_ORIGIN` if you swap in a
different build's artifacts.

---

## How the protocol works

| Layer | Detail |
|-------|--------|
| **Gateway** | `https://line-chrome-gw.line-apps.com` |
| **RPC form** | `POST /api/talk/thrift/<Namespace>/<Service>/<method>` |
| **Body** | a JSON **array of positional Thrift args**; struct args are plain JSON objects with **named** (camelCase) fields |
| **Auth header** | `X-Line-Access: <accessToken>` |
| **App header** | `X-Line-Application: CHROMEOS\t3.7.2\tChrome_OS\t` |
| **Version header** | `X-Line-Chrome-Version: 3.7.2` |
| **Signature** | `X-Hmac: <base64>` on every request (computed by `ltsm.wasm`, see above) |
| **Locale** | `X-LAL: en_US` + `Accept-Language: en-US` |
| **Receive** | SSE `GET /api/operation/receive` (+ long-poll `/api/talk/long-polling/LF1`,`/JQ`) |
| **Token refresh** | `POST /api/auth/tokenRefresh` `{refreshToken}` |
| **Media** | OBS `obs.line-apps.com` + gateway `/api/obs/*` |

Example — `sendMessage(reqSeq, Message)` becomes:

```http
POST /api/talk/thrift/Talk/TalkService/sendMessage
X-Line-Access: <token>
X-Line-Application: CHROMEOS	3.7.2	Chrome_OS
content-type: application/json

[1, {"to":"u...","toType":0,"text":"hi","contentType":0,"contentMetadata":{},"sessionId":0}]
```

A full, generated list of all 77 endpoints with their argument fields and example
payloads is in **[`docs/ENDPOINTS.md`](docs/ENDPOINTS.md)**.

---

## Quick start

```python
from okline import OkLine, enums

# A) reuse an existing access token (e.g. captured from the running extension)
api = OkLine(access_token="...", refresh_token="...")
print(api.get_profile())
api.send_text("u0123456789abcdef0123456789abcdef", "hello from python")

# B) log in with e-mail + password (RSA flow, exactly like the extension)
api = OkLine()
res = api.auth.email_login("me@example.com", "secret", with_e2ee=False)
if res.success:
    api.send_text("u....", "hi!")

# C) log in by scanning a QR code with your phone
api = OkLine()
api.auth.qr_login(on_qr=print, on_pin=lambda pin: print("PIN:", pin))
```

---

## Authentication

Three faithful flows, all under `api.auth` (see [`auth.py`](okline/auth.py)):

### E-mail + password (RSA)
```python
res = api.auth.email_login(email, password, with_e2ee=True)
# internally: getRSAKeyInfo -> RSA/PKCS1v1.5 encrypt
#   chr(len(sessionKey))+sessionKey+chr(len(email))+email+chr(len(pw))+pw
#   -> loginV2(LoginRequest{identifier=keynm, password=<hex>, ...})
```

### QR-code (secondary device login)
```python
from okline.qrterm import print_qr
api.auth.qr_login(on_qr=lambda url: print_qr(url),     # renders an ASCII QR in the terminal
                  on_pin=lambda pin: print("PIN:", pin))
# createSession -> createQrCode -> curveKeyGenerate (in the WASM)
#   -> show QR (callbackUrl + ?secret=<curve25519 pubkey>&e2eeVersion=1)
#   -> checkQrCodeVerified (long-poll) -> verifyCertificate / PIN flow
#   -> qrCodeLoginV2 (issues the token) -> best-effort E2EE keychain unwrap
```
The `?secret=…&e2eeVersion=1` is **mandatory** — the LINE app shows "an error
occurred" right after scanning if it is missing. This library generates the
Curve25519 key inside the real `ltsm.wasm` (via the same Node bridge used for
`X-Hmac`) and appends it automatically, so you just render the URL `on_qr`
gives you.

### Token refresh (automatic)
If you supply a `refresh_token`, a `401` triggers an automatic
`POST /api/auth/tokenRefresh` and the request is retried.

---

## Sending messages

```python
api.send_text(to, "plain text")
api.reply_text(to, "a reply", related_message_id="140000...")
api.send_sticker(to, package_id="11537", sticker_id="52002734")
api.send_location(to, 35.6586, 139.7454, title="Tokyo Tower")
api.send_contact(to, contact_mid="u....")
api.send_flex(to, "alt text", {"type": "bubble", ...})

# low-level: build any Message yourself
from okline import Message
api.send_message(Message.text(to, "hi", content_metadata={"AGENT_NAME": "bot"}))

# reactions, unsend, read receipts
api.react("140000...", enums.PredefinedReactionType.LOVE)
api.cancel_reaction("140000...")
api.unsend_message("140000...")
api.send_chat_checked(chat_mid, last_message_id="140000...")   # mark read
```

`to` may be a user (`u…`), room (`r…`) or group/chat (`c…`) mid — the message
builder auto-detects `toType`.

## Receiving messages

```python
for op in api.ops.iter_operations():          # SSE stream, auto-reconnect
    if op.type == enums.OpType.RECEIVE_MESSAGE and op.message:
        print(op.message["from"], op.message.get("text"))
```

Or get the raw SSE events (`ping`, `fullSync`, …) via `api.ops.stream()`.

---

## Recording — paste the response of every endpoint

Recording is **on by default**. Every request/response is captured in full
(method, URL, all headers, body, status, timing) as an `Exchange`:

```python
api = OkLine(access_token="...")     # record=True, redact=True by default
api.get_profile()
api.send_text("u...", "hi")

print(api.last.pretty())             # the most recent call, as an HTTP transcript
print(api.dump())                    # every call this session
api.history                          # list[Exchange]
api.recorder.find("Talk.TalkService.getProfile")   # filter by endpoint

# export
api.save_log("session.txt")                  # plain transcript
api.save_log("session.har", fmt="har")       # open in browser devtools
api.save_log("session.json", fmt="json")

# secrets (token / X-Hmac / passwords) are masked by default — reveal with:
print(api.last.pretty(redact=False))
api = OkLine(access_token="...", redact=False)

# react to each call live:
@api.on_exchange
def _(ex):
    print(ex.seq, ex.endpoint, ex.status, f"{ex.duration_ms:.0f}ms")
```

Disable with `OkLine(record=False)`. Set `LINE_DEBUG=1` to also dump raw bodies
to stderr.

---

## CLI

Call any endpoint and paste its response straight from the shell:

```bash
python -m okline endpoints                 # list every endpoint key (optionally grep)
python -m okline version

# call ANY endpoint (args = positional thrift args as a JSON array)
python -m okline call Talk.TalkService.getProfile "[2]" --token "$TOKEN"
python -m okline call Talk.TalkService.sendMessage \
   '[0,{"to":"u...","text":"hi","contentType":0,"contentMetadata":{}}]' --token "$TOKEN"
python -m okline call LoginQrCode.SecondaryQrCodeLoginService.createSession "[{}]" --no-auth --raw

# log in by scanning a terminal QR, then save the tokens
python -m okline qr-login --save tokens.json

python -m okline profile --token "$TOKEN" --raw   # --raw prints the full transcript
```

`--raw` prints the full HTTP transcript; `--show-secrets` unmasks tokens. Tokens
come from `--token`/`--refresh` or `LINE_ACCESS_TOKEN`/`LINE_REFRESH_TOKEN`.

---

## Full method map

Every endpoint has a typed method on `OkLine` (and is reachable generically via
`api.call("Namespace.Service.method", *args)`).

| Area | Methods |
|------|---------|
| **Auth** | `auth.email_login`, `auth.qr_login`, `auth.refresh_access_token`, `login_v2`, `logout_v2`, `confirm_e2ee_login`, `get_rsa_key_info`, `get_encrypted_identity_v3`, `acquire_encrypted_access_token` |
| **Messaging** | `send_message`/`send_text`/`send_sticker`/`send_location`/`send_contact`/`send_flex`/`reply_text`, `unsend_message`, `send_postback`, `react`, `cancel_reaction`, `send_chat_checked`, `send_chat_removed`, `set_chat_hidden_status`, `get_recent_messages`, `get_previous_messages`, `get_messages_by_ids`, `get_message_boxes`, `get_message_boxes_by_ids`, `get_message_read_range`, `determine_media_message_flow`, `get_last_op_revision` |
| **Contacts** | `get_all_contact_ids`, `get_contacts`, `find_contact_by_userid`, `find_contacts_by_phone`, `find_and_add_contacts_by_mid`, `block_contact`, `unblock_contact`, `get_blocked_contact_ids`, `update_contact_setting`, `set_favorite`, `hide_contact`, `get_favorite_mids`, `get_recommendation_ids`, `get_blocked_recommendation_ids`, `block_recommendation`, `add_friend_by_mid`, `get_target_profile_notice`, `get_buddy_detail` |
| **Chats / Groups / Rooms** | `create_chat`/`create_group`, `update_chat`, `rename_chat`, `set_chat_favorite`, `invite_into_chat`, `kick_from_chat`, `cancel_chat_invitation`, `leave_chat`, `accept_chat_invitation`, `reject_chat_invitation`, `get_all_chat_mids`, `get_chats`, `invite_into_room`, `leave_room`, `get_rooms` |
| **Profile / Settings** | `get_profile`, `update_profile_attributes`, `set_display_name`, `set_status_message`, `get_settings`, `get_settings_attributes2`, `update_settings_attributes2`, `get_configurations`, `get_server_time`, `report_abuse` |
| **E2EE keys** | `get_e2ee_public_key`, `negotiate_e2ee_public_key`, `get_e2ee_public_keys_ex`, `get_last_e2ee_public_keys`, `register_e2ee_group_key`, `get_e2ee_group_shared_key`, `get_last_e2ee_group_shared_key` |
| **Channel / Shop / OBS** | `issue_channel_token`, `get_owned_product_summaries`, `iter_owned_products`, `preview_customized_image_text`, `set_customized_image_text`, `obs.upload_profile_image`, `obs.upload_object`, `obs.download_object`, `obs.copy_for_message` |

---

## Package layout

```
okline/
  client.py         OkLine facade (transport + auth + ops + obs + recorder + services)
  transport.py      HTTP engine: headers, X-Hmac, JSON encode/decode, errors, recording
  hmac_signer.py    LtsmBridge — manages the Node bridge (X-Hmac + Curve25519/E2EE)
  ltsm/             ltsm.wasm + ltsmSandbox.js + ltsm_bridge.js (LINE's real module)
  auth.py           email / QR / token-refresh login flows
  crypto.py         RSA/PKCS1v1.5 login credential encryption
  operations.py     SSE + long-poll operation receiver
  obs.py            object storage (media) upload/download
  recorder.py       Exchange + Recorder (capture/redact/pretty/HAR/JSON)
  qrterm.py         render a QR as ASCII/Unicode in the terminal
  enums.py          all enums (OpType, ContentType, ErrorCode, ...) — extracted verbatim
  models.py         Message builders (text/sticker/location/contact/flex)
  endpoints.py      complete endpoint registry (all 77 paths)
  exceptions.py     OkLine error hierarchy
  services/         one typed method per endpoint, grouped by area
  __main__.py       the `python -m okline` CLI
  selftest.py       live read-only self-test of every endpoint
docs/               full documentation (see below)
example.py          runnable examples
tests/              392 offline tests (conftest fixtures + per-module files)
```

## 📚 Documentation

| Page | What's inside |
|------|---------------|
| [Getting started](docs/getting-started.md) | install, your first call, the `OkLine` object |
| [Authentication](docs/authentication.md) | token reuse, e-mail (RSA), QR login, refresh, logout |
| [Sending messages](docs/messaging.md) | text, stickers, location, contacts, flex, reactions |
| [Receiving events](docs/receiving-events.md) | the SSE stream, an echo bot |
| [Recording](docs/recording.md) | capture, paste, redact and export every response |
| [CLI](docs/cli.md) | `python -m okline` — call any endpoint, `selftest`, `qr-login` |
| [Architecture](docs/architecture.md) | the protocol, `X-Hmac`, module map |
| [Troubleshooting](docs/troubleshooting.md) | common errors & fixes (FAQ) |
| [Contributing](docs/contributing.md) | dev setup, tests, adding endpoints |
| [Endpoint reference](docs/ENDPOINTS.md) | every endpoint with its argument fields |

## Tests

```bash
python -m pytest -q             # 392 offline tests, no network/Node needed
```

## Notes on fidelity

- Enum values, endpoint paths, header strings and the RSA login format are taken
  **verbatim** from the extension bundle.
- Argument *order* for each method matches the wire (verified against call sites).
- E2EE (Letter Sealing) key **endpoints** are implemented; performing the actual
  Curve25519 ECDH + AES message encryption is left to the caller (the key structs
  are returned raw). Non-E2EE messaging works out of the box.
- `reqSeq` is auto-generated (monotonic) when you don't pass one.

---

## Publishing to GitHub

After creating the repo, set a keyword-rich **description** and **topics** (the
two biggest factors for GitHub search), and replace `NiceDayZc` in the
badges / `pyproject.toml` URLs:

```bash
gh repo create OkLine --public --source . --remote origin --push \
  --description "Unofficial Python client/SDK for the LINE Chrome messaging API — send/receive LINE messages, QR & e-mail login, X-Hmac signing, full response recording."

# Topics (GitHub allows up to 20) — these drive discoverability:
gh repo edit --add-topic line,line-api,line-bot,line-messaging-api,line-chrome \
  --add-topic python,python-line,line-sdk,line-client,unofficial \
  --add-topic messaging,chatbot,automation,reverse-engineering,chrome-extension
```

Recommended **topics**: `line` · `line-api` · `line-bot` · `line-messaging-api`
· `line-chrome` · `python` · `python-line` · `line-sdk` · `line-client` ·
`unofficial` · `messaging` · `chatbot` · `automation` · `reverse-engineering` ·
`chrome-extension`.

Tips that help ranking & community score: keep the first README paragraph
keyword-rich (done), add a **social-preview image** in *Settings → General*,
enable Issues/Discussions, tag a **release** (`v2.0.0`), and keep CI green.

---

## Disclaimer

OkLine is an **unofficial, independent** project and is **not affiliated with,
endorsed by, or sponsored by LINE Corporation**. "LINE" is a trademark of its
respective owner. Use it only with **your own account** and in compliance with
LINE's [Terms of Service](https://terms.line.me/line_terms). You are responsible
for how you use it. See [SECURITY.md](SECURITY.md) for handling credentials.

## License

[MIT](LICENSE) © OkLine contributors.
