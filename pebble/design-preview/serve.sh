#!/usr/bin/env bash
# Local presentation of the Pebble Time 2 client options.
#   ./serve.sh            → http://127.0.0.1:8765/
#   PORT=9000 ./serve.sh
set -euo pipefail

PORT="${PORT:-8765}"
DIR="$(cd "$(dirname "$0")" && pwd)"
URL="http://127.0.0.1:${PORT}/"

echo "Pebble Time 2 options → $URL"
echo "(Ctrl+C to stop)"
command -v open >/dev/null 2>&1 && (sleep 1 && open "$URL" >/dev/null 2>&1 &) || true
exec python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$DIR"
