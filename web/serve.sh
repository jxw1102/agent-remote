#!/usr/bin/env bash
# Serve the single-file web client on a stable local origin.
#
# Opening dist/agent-remote.html straight off disk works, but a file:// page
# has no stable origin, so browsers can be inconsistent about where its
# localStorage (your daemon profiles) lives. Serving it on a fixed port gives
# one durable origin — http://localhost:8787 — that you can bookmark and that
# keeps your profiles across restarts.
#
#   ./serve.sh            → http://localhost:8787/agent-remote.html
#   PORT=9000 ./serve.sh
set -euo pipefail

PORT="${PORT:-8787}"
DIR="$(cd "$(dirname "$0")" && pwd)/dist"

if [[ ! -f "$DIR/agent-remote.html" ]]; then
  echo "no build yet — running build.py first" >&2
  python3 "$(dirname "$0")/build.py"
fi

URL="http://localhost:$PORT/agent-remote.html"
echo "Agent Remote → $URL"
echo "(Ctrl+C to stop)"
command -v open >/dev/null 2>&1 && (sleep 1 && open "$URL" >/dev/null 2>&1 &) || true
exec python3 -m http.server "$PORT" --bind 127.0.0.1 --directory "$DIR"
