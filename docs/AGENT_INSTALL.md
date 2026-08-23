# Agent install brief — Agent Remote daemon

**Give this brief to your coding agent** (Claude Code, Codex, Grok, Cursor, …).
The human does **not** have a clone of this repo. Fetch this file from:

https://raw.githubusercontent.com/jxw1102/agent-remote/main/docs/AGENT_INSTALL.md

Instruction they were told to paste:

> Follow https://raw.githubusercontent.com/jxw1102/agent-remote/main/docs/AGENT_INSTALL.md and install the Agent Remote daemon on this machine. Print the Base URL and token when done.

---

## Goal

Install **agentremoted** (the Agent Remote daemon) so this computer’s AI coding CLI sessions (Claude Code, Grok, Codex, DeepSeek) can be controlled from a phone, browser, BlackBerry 10, or ESP32 pager.

The daemon does **not** sell LLM tokens. It runs the host’s existing CLIs. The human must already be able to run `claude`, `grok`, `codex`, and/or `dsh` (DeepSeek) interactively on this machine (subscription login **or** API key).

## Non-goals

- Do not open port 8473 on the public internet without a tunnel.
- Do not commit or paste the daemon token into public chats.
- Do not replace or reconfigure Anthropic/OpenAI billing.
- Do not require Docker unless packaging BlackBerry (not needed for the daemon).

## Prerequisites (check first)

1. `python3` on `PATH` (3.9+ is fine).
2. At least one of: `claude`, `grok`, `codex`, `dsh` on `PATH` (warn if missing; still install).
3. macOS or Linux. Windows: WSL2 or manual Python run is acceptable; document it.

## Preferred install (one command)

Do **not** clone the repo. Run:

```bash
curl -fsSL https://raw.githubusercontent.com/jxw1102/agent-remote/main/install.sh | bash
```

What success looks like:

- Process listening on `127.0.0.1:8473` (or configured `PORT`).
- `curl -s http://127.0.0.1:8473/api/ping` returns JSON with `"ok": true`, `"app": "agentremoted"`.
- Token file exists at `~/.agentremoted/token` (mode 0600).
- Config at `~/.agentremoted/config.json` uses `"providers": [...]` multi shape.

### Platform notes

| OS | What `install.sh` does |
|----|------------------------|
| macOS | Copies daemon under `~/.local/share/agent-remote`, runs `daemon/scripts/install-launchd.sh` |
| Linux | User systemd unit `agentremoted.service` (no root) |

Foreground (debug):

```bash
curl -fsSL https://raw.githubusercontent.com/jxw1102/agent-remote/main/install.sh | bash -s -- --foreground
```

## Manual fallback

```bash
mkdir -p ~/.agentremoted
cp deploy/config.example.json ~/.agentremoted/config.json
# Edit providers to match CLIs on PATH, e.g. ["claude","codex"]
cd daemon
PYTHONPATH=. python3 -m agentremoted
```

macOS service: `cd daemon && ./scripts/install-launchd.sh`  
Linux VPS as root: `./deploy/deploy.sh user@host` from a machine that has SSH.

## Verify harness login (LLM billing)

Auth is **per host CLI**, not Agent Remote:

```bash
curl -s http://127.0.0.1:8473/api/ping | python3 -m json.tool
```

Look at `auth` / `provider_details.*.auth`:

| status | Meaning | Fix |
|--------|---------|-----|
| `ok` | CLI + credentials look usable | — |
| `warning` | Partial (e.g. key set but binary missing) | Install CLI or fix PATH |
| `expired` | Subscription login needs refresh | Run `claude` / `codex` and `/login` on this host |
| `missing` | No login/API key | Log in or set API key |

**Claude:** Pro/Max login via `claude` is usually cheaper than `ANTHROPIC_API_KEY`. If both exist, the API key wins and bills pay-per-token.

## Phone access (remote)

Do not port-forward raw 8473 to the world.

```bash
# After daemon is healthy:
cloudflared tunnel --url http://127.0.0.1:8473
```

Then give the human:

| Field | Value |
|-------|--------|
| Base URL | `https://….trycloudflare.com` from cloudflared |
| Token | contents of `~/.agentremoted/token` |

Client options:

- Hosted web: https://nice-dune-0415af003.7.azurestaticapps.net/ → **Add a daemon**
- Android / iOS / BlackBerry 10 / LILYGO pager: add a profile with the same URL + token

Smoke path: [getting-started.md](getting-started.md)

## Multi-host

Repeat install on each machine (Mac, VPS, …). Each has its own URL + token. Clients add **one profile per host**; sessions merge into one list.

## Update (existing install)

If the human asked to **update** to a new release, do the same as first install.
Do **not** clone. Do **not** wipe `~/.agentremoted/token` or `config.json`.

```bash
curl -fsSL https://raw.githubusercontent.com/jxw1102/agent-remote/main/install.sh | bash
```

The script already:

- Overwrites daemon code under `~/.local/share/agent-remote`
- Merges (does not wipe) `~/.agentremoted/config.json`
- Leaves the existing token in place
- Restarts launchd (macOS) or user systemd (Linux)

Afterward, `GET /api/ping` `"version"` should match the latest
[release](https://github.com/jxw1102/agent-remote/releases). Print URL,
token, and the **new version** for the human.

Native apps (Android / iOS / BlackBerry) are separate: the human installs a
new APK / BAR from Releases. The hosted web client needs no update.

## Completion report (print for the human)

When finished, print exactly:

```text
Agent Remote install complete
  Host:     <hostname>
  Base URL: http://127.0.0.1:8473
  Token:    <token>
  Ping:     ok / version <x>
  Providers: <list from /api/ping>
  Auth:     <summary from /api/ping auth field>
  Service:  launchd | systemd user | foreground
  Remote:   cloudflared tunnel --url http://127.0.0.1:8473 for phone HTTPS URL
```

## Troubleshooting

| Symptom | Check |
|---------|--------|
| ping fails | `python3 -m agentremoted` stderr; port conflict; `AGENTREMOTED_HOME` |
| empty sessions | token wrong (ping is unauthenticated — always test with `/api/projects` + token); wrong host |
| Claude turns fail | `claude` on PATH for the **same user** as the daemon; login or API key |
| launchd no harnesses | PATH in plist must include Homebrew / `~/.local/bin` |
