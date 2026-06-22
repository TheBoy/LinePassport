# Command-line interface

[← docs home](./index.md)

OkLine ships a CLI so you can hit any endpoint and paste its response straight
from the shell. Run it as a module:

```bash
python -m okline <command> [options]
```

After `pip install .` an `okline` console script is also installed, so
`okline <command>` works too.

## Global options

| Option | Meaning |
|--------|---------|
| `--token T` | access token (or env `LINE_ACCESS_TOKEN`) |
| `--refresh R` | refresh token (or env `LINE_REFRESH_TOKEN`) |
| `--show-secrets` | do **not** redact tokens/secrets in transcripts |

## `endpoints` — list every endpoint

```bash
python -m okline endpoints           # all 77 keys + their paths
python -m okline endpoints message   # filter by substring
```

## `call` — hit any endpoint

```bash
python -m okline call <Namespace.Service.method> [argsJSON] [options]
```

`argsJSON` is the positional Thrift args as a **JSON array** (default `[]`).
Options: `--raw` (print the full HTTP transcript), `--no-auth` (don't require a
token — for pre-login endpoints).

```bash
# read your profile
python -m okline call Talk.TalkService.getProfile "[2]" --token "$TOKEN"

# send a message
python -m okline call Talk.TalkService.sendMessage \
  '[0,{"to":"u0123...","text":"hi","contentType":0,"contentMetadata":{}}]' \
  --token "$TOKEN"

# a pre-login call (no token needed) + full transcript
python -m okline call LoginQrCode.SecondaryQrCodeLoginService.createSession "[{}]" \
  --no-auth --raw
```

Output is the decoded JSON response, or with `--raw`, the full request/response
transcript (secrets redacted unless `--show-secrets`).

## `profile` — quick self profile

```bash
python -m okline profile --token "$TOKEN"
python -m okline profile --token "$TOKEN" --raw     # with transcript
```

## `qr-login` — log in by scanning a terminal QR

```bash
python -m okline qr-login                       # prints an ASCII QR; scan it
python -m okline qr-login --save tokens.json    # save the issued tokens
python -m okline qr-login --wait 240            # wait up to 240s
python -m okline qr-login --invert              # for light-background terminals
```

It draws the QR, waits for you to scan + confirm the PIN, prints your profile,
then saves/prints the tokens. **Keep `tokens.json` secret.**

## `selftest` — verify the endpoints against the real server

Calls every **read-only** endpoint (discovering your mid / first contact / first
chat as it goes) and prints a pass/fail table. State-changing endpoints
(sendMessage, block, leave, …) are **not** run.

```bash
python -m okline selftest --token "$TOKEN"
python -m okline selftest --token "$TOKEN" --verbose --save selftest.txt
```

```
OkLine self-test - 21/21 endpoints OK

  [OK ] Talk.TalkService.getProfile                      200     109ms  {mid, regionCode, displayName}
  [OK ] Talk.TalkService.getAllContactIds                200     125ms  list[148]
  [OK ] Talk.TalkService.getContactsV2                   200     125ms  {contacts}
  ...
All read-only endpoints responded successfully. [PASS]
```

Exit code is `0` if everything passed, `1` if any endpoint failed.

## `version`

```bash
python -m okline version
# OkLine 2.0.0 (emulates LINE CHROMEOS 3.7.2)
```

## Notes

- Node.js 18+ must be on your PATH (for `X-Hmac`) — see
  [architecture](./architecture.md).
- Exit codes: `0` success, `1` request/runtime error, `2` bad arguments.
