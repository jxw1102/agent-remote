#!/usr/bin/env python3
"""Minimal MCP stdio server: Claude's headless permission prompt tool.

`claude -p` cannot show an interactive permission prompt, but it can be told
to call an MCP tool whenever a tool use needs approval
(`--permission-prompt-tool mcp__bb10__approve`). This module is that tool.

One instance is launched by claude per job (via `--mcp-config`, with the job
id / callback URL / nonce in the environment). When claude calls `approve`,
this server POSTs the request to the daemon and blocks until the phone
answers, then returns the daemon's decision in the shape Claude Code expects:

    {"behavior": "allow", "updatedInput": {...}}
    {"behavior": "deny",  "message": "..."}

Protocol: MCP stdio transport is newline-delimited JSON-RPC 2.0. Only the
handful of methods claude actually calls are implemented; anything else with
an id gets a "method not found" error, notifications are ignored.

Stdlib only — no dependency on the rest of agentremoted, so claude can launch it as
a plain script with the daemon's own interpreter.
"""

import json
import os
import sys
import urllib.error
import urllib.request

SERVER_NAME = "bb10"
TOOL_NAME = "approve"
PROTOCOL_VERSION = "2024-11-05"


def _send(msg):
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _ask_daemon(url, job_id, nonce, tool_name, tool_input, timeout):
    """Block until the phone answers; return the Claude-shaped decision."""
    payload = json.dumps({
        "job_id": job_id,
        "nonce": nonce,
        "tool_name": tool_name,
        "input": tool_input,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 — any failure must deny, never hang
        return {"behavior": "deny", "message": "permission bridge error: %s" % e}

    if data.get("allow"):
        return {"behavior": "allow", "updatedInput": tool_input}
    return {"behavior": "deny",
            "message": data.get("message") or "denied from phone"}


def main():
    url = os.environ.get("AGENTREMOTE_PERM_URL") or ""
    job_id = os.environ.get("AGENTREMOTE_JOB_ID") or ""
    nonce = os.environ.get("AGENTREMOTE_PERM_NONCE") or ""
    try:
        timeout = float(os.environ.get("AGENTREMOTE_PERM_TIMEOUT") or "300")
    except ValueError:
        timeout = 300.0
    # Give the daemon's long-poll a little slack over its own deadline.
    http_timeout = timeout + 15

    for raw in sys.stdin:
        raw = raw.strip()
        if not raw:
            continue
        try:
            req = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            continue

        method = req.get("method")
        req_id = req.get("id")

        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": req_id, "result": {
                "protocolVersion": (req.get("params") or {}).get(
                    "protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": "1.0.0"},
            }})
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": req_id, "result": {"tools": [{
                "name": TOOL_NAME,
                "description": "Ask the BlackBerry client to approve or deny "
                               "a tool use.",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "tool_name": {"type": "string"},
                        "input": {"type": "object"},
                    },
                },
            }]}})
        elif method == "tools/call":
            params = req.get("params") or {}
            args = params.get("arguments") or {}
            tool_name = args.get("tool_name", "")
            tool_input = args.get("input", {})
            if not isinstance(tool_input, dict):
                tool_input = {}
            decision = _ask_daemon(url, job_id, nonce, tool_name, tool_input,
                                   http_timeout)
            _send({"jsonrpc": "2.0", "id": req_id, "result": {
                "content": [{"type": "text", "text": json.dumps(decision)}],
            }})
        elif req_id is not None:
            # Unknown request (notifications have no id and are ignored).
            _send({"jsonrpc": "2.0", "id": req_id,
                   "error": {"code": -32601, "message": "method not found"}})

    return 0


if __name__ == "__main__":
    sys.exit(main())
