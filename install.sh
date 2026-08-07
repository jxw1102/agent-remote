#!/usr/bin/env bash
# One-shot install of agentremoted on this machine (macOS or Linux).
#
#   curl -fsSL https://raw.githubusercontent.com/jxw1102/agent-remote/main/install.sh | bash
#   # or from a clone:
#   ./install.sh
#   ./install.sh --foreground   # run once in this terminal (no service)
#   ./install.sh --print-only   # write config/token, print how to connect, do not start
#
# Does NOT install Claude/Grok/Codex CLIs — log those in separately on this host.
set -euo pipefail

MODE="service"   # service | foreground | print-only
PORT="${PORT:-8473}"
PROVIDERS="${PROVIDERS:-}"  # empty = auto-detect from PATH
REPO_URL="${REPO_URL:-https://github.com/jxw1102/agent-remote.git}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --foreground) MODE="foreground"; shift ;;
    --print-only) MODE="print-only"; shift ;;
    --port) PORT="${2:?}"; shift 2 ;;
    --providers) PROVIDERS="${2:?}"; shift 2 ;;
    -h|--help)
      sed -n '2,12p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "unknown option: $1 (try --help)" >&2
      exit 2
      ;;
  esac
done

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

need python3
need tar
need curl

OS="$(uname -s)"
HOME_DIR="${HOME:-/root}"
AR_HOME="${AGENTREMOTED_HOME:-$HOME_DIR/.agentremoted}"
mkdir -p "$AR_HOME"
chmod 700 "$AR_HOME" 2>/dev/null || true

# Resolve install source: this script's repo, or a temp clone.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" 2>/dev/null && pwd || true)"
if [[ -n "$SCRIPT_DIR" && -d "$SCRIPT_DIR/daemon/agentremoted" ]]; then
  SRC="$SCRIPT_DIR"
  echo "== using local tree: $SRC =="
else
  need git
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/agent-remote.XXXXXX")"
  trap 'rm -rf "$TMP"' EXIT
  echo "== cloning $REPO_URL =="
  git clone --depth 1 "$REPO_URL" "$TMP/agent-remote"
  SRC="$TMP/agent-remote"
fi

INSTALL_ROOT="${AGENTREMOTE_INSTALL:-$HOME_DIR/.local/share/agent-remote}"
mkdir -p "$INSTALL_ROOT"
echo "== installing daemon code → $INSTALL_ROOT =="
rm -rf "$INSTALL_ROOT/daemon"
cp -a "$SRC/daemon" "$INSTALL_ROOT/daemon"
# Drop pycache noise if present
find "$INSTALL_ROOT/daemon" -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# Config: multi-provider, auto-detect CLIs on PATH.
python3 - <<PY
import json, os, pathlib, shutil
home = pathlib.Path(os.path.expanduser("$AR_HOME"))
home.mkdir(mode=0o700, exist_ok=True)
port = int("$PORT")
forced = [p.strip().lower() for p in "${PROVIDERS}".split(",") if p.strip()]

def has_bin(name, *extra):
    if shutil.which(name):
        return True
    for p in extra:
        if pathlib.Path(os.path.expanduser(p)).is_file():
            return True
    return False

if forced:
    providers = forced
else:
    providers = []
    if has_bin("claude"):
        providers.append("claude")
    if has_bin("grok", "~/.grok/bin/grok", "~/.local/bin/grok"):
        providers.append("grok")
    if has_bin("codex", "~/.local/bin/codex"):
        providers.append("codex")
    if not providers:
        providers = ["claude", "grok", "codex"]
        print("warn: no claude/grok/codex on PATH; wrote all three — install a CLI and restart")

cfg = {}
path = home / "config.json"
if path.is_file():
    try:
        cfg.update(json.loads(path.read_text()))
    except Exception as e:
        print("warn: could not read existing config:", e)
cfg["providers"] = providers
cfg.pop("provider", None)
cfg["port"] = port
cfg.setdefault("bind", "127.0.0.1")
path.write_text(json.dumps(cfg, indent=2) + "\n")
print("wrote", path)
print(json.dumps({"providers": providers, "port": port, "bind": cfg.get("bind")}, indent=2))
PY

export PYTHONPATH="$INSTALL_ROOT/daemon"
export AGENTREMOTED_HOME="$AR_HOME"
TOKEN="$(python3 -m agentremoted --print-token 2>/dev/null || true)"
if [[ -z "$TOKEN" && -f "$AR_HOME/token" ]]; then
  TOKEN="$(cat "$AR_HOME/token")"
fi

print_card() {
  echo
  echo "────────────────────────────────────────────────────────"
  echo "  Agent Remote daemon is ready on this machine"
  echo "────────────────────────────────────────────────────────"
  echo "  Base URL : http://127.0.0.1:$PORT"
  echo "  Token    : ${TOKEN:-see $AR_HOME/token}"
  echo "  Config   : $AR_HOME/config.json"
  echo "  Code     : $INSTALL_ROOT/daemon"
  echo
  echo "  Phone / another device:"
  echo "    1) On this machine:  $INSTALL_ROOT/daemon/scripts/tunnel.sh"
  echo "       (or: cloudflared tunnel --url http://127.0.0.1:$PORT)"
  echo "    2) Open the web client and Add a daemon with the HTTPS URL + token"
  echo "       https://nice-dune-0415af003.7.azurestaticapps.net/"
  echo
  echo "  LLM billing is NOT Agent Remote — log into claude / codex / grok"
  echo "  on this host first (Pro/Max or API key). See docs/billing-and-auth.md"
  echo "────────────────────────────────────────────────────────"
}

if [[ "$MODE" == "print-only" ]]; then
  print_card
  exit 0
fi

if [[ "$MODE" == "foreground" ]]; then
  print_card
  echo "== starting in foreground (Ctrl-C to stop) =="
  exec python3 -m agentremoted
fi

# Service install
if [[ "$OS" == "Darwin" ]]; then
  echo "== macOS launchd =="
  # install-launchd.sh resolves DAEMON_DIR from its own location.
  if ! (cd "$INSTALL_ROOT/daemon" && ./scripts/install-launchd.sh); then
    echo "launchd helper failed; falling back to foreground instructions" >&2
    print_card
    echo "Run:  cd $INSTALL_ROOT/daemon && PYTHONPATH=. python3 -m agentremoted"
    exit 1
  fi
  print_card
  echo "  log: $AR_HOME/daemon.log"
  exit 0
fi

if [[ "$OS" == "Linux" ]]; then
  echo "== Linux user systemd (no root required) =="
  UNIT_DIR="${XDG_CONFIG_HOME:-$HOME_DIR/.config}/systemd/user"
  mkdir -p "$UNIT_DIR"
  UNIT="$UNIT_DIR/agentremoted.service"
  cat > "$UNIT" <<EOF
[Unit]
Description=Agent Remote daemon (agentremoted)
After=network.target

[Service]
Type=simple
WorkingDirectory=$INSTALL_ROOT/daemon
Environment=PYTHONPATH=$INSTALL_ROOT/daemon
Environment=AGENTREMOTED_HOME=$AR_HOME
Environment=PATH=$HOME_DIR/.local/bin:$HOME_DIR/.grok/bin:/usr/local/bin:/usr/bin:/bin
ExecStart=$(command -v python3) -m agentremoted
Restart=on-failure
RestartSec=3
KillMode=process

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now agentremoted.service
  # Linger so the service survives logout (optional; may need passwordless)
  if command -v loginctl >/dev/null 2>&1; then
    loginctl enable-linger "$(id -un)" 2>/dev/null || true
  fi
  print_card
  echo "  status: systemctl --user status agentremoted"
  echo "  logs:   journalctl --user -u agentremoted -f"
  exit 0
fi

echo "Unsupported OS: $OS — start manually:" >&2
print_card
echo "  cd $INSTALL_ROOT/daemon && PYTHONPATH=. python3 -m agentremoted"
exit 1
