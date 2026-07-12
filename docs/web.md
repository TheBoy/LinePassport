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
  generate images from a text prompt. See below.

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
