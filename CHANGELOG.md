# Changelog

All notable changes to OkLine are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [2.1.0] - 2026-06-23

### Added
- **Bot framework** (`okline.bot.Bot`) — `@bot.on_message`, `@bot.command("…")`
  and `@bot.on(OpType…)` decorators with a `MessageContext.reply()` helper and a
  resilient `bot.run()` dispatch loop.
- **Typed entities** (`okline.entities`) — `Profile`, `Contact`, `Group`, `Room`
  dataclasses with `from_dict` (raw payload kept on `.raw`).
- **Session persistence** — `OkLine.from_tokens_file(path)` / `api.save_tokens()`
  (auto-saves on token refresh); `okline.Session`.
- **Rate limiter** — `okline.ratelimit.RateLimiter` (token bucket), attachable as
  `api.transport.rate_limiter`.
- **Media message builders** — `Message.image/video/audio/file`.
- `py.typed` marker (PEP 561) — the package now ships type information.

### Experimental / known limitations
- Full **media upload** (`send_image` over OBS) and **E2EE message
  encrypt/decrypt** (Letter Sealing) require a live session to finalise and are
  not yet shipped end-to-end; the building blocks (media metadata builders, the
  Curve25519/E2EE bridge primitives, the E2EE key endpoints) are in place.

## [2.0.0] - 2026-06-22

The library was renamed from `line_chrome_api` to **`okline`** (main class
`OkLine`; `LineApi` kept as an alias).

### Added
- **Full response recording** — every request/response is captured as an
  `Exchange`. Inspect via `api.last` / `api.history`, format with
  `Exchange.pretty()` / `api.dump()`, export with `api.save_log(..., fmt="text"|"json"|"har")`.
  Secrets are redacted by default; `on_exchange` hooks let you observe calls live.
- **Command-line interface** (`python -m okline` / `okline`): `call`, `qr-login`,
  `profile`, `endpoints`, `version`.
- Curve25519 key generation + E2EE keychain unwrap via the LTSM bridge, so
  **QR login works fully** (the QR now carries the required
  `?secret=<pubkey>&e2eeVersion=1`).
- Terminal ASCII/Unicode QR rendering (`okline.qrterm.print_qr`).
- Response-body secret redaction (access/refresh tokens, certificate, keychain).
- GitHub project scaffolding, multi-file test-suite and full docs.

### Fixed
- Correctly unwrap the `{"message":"OK","data":...}` gateway envelope (previously
  caused `KeyError`), and surface non-OK envelopes as `LineApiError`.

## [1.0.0] - 2026-06-22

### Added
- Initial client covering all 77 Thrift-over-JSON endpoints of the LINE Chrome
  extension (CHROMEOS 3.7.2): typed service methods + a generic `call()`.
- Mandatory **`X-Hmac`** request signing, computed by LINE's real `ltsm.wasm`
  module driven through a persistent Node.js bridge.
- E-mail (RSA/PKCS1v1.5) and secondary-device QR login flows.
- SSE + long-poll operation receiver, OBS media client, full enum/struct set,
  and message builders.
