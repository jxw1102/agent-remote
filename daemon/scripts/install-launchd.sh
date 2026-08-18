#!/bin/bash
# Install agentremoted as a launchd user agent on macOS so it starts at login
# and restarts if it crashes. Multi-provider by default (Claude + Grok + Codex
# when available) — one process, one client profile.
#
#   ./install-launchd.sh            install / update and start
#   ./install-launchd.sh --remove   stop and uninstall
set -euo pipefail

LABEL="com.agentremoted"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
DAEMON_DIR="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$HOME/.agentremoted"
PYTHON3="$(command -v python3)"

MODE="install"
case "${1:-}" in
  --remove)
    MODE="remove"
    ;;
  --multi|"")
    MODE="install"
    ;;
  *)
    echo "usage: $0 [--remove]" >&2
    exit 2
    ;;
esac

bootout_label() {
  local label="$1"
  local plist="$HOME/Library/LaunchAgents/$label.plist"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootout "gui/$(id -u)" "$plist" 2>/dev/null || true
}

if [[ "$MODE" == "remove" ]]; then
  bootout_label "$LABEL"
  rm -f "$PLIST"
  echo "agentremoted launch agent removed."
  exit 0
fi

mkdir -p "$LOG_DIR" "$HOME/Library/LaunchAgents"

# Seed / refresh multi config: providers for every harness binary on PATH.
python3 - <<PY
import json, os, pathlib, shutil
home = pathlib.Path(os.path.expanduser("$LOG_DIR"))
home.mkdir(mode=0o700, exist_ok=True)
cfg = {}
existing = home / "config.json"
if existing.is_file():
    try:
        cfg.update(json.loads(existing.read_text()))
    except Exception as e:
        print("warn: skip", existing, e)

def has_bin(name, *extra):
    if shutil.which(name):
        return True
    for p in extra:
        if pathlib.Path(os.path.expanduser(p)).is_file():
            return True
    return False

providers = []
if has_bin("claude"):
    providers.append("claude")
if has_bin("grok", "~/.grok/bin/grok", "~/.local/bin/grok"):
    providers.append("grok")
if has_bin("codex", "~/.local/bin/codex"):
    providers.append("codex")
# DeepSeek Harness: official UI is `dsh web` on loopback (daemon starts it).
if has_bin("dsh") or pathlib.Path(os.path.expanduser("~/.dsh")).is_dir():
    providers.append("deepseek")
if not providers:
    # Nothing on PATH yet — still write a multi-shaped config so adding a
    # CLI later only needs a restart, not a re-shape of the file.
    providers = ["claude", "grok", "codex"]
    print("warn: no claude/grok/codex on PATH; wrote all three in config")

cfg["providers"] = providers
cfg.pop("provider", None)
cfg["port"] = 8473
cfg.setdefault("bind", "0.0.0.0")
out = home / "config.json"
out.write_text(json.dumps(cfg, indent=2) + "\n")
print("wrote", out)
print(json.dumps({"providers": cfg["providers"], "port": cfg.get("port")}, indent=2))
PY

# PATH must include harness bins.
PATH_VAL="$HOME/.local/bin:$HOME/.grok/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin"

cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>$LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON3</string>
        <string>-m</string>
        <string>agentremoted</string>
    </array>
    <key>WorkingDirectory</key>
    <string>$DAEMON_DIR</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONPATH</key>
        <string>$DAEMON_DIR</string>
        <key>AGENTREMOTED_HOME</key>
        <string>$LOG_DIR</string>
        <key>PATH</key>
        <string>$PATH_VAL</string>
        <key>HOME</key>
        <string>$HOME</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$LOG_DIR/daemon.log</string>
    <key>StandardErrorPath</key>
    <string>$LOG_DIR/daemon.log</string>
</dict>
</plist>
EOF

bootout_label "$LABEL"
launchctl bootstrap "gui/$(id -u)" "$PLIST"
if ! launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null || true
fi

TOKEN="$(PYTHONPATH="$DAEMON_DIR" AGENTREMOTED_HOME="$LOG_DIR" "$PYTHON3" -m agentremoted --print-token 2>/dev/null || true)"

echo "agentremoted installed and started (multi)."
echo "  log:    $LOG_DIR/daemon.log"
echo "  token:  ${TOKEN:-see $LOG_DIR/token}"
echo "  config: $LOG_DIR/config.json"
echo
echo "Client: add ONE profile →  http://127.0.0.1:8473"
echo "  token:  $LOG_DIR/token"
echo "  New session will ask which harness (Claude / Grok / Codex)."
echo
echo "Catalogue:  curl -s http://127.0.0.1:8473/api/ping"
