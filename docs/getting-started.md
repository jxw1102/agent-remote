# Getting started — laptop to phone in one sitting

Smoke path for a **fresh machine → working phone client**.

## 0. Prerequisites on the laptop/server

- Python 3
- At least one agent CLI installed and working in a normal terminal:
  - `claude` (Claude Code — Pro/Max login **or** API key)
  - and/or `codex`
  - and/or `grok`
- Optional: `cloudflared` for HTTPS to the phone
  ([downloads](https://developers.cloudflare.com/tunnel/downloads/) —
  macOS: `brew install cloudflared`)

Confirm the CLI alone:

```bash
claude   # or: codex / grok
# complete login if prompted, then quit
```

## 1. Install the daemon (easiest)

**Option A — one script**

```bash
curl -fsSL https://raw.githubusercontent.com/jxw1102/agent-remote/main/install.sh | bash
```

**Option B — ask your coding agent**

Paste this (no clone needed):

```text
Follow https://raw.githubusercontent.com/jxw1102/agent-remote/main/docs/AGENT_INSTALL.md
and install the Agent Remote daemon on this machine.
Print the Base URL and token when done.
```

Brief: https://github.com/jxw1102/agent-remote/blob/main/docs/AGENT_INSTALL.md

**Option C — manual**

```bash
mkdir -p ~/.agentremoted
cp deploy/config.example.json ~/.agentremoted/config.json
cd daemon && PYTHONPATH=. python3 -m agentremoted
```

macOS background service:

```bash
cd daemon && ./scripts/install-launchd.sh
```

## 2. Verify on the laptop

```bash
curl -s http://127.0.0.1:8473/api/ping | python3 -m json.tool
cat ~/.agentremoted/token
```

You want `"ok": true`, `"app": "agentremoted"`, and an `auth` block that is not all `missing` for the CLIs you use.

## 3. Reach the phone (HTTPS tunnel)

Leave the daemon running. In a second terminal:

```bash
./daemon/scripts/tunnel.sh
# same as: cloudflared tunnel --url http://127.0.0.1:8473
```

Copy the `https://….trycloudflare.com` URL cloudflared prints.

| Client field | Value |
|--------------|--------|
| Base URL | that `https://…` URL |
| Token | contents of `~/.agentremoted/token` |

**Do not** expose port 8473 on a public IP without a tunnel or private mesh (Tailscale, etc.).

### Alternatives to Cloudflare quick tunnel

- **Named Cloudflare tunnel** + your domain (stable URL)
- **Tailscale** / private VPN: Base URL = `http://100.x.y.z:8473` (token still required)
- **Same Wi‑Fi only**: `http://<laptop-lan-ip>:8473` — bind must allow LAN (`"bind": "0.0.0.0"` in config); still prefer HTTPS when possible

## 4. Connect a client

| Client | How |
|--------|-----|
| **Web** | Open [hosted web client](https://nice-dune-0415af003.7.azurestaticapps.net/) → **Add a daemon** |
| **Android** | Profiles → + → Base URL + token → Test connection |
| **iOS** | Same profile fields (community client) |
| **BlackBerry 10** | Install BAR from releases; Settings → daemon URL + token |
| **LILYGO T-LoRa Pager** | See [esp32/README.md](../esp32/README.md) |

Test connection should show host name, version, harness list, and (daemon ≥ 2.5.3) auth summary.

## 5. Start a session from the phone

1. **New session**
2. Pick **which daemon** (if you have more than one profile)
3. Pick **provider** if the host runs multi (Claude / Grok / Codex)
4. Project/cwd if required, then send a prompt

You should see live status, permissions / questions when the harness asks, and the same sessions that appear in the host CLI history.

## 6. Second machine (optional)

Install the daemon on a VPS the same way (the curl one-liner, or `./deploy/deploy.sh user@vps` if you already have the repo). Add a **second profile** on the phone. Both hosts’ sessions appear in one list.

## 7. Update when a new version ships

Releases: https://github.com/jxw1102/agent-remote/releases

**Daemon** — same command as install, on every machine that runs it. No clone.

```bash
curl -fsSL https://raw.githubusercontent.com/jxw1102/agent-remote/main/install.sh | bash
```

Your token and `~/.agentremoted/config.json` stay put. The script replaces
the daemon code and restarts launchd / systemd.

Check the version:

```bash
curl -s http://127.0.0.1:8473/api/ping | python3 -m json.tool
```

Look at `"version"`. Or open a client and Test connection.

| Piece | How to update |
| --- | --- |
| Daemon | Re-run the curl above (every host) |
| Hosted web client | Nothing — it is already the latest |
| Android / iOS / BlackBerry | Install the new APK / BAR from [Releases](https://github.com/jxw1102/agent-remote/releases) |

Or paste this to your coding agent:

```text
Follow https://raw.githubusercontent.com/jxw1102/agent-remote/main/docs/AGENT_INSTALL.md
and update the Agent Remote daemon on this machine to the latest release.
Keep the existing token and config. Print the Base URL, token, and new version when done.
```

## Billing reminder

| You pay | What for |
|---------|----------|
| Claude Pro/Max, ChatGPT, xAI, or API keys | Model usage — via the CLIs on each host |
| Nothing to Agent Remote | Self-hosted daemon + clients |

If Claude turns suddenly cost API rates, check for `ANTHROPIC_API_KEY` overriding a Max login. Details: [billing-and-auth.md](billing-and-auth.md).

## Checklist

- [ ] CLI works alone on the host  
- [ ] `curl …/api/ping` → ok  
- [ ] Token known  
- [ ] Tunnel URL (or LAN/Tailscale) on the phone  
- [ ] Test connection succeeds  
- [ ] New session + one prompt returns  
- [ ] Later: same curl updates the daemon; native apps from Releases  
