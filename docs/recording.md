# Recording & pasting responses

[← docs home](./index.md)

OkLine captures **every** request/response by default, so you can inspect or
paste the full exchange for any endpoint. Implementation:
[`okline/recorder.py`](../okline/recorder.py).

## The basics

```python
api = OkLine(access_token="...")     # record=True by default
api.get_profile()
api.send_text("u...", "hi")

api.last                 # the most recent Exchange (or None)
api.history              # list[Exchange], oldest → newest
print(api.last.pretty()) # one HTTP transcript, secrets redacted
print(api.dump())        # every call this session
```

A transcript looks like:

```
#1 [OK] POST /api/talk/thrift/Talk/TalkService/getProfile   (Talk.TalkService.getProfile)
======================================================================
  -> POST https://line-chrome-gw.line-apps.com/api/talk/thrift/Talk/TalkService/getProfile
  >  X-Line-Application: CHROMEOS	3.7.2	Chrome_OS
  >  X-Line-Access: <redacted>
  >  X-Hmac: <redacted>
  >  body: [ 2 ]
  <- HTTP 200   109 ms
  <  resp: { "mid": "u...", "displayName": "...", "regionCode": "TH" }
```

## The `Exchange` object

| Attribute | Meaning |
|-----------|---------|
| `seq` | call number this session |
| `method`, `url`, `path`, `endpoint` | the request target |
| `request_headers`, `request_body` | what was sent |
| `status`, `response_headers`, `response_body`, `response_text` | what came back |
| `duration_ms`, `ok`, `error`, `started_at` | timing / outcome |

Methods: `.pretty(redact=True)`, `.to_dict()`, `.to_har_entry()`.

## Filtering

```python
api.recorder.find("Talk.TalkService.sendMessage")   # all calls to one endpoint
api.history[-5:]                                     # last 5 calls
```

## Exporting

```python
api.save_log("session.txt")              # plain transcript
api.save_log("session.json", fmt="json") # structured JSON
api.save_log("session.har", fmt="har")   # open in browser DevTools → Network
```

HAR files import straight into Chrome/Firefox DevTools for a familiar view.

## Redaction (important)

By default OkLine **masks secrets** so you can safely paste output:

- request headers: `X-Line-Access`, `X-Hmac`, `Authorization`, `Cookie`, …
- request/response body keys: `password`, `accessToken`, `refreshToken`,
  `certificate`, `secret`, `encryptedKeyChain`, `verifier`.

Reveal them when you really need to:

```python
print(api.last.pretty(redact=False))     # one-off
api = OkLine(access_token="...", redact=False)   # whole session
```

> 🔒 Even with redaction on, **don't** paste production logs publicly without a
> second look — response bodies can contain personal data (mids, names, …).

## Live hooks

React to each call as it completes:

```python
@api.on_exchange
def _(ex):
    print(ex.seq, ex.endpoint, ex.status, f"{ex.duration_ms:.0f}ms")
```

Or pass `OkLine(on_exchange=callback)`. Hooks never break a call — exceptions in
a hook are swallowed.

## Controls

```python
OkLine(record=False)            # disable recording entirely
OkLine(record_capacity=2000)    # keep more history (default 500, ring buffer)
api.clear_log()                 # drop captured exchanges
```

Set `LINE_DEBUG=1` in the environment to also dump raw response bodies to stderr
as they arrive.
