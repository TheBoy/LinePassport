# OkLine documentation

**OkLine** is a complete, high-level Python client for the API used by the
official **LINE Chrome extension** (`CHROMEOS` 3.7.2). It reproduces the real
protocol byte-for-byte — gateway, headers, payloads, the mandatory `X-Hmac`
signature (computed by LINE's own `ltsm.wasm`), RSA + QR login, the SSE event
stream and OBS media — and records every response so you can paste it.

> ⚠️ **Unofficial.** Not affiliated with LINE Corporation. Use it only with your
> own account and in compliance with LINE's Terms of Service. Treat tokens like
> passwords.

## Features

- ✅ All **77 Thrift endpoints**, typed — or call any of them generically.
- ✅ Mandatory **`X-Hmac`** request signing, handled automatically.
- ✅ **E-mail (RSA)** and **QR** login (QR drawn as ASCII in your terminal).
- ✅ **Full response recording** — `api.last`, `api.dump()`, HAR/JSON export.
- ✅ A **CLI**: `python -m okline call …` from the shell.

## Install

```bash
pip install -r requirements.txt        # requests + cryptography
pip install qrcode                     # optional, for QR-login rendering
```

Requires **Python 3.9+** and **Node.js 18+ on your PATH** (used to compute the
mandatory `X-Hmac` signature — see [architecture](./architecture.md)).

## Quick start

```python
from okline import OkLine

api = OkLine(access_token="...", refresh_token="...")   # reuse a token
print(api.get_profile())
api.send_text("u0123456789abcdef0123456789abcdef", "hello from python")

print(api.last.pretty())     # paste the last request/response
```

No token yet? See [Authentication](./authentication.md) for e-mail and QR login.

## Table of contents

| Page | What's inside |
|------|---------------|
| [Getting started](./getting-started.md) | Install, your first call, the `OkLine` object |
| [Authentication](./authentication.md) | Token reuse, e-mail (RSA), QR login, refresh, logout |
| [Sending messages](./messaging.md) | Text, stickers, location, contacts, flex, reactions |
| [Receiving events](./receiving-events.md) | The SSE stream, operations, a simple echo bot |
| [Recording](./recording.md) | Capture, paste, redact and export every response |
| [CLI](./cli.md) | `python -m okline` subcommands |
| [Architecture](./architecture.md) | The protocol, `X-Hmac`, module map |
| [Troubleshooting](./troubleshooting.md) | Common errors and fixes (FAQ) |
| [Contributing](./contributing.md) | Dev setup, tests, adding endpoints |
| [Endpoint reference](./ENDPOINTS.md) | Every endpoint with its argument fields |
