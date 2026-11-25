# Changelog

All notable changes to OkLine are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/), and this project adheres to
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- **More AI providers for auto reply.** Settings → AI → "AI for auto reply"
  now has one provider list instead of the Gemini / Ollama / Shared AI cards:
  Gemini, Claude (Anthropic) and ChatGPT (OpenAI) first, then GLM (Z.ai),
  DeepSeek, Qwen (Alibaba Cloud), Grok (xAI), Mistral AI, Kimi (Moonshot AI)
  and OpenRouter, and a **Custom API** for any OpenAI-compatible endpoint
  with its own base URL, model and an optional key. The Custom API address
  must be a public `https://` address: private, loopback, link-local and
  reserved addresses are refused, and its key is only sent to that address
  (the form says so as soon as another server's address is typed). Each call
  to it has one time limit for looking up the host, connecting, the TLS
  handshake, the headers and the body together; its answers have a size cap
  and may not be compressed. Search vectors longer than 8192 numbers are
  refused, and one workspace keeps at most 1,000,000 vector numbers (all
  workspaces together 4,000,000, first come first served, so a few large
  workspaces can use that up); past that its shop info is searched by
  keyword instead, with the reason shown. An ask waits at most 15 seconds
  for its question's search vector and 60 for the answer. AI answers are
  cut to 5,000 characters (LINE's longest message) before they are stored
  or sent. The question log that every workspace shares keeps at most 500
  questions of one workspace, and once it is full (5,000) the workspace
  holding the most questions loses its oldest, so a busy workspace no
  longer pushes out a quieter one's questions. These limits keep the shared
  AI record from growing without bound through AI answers, questions and
  search vectors; moving each workspace's AI data into a record of its own
  is planned.
  Each provider keeps its own API key (shown as `••••abcd`, or only `••••`
  for a key shorter than 16 characters; Show/Hide while typing), so switching
  providers never sends one provider's key to another, and switching back
  finds that provider's saved key and models again. **Refresh models** works
  for every provider, and **Save and test** checks the provider with a short
  request; while that check fails, step 1 of the auto reply setup stays open
  with the reason. Claude needs Python 3.10 or newer on the server
  (`pip install "okline[claude]"`; the Docker image has it); without it
  Claude is listed as not available. A Claude request keeps its 60-second
  limit: it is sent again at most once, after a rate limit or a server error
  whose short wait still fits, and never after a timeout or a lost
  connection, which may already have been answered and billed. New error
  codes, each with Thai and English text: `ai_base_url_required`,
  `ai_base_url_invalid`, `ai_base_url_not_found` (the host does not exist),
  `ai_base_url_blocked`, `ai_model_required`, `ai_provider_unavailable`, and
  `ai_unavailable` (HTTP 422) when the provider fails to answer, which
  replaces `ollama_unavailable`. `ai_key_required` no longer names Gemini and
  Save returns it too; `ai_settings_invalid` now has page text, and `ollama`
  and `global` get it like any unknown provider. An auto reply whose owner
  has no AI set up skips messages quietly and shows "AI not configured".
- **Refresh models in Settings → AI.** "AI for auto reply" has a **Refresh
  models** button next to the chat model: it asks the chosen provider (with
  the key typed in the form, else that provider's saved key; the Custom API
  also uses the address in the form) which models it offers, and turns the
  chat and search model boxes into lists to pick from; "Type a name…" still
  takes any model. The chat model now sits
  next to the key instead of under Advanced options. In "AI image settings",
  the same button adds the Gemini image models that section's key can use to
  the Google model list, and a model picked from it stays listed after a
  reload (`POST /api/ai/models`, needs the manage-accounts permission; nothing
  is saved until Save). It waits at most 30 seconds for the provider, without
  retrying, and then says the models could not be loaded. An API key pasted
  with invisible characters (from a chat or a document) is cleaned when it is
  used or saved, and one with characters no key has is refused with a message
  saying so.
- **Pick a proxy provider and type its key.** The proxy fields (adding a LINE
  account, Edit in Settings → Account Management, adding a LINE OA) have a
  **Proxy source** choice next to the manual host and port: Bright Data,
  Oxylabs, Litport, Shifter or Decodo. With the proxy username and password
  from the provider's dashboard (plus the hub and pool for Litport, and
  optionally an allocated IP for Bright Data), the server builds the proxy
  itself: the provider's gateway and Thailand pinned. On Oxylabs, Litport,
  Decodo and Bright Data without a zone IP each account gets a session of its
  own that keeps one IP as long as the product allows (24 hours on Oxylabs,
  Litport and Decodo residential); a Shifter ISP username (placed in Thailand
  in its panel) or a Bright Data zone IP is one fixed IP. On Decodo, Oxylabs,
  Litport and Bright Data, targeting copied along with a username
  (`-country-…`, `-session-…`, `_pool-…`) is cut off, and a Bright Data
  `-ip-…` becomes the zone IP when that box is empty. Every proxy field
  error now has Thai and English text.
  **Test proxy** (`POST /api/proxy/test`, needs the manage-accounts
  permission) shows which IP and country a proxy exits from, or a saved
  account's proxy when nothing new is typed; Oxylabs and Bright Data proxies
  are checked with the provider's own IP checker, and Start or Save keeps the
  session, so the IP, that the test showed. The formats follow each
  provider's documentation.
- **Rename a customer from the customer panel.** The pencil next to the name in
  the chat's customer panel (Chat and All chats) renames them in place: Enter
  saves, Escape cancels. The new name shows in chat lists, chat titles,
  message senders, All chats, Customers and notifications, for LINE, LINE OA
  and Telegram alike, while the panel keeps showing the name they use
  themselves ("Name on LINE: …"). Searching finds them by either name, the
  customer's history records the rename, and "Use their own name" (or saving
  an empty name) goes back. Until the team renames someone, their name now
  follows the one they use on Telegram or LINE OA. Names typed in the customer
  card before this update count as names the team gave (CRM schema 2 adds
  `crm_customers.name_custom`).
- **A soft chat alert that keeps beeping while a customer waits.** A new
  customer message on any account (All chats keeps them all live) plays a soft
  double "boop": pure 587 Hz sine tones that swell in and fade straight out,
  quieter and lower than the old sounds. Unless "Repeat" is turned off in
  Settings → Notifications, it beeps again every 4 seconds until someone
  opens or answers the chat, and a small bar shows how many customers wait,
  with Open chat and Stop sound. Groups beep once and never repeat, a chat the
  AI may answer waits 20 seconds before repeating, and two open tabs don't
  beep over each other. The reminder past the reply target is the same beep
  three times, a little louder. It replaces the short tick, which only covered
  the selected account, and the three-note chime; Settings → Notifications can
  play it.
- **God page: require a proxy.** A new System settings tab on `/god` can
  require a proxy for LINE accounts, LINE OA accounts, or both. The server
  then refuses to add or reconnect such an account without one and refuses
  to switch an account's proxy off; pages lock "Connect through proxy" on and
  say the admin requires it. Accounts added before keep working, are marked
  "Needs a proxy", and need one before they reconnect; the God page counts
  them.
- **A proxy for LINE OA accounts.** "+ Add a LINE OA" asks for an optional
  proxy before showing the QR code; the OA Manager sign-in goes through it and
  every OA added from that sign-in keeps it (stored encrypted), so the live
  stream, sends, files and reconnects all use the same address. Edit on a
  LINE OA row changes or removes it, like for a LINE account, and restarts
  the OA's stream on the new route. A saved proxy that can't be read stops
  the OA rather than falling back to a direct connection.
- **What proxy to use.** Under the proxy fields of a LINE account (adding
  it, or Edit in Settings → Account Management) LinePassport now says what to
  pick: a residential or ISP proxy with a Thai IP that stays the same, entered
  as host, port, username and password. The web guide and install.md say the
  same. It links no proxy providers.
- **LINE OA accounts (through LINE Official Account Manager).** Settings →
  Account Management has a LINE OA section: "+ Add a LINE OA" shows a QR code
  to scan with LINE on the phone of an account that is a member of the OA,
  then the 4-digit code LINE asks for, then which of its OAs to add. Each OA's
  one-to-one chats arrive live from OA Manager's event stream (recent chats
  are copied in on first connect without alerts) and show in Chat and All
  chats with the customer panel, alerts and KPI. Replies, images and files go
  out through OA Manager; opening a chat marks it read there too; replies the
  team types in OA Manager or the LINE OA app appear in LinePassport and count
  as phone-app replies, while the OA's own auto replies do not. The OA Manager
  cookies are stored encrypted like LINE sessions; when OA Manager signs an OA
  out, LINE is first asked to sign it in again quietly, then the account asks
  for a new scan. A LINE account with no LINE Business ID is told how to
  become a member first (LinePassport never creates one). An OA whose
  response mode is not chat (OA Manager then refuses its chats) is marked
  "chat is off" in the picker and in the account list, and is retried every
  10 minutes until chat is turned on. Email sign-in is
  not offered: that page needs reCAPTCHA and an emailed code. Unofficial: this
  uses the endpoints of the OA Manager web page and may break if LINE changes
  them. Groups, segment sends and scheduled sends are not available for LINE
  OA yet.
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
  → try a conversation): shops pick an AI provider and use their own API key
  (`/api/assistant/settings`). Shop info is
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
- **The God's global knowledge helps every workspace.** The text and API
  sources on the God page's "Knowledge / RAG" tab used to reach only the
  workspaces on the shared AI. Every workspace's auto reply now searches it by
  keyword matching (no embeddings), after the shop's own info, and sends the
  matching parts to that workspace's own AI provider, so it should hold no
  secrets. Saving, syncing and rebuilding its index need no AI. An English
  word matches it only as a whole word or a longer or shorter form of one
  ("red" no longer matches inside "ordered"), so short English questions do
  not pull in unrelated text, and a long message is matched by at most 300
  different terms from its start (English words, and 3-letter groups of Thai
  words), each English word by at most its first 40 letters. In a workspace's
  answer history and test chat it is cited
  as "Shared info from the system administrator", not by the God's file and
  source names.
- **Thai keyword matching.** Thai vowel and tone marks no longer split words,
  so a Thai question finds the related Thai shop info and global knowledge
  more often.
- **Gemini's default chat model is `gemini-3.8-flash`.** `gemini-2.5-flash` is
  deprecated and Google reroutes it from 2026-12-01. Workspaces that set up
  Gemini before this release saved `gemini-2.5-flash` as their chat model
  (the old form always sent the default) and keep it, so the reroute applies
  to them. To use the new default, the owner picks `gemini-3.8-flash` (or
  clears the chat model box) in Settings → AI → "AI for auto reply" and
  presses Save and test.
- **A Gemini model that refuses the temperature still answers.** Google
  ignores `temperature` on Gemini 3.6 and later and says that later models
  will answer it with an error (HTTP 400). When a Gemini model refuses a
  question that way, the question now goes out once more without the
  temperature, inside the same 60 seconds, and that model gets no temperature
  again until LinePassport restarts. Auto reply, Try a conversation and Save
  and test all get this. Every other error stays an error, and a question
  that timed out or lost its connection is still never sent again. Google
  has not published that error's wording, so it is recognised by an HTTP 400
  that names `temperature` or the sampling parameters, or that calls a
  `generationConfig` field unknown or unsupported without being about the
  JSON output.
- **Auto replies share the workers fairly.** The four auto-reply workers that
  every workspace shares run at most two replies of one workspace at a time.
  Further Telegram and LINE OA messages of that workspace wait in a queue of
  20, oldest first; when it is full the oldest is dropped with an activity
  log line ("Waiting AI reply dropped", `ai.auto_reply.dropped`), and what
  it used of the reply limits (20 a minute per account, 6 per chat) is given
  back. Further LINE messages wait too: a place that frees up goes to the
  one that has waited longest of those the latest check found (up to 8 per
  account, a workspace's accounts in turn) and to the queue in turn, so
  neither kind of message holds up the other, and the rest wait for the
  next check. A chat that wrote more at once than the 10 messages LINE
  shows of it (a photo album, say) goes after the chats that were waiting
  at the last check that found it with nothing to answer, though not always
  after one that began to wait a few seconds before it. In an account with
  more than 500 chats it can still go ahead of chats that waited longer when
  its customer's last message before got no reply (and, with 1,000 chats or
  more, when the customer is new to the account or not among its 1,000
  newest chats). A waiting
  LINE message uses the reply limits only once its reply starts (a
  Telegram or LINE OA message uses them when it arrives).
  A LINE account that has used its 20 auto replies of the last minute now
  leaves its further chats waiting until that minute is over instead of
  passing them over; a chat past its own 6 a minute is still passed over.
- **Deleting an account deletes its customers.** Deleting a LINE, LINE OA or
  Telegram account in Settings → Account Management now also deletes every
  customer who chatted only through that account, with their tags, notes,
  tasks, history and broadcast entries. A customer who also chats through
  another account stays, with their other chats. The delete dialog says first
  how many customers will go (`GET /api/accounts/delete-impact`), and the
  confirmation says how many went.
- **Times use a 24-hour clock everywhere.** Times in chat lists, customer
  panels, logs, the KPI calendar and auto-reply history show as 14:05 in
  English too, never 2:05 PM. Every time box (the schedule wizard, KPI
  business hours and shifts, a CRM task's due time) is picked from hour
  (00–23) and minute lists, the way the scheduler always did, because a
  browser in US English shows its own time boxes with AM/PM.
- **The God page signs in on the main page.** `/god` no longer has a sign-in
  form of its own: signed out, it sends you to the main page's form
  (`/?signin=god`), which also accepts a God account and then opens the God
  page. The form shows even when the browser is signed in to a workspace,
  says to sign in with the God account and offers a way back to the
  workspace; that workspace session stays signed in next to the God page.
  God accounts stay apart from workspace accounts (their session works on
  `/god` only, in its own cookie). `POST /api/god/login` now answers 410 with
  a message, so a God page opened before the update says to reload. The God
  page has a **Change password** button for the God account's own password
  (`POST /api/god/change-password`); changing it signs that account out
  everywhere else. A blank name no longer signs in an account whose email is
  empty.
- **LINE and Telegram accounts are managed apart.** Settings → Account
  Management has a LINE section (QR sign-in, proxy, reconnect by QR) and a
  Telegram section (add a bot or a personal account, new token or sign in
  again, rename); Telegram rows show "Bot · @name" instead of an internal id.
  The account switcher, the "Select an account" screen and team access group
  accounts under LINE and Telegram, the sidebar card and the phone header name
  the channel, and lost-connection prompts, auto reply and errors use the
  account's own words. The Settings tab for staff alerts is now "Telegram
  alerts".

### Removed
- **Ollama.** The Ollama choice of "AI for auto reply", its address field and
  the Ollama client are gone.
- **The shared AI.** The God page's "Ollama" tab ("Global AI / Ollama") and
  its routes (`GET`/`POST /api/god/ai/settings` and `POST /api/god/ai/test`,
  which now answer 404). Every workspace uses its own key; the God page keeps
  "Knowledge / RAG" and "Unanswered". Its rebuild-index button now sits next
  to the knowledge.md editor: with unsaved text in the editor it saves and
  indexes that text first, and saving, syncing or deleting an API source no
  longer clears unsaved text. The menu count of unanswered questions loads
  with the page and shows "500+" when more than the 500 it lists are waiting.
- **Upgrading.** Back up before updating (PostgreSQL: `pg_dump`; file
  storage: the state folder, at least `global_ai.json`, `ai_auto_reply.json`
  and `bot_logs.json`). The first start after the update changes the AI
  records one way: every workspace's AI settings move to a new shape, the
  shared AI's settings are deleted, and God answers filed under a staff
  member move to the team owner. The previous version cannot read the new
  shape (a workspace's own Gemini key looks missing, and the shared AI is
  gone), so going back to it needs that backup restored too. Workspaces that
  relied on the shared AI or on Ollama have their auto reply switched off,
  and their owners see a notice in Settings → AI and on the Auto reply page
  asking them to pick a provider and add their key; an account switched off
  this way says so on the Auto reply page until it is switched on again. A
  workspace that relied on a shared AI set to answer only from shop info
  keeps that setting; nothing else of the shared AI's settings carries over.
  Nobody is moved to Gemini with their picture AI key. A state file saved
  as UTF-16 or UTF-32, or with a byte order mark (as Notepad and Windows
  PowerShell can save them), is now read normally. A `global_ai.json` that
  cannot be read at all is not changed by the update, which runs on a later
  start once the file is repaired. The next save of AI settings, shop info
  or the God's knowledge does write a new file in its place, so the app
  first keeps the damaged one as `global_ai.json.unreadable-<date>-<time>`
  (once, however often it restarts): repair from that copy or from the
  backup. While no copy can be kept (a full disk), nothing is saved over a
  damaged state file LinePassport has found; those saves fail until there is
  room, and the page says to make room. Repair a state file, or put a copy
  back, with LinePassport stopped: it keeps sign-ins, accounts and schedules
  in memory and saves them over a file changed while it runs. An install moving
  to PostgreSQL now stops at the state files it cannot read, before moving
  anything into the database, and names each one and the copy it kept:
  repair them (or move them away to go on without them) and start again.
  Self-hosted installs that used a local Ollama need a cloud provider's key,
  or a public `https://` OpenAI-compatible endpoint as a Custom API, since
  local and private addresses are refused.

### Fixed
- **A God answer to a staff member's question was lost.** Answering it on the
  God page's "Unanswered" tab saved the answer under the staff member, where
  no shop's AI looked. It now joins the shop info of the staff member's team
  owner, like an answer to the owner's own question. Answers given before
  this release move to the team owner's shop info on the first start, once;
  those of removed users, or of staff whose owner is gone, stay where they
  are.
- **A LINE message could get two auto replies.** A reply that finished while
  the next check was reading the chats could be sent again, and a check that
  failed partway left its chats never answered again.
- **Every sealed LINE message cost a request to LINE.** Reading a Letter
  Sealing message asked LINE for the sender's key each time, so the
  auto-reply check, the inbox and customer records made one LINE request
  per sealed message they read, again and again for chats waiting for an
  auto reply. Each key is now asked for once.
- **A full disk dropped auto replies the AI had already answered.** When
  the AI question log could not be saved, the answer was not sent (and Try
  a conversation showed an error). It now goes out, and Try a conversation
  on the Knowledge page shows the answer with a note that the question could
  not be saved.
- **A LINE message could go unanswered when saving or reading failed.** A
  failed read of LinePassport's own data while an auto reply was being
  prepared (a refused database connection, say) passed the message over
  for good, and a reply that failed while its chat's read position could
  not be saved left the chat unanswered until a restart. Such a message
  now waits until LinePassport can save again (the AI is not asked again
  meanwhile), and is answered then.
- **A scheduled send could go out twice.** When the schedules could not be
  saved just after a scheduled send went out (a full disk, a database that
  dropped the connection, a file another program held open), the send
  counted as failed and could go out to every chat again, at the retry 5
  minutes later or after a restart; a send whose count toward the hourly
  and daily limits could not be saved counted as failed too. A send that
  went out now counts as sent, and the schedules are saved at the
  scheduler's next round that can (it tries every 10 seconds). While they
  cannot be saved no scheduled send starts, and the schedules page says so;
  it also names a send that went out meanwhile, because restarting
  LinePassport before the schedules are saved loses the record of how it
  ended (see the entry on restarts below). A Run now to
  several chats is saved as started before it is answered, so one that
  cannot start is refused (before, it was queued, dropped without a word and
  shown as sent), and the page now says it has started, not that it was
  sent. Before, a
  round that could not save the schedules, or read their settings, left due
  schedules unsent until a restart. A change to the schedules that could not
  be saved is not kept either (a new, edited, paused or deleted schedule,
  Clear stuck jobs and Resume all), so creating a schedule again after such
  an error no longer adds it twice.
- **A scheduled text with a picture could go out twice.** When the picture
  failed after the text went out (LINE's picture upload failing, say), the
  chat counted as not sent, and when every chat failed that way the retry 5
  minutes later sent the text to every chat again. A chat that got the text
  now counts as sent, and the schedule names the chats whose picture did
  not go out.
- **A scheduled send cut short went wrong.** When LINE asked to sign in
  again partway through a send to several chats, the whole send stayed due,
  so once the account was connected again the chats that had got it got it
  again. When LinePassport was stopped (Ctrl-C) partway, the send counted
  as sent to every chat, and the rest never got it. Now the chats that got
  it do not get it again, and the schedule names those it did not reach
  (also once it waits for the account to be connected again); a send
  stopped before it reached any chat is not counted, and goes out once
  LinePassport starts again (a Run now says it was not sent: one to a
  single chat on the page, one to several chats on the schedule). When
  LinePassport ends without stopping (a crash, a kill), a send it was in the
  middle of goes on when it starts, with the chats it had not reached (see
  the next entry). Either way, a once-a-day send goes out then only while
  that is nearer its time than its next time (see the once-a-day entry
  below).
- **A scheduled send that a restart cut short went out again.** When
  LinePassport was killed or crashed partway through a send to several chats
  (a Docker container stopped or redeployed, say), the next start sent it to
  every chat again, also to those that had got it. Each chat's turn is now
  saved with the schedule before anything goes to that chat, and the next
  start goes on with the chats that have had no turn. It is one run: one
  history line, written when it ends, with the message ids from before and
  after the stop, and one more on the sent count; until then the schedules
  page shows it as "Part sent, goes on". A chat that was being sent to when
  LinePassport was killed gets nothing more, and the schedule's report names
  it as one that may not have got it. If nothing gets through when the send
  goes on (the network is not up yet after a reboot, say), it is tried once
  more 5 minutes later. A Run now that was cut short goes on by itself too,
  once its account's sends are not paused and the account is connected (it
  gets no second try); pressing Run now on a send that was cut short goes on
  with it instead of starting again (new error code `schedule_cut_ended`,
  with Thai and English text, when no chat is left to send to), and Clear
  stuck jobs leaves it to go on. An edit made meanwhile changes only what the
  chats that have had no turn get, and when: the page says so when such a
  schedule is opened. A repeating send whose next run has come due by the
  time LinePassport starts again is not gone on with: that run goes out, to
  every chat, and the cut one counts as it stands, with the chats it did not
  reach named in its history line. A once-a-day send that would go on nearer
  its next start than its own time is not gone on with either: it counts as
  it stands, and goes out at that next start. A send that runs once, or is on
  the last of its runs, goes on however late. A chat whose turn cannot be
  saved (a full disk) gets nothing until it can be, and a send whose end
  could not be saved is no longer sent again by a restart: the chat whose
  result was not saved then shows as one that may not have got it. A send now
  stops before its next chat when its schedule is deleted, or its account
  removed, while it goes out. `okline web` handles SIGTERM (and Ctrl-Break on
  Windows): `docker stop`, a redeploy or `kill` now make it stop in order and
  exit with code 0. Before, it was killed: at once, or, as a container's first
  process, when Docker's 10-second wait was over. Such a stop waits up to 6
  seconds for a message that is on its way and pauses a send between two
  chats, to go on after the next start; Ctrl-C still ends a send and names
  the chats it did not reach. The first start of this version cannot go on
  with a send that the version before was in the middle of (that version
  saved no turns): that send goes out again in full, one last time. Going
  back to that version does the same to a send this version was in the middle
  of. So check that no send is going out, and none waits to go on, before
  updating or going back.
- **A scheduled send waiting its turn ignored changes made meanwhile.**
  LinePassport sends at most four schedules at a time (scheduled sends, and
  Run nows to several chats), across all workspaces; one that comes due
  meanwhile waits its turn. It then went out even when it had been switched
  off, paused with Pause all or moved to a later time meanwhile. It now goes
  out only if it is still due when its turn comes. Editing what a schedule
  sends, or to whom, keeps its next run, so a send that was due (waiting its
  turn, queued for the hourly limit or about to be tried again) is no longer
  put off to its next day. Clear stuck jobs now stops a Run now to several
  chats that waits its turn (deleting the schedule does too, and an edit
  made meanwhile is what goes out). Editing a schedule while it is sending
  no longer shows it as waiting, which let a second run of it start beside
  the first.
- **A once-a-day scheduled send could go out twice, or skip a day.** A send
  that goes out once a day (every 1440 minutes in Custom, say) and whose
  time window ends after midnight (22:00 to 06:00, say) went out twice on
  its first night, at its start and again at midnight, and then at midnight
  each night instead of at its start; one that runs on some days only went
  out twice on each of its days that follows a day it does not run (every
  time for a weekly or monthly send, every Monday night for one from Monday
  to Friday, the days Custom starts with). A once-a-day send whose time
  window ends by midnight and that went out after midnight skipped the next
  day: one late by its random delay (as an every-day send at 23:59 often
  was), a wait for a free sender, the hourly limit or a retry. Switching a
  once-a-day send off and on, or editing when it runs, after it went out in
  a time window that is still open sent it again. After each run, a
  once-a-day send now goes on at the start of its next window, and it goes
  out at most once in each window (a Run now in its window counts too). A
  once-a-day send that could not go out at its time (LinePassport was not
  running, the LINE account had to be connected again, or the hourly or
  daily limit held it) went out as soon as it could, however late; it now
  goes out late only while that is nearer the time it was due than its next
  time (if it then fails, its retry 5 minutes later still goes out), however
  long its time window, and otherwise waits for its next time, and its
  history and the activity log say the one it missed was not sent. A
  repeating send that failed twice on its last day tried again every few
  minutes until its time window closed, with a failure alert every other
  try; it now stops there.
- **An Incoming API request could send its messages twice.** When the
  link's counters could not be saved, or read back (a database that went
  down), after its messages went out, the request answered 500, so a caller
  that tries again sent them again. It now answers with what was sent, and
  the link's counters miss that request.
- **A Telegram bot could stop receiving.** When the bot's place in
  Telegram's list of updates could not be saved (a database that dropped
  the connection, a full disk), the bot received nothing more until its
  account was opened or LinePassport restarted, and the messages it had just
  answered then got their auto reply again. It now keeps receiving, and
  saves its place with the next messages. A message that cannot be stored
  (the database is down, full or read-only) stays with Telegram, which
  hands it over again: the bot tries again after a pause (5 seconds,
  growing to a minute), and the messages after it wait with it; Telegram
  keeps them for a day. A message the database refuses for itself is passed
  over, and the log says so. A message handed over again that is stored
  already gets no second auto reply. The bot that sends staff alerts
  stopped the same way, and then relayed a staff reply to the customer
  again; a staff reply it cannot handle now waits the same way (one that
  waited more than 15 minutes is not sent, and the bot says so). A button
  pressed while a message to the bot before it (a staff reply, say) waits
  for the database is done if the database is back when the bot tries that
  message again, 5 seconds after it first could not, and the bot gets to
  the button within 10 seconds of that first try; otherwise, and for any
  other button pressed while the database is down, the bot says to press
  it again, and so it may for a button pressed while it still works through
  a hundred or more messages that came in meanwhile (a button pressed
  before LinePassport restarts is still done once it runs again).
- **A full disk could stop scheduled sends, broadcasts and auto replies.**
  When the server log was a file on the full disk, a round of the scheduler
  that failed (a database read, say), or of the broadcasts, the auto
  replies or the LINE session keeper, ended it until LinePassport
  restarted, and so did a Telegram bot or LINE OA account that met an
  error; a LINE OA sign-in that failed then kept showing its expired QR,
  and adding a LINE OA again was refused until the dialog was closed.
  Every place that logs an error and goes on now goes on when the
  log cannot be written, and a damaged state file is read as empty then
  too, instead of failing the read.
- **LinePassport could freeze when an account was removed.** Removing an
  account (or a user) just as the scheduler checked the schedules, the
  schedules page loaded or a schedule was saved could leave the two waiting
  for each other, and LinePassport then answered no more requests until it
  was restarted. None of these waits for the account list while it holds
  the schedules any more.
- **Removing an account could leave some of it behind.** A chat stored as
  the account was being removed came back into the CRM, with its customer
  and messages, and so did a chat claimed or closed just then (in the chat
  panel or from a Telegram alert), or a record of that claim or close; a
  question its auto reply was asking the AI just then stayed in the question
  log all workspaces share (as did one of a workspace being deleted); a
  reply ending just then kept its status and log lines; auto-reply settings
  saved just then were kept, switched on, so the same LINE account added
  again later answered customers with the AI by itself; a schedule created
  for it just then was kept, switched on; and a LINE session renewal running
  just then saved the account's sign-in to disk again. None of these is
  kept now.
- **Saving auto-reply settings could make a LINE message get a second
  reply.** A save that overlapped a reply ending wrote back the chats' read
  positions from before that reply, so the next check answered the message
  again.
- **A start that could not read a state file said "could not bind".** It
  first tried 20 ports as if each were in use. It now stops at once with
  the real reason, which names the file. A state file nested too deeply now
  counts as unreadable (a copy is kept) instead of stopping the start
  without naming the file.
- **Syncing a Knowledge API source could undo changes saved meanwhile.** The
  sync wrote back the whole AI record it had read before fetching, so
  questions logged and AI settings saved during the fetch were lost. Now only
  that source's text, sync time and error are written, and a source deleted
  during the fetch stays deleted. This holds for a shop's sources and for the
  God page's.
- **Typing a Gemini key for auto reply switched the picture AI to Google.**
  The key still fills an empty Google key of the AI image tool, but the
  image tool keeps the provider it is on.
- **A blocked Gemini question or answer was an error.** A Gemini safety
  block now counts as a question the AI cannot answer, like Z.ai's content
  filter (code 1301), so auto reply hands over to staff with the shop's
  handover text. An answer cut off by the token limit says so.
- **Rebuilding a shop's search index could drop questions.** The rebuild
  wrote back the whole AI record it had read before calling the AI, so a
  question logged (or another shop's info saved) during the call was lost. It
  now writes into the latest record, and keeps vectors only when they match
  the shop info and search model in use.
- **A URL Python cannot parse broke log scrubbing.** An upstream error
  holding such a URL (an unbalanced IPv6 bracket, for example) made the
  scrubber that hides secrets in log details raise an error. Such a URL now
  keeps only its scheme and a plain host: a user name and password, the
  query and LINE media paths stay hidden, as for any other URL.
- **A wrong password said "Please sign in again".** Sign-in failures and a
  wrong current password shared the error code of an expired session. The
  sign-in form now says the email or password is wrong
  (`invalid_credentials`), and a wrong current password in Change Password
  says so (`current_password_incorrect`) instead of dropping a signed-in
  person back to the sign-in screen.
- **All chats showed one account after time away.** Coming back opened the
  remembered account's own Chat page instead of All chats, and signing in
  again on a tab left open kept "could not load" until the page reloaded. All
  chats now reopens where it was left and reloads right after signing in,
  asks again for any LINE inbox the server has no fresh copy of, and says
  which accounts are still loading or could not be fetched.
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

### Security
- **Any signed-in account could take over the first account.**
  `POST /api/auth/secure` exists so the local user of a new install can set
  its first email and password. It accepted any signed-in account, including
  one registered a minute earlier, and once no local user was left it
  rewrote the first stored account instead: its email, password, role (to
  admin) and active flag. That locked the owner out, or demoted a God
  account. Only the local user can now use it, and only for itself (403
  otherwise).
- **Rate limits could be dodged with a made-up account.** A route's own
  limit (on the AI routes, for example) counted each `?accountId=` value
  apart, so adding any value gave a fresh allowance. Now only the account
  routes that read that value (chats, messages, contacts, auto reply and the
  like) count per account, and only for an account the user can open; every
  other limited route counts per user.
- **Saved AI keys were partly shown to staff.** The AI settings showed the
  last four characters of a saved key to every role, which for a short Custom
  API key is most of it. A key shorter than 16 characters now shows only
  `••••`, and staff and roles that cannot change the AI see no key preview and
  no Custom API address (only whether a key is saved).
- **A compressed answer could take a lot of memory.** On Python 3.10 and newer
  the package now requires urllib3 2.6 or newer, which limits how much of a
  compressed AI provider answer is expanded at once; the Custom API refuses
  compressed answers outright.
- **Staff could send a Knowledge API source's key to another server.**
  Saving a source with a new address kept its saved key, so staff (who may
  edit and sync sources) could point the owner's source at their own server
  and receive the key on the next sync. A saved key now stays with the
  address it was saved for: a source moved to another address (scheme, host
  or port) needs its key typed again, on a shop's sources and on the God
  page's. A source that redirects to another address no longer passes on the
  key or cookies either (a move from `http` to `https` on the same host
  still does).

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
