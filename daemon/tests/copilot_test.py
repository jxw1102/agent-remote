"""GitHub Copilot provider against a synthetic ``~/.copilot`` tree.

Proves the session-state walk (workspace.yaml incl. block-scalar names,
events.jsonl -> transcript + process-view steps), the user_only filters,
``/rewind`` as an events.jsonl cut, the stream parser on output captured from
copilot 1.0.86, headless command shapes, the premium-request usage buckets
(against a fake GitHub endpoint), and the TUI screen heuristics.

Run:  python3 tests/copilot_test.py
"""

import json
import os
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-copilot-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"
for k in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
    os.environ.pop(k, None)

from agentremoted import titles                                   # noqa: E402
from agentremoted.config import Config                            # noqa: E402
from agentremoted.jobs import Job                                 # noqa: E402
from agentremoted import providers as providers_mod               # noqa: E402
from agentremoted.providers.copilot import (                      # noqa: E402
    CopilotRunner, CopilotStore, parse_workspace_yaml, usage_buckets)
from agentremoted.providers.copilot_interactive import (          # noqa: E402
    pane_busy, pane_ready, pane_trust_dialog)

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


HOME = Path(FAKE_HOME) / ".copilot"
S1 = "11111111-1111-4111-8111-111111111111"   # two turns, tools, in a project
S2 = "22222222-2222-4222-8222-222222222222"   # opened, nothing typed -> hidden
S3 = "33333333-3333-4333-8333-333333333333"   # titler's own session -> hidden
S4 = "44444444-4444-4444-8444-444444444444"   # block-scalar name, other project


def ev(t, data, ts):
    return json.dumps({"type": t, "data": data, "id": t + ts,
                       "timestamp": ts, "parentId": None})


def write_session(sid, workspace, events):
    d = HOME / "session-state" / sid
    d.mkdir(parents=True)
    (d / "workspace.yaml").write_text(workspace)
    if events is not None:
        (d / "events.jsonl").write_text("".join(e + "\n" for e in events))
    return d


# Captured from `copilot -p … --output-format json` (1.0.86), trimmed.
STREAM = r'''
{"type": "user.message", "data": {"content": "Use your shell tool to run: cat note.txt   Then tell me what it said in one short sentence."}}
{"type": "assistant.turn_start", "data": {"turnId": "0"}}
{"type": "assistant.message", "data": {"content": "", "toolRequests": [{"toolCallId": "toolu_1", "name": "bash", "arguments": {"command": "cat note.txt", "description": "Read note.txt contents"}, "type": "function"}], "model": "claude-sonnet-5"}}
{"type": "tool.execution_start", "data": {"toolCallId": "toolu_1", "toolName": "bash", "arguments": {"command": "cat note.txt", "description": "Read note.txt contents"}}}
{"type": "tool.execution_complete", "data": {"toolCallId": "toolu_1", "success": true, "result": {"content": "hello from copilot test\n<shellId: 0 completed with exit code 0>"}}}
{"type": "assistant.turn_end", "data": {"turnId": "0"}}
{"type": "assistant.turn_start", "data": {"turnId": "1"}}
{"type": "assistant.message", "data": {"content": "The note says: \"hello from copilot test\".", "toolRequests": [], "model": "claude-sonnet-5"}}
{"type": "assistant.turn_end", "data": {"turnId": "1"}}
{"type": "result", "sessionId": "d7edb3e2-bf95-4959-bcdc-ee969d3d3176", "exitCode": 0, "usage": {"premiumRequests": 1, "sessionDurationMs": 10035}}
'''.strip().splitlines()

QUOTA = {
    "copilot_plan": "business",
    "quota_reset_date": "2026-10-01",
    "quota_snapshots": {
        "chat": {"entitlement": 0, "remaining": 0, "unlimited": True},
        "premium_interactions": {"entitlement": 3000, "remaining": 2960,
                                 "percent_remaining": 98.6, "unlimited": False},
    },
}


class FakeGitHub(BaseHTTPRequestHandler):
    seen_auth = []

    def log_message(self, *a):
        return

    def do_GET(self):
        type(self).seen_auth.append(self.headers.get("Authorization", ""))
        body = json.dumps(QUOTA).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    cwd = "/Users/dev/code/app"
    write_session(S1, (
        "id: %s\ncwd: %s\nbranch: main\nname: 'Fix the login bug'\n"
        "user_named: false\ncreated_at: 2026-09-20T03:38:44.264Z\n"
        "updated_at: 2026-09-20T03:40:39.800Z\n") % (S1, cwd), [
        ev("session.start", {"sessionId": S1}, "2026-09-20T03:38:44.279Z"),
        ev("session.model_change", {"newModel": "claude-sonnet-5"}, "2026-09-20T03:38:50Z"),
        ev("system.message", {"role": "system", "content": "You are Copilot"}, "2026-09-20T03:38:51Z"),
        ev("user.message", {"content": "the login page crashes",
                            "transformedContent": "<current_datetime>x</current_datetime>\nthe login page crashes"},
           "2026-09-20T03:39:00Z"),
        ev("assistant.message", {"content": "", "model": "claude-sonnet-5",
                                 "reasoningText": "Look at auth.py first.",
                                 "toolRequests": [{"toolCallId": "t1", "name": "bash",
                                                   "arguments": {"command": "cat auth.py",
                                                                 "description": "Read auth.py"}}]},
           "2026-09-20T03:39:05Z"),
        ev("tool.execution_complete", {"toolCallId": "t1", "success": True,
                                       "result": {"content": "def login(): ...\n" * 80}},
           "2026-09-20T03:39:06Z"),
        ev("assistant.message", {"content": "Fixed the **null check**.", "toolRequests": []},
           "2026-09-20T03:39:10Z"),
        ev("assistant.turn_end", {"turnId": "1"}, "2026-09-20T03:39:11Z"),
        ev("user.message", {"content": "now add a test"}, "2026-09-20T03:40:00Z"),
        ev("assistant.message", {"content": "Added test_login.", "toolRequests": []},
           "2026-09-20T03:40:30Z"),
        ev("assistant.turn_end", {"turnId": "2"}, "2026-09-20T03:40:31Z"),
    ])
    write_session(S2, "id: %s\ncwd: %s\ncreated_at: 2026-09-21T00:00:00Z\n" % (S2, cwd), None)
    write_session(S3, "id: %s\ncwd: %s\ncreated_at: 2026-09-22T00:00:00Z\n"
                  % (S3, titles.titler_cwd()),
                  [ev("user.message", {"content": "name this"}, "2026-09-22T00:00:01Z")])
    write_session(S4, (
        "id: %s\ncwd: /Users/dev/other\nname: |-\n  You are being used as the\n"
        "  agent backend.\n\n  More text.\nuser_named: false\n"
        "created_at: 2026-09-23T00:00:00Z\nupdated_at: 2026-09-23T00:05:00Z\n") % S4,
        [ev("user.message", {"content": "hello there"}, "2026-09-23T00:00:01Z"),
         ev("assistant.message", {"content": "Hi!", "toolRequests": []}, "2026-09-23T00:00:02Z")])

    usage_srv = HTTPServer(("127.0.0.1", 0), FakeGitHub)
    threading.Thread(target=usage_srv.serve_forever, daemon=True).start()
    (HOME / "config.json").write_text(
        "// managed automatically\n" + json.dumps({
            "copilotTokens": {"https://github.com:dev": "gho_testtoken"},
            "lastLoggedInUser": {"host": "https://github.com", "login": "dev"}}))
    cfg = Config({"providers": ["copilot"], "copilot_home": str(HOME),
                  "copilot_usage_url": "http://127.0.0.1:%d/" % usage_srv.server_address[1]})
    store, runner = providers_mod.build_one(cfg, "copilot")

    print("yaml")
    y = parse_workspace_yaml("a: plain\nb: 'it''s'\nc: |-\n  line one\n  line two\nd: \"q\"\n")
    check("plain / quoted / block scalars", y == {"a": "plain", "b": "it's",
                                                  "c": "line one\nline two", "d": "q"}, y)

    print("store")
    ids = [s["id"] for s in store.list_sessions(limit=25)]
    check("human sessions listed newest first; empty + titler hidden", ids == [S4, S1], ids)
    check("&all=1 shows every folder", len(store.list_sessions(limit=25, user_only=False)) == 4)
    s1 = store.get_session(S1)
    check("title from workspace name", s1["title"] == "Fix the login bug", s1["title"])
    check("model + last message", s1["model"] == "claude-sonnet-5"
          and s1["last_role"] == "assistant" and s1["last_text"] == "Added test_login.", s1)
    check("timestamps normalised", s1["started"] == "2026-09-20T03:38:44Z"
          and s1["last_active"] == "2026-09-20T03:40:39Z", (s1["started"], s1["last_active"]))
    check("branch carried", s1["git_branch"] == "main")
    s4 = store.get_session(S4)
    check("block-scalar name is not '|-'", s4["title"].startswith("You are being used"), s4["title"])
    check("projects", sorted(p["cwd"] for p in store.list_projects()) == ["/Users/dev/code/app", "/Users/dev/other"])
    check("project filter", [s["id"] for s in store.list_sessions(project_id="-Users-dev-code-app")] == [S1])
    check("unknown id -> None", store.get_session("not-a-uuid") is None)

    print("transcript")
    m = store.get_messages(S1, steps=True)
    rows = [(x["role"], x["text"]) for x in m["messages"]]
    check("content, not transformedContent; system + empty assistant skipped", rows == [
        ("user", "the login page crashes"), ("assistant", "Fixed the **null check**."),
        ("user", "now add a test"), ("assistant", "Added test_login.")], rows)
    steps0 = m["messages"][0].get("steps") or []
    check("thinking, tool call and result hang under the prompt",
          [s["kind"] for s in steps0] == ["thinking", "tool_use", "tool_result"], steps0)
    check("tool body is description + command", steps0[1]["preview"] == "Read auth.py\n$ cat auth.py",
          steps0[1]["preview"])
    check("big result truncated with a ref", steps0[2]["truncated"], steps0[2])
    full = store.get_step(S1, steps0[2]["ref"])
    check("step endpoint returns the full body", full and full["bytes"] > 1000, full)
    check("user rows render as k=user", m["messages"][0]["blocks"][0]["k"] == "user")
    check("no steps without detail=steps", "steps" not in store.get_messages(S1)["messages"][0])
    check("search hits body", [h["id"] for h in store.search_sessions("null check")] == [S1])
    check("search hits title", [h["id"] for h in store.search_sessions("login bug")] == [S1])

    print("rewind")
    runner._interactive = type("F", (), {"close_for_session": lambda self, s: False})()
    done, preview = runner.rewind_session(S1, 1)
    check("rewound one prompt", (done, preview) == (1, "now add a test"), (done, preview))
    check("transcript ends at turn 1", [x["text"] for x in store.get_messages(S1)["messages"]]
          == ["the login page crashes", "Fixed the **null check**."])
    check("backup left beside the log", (HOME / "session-state" / S1 / "events.jsonl.rewind-bak").is_file())
    check("clamps to what is left", runner.rewind_session(S1, 9)[0] == 1)
    try:
        runner.rewind_session(S1, 1)
        check("empty session refuses", False)
    except providers_mod.RunnerError as e:
        check("empty session refuses", "nothing to rewind" in str(e), e)

    print("headless")
    wk = os.path.join(FAKE_HOME, "wk")
    os.makedirs(wk)
    job = Job("j1", "", "do it", wk)
    cmd, env = runner.prepare(job, "bypassPermissions")
    check("new turn names its session", "--session-id" in cmd
          and cmd[cmd.index("--session-id") + 1] == job.new_session_id
          and len(job.new_session_id) == 36, cmd)
    check("json stream + auto-approve", cmd[:6] == ["copilot", "-p", "do it", "--output-format",
                                                    "json", "--allow-all-tools"], cmd)
    job2 = Job("j2", S4, "again", wk)
    job2.model = "claude-sonnet-5"
    job2.effort = "high"
    cmd2, _ = runner.prepare(job2, "default")
    check("resume + model + effort", cmd2[cmd2.index("--resume") + 1] == S4
          and cmd2[cmd2.index("--model") + 1] == "claude-sonnet-5"
          and cmd2[cmd2.index("--reasoning-effort") + 1] == "high"
          and "--session-id" not in cmd2, cmd2)
    try:
        runner.prepare(Job("j3", "", "x", ""), "default")
        check("cwd required", False)
    except providers_mod.RunnerError:
        check("cwd required", True)
    for line in STREAM:
        runner.handle_stream_line(job, line)
    kinds = [e["kind"] for e in job.events]
    check("event stream", kinds == ["init", "tool", "text", "result"], kinds)
    tool = next(e for e in job.events if e["kind"] == "tool")
    check("tool event named + described", tool["name"] == "bash"
          and tool["detail"] == "Read note.txt contents", tool)
    check("result text", job.result_text == 'The note says: "hello from copilot test".', job.result_text)
    check("clean exit", runner.finalize(job, 0, "") is None and not job.error)
    bad = Job("j4", "", "x", wk)
    runner.prepare(bad, "default")
    runner.handle_stream_line(bad, json.dumps({"type": "result", "exitCode": 1, "usage": {}}))
    check("non-zero exitCode is an error", bool(bad.error) and bad.events[-1]["is_error"], bad.error)
    check("finalize on non-zero return fails", runner.finalize(Job("j5", "", "x", wk), 2, "boom\n") is False)

    print("usage")
    b = usage_buckets(QUOTA)
    check("premium allowance is a bar", b == [{"title": "Premium requests · Business", "percent": 1,
                                               "resets_text": "40 / 3000 used · Resets Oct 1",
                                               "severity": "normal"}], b)
    unl = usage_buckets({"quota_snapshots": {"premium_interactions": {"unlimited": True}}})
    check("unlimited has no bar", unl and unl[0]["show_bar"] is False, unl)
    u = runner.usage()
    check("usage via the CLI's own token", u["ok"] and u["account"] == "dev"
          and FakeGitHub.seen_auth[-1] == "token gho_testtoken", (u, FakeGitHub.seen_auth))
    ah = runner.auth_health()
    check("auth reads the login", ah["mode"] == "subscription" and "dev" in ah["detail"], ah)
    check("models: auto + history", runner.models()[:2] == ["auto", "claude-sonnet-5"], runner.models())
    check("efforts", runner.efforts() == ["low", "medium", "high", "xhigh"])
    caps = runner.capabilities()
    check("caps", caps["rewind"] and caps["can_set_effort"] and caps["can_show_usage"], caps)

    print("tui heuristics")
    trust = "│ Do you trust the files in this folder?\n│ ❯ 1. Yes\n│   2. Yes, and remember"
    idle = ("────\n❯ \n────\n ← open sidebar · / commands · ? help · tab next tab   Claude Sonnet 5")
    busy = ("────\n❯ \n────\n ◎ Working · 93 B esc interrupt                  Claude Sonnet 5")
    check("trust dialog detected, not ready", pane_trust_dialog(trust) and not pane_ready(trust))
    check("idle composer is ready", pane_ready(idle) and not pane_busy(idle))
    check("working pane is busy", pane_busy(busy))
    check("empty pane is neither", not pane_ready("") and not pane_busy(""))

    usage_srv.shutdown()
    print()
    if failures:
        print("%d FAILURE(S): %s" % (len(failures), ", ".join(failures)))
        return 1
    print("all ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
