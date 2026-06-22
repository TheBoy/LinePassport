# How it works (architecture)

[← docs home](./index.md)

OkLine speaks the exact protocol of the LINE Chrome extension (`CHROMEOS`
3.7.2), reverse-engineered from the extension bundle.

## The gateway protocol

| Layer | Detail |
|-------|--------|
| **Gateway** | `https://line-chrome-gw.line-apps.com` |
| **RPC** | `POST /api/talk/thrift/<Namespace>/<Service>/<method>` |
| **Request body** | a JSON **array of positional Thrift args**; struct args are plain JSON objects with **named** (camelCase) fields |
| **Response** | wrapped: `{"message":"OK","data":<result>}` — OkLine unwraps `.data`; a non-`OK` message becomes a `LineApiError` |
| **Auth** | `X-Line-Access: <accessToken>` |
| **App** | `X-Line-Application: CHROMEOS\t3.7.2\tChrome_OS\t` + `X-Line-Chrome-Version: 3.7.2` |
| **Signature** | `X-Hmac: <base64>` on every request (see below) |
| **Locale** | `X-LAL: en_US` + `Accept-Language: en-US` |
| **Receive** | SSE `GET /api/operation/receive` (+ long-poll `LF1`/`JQ`) |
| **Media** | OBS `obs.line-apps.com` + gateway `/api/obs/*` |

Example: `sendMessage(reqSeq, Message)` →

```http
POST /api/talk/thrift/Talk/TalkService/sendMessage
X-Line-Access: <token>
X-Hmac: <base64>
content-type: application/json

[0, {"to":"u...","toType":0,"text":"hi","contentType":0,"contentMetadata":{}}]
```

Every endpoint, with its argument fields, is listed in
[ENDPOINTS.md](./ENDPOINTS.md).

## The `X-Hmac` signature (and why Node.js)

The gateway rejects any request without a valid **`X-Hmac`** header
(`REQUEST_INVALID_HMAC`, code `10005`). The signature is:

```
X-Hmac = base64( Hmac( deriveKey( SHA256("3.7.2"), SHA256(accessToken) ) )
                   .digest(path + body) )
```

where the key is derived from `SecureKey.loadToken(<per-extension token>)`. The
`deriveKey` / `Hmac` / `loadToken` primitives live inside LINE's secure WASM
module **`ltsm.wasm`** (a C++ `LTSM::…` build). Rather than guess at that custom
crypto, OkLine **runs the real module**: a tiny persistent **Node.js bridge**
([`okline/ltsm/ltsm_bridge.js`](../okline/ltsm/ltsm_bridge.js)) loads
`ltsm.wasm` inside a minimal DOM shim and drives it through the same
`postMessage` command protocol the extension uses. The Python side
([`okline/hmac_signer.py`](../okline/hmac_signer.py), class `LtsmBridge`)
manages that subprocess and exposes `sign()`.

The **same bridge** also generates the Curve25519 keypair for QR login
(`curvekey_generate` → `e2ee_public_key`) and can unwrap the E2EE keychain — so
one WASM process serves both signing and login crypto.

The bundled token is specific to this extension build
(`chrome-extension://ophjlpahpchlmihnnnihgmmeilfjmjjc`). Override with
`LineConfig(ltsm_origin=...)` / env `LTSM_ORIGIN` if you swap in a different
build's `ltsm.wasm` + `ltsmSandbox.js`.

## Request lifecycle

```
OkLine.send_text(...)                 # a typed service method
  -> Transport.call(endpoint, args)
       -> resolve path from endpoints.py
       -> JSON-encode the args array               (exact bytes)
       -> LtsmBridge.sign(token, path, body)       -> X-Hmac
       -> add headers, POST via requests.Session
       -> retry once on 401 if a refresh_token exists
       -> unwrap the {message,data} envelope        (errors -> LineApiError)
       -> record the Exchange                        (recorder.py)
  <- decoded result
```

## Module map

| Module | Responsibility |
|--------|----------------|
| `client.py` | `OkLine` facade: services + auth + ops + obs + recorder |
| `transport.py` | HTTP engine: headers, signing, envelope, errors, recording |
| `hmac_signer.py` | `LtsmBridge` — manages the Node bridge (X-Hmac + Curve25519/E2EE) |
| `ltsm/` | `ltsm.wasm`, `ltsmSandbox.js`, `ltsm_bridge.js` |
| `auth.py` | e-mail / QR / token-refresh login flows |
| `crypto.py` | RSA/PKCS1v1.5 login credential encryption |
| `operations.py` | SSE + long-poll operation receiver |
| `obs.py` | object storage (media) |
| `recorder.py` | `Exchange` + `Recorder` (capture / redact / export) |
| `qrterm.py` | terminal ASCII QR |
| `enums.py`, `models.py`, `endpoints.py`, `exceptions.py` | data + registry |
| `services/` | one typed method per endpoint |
| `__main__.py` | the CLI |

## Why not pure Python?

Everything *except* the `ltsm.wasm` crypto is pure Python. The WASM holds the
`X-Hmac` key-derivation; reproducing it without the module would mean
reverse-engineering custom C++ crypto. Running the real module via Node is the
reliable, faithful choice. (Pure-Python re-implementation is an open research
direction — the standard primitives exist, only the exact `deriveKey`/`loadToken`
construction would need to be recovered.)
