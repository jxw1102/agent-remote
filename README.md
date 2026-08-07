<h1 align="center">
  <img src="blackberry/variant/unified/icon.png" alt="Agent Remote logo" width="36" height="36" align="absmiddle">
  Agent Remote
</h1>

<p align="center">
  Control your AI sessions from anywhere.<br>
  <strong>One list across every machine.</strong> Self-hosted. No token resale.
</p>

<p align="center">
  <a href="https://github.com/jxw1102/agent-remote/releases"><img src="https://img.shields.io/github/v/release/jxw1102/agent-remote?display_name=tag&sort=semver" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/jxw1102/agent-remote" alt="MIT license"></a>
  <a href="https://github.com/jxw1102/agent-remote/actions/workflows/release.yml"><img src="https://img.shields.io/github/actions/workflow/status/jxw1102/agent-remote/release.yml?label=release" alt="Release workflow status"></a>
</p>

<p align="center">
  <img src="docs/cover.jpg" alt="Agent Remote — control your AI sessions from anywhere" width="100%">
</p>

Agent Remote is a **self-hosted control plane** for AI coding agents. A small
Python daemon on your Mac or VPS fronts the CLIs you already use — Claude Code,
Grok, Codex — over a token-authenticated HTTP API. Clients (web, phone,
BlackBerry 10, hardware pager) merge sessions from every host into **one place**:
start turns, watch live status, approve permissions, answer questions, queue
prompts, and stop work without sitting at the desk.

```text
  Phone / web / BB10 / pager
            │  profiles (Mac, VPS, …)
            ▼
     ┌──────────────┐     ┌──────────────┐
     │ agentremoted │     │ agentremoted │
     │   Mac · Max  │     │  VPS · Grok  │
     └──────┬───────┘     └──────┬───────┘
            │                    │
       claude / codex          grok
```

## Why Agent Remote?

- **Multi-host session home** — several daemons, one client list sorted by activity.
- **Keeps subscription economics** — runs official CLIs; Pro/Max and ChatGPT logins stay on the host (API keys work too).
- **Agent-aware remote** — permissions, AskUserQuestion, queue, stop, live TUI, rewind — not a dumb terminal proxy.
- **Ultra-light daemon** — Python standard library only; launchd / systemd / one-shot install.
- **Unusual clients** — BlackBerry 10 Cascades and LILYGO T-LoRa Pager alongside web, Android, and iOS.

## Features

- Multi-host profiles and multi-agent sessions (Claude · Grok · Codex)
- Live status (SSE / WebSocket), queues, stop, capability discovery (`GET /api/ping`)
- Auth / login health on `/api/ping` (daemon ≥ 2.5.3)
- Permission Allow/Deny and `AskUserQuestion` where the harness supports them
- Attachments, slash commands, host→device file drop, session rewind
- Interactive and headless execution modes

## Billing (important)

Agent Remote **does not** bill model usage and **does not** require its own
Anthropic/OpenAI account.

| You already pay | What Agent Remote uses |
|-----------------|------------------------|
| Claude Pro/Max (or API key) | Host `claude` CLI login / env |
| ChatGPT / Codex (or API key) | Host `codex` CLI |
| xAI / Grok | Host `grok` CLI |
| Nothing extra for AR | Only a local daemon **token** for clients |

Log into each harness **on the host** once. Clients only store the daemon URL +
token. If both a Claude subscription and `ANTHROPIC_API_KEY` are present, the
CLI bills the **API key** — unset the key to stay on Max. Full notes:
[docs/billing-and-auth.md](docs/billing-and-auth.md).

## Clients

| Client | Location | Intended use |
| --- | --- | --- |
| Web | [`web/`](web/) | Browser; no build step |
| Android | [`android/`](android/) | Full mobile client + notifications |
| iOS | community / releases | Native remote (contributor) |
| BlackBerry 10 | [`blackberry/`](blackberry/) | Cascades app for BB10 devices |
| LILYGO T-LoRa Pager | [`esp32/`](esp32/) | Small-screen, keyboard-driven remote |

Hosted web client (talks only to **your** daemons):
[nice-dune-0415af003.7.azurestaticapps.net](https://nice-dune-0415af003.7.azurestaticapps.net/).

## Quick start

### Easiest: one script

```bash
./install.sh
# or: curl -fsSL https://raw.githubusercontent.com/jxw1102/agent-remote/main/install.sh | bash
```

Prints Base URL + token. macOS → launchd; Linux → user systemd.

### Non-technical: hand this to your coding agent

Open [docs/AGENT_INSTALL.md](docs/AGENT_INSTALL.md) and tell Claude/Codex/Grok:

> Follow this brief and install the Agent Remote daemon. Print URL and token when done.

### Manual

```bash
mkdir -p ~/.agentremoted
cp deploy/config.example.json ~/.agentremoted/config.json
# providers: ["claude","grok","codex"] — trim to CLIs on PATH
cd daemon && PYTHONPATH=. python3 -m agentremoted
curl -s http://127.0.0.1:8473/api/ping
```

macOS service: `daemon/scripts/install-launchd.sh`  
Linux VPS: [`deploy/deploy.sh`](deploy/deploy.sh)  
Details: [daemon/README.md](daemon/README.md)

### Connect a client

| Setting | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:8473`, LAN, Tailscale, or tunnel HTTPS |
| Token | `~/.agentremoted/token` |

## Remote access (phone)

Do **not** expose the daemon port on the public internet.

```bash
./daemon/scripts/tunnel.sh
# same as: cloudflared tunnel --url http://localhost:8473
```

Use the printed `https://….trycloudflare.com` URL as the client Base URL. The
daemon token still authenticates every request. Stable hostname: named
Cloudflare tunnel or Tailscale. Step-by-step smoke path:
[docs/getting-started.md](docs/getting-started.md).

## Security

- Every API request (except unauthenticated `/api/ping`) needs the daemon token.
- Keep the token private; rotate if exposed.
- Prefer private LAN, Tailscale, or HTTPS tunnel.
- [SECURITY.md](SECURITY.md)

## Development

```bash
cd daemon
python3 tests/smoke_test.py
python3 tests/render_test.py
```

BlackBerry BAR: `cd blackberry && ./build-bar-docker.sh`  
Android: `cd android && ./gradlew assembleDebug` (JDK 17+, platform 36)

## Documentation

- [Getting started (laptop → phone)](docs/getting-started.md)
- [Agent-facing install brief](docs/AGENT_INSTALL.md)
- [Billing and auth](docs/billing-and-auth.md)
- [Remote access notes](docs/remote-access.md)
- [Daemon setup and API](daemon/README.md)
- [Android](android/README.md) · [ESP32](esp32/README.md) · [Deploy](deploy/README.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md)

## Releases

APK, BAR, single-file web HTML, and packaged daemon builds:
[GitHub Releases](https://github.com/jxw1102/agent-remote/releases). Tags match
the daemon version (e.g. `v2.5.3`).

## License

Agent Remote is released under the [MIT License](LICENSE).
