#!/usr/bin/env python3
"""SessionWatcher: `sessions_rev` bumps when the session set changes, not on
activity inside a session; scans are throttled; a failing store is ignored."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from agentremoted.session_watch import SessionWatcher  # noqa: E402

failures = []


def check(name, cond, detail=""):
    print("  [%s] %s%s" % ("ok" if cond else "FAIL", name,
                           (" — " + str(detail)) if detail and not cond else ""))
    if not cond:
        failures.append(name)


class FakeStore:
    def __init__(self, projects):
        self.projects = projects
        self.calls = 0
        self.fail = False

    def list_projects(self):
        self.calls += 1
        if self.fail:
            raise OSError("disk went away")
        return [dict(p) for p in self.projects]


claude = FakeStore([{"id": "a", "session_count": 2}])
cursor = FakeStore([])
w = SessionWatcher({"claude": claude, "cursor": cursor, "missing": None},
                   interval=2.0)

check("first scan baselines at 0", w.rev(now=100.0) == 0)
claude.projects[0]["session_count"] = 3
check("throttled: no rescan inside the interval", w.rev(now=101.0) == 0)
check("throttled: store scanned once", claude.calls == 1, claude.calls)
check("new session bumps", w.rev(now=103.0) == 1)
check("unchanged set keeps the rev", w.rev(now=106.0) == 1)
cursor.projects.append({"id": "p", "session_count": 1})
check("new project in another provider bumps", w.rev(now=109.0) == 2)
claude.fail = True
check("failing store keeps its last slice", w.rev(now=112.0) == 2)
claude.fail = False
claude.projects[0]["session_count"] = 2
check("removed session bumps", w.rev(now=115.0) == 3)

print("FAIL" if failures else "all session-watch checks passed")
sys.exit(1 if failures else 0)
