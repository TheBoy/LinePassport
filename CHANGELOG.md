# Changelog

All notable changes to OkLine are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **All chats.** One page, at the top of the menu above the account switcher,
  for every LINE and Telegram account the person can open: customer chats
  and LINE groups newest first, each labelled with its
  channel and account, with unread counts, status and handler. Filter by
  channel, account or status (unread / mine / nobody yet / waiting / closed),
  search by name, message or account, and reply, send images and files, or
  use the customer panel without switching accounts. The list updates live
  and is built from data the server already keeps, so LINE is not polled any
  faster (group names are looked up at most every 10 minutes); a
  disconnected account's chats stay listed with a notice.

- **Customers (CRM).** A new "Customers" section keeps one card per person
  across every account of a shop: contact details, tags, team notes, each
  chat with its status (new / in progress / waiting on customer / closed) and
  the staff member looking after it, follow-up tasks with reminders, and a
  timeline of messages and changes. The same person writing to two accounts
  is one card; duplicates can be merged. Cards are created from incoming
  chats automatically. Data lives in `crm.sqlite3` in the state directory, or
  in the same PostgreSQL database when `OKLINE_DATABASE_URL` is set.

- **Customer panel in Chat.** Beside the conversation: set the chat status,
  take the chat or hand it to someone, add tags, notes and follow-ups. The
  chat list filters by mine / nobody yet / waiting on customer / closed and
  shows who handles each chat. The first staff member to reply takes a new
  chat (can be turned off), quiet chats close after a set number of days, a
  customer writing to a closed chat reopens it, and alerts for an assigned
  chat go only to its handler.

- **Segments and messages to a segment.** Save a filter (tags, status,
  handler, channel, last contact) as a segment, export it as CSV, and send it
  one message with `{ชื่อลูกค้า}` and the scheduler placeholders. Sending is
  one person at a time, 30-90 seconds apart and within the account's hourly
  and daily limits shared with scheduled sends (bots go faster); runs can be
  paused, resumed and cancelled.

- **Telegram alerts for staff.** A shop connects its own Telegram bot
  (Settings > Telegram) and each team member links their Telegram. Alerts for
  waiting customers, assigned chats and due follow-ups follow the browser
  push rules; replying to an alert answers the customer in that person's
  name, and buttons take or close the chat.

- **Telegram as a chat channel.** Add a Telegram bot, or a personal Telegram
  account (`pip install "okline[telegram]"`, sign in with api_id/api_hash,
  phone, code and two-step password), next to LINE accounts. Their chats use
  the same inbox, customer cards, KPI, alerts and AI auto-reply. Tokens and
  sessions are stored encrypted; LINE-only features say so instead of
  failing.

- **LINE sessions stay connected.** Access tokens last about 7 days; a
  background keeper now renews them when fewer than two days remain, and
  retries hourly (with the one-year refresh token) for accounts that dropped
  only because the token expired (code 119), so no QR scan is needed.

- **Disconnected LINE accounts go straight to the QR.** A red "Reconnect
  (scan QR)" button under the account card and a banner above the chat list
  open the QR in one click; the first time a disconnected account is opened
  it goes there by itself. The page explains when LINE signed it out, common
  causes, what to do, and that jobs and settings are kept.
- **Settings is a normal page.** It opens inside the workspace with the
  sidebar still visible, scrolls like every other page, and lists its
  sections in a menu on the left (a scrollable row on phones).
- **Plain-language usage guides.** All seven guides (Chat, Auto reply,
  Scheduled sends, Activity log, Tools, Incoming API, Settings) share one
  layout: what the page is for, numbered steps with jump buttons, a
  "good to know" box and a tap-to-open FAQ. The text matches today's screens.
- **Scheduled sends page with two tabs**, *Scheduled sends* and *Message
  patterns*, under one header whose main button follows the tab. Patterns show
  as cards with category chips, search, a "used in N jobs" badge and a
  *Use in a job* button that opens the wizard with that pattern ticked.
- **Clearer scheduled sends list.** The two tabs are a segmented switch with a
  one-line explanation; three summary tiles show active jobs, the next send
  and today's usage; every job card leads with the job name, a type icon and
  labelled rows (to · when · message · last sends); paused jobs are dimmed.
- **Redesigned LinePassport web UI.** Left sidebar (Chat · Auto reply ·
  Scheduled sends · Staff KPI · Settings, plus a small developer group for API,
  Tools and Activity log), an account card with one connection dot, a phone
  bottom bar, and Thai-only wording in Thai mode. Clicking a chat opens it
  (paste a chat ID into search to open any chat); the Send button has a label.
- **QR how-to.** The QR step of "Add LINE account" lists the three steps for
  scanning it with the LINE app.
- **Scheduled sends: list + 3-step wizard** (who → what → when) with recipient
  names, coloured status, failed jobs pinned on top with a fix button, last-3
  send history, an on/off switch and a ⋯ menu. The wizard has Thai variable
  chips (`{วันที่} {เวลา} {ชื่อผู้รับ} {เลขสุ่ม 1–100}`), message versions 2, 3…
  (saved as patterns), a live preview, once/daily/weekly/custom presets with a
  real time picker and weekdays, a "send a test to myself" button and safe
  defaults (once a day, never every 15 minutes). Repeating jobs support
  `weekdays`, and once-a-day jobs no longer drift.
- **Auto reply without `/god`.** A 3-step setup (choose the AI brain → shop info
  → try a conversation): shops can use a **Gemini API key** or their own Ollama
  (`/api/assistant/settings`, falls back to the global God AI). Shop info is
  edited as topics, saved automatically, and every test answer shows which topic
  it came from. Unanswered questions can be answered once and saved into a
  "Frequently asked" topic.
- **Staff KPI and team sharing.** Owners add staff (`/api/team`) who reply from
  the owner's LINE account and share its shop info. The server tracks waiting
  customer chats and who answered first (staff, AI, or the phone app);
  `/api/kpi` reports first-response time, % within the reply target, chats per
  person, chats waiting now, AI vs staff, and screen time from a presence
  heartbeat (replying / standby / switched away / idle / on other work /
  offline), counted inside shifts. KPI settings cover the reply target, business
  hours and shifts; CSV export included.
- **Live updates and calmer notifications.** `/api/events` (server-sent events)
  replaces 8-second polling; hidden tabs keep updating; the server refreshes every
  signed-in account. Notifications turn on with browser permission (banner for
  "not asked" / "blocked"), at most one per chat per 2 minutes, a single digest
  when many customers write at once, one reminder past the reply target, no alert
  when the AI already answered, and quiet outside your shift. A chip shows how
  fresh the chat list is.
- **Safer scheduled sends.** Per-account sending limits (default 30 per hour,
  200 per day; jobs over the limit wait in a queue), a random delay of up to
  3 minutes (or "send at the exact time"), a clash warning with a suggested free
  time, "pause all" for holidays, one automatic retry 5 minutes after a failure
  and a push alert when a job still fails, and a 7-day calendar of upcoming sends.
  A job can now go to up to 20 chats (sent one by one, 30–90 s apart, with
  `{ชื่อผู้รับ}` filled per chat), carry a picture after its text, use a saved
  message, and repeat monthly (a day of the month or the last day). Repeating
  windows default to 00:00:00–23:59:59 and the end minute is inclusive.
- **LINE operation stream.** The server follows each signed-in account's LINE
  operation stream (the Chrome client's fetchOps) and refreshes the inbox as soon
  as something happens; `getMessageBoxes` polling drops to a once-a-minute safety
  net, and the stream pauses for 15 minutes after 3 failures in a row.
- **Web Push.** Staff with no LinePassport page open still get notified: the
  browser subscribes once notifications are allowed (`/api/push/*`, VAPID key in
  the state dir), and the server applies the same policy — one push per chat per
  2 minutes, a digest for many chats, one reminder past the reply target, a
  30-second wait when the AI may answer, and nothing outside the person's shift.
  Only known browser push services are accepted as endpoints.

### Changed
- **LINE and Telegram accounts are managed apart.** Settings → Account
  Management has a LINE section (QR sign-in, proxy, reconnect by QR) and a
  Telegram section (add a bot or a personal account, new token or sign in
  again, rename); Telegram rows show "Bot · @name" instead of an internal id.
  The account switcher, the "Select an account" screen and team access group
  accounts under LINE and Telegram, the sidebar card and the phone header name
  the channel, and lost-connection prompts, auto reply and errors use the
  account's own words. The Settings tab for staff alerts is now "Telegram
  alerts".

### Fixed
- **Deleting a user left their Telegram accounts running.** Their bots kept
  polling and the saved Telegram sign-in stayed on disk; customer records and
  auto-reply settings of all their accounts stayed too. A user's accounts are
  now removed exactly like a single deleted account.
- **KPI missed replies sent from the Telegram app.** They now count in the
  phone-app row, which names the account's channel.
- **Token refresh never succeeded.** The refresh call sent no access header
  but signed X-Hmac with the stale access token, so the signature could not
  verify. It now signs with no token, and if rejected retries with the stale
  token in X-Line-Access and a matching signature.
- The reconnect page title names the account ("Reconnect LINE account: <name>").
- **Stuck on "Select an account" with several accounts.** Since the sidebar
  redesign, desktop had no account picker on that screen. It now lists the
  accounts (and All chats), and each browser reopens the account it used last.

## [2.8.0] - 2026-07-08

### Added
- **`okline web` — LinePassport.** A new subcommand starts a self-hosted
  web app (default `http://127.0.0.1:8765`, opens your browser automatically)
  with a **guided 4-step QR login wizard** (Start → Scan QR → Confirm PIN →
  Done), contacts/groups browsing, a chat view, sending (text + E2EE), and an
  auto-bot scheduler. Flags: `--host`, `--port`, `--state-dir`, `--database-url`,
  `--no-open`. It binds to `127.0.0.1` by default. See [docs/web.md](docs/web.md).
- **AI images in the web UI.** A new top-level **AI Settings** tab stores an image
  API key locally, and the auto-bot scheduler gains an **“AI image”** content
  type: the bot sends your text prompt to the AI and posts the generated image to
  the target. The AI Settings tab includes a one-click **Generate image** test.
  Two **providers** are supported and switchable per-key: **Google Gemini**
  (“Nano Banana”, called directly) and **nanobananaapi.ai** (a hosted async
  service — OkLine submits the task, polls it to completion, and downloads the
  result). Each provider's key is stored separately under `--state-dir` and is
  never returned to the browser (only a masked preview is).
- **Optional PostgreSQL persistence for the web UI** via `--database-url` (or the
  `OKLINE_DATABASE_URL` / `DATABASE_URL` env vars). Requires the new `web` extra
  (`pip install "okline[web]"`); without it the UI stores state as on-disk JSON.
- **Thai localization** of both the interactive terminal menu and the web UI, so
  non-English users can drive OkLine in their own language.
- **README.th.md** — a Thai quick-start guide.

### Changed
- **`psycopg` is no longer a hard dependency.** It moved from the required
  `dependencies` to a new `web` optional-dependency extra, since it is only used
  (via a lazy import) by the web UI's PostgreSQL store. A plain `pip install
  okline` no longer pulls in `psycopg`.

### Fixed
- **Five CLI subcommands that were previously no-ops now work** as documented.
- Documentation now covers `okline web` (previously undocumented) and corrects the
  README quick-start to use `api.qr_login(on_qr=print_qr)` — the high-level call
  that also loads your E2EE (Letter Sealing) keychain — instead of the low-level
  `api.auth.qr_login(on_qr=print)`.

## [2.7.0] - 2026-06-23

### Added
- **A full-featured interactive TUI.** Running `okline` (no args) now opens a
  **categorised** menu — 8 sections, ~40 actions — covering essentially every
  capability: account/profile/settings/logout, contacts (list/search/find/add/
  block/favorites/export), groups (list/members/leave/accept/boxes), sending
  (text/sticker/location/media/reply/react/unsend/broadcast), reading
  (chat log with E2EE decrypt / raw / search / backup), live bots
  (watch/auto-reply/notify), E2EE (status/send/decrypt/round-trip), and a
  developer section (call any endpoint, list endpoints, self-test, recording).
  Sub-menu navigation with Back/Quit; same soft palette.

## [2.6.0] - 2026-06-23

A code-quality / architecture pass. No behaviour or wire changes — the public API,
protocol, crypto and E2EE framing are unchanged, and the live integration test
still passes.

### Changed
- **Tooling**: adopted **ruff** (lint + format) and **mypy**, configured in
  `pyproject.toml`, with a `.pre-commit-config.yaml`, a `Makefile` (`make
  lint/format/typecheck/test/check`) and a `[dev]` extra.
- **Modernised type hints** to PEP 585/604 (`list`/`dict`/`X | None`) across the
  package; imports sorted; whole codebase auto-formatted.
- **Type-clean**: `mypy` now reports no issues. Introduced a typed
  `services._base.ServiceMixin` so the mixin architecture type-checks, a shared
  `_util.reconfigure_stdout_utf8` helper, and narrowed optional types.
- Minor robustness: `raise ... from` on a re-raise; `qr_login` no longer calls the
  PIN callback with `None`.

A full system audit (every endpoint cross-checked against the real LINE Chrome
bundle; 21/21 read endpoints re-verified live) plus a complete docs rewrite.

### Fixed
- **API fidelity** (the only two drifts found across 88 audited endpoints):
  `getChats` now sends the trailing `syncReason` arg, and `logoutV2` sends an
  empty arg array (it is a no-arg method).
- `get_contacts` auto-chunks at 100 mids (same `Invalid Length` cap as
  `get_chats`), so large accounts can fetch all contacts.
- The **bot framework now transparently decrypts** Letter-Sealed messages —
  `ctx.text` is the plaintext.
- `send_message` only auto-seals text/location on code 82 (no longer mangles
  media-placeholder sends).

### Added / Changed
- CLI: `qr-login` is an alias of `login`; new top-level `--version`/`-V`; new
  `logout` command; `react` takes the reaction as a positional
  (`okline react <id> LOVE`); `send <name>` resolves a contact name to its mid;
  a friendly Node.js preflight before login; `broadcast` stops on rate-limit/abuse.
- **Docs overhaul** — every page rewritten and accurate to this version, with new
  [E2EE](docs/e2ee.md), [Media](docs/media.md) and [Cookbook](docs/cookbook.md)
  pages and a documentation [index](docs/index.md).

## [2.5.2] - 2026-06-23

### Fixed
- `get_chats` now **auto-chunks** at 100 mids per request (and merges the results)
  — large accounts hit `Invalid Length` (code 6) listing groups. Fixes
  `okline groups` / the menu for 100+ chats.
- The CLI now forces UTF-8 stdout, so non-ASCII (e.g. Thai) group/contact names no
  longer raise `UnicodeEncodeError` on Windows code pages.

## [2.5.1] - 2026-06-23

### Changed
- The interactive menu now goes **straight to QR login** when there's no saved
  session (instead of a yes/no prompt) — `okline` on a fresh machine shows the QR
  immediately, scan it, and you're in the menu.

## [2.5.0] - 2026-06-23

### Added
- **Interactive terminal UI** — run `okline` with no arguments for a soft-coloured,
  menu-driven console: pick actions by number, no commands to memorise. It logs in
  by QR on first use and saves the session. New `okline.ui` toolkit (muted palette,
  TTY-aware, ASCII fallback) + `okline.menu`.
- **Full CLI** — `okline <command>` now covers ~30 actions: `login`, `whoami`,
  `profile`, `contacts` (search/export), `find`, `search`, `add`, `block`,
  `favorites`, `groups`, `members`, `leave`, `accept`, `send` (text/sticker/
  location/image/file/`--encrypt`), `react`, `unsend`, `broadcast`, `set-name`,
  `set-status`, `boxes`, `chatlog` (decrypts E2EE), `backup`, `watch`, `autoreply`,
  `notify`, plus the existing `endpoints`/`call`/`selftest`.
- Every command reuses a saved `tokens.json` by default (and restores E2EE keys),
  so after `okline login` the rest "just work".

## [2.4.0] - 2026-06-23

### Added
- **Cross-session E2EE** — the unwrapped keychain is now exported
  (`E2EEManager.export_keys`, via the WASM `e2eekey_export_key`) into the session
  file by `save_tokens` and restored by `from_tokens_file`
  (`load_from_export` / `e2eekey_load_key`). Letter Sealing now works from a saved
  token **without a fresh QR login**.
- **Group Letter Sealing** — decrypt group messages and send to groups that
  already have a key. The group shared key is fetched
  (`getLastE2EEGroupSharedKey` / `getE2EEGroupSharedKey`) and unwrapped via the new
  `e2eechannel_unwrap_group_shared_key` bridge op. `encrypt()`/`decrypt()` route
  group-vs-1:1 automatically. (Bootstrapping a brand-new group key —
  `registerE2EEGroupKey` — is still future work.)

Both live-verified (35/35 live checks pass, incl. group decrypt + cross-session
reload + roundtrip).

## [2.3.0] - 2026-06-23

### Added
- **E2EE / Letter Sealing — now fully live-verified (1:1).** Encrypted **send**
  (V2) and **decrypt of received messages** (both **V1** and **V2** formats) work
  end-to-end against the real servers. Added `E2EEManager.roundtrip()` as a
  self-test, and V1 framing (`build_chunks_v1`/`parse_chunks_v1`) plus the
  `e2ee_decrypt_v1` bridge op (`decryptV1(channel, ciphertext)` — no AAD args).
- **`examples/`** — eight runnable mini-tools (`whoami`, `find_contact`,
  `export_contacts`, `group_members`, `backup_chat`, `send_media`, `broadcast`,
  `watch`) with their own README.

### Fixed
- **Encrypted send was rejected (500/99999).** The sealed message must omit
  `text`/`location`/`from` entirely (the real `EL()` sets them to `undefined`);
  we were sending `text:null`/`from:<mid>`. Now deleted outright.
- **mid type detection is case-insensitive** — modern mids are upper-case
  (`U`/`C`/`R`/`S`); groups were being misclassified as users (wrong `toType`).

### Changed
- Safe, behaviour-preserving optimizations (wire bytes/crypto unchanged): signed
  GET path computed once, recorder uses a bounded `deque`, the SSE response is
  closed in a `finally`, dead imports removed.
- Removed the GitHub Actions workflow; slimmed the README to a clean overview
  with details under `docs/`.

## [2.2.0] - 2026-06-23

### Added
- **Media send (V1)** — `OkLine.send_image/send_video/send_audio/send_file`:
  posts a placeholder message then uploads the bytes to OBS
  (`/r/talk/m/<messageId>`) with the encrypted OBS token. New `okline send` CLI.
- **E2EE / Letter Sealing (experimental, 1:1)** — `okline.e2ee.E2EEManager`
  (loaded automatically by `qr_login`), `api.send_encrypted_text()`,
  `api.decrypt_message()`, and **auto-seal-and-retry** in `send_message` when the
  server rejects plain text with code 82. Framing in `okline.e2ee_crypto`
  (chunks/plaintext) is fully unit-tested; the crypto runs in the WASM bridge.
  Works **in the same session as `qr_login`** (cross-session reuse is future
  work). Group Letter Sealing is not wired yet.
- Errors now surface the **inner Thrift exception** code/reason (e.g. 82
  "can not send using plain mode") instead of a generic `RESPONSE_ERROR`.
- `live_test.py` — detailed live integration test (`--to`, `--image`, `--qr`,
  `--listen`).

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
