#!/bin/bash
# Forward the Claude Code hook payload (stdin JSON) to the agentremoted daemon.
#
# The daemon injects AGENTREMOTE_HOOK_URL (including its auth secret) into the
# environment of the TUIs it spawns. Without it — e.g. someone loads this
# plugin into a manual session — the hook is a silent no-op. Always exit 0:
# a dead daemon must never block or annotate the user's turn.
[ -n "${AGENTREMOTE_HOOK_URL:-}" ] || exit 0
curl -s -m 5 -X POST "$AGENTREMOTE_HOOK_URL" \
    -H 'Content-Type: application/json' \
    --data-binary @- >/dev/null 2>&1 || true
exit 0
