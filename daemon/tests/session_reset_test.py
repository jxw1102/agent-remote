"""Force stopping a session on its host (POST /api/sessions/<id>/reset).

A fake `claude` stands in for the real one: it lists a background session
for `agents --json` and records what `stop` was asked for.

Run:  python3 tests/session_reset_test.py
"""

import json
import os
import stat
import sys
import tempfile
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted.providers.claude_interactive import InteractiveManager  # noqa: E402
from agentremoted.providers.codebuddy_interactive import CodebuddyInteractiveManager  # noqa: E402

failed = 0


def check(name, ok, detail=""):
    global failed
    print("  [%s] %s%s" % ("ok" if ok else "FAIL", name,
                           (" — %r" % (detail,)) if detail != "" and not ok else ""))
    failed += 0 if ok else 1


SID = "9393e381-243c-4ab8-be1e-5b797c38ce69"
tmp = tempfile.mkdtemp()
log_path = os.path.join(tmp, "calls.log")
rows = [
    {"pid": 1, "kind": "interactive", "sessionId": "other", "status": "idle"},
    {"pid": 2, "id": "9393e381", "kind": "background", "sessionId": SID,
     "status": "idle", "state": "blocked"},
]
fake = os.path.join(tmp, "claude")
with open(fake, "w") as f:
    f.write("#!/bin/sh\necho \"$@\" >> %s\n"
            "if [ \"$1\" = agents ]; then cat %s; exit 0; fi\n"
            "if [ \"$1\" = stop ]; then [ \"$2\" = bad ] && exit 1; exit 0; fi\n"
            "exit 2\n" % (log_path, os.path.join(tmp, "rows.json")))
os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
json.dump(rows, open(os.path.join(tmp, "rows.json"), "w"))


class Cfg:
    claude_bin = fake


class Tui:
    def __init__(self, name, sid):
        self.name, self.session_id = name, sid


def manager(cls=InteractiveManager):
    m = cls.__new__(cls)
    m.config = Cfg()
    m._lock = threading.Lock()
    m.killed = []
    m.saved = [0]
    m._tmux = lambda *a, **k: m.killed.append(a[-1]) if a[0] == "kill-session" else None
    m._save_state = lambda: m.saved.__setitem__(0, m.saved[0] + 1)
    m._tuis = {"cld-a": Tui("cld-a", SID), "cld-b": Tui("cld-b", "another")}
    return m


def calls():
    try:
        return open(log_path).read().split("\n")[:-1]
    except OSError:
        return []


print("session reset")
m = manager()
out = m.reset_session(SID)
check("its terminal is closed", out["terminals_closed"] == 1 and m.killed == ["cld-a"], (out, m.killed))
check("another session's terminal is left", "cld-b" in m._tuis and "cld-a" not in m._tuis)
check("the registry is saved", m.saved[0] == 1)
check("Claude's background copy is stopped", out["background_stopped"] is True, out)
check("by its background id, after listing", calls() == ["agents --json", "stop 9393e381"], calls())

os.remove(log_path)
m = manager()
out = m.reset_session("no-such-session")
check("an unknown session: nothing closed, nothing stopped",
      out["terminals_closed"] == 0 and out["background_stopped"] is False
      and calls() == ["agents --json"], (out, calls()))

os.remove(log_path)
rows[1]["kind"] = "interactive"
json.dump(rows, open(os.path.join(tmp, "rows.json"), "w"))
m = manager()
out = m.reset_session(SID)
check("a session open interactively is not stopped through Claude",
      out["background_stopped"] is False and calls() == ["agents --json"], (out, calls()))

rows[1]["kind"] = "background"
rows[1]["id"] = "bad"
json.dump(rows, open(os.path.join(tmp, "rows.json"), "w"))
out = manager().reset_session(SID)
check("a stop that fails is reported",
      out["background_stopped"] is False and "claude stop bad" in out["detail"], out)

rows[1]["id"] = "x; rm -rf /"
json.dump(rows, open(os.path.join(tmp, "rows.json"), "w"))
os.remove(log_path)
out = manager().reset_session(SID)
check("an id that is not a plain token is never passed on",
      out["background_stopped"] is False and calls() == ["agents --json"], calls())

Cfg.claude_bin = os.path.join(tmp, "missing")
out = manager().reset_session(SID)
check("no claude on the host: the terminal is still closed, the rest is said",
      out["terminals_closed"] == 1 and out["background_stopped"] is False
      and "could not list" in out["detail"], out)

Cfg.claude_bin = fake
os.remove(log_path) if os.path.exists(log_path) else None
out = manager(CodebuddyInteractiveManager).reset_session(SID)
check("CodeBuddy: its terminal is closed, Claude's commands are not run",
      out["terminals_closed"] == 1 and calls() == [], (out, calls()))

print("FAILED: %d" % failed if failed else "all passed")
sys.exit(1 if failed else 0)
