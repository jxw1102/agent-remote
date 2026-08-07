<p align="center">
  <img src="blackberry/variant/unified/icon.png" alt="Agent Remote logo" width="96">
</p>

<h1 align="center">Agent Remote</h1>

<p align="center">
  Control your AI coding sessions from anywhere.
</p>

<p align="center">
  <a href="https://github.com/jxw1102/agent-remote/releases"><img src="https://img.shields.io/github/v/release/jxw1102/agent-remote?display_name=tag&sort=semver" alt="Latest release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/jxw1102/agent-remote" alt="MIT license"></a>
  <a href="https://github.com/jxw1102/agent-remote/actions/workflows/release.yml"><img src="https://img.shields.io/github/actions/workflow/status/jxw1102/agent-remote/release.yml?label=release" alt="Release workflow status"></a>
</p>

Agent Remote is a self-hosted remote interface for AI coding agents. Run agents
on your own computer or server, then connect securely from a browser, phone, or
specialized device. Start sessions, send prompts, follow live progress, approve
actions, and manage work across multiple hosts from one place.

The project consists of a lightweight Python daemon and native clients. The
daemon fronts the agent command-line tools through a token-authenticated HTTP
API, with server-sent events and WebSockets available for live status.

## Why Agent Remote?

- Keep long-running coding sessions available when you leave your desk.
- Use one profile for a host and choose the agent when starting a session.
- Monitor active work and answer permission or user-question prompts remotely.
- Connect several hosts and see their sessions in one unified client view.
- Keep your code and agent state on infrastructure you control.

## Features

- Multi-host profiles and multi-agent sessions
- Live status, queues, stop controls, and event streams
- Permission Allow/Deny and `AskUserQuestion` support where available
- Attachments, slash commands, and host-to-device file drops
- Interactive and headless execution modes
- Capability discovery through `GET /api/ping`
- Web, Android, BlackBerry 10, and LILYGO T-LoRa Pager clients
- Python daemon with no third-party runtime dependencies

## Architecture

```text
┌──────────────────────────────────────────────┐
│              Agent Remote clients             │
│       Web · Android · BlackBerry · Pager       │
└──────────────────────┬───────────────────────┘
                       │ HTTP + JSON
                       │ token auth · SSE / WebSocket
┌──────────────────────▼───────────────────────┐
│                 agentremoted                   │
│     sessions · queue · permissions · status    │
└──────────────┬───────────────┬────────────────┘
               │               │
        Agent command      Agent session state
        line interfaces       on the host
```

The HTTP API is the source of truth for all clients. Provider-specific behavior
lives inside the daemon, while clients use the capabilities reported by
`/api/ping` to decide what to display and enable.

## Clients

| Client | Location | Intended use |
| --- | --- | --- |
| Web | [`web/`](web/) | Browser access with no build step |
| Android | [`android/`](android/) | Full mobile client with notifications and live status |
| BlackBerry 10 | [`blackberry/`](blackberry/) | Cascades app for BB10 devices |
| LILYGO T-LoRa Pager | [`esp32/`](esp32/) | Small-screen, keyboard-driven remote |

The hosted web client is available at
[nice-dune-0415af003.7.azurestaticapps.net](https://nice-dune-0415af003.7.azurestaticapps.net/).
It talks only to the daemons you configure; it does not host an agent daemon.

## Quick start

### Requirements

- Python 3
- At least one supported agent CLI on `PATH`
- Optional: Docker for building the BlackBerry 10 package

### 1. Configure the daemon

```bash
mkdir -p ~/.agentremoted
cp deploy/config.example.json ~/.agentremoted/config.json
# Edit providers and port as needed.
```

Use the multi-provider configuration shape even when only one provider is
enabled:

```json
{
  "providers": ["claude", "grok", "codex"],
  "port": 8473
}
```

The first start creates a token at `~/.agentremoted/token`.

### 2. Start the daemon

For a foreground run:

```bash
cd daemon
PYTHONPATH=. python3 -m agentremoted
```

Verify that it is responding:

```bash
curl -s http://127.0.0.1:8473/api/ping
```

On macOS, install the login service with `daemon/scripts/install-launchd.sh`.
On Linux, use [`deploy/deploy.sh`](deploy/deploy.sh) or the systemd unit in
[`deploy/`](deploy/). See the [daemon launch guide](daemon/README.md#launch-the-daemon)
for detailed instructions.

### 3. Connect a client

Add a profile using:

| Setting | Value |
| --- | --- |
| Base URL | `http://127.0.0.1:8473`, a LAN address, or a tunnel URL |
| Token | Contents of `~/.agentremoted/token` |

For the fastest setup, open the [hosted web client](https://nice-dune-0415af003.7.azurestaticapps.net/)
and choose **Add a daemon**.

## Remote access

Do not expose the daemon port directly to the public internet. For phone access,
the recommended option is a Cloudflare Tunnel:

```bash
cloudflared tunnel --url http://localhost:8473
```

Use the HTTPS URL printed by `cloudflared` as the client base URL. For a stable
address, configure a named tunnel and a domain you control. The daemon token
still authenticates every request.

## Security

- Every API request is authenticated with the daemon token.
- Keep the token private and rotate it if it is exposed.
- Prefer a private LAN or an HTTPS tunnel for remote connections.
- Review [SECURITY.md](SECURITY.md) before reporting a vulnerability.

## Development

The daemon uses only the Python standard library. Run its smoke and rendering
tests with:

```bash
cd daemon
python3 tests/smoke_test.py
python3 tests/render_test.py
```

Build the BlackBerry package with Docker:

```bash
cd blackberry
./build-bar-docker.sh
```

Build the Android client with JDK 17+ and Android SDK platform 36:

```bash
cd android
./gradlew assembleDebug
```

See the client-specific READMEs for platform setup and build details.

## Documentation

- [Daemon setup and API](daemon/README.md)
- [Android client](android/README.md)
- [ESP32 pager client](esp32/README.md)
- [Deployment guide](deploy/README.md)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Releases

APK, BAR, single-file web HTML, and packaged daemon releases are published on
[GitHub Releases](https://github.com/jxw1102/agent-remote/releases). Release
tags match the daemon version, such as `v2.4.5`.

## License

Agent Remote is released under the [MIT License](LICENSE).
