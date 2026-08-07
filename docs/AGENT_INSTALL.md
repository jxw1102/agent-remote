# Agent install brief — Agent Remote daemon

**Give this whole file to your coding agent** (Claude Code, Codex, Grok, Cursor, …) with a short instruction such as:

> Follow `docs/AGENT_INSTALL.md` and install the Agent Remote daemon on this machine. When done, print the Base URL and token so I can add them on my phone.

---

## Goal

Install **agentremoted** (the Agent Remote daemon) so this computer’s AI coding CLI sessions (Claude Code, Grok, Codex) can be controlled from a phone, browser, BlackBerry 10, or ESP32 pager.

The daemon does **not** sell LLM tokens. It runs the host’s existing CLIs. The human must already be able to run `claude`, `grok`, and/or `codex` interactively on this machine (subscription login **or** API key).

## Non-goals

- Do not open port 8473 on the public internet without a tunnel.
- Do not commit or paste the daemon token into public chats.
- Do not replace or reconfigure Anthropic/OpenAI billing.
- Do not require Docker unless packaging BlackBerry (not needed for the daemon).

## Prerequisites (check first)

1. `python3` on `PATH` (3.9+ is fine).
2. At least one of: `claude`, `grok`, `codex` on `PATH` (warn if missing; still install).
3. macOS or Linux. Windows: WSL2 or manual Python run is acceptable; document it.

## Preferred install (one command)

From a clone of this repo:

```bash
./install.sh
```

Or without cloning:

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
./install.sh --foreground
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
./daemon/scripts/tunnel.sh
# or:
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
  Remote:   run daemon/scripts/tunnel.sh for phone HTTPS URL
```

## Troubleshooting

| Symptom | Check |
|---------|--------|
| ping fails | `python3 -m agentremoted` stderr; port conflict; `AGENTREMOTED_HOME` |
| empty sessions | token wrong (ping is unauthenticated — always test with `/api/projects` + token); wrong host |
| Claude turns fail | `claude` on PATH for the **same user** as the daemon; login or API key |
| launchd no harnesses | PATH in plist must include Homebrew / `~/.local/bin` |
