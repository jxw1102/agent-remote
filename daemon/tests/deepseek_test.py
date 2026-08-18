"""DeepSeek Harness provider against a fake dsh web /api.

Proves the localhost RPC envelope, session list/history, create+prompt
via run_alternate, and stop → session.cancel. No real dsh process.

Run:  python3 tests/deepseek_test.py
"""

import json
import os
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
from agentremoted import providers as providers_mod             # noqa: E402

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


def seed_state():
    sid = "session-1111-2222-3333-444455556666"
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
        },
        "events": {
            sid: [
                {"type": "user/message", "seq": 1, "ts": time.time() - 10,
                 "text": "fix the login crash"},
                {"type": "assistant/message", "seq": 2, "ts": time.time() - 5,
                 "text": "Fixed the null check."},
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
    check("one user session", len(sessions) == 1, sessions)
    check("title from projection", sessions[0]["title"] == "Login crash",
          sessions[0])
    check("cwd grouped", sessions[0]["cwd"] == "/tmp/app", sessions[0])
    projects = store.list_projects()
    check("one project", len(projects) == 1, projects)
    page = store.get_messages(sessions[0]["id"])
    check("history has 2 messages", page and page["total"] == 2, page)
    check("user text",
          page["messages"][0]["role"] == "user"
          and "login crash" in page["messages"][0]["text"], page)
    hits = store.search_sessions("login")
    check("search hits the session",
          hits and "login" in (hits[0].get("snippet") or "").lower(), hits)

    print("unknown session:")
    check("missing history is None",
          store.get_messages("session-does-not-exist") is None)

    print("runner turn:")
    runner = DeepseekRunner(config)
    runner.client = client
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

    print()
    if failures:
        print("FAILED: %d" % len(failures))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
