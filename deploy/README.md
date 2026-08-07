# `deploy/` — install agentremoted on a host

Files used to **configure and run** the daemon (especially on Linux/VPS).
Mac day-to-day install is still `daemon/scripts/install-launchd.sh`; this
folder is the shared config example + remote/systemd pieces.

| File | Role |
|------|------|
| [`config.example.json`](config.example.json) | Multi-provider template → copy to `~/.agentremoted/config.json` (or `/root/.agentremoted/…`) |
| [`agentremoted.service`](agentremoted.service) | systemd unit (`WorkingDirectory=/opt/bb10-remote/daemon`) |
| [`deploy.sh`](deploy.sh) | One-shot: rsync tree over SSH, write multi config, enable unit |
| [`CONNECTION.txt`](CONNECTION.txt) | URL / token / curl cheatsheet for clients |

## When to use what

| Situation | Do this |
|-----------|---------|
| Non-technical / agent-assisted | Root [`../install.sh`](../install.sh) or [`../docs/AGENT_INSTALL.md`](../docs/AGENT_INSTALL.md) |
| Mac laptop | `../install.sh` or `cd daemon && ./scripts/install-launchd.sh` |
| Phone HTTPS | `../daemon/scripts/tunnel.sh` |
| First config anywhere | `cp deploy/config.example.json ~/.agentremoted/config.json` |
| Remote Linux / VPS | `./deploy/deploy.sh user@host` |
| Manual systemd | Copy unit + config (see root [README](../README.md)) |

`deploy.sh` defaults to port **8473** and `PROVIDERS=claude,grok,codex`.
Override as needed:

```bash
./deploy.sh user@host
KEY=~/.ssh/id_ed25519 PORT=2096 PROVIDERS=grok ./deploy.sh user@host
```

Full launch docs: [../README.md](../README.md), [../daemon/README.md](../daemon/README.md).
