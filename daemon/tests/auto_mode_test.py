"""permission_mode "auto": a message goes into the session's terminal when
it has a live one, and runs headless when it does not.

Run:  python3 tests/auto_mode_test.py
"""

import os
import sys
import threading
import types

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agentremoted import jobs as jobs_mod  # noqa: E402

MANAGER = next(v for k, v in vars(jobs_mod).items()
               if isinstance(v, type) and hasattr(v, "auto_mode"))

failures = []


def check(name, ok, got=None):
    print("  [%s] %s" % ("ok" if ok else "FAIL", name))
    if not ok:
        failures.append(name)
        if got is not None:
            print("        got:", got)


class Mgr:
    """The slice of an interactive manager the lookup uses."""

    def __init__(self, sessions, alive=True):
        self._lock = threading.Lock()
        self._tuis = {
            "t%d" % i: types.SimpleNamespace(session_id=s, name="t%d" % i, job=None)
            for i, s in enumerate(sessions)}
        self.alive = alive

    def _tmux_alive(self, name):
        return self.alive


def manager(mgr=None, raises=False):
    def _interactive_mgr():
        if raises:
            raise RuntimeError("no tmux")
        return mgr
    runner = types.SimpleNamespace(_interactive_mgr=_interactive_mgr) if (mgr or raises) \
        else types.SimpleNamespace()
    self = types.SimpleNamespace(runner=runner)
    self.session_has_tui = lambda sid: MANAGER.session_has_tui(self, sid)
    return self


print("auto mode")
m = manager(Mgr(["s1"]))
check("a live terminal for the session: interactive", MANAGER.auto_mode(m, "s1") == "interactive")
check("another session's terminal does not count",
      MANAGER.auto_mode(m, "s2") == "bypassPermissions")
check("no session id (a new session): headless", MANAGER.auto_mode(m, "") == "bypassPermissions")
m = manager(Mgr(["s1"], alive=False))
check("a terminal whose tmux is gone: headless", MANAGER.auto_mode(m, "s1") == "bypassPermissions")
m = manager()
check("a harness with no terminal at all: headless",
      MANAGER.auto_mode(m, "s1") == "bypassPermissions")
m = manager(raises=True)
check("a lookup that fails: headless, not an error",
      MANAGER.auto_mode(m, "s1") == "bypassPermissions")

print()
if failures:
    print("FAILED: " + ", ".join(failures))
    sys.exit(1)
print("all ok")
