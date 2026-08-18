# Security

## Model

`agentremoted` is a **trusted-host bridge**: anyone who has the bearer token can
list sessions, start turns, queue prompts, and (depending on harness mode)
approve tools or run agent actions as the daemon user.

Treat the token like a password.

## Token

- Stored at `$AGENTREMOTED_HOME/token` (default `~/.agentremoted/token`)
- Mode `600` when the daemon creates it
- Sent as `X-Auth-Token`, `Authorization: Bearer`, or `?token=`
- **Never** commit tokens or paste them into public issues

Rotate by replacing the file and restarting the daemon; update every client
profile.

## Session share links

`POST /api/sessions/<id>/share` (daemon token required) mints a random
token bound to that session. `GET /share/<token>` and
`GET /api/share/<token>` are public and **read-only**: they return that
session's transcript, nothing else.

- The share token is not the daemon token and is rejected as `X-Auth-Token`
- Tokens expire after 7 days
- Only the SHA-256 of the token is stored (`$AGENTREMOTED_HOME/shares.json`)
- Changing the URL to another session (or another token) returns no data

A share link is as public as the URL. Treat it like a document you emailed.

## Network

- Default examples bind `0.0.0.0` so phones on the LAN can connect. On a VPS,
  that exposes the port to the internet unless a firewall or reverse proxy
  restricts it.
- Prefer firewall allowlists, reverse proxy auth, or bind `127.0.0.1` when only
  a local tunnel/proxy should reach the daemon.
- BlackBerry 10 clients often need plain HTTP; use a modern TLS terminator in
  front when on the public internet (`tls_cert` / `tls_key` in config, or an
  external proxy).

## Host power

The daemon runs harness CLIs (**Claude / Grok / Codex**) as the service user.
Interactive and headless modes can execute tools with that user’s privileges.
Run it only on machines you control; do not expose an open token on a shared
host.

## Reporting

If you find a vulnerability in this project, open a private report with the
maintainer (or a GitHub security advisory if enabled) rather than a public
issue with exploit detail.
