"""End-to-end smoke test, run against BOTH providers.

Fixture transcripts + fake agent binaries + real HTTP servers:
  - claude suite: fake `claude` emitting stream-json, ~/.claude/projects tree
  - grok suite:   fake `grok` emitting streaming-json, ~/.grok/sessions tree
                  (new-session id discovered via filesystem diff, no
                  sessionId on stdout — the hard path)

Run:  python3 tests/smoke_test.py
"""

import base64
import json
import os
import shutil
import socket
import stat
import struct
import sys
import tempfile
import threading
import time
import urllib.request
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-test-")
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
# Never read the real macOS Keychain (real OAuth tokens -> live API calls).
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.config import Config, load_or_create_token  # noqa: E402
from agentremoted.jobs import JobManager                      # noqa: E402
from agentremoted.server import make_server                   # noqa: E402
from agentremoted import providers                            # noqa: E402

SESSION_ID = "11111111-2222-3333-4444-555555555555"
PROJECT_DIR_NAME = "-Users-me-code-myapp"
PROJECT_CWD = os.path.join(FAKE_HOME, "myapp")

TRANSCRIPT = [
    {"type": "summary", "summary": "Fix login crash", "leafUuid": "u3"},
    {"type": "user", "uuid": "u1", "timestamp": "2026-07-19T10:00:00Z",
     "sessionId": SESSION_ID, "cwd": PROJECT_CWD, "gitBranch": "main",
     "message": {"role": "user", "content": "the app crashes on login, please fix"}},
    {"type": "assistant", "uuid": "u2", "timestamp": "2026-07-19T10:00:05Z",
     "sessionId": SESSION_ID, "cwd": PROJECT_CWD,
     "message": {"role": "assistant", "content": [
         {"type": "text", "text": "Let me look at the login code."},
         {"type": "tool_use", "name": "Read", "input": {"file_path": "src/login.c"}},
     ]}},
    {"type": "user", "uuid": "u2r", "timestamp": "2026-07-19T10:00:06Z",
     "sessionId": SESSION_ID,
     "message": {"role": "user", "content": [
         {"type": "tool_result", "tool_use_id": "x", "content": "int main() {}"},
     ]}},
    {"type": "assistant", "uuid": "u3", "timestamp": "2026-07-19T10:00:10Z",
     "sessionId": SESSION_ID,
     "message": {"role": "assistant", "model": "claude-opus-4-8", "content": [
         {"type": "text", "text": "Fixed: the null check was missing."},
     ]}},
    # Harness injections stored as user-role lines the human never typed —
    # none of these may surface in the transcript or previews.
    {"type": "user", "uuid": "u4", "timestamp": "2026-07-19T10:00:11Z",
     "sessionId": SESSION_ID, "isMeta": True,
     "message": {"role": "user", "content":
                 "Caveat: the messages below were generated while running "
                 "local commands."}},
    {"type": "user", "uuid": "u5", "timestamp": "2026-07-19T10:00:12Z",
     "sessionId": SESSION_ID,
     "message": {"role": "user", "content":
                 "<task-notification>\n<task-id>abc</task-id>\nAgent "
                 "finished\n</task-notification>"}},
    {"type": "user", "uuid": "u6", "timestamp": "2026-07-19T10:00:13Z",
     "sessionId": SESSION_ID,
     "message": {"role": "user", "content": [
         {"type": "text", "text": "<system-reminder>internal nudge"
                                  "</system-reminder>ship it\nplease"},
     ]}},
    {"type": "user", "uuid": "u7", "timestamp": "2026-07-19T10:00:14Z",
     "sessionId": SESSION_ID,
     "message": {"role": "user", "content":
                 "<command-name>/compact</command-name>"
                 "<command-message>compact</command-message>"
                 "<command-args>keep the login work</command-args>"}},
]

# Two transcripts that sit in the same project dir but are not the user's own
# sessions, so the list/search/projects endpoints must skip them (while a
# direct lookup by id still resolves).
SHELL_SESSION_ID = "aaaaaaaa-1111-2222-3333-444444444444"
SHELL_TRANSCRIPT = [
    # TUI opened and quit: a slash command, its output, no model turn at all.
    {"type": "user", "uuid": "s1", "timestamp": "2026-07-19T12:00:00Z",
     "sessionId": SHELL_SESSION_ID, "cwd": PROJECT_CWD, "gitBranch": "main",
     "message": {"role": "user", "content":
                 "<command-name>/exit</command-name>"
                 "<command-message>exit</command-message>"
                 "<command-args></command-args>"}},
    {"type": "user", "uuid": "s2", "timestamp": "2026-07-19T12:00:00Z",
     "sessionId": SHELL_SESSION_ID,
     "message": {"role": "user", "content":
                 "<local-command-stdout>Catch you later!</local-command-stdout>"}},
]

# A turn that started seconds ago: the human's prompt is on disk, the reply is
# not yet. Must be listed anyway (mtime is fresh), or a session the user just
# started from the phone would be missing from the list.
FRESH_SESSION_ID = "cccccccc-1111-2222-3333-444444444444"
FRESH_TRANSCRIPT = [
    {"type": "user", "uuid": "f1", "timestamp": "2026-07-19T14:00:00Z",
     "sessionId": FRESH_SESSION_ID, "cwd": PROJECT_CWD, "gitBranch": "main",
     "message": {"role": "user", "content": "start on the parser rewrite"}},
]

SIDE_SESSION_ID = "bbbbbbbb-1111-2222-3333-444444444444"
SIDE_TRANSCRIPT = [
    {"type": "user", "uuid": "d1", "timestamp": "2026-07-19T13:00:00Z",
     "sessionId": SIDE_SESSION_ID, "cwd": PROJECT_CWD, "isSidechain": True,
     "message": {"role": "user", "content": "Search the repo for login code"}},
    {"type": "assistant", "uuid": "d2", "timestamp": "2026-07-19T13:00:05Z",
     "sessionId": SIDE_SESSION_ID, "isSidechain": True,
     "message": {"role": "assistant", "model": "claude-opus-4-8",
                 "content": [{"type": "text", "text": "Found src/login.c"}]}},
]

FAKE_CLAUDE = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""
resumed = args[args.index("--resume") + 1] if "--resume" in args else ""
model = args[args.index("--model") + 1] if "--model" in args else ""
sid = "99999999-8888-7777-6666-555555555555"
print(json.dumps({"type": "system", "subtype": "init", "session_id": sid, "model": "fake-model"}),
      flush=True)
if "SLEEP" in prompt:
    # A tool_use first, so live-status streams a semantic phase while we hang.
    print(json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Bash", "input": {"command": "sleep 30"}},
    ]}}), flush=True)
    time.sleep(30)   # the stop test kills us long before this
    sys.exit(0)
if "NAP" in prompt:
    time.sleep(1.0)  # long enough to queue prompts behind this job
env_marker = os.environ.get("AGENTREMOTE_TEST_ENV", "")
print(json.dumps({"type": "assistant", "message": {"content": [
    {"type": "text", "text": "echo: " + prompt
        + (" (resumed " + resumed + ")" if resumed else "")
        + ((" model=" + model) if model else "")
        + ((" env=" + env_marker) if env_marker else "")},
    {"type": "tool_use", "name": "Bash", "input": {"command": "ls -la"}},
]}}))
print(json.dumps({"type": "result", "result": "all done", "is_error": False,
                  "duration_ms": 42, "total_cost_usd": 0.01}))
'''

# The fake grok never prints a sessionId for new sessions — it only creates
# the session dir on disk, exactly the case the fs-diff scanner exists for.
GROK_NEW_SID = "12345678-9999-aaaa-bbbb-cccccccccccc"
FAKE_GROK = r'''#!/usr/bin/env python3
import json, os, sys, time
args = sys.argv[1:]
prompt = args[args.index("-p") + 1] if "-p" in args else ""
resumed = args[args.index("--resume") + 1] if "--resume" in args else ""
cwd = args[args.index("--cwd") + 1] if "--cwd" in args else ""
model = args[args.index("--model") + 1] if "--model" in args else ""
effort = args[args.index("--effort") + 1] if "--effort" in args else ""
if "SLEEP" in prompt:
    time.sleep(30)   # the timeout test kills us long before this
    sys.exit(0)
if "NAP" in prompt:
    time.sleep(1.0)
flags_marker = " flags=yolo" if "--yolo" in args else ""
print(json.dumps({"type": "thought", "data": "hmm"}), flush=True)
# Real grok streaming-json omits tool events — they only land in
# updates.jsonl. For DISKTOOL prompts, write a tool_call there and hang
# long enough for tick() to poll it into the live status banner.
if "DISKTOOL" in prompt and resumed:
    root = os.path.join(os.environ["FAKE_GROK_HOME"], "sessions")
    updates = None
    for group in os.listdir(root):
        cand = os.path.join(root, group, resumed, "updates.jsonl")
        if os.path.isfile(cand):
            updates = cand
            break
    if updates:
        rec = {"params": {"update": {
            "sessionUpdate": "tool_call",
            "toolCallId": "call-disk-1",
            "title": "run_terminal_command",
            "rawInput": {"command": "ls -la", "description": "list files"},
            "_meta": {"x.ai/tool": {
                "name": "run_terminal_command", "kind": "execute",
                "label": "Run Command",
            }},
        }}, "timestamp": time.time()}
        with open(updates, "a") as f:
            f.write(json.dumps(rec) + "\n")
    time.sleep(1.5)
    print(json.dumps({"type": "text", "data": "disk tool done"}), flush=True)
    print(json.dumps({"type": "end", "stopReason": "completed",
                      "sessionId": resumed}))
    sys.exit(0)
print(json.dumps({"type": "text", "data": "echo: " + prompt
                  + (" (resumed " + resumed + ")" if resumed else "")
                  + ((" model=" + model) if model else "")
                  + ((" effort=" + effort) if effort else "")
                  + flags_marker}), flush=True)
print(json.dumps({"type": "tool", "title": "Bash",
                  "input": {"command": "ls -la"}}), flush=True)
print(json.dumps({"type": "text", "data": " tail"}), flush=True)
if not resumed:
    root = os.path.join(os.environ["FAKE_GROK_HOME"], "sessions", "g1", "%s")
    os.makedirs(root, exist_ok=True)
    with open(os.path.join(root, "summary.json"), "w") as f:
        json.dump({"info": {"id": "%s", "cwd": cwd or "/"},
                   "num_messages": 1, "generated_title": "fresh one",
                   "created_at": "2026-07-20T00:00:00Z"}, f)
    open(os.path.join(root, "updates.jsonl"), "w").close()
    time.sleep(1.2)  # keep running so tick()'s scanner finds the dir live
print(json.dumps({"type": "end", "stopReason": "completed"}))
if "EXITCODE1" in prompt:
    sys.exit(1)      # a clean `end` must still count as done
''' % (GROK_NEW_SID, GROK_NEW_SID)

GROK_SESSION_ID = "aaaaaaaa-bbbb-cccc-dddd-eeeeffff0000"

failures = []


def check(name, cond, detail=""):
    status = "ok" if cond else "FAIL"
    print("  [%s] %s%s" % (status, name, (" — " + str(detail)) if detail and not cond else ""))
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
    deadline = time.time() + timeout
    snap = {}
    while time.time() < deadline:
        _, snap = api(base, token, "/api/jobs/" + job_id)
        if snap["status"] in want:
            break
        time.sleep(0.2)
    return snap


def ws_connect(port, token):
    """Bare-bones RFC 6455 client: handshake and return the socket."""
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((
        "GET /ws/status?token=%s HTTP/1.1\r\n"
        "Host: 127.0.0.1:%d\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        "Sec-WebSocket-Key: %s\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n" % (token, port, key)).encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise RuntimeError("ws handshake: connection closed")
        buf += chunk
    head, rest = buf.split(b"\r\n\r\n", 1)
    if b" 101 " not in head.split(b"\r\n", 1)[0]:
        raise RuntimeError("ws handshake rejected: %r" % head[:120])
    return s, rest


def ws_recv_text(s, leftover, deadline):
    """Next text frame's JSON payload (skips pings). Returns (obj, leftover)."""
    buf = leftover
    while time.time() < deadline:
        # Parse one complete (unmasked, unfragmented) server frame.
        if len(buf) >= 2:
            n = buf[1] & 0x7F
            off = 2
            if n == 126 and len(buf) >= 4:
                n = struct.unpack(">H", buf[2:4])[0]
                off = 4
            if len(buf) >= off + n:
                opcode = buf[0] & 0x0F
                payload, buf = buf[off:off + n], buf[off + n:]
                if opcode == 0x1:
                    return json.loads(payload.decode()), buf
                continue  # ping/pong/close — skip
        chunk = s.recv(4096)
        if not chunk:
            raise RuntimeError("ws stream closed")
        buf += chunk
    raise RuntimeError("ws frame timeout")


def write_script(path, body):
    with open(path, "w") as f:
        f.write(body)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)


def start_server(config, token):
    bundles = providers.build_all(config, JobManager)
    jobs = next(iter(bundles.values())).jobs
    server = make_server(config, token, bundles)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, jobs, port, "http://127.0.0.1:%d" % port


# ---------------------------------------------------------------------------
# claude provider suite
# ---------------------------------------------------------------------------

def run_claude_suite(token):
    print("\n=== claude provider ===")
    projects = os.path.join(FAKE_HOME, ".claude", "projects", PROJECT_DIR_NAME)
    os.makedirs(projects)
    os.makedirs(PROJECT_CWD)
    for sid, transcript in ((SESSION_ID, TRANSCRIPT),
                            (SHELL_SESSION_ID, SHELL_TRANSCRIPT),
                            (SIDE_SESSION_ID, SIDE_TRANSCRIPT),
                            (FRESH_SESSION_ID, FRESH_TRANSCRIPT)):
        path = os.path.join(projects, sid + ".jsonl")
        with open(path, "w") as f:
            for line in transcript:
                f.write(json.dumps(line) + "\n")
        # Everything but the in-flight session is history: age it past the
        # "no reply yet, give it a moment" window.
        if sid != FRESH_SESSION_ID:
            old = time.time() - 3600
            os.utime(path, (old, old))

    fake_bin = os.path.join(FAKE_HOME, "claude")
    write_script(fake_bin, FAKE_CLAUDE)

    config = Config({
        "provider": "claude",
        "bind": "127.0.0.1", "port": 0,
        "projects_dir": os.path.join(FAKE_HOME, ".claude", "projects"),
        "claude_bin": fake_bin,
        "claude_env": {"AGENTREMOTE_TEST_ENV": "marker-xyz"},
        "permission_timeout": 5,
        # Pin drop under the fake home so macOS's default ~/Public is not used.
        "drop_dir": os.path.join(os.environ["AGENTREMOTED_HOME"], "drop"),
    })
    server, jobs, port, base = start_server(config, token)

    print("ping / auth:")
    with urllib.request.urlopen(base + "/api/ping", timeout=5) as resp:
        ping = json.loads(resp.read().decode())
    check("ping without token", ping.get("ok") is True)
    check("ping reports provider", ping.get("provider") == "claude", ping)
    auth = ping.get("auth") or {}
    check("ping includes auth_health", isinstance(auth, dict) and "status" in auth, auth)
    check("auth reports cli name", auth.get("cli") == "claude", auth)
    caps = ping.get("caps") or {}
    check("claude caps: permissions on",
          caps.get("permissions") is True and caps.get("permission_modes") is True, caps)
    check("claude caps: cwd required", caps.get("requires_cwd") is True, caps)
    check("claude caps: can set model", caps.get("can_set_model") is True, caps)
    check("claude caps: no effort flag", caps.get("can_set_effort") is False, caps)
    try:
        urllib.request.urlopen(base + "/api/projects", timeout=5)
        check("reject missing token", False)
    except urllib.error.HTTPError as e:
        check("reject missing token", e.code == 401)

    # Slash commands only for authenticated pings.
    check("no slash commands without token", "slash_commands" not in ping, ping)
    _, authed_ping = api(base, token, "/api/ping")
    check("authed ping lists slash commands",
          isinstance(authed_ping.get("slash_commands"), list), authed_ping)
    check("authed ping lists models",
          "default" in (authed_ping.get("models") or []), authed_ping)

    # Same token over the grok-legacy header styles must also pass.
    req = urllib.request.Request(base + "/api/projects",
                                 headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=5) as resp:
        check("bearer auth accepted", resp.status == 200)
    req = urllib.request.Request(base + "/api/projects",
                                 headers={"X-Grok-Token": token})
    with urllib.request.urlopen(req, timeout=5) as resp:
        check("legacy X-Grok-Token accepted", resp.status == 200)

    print("browsing:")
    _, data = api(base, token, "/api/projects")
    check("one project", len(data["projects"]) == 1, data)
    check("project cwd recovered", data["projects"][0]["cwd"] == PROJECT_CWD, data)
    check("project counts only the user's sessions",
          data["projects"][0]["session_count"] == 2, data)

    _, data = api(base, token, "/api/sessions")
    check("shell + sidechain hidden, in-flight session kept",
          {x["id"] for x in data["sessions"]} == {SESSION_ID, FRESH_SESSION_ID},
          [x["id"] for x in data["sessions"]])
    s = [x for x in data["sessions"] if x["id"] == SESSION_ID][0]
    check("session title from summary", s["title"] == "Fix login crash", s)
    check("session branch", s["git_branch"] == "main", s)
    check("session model from tail", s.get("model") == "claude-opus-4-8", s)
    # The newest human-visible line is the /compact envelope (u7); the
    # injected u4/u5 lines after the assistant reply must not win.
    check("last text", "/compact" in s["last_text"]
          and "task-notification" not in s["last_text"], s)

    _, data = api(base, token, "/api/sessions?all=1")
    check("all=1 reveals the filtered sessions",
          {x["id"] for x in data["sessions"]}
          == {SESSION_ID, SHELL_SESSION_ID, SIDE_SESSION_ID, FRESH_SESSION_ID},
          [x["id"] for x in data["sessions"]])
    for hidden in (SHELL_SESSION_ID, SIDE_SESSION_ID):
        _, one = api(base, token, "/api/sessions/" + hidden)
        check("hidden session still resolves by id " + hidden[:8],
              one.get("id") == hidden, one)

    print("search:")
    _, data = api(base, token, "/api/sessions/search?q=login")
    check("search returns query echo", data.get("query") == "login", data)
    check("search hits title", len(data.get("results") or []) == 1, data)
    hit = (data.get("results") or [{}])[0]
    check("search result id", hit.get("id") == SESSION_ID, hit)
    check("search snippet mentions login",
          "login" in (hit.get("snippet") or "").lower(), hit)
    _, data = api(base, token, "/api/sessions/search?q=null%20check")
    check("search hits message body",
          len(data.get("results") or []) == 1
          and "null" in (data["results"][0].get("snippet") or "").lower(), data)
    _, data = api(base, token, "/api/sessions/search?q=zzzz-no-such-token")
    check("search empty for miss", data.get("results") == [], data)
    _, data = api(base, token, "/api/sessions/search?q=")
    check("empty query returns no results", data.get("results") == [], data)
    _, data = api(base, token, "/api/sessions/search?q=Found%20src")
    check("search skips the sidechain transcript", data.get("results") == [], data)
    _, data = api(base, token, "/api/sessions/search?q=Found%20src&all=1")
    check("all=1 search finds it",
          [r.get("id") for r in data.get("results") or []] == [SIDE_SESSION_ID], data)

    _, data = api(base, token, "/api/sessions/" + SESSION_ID + "/messages")
    # u1..u3 conversation + cleaned u6 ("ship it") + u7 ("/compact ...");
    # isMeta (u4), task-notification (u5) and tool_result (u2r) are dropped.
    check("5 conversational messages", data["total"] == 5, data)
    check("tool_result line filtered", all(m["uuid"] != "u2r" for m in data["messages"]))
    check("isMeta + notification lines filtered",
          all(m["uuid"] not in ("u4", "u5") for m in data["messages"]),
          [m["uuid"] for m in data["messages"]])
    joined = json.dumps(data["messages"])
    check("no injected tags leak",
          "task-notification" not in joined and "system-reminder" not in joined,
          joined[:200])
    u6 = [m for m in data["messages"] if m["uuid"] == "u6"][0]
    check("reminder stripped, human text kept",
          u6["text"] == "ship it\nplease", u6)
    u7 = [m for m in data["messages"] if m["uuid"] == "u7"][0]
    check("slash command shown as command",
          u7["text"] == "/compact keep the login work", u7)
    check("rendered blocks on every message",
          all(m.get("blocks") for m in data["messages"]), data["messages"])
    check("user message renders as user block",
          data["messages"][0]["blocks"][0]["k"] == "user", data["messages"][0])

    _, data = api(base, token, "/api/sessions/" + SESSION_ID + "/messages?offset=0&limit=1")
    check("pagination window", len(data["messages"]) == 1 and data["messages"][0]["uuid"] == "u1", data)

    try:
        api(base, token, "/api/sessions/nope-nope")
        check("unknown session 404", False)
    except urllib.error.HTTPError as e:
        check("unknown session 404", e.code == 404)

    print("continue via job:")
    status, data = api(base, token, "/api/sessions/" + SESSION_ID + "/continue",
                       {"prompt": "now add tests", "model": "opus"})
    check("continue accepted", status == 202 and data.get("job_id"), data)
    snap = wait_job(base, token, data["job_id"])
    check("job finished ok", snap.get("status") == "done", snap)
    check("model flag passed through",
          any("model=opus" in e.get("text", "") for e in snap["events"]), snap["events"])
    check("job captured new session id", snap.get("new_session_id", "").startswith("9999"), snap)
    kinds = [e["kind"] for e in snap.get("events", [])]
    check("events: init/text/tool/result", kinds == ["init", "text", "tool", "result"], kinds)
    text_events = [e for e in snap["events"] if e["kind"] == "text"]
    check("live text events carry rendered blocks",
          all(e.get("blocks") for e in text_events), text_events)
    check("result text", snap.get("result_text") == "all done", snap)
    check("resume flag passed through",
          any("resumed " + SESSION_ID in e.get("text", "") for e in snap["events"]), snap["events"])
    check("claude_env passed through",
          any("env=marker-xyz" in e.get("text", "") for e in snap["events"]), snap["events"])

    _, incr = api(base, token, "/api/jobs/%s?since=%d" % (snap["id"], snap["next_seq"]))
    check("incremental poll returns nothing new", incr["events"] == [], incr)

    print("stop:")
    status, data = api(base, token, "/api/sessions/" + SESSION_ID + "/continue",
                       {"prompt": "SLEEP forever"})
    check("slow job accepted", status == 202 and data.get("job_id"), data)
    slow_id = data["job_id"]
    snap = wait_job(base, token, slow_id, want=("running",), timeout=10)
    check("slow job running", snap.get("status") == "running", snap)
    _, data = api(base, token, "/api/jobs/" + slow_id + "/stop", {})
    check("stop accepted", data.get("ok") is True, data)
    snap = wait_job(base, token, slow_id)
    check("job stopped", snap.get("status") == "stopped", snap)

    print("prompt queue:")
    status, data = api(base, token, "/api/sessions/" + SESSION_ID + "/continue",
                       {"prompt": "NAP then answer"})
    nap_id = data["job_id"]
    status, data = api(base, token, "/api/jobs/%s/queue" % nap_id,
                       {"prompt": "queued one"})
    check("queue accepted", status == 202 and len(data.get("queued", [])) == 1, data)
    status, data = api(base, token, "/api/jobs/%s/queue" % nap_id,
                       {"prompt": "queued two"})
    check("second queued", len(data.get("queued", [])) == 2, data)
    qid_one = data["queued"][0]["id"]
    _, data = api(base, token, "/api/jobs/%s/queue/%s/cancel" % (nap_id, qid_one), {})
    check("cancel returns prompt", data.get("prompt") == "queued one", data)
    check("one left after cancel", len(data.get("queued", [])) == 1, data)
    _, snap = api(base, token, "/api/jobs/" + nap_id)
    check("snapshot mirrors queue",
          [q["prompt"] for q in snap.get("queued", [])] == ["queued two"], snap)

    # When the nap job finishes, the daemon must chain into the queued
    # prompt (resuming the fork) and expose next_job_id.
    next_id = ""
    deadline = time.time() + 15
    while time.time() < deadline:
        _, snap = api(base, token, "/api/jobs/" + nap_id)
        if snap["status"] == "done" and snap.get("next_job_id"):
            next_id = snap["next_job_id"]
            break
        time.sleep(0.2)
    check("chained into queued prompt", next_id != "", snap)
    snap = wait_job(base, token, next_id) if next_id else {}
    check("chained job ran the queued prompt",
          any("echo: queued two" in e.get("text", "") for e in snap.get("events", [])),
          snap.get("events"))
    check("chained job resumed the fork",
          any("resumed 9999" in e.get("text", "") for e in snap.get("events", [])),
          snap.get("events"))

    # Stop drops whatever is still queued and reports the count.
    status, data = api(base, token, "/api/sessions/" + SESSION_ID + "/continue",
                       {"prompt": "SLEEP with queue"})
    sq_id = data["job_id"]
    api(base, token, "/api/jobs/%s/queue" % sq_id, {"prompt": "never runs"})
    api(base, token, "/api/jobs/%s/stop" % sq_id, {})
    snap = wait_job(base, token, sq_id, want=("stopped",))
    check("stop drops the queue",
          snap.get("queued") == [] and snap.get("dropped_queued") == 1, snap)

    print("permission round-trip:")
    status, data = api(base, token, "/api/sessions/" + SESSION_ID + "/continue",
                       {"prompt": "SLEEP for perms", "permission_mode": "default"})
    perm_job = data["job_id"]
    wait_job(base, token, perm_job, want=("running",), timeout=10)
    nonce = jobs.get(perm_job).perm_nonce

    # The helper MCP tool bridges through /internal/permission (nonce auth,
    # not the app token). It blocks until the phone answers.
    bridge = {}

    def call_bridge():
        try:
            _, d = api(base, token, "/internal/permission",
                       {"job_id": perm_job, "nonce": nonce,
                        "tool_name": "Bash", "input": {"command": "ls -la"}})
            bridge.update(d)
        except Exception as e:  # noqa: BLE001
            bridge["exc"] = str(e)

    t = threading.Thread(target=call_bridge)
    t.start()

    pend = None
    deadline = time.time() + 5
    while time.time() < deadline:
        _, snap = api(base, token, "/api/jobs/" + perm_job)
        if snap.get("pending_permission"):
            pend = snap["pending_permission"]
            break
        time.sleep(0.1)
    check("pending permission surfaces",
          pend is not None and pend.get("tool_name") == "Bash", pend)
    check("permission detail carried", pend and pend.get("detail") == "ls -la", pend)

    _, d = api(base, token, "/api/jobs/%s/permission" % perm_job,
               {"request_id": pend["request_id"], "allow": True})
    check("permission answer accepted", d.get("ok") is True, d)
    t.join(timeout=5)
    check("bridge received allow", bridge.get("allow") is True, bridge)

    _, snap = api(base, token, "/api/jobs/" + perm_job)
    check("pending cleared after answer", not snap.get("pending_permission"), snap)

    # A wrong nonce must be rejected, not honored.
    _, dbad = api(base, token, "/internal/permission",
                  {"job_id": perm_job, "nonce": "wrong",
                   "tool_name": "Bash", "input": {}})
    check("bad nonce denied", dbad.get("allow") is False, dbad)

    api(base, token, "/api/jobs/%s/stop" % perm_job, {})

    print("websocket status stream:")
    ws, left = ws_connect(port, token)
    obj, left = ws_recv_text(ws, left, time.time() + 5)
    check("first frame is a status snapshot", obj.get("type") == "status", obj)

    status, data = api(base, token, "/api/sessions/" + SESSION_ID + "/continue",
                       {"prompt": "SLEEP for ws"})
    ws_job = data["job_id"]
    seen = None
    deadline = time.time() + 10
    while time.time() < deadline:
        obj, left = ws_recv_text(ws, left, deadline)
        match = [a for a in obj.get("active", []) if a.get("job_id") == ws_job]
        if match and match[0].get("status") == "running":
            seen = match[0]
            break
    check("stream pushes the running job", seen is not None, obj)
    check("stream carries session + elapsed",
          seen and seen.get("session_id") == SESSION_ID and "elapsed_s" in seen, seen)
    # The Bash tool_use maps to the "running" phase for the banner verb.
    pseen = None
    deadline = time.time() + 10
    while time.time() < deadline and pseen is None:
        match = [a for a in obj.get("active", []) if a.get("job_id") == ws_job]
        if match and match[0].get("phase") == "running":
            pseen = match[0]
            break
        obj, left = ws_recv_text(ws, left, deadline)
    check("stream carries semantic phase",
          pseen is not None and pseen.get("phase_detail") == "sleep 30", pseen)
    api(base, token, "/api/jobs/%s/queue" % ws_job, {"prompt": "queued for ws"})
    deadline = time.time() + 10
    qseen = False
    while time.time() < deadline and not qseen:
        obj, left = ws_recv_text(ws, left, deadline)
        match = [a for a in obj.get("active", []) if a.get("job_id") == ws_job]
        qseen = bool(match) and match[0].get("queued_count") == 1
    check("stream reflects queued count", qseen, obj)
    api(base, token, "/api/jobs/%s/stop" % ws_job, {})
    deadline = time.time() + 10
    gone = False
    while time.time() < deadline and not gone:
        obj, left = ws_recv_text(ws, left, deadline)
        gone = all(a.get("job_id") != ws_job for a in obj.get("active", []))
    check("stopped job leaves the stream", gone, obj)
    ws.close()

    try:
        ws_connect(port, "wrong-token")
        check("ws rejects bad token", False)
    except (RuntimeError, OSError):
        check("ws rejects bad token", True)

    print("attachments:")
    payload = b"\x89PNG fake image bytes" * 100
    req = urllib.request.Request(
        base + "/api/attachments?name=../shot%201.png", data=payload,
        headers={"X-Auth-Token": token,
                 "Content-Type": "application/octet-stream"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        up = json.loads(resp.read().decode())
        check("upload accepted", resp.status == 201 and up.get("ok") is True, up)
    check("upload size recorded", up.get("size") == len(payload), up)
    check("filename sanitized (no traversal)",
          "/uploads/" in up.get("path", "") and ".." not in os.path.basename(up["path"]),
          up)
    with open(up["path"], "rb") as f:
        check("upload stored verbatim", f.read() == payload, up["path"])
    try:
        req = urllib.request.Request(base + "/api/attachments?name=x.bin",
                                     data=b"x", headers={})
        urllib.request.urlopen(req, timeout=5)
        check("upload requires token", False)
    except urllib.error.HTTPError as e:
        check("upload requires token", e.code == 401)

    print("drop (host→phone):")
    # The server under test uses AGENTREMOTED_HOME; write a file into its drop dir.
    drop_dir = os.path.join(os.environ["AGENTREMOTED_HOME"], "drop")
    os.makedirs(drop_dir, exist_ok=True)
    drop_name = "hello drop.txt"
    drop_body = b"hello from host\n"
    with open(os.path.join(drop_dir, drop_name), "wb") as f:
        f.write(drop_body)
    # Hidden files must not appear.
    with open(os.path.join(drop_dir, ".secret"), "w") as f:
        f.write("nope")
    _, listing = api(base, token, "/api/drop")
    names = [f["name"] for f in listing.get("files", [])]
    check("drop lists staged file", drop_name in names, listing)
    check("drop hides dotfiles", ".secret" not in names, listing)
    check("drop path is absolute",
          os.path.isabs(listing.get("path", "")), listing)
    check("authed ping carries drop_path",
          os.path.isabs(authed_ping.get("drop_path", "")), authed_ping)
    req = urllib.request.Request(
        base + "/api/drop/" + urllib.request.quote(drop_name),
        headers={"X-Auth-Token": token})
    with urllib.request.urlopen(req, timeout=10) as resp:
        got = resp.read()
        check("drop download bytes match", got == drop_body, got[:40])
        check("drop download content-type",
              resp.headers.get("Content-Type", "").startswith("application/octet"),
              resp.headers.get("Content-Type"))
    try:
        urllib.request.urlopen(
            urllib.request.Request(base + "/api/drop/" + urllib.request.quote(drop_name)),
            timeout=5)
        check("drop download requires token", False)
    except urllib.error.HTTPError as e:
        check("drop download requires token", e.code == 401)
    try:
        api(base, token, "/api/drop/../etc/passwd")
        check("drop rejects traversal", False)
    except urllib.error.HTTPError as e:
        check("drop rejects traversal", e.code in (400, 404), e.code)
    status, data = api(base, token,
                       "/api/drop/" + urllib.request.quote(drop_name) + "/delete",
                       {})
    check("drop delete ok", status == 200 and data.get("ok") is True, data)
    _, listing2 = api(base, token, "/api/drop")
    check("drop delete removed file",
          drop_name not in [f["name"] for f in listing2.get("files", [])],
          listing2)

    print("new session:")
    status, data = api(base, token, "/api/sessions/new",
                       {"prompt": "hello", "cwd": FAKE_HOME})
    check("new session accepted", status == 202, data)
    try:
        api(base, token, "/api/sessions/new", {"prompt": "hello"})
        check("claude requires cwd", False)
    except urllib.error.HTTPError as e:
        check("claude requires cwd", e.code == 400)

    try:
        api(base, token, "/api/sessions/" + SESSION_ID + "/continue", {"prompt": "  "})
        check("reject empty prompt", False)
    except urllib.error.HTTPError as e:
        check("reject empty prompt", e.code == 400)

    server.shutdown()


# ---------------------------------------------------------------------------
# grok provider suite
# ---------------------------------------------------------------------------

GROK_SUBAGENT_ID = "dddddddd-eeee-ffff-0000-111122223333"


def grok_fixture(grok_home):
    """One real session (streamed chunks, thought span, tool call,
    turn_completed with NaN usage) + one empty session and one subagent
    session, both of which must be hidden."""
    cwd = os.path.join(FAKE_HOME, "grokproj")
    os.makedirs(cwd, exist_ok=True)
    sdir = os.path.join(grok_home, "sessions", "g0", GROK_SESSION_ID)
    os.makedirs(sdir)
    with open(os.path.join(sdir, "summary.json"), "w") as f:
        json.dump({
            "info": {"id": GROK_SESSION_ID, "cwd": cwd},
            "git_root_dir": cwd,
            "head_branch": "main",
            "num_messages": 3,
            "generated_title": "Refactor the parser",
            "created_at": "2026-07-19T10:00:00Z",
            "updated_at": "2026-07-19T11:00:00Z",
            "current_model_id": "grok-4.5",
        }, f)

    def upd(kind, ts, **fields):
        update = {"sessionUpdate": kind}
        update.update(fields)
        return {"params": {"update": update}, "timestamp": ts}

    events = [
        # Turn 1: normal — "Worked for" comes from wall time.
        upd("user_message_chunk", 100.0, content="please refactor "),
        upd("user_message_chunk", 100.5, content={"text": "the parser"}),
        upd("agent_thought_chunk", 101.0, content="thinking..."),
        upd("agent_thought_chunk", 103.0, content="more thinking"),
        upd("tool_call", 103.5, title="Edit parser.py"),
        upd("agent_message_chunk", 104.0, content="Done. "),
        upd("agent_message_chunk", 105.0, content="**Refactored** the parser."),
        upd("turn_completed", 110.0, usage={"apiDurationMs": 9500}),
        # Turn 2: zero wall time and NaN usage — the "Worked for" row must
        # be dropped, never rendered as "Worked for nans".
        upd("user_message_chunk", 200.0, content="and a quick fix"),
        upd("agent_message_chunk", 200.0, content="Quick fix **done**."),
        upd("turn_completed", 200.0, usage={"apiDurationMs": float("nan")}),
    ]
    with open(os.path.join(sdir, "updates.jsonl"), "w") as f:
        for ev in events:
            # json.dumps happily writes NaN — exactly what real grok does.
            f.write(json.dumps(ev) + "\n")

    empty = os.path.join(grok_home, "sessions", "g0",
                         "bbbbbbbb-cccc-dddd-eeee-ffff00001111")
    os.makedirs(empty)
    with open(os.path.join(empty, "summary.json"), "w") as f:
        json.dump({"info": {"id": "bbbbbbbb-cccc-dddd-eeee-ffff00001111",
                            "cwd": cwd}, "num_messages": 0}, f)
    open(os.path.join(empty, "updates.jsonl"), "w").close()

    # A subagent grok spawned for itself: a complete session tree next to the
    # real ones, told apart only by summary.json's session_kind.
    sub = os.path.join(grok_home, "sessions", "g0", GROK_SUBAGENT_ID)
    os.makedirs(sub)
    with open(os.path.join(sub, "summary.json"), "w") as f:
        json.dump({
            "info": {"id": GROK_SUBAGENT_ID, "cwd": cwd},
            "git_root_dir": cwd,
            "num_messages": 12,
            "session_kind": "subagent",
            "agent_name": "general-purpose",
            "generated_title": "Delegated parser survey",
            "created_at": "2026-07-19T10:30:00Z",
            "updated_at": "2026-07-19T10:40:00Z",
        }, f)
    with open(os.path.join(sub, "updates.jsonl"), "w") as f:
        f.write(json.dumps(upd("user_message_chunk", 300.0,
                               content="survey the parser")) + "\n")
        f.write(json.dumps(upd("agent_message_chunk", 301.0,
                               content="Surveyed.")) + "\n")
    return cwd


def run_grok_suite(token):
    print("\n=== grok provider ===")
    grok_home = os.path.join(FAKE_HOME, ".grok")
    cwd = grok_fixture(grok_home)

    fake_bin = os.path.join(FAKE_HOME, "grok")
    write_script(fake_bin, FAKE_GROK)

    config = Config({
        "provider": "grok",
        "bind": "127.0.0.1", "port": 0,
        "grok_home": grok_home,
        "grok_bin": fake_bin,
        "grok_prompt_flags": "--yolo",
        "grok_env": {"FAKE_GROK_HOME": grok_home},
        "turn_timeout": 3,
    })
    server, jobs, port, base = start_server(config, token)

    print("ping / caps:")
    with urllib.request.urlopen(base + "/api/ping", timeout=5) as resp:
        ping = json.loads(resp.read().decode())
    check("ping reports grok", ping.get("provider") == "grok", ping)
    caps = ping.get("caps") or {}
    check("grok caps: no interactive permissions",
          caps.get("permissions") is False and caps.get("permission_modes") is False, caps)
    check("grok caps: cwd optional", caps.get("requires_cwd") is False, caps)
    check("grok caps: queue + ws still on",
          caps.get("queue") is True and caps.get("ws_status") is True, caps)

    print("browsing:")
    _, data = api(base, token, "/api/projects")
    check("one grok project (empty session hidden)", len(data["projects"]) == 1, data)
    proj = data["projects"][0]
    check("project cwd + name", proj["cwd"] == cwd and proj["name"] == "grokproj", proj)
    check("project counts non-empty user sessions only",
          proj["session_count"] == 1, proj)

    _, data = api(base, token, "/api/sessions")
    check("one session listed (subagent hidden)",
          [x["id"] for x in data["sessions"]] == [GROK_SESSION_ID], data)
    s = data["sessions"][0]
    check("title from generated_title", s["title"] == "Refactor the parser", s)
    check("branch from head_branch", s["git_branch"] == "main", s)
    check("cwd from summary info", s["cwd"] == cwd, s)
    check("last text from streamed tail",
          "Quick fix" in s["last_text"] and s["last_role"] == "assistant", s)

    print("search:")
    _, data = api(base, token, "/api/sessions/search?q=parser")
    check("grok search hits title", len(data.get("results") or []) == 1, data)
    check("grok search snippet",
          "parser" in (data["results"][0].get("snippet") or "").lower(), data)
    _, data = api(base, token, "/api/sessions/search?q=Quick%20fix")
    check("grok search hits body",
          len(data.get("results") or []) == 1
          and data["results"][0].get("id") == GROK_SESSION_ID, data)

    _, data = api(base, token, "/api/sessions?project=" + proj["id"])
    check("project filter matches munged id", len(data["sessions"]) == 1, data)

    print("subagent sessions:")
    _, data = api(base, token, "/api/sessions/search?q=survey")
    check("search skips subagent sessions", data.get("results") == [], data)
    _, data = api(base, token, "/api/sessions?all=1")
    check("all=1 reveals the subagent",
          {x["id"] for x in data["sessions"]}
          == {GROK_SESSION_ID, GROK_SUBAGENT_ID},
          [x["id"] for x in data["sessions"]])
    _, data = api(base, token, "/api/sessions/" + GROK_SUBAGENT_ID)
    check("subagent still resolves by id", data.get("id") == GROK_SUBAGENT_ID, data)
    store = providers.build(config)[0]
    check("subagent excluded from new-session id discovery",
          GROK_SUBAGENT_ID not in store.known_session_ids()
          and GROK_SUBAGENT_ID in store.known_session_ids(user_only=False))

    print("transcript:")
    _, data = api(base, token, "/api/sessions/" + GROK_SESSION_ID + "/messages")
    msgs = data["messages"]
    roles = [m["role"] for m in msgs]
    check("rows: two turns, NaN worked row dropped",
          roles == ["user", "status", "assistant", "status", "user", "assistant"],
          roles)
    check("user chunks coalesced",
          msgs[0]["text"] == "please refactor the parser", msgs[0])
    check("thought status timed",
          msgs[1]["text"] == "Thought for 2.0s"
          and msgs[1].get("metaKind") == "thought", msgs[1])
    check("status renders as meta block",
          msgs[1]["blocks"][0]["k"] == "meta" and msgs[1]["blocks"][0]["accent"] == 1,
          msgs[1]["blocks"])
    check("assistant chunks coalesced + markdown rendered",
          "Refactored" in msgs[2]["text"] and any(
              "<b>" in (b.get("rich") or "") for b in msgs[2]["blocks"]), msgs[2])
    check("worked row from wall time",
          msgs[3]["text"] == "Worked for 9.5s"
          and msgs[3].get("metaKind") == "worked", msgs[3])
    check("no NaN anywhere in the payload",
          "NaN" not in json.dumps(data), None)

    print("continue via job:")
    status, data = api(base, token, "/api/sessions/" + GROK_SESSION_ID + "/continue",
                       {"prompt": "now add tests"})
    check("continue accepted", status == 202 and data.get("job_id"), data)
    snap = wait_job(base, token, data["job_id"])
    check("grok job done", snap.get("status") == "done", snap)
    kinds = [e["kind"] for e in snap.get("events", [])]
    check("text flushed before tool", kinds == ["text", "tool", "text", "result"], kinds)
    tool_ev = next((e for e in snap["events"] if e.get("kind") == "tool"), {})
    check("stream tool carries command detail",
          tool_ev.get("name") == "Bash" and tool_ev.get("detail") == "ls -la",
          tool_ev)
    check("resume + flags passed through",
          any("resumed " + GROK_SESSION_ID in e.get("text", "")
              and "flags=yolo" in e.get("text", "") for e in snap["events"]),
          snap["events"])
    check("result text accumulated", "echo: now add tests" in snap.get("result_text", "")
          and "tail" in snap.get("result_text", ""), snap)

    # Real grok only writes tools to updates.jsonl; verify tick() tails it
    # into tool events + a semantic "running" phase for the status banner.
    status, data = api(base, token, "/api/sessions/" + GROK_SESSION_ID + "/continue",
                       {"prompt": "DISKTOOL from journal"})
    disk_job = data["job_id"]
    check("disktool continue accepted", status == 202 and disk_job, data)
    # While the fake is sleeping after appending the tool_call, the active
    # status snapshot should surface phase=running + the command detail.
    phase_seen = False
    tool_seen = False
    deadline = time.time() + 4
    while time.time() < deadline and not (phase_seen and tool_seen):
        _, st = api(base, token, "/api/jobs")
        # jobs list has no phase — poll the job snapshot's events + use
        # the WS-equivalent active_status via a full job get... phase is
        # only on /ws/status. Read it from the runner via job snapshot's
        # last tool event instead, and also through a tiny internal check:
        _, snap_live = api(base, token, "/api/jobs/" + disk_job + "?since=0")
        for e in snap_live.get("events") or []:
            if e.get("kind") == "tool" and e.get("name") == "run_terminal_command":
                tool_seen = True
                if e.get("detail") == "ls -la":
                    phase_seen = True
        time.sleep(0.2)
    snap = wait_job(base, token, disk_job)
    check("disk tool_call becomes a tool event", tool_seen, snap)
    check("disk tool detail is the command", phase_seen, snap)
    disk_tools = [e for e in snap.get("events", []) if e.get("kind") == "tool"]
    check("disk tool name is run_terminal_command",
          any(e.get("name") == "run_terminal_command" for e in disk_tools),
          disk_tools)

    status, data = api(base, token, "/api/sessions/" + GROK_SESSION_ID + "/continue",
                       {"prompt": "EXITCODE1 still fine"})
    snap = wait_job(base, token, data["job_id"])
    check("clean end beats nonzero exit", snap.get("status") == "done", snap)


    print("new session (no cwd, id via fs-diff):")
    status, data = api(base, token, "/api/sessions/new", {"prompt": "hello grok"})
    check("new session accepted without cwd", status == 202, data)
    snap = wait_job(base, token, data["job_id"])
    check("new-session job done", snap.get("status") == "done", snap)
    check("session id discovered from session tree",
          snap.get("new_session_id") == GROK_NEW_SID, snap)
    check("init event emitted for discovered id",
          any(e["kind"] == "init" and e.get("session_id") == GROK_NEW_SID
              for e in snap.get("events", [])), snap.get("events"))

    print("queue chains on grok:")
    status, data = api(base, token, "/api/sessions/" + GROK_SESSION_ID + "/continue",
                       {"prompt": "NAP then answer"})
    nap_id = data["job_id"]
    status, data = api(base, token, "/api/jobs/%s/queue" % nap_id, {"prompt": "queued one"})
    check("queue accepted", status == 202 and len(data.get("queued", [])) == 1, data)
    next_id = ""
    deadline = time.time() + 15
    while time.time() < deadline:
        _, snap = api(base, token, "/api/jobs/" + nap_id)
        if snap["status"] == "done" and snap.get("next_job_id"):
            next_id = snap["next_job_id"]
            break
        time.sleep(0.2)
    check("chained into queued prompt", next_id != "", snap)
    snap = wait_job(base, token, next_id) if next_id else {}
    check("chained job resumed the session",
          any("resumed" in e.get("text", "") for e in snap.get("events", [])),
          snap.get("events"))

    print("turn timeout:")
    status, data = api(base, token, "/api/sessions/" + GROK_SESSION_ID + "/continue",
                       {"prompt": "SLEEP forever"})
    snap = wait_job(base, token, data["job_id"], timeout=15)
    check("wedged turn times out as error",
          snap.get("status") == "error" and "timed out" in snap.get("error", ""), snap)

    check_usage_parser()

    server.shutdown()


def check_usage_parser():
    """The /usage pane parser (no tmux/grok needed — pure text in, rows out).

    The rows below are a verbatim capture from `grok 0.2.118 --minimal` on the
    VPS: the three lines /usage printed plus the status line that the pane
    diff drags along with them.
    """
    from agentremoted.providers.grok_interactive import _usage_buckets, _new_rows

    print("usage parser:")
    rows = [
        "Session usage: no model calls yet in this session.",
        "Weekly limit: 17%",
        "Next reset: August 2, 17:39",
        "Grok 4.5 (high) · always-approve · 5.2K / 500K (1%) · ctrl+o transcript",
    ]
    buckets = _usage_buckets(rows)
    check("one bucket per percentage row", len(buckets) == 1, buckets)
    check("bucket matches the phone's shape",
          buckets and buckets[0] == {"title": "Weekly limit", "percent": 17,
                                     "resets_text": "Resets August 2, 17:39",
                                     "severity": "normal"}, buckets)

    hot = _usage_buckets(["Hourly limit: 80%", "Weekly limit: 96%"])
    check("severity tracks the percentage",
          [b["severity"] for b in hot] == ["warning", "critical"], hot)

    check("TUI chrome yields no buckets",
          _usage_buckets(["❯", "minimal · /help", "Grok Build  v0.2.118",
                          "/root", "5.2K / 500K (1%)"]) == [])

    before = ["Grok Build  v0.2.118", "/root", "minimal · /help"]
    after = ["Grok Build  v0.2.118", "/root", "Weekly limit: 17%",
             "minimal · /help"]
    check("pane diff keeps only what /usage printed",
          _new_rows(before, after)[0] == "Weekly limit: 17%",
          _new_rows(before, after))


def main():
    token = load_or_create_token()
    run_claude_suite(token)
    run_grok_suite(token)

    shutil.rmtree(FAKE_HOME, ignore_errors=True)

    if failures:
        print("\n%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("\nall checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
