# Receiving messages & operations

[← docs home](./index.md)

Incoming activity (new messages, invitations, reads, …) arrives as a stream of
**operations**. OkLine exposes it through `api.ops`
([`okline/operations.py`](../okline/operations.py)), which uses the modern
Server-Sent-Events transport (`GET /api/operation/receive`) with automatic
reconnect.

## A simple echo bot

```python
from okline import OkLine, enums

api = OkLine(access_token="...", refresh_token="...")

for op in api.ops.iter_operations():          # blocks, auto-reconnects
    if op.type == enums.OpType.RECEIVE_MESSAGE and op.message:
        msg = op.message
        sender, text = msg.get("from"), msg.get("text")
        print(f"[{sender}] {text!r}")
        if text:
            api.send_text(sender, f"you said: {text}")
```

`iter_operations()` yields `Operation` objects:

| Field | Meaning |
|-------|---------|
| `type` | an `OpType` (see below) |
| `revision` | sync cursor |
| `param1` / `param2` / `param3` | operation-specific (mids, flags, …) |
| `message` | the `Message` dict, on message ops |
| `reqSeq`, `checksum` | request metadata |
| `raw` | the original op dict |

## Raw events

For control events (keep-alives, full-sync notices) use the lower-level stream:

```python
for ev in api.ops.stream():        # yields SSEEvent(event, data, id)
    if ev.event == "ping":
        continue
    if ev.event in ("fullSync", "partialFullSync"):
        ...   # the server is asking you to re-sync
    else:
        ...   # default events carry operations
```

Named events: `ping`, `connInfoRevision`, `reconnect`, `talkException`,
`fullSync`, `partialFullSync`.

## Common `OpType` values

From `okline.enums.OpType`:

| Value | Name | Meaning |
|------:|------|---------|
| 25 | SEND_MESSAGE | you sent a message (echo) |
| 26 | RECEIVE_MESSAGE | someone sent you a message |
| 55 | NOTIFIED_READ_MESSAGE | a message you sent was read |
| 124 | NOTIFIED_INVITE_INTO_CHAT | you were invited to a chat |
| 130 | NOTIFIED_ACCEPT_CHAT_INVITATION | someone joined a chat |
| 122 | NOTIFIED_UPDATE_CHAT | a chat was updated |
| 5 | NOTIFIED_ADD_CONTACT | someone added you |
| 140 | NOTIFIED_SEND_REACTION | someone reacted |

The full list (≈150 values) is in [`okline/enums.py`](../okline/enums.py).

## Long-poll fallback & revision

The classic long-poll endpoints are also available:

```python
api.get_last_op_revision()                       # current sync cursor
api.ops.long_poll(session_id, endpoint="LF1")    # one blocking round-trip
```

> **Tip:** combine receiving with [recording](./recording.md) — every reply you
> send is captured too, so you can replay a whole bot session.
