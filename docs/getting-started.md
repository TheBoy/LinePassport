# Getting started

[← docs home](./index.md)

This page takes you from nothing to a working session: install OkLine, log in
once with `okline login`, then drive it either from the **interactive menu** or
from **Python**.

## 1. Install

OkLine is on PyPI:

```bash
pip install okline
```

Want the login QR drawn inline in your terminal? Install the optional extra:

```bash
pip install "okline[qr]"
```

### Prerequisites

| Requirement | Why |
|-------------|-----|
| **Python 3.9+** | runs the library |
| **Node.js 18+ on your PATH** | computes the mandatory `X-Hmac` signature via LINE's bundled `ltsm.wasm` (a tiny Node bridge) |
| `okline[qr]` *(optional)* | renders the QR-login code inline instead of printing a URL |

Check Node is available:

```bash
node --version      # v18 or newer
```

If `node` lives somewhere non-standard, point OkLine at it with the `LINE_NODE`
environment variable (e.g. `LINE_NODE=/full/path/to/node`).

## 2. Log in once

> **Not comfortable in a terminal? Start here.** The friendliest first run is the
> LinePassport: just run **`okline web`**, and a guided wizard opens in your browser
> and walks you through scanning the QR — no commands to learn. Full walkthrough in
> **[LinePassport](./web.md)**. The terminal `okline login` below does the same login
> without a browser; use whichever you prefer.

Run the login command and scan the QR with the LINE app
(**Settings › Add friends › QR code**), then confirm the PIN it shows on your
phone:

```bash
okline login
```

On success you'll see something like:

```text
Logged in as Your Name  (u0123...)
E2EE keys ready: yes
Session saved to tokens.json — reused by every other command.
```

The session is written to `./tokens.json` **including your E2EE (Letter Sealing)
keychain**, so encrypted chats keep working across runs without re-scanning.
Every other command (and `OkLine.from_tokens_file`) reuses this file
automatically. Treat it like a password.

> For e-mail/password login, token refresh and logout, see
> [Authentication](./authentication.md).

## 3a. Use the interactive menu

Run `okline` with **no arguments** to open a soft-coloured terminal menu — pick
actions by number, nothing to memorise:

```bash
okline
```

The menu is **categorised** into **8 sections**; pick a section by number to open
its sub-menu of actions:

```text
OkLine  ·  LINE in your terminal
  Your Name   u0123...        e2ee ready

   1  Me & account          — profile, name, status, settings, logout
   2  Contacts & people     — list/search, find, add, block, favorites, export
   3  Groups & chats        — groups, members, leave/accept, message boxes
   4  Send a message        — text, sticker, location, media, reply, react, broadcast
   5  Read & history        — chat log (decrypts E2EE), raw, search, backup
   6  Live & bots           — watch, auto-reply, keyword notifier
   7  E2EE / Letter Sealing — status, encrypt, decrypt, round-trip test
   8  Advanced / developer  — call any endpoint, list, self-test, recording

 choose:
```

Each section opens a sub-menu with **Back** and **Quit** entries, so you can
explore all ~40 actions without memorising anything. The menu can also display
in **Thai**. (Exact labels may differ slightly by version/language.)

If you haven't logged in yet, running `okline` goes straight to QR login first.
Type `q` (or `0`) to quit.

## 3b. Use it from Python

A minimal program: load the saved session, read your profile, and send a text
message.

```python
from okline import OkLine

# Restores tokens AND your E2EE keychain from the file `okline login` wrote.
api = OkLine.from_tokens_file("tokens.json")

profile = api.get_profile()
print("Logged in as:", profile["displayName"])

# `to` is a mid: a user (u...), group/chat (c...) or room (r...) — case-insensitive.
api.send_text("u0123456789abcdef0123456789abcdef", "hello from python")
```

The first request lazily starts the Node bridge (about 1–2 s to load the WASM),
then reuses it for the rest of the session.

### Don't have a saved session?

Build the client directly from tokens you already hold, or run a QR login from
Python:

```python
from okline import OkLine

# (a) reuse tokens you captured elsewhere
api = OkLine(access_token="...", refresh_token="...")

# (b) or log in by QR, then save a session for next time
api = OkLine()
api.qr_login(on_qr=lambda url: print("scan:", url),
             on_pin=lambda pin: print("confirm PIN:", pin))
api.save_tokens("tokens.json")     # writes tokens + E2EE keys
```

With a refresh token set, OkLine automatically refreshes the access token on a
401 (and re-saves the session file if it came from one).

### Clean up

The Node bridge is a subprocess. Close it when done, or use a `with` block:

```python
with OkLine.from_tokens_file("tokens.json") as api:
    print(api.get_profile()["displayName"])
# bridge closed automatically
```

## Explore the CLI

The CLI has ~30 commands. List them all, or get help for one:

```bash
okline -h               # all commands
okline send -h          # help for a single command
okline whoami           # your profile + account stats
okline contacts --search alice
okline send alice "hi"  # a unique contact name resolves to its mid
```

Every command also runs as `python -m okline <command>`. See the full reference
in [CLI](./cli.md).

## Next steps

- Send richer messages → [Sending messages](./messaging.md)
- Share photos and files → [Media](./media.md)
- Encrypt & decrypt chats → [E2EE / Letter Sealing](./e2ee.md)
- React to incoming messages → [Receiving events](./receiving-events.md) and [Bots](./bots.md)
- Copy-paste a working script → [Cookbook](./cookbook.md)
