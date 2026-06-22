# Getting started

[← docs home](./index.md)

## Prerequisites

| Requirement | Why |
|-------------|-----|
| **Python 3.9+** | the library |
| **Node.js 18+ on PATH** | computes the mandatory `X-Hmac` signature via LINE's `ltsm.wasm` |
| `pip install -r requirements.txt` | `requests` + `cryptography` |
| `pip install qrcode` *(optional)* | render the QR-login code in your terminal |

Check Node is available:

```bash
node --version      # v18 or newer
```

If `node` is somewhere non-standard, set `LINE_NODE=/path/to/node` or pass
`OkLine(config=LineConfig(node_path="/path/to/node"))`.

## Your first call

If you already have an access token (e.g. captured from a logged-in extension,
or from a previous login — see [Authentication](./authentication.md)):

```python
from okline import OkLine

api = OkLine(access_token="...", refresh_token="...")
print(api.get_profile())
# {'mid': 'u...', 'displayName': '...', 'regionCode': 'TH', ...}
```

The first request lazily starts the Node bridge (≈1–2 s to load the WASM), then
reuses it. Send a message:

```python
api.send_text("u0123456789abcdef0123456789abcdef", "hello from python")
```

`to` can be a **user** (`u…`), **room** (`r…`) or **group/chat** (`c…`) mid; the
message type is detected automatically.

## The `OkLine` object

`OkLine` mixes in one typed method per endpoint, plus sub-clients:

```python
api.get_profile()                 # typed endpoint methods (see ENDPOINTS.md)
api.send_text(to, "hi")

api.auth      # login flows        -> AuthFlows   (authentication.md)
api.ops       # incoming events    -> OperationReceiver (receiving-events.md)
api.obs       # media upload/download
api.recorder  # captured exchanges (recording.md)
```

**Generic escape hatch** — every endpoint is also reachable by key, so nothing
is ever out of reach:

```python
api.call("Talk.TalkService.getProfile", 2)        # == api.get_profile()
api.call("Talk.TalkService.sendMessage", 0, {"to": "u...", "text": "hi",
                                              "contentType": 0, "contentMetadata": {}})
```

List every endpoint key:

```python
from okline import all_method_names
print(all_method_names())          # 77 keys
```

## Recording is on by default

Every call is captured. Paste the last one, or the whole session:

```python
api.get_profile()
print(api.last.pretty())     # one HTTP transcript (secrets redacted)
print(api.dump())            # every call this session
```

See [Recording](./recording.md) for export (HAR/JSON) and hooks.

## Clean up

The Node bridge is a subprocess. Close it when done (or use a `with` block):

```python
with OkLine(access_token="...") as api:
    api.get_profile()
# bridge closed automatically
```

## Next steps

- Don't have a token? → [Authentication](./authentication.md)
- Send richer messages → [Sending messages](./messaging.md)
- React to incoming messages → [Receiving events](./receiving-events.md)
- Use it from the shell → [CLI](./cli.md)
