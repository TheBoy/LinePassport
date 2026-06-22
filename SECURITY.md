# Security Policy

## Handling credentials

OkLine works with **live LINE account credentials** (access tokens, refresh
tokens, device certificates, E2EE key material). Treat them like passwords:

- **Never** commit `tokens.json`, `.har`/`.txt` session logs, or pasted
  transcripts containing tokens. They are in [`.gitignore`](.gitignore).
- OkLine **redacts** secrets in recorded output by default; only use
  `redact=False` / `--show-secrets` when you understand the risk.
- If a token leaks, revoke it: `api.logout_v2()`, or remove the device in the
  LINE app → **Settings → Account → logged-in devices**.

## Reporting a vulnerability

If you find a security issue in OkLine itself (not in LINE's services), please
**do not open a public issue**. Instead, open a [private security advisory] on
GitHub, or contact the maintainers privately. We aim to respond within a few
days.

[private security advisory]: https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability

## Scope & responsible use

OkLine is an **unofficial, independent** project for interoperability, research
and use with **your own account**, in compliance with LINE's Terms of Service.
Please do not use or contribute to it for spam, scraping other people's data,
account takeover, or any abusive purpose. Vulnerability reports about LINE's own
infrastructure should go to LINE Corporation, not here.
