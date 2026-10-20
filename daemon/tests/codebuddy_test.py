"""CodeBuddy provider against a synthetic ~/.codebuddy tree.

Proves the Claude-shaped JSONL walk, /rewind as a transcript cut, the
stream-json parser, the headless `codebuddy -p` command shape
(2.169.0: stream-json, -y, --permission-mode, --model, --effort), and
the interactive TUI argv (trust dialog, login stop, hook routing).

Run:  python3 tests/codebuddy_test.py
"""

import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-codebuddy-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"
os.environ["CODEBUDDY_CLI_AUTH_FILE"] = os.path.join(FAKE_HOME, "no-login.info")
for key in ("CODEBUDDY_API_KEY", "CODEBUDDY_AUTH_TOKEN"):
    os.environ.pop(key, None)

from agentremoted.config import Config                            # noqa: E402
from agentremoted.jobs import Job                                 # noqa: E402
from agentremoted import providers as providers_mod               # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


HOME = Path(FAKE_HOME) / ".codebuddy"
CWD = os.path.join(FAKE_HOME, "app")
PROJ = "-tmp-app"
S1 = "11111111-1111-4111-8111-111111111111"
S2 = "22222222-2222-4222-8222-222222222222"
S3 = "33333333-3333-4333-8333-333333333333"


def line(obj):
    return json.dumps(obj)


def write_jsonl(sid, rows, mtime=None):
    directory = HOME / "projects" / PROJ
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (sid + ".jsonl")
    path.write_text("".join(line(row) + "\n" for row in rows), encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def user(uid, parent, text, ts):
    return {
        "type": "user", "uuid": uid, "parentUuid": parent,
        "timestamp": ts, "cwd": CWD, "gitBranch": "main",
        "message": {"role": "user", "content": text},
    }


def assistant(uid, parent, text, ts, tool=None):
    content = [{"type": "text", "text": text}]
    if tool:
        content.append(tool)
    return {
        "type": "assistant", "uuid": uid, "parentUuid": parent,
        "timestamp": ts, "cwd": CWD,
        "message": {"role": "assistant", "model": "glm-5.2", "content": content},
    }


write_jsonl(S1, [
    user("u1", None, "the login page crashes", "2026-07-19T10:00:00Z"),
    assistant("a1", "u1", "Fixed the **null check**.", "2026-07-19T10:00:05Z",
              {"type": "tool_use", "name": "Bash", "id": "t1",
               "input": {"command": "cat auth.py", "description": "Read auth.py"}}),
    user("u2", "a1", "now add a test", "2026-07-19T10:01:00Z"),
    assistant("a2", "u2", "Added the test.", "2026-07-19T10:01:05Z"),
])
# Opened and abandoned: no model reply, and old enough to hide.
write_jsonl(S2, [
    user("e1", None, "", "2026-07-19T09:00:00Z"),
], mtime=time.time() - 3600)
# Subagent transcript.
write_jsonl(S3, [
    {"type": "user", "uuid": "s1", "isSidechain": True, "cwd": CWD,
     "timestamp": "2026-07-19T11:00:00Z",
     "message": {"role": "user", "content": "side work"}},
    assistant("s2", "s1", "done on the side", "2026-07-19T11:00:05Z"),
])

cfg = Config({
    "providers": ["codebuddy"],
    "codebuddy_home": str(HOME),
    "codebuddy_bin": "codebuddy",
    "codebuddy_flags": "--max-turns 3",
})
store, runner = providers_mod.build_one(cfg, "codebuddy")
store.titler = lambda text: ""
alias_store, alias_runner = providers_mod.build_one(
    Config({"provider": "cbc", "codebuddy_home": str(HOME)}), "cbc")

print("store")
check("cbc alias", alias_runner.name == "codebuddy")
projects = store.list_projects()
check("one project", len(projects) == 1 and projects[0]["cwd"] == CWD, projects)
sessions = store.list_sessions()
check("user session only", [s["id"] for s in sessions] == [S1],
      [s["id"] for s in sessions])
check("title falls back to the prompt",
      sessions[0]["title"].startswith("the login page"), sessions[0].get("title"))
messages = store.get_messages(S1)
texts = [m["text"] for m in messages["messages"]]
check("transcript keeps both turns",
      texts == ["the login page crashes", "Fixed the **null check**.",
                "now add a test", "Added the test."], texts)
check("search hits the body",
      [h["id"] for h in store.search_sessions("null check")] == [S1])

print("ptc shape")
S4 = "44444444-4444-4444-8444-444444444444"
write_jsonl(S4, [
    {"type": "session-meta", "id": "m0", "sessionId": S4,
     "timestamp": 1789000000000, "meta": {"codebuddy.ai/mainAgent": "ptc"}},
    {"id": "p1", "parentId": None, "type": "message", "role": "user",
     "timestamp": 1789000000000, "cwd": CWD,
     "content": [{"type": "input_text", "text": "diagnose the cert error"}]},
    {"id": "r1", "parentId": "p1", "type": "reasoning",
     "timestamp": 1789000000123,
     "rawContent": [{"type": "reasoning_text", "text": "TLS first."}]},
    {"id": "c1", "parentId": "r1", "type": "function_call", "callId": "call-1",
     "name": "REPL", "arguments": json.dumps({"code": "1+1"}),
     "timestamp": 1789000000200},
    {"id": "o1", "parentId": "c1", "type": "function_call_result",
     "callId": "call-1", "name": "REPL", "status": "completed",
     "output": {"type": "text", "text": "2"},
     "timestamp": 1789000000300},
    {"id": "p2", "parentId": "o1", "type": "message", "role": "assistant",
     "timestamp": 1789000000400, "cwd": CWD,
     "providerData": {"model": "hy4-preview"},
     "content": [{"type": "output_text", "text": "The cert was expired."}]},
    {"id": "h1", "timestamp": 1789000000500, "type": "file-history-snapshot",
     "messageId": "h1", "cwd": CWD, "snapshot": {}},
])
sessions = store.list_sessions()
check("ptc session listed first",
      [s["id"] for s in sessions] == [S4, S1], [s["id"] for s in sessions])
row = sessions[0]
check("ptc row keeps cwd and model",
      row["cwd"] == CWD and row["model"] == "hy4-preview", row)
check("ptc timestamps normalized to ISO",
      str(row["started"]).startswith("20") and "T" in str(row["started"]),
      row["started"])
check("ptc title from the prompt",
      row["title"].startswith("diagnose the cert"), row["title"])
data = store.get_messages(S4)
texts = [m["text"] for m in data["messages"]]
check("ptc transcript keeps both turns",
      texts == ["diagnose the cert error", "The cert was expired."], texts)
detailed = store.get_messages(S4, steps=True)
kinds = [st["kind"] for m in detailed["messages"]
         for st in (m.get("steps") or [])]
check("ptc steps cover think, call, result",
      kinds == ["thinking", "tool_use", "tool_result"], kinds)
tool = [st for m in detailed["messages"] for st in (m.get("steps") or [])
        if st["kind"] == "tool_use"][0]
check("ptc tool_use carries name and args",
      tool["name"] == "REPL"
      and "1+1" in (tool["preview"] or "") + (tool.get("detail") or ""), tool)
check("ptc search hits the body",
      [h["id"] for h in store.search_sessions("cert was expired")] == [S4])

# Harness plumbing that leaked into the transcript until 2.16.1: the hint
# line and warning banner that follow a </task-notification> block, the
# same two lines quoted back with no tags around them, and non-blocking
# Notification events (auth_success) claiming the status strip.
from agentremoted.providers.claude import _human_user_text        # noqa: E402
from agentremoted.providers.codebuddy_interactive import (        # noqa: E402
    CodebuddyInteractiveManager)

_hint = ('Use the TaskOutput tool with task_id="j1" to retrieve unread '
         "output if you need to act on it. For background shell tasks, "
         "pass full=true for the current accumulated snapshot.")
_banner = ("[SYSTEM NOTIFICATION - NOT USER INPUT] This is an automated "
           "background-task event, not a message from the user.")
_clean = lambda text: _human_user_text({                          # noqa: E731
    "type": "user", "message": {"role": "user",
                                "content": [{"type": "text", "text": text}]}})
check("task-notification block plus trail is not a prompt",
      _clean("<task-notification>\n<task-id>j1</task-id>\n"
             "</task-notification>\n\n%s\n%s\n" % (_hint, _banner)) == "")
# CodeBuddy paints AskUserQuestion only in the pane (its transcript line is
# written after the answer). Without this the turn looked frozen and the
# crash watchdog killed the TUI mid-question.
_panel = """❯ 1. You run ssh-copy-id
     Run: ssh-copy-id -i ~/.ssh/id.pub root@host
  2. Paste the password
     Send the root password.
  3. Different key path
  4. Skip the VPS deploy
  5. Type something
  Submit
  Chat about this
Enter to select · ↑/↓ to navigate · Esc to cancel
"""
from agentremoted.providers.claude_interactive import InteractiveManager  # noqa: E402
_p = InteractiveManager._ask_panel(_panel)
check("ask panel parsed", bool(_p), _p)
check("ask panel options",
      [o["label"] for o in (_p or {}).get("options") or []]
      == ["You run ssh-copy-id", "Paste the password", "Different key path",
          "Skip the VPS deploy"], _p)
check("ask panel ignores idle pane", InteractiveManager._ask_panel("❯\n") == {})

check("pasted content wrapper is stripped, text kept",
      _clean('<pasted_content id="20e4">\nssh -i ~/.ssh/k root@10.0.0.1\n'
             '</pasted_content id="20e4">')
      == "ssh -i ~/.ssh/k root@10.0.0.1")
check("bare pasted content wrapper is stripped",
      _clean("<pasted_content>hello</pasted_content>") == "hello")
check("quoted notification trail leaves the typed prose",
      _clean("> %s\n%s\n\nfix the parser" % (_hint, _banner))
      == "fix the parser")

_ni = CodebuddyInteractiveManager(Config({"codebuddy_home": str(HOME)}))
check("auth_success notice is not a wait",
      _ni._notification_message(
          {"notification_type": "auth_success",
           "message": "auth_success: 木乃猫"}) == "")
check("agent_completed notice is not a wait",
      _ni._notification_message(
          {"notification_type": "agent_completed", "message": "done"}) == "")
check("permission prompt still reaches the banner",
      _ni._notification_message(
          {"notification_type": "permission_prompt",
           "message": "needs your permission to use Bash"})
      == "needs your permission to use Bash")
done, preview = runner.rewind_session(S4, 1)
check("ptc rewind cuts the only prompt",
      (done, preview) == (1, "diagnose the cert error"), (done, preview))

# The title generator shells out to `codebuddy -p` in ~/.agentremoted/titler;
# CodeBuddy journals those one-shots like any session, and they are not the
# human's work. (Claude titles in-process, so only this store leaks them.)
from agentremoted import titles as titles_mod              # noqa: E402
_tproj = HOME / "projects" / "-agentremoted-titler"
_tproj.mkdir(parents=True, exist_ok=True)
_tcwd = str(titles_mod.titler_cwd())
(_tproj / "TITLER.jsonl").write_text(json.dumps({
    "type": "session-meta", "id": "t0", "sessionId": "TITLER",
    "timestamp": 1789000001000, "meta": {"codebuddy.ai/mainAgent": "ptc"},
}) + "\n" + json.dumps({
    "id": "t1", "parentId": None, "type": "message", "role": "user",
    "timestamp": 1789000001000, "cwd": _tcwd,
    "content": [{"type": "input_text", "text": "Name this session"}],
}) + "\n" + json.dumps({
    "id": "t2", "parentId": "t1", "type": "message", "role": "assistant",
    "timestamp": 1789000002000, "cwd": _tcwd,
    "providerData": {"model": "hy4-preview"},
    "content": [{"type": "output_text", "text": "A short title"}],
}) + "\n", encoding="utf-8")
_ids = [s["id"] for s in store.list_sessions()]
check("titler session not listed", "TITLER" not in _ids, _ids)
# Usage: no quota endpoint, so the runner sums providerData.usage from the
# transcripts. A session stamped "now" keeps the today/7d windows stable.
S5 = "55555555-5555-4555-8555-555555555555"
_now_ms = int(time.time() * 1000)
write_jsonl(S5, [
    {"type": "session-meta", "id": "u0", "sessionId": S5,
     "timestamp": _now_ms, "meta": {"codebuddy.ai/mainAgent": "ptc"}},
    {"id": "u1", "parentId": None, "type": "message", "role": "user",
     "timestamp": _now_ms, "cwd": CWD,
     "content": [{"type": "input_text", "text": "do a thing"}]},
    {"id": "u2", "parentId": "u1", "type": "function_call", "callId": "c9",
     "name": "REPL", "arguments": "{}", "timestamp": _now_ms,
     "providerData": {"model": "test-model",
                      "usage": {"totalTokens": 1234}}},
])
from agentremoted.providers import codebuddy as codebuddy_mod     # noqa: E402
codebuddy_mod._usage_cache = (0.0, None)
_usage = runner.usage()
_ub = _usage.get("buckets") or []
check("codebuddy usage ok", _usage.get("ok") is True, _usage)
check("codebuddy usage today bucket",
      bool(_ub) and _ub[0].get("title") == "Today"
      and "1.2k tokens" in _ub[0].get("resets_text", ""), _ub)
check("codebuddy usage models",
      any("test-model" in (b.get("resets_text") or "") for b in _ub), _ub)
check("codebuddy advertises usage", runner.capabilities().get("can_show_usage") is True)

# Provider colours come from the daemon, not from each client.
from agentremoted import palette as palette_mod            # noqa: E402
check("codebuddy palette",
      palette_mod.palette_for("codebuddy").get("accent") == "#58c08b"
      and palette_mod.palette_for("codebuddy").get("heading") == "#7fd4a8")
check("palette aliases resolve",
      palette_mod.palette_for("dsh").get("accent") == "#4d6bfe"
      and palette_mod.palette_for("cbc").get("accent") == "#58c08b")
check("unknown provider has no palette", palette_mod.palette_for("nope") == {})

check("titler project not listed",
      all(p["id"] != "-agentremoted-titler" for p in store.list_projects()),
      [p["id"] for p in store.list_projects()])

print("rewind")
done, preview = runner.rewind_session(S1, 1)
check("rewound the last prompt", (done, preview) == (1, "now add a test"),
      (done, preview))
left = [m["text"] for m in store.get_messages(S1)["messages"]]
check("transcript ends at the first turn",
      left == ["the login page crashes", "Fixed the **null check**."], left)
check("backup left beside the log",
      (HOME / "projects" / PROJ / (S1 + ".jsonl.rewind-bak")).is_file())
try:
    runner.rewind_session("not a session", 1)
    check("bad id refused", False)
except providers_mod.RunnerError:
    check("bad id refused", True)

print("headless")
job = Job("j1", "", "do it", CWD)
cmd, env = runner.prepare(job, "bypassPermissions")
check("print stream + auto-approve",
      cmd[:7] == ["codebuddy", "-p", "do it", "--output-format", "stream-json",
                  "--verbose", "--dangerously-skip-permissions"], cmd)
check("permission mode bypass",
      cmd[cmd.index("--permission-mode") + 1] == "bypassPermissions", cmd)
check("extra flags kept", "--max-turns" in cmd and "3" in cmd, cmd)
check("config dir in env", env.get("CODEBUDDY_CONFIG_DIR") == str(HOME), env.get("CODEBUDDY_CONFIG_DIR"))
job2 = Job("j2", S1, "again", CWD)
job2.model = "glm-5.2"
job2.effort = "high"
cmd2, _ = runner.prepare(job2, "plan")
check("resume + model + effort",
      cmd2[cmd2.index("--resume") + 1] == S1
      and cmd2[cmd2.index("--model") + 1] == "glm-5.2"
      and cmd2[cmd2.index("--effort") + 1] == "high", cmd2)
check("plan does not skip permissions",
      "--dangerously-skip-permissions" not in cmd2
      and cmd2[cmd2.index("--permission-mode") + 1] == "plan", cmd2)
try:
    runner.prepare(Job("j3", "", "x", ""), "default")
    check("cwd required", False)
except providers_mod.RunnerError:
    check("cwd required", True)
bare = runner.__class__(Config({
    "codebuddy_home": str(HOME),
    "codebuddy_bin": "/no/such/codebuddy",
}))
try:
    bare.prepare(Job("j4", "", "x", CWD), "default")
    check("missing binary refused", False)
except providers_mod.RunnerError as exc:
    check("missing binary refused", "not on PATH" in str(exc), exc)

STREAM = [
    json.dumps({"type": "system", "subtype": "init", "session_id": S1,
                "model": "glm-5.2"}),
    json.dumps({"type": "system", "subtype": "keepalive"}),
    json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "Bash",
         "input": {"command": "cat note.txt", "description": "Read the note"}},
        {"type": "text", "text": "The note is hello."},
    ]}}),
    json.dumps({"type": "result", "subtype": "success", "is_error": False,
                "result": "The note is hello.", "duration_ms": 12,
                "session_id": S1}),
]
for row in STREAM:
    runner.handle_stream_line(job, row)
kinds = [event["kind"] for event in job.events]
check("event stream skips keepalive", kinds == ["init", "tool", "text", "result"], kinds)
check("session id from init", job.new_session_id == S1, job.new_session_id)
tool = next(event for event in job.events if event["kind"] == "tool")
check("tool named", tool["name"] == "Bash" and "note" in (tool.get("detail") or ""), tool)
check("result text", job.result_text == "The note is hello.", job.result_text)
check("clean exit", runner.finalize(job, 0, "") is None and not job.error)

print("catalog")
models = runner.models()
check("models start at default", models[0] == "default" and len(models) > 3, models[:4])
check("help or fallback includes an alias",
      "default-model" in models or "glm-5.2" in models, models[:6])
check("efforts", runner.efforts() == ["low", "medium", "high", "xhigh", "max"])
check("slash commands", "/rewind" in runner.slash_commands())
caps = runner.capabilities()
from agentremoted.providers.codebuddy_interactive import (  # noqa: E402
    CodebuddyInteractiveManager, tmux_available,
)
from agentremoted.providers.claude_interactive import (  # noqa: E402
    InteractiveManager, _Tui,
)
has_tmux = tmux_available()
check("caps", caps["rewind"] and caps["can_set_model"] and caps["can_set_effort"]
      and caps["interactive"] is has_tmux and caps["live_tui"] is has_tmux, caps)
auth = runner.auth_health()
check("no login yet", auth["status"] == "missing" and auth["cli"] == "codebuddy", auth)
keyed = runner.__class__(Config({
    "codebuddy_home": str(HOME),
    "codebuddy_bin": "codebuddy",
    "codebuddy_env": {"CODEBUDDY_API_KEY": "cb-test"},
}))
auth2 = keyed.auth_health()
check("api key counts", auth2["mode"] == "api_key" and auth2["status"] == "ok", auth2)
try:
    providers_mod.build_one(cfg, "nope")
    check("unknown provider refused", False)
except ValueError:
    check("unknown provider refused", True)

print("live tui")
import threading
mgr = CodebuddyInteractiveManager.__new__(CodebuddyInteractiveManager)
mgr.config = Config({
    "codebuddy_home": str(HOME),
    "codebuddy_bin": "codebuddy",
    "codebuddy_flags": "--verbose",
    "codebuddy_env": {"CODEBUDDY_API_KEY": "cb-test"},
})
mgr.secret = "shared-secret"
tui = _Tui("cbc-test", CWD)
parts = mgr._cli_parts(tui, S1, "glm-5.2", "high")
check("tui argv is the interactive binary",
      parts[0] == "codebuddy" and "-p" not in parts and "--print" not in parts, parts)
check("tui resumes and sets model, effort, plugin, bypass",
      "--resume" in parts and S1 in parts and "--model" in parts
      and "glm-5.2" in parts and "--effort" in parts and "high" in parts
      and "--permission-mode" in parts and "bypassPermissions" in parts
      and "--plugin-dir" in parts and "--verbose" in parts, parts)
line = mgr._exec_line(tui, parts, guest=False)
check("tui env pins config dir and disables agent view",
      "CODEBUDDY_CONFIG_DIR=" in line
      and "CODEBUDDY_CODE_DISABLE_AGENT_VIEW=1" in line
      and "CODEBUDDY_API_KEY=" in line
      and "AGENTREMOTE_HOOK_URL=" in line, line)
slug = os.path.basename(mgr._project_dir("/Users/ada/my.proj"))
check("project slug keeps hyphens and dashes the dot",
      slug.startswith("-Users-ada-my-proj"), slug)
trust = "Do you trust the files in this folder?\n  > 1. Trust folder only\n"
login = "Select login method:\n ❯ Log in via Chinese Site\n (Use ↑↓ to select, Enter to login)\n"
check("trust dialog is confirmed with Enter", mgr._startup_key(trust, 0) == "Enter")
check("login screen is not keyed", mgr._startup_key(login, 0) == "")
check("login screen fails the turn", "not signed in" in mgr._blocked_reason(login))
check("trust dialog is not a login failure", mgr._blocked_reason(trust) == "")
ready = "bypass\n❯ \n⏵⏵ bypass permissions on\n"
check("composer footer counts as ready", mgr._pane_is_ready(ready))

claude = InteractiveManager.__new__(InteractiveManager)
claude.secret = "shared-secret"
claude._tuis = {}
claude._lock = threading.Lock()
mgr._tuis = {}
mgr._lock = threading.Lock()
live = _Tui("cbc-live", CWD)
live.session_id = S1
mgr._tuis[live.name] = live
payload = {"hook_event_name": "Stop", "session_id": S1,
           "last_assistant_message": "done"}
check("claude manager does not claim a codebuddy hook",
      claude.on_hook(payload, "shared-secret", live.name) is False)
check("codebuddy manager takes the Stop hook",
      mgr.on_hook(payload, "shared-secret", live.name) is True
      and live.stop_event.is_set())

print()
if failures:
    print("FAILED: %s" % ", ".join(failures))
    sys.exit(1)
print("all checks passed")
