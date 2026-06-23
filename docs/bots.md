# Building bots & helpers

[← docs home](./index.md)

OkLine ships a small framework for writing LINE bots, plus typed models, session
persistence and rate limiting.

## A bot in 10 lines

```python
from okline import OkLine, Bot

api = OkLine.from_tokens_file("session.json")   # log in once, reuse forever
bot = Bot(api)

@bot.on_message
def echo(ctx):
    if ctx.text:
        ctx.reply(f"you said: {ctx.text}")

@bot.command("ping")          # triggers on "/ping"
def ping(ctx):
    ctx.reply("pong")

bot.run()                     # blocks; Ctrl-C to stop
```

### Handlers

| Decorator | Fires on |
|-----------|----------|
| `@bot.on_message` | every incoming text/message (`MessageContext`) |
| `@bot.command("name")` | a message starting with `/name` (configurable `bot.command_prefix`) |
| `@bot.on(OpType.X, ...)` | specific operation types (`EventContext`) |

```python
from okline import enums

@bot.on(enums.OpType.NOTIFIED_INVITE_INTO_CHAT)
def on_invited(ctx):
    print("invited:", ctx.op.param1, ctx.op.param2)
```

### `MessageContext`

| Member | Meaning |
|--------|---------|
| `.text`, `.sender`, `.to`, `.content_type` | message fields |
| `.is_group` | True for group/room/square chats |
| `.reply(text)` | reply into the same conversation (group → group, DM → sender) |
| `.reply_sticker(pkg, id)` | reply with a sticker |
| `.mark_read()` | send a read receipt |

Options: `Bot(api, ignore_self=True, auto_mark_read=False)`. Handler exceptions
are caught and logged so one bad handler never kills the loop.

## Typed models

Wrap raw dict responses for attribute access (the original payload stays on
`.raw`):

```python
from okline import Profile, Contact, Group
from okline.entities import parse_contacts

me = Profile.from_dict(api.get_profile())
print(me.display_name, me.region_code)

contacts = parse_contacts(api.get_contacts(["u..."]))   # {mid: Contact}
for mid, c in contacts.items():
    print(c.name, "official" if c.is_official else "")
```

Available: `Profile`, `Contact`, `Group`, `Room` (and `entities.Message`).

## Session persistence

Log in once, then reuse — tokens auto-save when refreshed:

```python
api = OkLine()
api.auth.qr_login(on_qr=print)     # first run only
api.save_tokens("session.json")

# later runs
api = OkLine.from_tokens_file("session.json")
api.get_profile()                  # instant, refreshes + re-saves as needed
```

> Keep the session file private — it holds live credentials (it is covered by
> the project `.gitignore`).

## Rate limiting

Avoid `EXCESSIVE_ACCESS` / `ABUSE_BLOCK` by spacing requests:

```python
from okline.ratelimit import RateLimiter
api.transport.rate_limiter = RateLimiter(rate=5, per=1.0)   # ~5 requests/sec
```

## Media & E2EE (experimental)

`Message.image/video/audio/file` build the message half of a media send, and the
Curve25519 / E2EE primitives are exposed via the LTSM bridge. Full media
**upload** and **Letter-Sealing** message encrypt/decrypt are not yet wired
end-to-end — see [messaging](./messaging.md). Contributions welcome!
