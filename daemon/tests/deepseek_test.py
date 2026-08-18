"""DeepSeek Harness provider against a fake dsh web /api.

Proves the localhost RPC envelope, session list/history, create+prompt
via run_alternate, and stop → session.cancel. Also: dsh web supervisor
adopts a live host and starts one when nothing is listening.

Run:  python3 tests/deepseek_test.py
"""

import json
import os
import signal
import socket
import stat
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FAKE_HOME = tempfile.mkdtemp(prefix="agentremoted-dsh-")
os.environ["HOME"] = FAKE_HOME
os.environ["AGENTREMOTED_HOME"] = os.path.join(FAKE_HOME, ".agentremoted")
os.environ["AGENTREMOTED_NO_KEYCHAIN"] = "1"

from agentremoted.config import Config                          # noqa: E402
from agentremoted.jobs import Job                               # noqa: E402
from agentremoted.providers.deepseek import (                   # noqa: E402
    DeepseekRunner, DeepseekStore)
from agentremoted.providers.dsh_rpc import DshClient, DshError  # noqa: E402
from agentremoted.providers.dsh_host import (                   # noqa: E402
    DshHost, bind_from_url, is_loopback)
from agentremoted import providers as providers_mod             # noqa: E402
from agentremoted.jobs import JobManager                        # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % (
        "ok" if cond else "FAIL", name,
        (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeDsh(BaseHTTPRequestHandler):
    """Minimal dsh web host: session.list/create/prompt/history/cancel."""

    state = None  # set on the class before serve

    def log_message(self, fmt, *args):
        return

    def _read_json(self):
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def _send(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, rpc_id, value):
        self._send({
            "type": "server-response",
            "rpcId": rpc_id,
            "result": {"ok": True, "value": value},
        })

    def _err(self, rpc_id, code, message):
        self._send({
            "type": "server-response",
            "rpcId": rpc_id,
            "result": {"ok": False, "error": {
                "code": code, "message": message, "details": {},
            }},
        })

    def do_POST(self):
        st = type(self).state
        msg = self._read_json()
        rpc_id = msg.get("rpcId") or "x"
        method = (self.path.rsplit("/", 1)[-1] or msg.get("method") or "")
        payload = msg.get("payload") if isinstance(msg.get("payload"), dict) else {}
        st["calls"].append(method)

        if method == "session.list":
            self._ok(rpc_id, {"items": list(st["sessions"].values())})
            return
        if method == "session.search":
            q = str(payload.get("query") or "").lower()
            hits = []
            for row in st["sessions"].values():
                blob = " ".join(e.get("text", "") for e in st["events"].get(
                    row["sessionId"], []))
                if q and q in blob.lower():
                    hits.append({"sessionId": row["sessionId"],
                                 "snippet": blob[:80]})
            self._ok(rpc_id, {"items": hits, "hasMore": False})
            return
        if method == "session.create":
            sid = "session-aaaa-bbbb-cccc-ddddeeee0001"
            cwd = str(payload.get("cwd") or "/tmp/app")
            st["sessions"][sid] = {
                "sessionId": sid, "cwd": cwd, "updatedAt": time.time(),
                "running": False, "blank": True,
                "projections": {"asOfSeq": -1, "values": {"title": "New"}},
            }
            st["events"][sid] = []
            self._ok(rpc_id, {"sessionId": sid})
            return
        if method == "session.prompt":
            sid = str(payload.get("sessionId") or "")
            row = st["sessions"].get(sid)
            if row is None:
                self._err(rpc_id, "session-not-found", "missing")
                return
            text = ""
            for part in payload.get("content") or []:
                if isinstance(part, dict) and part.get("type") == "text":
                    text += str(part.get("text") or "")
            evs = st["events"].setdefault(sid, [])
            evs.append({"type": "user/message", "seq": len(evs),
                        "ts": time.time(), "text": text})
            evs.append({"type": "assistant/message", "seq": len(evs) + 1,
                        "ts": time.time(),
                        "text": "echo: " + text})
            evs.append({"type": "turn/end", "seq": len(evs) + 2,
                        "ts": time.time()})
            row["running"] = False
            row["blank"] = False
            row["updatedAt"] = time.time()
            st["prompted"] += 1
            self._ok(rpc_id, {"accepted": True})
            return
        if method == "session.history":
            sid = str(payload.get("sessionId") or "")
            if sid not in st["events"] and sid not in st["sessions"]:
                self._err(rpc_id, "session-not-found", "missing")
                return
            events = [{"event": e} for e in st["events"].get(sid, [])]
            self._ok(rpc_id, {"events": events, "hasMore": False})
            return
        if method == "session.cancel":
            st["cancelled"] += 1
            self._ok(rpc_id, {"accepted": True})
            return
        if method == "session.selectModel":
            self._ok(rpc_id, {"selected": {
                "provider": payload.get("provider") or "deepseek-official",
                "model": payload.get("model") or "deepseek-v4-flash",
            }})
            return
        if method == "llm.models":
            self._ok(rpc_id, {"groups": [{
                "id": "deepseek-official", "name": "DeepSeek",
                "models": [{"id": "deepseek-v4-flash", "name": "V4 Flash"},
                           {"id": "deepseek-v4-pro", "name": "V4 Pro"}],
            }]})
            return
        self._err(rpc_id, "internal", "unknown method " + method)


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_FAKE_DSH = r"""#!/usr/bin/env python3
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

host = "127.0.0.1"
port = 3080
args = sys.argv[1:]
if args and args[0] == "web":
    args = args[1:]
i = 0
while i < len(args):
    if args[i] == "--host" and i + 1 < len(args):
        host = args[i + 1]
        i += 2
    elif args[i] == "--port" and i + 1 < len(args):
        port = int(args[i + 1])
        i += 2
    else:
        i += 1


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        return

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b"{}"
        try:
            msg = json.loads(raw.decode("utf-8"))
        except ValueError:
            msg = {}
        body = json.dumps({
            "type": "server-response",
            "rpcId": msg.get("rpcId") or "x",
            "result": {"ok": True, "value": {"items": []}},
        }).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


HTTPServer((host, port), H).serve_forever()
"""


def _write_fake_dsh(path):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(_FAKE_DSH)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


def seed_state():
    sid = "session-1111-2222-3333-444455556666"
    tool_sid = "session-toolcall-0001"
    return {
        "calls": [],
        "prompted": 0,
        "cancelled": 0,
        "sessions": {
            sid: {
                "sessionId": sid,
                "cwd": "/tmp/app",
                "updatedAt": time.time(),
                "running": False,
                "blank": False,
                "projections": {"asOfSeq": 4,
                                "values": {"title": "Login crash"}},
            },
            tool_sid: {
                "sessionId": tool_sid,
                "cwd": "/tmp/app",
                "updatedAt": time.time(),
                "running": False,
                "blank": False,
                "projections": {"asOfSeq": 8,
                                "values": {"title": "Tool turn"}},
            },
        },
        "events": {
            sid: [
                {"type": "user/message", "seq": 1, "ts": time.time() - 10,
                 "text": "fix the login crash"},
                # dsh injects harness content as user-role events the human
                # never typed; these must not surface in the transcript.
                {"type": "user/message", "seq": 2, "ts": time.time() - 9,
                 "text": "<system-reminder>background task finished"
                         "</system-reminder>"},
                {"type": "user/message", "seq": 3, "ts": time.time() - 8,
                 "text": "<system-reminder>nudge</system-reminder>"
                         "also check the logs"},
                {"type": "assistant/message", "seq": 4, "ts": time.time() - 5,
                 "text": "Fixed the null check."},
            ],
            # A turn with tool calls: tool-calls live inside the assistant
            # message content blocks, and the results come back as separate
            # tool/result events. This exercises process-view step building.
            tool_sid: [
                {"type": "user/message", "seq": 1, "ts": time.time() - 20,
                 "text": "find the crash"},
                {"type": "assistant/message", "seq": 2,
                 "ts": time.time() - 15, "data": {"message": {"content": [
                     {"type": "reasoning",
                      "text": "Let me search the code."},
                     {"type": "tool-call", "id": "call-1", "name": "bash",
                      "arguments": "{\"command\": \"grep -r crash src/\"}"},
                 ]}}},
                {"type": "tool/result", "seq": 3, "ts": time.time() - 14,
                 "data": {"message": {"source": {"callId": "call-1"},
                                      "content": [
                     {"content": "src/app.py:12: crash on null",
                      "isError": False},
                 ]}}},
                {"type": "assistant/message", "seq": 4,
                 "ts": time.time() - 10, "data": {"message": {"content": [
                     {"type": "text", "text": "Found it — null deref."},
                 ]}}},
            ],
        },
    }


def main():
    state = seed_state()
    FakeDsh.state = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeDsh)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = "http://127.0.0.1:%d" % port
    config = Config({"provider": "deepseek", "dsh_url": url, "turn_timeout": 8})

    print("rpc envelope:")
    client = DshClient(url)
    listed = client.call("session.list", {})
    check("list returns items", isinstance(listed.get("items"), list), listed)
    check("seeded session present",
          listed["items"][0]["sessionId"].startswith("session-"), listed)

    print("store:")
    store = DeepseekStore(config, client=client)
    sessions = store.list_sessions(limit=10)
    check("two user sessions", len(sessions) == 2, sessions)
    check("title from projection", sessions[0]["title"] == "Login crash",
          sessions[0])
    check("cwd grouped", sessions[0]["cwd"] == "/tmp/app", sessions[0])
    projects = store.list_projects()
    check("one project", len(projects) == 1, projects)
    page = store.get_messages(sessions[0]["id"])
    check("history has 3 messages", page and page["total"] == 3, page)
    check("user text",
          page["messages"][0]["role"] == "user"
          and "login crash" in page["messages"][0]["text"], page)
    check("system-reminder filtered",
          all("system-reminder" not in m.get("text", "")
              for m in page["messages"]), page)
    check("injected+nudge cleaned",
          page["messages"][1]["text"] == "also check the logs",
          page["messages"][1])
    hits = store.search_sessions("login")
    check("search hits the session",
          hits and "login" in (hits[0].get("snippet") or "").lower(), hits)

    print("process view (steps):")
    tstore = DeepseekStore(config, client=client)
    tpage = tstore.get_messages("session-toolcall-0001", steps=True)
    check("tool session has 3 messages",
          tpage and tpage["total"] == 3, tpage)
    tool_msg = tpage["messages"][1] if tpage else None
    check("assistant carries tool steps",
          tool_msg and tool_msg["role"] == "assistant"
          and tool_msg.get("steps"), tool_msg)
    kinds = [s["kind"] for s in (tool_msg or {}).get("steps") or []]
    check("tool_use + thinking steps",
          "tool_use" in kinds and "thinking" in kinds, kinds)
    tus = [s for s in (tool_msg or {}).get("steps") or []
           if s["kind"] == "tool_use"]
    check("tool_use named bash", tus and tus[0]["name"] == "bash", tus)
    check("tool_use ref resolves", tus and ":" in (tus[0].get("ref") or ""),
          tus)
    got = None
    if tus:
        got = tstore.get_step("session-toolcall-0001", tus[0]["ref"])
    check("get_step returns full body",
          got and "grep -r crash" in (got.get("text") or ""), got)
    trs = [s for s in (tool_msg or {}).get("steps") or []
           if s["kind"] == "tool_result"]
    check("tool_result step present", bool(trs), trs)
    if trs:
        check("tool_result has output",
              "null deref" in (trs[0].get("preview") or "")
              or "app.py" in (trs[0].get("preview") or ""), trs[0])
    tplain = tstore.get_messages("session-toolcall-0001")
    check("no steps without ?detail=steps",
          tplain and all("steps" not in m for m in tplain["messages"]),
          tplain)

    print("unknown session:")
    check("missing history is None",
          store.get_messages("session-does-not-exist") is None)

    print("runner turn:")
    runner = DeepseekRunner(config, client=client)
    job = Job("job1", "", "please ship it", "/tmp/app")
    job.status = "starting"
    runner.run_alternate(job, "headless")
    check("turn finishes", job.status == "done", job.status)
    check("created a session id",
          (job.new_session_id or "").startswith("session-"), job.new_session_id)
    check("prompted dsh", state["prompted"] == 1, state["prompted"])
    check("assistant echoed", "ship it" in (job.result_text or ""),
          job.result_text)
    caps = runner.capabilities()
    check("no live tui cap", caps.get("live_tui") is False, caps)
    check("requires cwd", caps.get("requires_cwd") is True, caps)
    models = runner.models()
    check("models include flash", "deepseek-v4-flash" in models, models)

    print("stop cancels:")
    before = state["cancelled"]
    job2 = Job("job2", job.new_session_id, "again", "/tmp/app")
    runner.cancel_job(job2)
    check("session.cancel called", state["cancelled"] == before + 1,
          state["cancelled"])

    print("provider registry:")
    store2, runner2 = providers_mod.build_one(config, "deepseek")
    check("build_one deepseek", runner2.name == "deepseek", runner2.name)
    check("alias dsh",
          providers_mod.build_one(config, "dsh")[1].name == "deepseek")

    print("unreachable:")
    dead = DshClient("http://127.0.0.1:1")
    try:
        dead.call("session.list", {}, timeout=1)
        check("dead host raises", False)
    except DshError as e:
        check("dead host raises DshError", True, e)

    print("queue routing:")
    mgr = JobManager(config, runner)
    jr = mgr.start_job("first", "/tmp/app")
    jr.session_id = "SESSION-X"
    jr.status = "running"
    hit = mgr.running_for_session("SESSION-X")
    check("running_for_session matches session_id", hit is jr, hit)
    jr.session_id = ""
    jr.new_session_id = "SESSION-Y"
    hit = mgr.running_for_session("SESSION-Y")
    check("running_for_session matches new_session_id", hit is jr, hit)
    check("running_for_session unknown is None",
          mgr.running_for_session("nope") is None)
    jr.status = "done"
    check("running_for_session ignores finished",
          mgr.running_for_session("SESSION-Y") is None)

    print("dsh web supervisor:")
    check("bind default port",
          bind_from_url("http://127.0.0.1") == ("127.0.0.1", 3080))
    check("bind custom port",
          bind_from_url("http://127.0.0.1:3099") == ("127.0.0.1", 3099))
    check("loopback hosts",
          is_loopback("127.0.0.1") and is_loopback("localhost")
          and is_loopback("::1"))
    check("remote is not loopback", not is_loopback("192.0.2.1"))

    spawned = []

    def refuse_spawn(host, port):
        spawned.append((host, port))
        raise RuntimeError("should not spawn")

    adopted = DshHost(config, spawn=refuse_spawn)
    check("adopt live host", adopted.ensure() is True, adopted.last_error)
    check("adopt is external", adopted.source == "external" and not adopted.owned,
          adopted.source)
    check("adopt does not spawn", spawned == [], spawned)
    adopted.shutdown()
    still = DshClient(url)
    check("shutdown leaves adopted host", still.reachable(), url)

    shared_store, shared_runner = providers_mod.build_one(config, "deepseek")
    check("build_one shares host",
          shared_store.host is shared_runner.host and shared_store.host is not None)

    fake_bin = _write_fake_dsh(os.path.join(FAKE_HOME, "fake-dsh"))

    class _Down:
        base = "http://192.0.2.1:3080"

        def reachable(self):
            return False

        def call(self, *a, **k):
            raise DshError("down")

    remote = DshHost(Config({
        "dsh_url": "http://192.0.2.1:3080",
        "dsh_bin": fake_bin,
        "dsh_manage": True,
    }), client=_Down(), spawn=refuse_spawn)
    check("remote url does not spawn",
          remote.ensure() is False and not spawned
          and "loopback" in (remote.last_error or ""), remote.last_error)

    missing_port = _free_port()
    missing = DshHost(Config({
        "dsh_url": "http://127.0.0.1:%d" % missing_port,
        "dsh_bin": os.path.join(FAKE_HOME, "no-such-dsh"),
        "dsh_manage": True,
    }))
    check("missing binary",
          missing.ensure() is False
          and "not found" in (missing.last_error or ""),
          missing.last_error)

    quiet_port = _free_port()
    quiet = DshHost(Config({
        "dsh_url": "http://127.0.0.1:%d" % quiet_port,
        "dsh_bin": fake_bin,
        "dsh_manage": False,
    }))
    check("manage off does not start",
          quiet.ensure() is False and quiet.proc is None, quiet.last_error)

    spawn_port = _free_port()
    managed = DshHost(Config({
        "dsh_url": "http://127.0.0.1:%d" % spawn_port,
        "dsh_bin": fake_bin,
        "dsh_manage": True,
    }))
    check("spawn when down", managed.ensure() is True, managed.last_error)
    check("spawn is managed",
          managed.source == "managed" and managed.owned, managed.source)
    check("spawned host answers", managed.client.reachable())
    old_pid = getattr(managed.proc, "pid", None)
    check("spawned pid", bool(old_pid), old_pid)
    if old_pid:
        os.kill(old_pid, signal.SIGKILL)
        try:
            managed.proc.wait(timeout=2)
        except Exception:
            pass
        time.sleep(0.15)
    check("respawn after death", managed.ensure() is True, managed.last_error)
    new_pid = getattr(managed.proc, "pid", None)
    check("respawned different pid",
          bool(new_pid) and new_pid != old_pid, (old_pid, new_pid))
    check("respawned host answers", managed.client.reachable())
    managed.shutdown()
    dead_after = DshClient("http://127.0.0.1:%d" % spawn_port)
    check("shutdown kills managed host", not dead_after.reachable())

    print("stream reasoning excluded:")
    from agentremoted.providers.deepseek import (                 # noqa: E402
        _content_blocks, _text_blocks_only)
    rev = {"type": "assistant/message", "data": {"message": {"content": [
        {"type": "reasoning", "text": "deep think here"},
        {"type": "text", "text": "the visible answer"},
    ]}}}
    blk = _content_blocks(rev)
    check("streamed text drops reasoning",
          (_text_blocks_only(blk) if blk else "") == "the visible answer",
          _text_blocks_only(blk) if blk else "")

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
