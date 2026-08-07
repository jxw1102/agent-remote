#!/usr/bin/env bash
# Deploy agentremoted to a remote host (multi-provider config).
#
#   ./deploy.sh user@host
#   KEY=~/.ssh/id_ed25519 PORT=8473 ./deploy.sh user@host
#   PROVIDERS=grok,codex PORT=2096 ./deploy.sh user@host
#
# Installs to /opt/daemon, keeps any existing agentremoted token,
# reuses TLS certs under ~/.agentremoted/tls when present, restarts the unit.
set -euo pipefail

HOST="${1:?usage: $0 user@host}"
KEY="${KEY:-$HOME/.ssh/id_rsa}"
PORT="${PORT:-8473}"
# Comma-separated; default multi list. Override e.g. PROVIDERS=grok on a VPS
# that only has the Grok CLI.
PROVIDERS="${PROVIDERS:-claude,grok,codex}"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SSH=(ssh -i "$KEY" -o ConnectTimeout=10 -o IdentitiesOnly=yes "$HOST")

echo "== copying daemon to $HOST =="
"${SSH[@]}" 'mkdir -p /opt/bb10-remote'
tar -C "$REPO_DIR" --exclude '__pycache__' --exclude '*.pyc' -czf - \
    daemon deploy/agentremoted.service deploy/config.example.json \
    | "${SSH[@]}" 'tar -C /opt/bb10-remote -xzf -'

echo "== configuring (port=$PORT providers=$PROVIDERS) =="
"${SSH[@]}" "export PORT=$PORT PROVIDERS=$PROVIDERS; bash -s" <<'REMOTE'
set -euo pipefail
PORT="${PORT:-8473}"
PROVIDERS="${PROVIDERS:-claude,grok,codex}"
mkdir -p /root/.agentremoted

if [ ! -s /root/.agentremoted/token ]; then
  head -c 32 /dev/urandom | base64 | tr -d '=+/' | cut -c1-43 > /root/.agentremoted/token
  chmod 600 /root/.agentremoted/token
fi

TLS_CERT=""
TLS_KEY=""
if [ -f /root/.agentremoted/tls/origin.crt ] && [ -f /root/.agentremoted/tls/origin.key ]; then
  TLS_CERT="/root/.agentremoted/tls/origin.crt"
  TLS_KEY="/root/.agentremoted/tls/origin.key"
fi

export PORT PROVIDERS TLS_CERT TLS_KEY
python3 <<'PY'
import json, os
from pathlib import Path

port = int(os.environ["PORT"])
providers = [p.strip().lower() for p in os.environ.get("PROVIDERS", "").split(",") if p.strip()]
if not providers:
    providers = ["claude", "grok", "codex"]
tls_cert = os.environ.get("TLS_CERT") or ""
tls_key = os.environ.get("TLS_KEY") or ""
cfg_path = Path("/root/.agentremoted/config.json")
cfg = {}
if cfg_path.is_file():
    try:
        cfg.update(json.loads(cfg_path.read_text()))
    except Exception as e:
        print("warn: skip", cfg_path, e)
cfg["providers"] = providers
cfg.pop("provider", None)
cfg["bind"] = cfg.get("bind") or "0.0.0.0"
cfg["port"] = port
if tls_cert and tls_key:
    cfg["tls_cert"] = tls_cert
    cfg["tls_key"] = tls_key
cfg.setdefault(
    "grok_prompt_flags",
    "--yolo --deny Bash(rm*) --deny Bash(sudo*) --deny Bash(*--force*)",
)
cfg.setdefault("turn_timeout", 1800)
cfg_path.write_text(json.dumps(cfg, indent=4) + "\n")
print("wrote", cfg_path)
print(json.dumps(
    {k: cfg.get(k) for k in ("providers", "port", "bind", "tls_cert", "tls_key")},
    indent=2,
))
PY

cp /opt/deploy/agentremoted.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable agentremoted
systemctl restart agentremoted
sleep 2
systemctl --no-pager --lines=20 status agentremoted || true
echo "== local ping =="
if [ -n "$TLS_CERT" ]; then
  curl -sk -m 5 "https://127.0.0.1:${PORT}/api/ping" && echo
else
  curl -s -m 5 "http://127.0.0.1:${PORT}/api/ping" && echo
fi
REMOTE

echo "== remote ping from this machine =="
BOX="${HOST#*@}"
if curl -sk -m 8 "https://${BOX}:${PORT}/api/ping"; then
  echo
elif curl -s -m 8 "http://${BOX}:${PORT}/api/ping"; then
  echo
else
  echo "WARN: could not reach ${BOX}:${PORT} from here (firewall/proxy?); check remote ping above"
fi
echo "OK — profile URL: https://${BOX}:${PORT}  (token: cat /root/.agentremoted/token on host)"
