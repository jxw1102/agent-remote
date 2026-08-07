# Agent Remote

Drive **Claude Code**, **Grok**, and **Codex** from a phone or browser.
Clients: **web**, **Android**, **BlackBerry 10**, **iOS**, and **LILYGO / T-Deck** firmware (`esp32/`).

One Python daemon (`agentremoted`) fronts the host CLIs; clients talk to it
over a token-authenticated HTTP API.

**One process, every harness** — config uses `"providers": ["claude", "grok",
"codex"]`. Clients add a single profile per host and pick the harness when
starting a session.

| Client | Location |
|--------|----------|
| BlackBerry 10 (Cascades) | `blackberry/` → `AgentRemote.bar` |
| Web (single HTML file) | `web/` — or the **hosted client** below |
| Android | `android/` |
| iOS (iPhone / iPad) | `ios/` → SwiftUI app + XcodeGen |

**Hosted web client** (no build step): open
[https://nice-dune-0415af003.7.azurestaticapps.net/](https://nice-dune-0415af003.7.azurestaticapps.net/),
then **Add a daemon** with your host Base URL and token. The page only talks
to the daemons you configure — it does not host `agentremoted` for you.

**Releases** (APK, BAR, single-file web HTML, and a packaged daemon tarball) are
published on GitHub when the daemon version changes — see
[github.com/jxw1102/agent-remote/releases](https://github.com/jxw1102/agent-remote/releases).
The release tag matches `agentremoted`’s `__version__` (for example `v2.4.5`).

```
[Agent Remote client: BB10 / Android / web]
        ↕  HTTP + JSON  (X-Auth-Token; SSE and/or WebSocket for live status)
[agentremoted — multi providers]
        ├─ claude  →  ~/.claude/projects/**  + `claude …`
        ├─ grok    →  ~/.grok/sessions/**    + `grok …`
        └─ codex   →  ~/.codex/**            + `codex …`
```

## Requirements

- **Python 3** (stdlib only for the daemon — no pip packages)
- Harness CLIs on `PATH` for the providers you list (`claude`, `grok`, `codex`)
- Optional: Docker (to build the BB10 `.bar` without a host NDK)

## Quick start — run the daemon

### 1. Config + token (first time only)

```bash
mkdir -p ~/.agentremoted
cp deploy/config.example.json ~/.agentremoted/config.json
# Edit "providers" / port if needed — leave the multi shape.
```

The first start creates `~/.agentremoted/token`. Print it anytime:

```bash
cd daemon
PYTHONPATH=. python3 -m agentremoted --print-token
```

### 2a. Foreground (any OS — good for trying it)

```bash
cd daemon
PYTHONPATH=. python3 -m agentremoted
```

```bash
curl -s http://127.0.0.1:8473/api/ping
```

### 2b. macOS — login service (recommended)

```bash
cd daemon
./scripts/install-launchd.sh          # install / update + start
# ./scripts/install-launchd.sh --remove
```

Writes a multi `providers` list for every harness found on `PATH`, starts at
login, restarts on crash.

| | |
|--|--|
| URL for clients | `http://127.0.0.1:8473` |
| Token | `~/.agentremoted/token` |
| Config | `~/.agentremoted/config.json` |
| Log | `~/.agentremoted/daemon.log` |

```bash
curl -s http://127.0.0.1:8473/api/ping
tail -f ~/.agentremoted/daemon.log
launchctl print "gui/$(id -u)/com.agentremoted"
```

### 2c. Linux — systemd

**Option A — one-shot deploy**

```bash
./deploy/deploy.sh user@your-host
# KEY=~/.ssh/id_ed25519 PORT=8473 PROVIDERS=claude,grok,codex ./deploy/deploy.sh user@host
# VPS with only Grok:  PROVIDERS=grok PORT=2096 ./deploy/deploy.sh user@host
```

Installs under `/opt/bb10-remote`, multi-shaped config under
`/root/.agentremoted/`, unit `agentremoted`.

**Option B — manual**

```bash
sudo mkdir -p /opt/bb10-remote
sudo cp -a daemon deploy /opt/

sudo mkdir -p /root/.agentremoted
sudo cp deploy/config.example.json /root/.agentremoted/config.json
# edit providers / port / tls_* as needed

sudo cp deploy/agentremoted.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now agentremoted
```

```bash
systemctl status agentremoted
journalctl -u agentremoted -f
sudo cat /root/.agentremoted/token
curl -s http://127.0.0.1:8473/api/ping
```

The unit uses `WorkingDirectory=/opt/daemon` and
`AGENTREMOTED_HOME=/root/.agentremoted`. For a non-root install, copy the unit
and change those paths (and `User=`).

More detail: [daemon/README.md](daemon/README.md).

## Connect a client

| Field | Example |
|-------|---------|
| Base URL | `http://127.0.0.1:8473` (LAN) or your Cloudflare Tunnel URL |
| Token | contents of `~/.agentremoted/token` on that host |

- **Web (easiest):** open the hosted client at
  [https://nice-dune-0415af003.7.azurestaticapps.net/](https://nice-dune-0415af003.7.azurestaticapps.net/)
  and add your daemon URL + token  
- **Web (local):** `cd web && python3 build.py && ./serve.sh`  
- **BlackBerry:** `cd blackberry && ./build-bar-docker.sh`, sideload, Settings → profile  
- **Android:** see [`android/`](android/)

`GET /api/ping` reports `multi`, `providers`, and capability flags.

See also [deploy/CONNECTION.txt](deploy/CONNECTION.txt).

### Reach it from your phone (recommended: Cloudflare Tunnel)

Do **not** open the daemon port on the public internet. Prefer a tunnel so
clients get HTTPS without firewall changes or a VPS.

[cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/install-and-setup/installation/)
quick tunnel (no Cloudflare account required for a temporary URL):

```bash
# match the port in config.json (default 8473)
cloudflared tunnel --url http://localhost:8473
```

If you run the daemon on another port (e.g. `8080`):

```bash
cloudflared tunnel --url http://localhost:8080
```

`cloudflared` prints a URL like `https://….trycloudflare.com` — paste that as
the client **Base URL**, and keep using the same host token. Stop the tunnel
when you are done; the hostname expires with the process.

For a stable hostname, create a named Cloudflare Tunnel and route a domain
you control. The token still authenticates every request.

## Layout

```

  daemon/           agentremoted (Python), providers, tests, launchd script
  web/              browser client (no npm) → one HTML file
  blackberry/       Cascades BB10 app → AgentRemote.bar
  android/          Android app
  deploy/           host install: systemd unit, multi config, deploy.sh
                    (see deploy/README.md)
  dist/             built .bars (gitignored)
```

## Build the BlackBerry app

```bash
cd blackberry
./build-bar-docker.sh          # → ../dist/AgentRemote.bar
```

Needs Docker with a BB NDK image (`delaya73/bbndk` or `accupara/bbndk`). On
Apple Silicon, if you see `exec format error`, turn **off** “Use Rosetta for
x86/amd64 emulation” in Docker Desktop.

## Networking

- **Phone over the internet:** Cloudflare Tunnel (above) is the recommended
  path — free HTTPS, no open ports, works with Android / web / BB10.
- **BB10:** prefer plain HTTP + shared token on LAN (old TLS stack). Through
  Cloudflare you get HTTPS termination at the edge, which BB10 clients handle
  better than a self-signed daemon cert. You can also put any reverse proxy in
  front, or set `tls_cert` / `tls_key` in config for an HTTPS origin.
- Live status: **`/sse/status`** (works through HTTPS proxies / tunnels) and
  **`/ws/status`** (plain `http://` base URLs; often blocked behind tunnels).

## App features (summary)

- Multi **profiles** (several hosts) and multi-**harness** (one process)
- Live status, queue, stop, attachments, slash commands
- Permission Allow/Deny and AskUserQuestion when the harness supports them
- Host→phone **drop** folder (`~/Public` on macOS by default)

## Tests

```bash
cd daemon
python3 tests/smoke_test.py
python3 tests/render_test.py
```

## Contributing / security / license

- [CONTRIBUTING.md](CONTRIBUTING.md)  
- [SECURITY.md](SECURITY.md)  
- [LICENSE](LICENSE) — MIT  
- [AGENTS.md](AGENTS.md) — multi-client parity for agents  
