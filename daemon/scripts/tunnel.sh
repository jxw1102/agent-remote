#!/usr/bin/env bash
# Expose agentremoted to your phone via Cloudflare Tunnel (HTTPS).
#
#   ./tunnel.sh              # quick temporary URL
#   ./tunnel.sh --port 8473
#   ./tunnel.sh --print-card # print Base URL / token after tunnel is up
#
# Do NOT open the daemon port on the public internet without a tunnel or VPN.
# The daemon token still authenticates every client request.
set -euo pipefail

PORT="${PORT:-8473}"
PRINT_CARD=1

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:?}"; shift 2 ;;
    --no-card) PRINT_CARD=0; shift ;;
    -h|--help)
      sed -n '2,10p' "$0" | sed 's/^# \?//'
      exit 0
      ;;
    *)
      echo "unknown option: $1" >&2
      exit 2
      ;;
  esac
done

AR_HOME="${AGENTREMOTED_HOME:-$HOME/.agentremoted}"
TOKEN_FILE="$AR_HOME/token"
LOCAL="http://127.0.0.1:${PORT}"

if ! curl -fsS --max-time 2 "$LOCAL/api/ping" >/dev/null 2>&1; then
  echo "daemon does not answer at $LOCAL/api/ping" >&2
  echo "Start it first:" >&2
  echo "  macOS:  cd daemon && ./scripts/install-launchd.sh" >&2
  echo "  any:    cd daemon && PYTHONPATH=. python3 -m agentremoted" >&2
  echo "  one-shot: ./install.sh" >&2
  exit 1
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared is not installed." >&2
  echo
  echo "Install cloudflared (https://developers.cloudflare.com/tunnel/downloads/):" >&2
  echo >&2
  echo "  macOS (Homebrew):" >&2
  echo "    brew install cloudflared" >&2
  echo >&2
  echo "  Linux (binary, amd64 example):" >&2
  echo "    curl -L --output cloudflared \\" >&2
  echo "      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64" >&2
  echo "    chmod +x cloudflared && sudo mv cloudflared /usr/local/bin/" >&2
  echo "    # other arch: cloudflared-linux-arm64 | -arm | -386  (.deb / .rpm also available)" >&2
  echo >&2
  echo "  Windows: download the .exe or .msi from" >&2
  echo "    https://github.com/cloudflare/cloudflared/releases/latest" >&2
  echo >&2
  echo "  Docker:  docker pull cloudflare/cloudflared" >&2
  echo "  Full matrix: https://developers.cloudflare.com/tunnel/downloads/" >&2
  echo >&2
  echo "Or use Tailscale Serve / a VPN and point the client at the LAN URL." >&2
  exit 1
fi

TOKEN=""
if [[ -f "$TOKEN_FILE" ]]; then
  TOKEN="$(tr -d '[:space:]' < "$TOKEN_FILE")"
fi

echo "Starting Cloudflare quick tunnel → $LOCAL"
echo "Leave this terminal open. Ctrl-C stops the tunnel."
echo

if [[ "$PRINT_CARD" -eq 1 ]]; then
  echo "When cloudflared prints an https://….trycloudflare.com URL, use:"
  echo "  Base URL : <that https URL>"
  echo "  Token    : ${TOKEN:-$TOKEN_FILE}"
  echo
  echo "Web client: https://nice-dune-0415af003.7.azurestaticapps.net/ → Add a daemon"
  echo "Android / iOS / BlackBerry / pager: same Base URL + token as a profile"
  echo
fi

# cloudflared prints the public URL on stderr/stdout; we do not parse it so
# the user always sees the official output.
exec cloudflared tunnel --url "$LOCAL"
