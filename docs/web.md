# LinePassport (`okline web`)

[← docs home](./index.md)

`okline web` starts **LinePassport** on your own machine — no
terminal knowledge needed. It gives you a **guided QR-login wizard**, a contacts
and groups browser, a chat view, message sending (plain **and** E2EE), a simple
auto-bot scheduler, and multi-account management, all in a web page.

It is the friendliest way to get started: run one command, scan a QR in your
browser, and you're in. If you have never used a terminal beyond this single
command, this is the page for you.

## Start it

```bash
okline web
```

You'll see:

```text
LinePassport running at http://127.0.0.1:8765/
Press Ctrl-C to stop.
```

The UI serves on **`http://127.0.0.1:8765`** by default and **opens your browser
automatically**. Leave the terminal window open while you use it; press
**Ctrl-C** there to stop the server. (If port 8765 is busy, LinePassport tries the next
few ports and prints the URL it actually bound to.)

> **Requirements:** Python 3.9+ and **Node.js 18+ on your PATH** (used to sign
> requests with `X-Hmac`), powered by the OkLine client. See
> [Getting started](./getting-started.md).

## First run: create an admin sign-in

The web UI is protected by its own username/password so nobody else on your
machine can drive your LINE account through the page. On the **first run** it
asks you to **create an admin account** (default username `admin`); on later runs
you simply **sign in**. This login is local to the web app — it is not your LINE
account.

## Log in to LINE (the guided wizard)

Once you're signed in to the web app, add a LINE account with the 4-step wizard:

1. **Start** — click **Add Account → Start** to begin a clean login flow. The
   account name is taken from your LINE profile automatically.
2. **Scan QR** — a QR code appears in the page. Open **LINE on your phone →
   Add friends → QR code** and scan it.
3. **Confirm PIN** — the page shows a PIN. If LINE asks, confirm that same number
   on your phone.
4. **Done** — LinePassport finishes login, loads your E2EE (Letter Sealing) keychain,
   and opens the account. Your session is saved so you don't have to scan again.

You can add **several LINE accounts** and switch between them with the account
picker at the top of the page.

## A proxy per LINE or LINE OA account

Tick **Connect through proxy** in step 1 of adding a LINE account, or set it later
in Settings → Account Management → Edit. HTTP, HTTPS, SOCKS5 and SOCKS5H are
supported; the proxy is used only by that account and its password is stored
encrypted.

- **What to pick:** residential or ISP proxies with an IP in the account's
  country (Thailand for Thai accounts) that stays the same: a static IP or a
  sticky session. When the IP keeps changing or is abroad, LINE may sign the
  account out or ask to verify often. Give each account its own IP.
- **Filling it in:** providers usually hand out `host:port:username:password`;
  put each part in its own field.
- **Or pick a provider:** under **Proxy source**, choose Bright Data, Oxylabs,
  Litport, Shifter or Decodo and type the proxy username and password from the
  provider's dashboard (Litport also takes its hub host and pool; Bright Data
  takes the zone's IP when a Thai IP is allocated). LinePassport fills in the
  gateway, pins Thailand and keeps one IP as long as the provider allows
  (up to 24 hours on Oxylabs, Litport and Decodo; Shifter uses the IP you
  placed in Thailand in its panel). On Oxylabs, Litport, Decodo and Bright
  Data without a zone IP, each account gets its own session, so its own IP.
  A Shifter username or a Bright Data zone IP is one fixed IP, so give each
  account a different one. On Decodo, Oxylabs, Litport and Bright Data, a
  username copied with targeting already in it (`-country-…`, `-session-…`,
  `_pool-…`) is cut back to the account name; a Bright Data `-ip-…` is used as
  the zone IP when the zone IP box is empty (a broken one is refused).
  **Test proxy** shows the IP and country the proxy exits from (Oxylabs and
  Bright Data are checked with the provider's own IP checker), and the account
  then keeps the IP the test showed. **Enter it myself** keeps the host and
  port fields as before.
- **Requiring one:** on the God page (`/god`) → System settings → Require a
  proxy, separately for LINE and LINE OA accounts. Then "Connect through
  proxy" is ticked and locked, and no account of that kind can be added or
  reconnected without one. Accounts added before keep working, are marked
  "Needs a proxy", and need one before they reconnect. The God page shows
  how many accounts still have none.

LINE OA accounts take a proxy too: "+ Add a LINE OA" asks for it before the QR
code, the sign-in goes through it, and every OA added from that sign-in keeps
it; change or remove it later with Edit. Telegram accounts don't use it
(Telegram uses `LINEPASSPORT_TELEGRAM_PROXY`).

## After login

- **Contacts & groups** — load and search your contacts, list your groups.
- **Chat** — open a conversation by contact name or MID, read recent messages
  (Letter-Sealed messages are decrypted when your E2EE keys are loaded), and send
  text — with an **E2EE** button for encrypted sends.
- **Auto Bot** — schedule a one-off or repeating send (text, image, an
  **AI-generated image**, or content fetched from an API), with an optional
  active-time window.
- **Tools** — look up a user by LINE ID, or call any endpoint directly.
- **AI Settings** — store a Nano Banana (Google Gemini) API key so the bot can
  generate images from a text prompt. See below. In Settings → AI, **Refresh
  models** lists the chat and search models the chosen auto reply provider
  offers for your key (see [AI for auto reply](#ai-for-auto-reply)), and adds
  the Gemini image models your key can use to the image model list.
- **Deleting an account** (Settings → Account Management) also deletes the
  customers who chatted only through it, with their tags, notes, tasks and
  history; customers who also chat through another account stay. The dialog
  says how many customers will go before you confirm.
- **Times** show and are picked on a 24-hour clock (14:05), in English too.

## Scheduled sends and restarts

A scheduled send (or a Run now) to several chats goes to one chat at a time, with
a pause of 30 to 90 seconds between chats, so it can take many minutes.
LinePassport saves each chat's turn with the schedule before it sends to that
chat, so a stop in the middle never sends to a chat twice:

- **A stop by the system, a kill or a crash** (`docker stop`, a redeploy, a
  server restart, `kill`): the send goes on after the next start, with the chats
  that have not had their turn. Until then the schedules page shows it as
  **Part sent, goes on**. It is still one run: one line in its history, written
  when it ends, and one more on its sent count. A stop by the system (SIGTERM;
  Ctrl-Break on Windows) waits up to 6 seconds for the message that is on its
  way and stops before the next chat. After a kill or a crash nobody knows
  whether the message that was on its way arrived: that chat gets nothing more,
  and the schedule's report names it ("may not have got it").
- **Ctrl-C at the terminal** ends the send, as before: the chats it did not reach
  are named on the schedule and get nothing later.
- A send that had reached no chat yet is not run: a scheduled one goes out after
  the next start; a Run now does not, and its schedule says that it was not sent
  (after a kill or a crash: that it was recovered after an unexpected stop).

After the start a scheduled send goes on at the scheduler's first round (about
10 seconds), under the usual rules: not while the schedule is switched off, its
account's sends are paused, the account has to be connected again, or it is
over its hourly or daily limit. A Run now that was cut short goes on by itself
too (also for a schedule that is switched off). If nothing gets through at that
first try (the network is not up yet after a reboot, say), a scheduled send is
tried once more 5 minutes later, as a whole send that reaches nobody is; a Run
now is not tried again, and what it could not send is reported. What a send
sends is made again when it goes on (another of its message patterns may be
drawn, an AI image is generated again), and an edit made meanwhile counts: the
rest of the send goes out as edited, also at a new time, and the chats that
have had their turn still get nothing from it. For a new message to every
chat, make a new schedule instead of changing one that shows **Part sent, goes
on** (the page says so when you open such a schedule to edit it).

Two cases end a send where it stopped instead, with the chats it did not reach
named in its history line: a repeating send whose next run has come due by then
(that run goes out, to every chat), and a once-a-day send that would go on
nearer its next start than the time it was for (it goes out at that start). A
send that runs once, or is on the last of its runs, has no next run: it goes
on however late. Pressing **Run now** on a send that was cut short goes on with
it instead of starting again, and **Clear stuck jobs** leaves it to go on.

**In Docker**, `docker stop` and a redeploy send SIGTERM and kill the container
10 seconds later. LinePassport handles SIGTERM: it stops in order and exits with
code 0. The image starts it with `exec`, so it is the container's first process
and gets the signal itself; `init: true` (`docker run --init`) is not needed for
that and does no harm (the init passes the signal on). A command of your own
that wraps LinePassport in a shell or a script has to `exec` it as the image
does: otherwise the signal stops at the wrapper, with or without an init, and
LinePassport is killed without stopping in order (exit code 137 once the 10
seconds are over, when the wrapper is the first process). A message that takes
longer than the 10 seconds to go out is cut by the kill as well; raise
`stop_grace_period` (`docker stop -t`) if you see that. Either way no chat gets
a send twice.

> **Updating to this version, or going back:** the version before saved no
> turns, so the first start of this one cannot go on with a send that the old
> one was in the middle of: that send goes out again in full, one last time.
> Going back to that version does the same to a send that this one was in the
> middle of or shows as **Part sent, goes on**: the old version knows no turns
> and sends it to every chat again. Check that no send is going out, and none
> waits to go on, before you update or go back.

## AI for auto reply

Auto reply answers customers with an AI service the shop owner picks in
**Settings → AI → AI for auto reply** (or in step 1 of the auto reply setup).
Each workspace uses its own API key; there is no shared AI.

| Group | Providers |
|-------|-----------|
| Popular | Gemini (Google), Claude (Anthropic), ChatGPT (OpenAI) |
| More providers | GLM (Z.ai), DeepSeek, Qwen (Alibaba Cloud), Grok (xAI), Mistral AI, Kimi (Moonshot AI), OpenRouter |
| Your own API | Custom API (OpenAI-compatible) |

1. **Pick the provider** and paste its **API key**; the line under the box says
   where to get one (for GLM (Z.ai), a Standard API key: Coding Plan keys do
   not work). Each provider keeps its own key: switching providers clears the
   box and never sends one provider's key to another, and a saved key shows
   only as `••••abcd`, or `••••` when it is shorter than 16 characters (leave
   the box blank to keep it). Staff see neither the preview nor a Custom API
   address. A Gemini key saved for AI images is used when the Gemini box is
   left blank.
2. **Chat model** — leave the suggested model, or press **Refresh models** to
   pick from the models your key can use ("Type a name…" takes any other).
   Refresh models waits at most 30 seconds for the provider. Under *Advanced
   options*, providers that offer embeddings also have a **search model**
   (optional), and "Answer only from shop info".
3. **Save and test** saves the settings and sends the provider a short check.
   If the check fails, the reason stays next to the button, and step 1 of the
   auto reply setup stays open until a check passes.

**Custom API** works with any server that speaks the OpenAI chat completions
API (`POST {base URL}/chat/completions`, and `GET {base URL}/models` for
Refresh models). Enter its **base URL** (for example
`https://api.example.com/v1`), the **model name** (required) and, if the server
needs one, its key. The LinePassport server itself calls that address, so it
must be a public `https://` address: private, loopback, link-local and other
reserved addresses are refused, a host that does not exist is reported as not
found, and a domain name in Thai (or another script) is typed in its `xn--`
form. The key is only sent to the saved address: once you type another
server's address, the form says to type that server's key. Each call has one
time limit for the whole request (looking up the host included), an answer
has a size cap and may not be compressed, and search vectors longer than 8192
numbers are refused. One workspace keeps at most 1,000,000 vector numbers,
and all workspaces together 4,000,000 (first come, first served); past that
the shop info is found by keyword search, with the reason shown. A search
model is optional; without one the shop info is found by keyword search.

**Claude** uses the official `anthropic` package, which needs **Python 3.10 or
newer** on the server: `pip install "okline[claude]"`. The Docker image
installs it. On a server without it, Claude is listed as "not available".

The God page's **Knowledge / RAG** text is used by every workspace too: it is
searched by keyword matching (an English word matches whole words or longer
and shorter forms of it, never letters inside another word), and only the
parts that are about the customer's question (at most three) are sent, after
the shop's own info, to that workspace's provider. A workspace sees it cited
as "Shared info from the system administrator".

Each workspace runs at most two auto replies at a time on the four workers
that every workspace shares. Further Telegram and LINE OA messages wait in a
queue of 20 per workspace, and when it is full the oldest one is dropped with
an activity log line. Further LINE messages wait too, without using up the
reply limits: a place that frees up goes to the one that has waited longest
of those the latest check found (up to 8 per account, a workspace's accounts
in turn) and to the queue in turn, and the rest wait for the next check. A
chat that wrote more at once than the 10 messages LINE shows of it (a photo
album, say) goes after the chats that were waiting at the last check that
found it with nothing to answer, though not always after one that began to
wait a few seconds before it. In an account with more than 500 chats it can
still go ahead of chats that waited longer when its customer's last message
before got no reply (and, with 1,000 chats or more, when the customer is new
to the account or not among its 1,000 newest chats). A LINE account that has
used its 20 auto replies
of the last minute (failed replies count too) leaves its further chats
waiting until the minute is over.

> **Upgrading from Ollama or the shared AI:** back up before updating
> (PostgreSQL: `pg_dump`; file storage: the `--state-dir` folder, at least
> `global_ai.json`, `ai_auto_reply.json` and `bot_logs.json`). The first start
> changes the AI records one way: each workspace's AI settings move to a new
> shape and the shared AI's settings are deleted. An earlier version cannot
> read them (a workspace's own Gemini key looks missing, and the shared AI is
> gone), so going back to it needs that backup restored too.
> LinePassport no longer supports Ollama or a shared AI set up on the God page.
> Auto replies that used them are switched off once, and their owners see a
> notice asking them to pick a provider and add a key; each account switched
> off this way says so until it is switched on again. A workspace that relied
> on a shared AI set to "Answer only from shop info" keeps that setting. A
> self-hosted install that used a local Ollama needs a cloud provider's key,
> or a public `https://` OpenAI-compatible endpoint as a Custom API.
>
> Workspaces that set up Gemini before this release keep the chat model they
> saved, usually `gemini-2.5-flash` (the old form always sent it), which Google
> reroutes from 2026-12-01. To use the new default, `gemini-3.8-flash`, pick it
> (or clear the chat model box) and press **Save and test**.

## AI images (Nano Banana)

LinePassport can have the bot **generate an image from a text prompt** and post the
result to a chat. Two providers are supported — pick one in the **AI Settings**
tab:

- **Google Gemini** (the “Nano Banana” image model, called directly). Get a key
  from [Google AI Studio](https://aistudio.google.com/) → *API keys*. Image
  generation usually requires **billing** enabled on the Google project (the free
  tier's image quota is often `0`).
- **nanobananaapi.ai** — a hosted service. Get a key from
  [nanobananaapi.ai](https://nanobananaapi.ai/api-key) → *API keys* (needs
  available credits). LinePassport submits the generation task, polls it to completion,
  and downloads the result, so a send may take a few seconds longer.

1. **Add your key** — open the **AI Settings** tab, choose the **Provider**, paste
   its API key, pick the image model, and click **Save**. For
   **nanobananaapi.ai**, LinePassport also shows an **Image size** dropdown and maps the
   selected model to the correct hosted endpoint (`generate`, `generate-2`, or
   `generate-pro`). Each provider's key is stored separately and locally under
   `--state-dir`, and is **never** sent back to the browser — the tab only shows a
   masked preview. The provider you save is the one the bot uses.
2. **Test it** — type a prompt and click **Generate image** to preview a result
   right there, confirming your key works.
3. **Use it in the bot** — in **Auto Bot → New scheduled message**, set
   **Content type** to **AI image (Nano Banana)** and enter your prompt. On each
   run the bot generates the image with your active provider and posts it.
   Placeholders like `{1D}`/`{date}` are expanded in the prompt on every send.

> Configuring the key requires the **manage_accounts** permission (admins);
> operators can still create AI-image schedules. AI images cannot be combined
> with E2EE (encrypted) sends — LinePassport blocks that combination up front so you
> never pay to generate an image that can't be delivered.

## Command-line flags

| Flag | Default | Meaning |
|------|---------|---------|
| `--host HOST` | `127.0.0.1` | Address to bind. Keep it as `127.0.0.1` unless you know you want the UI reachable from other machines. |
| `--port PORT` | `8765` | Port to bind (falls forward to a free port if busy; `0` picks any free port). |
| `--state-dir DIR` | `.okline` | Where the web UI keeps its state (accounts, schedules, saved sign-in, LINE sessions). |
| `--database-url URL` | *(none)* | Optional PostgreSQL connection URL for persistence — see below. |
| `--no-open` | *(off)* | Don't open the browser automatically (just print the URL). |

The global CLI options (`--tokens-file`, `--token`, `--refresh`,
`--show-secrets`) also apply — see [CLI](./cli.md).

```bash
okline web --port 9000 --no-open        # bind a different port, don't auto-open
okline web --host 0.0.0.0               # expose on your LAN (read the security note!)
okline web --state-dir ~/okline-web     # keep state somewhere specific
```

## Security

- **It binds to `127.0.0.1` by default**, so the UI is reachable only from your
  own machine. That is the intended, safe default.
- Passing `--host 0.0.0.0` (or any non-loopback address) exposes the UI — and,
  through it, full control of your LINE account — to your network. Only do this on
  a trusted network and behind your own authentication/reverse proxy. Anyone who
  can reach the page and knows the web sign-in can act as you on LINE.
- The web app's own sign-in and your LINE session data live under `--state-dir`
  (`.okline` by default). Treat that directory like a password store; it is not
  meant to be committed or shared.

## Optional PostgreSQL persistence

By default the web UI stores its state as **JSON files** under `--state-dir`,
which is all most people need. If you'd rather persist state in **PostgreSQL**
(e.g. to run the UI as a small always-on service), point it at a database:

```bash
pip install "okline[web]"                          # installs psycopg
okline web --database-url postgresql://user:pass@localhost/okline
```

You can also set the URL via the `OKLINE_DATABASE_URL` (or `DATABASE_URL`)
environment variable instead of the flag.

PostgreSQL support needs the optional `psycopg` driver, which is **not** a
required dependency of LinePassport — install it with the `web` extra as shown above.
If you pass `--database-url` without `psycopg` installed, LinePassport tells you exactly
what to install.

---

See also: [Getting started](./getting-started.md) · [Authentication](./authentication.md) ·
[CLI](./cli.md) · [Troubleshooting](./troubleshooting.md).
