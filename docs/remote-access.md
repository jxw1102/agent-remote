# Remote access

How clients reach `agentremoted` when they are not on localhost.

## Rules of thumb

1. **Never** put an unauthenticated agent daemon on a public IP.
2. Prefer **HTTPS tunnel** or **private mesh** (Tailscale) over raw TCP.
3. The **daemon token** authenticates clients even behind a tunnel.
4. LLM logins stay on the host; the tunnel only carries the Agent Remote API.

## Path A — Cloudflare quick tunnel (fastest phone demo)

```bash
# daemon already healthy on :8473
./daemon/scripts/tunnel.sh
```

Requires [cloudflared](https://developers.cloudflare.com/tunnel/downloads/):

```bash
# macOS
brew install cloudflared

# Linux amd64 binary
curl -L --output cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x cloudflared && sudo mv cloudflared /usr/local/bin/
```

Full OS matrix (Windows MSI, arm64, deb/rpm, Docker):  
https://developers.cloudflare.com/tunnel/downloads/

- **Pro:** zero DNS, works from café Wi‑Fi  
- **Con:** URL changes each run; session is temporary  

## Path B — Named Cloudflare tunnel (stable URL)

Configure a named tunnel and hostname you control, origin
`http://127.0.0.1:8473`. Use that HTTPS origin as the client Base URL.

## Path C — Tailscale / private VPN

1. Install Tailscale on laptop and phone.  
2. Bind daemon to all interfaces if needed: `"bind": "0.0.0.0"` in
   `~/.agentremoted/config.json`.  
3. Base URL: `http://100.x.y.z:8473` (or MagicDNS name).  
4. Token still required.

## Path D — Same LAN only

Base URL `http://192.168.x.y:8473`. Suitable for home lab; not for untrusted
networks. Cleartext HTTP is allowed by the Android client for this reason.

## Client fields

| Field | Example |
|-------|---------|
| Base URL | `https://abc.trycloudflare.com` |
| Token | `cat ~/.agentremoted/token` |

Hosted web client: https://nice-dune-0415af003.7.azurestaticapps.net/

## Smoke checklist

See [getting-started.md](getting-started.md).

## Troubleshooting

| Symptom | Likely cause |
|---------|----------------|
| Tunnel up, client 401 | Wrong token |
| Tunnel up, empty list | Auth OK but no sessions yet, or filtered project |
| Ping works in browser, app fails | Mixed content / wrong scheme; use HTTPS tunnel URL as-is |
| Works on Wi‑Fi, fails on LTE | Using LAN IP — switch to tunnel or Tailscale |
