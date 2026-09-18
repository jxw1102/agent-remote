"""Cursor Agent provider against a fake `cursor-agent` binary.

Fixture chats + agent-transcripts + a script that emits real stream-json
(init / assistant / tool_call / result).

Run:  python3 tests/cursor_test.py
"""

import json
import hashlib
import os
import shutil
import sqlite3
import stat
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-cursor-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.config import Config, load_or_create_token  # noqa: E402
from agentremoted.jobs import Job, JobManager                 # noqa: E402
from agentremoted.server import make_server                   # noqa: E402
from agentremoted import providers                            # noqa: E402
from agentremoted.providers.cursor_interactive import (       # noqa: E402
    CursorInteractiveManager, _Tui, _pane_ready, tmux_available,
)

SESSION_ID = "11111111-2222-3333-4444-555555555555"
NEW_SID = "99999999-8888-7777-6666-555555555555"
PROJECT_CWD = os.path.join(FAKE_HOME, "myapp")
CURSOR_HOME = os.path.join(FAKE_HOME, ".cursor")

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


def api(base, token, path, body=None):
    req = urllib.request.Request(base + path, headers={"X-Auth-Token": token})
    if body is not None:
        req.data = json.dumps(body).encode()
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, json.loads(resp.read().decode())


def wait_job(base, token, job_id, want=("done", "error", "stopped"), timeout=15):
    snap = {}
    deadline = time.time() + timeout
    while time.time() < deadline:
        _, snap = api(base, token, "/api/jobs/" + job_id)
        if snap["status"] in want:
            break
        time.sleep(0.2)
    return snap


def write_script(path, body):
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


FAKE_CURSOR = r'''#!/usr/bin/env python3
import hashlib, json, os, sys, time
args = sys.argv[1:]
if "create-chat" in args:
    print("99999999-8888-7777-6666-555555555555")
    sys.exit(0)
if "--list-models" in args:
    print("Available models")
    print("auto - Auto (default)")
    print("composer-2.5 - Composer 2.5")
    sys.exit(0)

opts = {}
prompt_parts = []
i = 0
while i < len(args):
    a = args[i]
    if a in ("--resume", "--model", "--workspace", "--output-format"):
        opts[a] = args[i + 1] if i + 1 < len(args) else ""
        i += 2
        continue
    if a.startswith("-"):
        i += 1
        continue
    prompt_parts.append(a)
    i += 1
prompt = " ".join(prompt_parts)
resumed = opts.get("--resume") or ""
model = opts.get("--model") or ""
cwd = opts.get("--workspace") or os.getcwd()
sid = resumed or "99999999-8888-7777-6666-555555555555"

print(json.dumps({
    "type": "system", "subtype": "init", "session_id": sid,
    "model": model or "Composer 2.5 Fast", "cwd": cwd,
    "permissionMode": "default",
}), flush=True)
print(json.dumps({
    "type": "user",
    "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
    "session_id": sid,
}), flush=True)
print(json.dumps({
    "type": "thinking", "subtype": "delta", "text": "thinking",
    "session_id": sid,
}), flush=True)
print(json.dumps({
    "type": "assistant",
    "message": {"role": "assistant", "content": [
        {"type": "text", "text": "echo: " + prompt
         + ((" resumed=" + resumed) if resumed else "")
         + ((" model=" + model) if model else "")},
    ]},
    "session_id": sid,
}), flush=True)
print(json.dumps({
    "type": "tool_call", "subtype": "started",
    "call_id": "tool_1",
    "tool_call": {"globToolCall": {"args": {
        "targetDirectory": cwd, "globPattern": "*",
    }}},
    "session_id": sid,
}), flush=True)
print(json.dumps({
    "type": "tool_call", "subtype": "completed",
    "call_id": "tool_1",
    "tool_call": {"globToolCall": {"args": {
        "targetDirectory": cwd, "globPattern": "*",
    }, "result": {"success": {"files": ["README.md"]}}}},
    "session_id": sid,
}), flush=True)
print(json.dumps({
    "type": "result", "subtype": "success", "is_error": False,
    "result": "all done", "duration_ms": 42, "session_id": sid,
}), flush=True)

# Persist like the real CLI so list/resume can see the session.
home = os.path.join(os.environ.get("HOME", ""), ".cursor")
digest = hashlib.md5(cwd.encode()).hexdigest()
sdir = os.path.join(home, "chats", digest, sid)
os.makedirs(sdir, exist_ok=True)
now = int(time.time() * 1000)
with open(os.path.join(sdir, "meta.json"), "w") as f:
    json.dump({"schemaVersion": 1, "createdAtMs": now, "updatedAtMs": now,
               "hasConversation": True, "cwd": cwd, "title": ""}, f)
munged = "".join(ch if ch.isalnum() or ch in "-_" else "-" for ch in cwd.lstrip("/"))
td = os.path.join(home, "projects", munged, "agent-transcripts", sid)
os.makedirs(td, exist_ok=True)
with open(os.path.join(td, sid + ".jsonl"), "a") as f:
    f.write(json.dumps({
        "role": "user",
        "message": {"content": [{"type": "text", "text":
            "<user_query>\n" + prompt + "\n</user_query>"}]},
    }) + "\n")
    f.write(json.dumps({
        "role": "assistant",
        "message": {"content": [{"type": "text", "text": "echo: " + prompt}]},
    }) + "\n")
'''


def start_server(config, token):
    bundles = providers.build_all(config, JobManager)
    server = make_server(config, token, bundles)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, "http://127.0.0.1:%d" % port


def write_fixture():
    os.makedirs(PROJECT_CWD)
    digest = "abc123hash"
    sdir = os.path.join(CURSOR_HOME, "chats", digest, SESSION_ID)
    os.makedirs(sdir)
    now = 1789720609080
    with open(os.path.join(sdir, "meta.json"), "w") as f:
        json.dump({
            "schemaVersion": 1,
            "createdAtMs": now,
            "updatedAtMs": now + 10000,
            "hasConversation": True,
            "cwd": PROJECT_CWD,
            "title": "Fix login crash",
        }, f)
    munged = "".join(
        ch if ch.isalnum() or ch in "-_" else "-"
        for ch in PROJECT_CWD.lstrip("/"))
    tdir = os.path.join(
        CURSOR_HOME, "projects", munged, "agent-transcripts", SESSION_ID)
    os.makedirs(tdir)
    lines = [
        {"role": "user", "message": {"content": [
            {"type": "text", "text":
             "<timestamp>Friday</timestamp>\n<user_query>\nthe app crashes on login\n</user_query>"}
        ]}},
        {"role": "assistant", "message": {"content": [
            {"type": "text", "text": "Let me look at the login code."},
            {"type": "tool_use", "name": "Read",
             "input": {"path": "src/login.c"}},
        ]}},
        {"role": "assistant", "message": {"content": [
            {"type": "text", "text": "Fixed: the null check was missing. [REDACTED]"},
        ]}},
        {"type": "turn_ended", "status": "success"},
    ]
    with open(os.path.join(tdir, SESSION_ID + ".jsonl"), "w") as f:
        for obj in lines:
            f.write(json.dumps(obj) + "\n")

    # Minimal real Cursor content-addressed store: an initial root and a
    # complete root after the one visible user turn.
    db = sqlite3.connect(os.path.join(sdir, "store.db"))
    db.execute("CREATE TABLE blobs (id TEXT PRIMARY KEY, data BLOB)")
    db.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)")

    def blob(data):
        raw = data if isinstance(data, bytes) else data.encode()
        blob_id = hashlib.sha256(raw).hexdigest()
        db.execute("INSERT INTO blobs(id,data) VALUES(?,?)", (blob_id, raw))
        return blob_id

    system_id = blob(json.dumps({"role": "system", "content": "system"}))
    context_id = blob(json.dumps({"role": "user", "content": "<user_info/>"}))
    initial_data = (
        b"\x0a\x20" + bytes.fromhex(system_id)
        + b"\x12\x20" + bytes.fromhex(context_id)
    )
    initial_id = blob(initial_data)
    user_id = blob(json.dumps(lines[0]))
    assistant_id = blob(json.dumps(lines[1]))
    current_data = (
        b"\x0a\x20" + bytes.fromhex(system_id)
        + b"\x12\x20" + bytes.fromhex(context_id)
        + b"\x1a\x20" + bytes.fromhex(user_id)
        + b"\x22\x20" + bytes.fromhex(assistant_id)
    )
    current_id = blob(current_data)
    store_meta = {
        "agentId": SESSION_ID,
        "latestRootBlobId": current_id,
        "name": "Fix login crash",
    }
    db.execute("INSERT INTO meta(key,value) VALUES('0',?)", (
        json.dumps(store_meta, separators=(",", ":")).encode().hex(),))
    db.commit()
    db.close()
    return munged


class UsageHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args):
        pass

    def _send(self, value):
        raw = json.dumps(value).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    spend_only = False   # Enterprise-style seat: no plan limit, only spend

    def do_POST(self):
        if self.path.endswith("/GetCurrentPeriodUsage"):
            if type(self).spend_only:
                self._send({"billingCycleEnd": "1790781042000"})
                return
            self._send({
                "billingCycleEnd": "1790781042000",
                "planUsage": {
                    "totalSpend": 2500, "limit": 10000,
                    "totalPercentUsed": 25,
                },
            })
        elif self.path.endswith("/GetPlanInfo"):
            self._send({"planInfo": {"planName": "Pro"}})
        else:
            self._send({"totalCostCents": 2500})

    def do_GET(self):
        self._send({})


def main():
    token = load_or_create_token()
    print("\n=== cursor provider ===")
    munged = write_fixture()
    fake_bin = os.path.join(FAKE_HOME, "cursor-agent")
    write_script(fake_bin, FAKE_CURSOR)
    usage_server = HTTPServer(("127.0.0.1", 0), UsageHandler)
    threading.Thread(target=usage_server.serve_forever, daemon=True).start()
    os.environ["CURSOR_ACCESS_TOKEN"] = "test-token"

    config = Config({
        "provider": "cursor",
        "port": 0,
        "bind": "127.0.0.1",
        "cursor_home": CURSOR_HOME,
        "cursor_bin": fake_bin,
        "cursor_usage_url": "http://127.0.0.1:%d" % usage_server.server_address[1],
        "turn_timeout": 8,
    })
    server, base = start_server(config, token)

    _, ping = api(base, token, "/api/ping")
    check("ping reports cursor", ping.get("provider") == "cursor", ping)
    caps = ping.get("caps") or {}
    check("cursor caps: cwd required", caps.get("requires_cwd") is True, caps)
    check("cursor caps: can set model", caps.get("can_set_model") is True, caps)
    check("cursor caps: interactive follows tmux",
          caps.get("interactive") is tmux_available(), caps)
    check("cursor caps: Live TUI follows tmux",
          caps.get("live_tui") is tmux_available(), caps)
    check("cursor caps: rewind", caps.get("rewind") is True, caps)
    check("cursor caps: usage", caps.get("can_show_usage") is True, caps)
    check("models include auto", "auto" in (ping.get("models") or []), ping)
    slash = ping.get("slash_commands") or []
    check("Cursor advertises interactive slash commands",
          all(cmd in slash for cmd in ("/model", "/plan", "/usage", "/rewind")),
          slash)
    auth = ping.get("auth") or {}
    check("auth reports cli name", auth.get("cli") == "cursor-agent", auth)

    _, data = api(base, token, "/api/projects")
    projs = data.get("projects") or []
    check("one cursor project", len(projs) == 1, data)
    check("project cwd + name",
          projs and projs[0]["cwd"] == PROJECT_CWD
          and projs[0]["name"] == "myapp", projs)
    check("project id is munged cwd",
          projs and projs[0]["id"] == munged, projs)

    _, data = api(base, token, "/api/sessions?limit=10")
    sessions = data.get("sessions") or []
    check("lists fixture session",
          any(s.get("id") == SESSION_ID for s in sessions), data)
    row = next((s for s in sessions if s.get("id") == SESSION_ID), {})
    check("uses meta title", row.get("title") == "Fix login crash", row)

    _, data = api(base, token, "/api/sessions/" + SESSION_ID + "/messages")
    msgs = data.get("messages") or []
    user = next((m for m in msgs if m.get("role") == "user"), {})
    asst = [m for m in msgs if m.get("role") == "assistant"]
    check("unwraps <user_query>",
          user.get("text") == "the app crashes on login", user)
    check("strips [REDACTED]",
          asst and "REDACTED" not in (asst[-1].get("text") or ""), asst)
    check("keeps assistant prose",
          any("null check" in (m.get("text") or "") for m in asst), asst)

    check("Cursor TUI ready screen detected", _pane_ready(
        "Cursor Agent\n→ Plan, search, build anything\nRun Everything"))
    transcript_path = os.path.join(
        CURSOR_HOME, "projects", munged, "agent-transcripts",
        SESSION_ID, SESSION_ID + ".jsonl")
    tui = _Tui("cur-test", PROJECT_CWD)
    tui.session_id = SESSION_ID
    tui.rollout_path = transcript_path
    tui.rollout_offset = 0
    parser = CursorInteractiveManager.__new__(CursorInteractiveManager)
    parser.runner = None
    parser.config = config
    parse_job = Job("tui-parser", SESSION_ID, "test", PROJECT_CWD)
    parse_job.runner_state = {"parts": [], "full": [], "turn_done": False}
    done = parser._poll_rollout(parse_job, tui)
    check("Cursor TUI transcript emits turn completion", done)
    check("Cursor TUI transcript emits assistant text",
          any(e.get("kind") == "text" for e in parse_job.events),
          parse_job.events)

    _, data = api(base, token, "/api/sessions/search?q=login")
    check("search hits title or body",
          len(data.get("results") or []) >= 1, data)

    _, usage = api(base, token, "/api/usage")
    buckets = usage.get("buckets") or []
    check("Cursor usage loads dashboard plan",
          usage.get("ok") is True and buckets
          and buckets[0].get("percent") == 25, usage)
    check("plan bucket keeps its bar", buckets[0].get("show_bar", True) is True, buckets)
    from agentremoted.providers.cursor import CursorRunner
    UsageHandler.spend_only = True
    CursorRunner._usage_cache = (0.0, None)
    _, usage = api(base, token, "/api/usage")
    buckets = usage.get("buckets") or []
    check("spend-only seat reports money without a bar",
          usage.get("ok") is True and buckets
          and buckets[0].get("show_bar") is False
          and buckets[0].get("percent") == 0
          and "$25.00 used this cycle" in buckets[0].get("resets_text", ""), usage)
    UsageHandler.spend_only = False
    CursorRunner._usage_cache = (0.0, None)

    status, data = api(base, token, "/api/sessions/" + SESSION_ID + "/continue", {
        "prompt": "/rewind 1",
    })
    snap = wait_job(base, token, data["job_id"])
    check("Cursor rewind job done", snap.get("status") == "done", snap)
    _, rewound = api(base, token, "/api/sessions/" + SESSION_ID + "/messages")
    check("Cursor rewind trims transcript",
          not (rewound.get("messages") or []), rewound)
    check("Cursor rewind backs up store",
          os.path.isfile(os.path.join(
              CURSOR_HOME, "chats", "abc123hash", SESSION_ID,
              "store.db.rewind-bak")))

    try:
        api(base, token, "/api/sessions/new", {"prompt": "hello cursor"})
        check("cursor requires cwd", False)
    except urllib.error.HTTPError as e:
        check("cursor requires cwd", e.code == 400, e.read()[:200])

    status, data = api(base, token, "/api/sessions/new", {
        "prompt": "hello cursor",
        "cwd": PROJECT_CWD,
        "model": "composer-2.5",
    })
    check("new session accepted", status == 200 and data.get("job_id"), data)
    snap = wait_job(base, token, data["job_id"])
    check("cursor job done", snap.get("status") == "done", snap)
    check("new session id from init",
          snap.get("new_session_id") == NEW_SID, snap)
    events = snap.get("events") or []
    texts = [e.get("text") or "" for e in events if e.get("kind") == "text"]
    check("stream echoed prompt",
          any("hello cursor" in t for t in texts), texts)
    check("stream tool_call became tool event",
          any(e.get("kind") == "tool" and e.get("name") == "Glob"
              for e in events), events)

    status, data = api(base, token, "/api/sessions/" + NEW_SID + "/continue", {
        "prompt": "follow up",
        "cwd": PROJECT_CWD,
    })
    check("continue accepted", status == 200 and data.get("job_id"), data)
    snap = wait_job(base, token, data["job_id"])
    check("resume job done", snap.get("status") == "done", snap)
    events = snap.get("events") or []
    texts = [e.get("text") or "" for e in events if e.get("kind") == "text"]
    check("resume flag reached the CLI",
          any("resumed=" + NEW_SID in t for t in texts), texts)

    # Alias in config
    alias_cfg = Config({
        "providers": ["cursor-agent"],
        "port": 0,
        "bind": "127.0.0.1",
        "cursor_home": CURSOR_HOME,
        "cursor_bin": fake_bin,
    })
    names = alias_cfg.provider_names()
    check("cursor-agent alias canonicalizes", names == ["cursor"], names)

    server.shutdown()
    usage_server.shutdown()
    shutil.rmtree(FAKE_HOME, ignore_errors=True)
    if failures:
        print("\n%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
        sys.exit(1)
    print("\nall ok")


if __name__ == "__main__":
    main()
